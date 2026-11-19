"""Ce qu'un contributeur lit et écrit à l'atelier — défauts de l'audit du 15/11/2026.

Avant d'ouvrir l'atelier à des bénévoles : ce que la fiche lui cache ne doit
pas ressortir ailleurs (recherche par sens, liens présumés), il ne réécrit pas
ce qu'un autre a posé, et le validateur voit ce qui a été écrit avant de
retenir. Vrais jetons, vraie base SQLite.
"""
from __future__ import annotations

import sqlite3

import pytest

np = pytest.importorskip("numpy", reason="job « tests-deps » : pip install -r requirements.txt")
pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

MDP = "mot-de-passe-de-test"
AVANT = "2026-01-01 00:00:00"
VECTEUR = np.ones(8, dtype=np.float32)


class _OllamaConstant:
    """Le même vecteur pour tout : la recherche rend tout l'index, et l'essai
    ne regarde que CE QUI en sort, pas l'ordre."""
    class _R:
        def __init__(self, d):
            self._d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    def get(self, url, timeout=None):
        return self._R({"models": [{"name": "nomic-embed-text:latest",
                                    "model": "nomic-embed-text:latest",
                                    "digest": "0" * 64}]})

    def post(self, url, json=None, timeout=None):
        return self._R({"embedding": VECTEUR.tolist()})


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
    monkeypatch.setattr(api_auth, "_echecs", {})
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

    def fiche(type_="association", nom="Les Amis du Four", confidence="probable"):
        _, eid = sql("INSERT INTO entities(type, name, confidence, updated_at) VALUES(?,?,?,?)",
                     (type_, nom, confidence, AVANT))
        return eid

    return {"api": api, "client": client, "sql": sql, "fiche": fiche, "compte": compte,
            "c1": compte("c1@exemple.fr", "contributor"),
            "c2": compte("c2@exemple.fr", "contributor"),
            "v1": compte("v1@exemple.fr", "validator"),
            "v2": compte("v2@exemple.fr", "validator")}


# ─── 1. La recherche par sens ne rend pas les notes sur les personnes ─────────

class TestRechercheParSens:
    @pytest.fixture
    def index(self, atelier, monkeypatch):
        api = atelier["api"]
        monkeypatch.setattr(api, "RAG_ENABLED", True)
        monkeypatch.setattr(api, "_requests", _OllamaConstant())
        monkeypatch.setattr(api, "_modele_incompatible", lambda conn: None)
        personne = atelier["fiche"]("person", "Aldéric VALMOR")
        asso = atelier["fiche"]()
        for sid, eid, table, texte in ((1, personne, "entity_notes", "note sur la personne"),
                                       (2, asso, "entity_notes", "note sur l'association"),
                                       (3, None, "events", "délibération")):
            atelier["sql"]("INSERT INTO embeddings(source_table, source_id, entity_id,"
                           " chunk_text, vector) VALUES(?,?,?,?,?)",
                           (table, sid, eid, texte, VECTEUR.tobytes()))

    def _textes(self, atelier, h):
        r = atelier["client"].get("/api/rag/search", headers=h, params={"q": "x", "limit": 50})
        assert r.status_code == 200, r.text
        return sorted(x["chunk_text"] for x in r.json())

    def test_le_contributeur_ne_lit_pas_une_note_sur_une_personne(self, atelier, index):
        assert self._textes(atelier, atelier["c1"]) == ["délibération", "note sur l'association"]

    def test_le_validateur_la_lit(self, atelier, index):
        assert "note sur la personne" in self._textes(atelier, atelier["v1"])


# ─── 2. Les liens présumés ────────────────────────────────────────────────────

