"""Ce qu'un bénévole corrige à l'atelier tient après la collecte suivante.

Audit du 15/11/2026, « Chaîne aval » : une relation supprimée revenait à la
passe suivante, et une fiche saisie puis renommée était recréée sous l'ancien
nom. Décisions de Julien du même jour : un refus mémorisé que la collecte
respecte, levable par un validateur ; une saisie rattachée à sa fiche par
l'identifiant ; les champs vides d'une fiche publiée remplis par un
contributeur passent par une proposition. Vrais jetons, vraie base SQLite,
schéma réel, collecteurs rejoués hors réseau.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

from collectors.origine import ATELIER, INSTITUTIONNEL  # noqa: E402

MDP = "mot-de-passe-de-test"
AVANT = "2026-01-01 00:00:00"
SOURCE = {"sans_document_motif": "Copie papier remise en mairie"}


@pytest.fixture
def atelier(tmp_path, monkeypatch, schema_sql):
    chemin_db = tmp_path / "instance.db"
    conn = sqlite3.connect(chemin_db)
    conn.executescript(schema_sql)
    conn.close()

    import api
    import api_auth
    from collectors import db as db_mod, saisies as saisies_mod
    for module in (api, api_auth, db_mod):
        monkeypatch.setattr(module, "DB_PATH", chemin_db)
    monkeypatch.setattr(saisies_mod, "SAISIES", tmp_path / "saisies.json")
    monkeypatch.setattr(api, "RACINE", tmp_path)
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.setattr(api, "ADMIN_KEY", "")
    client = TestClient(api.app)

    def sql(requete, params=()):
        c = sqlite3.connect(chemin_db)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(requete, params)
            c.commit()
            return [dict(r) for r in cur.fetchall()], cur.lastrowid
        finally:
            c.close()

    def compte(email, role):
        sql("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
            (email, api_auth._hash_pw(MDP), role))
        j = client.post("/api/auth/login", json={"email": email, "password": MDP}).json()
        return {"Authorization": f"Bearer {j['access_token']}"}

    def fiche(nom, confidence="verified", origine=INSTITUTIONNEL, type_="association",
              address=None):
        _, eid = sql("INSERT INTO entities(type, name, name_norm, confidence, origine,"
                     " address, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                     (type_, nom, nom.lower(), confidence, origine, address, AVANT))
        return eid

    return {"client": client, "sql": sql, "fiche": fiche,
            "contrib": compte("contrib@exemple.fr", "contributor"),
            "valid": compte("valid@exemple.fr", "validator")}


def _collecter(from_id, to_id, type_="président", source="rna"):
    """Ce qu'écrivent les collecteurs : `upsert_relation` (sirene, rne,
    banatic…), et l'`INSERT OR IGNORE` direct de marches_publics ou cm_finances."""
    from collectors.db import transaction, upsert_relation
    with transaction() as conn:
        upsert_relation(conn, from_id, to_id, type_, source=source)
        conn.execute("INSERT OR IGNORE INTO relations(from_id, to_id, relation_type, source,"
                     " confidence) VALUES(?,?,?,?, 'verified')",
                     (from_id, to_id, type_, source + "-bis"))


def _relations(atelier, a, b, type_="président"):
    return atelier["sql"]("SELECT * FROM relations WHERE from_id=? AND to_id=?"
                          " AND relation_type=?", (a, b, type_))[0]


# ─── 1. Le refus mémorisé ────────────────────────────────────────────────────