class TestLiensPresumes:
    def _deux_personnes(self, atelier):
        a = atelier["fiche"]("person", "Aldéric VALMOR")
        b = atelier["fiche"]("person", "Orlane VALMOR")
        atelier["sql"]("INSERT INTO relations(from_id, to_id, relation_type, source, confidence)"
                       " VALUES(?,?,'famille_présumé','detect_links','hypothesis')", (a, b))
        atelier["sql"]("INSERT INTO relations(from_id, to_id, relation_type, source, confidence)"
                       " VALUES(?,?,'membre','manual','verified')", (a, b))
        return a, b

    def _types(self, atelier, h, eid):
        r = atelier["client"].get(f"/api/atelier/entities/{eid}", headers=h)
        assert r.status_code == 200, r.text
        return sorted(x["relation_type"] for x in r.json()["relations"])

    def test_la_fiche_ne_les_rend_pas_au_contributeur(self, atelier):
        a, _ = self._deux_personnes(atelier)
        assert self._types(atelier, atelier["c1"], a) == ["membre"]
        assert self._types(atelier, atelier["v1"], a) == ["famille_présumé", "membre"]

    def test_ni_le_journal_ni_les_compteurs_ne_les_trahissent(self, atelier):
        c = atelier["client"]
        a, b = self._deux_personnes(atelier)
        [(rid,)] = [tuple(x.values()) for x in atelier["sql"](
            "SELECT id FROM relations WHERE relation_type='membre'")[0]]
        assert c.put(f"/api/atelier/relations/{rid}", headers=atelier["v1"],
                     json={"relation_type": "époux_présumé"}).status_code == 200
        journal = c.get(f"/api/atelier/entities/{a}", headers=atelier["c1"]).json()["audit"]
        assert not any("présumé" in str(x.get("new_value")) + str(x.get("old_value"))
                       for x in journal)
        assert any("présumé" in str(x.get("new_value")) for x in
                   c.get(f"/api/atelier/entities/{a}", headers=atelier["v1"]).json()["audit"])
        def compte(h):
            items = c.get("/api/atelier/workqueue", headers=h,
                          params={"status": "jamais_relu"}).json()["items"]
            return [x["rel_count"] for x in items if x["id"] == a]
        assert compte(atelier["c1"]) == [0] and compte(atelier["v1"]) == [2]

    def test_le_contributeur_n_en_pose_pas(self, atelier):
        a, b = atelier["fiche"]("person", "A B"), atelier["fiche"]("person", "C D")
        r = atelier["client"].post(f"/api/atelier/entities/{a}/relations", headers=atelier["c1"],
                                   json={"direction": "from", "other_entity_id": b,
                                         "relation_type": "époux_présumé",
                                         "confidence": "hypothesis"})
        assert r.status_code == 403


# ─── 3. Un compte désactivé ───────────────────────────────────────────────────

class TestCompteDesactive:
    def test_desactiver_clot_ses_sessions_et_la_reactivation_ne_les_rend_pas(self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        admin = atelier["compte"]("admin@exemple.fr", "admin")
        [(cid,)] = [tuple(x.values()) for x in
                    sql("SELECT id FROM users WHERE email='c2@exemple.fr'")[0]]
        assert c.patch(f"/api/admin/comptes/{cid}", headers=admin,
                       json={"actif": False}).status_code == 200
        assert c.patch(f"/api/admin/comptes/{cid}", headers=admin,
                       json={"actif": True}).status_code == 200
        r = c.get("/api/atelier/files", headers=atelier["c2"])
        assert r.status_code == 401, "l'ancien jeton d'accès ne vaut plus"
        version = sql("SELECT sessions_version FROM users WHERE id=?", (cid,))[0][0]
        assert (version["sessions_version"] or 0) >= 1

    def test_la_connexion_d_un_compte_desactive_ne_dit_pas_que_le_mot_de_passe_est_juste(
            self, atelier):
        c, sql = atelier["client"], atelier["sql"]
        sql("UPDATE users SET desactive_le=datetime('now') WHERE email='c2@exemple.fr'")
        juste = c.post("/api/auth/login", json={"email": "c2@exemple.fr", "password": MDP})
        faux = c.post("/api/auth/login", json={"email": "c2@exemple.fr", "password": "x" * 14})
        assert (juste.status_code, juste.json()) == (faux.status_code, faux.json()) \
            == (401, {"detail": "Email ou mot de passe incorrect"})


# ─── 4. Ce qu'un autre a posé ─────────────────────────────────────────────────

class TestObjetsDAutrui:
    def test_un_contributeur_ne_reecrit_pas_la_note_d_un_autre(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        nid = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["c2"],
                     json={"note": "Siège déplacé en 2024", "confidence": "probable"}).json()["id"]
        r = c.put(f"/api/atelier/notes/{nid}", headers=atelier["c1"],
                  json={"note": "réécrite", "confidence": "verified"})
        assert r.status_code == 403
        r = c.put(f"/api/atelier/notes/{nid}", headers=atelier["c2"],
                  json={"note": "Siège déplacé en 2025"})
        assert r.status_code == 200 and r.json()["note"] == "Siège déplacé en 2025"
        r = c.put(f"/api/atelier/notes/{nid}", headers=atelier["c2"],
                  json={"confidence": "verified"})
        assert r.status_code == 403, "la fiabilité publiable revient au validateur"

    def test_une_note_de_contributeur_nait_probable(self, atelier):
        """Elle naissait `verified` par défaut, quel que soit l'auteur."""
        c, eid = atelier["client"], atelier["fiche"]()
        r = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["c1"],
                   json={"note": "Ouvert le samedi"})
        assert r.status_code == 200 and r.json()["confidence"] == "probable"
        r = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["c1"],
                   json={"note": "Ouvert le dimanche", "confidence": "verified"})
        assert r.status_code == 403
        r = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["v1"],
                   json={"note": "Ouvert le lundi"})
        assert r.status_code == 200 and r.json()["confidence"] == "verified"

    def test_un_contributeur_ne_modifie_pas_la_relation_probable_d_un_autre(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"](), atelier["fiche"](nom="Comité des fêtes")
        rid = c.post(f"/api/atelier/entities/{a}/relations", headers=atelier["c2"],
                     json={"direction": "from", "other_entity_id": b,
                           "relation_type": "membre", "confidence": "probable"}).json()["id"]
        r = c.put(f"/api/atelier/relations/{rid}", headers=atelier["c1"],
                  json={"source": "atelier:999"})
        assert r.status_code == 403
        r = c.put(f"/api/atelier/relations/{rid}", headers=atelier["c2"],
                  json={"since": "2020-01-01"})
        assert r.status_code == 200, r.text

    def test_la_note_d_une_decision_d_autrui_ne_se_reecrit_pas(self, atelier):
        """La note d'une décision est le motif publié d'une rectification
        (`note_revue`) : un contributeur la remplaçait et devenait l'auteur."""
        c, eid = atelier["client"], atelier["fiche"]()
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["v1"],
                    json={"review_status": "ecarte", "note": "doublon de la fiche 12"})
        assert r.status_code == 200, r.text
        lu = r.json()["reviewed_at"]
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["c1"],
                    json={"note": "je ne crois pas"})
        assert r.status_code == 403 and "Proposez une correction" in r.json()["detail"]
        # Renvoyer la note telle quelle (l'écran la préremplit) n'écrit rien.
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["c1"],
                    json={"note": "doublon de la fiche 12"})
        assert r.status_code == 200, r.text
        [a], _ = atelier["sql"]("SELECT note, reviewed_by, reviewed_at FROM annotations "
                                "WHERE object_type='entity' AND object_id=?", (eid,))
        assert (a["note"], a["reviewed_by"], a["reviewed_at"]) == \
            ("doublon de la fiche 12", "v1@exemple.fr", lu)

    def test_sans_decision_d_autrui_le_contributeur_note(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        for texte in ("à vérifier", "à vérifier en mairie"):
            r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["c1"],
                        json={"note": texte})
            assert r.status_code == 200, r.text
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["c2"],
                    json={"note": "autre chose"})
        assert r.status_code == 403

    def test_une_relation_collectee_ne_se_modifie_pas_par_un_contributeur(self, atelier):
        a, b = atelier["fiche"](), atelier["fiche"](nom="Comité des fêtes")
        _, rid = atelier["sql"]("INSERT INTO relations(from_id, to_id, relation_type, source,"
                                " confidence) VALUES(?,?,'membre','rna','probable')", (a, b))
        r = atelier["client"].put(f"/api/atelier/relations/{rid}", headers=atelier["c1"],
                                  json={"since": "2020-01-01"})
        assert r.status_code == 403

    def test_un_contributeur_ne_remplace_pas_la_reponse_d_un_autre_a_une_tache(self, atelier):
        from collectors import taches
        conn = sqlite3.connect(atelier["api"].DB_PATH)
        conn.row_factory = sqlite3.Row
        taches.assurer_schema(conn)
        tid = conn.execute("INSERT INTO taches(lacune_id, dossier, nature, libelle) "
                           "VALUES('eau:q-1', 'eau', 'verser', 'Qu''a répondu la mairie ?')"
                           ).lastrowid
        uid = {r["email"]: r["id"] for r in conn.execute("SELECT id, email FROM users")}
        contenu = {"resume": "réponse reçue", "recu_le": "2026-11-01", "ou": "boîte"}
        c2 = {"id": uid["c2@exemple.fr"], "role": "contributor"}
        c1 = {"id": uid["c1@exemple.fr"], "role": "contributor"}
        taches.repondre(conn, tid, c2, contenu)
        with pytest.raises(taches.Refus) as e:
            taches.repondre(conn, tid, c1, {**contenu, "resume": "autre"})
        assert e.value.args and "autre compte" in e.value.args[0]
        assert taches.repondre(conn, tid, c2, {**contenu, "resume": "corrigée"})["propose_par"] \
            == uid["c2@exemple.fr"]
        conn.close()

    def test_un_candidat_reserve_par_un_validateur_ne_se_tranche_pas_par_un_autre(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("person", "A B"), atelier["fiche"]("person", "C D")
        _, cid = atelier["sql"](
            "INSERT INTO relation_candidates(from_id, to_id, relation_type, confidence, signal) "
            "VALUES(?,?,'famille_présumé','hypothesis','same_surname')", (a, b))
        assert c.post(f"/api/atelier/queue/{cid}/claim", headers=atelier["v1"],
                      json={"table": "relation_candidates"}).status_code == 200
        r = c.post(f"/api/candidates/{cid}/review", headers=atelier["v2"],
                   json={"action": "reject", "note": ""})
        assert r.status_code == 409, r.text
        r = c.post(f"/api/candidates/{cid}/review", headers=atelier["v1"],
                   json={"action": "reject", "note": ""})
        assert r.status_code == 200, r.text


# ─── 5. Deux onglets : la seconde proposition reprend la première ─────────────

def test_une_seconde_proposition_reprend_la_premiere_et_le_dit(atelier):
    c, eid = atelier["client"], atelier["fiche"](confidence="verified")
    r1 = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["c1"],
                 json={"updated_at": AVANT, "responsible": "Mme Orlane"})
    assert r1.status_code == 202 and r1.json()["reprend"] == []
    r2 = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["c1"],
                 json={"updated_at": AVANT, "name": "Les Amis du Four de Valmorin"})
    assert r2.status_code == 202
    assert r2.json()["reprend"] == [r1.json()["proposition"]]
    assert "reprend aussi" in r2.json()["message"]
    [p], _ = atelier["sql"]("SELECT charge FROM propositions WHERE etat='en_attente'")
    assert "Mme Orlane" in p["charge"] and "de Valmorin" in p["charge"]