class TestRefusMemorise:
    def test_supprimer_recollecter_toujours_absent_puis_lever_et_retour(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        [r1, r2] = _relations(atelier, b, a)

        # Supprimer les deux lignes (deux sources disent le même lien).
        rep = c.delete(f"/api/atelier/relations/{r1['id']}?motif=Homonyme, pas le président",
                       headers=atelier["valid"])
        assert rep.status_code == 200, rep.text
        refus_id = rep.json()["refus_id"]
        assert refus_id
        assert c.delete(f"/api/atelier/relations/{r2['id']}?motif=Motif d essai",
                        headers=atelier["valid"]).json()["refus_id"] == refus_id

        # Recollecter : rien ne revient, et la tentative se compte.
        _collecter(b, a)
        _collecter(b, a)
        assert _relations(atelier, b, a) == []
        [refus] = c.get("/api/atelier/relations/refus", headers=atelier["valid"]).json()["refus"]
        assert (refus["motif"], refus["refuse_par"]) == ("Homonyme, pas le président",
                                                         "valid@exemple.fr")
        assert refus["tentatives"] == 4 and refus["derniere_source"] in ("rna", "rna-bis")
        assert refus["relation"]["id"] == r1["id"], "le lien supprimé, entier"

        # Journal : la suppression (lien entier) ET le refus.
        journal = sql("SELECT table_name, action, field, old_value, new_value FROM audit_log"
                      " WHERE table_name IN ('relations', 'relations_refusees') ORDER BY id")[0]
        assert [(j["table_name"], j["action"]) for j in journal] == [
            ("relations", "delete"), ("relations_refusees", "create"), ("relations", "delete")]
        assert json.loads(journal[0]["old_value"])["relation_type"] == "président"

        # Lever : journalisé, et la collecte suivante rétablit le lien.
        rep = c.delete(f"/api/atelier/relations/refus/{refus_id}?motif=Vérifié au RNA",
                       headers=atelier["valid"])
        assert rep.status_code == 200, rep.text
        [leve] = sql("SELECT * FROM audit_log WHERE table_name='relations_refusees'"
                     " AND action='delete'")[0]
        assert json.loads(leve["old_value"])["id"] == refus_id
        assert leve["new_value"] == "Vérifié au RNA"
        _collecter(b, a)
        assert len(_relations(atelier, b, a)) == 2

    def test_la_collecte_d_un_autre_lien_entre_les_memes_fiches_passe(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        for r in _relations(atelier, b, a):
            atelier["client"].delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai", headers=atelier["valid"])
        _collecter(b, a, type_="trésorier")
        assert len(_relations(atelier, b, a, "trésorier")) == 2
        _collecter(a, b)            # sens inverse : un autre lien
        assert len(_relations(atelier, a, b)) == 2

    def test_un_flux_saisi_rejoue_ne_recree_pas_sa_relation_refusee(self, atelier):
        """Le collecteur `saisies`, rejoué hors réseau comme à chaque passe."""
        from collectors import saisies
        c = atelier["client"]
        rep = c.post("/api/atelier/saisies", headers=atelier["valid"], json={
            "objet": "flux", "source": SOURCE, "confidence": "confirmed", "valeurs": {
                "type": "subvention", "year": 2025, "amount": 1500, "sens": "verse",
                "tiers": {"nom": "Club de pétanque fictif", "type": "association"},
                "description": "Subvention de fonctionnement"}})
        assert rep.status_code == 201, rep.text
        [rel] = atelier["sql"]("SELECT id, from_id, to_id FROM relations"
                               " WHERE relation_type='subventionné'")[0]
        assert c.delete(f"/api/atelier/relations/{rel['id']}?motif=Motif d essai",
                        headers=atelier["valid"]).json()["refus_id"]
        saisies.import_saisies()
        assert atelier["sql"]("SELECT 1 FROM relations WHERE relation_type='subventionné'")[0] == []
        [refus] = c.get("/api/atelier/relations/refus", headers=atelier["valid"]).json()["refus"]
        c.delete(f"/api/atelier/relations/refus/{refus['id']}?motif=Motif d essai", headers=atelier["valid"])
        saisies.import_saisies()
        assert len(atelier["sql"]("SELECT 1 FROM relations"
                                  " WHERE relation_type='subventionné'")[0]) == 1

    def test_un_lien_pose_a_l_atelier_n_est_pas_refuse(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        rep = c.post(f"/api/atelier/entities/{b}/relations", headers=atelier["valid"],
                     json={"direction": "from", "other_entity_id": a, "relation_type": "membre"})
        assert rep.status_code == 200, rep.text
        rep = c.delete(f"/api/atelier/relations/{rep.json()['id']}?motif=Motif d essai", headers=atelier["valid"])
        assert rep.json()["refus_id"] is None
        assert c.post(f"/api/atelier/entities/{b}/relations", headers=atelier["valid"],
                      json={"direction": "from", "other_entity_id": a,
                            "relation_type": "membre"}).status_code == 200

    def test_un_lien_presume_garde_son_circuit(self, atelier):
        a = atelier["fiche"]("Jeanne TESTAL", type_="person")
        b = atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(a, b, type_="époux_présumé", source="candidate")
        r = _relations(atelier, a, b, "époux_présumé")[0]
        assert atelier["client"].delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai",
                                        headers=atelier["valid"]).json()["refus_id"] is None

    def test_reposer_a_la_main_un_lien_refuse_dit_pourquoi(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        for r in _relations(atelier, b, a):
            refus_id = c.delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai",
                                headers=atelier["valid"]).json()["refus_id"]
        avant = atelier["sql"]("SELECT COUNT(*) AS n FROM audit_log")[0][0]["n"]
        rep = c.post(f"/api/atelier/entities/{b}/relations", headers=atelier["valid"],
                     json={"direction": "from", "other_entity_id": a,
                           "relation_type": "président"})
        assert rep.status_code == 409 and rep.json()["detail"]["refus_id"] == refus_id
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM audit_log")[0][0]["n"] == avant
        # Ni en changeant le type d'un autre lien vers le type refusé.
        _collecter(b, a, type_="membre", source="rna")
        autre = _relations(atelier, b, a, "membre")[0]
        assert c.put(f"/api/atelier/relations/{autre['id']}", headers=atelier["valid"],
                     json={"relation_type": "président"}).status_code == 409

    def test_le_contributeur_ne_supprime_ni_ne_lit_ni_ne_leve(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        r = _relations(atelier, b, a)[0]
        assert c.delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai",
                        headers=atelier["contrib"]).status_code == 403
        refus_id = c.delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai",
                            headers=atelier["valid"]).json()["refus_id"]
        assert c.get("/api/atelier/relations/refus", headers=atelier["contrib"]).status_code == 403
        assert c.delete(f"/api/atelier/relations/refus/{refus_id}",
                        headers=atelier["contrib"]).status_code == 403
        assert c.get("/api/atelier/relations/refus").status_code == 401
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM relations_refusees")[0][0]["n"] == 1

    def test_lister_par_fiche(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        x = atelier["fiche"]("Autre asso")
        _collecter(b, a)
        for r in _relations(atelier, b, a):
            c.delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai", headers=atelier["valid"])
        assert len(c.get(f"/api/atelier/relations/refus?entity_id={a}",
                         headers=atelier["valid"]).json()["refus"]) == 1
        assert c.get(f"/api/atelier/relations/refus?entity_id={x}",
                     headers=atelier["valid"]).json()["refus"] == []


def test_une_base_d_avant_le_refus_le_recoit_avec_son_declencheur(tmp_path, schema_sql):
    """L'API ne passe pas par `init_db` : `assurer_refus` pose la table ET le
    déclencheur ensemble, et une base sans eux écrit ses relations comme avant."""
    from collectors.db import assurer_refus, refus_de_relation
    conn = sqlite3.connect(tmp_path / "ancienne.db")
    conn.executescript(schema_sql)
    conn.executescript("DROP TRIGGER relations_refus_respecte; DROP TRIGGER relations_refus_respecte_maj;"
                       " DROP TABLE relations_refus_sources; DROP TABLE relations_refusees;")
    conn.execute("INSERT INTO entities(id, type, name) VALUES(1, 'person', 'A'), (2, 'service', 'B')")
    conn.execute("INSERT INTO relations(from_id, to_id, relation_type) VALUES(1, 2, 'élu_cm')")
    assert refus_de_relation(conn, 1, 2, "élu_cm") is None
    assurer_refus(conn)
    assurer_refus(conn)          # idempotent
    objets = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('relations_refusees',"
        " 'relations_refus_sources', 'relations_refus_respecte', 'relations_refus_respecte_maj')")}
    assert objets == {"relations_refusees", "relations_refus_sources", "relations_refus_respecte",
                      "relations_refus_respecte_maj"}
    conn.execute("INSERT INTO relations_refusees(from_id, to_id, relation_type, relation)"
                 " VALUES(1, 2, 'maire', '{}')")
    conn.execute("INSERT INTO relations(from_id, to_id, relation_type) VALUES(1, 2, 'maire')")
    assert conn.execute("SELECT COUNT(*) FROM relations WHERE relation_type='maire'"
                        ).fetchone()[0] == 0


# ─── 2. La saisie rattachée à sa fiche ───────────────────────────────────────

def _saisir_entite(atelier, qui, **valeurs):
    confiance = "confirmed" if qui == "valid" else "probable"
    rep = atelier["client"].post("/api/atelier/saisies", headers=atelier[qui], json={
        "objet": "entite", "source": SOURCE, "confidence": confiance,
        "valeurs": {"type": "association", **valeurs}})
    assert rep.status_code == 201, rep.text
    return rep.json()


class TestSaisieRattachee:
    def test_saisir_renommer_rejouer_une_seule_fiche(self, atelier):
        from collectors import saisies
        c, sql = atelier["client"], atelier["sql"]
        rep = _saisir_entite(atelier, "valid", name="Comité des fêtes de Valmorin")
        eid = rep["entite"]["id"]
        assert rep["entite"]["cree"] is True
        ligne = sql("SELECT updated_at, origine FROM entities WHERE id=?", (eid,))[0][0]
        assert ligne["origine"] == ATELIER
        r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["valid"],
                    json={"updated_at": ligne["updated_at"], "name": "Comité des fêtes"})
        assert r.status_code == 200, r.text

        saisies.import_saisies()
        saisies.import_saisies()
        noms = [r["name"] for r in sql("SELECT name FROM entities WHERE type='association'")[0]]
        assert noms == ["Comité des fêtes"]

    def test_une_saisie_d_avant_le_rattachement_retombe_sur_le_nom(self, atelier, tmp_path):
        from collectors import saisies
        eid = atelier["fiche"]("Foyer rural fictif", origine=ATELIER, confidence="probable")
        saisies.enregistrer({"saisies": [{
            "id": "ancienne0001", "objet": "entite", "confidence": "probable",
            "valeurs": {"name": "Foyer rural fictif", "type": "association"},
            "source": SOURCE, "saisi_par": "contrib@exemple.fr"}]})
        assert saisies.import_saisies()["ecrites"] == 0
        assert atelier["sql"]("SELECT entity_id, cree FROM saisies_entites")[0] == [
            {"entity_id": eid, "cree": 0}]
        # Rattachée désormais : renommée, elle n'est plus recréée.
        atelier["sql"]("UPDATE entities SET name='Foyer rural' WHERE id=?", (eid,))
        saisies.import_saisies()
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM entities"
                              " WHERE type='association'")[0][0]["n"] == 1