# ─── 6. Un contact sur une personne ───────────────────────────────────────────

def test_un_contact_sur_une_personne_revient_au_validateur_et_se_journalise(atelier):
    c = atelier["client"]
    personne, asso = atelier["fiche"]("person", "A B"), atelier["fiche"]()
    contact = {"type": "phone", "value": "01 99 00 13 00"}
    assert c.post(f"/api/atelier/entities/{personne}/contacts", headers=atelier["c1"],
                  json=contact).status_code == 403
    assert c.post(f"/api/atelier/entities/{asso}/contacts", headers=atelier["c1"],
                  json=contact).status_code == 200
    assert c.post(f"/api/atelier/entities/{personne}/contacts", headers=atelier["v1"],
                  json=contact).status_code == 200
    journal, _ = atelier["sql"]("SELECT entity_id FROM audit_log WHERE table_name='contacts'")
    assert sorted(j["entity_id"] for j in journal) == sorted([asso, personne])


# ─── 6 bis. Personne : note de décision et contacts réservés ──────────────────

class TestPersonneDecisionEtContacts:
    """Sur une personne, le contributeur n'écrit pas la note d'une décision et
    ne lit aucun contact, quel qu'en soit le type (décision du 16/11/2026)."""

    MOTIF = "motif de décision confidentiel PD31"
    TEL = "01 99 00 14 00"

    def _annoter(self, atelier, h, eid, **corps):
        return atelier["client"].patch(f"/api/atelier/annotations/entity/{eid}",
                                       headers=h, json=corps)

    def _lignes(self, atelier):
        return (atelier["sql"]("SELECT * FROM annotations")[0],
                atelier["sql"]("SELECT * FROM audit_log")[0])

    def test_le_contributeur_n_ecrit_pas_la_note_d_une_decision_sur_une_personne(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        avant = self._lignes(atelier)
        r = self._annoter(atelier, atelier["c1"], eid, note="texte libre sur quelqu'un")
        assert r.status_code == 403, r.text
        assert self._lignes(atelier) == avant, "rien d'écrit : ni annotations ni audit_log"

    def test_le_contributeur_qui_renvoie_le_corps_sans_la_note_ne_prend_pas_de_403(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        assert self._annoter(atelier, atelier["v1"], eid, review_status="a_revoir",
                             note=self.MOTIF).status_code == 200
        for corps in ({"review_status": "a_revoir"}, {"review_status": "a_revoir", "note": None},
                      {"review_status": "a_revoir", "note": ""}):
            r = self._annoter(atelier, atelier["c1"], eid, **corps)
            assert r.status_code == 200, (corps, r.text)
            assert _note_en_clair(atelier, eid) == self.MOTIF, "note existante intacte"
            assert self.MOTIF not in str(r.json())

    def test_le_validateur_ecrit_et_lit_la_note_d_une_decision_sur_une_personne(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        assert self._annoter(atelier, atelier["v1"], eid, review_status="a_revoir",
                             note=self.MOTIF).status_code == 200
        assert _note_en_clair(atelier, eid) == self.MOTIF
        fiche = atelier["client"].get(f"/api/atelier/entities/{eid}", headers=atelier["v1"]).json()
        assert fiche["verdict_note"] == self.MOTIF

    def test_hors_personne_le_contributeur_ecrit_toujours_la_note_d_une_decision(self, atelier):
        eid = atelier["fiche"]()
        r = self._annoter(atelier, atelier["c1"], eid, note="siège déplacé")
        assert r.status_code == 200, r.text
        assert _note_en_clair(atelier, eid) == "siège déplacé"

    def test_les_contacts_d_une_personne_ne_sortent_pas_pour_le_contributeur(self, atelier):
        c = atelier["client"]
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        for contact in ({"type": "phone", "value": self.TEL},
                        {"type": "website", "value": "https://perso.example.org/valmor"}):
            assert c.post(f"/api/atelier/entities/{eid}/contacts", headers=atelier["v1"],
                          json=contact).status_code == 200
        r = c.get(f"/api/atelier/entities/{eid}", headers=atelier["c1"])
        assert r.status_code == 200, r.text
        brut = r.json()
        assert brut["contacts"] == [] and brut["contacts_reserves"] is True
        assert self.TEL not in r.text and "perso.example.org" not in r.text
        # La liste des fiches ne porte pas non plus le site ni le compte.
        liste = c.get("/api/atelier/workqueue", headers=atelier["c1"],
                      params={"type": "person"}).json()["items"]
        mien = [x for x in liste if x["id"] == eid][0]
        assert mien["website"] is None and mien["contacts_count"] is None
        assert "perso.example.org" not in str(liste)

    def test_le_validateur_lit_les_contacts_d_une_personne(self, atelier):
        c = atelier["client"]
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        c.post(f"/api/atelier/entities/{eid}/contacts", headers=atelier["v1"],
               json={"type": "phone", "value": self.TEL})
        brut = c.get(f"/api/atelier/entities/{eid}", headers=atelier["v1"]).json()
        assert [x["value"] for x in brut["contacts"]] == [self.TEL]
        assert brut["contacts_reserves"] is False

    def test_hors_personne_le_contributeur_lit_les_contacts(self, atelier):
        c = atelier["client"]
        eid = atelier["fiche"]()
        c.post(f"/api/atelier/entities/{eid}/contacts", headers=atelier["c1"],
               json={"type": "phone", "value": self.TEL})
        brut = c.get(f"/api/atelier/entities/{eid}", headers=atelier["c1"]).json()
        assert [x["value"] for x in brut["contacts"]] == [self.TEL]
        assert brut["contacts_reserves"] is False


def _note_en_clair(atelier, eid):
    lignes, _ = atelier["sql"]("SELECT note FROM annotations WHERE object_type='entity'"
                               " AND object_id=?", (eid,))
    return lignes[0]["note"] if lignes else None


# ─── 7. Avant de retenir, ce que des contributeurs ont écrit ──────────────────

def test_le_validateur_voit_ce_que_des_contributeurs_ont_ecrit_avant_de_retenir(atelier):
    c, eid = atelier["client"], atelier["fiche"]()      # probable : écriture directe

    def vus(h=atelier["v1"]):
        return [(x["objet"], x["champ"], x["par"]) for x in
                c.get(f"/api/atelier/entities/{eid}", headers=h).json()
                ["ecrit_par_contributeurs"]]

    def compte():
        items = c.get("/api/atelier/workqueue", headers=atelier["v1"],
                      params={"status": "jamais_relu"}).json()["items"]
        return [x["ecrit_par_contributeurs"] for x in items if x["id"] == eid]

    r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["c1"],
                json={"updated_at": AVANT, "name": "Garage du Test"})
    assert r.status_code == 200, r.text
    assert c.post(f"/api/atelier/entities/{eid}/contacts", headers=atelier["c1"],
                  json={"type": "phone", "value": "01 99 00 13 00"}).status_code == 200
    assert vus() == [("entities", "name", "c1@exemple.fr"),
                     ("contacts", f"contact/1", "c1@exemple.fr")]
    assert compte() == [2]
    assert "ecrit_par_contributeurs" not in \
        c.get(f"/api/atelier/entities/{eid}", headers=atelier["c1"]).json()

    # Une simple note ne vide pas la liste : seule une décision qui change
    # le verdict la borne.
    assert c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["v1"],
                   json={"note": "vu"}).status_code == 200
    assert len(vus()) == 2
    assert c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["v1"],
                   json={"review_status": "a_revoir"}).status_code == 200
    assert vus() == []
    lu = c.get(f"/api/atelier/entities/{eid}", headers=atelier["v1"]).json()["updated_at"]
    assert c.patch(f"/api/atelier/entities/{eid}", headers=atelier["c1"],
                   json={"updated_at": lu, "address": "1 rue du Four"}).status_code == 200
    assert vus() == [("entities", "address", "c1@exemple.fr")]


# ─── 5. L'historique d'une fiche ne rend pas ce que la fiche cache ────────────

class TestHistoriqueDeFicheReservee:
    """Sur une personne, le contributeur lit « qui, quand, quel champ », jamais
    le contenu : ni le texte d'une note (avant / après), ni la note d'une
    décision. Refus par défaut, toutes tables."""

    ANCIEN = "ancien texte confidentiel ZX81"
    NOUVEAU = "nouveau texte confidentiel QW42"
    MOTIF = "motif de décision confidentiel KJ77"

    def _audit(self, atelier, h, eid):
        r = atelier["client"].get(f"/api/atelier/entities/{eid}", headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    def _note_modifiee(self, atelier, eid):
        c = atelier["client"]
        r = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["v1"],
                   json={"note": self.ANCIEN})
        assert r.status_code == 200, r.text
        r = c.put(f"/api/atelier/notes/{r.json()['id']}", headers=atelier["v1"],
                  json={"note": self.NOUVEAU})
        assert r.status_code == 200, r.text

    def test_le_texte_d_une_note_sur_une_personne_ne_sort_pas(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        self._note_modifiee(atelier, eid)
        brut = self._audit(atelier, atelier["c1"], eid)
        assert brut["notes"] == [] and brut["audit"], "la trace reste"
        assert self.ANCIEN not in str(brut) and self.NOUVEAU not in str(brut)
        assert all("old_value" not in x and "new_value" not in x for x in brut["audit"])
        assert {x["field"] for x in brut["audit"]} >= {"note/1"}

    def test_la_note_d_une_decision_sur_une_personne_ne_sort_pas(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        r = atelier["client"].patch(f"/api/atelier/annotations/entity/{eid}",
                                    headers=atelier["v1"],
                                    json={"review_status": "a_revoir", "note": self.MOTIF})
        assert r.status_code == 200, r.text
        brut = self._audit(atelier, atelier["c1"], eid)
        assert any(x["action"] == "annotate" for x in brut["audit"])
        assert self.MOTIF not in str(brut["audit"])

    def test_le_validateur_lit_toujours_les_valeurs(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        self._note_modifiee(atelier, eid)
        atelier["client"].patch(f"/api/atelier/annotations/entity/{eid}",
                                headers=atelier["v1"],
                                json={"review_status": "a_revoir", "note": self.MOTIF})
        audit = self._audit(atelier, atelier["v1"], eid)["audit"]
        textes = str(audit)
        assert self.ANCIEN in textes and self.NOUVEAU in textes and self.MOTIF in textes

    def test_hors_personne_le_contributeur_lit_l_historique_des_notes(self, atelier):
        eid = atelier["fiche"]()
        c = atelier["client"]
        r = c.post(f"/api/atelier/entities/{eid}/notes", headers=atelier["c1"],
                   json={"note": self.ANCIEN})
        assert r.status_code == 200, r.text
        assert c.put(f"/api/atelier/notes/{r.json()['id']}", headers=atelier["c1"],
                     json={"note": self.NOUVEAU}).status_code == 200
        audit = self._audit(atelier, atelier["c1"], eid)["audit"]
        maj = [x for x in audit if x["action"] == "update"]
        assert [(x["old_value"], x["new_value"]) for x in maj] == [(self.ANCIEN, self.NOUVEAU)]

    def test_les_presumes_restent_filtres_sur_une_personne(self, atelier):
        a = atelier["fiche"]("person", "Aldéric VALMOR")
        b = atelier["fiche"]("person", "Orlane VALMOR")
        atelier["sql"]("INSERT INTO relations(from_id, to_id, relation_type, source, confidence)"
                       " VALUES(?,?,'membre','manual','verified')", (a, b))
        rid = atelier["sql"]("SELECT id FROM relations")[0][0]["id"]
        assert atelier["client"].put(f"/api/atelier/relations/{rid}", headers=atelier["v1"],
                                     json={"relation_type": "époux_présumé"}).status_code == 200
        audit = self._audit(atelier, atelier["c1"], a)["audit"]
        assert not any(x["field"] and "relation" in str(x["field"]) and x["action"] == "update"
                       for x in audit)

    def test_la_note_de_decision_ne_sort_ni_du_corps_ni_du_409(self, atelier):
        eid = atelier["fiche"]("person", "Aldéric VALMOR")
        c = atelier["client"]
        assert c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["v1"],
                       json={"review_status": "a_revoir", "note": self.MOTIF}).status_code == 200
        assert self.MOTIF not in str(self._audit(atelier, atelier["c1"], eid))
        assert self.MOTIF in str(self._audit(atelier, atelier["v1"], eid))
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["c1"],
                    json={"lu_le": "2000-01-01 00:00:00", "note": "x"})
        assert r.status_code == 409 and self.MOTIF not in r.text
        r = c.patch(f"/api/atelier/annotations/entity/{eid}", headers=atelier["v1"],
                    json={"lu_le": "2000-01-01 00:00:00", "note": "x"})
        assert r.status_code == 409 and self.MOTIF in r.text