# ─── 3. Les champs vides d'une fiche publiée : une proposition ───────────────

class TestFicheRejointe:
    def test_le_contributeur_propose_l_adresse_d_une_fiche_publiee(self, atelier):
        sql = atelier["sql"]
        eid = atelier["fiche"]("Les Amis du Four", confidence="verified")
        rep = _saisir_entite(atelier, "contrib", name="Les Amis du Four",
                             address="3 rue des Essais")
        e = rep["entite"]
        assert (e["id"], e["cree"], e["proposes"], e["modifies"]) == (
            eid, False, ["address"], [])
        assert sql("SELECT address FROM entities WHERE id=?", (eid,))[0][0]["address"] is None
        [p] = sql("SELECT * FROM propositions WHERE id=?", (e["proposition"],))[0]
        assert (p["nature"], p["object_id"], p["etat"]) == ("fiche", eid, "en_attente")
        assert json.loads(p["charge"]) == {"address": "3 rue des Essais"}
        # Accepter l'écrit, par l'écrivain ordinaire de la fiche.
        r = atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                                   headers=atelier["valid"], json={"accepter": True})
        assert r.status_code == 200, r.text
        assert sql("SELECT address FROM entities WHERE id=?",
                   (eid,))[0][0]["address"] == "3 rue des Essais"

    def test_le_contributeur_ecrit_sur_une_fiche_non_publiee_comme_en_edition(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four", confidence="probable", origine=None)
        e = _saisir_entite(atelier, "contrib", name="Les Amis du Four",
                           address="5 rue des Essais")["entite"]
        assert (e["proposes"], e["modifies"]) == ([], ["address"])
        [j] = atelier["sql"]("SELECT * FROM audit_log WHERE entity_id=? AND field='address'",
                             (eid,))[0]
        assert (j["action"], j["new_value"]) == ("update", "5 rue des Essais")

    def test_un_champ_deja_rempli_n_est_pas_remplace(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four", address="7 rue des Essais")
        e = _saisir_entite(atelier, "valid", name="Les Amis du Four",
                           address="9 rue des Essais", commune="Valmorin")["entite"]
        assert (e["modifies"], e["proposes"]) == ([], [])
        assert e["non_repris"] == ["address", "commune"]
        assert atelier["sql"]("SELECT address FROM entities WHERE id=?",
                              (eid,))[0][0]["address"] == "7 rue des Essais"

    def test_le_rejeu_ne_touche_jamais_la_fiche_rejointe(self, atelier):
        from collectors import saisies
        eid = atelier["fiche"]("Les Amis du Four")
        _saisir_entite(atelier, "contrib", name="Les Amis du Four", address="11 rue des Essais")
        saisies.import_saisies()
        ligne = atelier["sql"]("SELECT address, origine FROM entities WHERE id=?", (eid,))[0][0]
        assert (ligne["address"], ligne["origine"]) == (None, INSTITUTIONNEL)


# ─── Suites de l'avis de vigie-securite ──────────────────────────────────────

class TestAvisSecurite:
    def _refuser(self, atelier, b, a, type_="président"):
        _collecter(b, a, type_=type_)
        rep = None
        for r in _relations(atelier, b, a, type_):
            rep = atelier["client"].delete(f"/api/atelier/relations/{r['id']}?motif=Motif d essai",
                                           headers=atelier["valid"]).json()
        return rep["refus_id"]

    def test_la_fusion_ne_ramene_pas_un_lien_refuse(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        a2 = atelier["fiche"]("Amis du Four (doublon)")
        self._refuser(atelier, b, a)
        _collecter(b, a2)
        # Ce que fait `scripts/fusionner_entites.py` : repointer l'absorbée.
        atelier["sql"]("UPDATE OR IGNORE relations SET to_id=? WHERE to_id=?", (a, a2))
        assert _relations(atelier, b, a) == []
        assert len(_relations(atelier, b, a2)) == 2

    def test_accepter_une_proposition_vers_un_type_refuse_est_refuse(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a, type_="membre")
        membre = _relations(atelier, b, a, "membre")[0]
        rep = c.put(f"/api/atelier/relations/{membre['id']}", headers=atelier["contrib"],
                    json={"relation_type": "président"})
        assert rep.status_code == 202, rep.text
        pid = rep.json()["proposition"]
        self._refuser(atelier, b, a)
        r = c.post(f"/api/atelier/propositions/{pid}/decision", headers=atelier["valid"],
                   json={"accepter": True})
        assert r.status_code == 409, r.text
        assert _relations(atelier, b, a, "membre")[0]["id"] == membre["id"]

    def test_la_suppression_rend_le_meme_lien_dit_par_une_autre_source(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        r1, r2 = _relations(atelier, b, a)
        rep = atelier["client"].delete(f"/api/atelier/relations/{r1['id']}?motif=Motif d essai",
                                       headers=atelier["valid"]).json()
        assert rep["encore_presents"] == [r2["id"]]

    def test_un_refus_qu_une_source_redit_se_compte_dans_aujourd_hui(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        self._refuser(atelier, b, a)
        files = {f["cle"]: f for f in c.get("/api/atelier/files", headers=atelier["valid"]).json()["files"]}
        assert (files["refus-contredits"]["reste"], files["refus-contredits"]["fait"]) == (0, 1)
        _collecter(b, a)
        files = {f["cle"]: f for f in c.get("/api/atelier/files", headers=atelier["valid"]).json()["files"]}
        assert files["refus-contredits"]["reste"] == 1


# ─── Second avis de vigie-securite (sur 344c756) ─────────────────────────────

def _files(atelier):
    return {f["cle"]: f for f in atelier["client"].get(
        "/api/atelier/files", headers=atelier["valid"]).json()["files"]}


class TestSecondAvis:
    def _refuser_un_des_deux(self, atelier):
        """Deux sources disent le lien ; on en supprime une : refus + un frère."""
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a)
        r1, r2 = _relations(atelier, b, a)
        rep = atelier["client"].delete(f"/api/atelier/relations/{r1['id']}?motif=Motif d essai",
                                       headers=atelier["valid"]).json()
        assert rep["encore_presents"] == [r2["id"]]
        return a, b, r2, rep["refus_id"]

    def test_modifier_le_frere_d_un_refus_ecrit_vraiment_et_journalise_vrai(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        a, b, frere, _ = self._refuser_un_des_deux(atelier)
        avant = sql("SELECT MAX(id) AS m FROM audit_log")[0][0]["m"]
        rep = c.put(f"/api/atelier/relations/{frere['id']}", headers=atelier["valid"],
                    json={"relation_type": "président", "confidence": "probable"})
        assert rep.status_code == 200, rep.text
        assert _relations(atelier, b, a)[0]["confidence"] == "probable"
        journal = sql("SELECT field, old_value, new_value FROM audit_log"
                      " WHERE id > ? AND table_name='relations'", (avant,))[0]
        assert journal == [{"field": "confidence", "old_value": "verified",
                            "new_value": "probable"}]

    def test_une_ligne_non_ecrite_rend_409_sans_journal(self, atelier):
        """Ce qu'avale un déclencheur ne se journalise pas : un déclencheur
        d'essai écarte toute modification de la fiabilité."""
        c, sql = atelier["client"], atelier["sql"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL", type_="person")
        _collecter(b, a, type_="membre")
        r = _relations(atelier, b, a, "membre")[0]
        sql("CREATE TRIGGER essai_avale BEFORE UPDATE OF confidence ON relations"
            " BEGIN SELECT RAISE(IGNORE); END")
        avant = sql("SELECT COUNT(*) AS n FROM audit_log")[0][0]["n"]
        rep = c.put(f"/api/atelier/relations/{r['id']}", headers=atelier["valid"],
                    json={"confidence": "probable"})
        assert rep.status_code == 409, rep.text
        assert sql("SELECT COUNT(*) AS n FROM audit_log")[0][0]["n"] == avant

    def test_garder_le_refus_vide_la_file_jusqu_a_une_source_nouvelle(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        a, b, _, refus_id = self._refuser_un_des_deux(atelier)
        # Un refus d'avant le motif obligatoire (15/11/2026) : il n'en a pas.
        sql("UPDATE relations_refusees SET motif=NULL WHERE id=?", (refus_id,))
        assert _files(atelier)["refus-contredits"]["reste"] == 0
        _collecter(b, a)                                  # rna et rna-bis le redisent
        f = _files(atelier)["refus-contredits"]
        assert (f["reste"], f["sans_motif"]) == (1, 1)

        assert c.post(f"/api/atelier/relations/refus/{refus_id}/garder",
                      headers=atelier["contrib"]).status_code == 403
        rep = c.post(f"/api/atelier/relations/refus/{refus_id}/garder?motif=Homonyme",
                     headers=atelier["valid"])
        assert rep.status_code == 200, rep.text
        f = _files(atelier)["refus-contredits"]
        assert (f["reste"], f["sans_motif"]) == (0, 0)
        [j] = sql("SELECT action, field FROM audit_log WHERE table_name='relations_refusees'"
                  " AND action='update'")[0]
        assert j["field"] == f"refus/{refus_id}"

        _collecter(b, a)                                  # les mêmes sources, passe suivante
        assert _files(atelier)["refus-contredits"]["reste"] == 0
        # Ni la fusion ni une modification à l'atelier ne sont une source.
        a2 = atelier["fiche"]("Amis du Four (doublon)")
        _collecter(b, a2, source="banatic")
        sql("UPDATE OR IGNORE relations SET to_id=? WHERE to_id=?", (a, a2))
        assert _files(atelier)["refus-contredits"]["reste"] == 0
        _collecter(b, a, source="sirene")                 # une source nouvelle
        assert _files(atelier)["refus-contredits"]["reste"] == 1
        [refus] = c.get("/api/atelier/relations/refus", headers=atelier["valid"]).json()["refus"]
        assert refus["motif"] == "Homonyme" and refus["revu_par"] == "valid@exemple.fr"
        assert {s["source"] for s in refus["sources"] if s["nouvelle"]} == {"sirene", "sirene-bis"}

    def test_accepter_un_candidat_refuse_rend_409_et_le_laisse_en_attente(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        a, b, frere, refus_id = self._refuser_un_des_deux(atelier)
        sql("DELETE FROM relations WHERE id=?", (frere["id"],))
        _, cid = sql("INSERT INTO relation_candidates(from_id, to_id, relation_type, confidence,"
                     " signal) VALUES(?,?,?,?,?)", (b, a, "président", "probable", "same_address"))
        rep = c.post(f"/api/candidates/{cid}/review", headers=atelier["valid"],
                     json={"action": "accept"})
        assert rep.status_code == 409, rep.text
        assert rep.json()["detail"]["refus_id"] == refus_id
        assert sql("SELECT review_status FROM relation_candidates WHERE id=?",
                   (cid,))[0][0]["review_status"] == "pending"
        assert _relations(atelier, b, a) == []
        # Rejeter reste possible.
        assert c.post(f"/api/candidates/{cid}/review", headers=atelier["valid"],
                      json={"action": "reject"}).status_code == 200

    def test_un_flux_saisi_sur_un_lien_refuse_le_dit(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        def saisir(tiers):
            rep = c.post("/api/atelier/saisies", headers=atelier["valid"], json={
                "objet": "flux", "source": SOURCE, "confidence": "confirmed", "valeurs": {
                    "type": "subvention", "year": 2025, "amount": 1500, "sens": "verse",
                    "tiers": tiers, "description": "Subvention de fonctionnement"}})
            assert rep.status_code == 201, rep.text
            return rep.json()
        premiere = saisir({"nom": "Club de pétanque fictif", "type": "association"})
        assert "lien" not in premiere
        [rel] = sql("SELECT id, to_id FROM relations WHERE relation_type='subventionné'")[0]
        refus_id = c.delete(f"/api/atelier/relations/{rel['id']}?motif=Motif d essai",
                            headers=atelier["valid"]).json()["refus_id"]
        seconde = saisir({"id": rel["to_id"]})
        assert seconde["lien"]["ecrit"] is False and seconde["lien"]["refus_id"] == refus_id
        assert sql("SELECT COUNT(*) AS n FROM financial_flows")[0][0]["n"] == 2
        assert sql("SELECT 1 FROM relations WHERE relation_type='subventionné'")[0] == []
