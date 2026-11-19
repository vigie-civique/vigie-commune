"""Intégrité de l'édition à l'atelier — les défauts relevés par l'audit du 15/11/2026.

Chaque test reproduit un défaut observé sur base fictive avant correction :
une saisie qui levait la protection d'une fiche institutionnelle, un verrou
optimiste à la seconde, une proposition périmée qui écrasait une correction
postérieure, une fiche sans nom. Vrais jetons, vraie base SQLite.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

from collectors.origine import ATELIER, INSTITUTIONNEL  # noqa: E402

MDP = "mot-de-passe-de-test"
AVANT = "2026-01-01 00:00:00"
SOURCE = {"sans_document_motif": "registre consulté en mairie"}


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

    def fiche(confidence="verified", nom="Les Amis du Four", origine=INSTITUTIONNEL):
        _, eid = sql("INSERT INTO entities(type, name, confidence, origine, updated_at) "
                     "VALUES('association', ?, ?, ?, ?)", (nom, confidence, origine, AVANT))
        return eid

    return {"client": client, "sql": sql, "fiche": fiche, "chemin_db": chemin_db,
            "contrib": compte("contrib@exemple.fr", "contributor"),
            "valid": compte("valid@exemple.fr", "validator"),
            "admin": compte("admin@exemple.fr", "admin")}


def _ligne(atelier, eid):
    return atelier["sql"]("SELECT * FROM entities WHERE id=?", (eid,))[0][0]


def _saisir_entite(atelier, nom, h=None):
    return atelier["client"].post("/api/atelier/saisies", headers=h or atelier["contrib"], json={
        "objet": "entite", "confidence": "probable",
        "valeurs": {"type": "association", "name": nom},
        "source": SOURCE})


class TestSaisieEtOrigine:
    def test_saisir_le_nom_d_une_fiche_existante_ne_change_pas_son_origine(self, atelier):
        eid = atelier["fiche"]()
        r = _saisir_entite(atelier, "Les Amis du Four")
        assert r.status_code == 201, r.text
        assert _ligne(atelier, eid)["origine"] == INSTITUTIONNEL

    def test_saisir_le_nom_d_une_fiche_existante_ne_comble_pas_ses_champs(self, atelier):
        eid = atelier["fiche"]()
        r = atelier["client"].post("/api/atelier/saisies", headers=atelier["contrib"], json={
            "objet": "entite", "confidence": "probable",
            "valeurs": {"type": "association", "name": "Les Amis du Four",
                        "address": "12 rue Inventée"},
            "source": SOURCE})
        assert r.status_code == 201, r.text
        ligne = _ligne(atelier, eid)
        assert ligne["address"] is None and ligne["updated_at"] == AVANT

    def test_une_fiche_nee_d_une_saisie_est_d_origine_atelier(self, atelier):
        r = _saisir_entite(atelier, "Le Comité des Fêtes de Valmorin")
        assert r.status_code == 201, r.text
        lignes, _ = atelier["sql"]("SELECT origine FROM entities WHERE name=?",
                                   ("Le Comité des Fêtes de Valmorin",))
        assert [l["origine"] for l in lignes] == [ATELIER]

    @pytest.mark.parametrize("nom", ["", "   ", "\t \n"])
    def test_un_nom_vide_ou_d_espaces_est_refuse(self, atelier, nom):
        r = _saisir_entite(atelier, nom)
        assert r.status_code == 400
        assert "obligatoire" in r.text
        assert atelier["sql"]("SELECT COUNT(*) n FROM entities")[0][0]["n"] == 0


# Ces essais portent sur le verrou, pas sur l'origine : leurs fiches sont d'origine
# `atelier`, que la garde des fiches institutionnelles (motif exigé) ne concerne pas.
class TestVerrouOptimiste:
    def test_chaque_ecriture_deplace_updated_at_meme_dans_la_meme_seconde(self, atelier):
        c, h, eid = atelier["client"], atelier["valid"], atelier["fiche"](origine=ATELIER)
        lu = AVANT
        vus = []
        for nom in ("Nom un", "Nom deux", "Nom trois"):
            r = c.patch(f"/api/atelier/entities/{eid}", headers=h,
                        json={"updated_at": lu, "name": nom})
            assert r.status_code == 200, r.text
            lu = r.json()["updated_at"]
            vus.append(lu)
        assert len(set(vus)) == 3
        assert vus == sorted(vus)

    def test_une_ecriture_sur_une_lecture_perimee_est_refusee(self, atelier):
        """Le cas reproduit le 15/11 : trois écritures dans la même seconde, la
        troisième faite sur la lecture d'avant la deuxième — elle passait."""
        c, eid = atelier["client"], atelier["fiche"](origine=ATELIER)
        r1 = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["valid"],
                     json={"updated_at": AVANT, "name": "Version un"})
        lu_par_les_deux = r1.json()["updated_at"]
        r2 = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["admin"],
                     json={"updated_at": lu_par_les_deux, "name": "Version deux"})
        assert r2.status_code == 200
        r3 = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["valid"],
                     json={"updated_at": lu_par_les_deux, "address": "1 rue du Four"})
        assert r3.status_code == 409, r3.text
        detail = r3.json()["detail"]
        assert detail["par"] == "admin@exemple.fr"
        assert [ch["champ"] for ch in detail["champs"]] == ["name"]
        assert _ligne(atelier, eid)["name"] == "Version deux"

    def test_la_migration_remplace_l_ancien_declencheur(self, atelier, monkeypatch):
        from collectors import db as db_mod
        c = sqlite3.connect(atelier["chemin_db"])
        c.executescript("""
            DROP TRIGGER entities_updated_at;
            CREATE TRIGGER entities_updated_at AFTER UPDATE ON entities
            BEGIN UPDATE entities SET updated_at = datetime('now') WHERE id = NEW.id; END;
        """)
        c.close()
        db_mod.init_db()
        sql = atelier["sql"]("SELECT sql FROM sqlite_master WHERE name='entities_updated_at'")
        assert "%f" in sql[0][0]["sql"]
        # À jour, il n'est plus reconstruit : aucun DROP au second passage.
        vus = []
        conn = sqlite3.connect(atelier["chemin_db"])
        conn.set_trace_callback(vus.append)
        db_mod._rattraper_declencheurs(conn, db_mod.SCHEMA_PATH.read_text())
        conn.close()
        assert not [v for v in vus if v.startswith("DROP TRIGGER")]

    def test_une_date_illisible_ne_vide_pas_le_verrou(self, atelier):
        eid = atelier["fiche"]()
        atelier["sql"]("UPDATE entities SET updated_at='9999-zz' WHERE id=?", (eid,))
        atelier["sql"]("UPDATE entities SET name='Autre' WHERE id=?", (eid,))
        assert _ligne(atelier, eid)["updated_at"] is not None


class TestPropositionPerimee:
    def test_une_proposition_perimee_ne_s_accepte_pas(self, atelier):
        c, eid = atelier["client"], atelier["fiche"](origine=ATELIER)
        r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["contrib"],
                    json={"updated_at": AVANT, "name": "Nom proposé"})
        assert r.status_code == 202, r.text
        pid = r.json()["proposition"]
        r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["valid"],
                    json={"updated_at": AVANT, "name": "Nom corrigé par le validateur"})
        assert r.status_code == 200, r.text

        r = c.post(f"/api/atelier/propositions/{pid}/decision", headers=atelier["valid"],
                   json={"accepter": True, "motif": ""})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["champs"][0]["apres"] == "Nom corrigé par le validateur"
        assert _ligne(atelier, eid)["name"] == "Nom corrigé par le validateur"

        r = c.post(f"/api/atelier/propositions/{pid}/decision", headers=atelier["valid"],
                   json={"accepter": False, "motif": "la fiche a changé depuis"})
        assert r.status_code == 200, r.text

    def test_null_et_vide_se_valent(self, atelier):
        """Une adresse passée de NULL à "" n'a pas changé : la proposition
        sur elle s'accepte."""
        c, eid = atelier["client"], atelier["fiche"]()
        r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["contrib"],
                    json={"updated_at": AVANT, "address": "1 place du Four"})
        pid = r.json()["proposition"]
        atelier["sql"]("UPDATE entities SET address='' WHERE id=?", (eid,))
        r = c.post(f"/api/atelier/propositions/{pid}/decision", headers=atelier["valid"],
                   json={"accepter": True, "motif": ""})
        assert r.status_code == 200, r.text

    def test_une_proposition_a_jour_s_accepte(self, atelier):
        c, eid = atelier["client"], atelier["fiche"](origine=ATELIER)
        r = c.patch(f"/api/atelier/entities/{eid}", headers=atelier["contrib"],
                    json={"updated_at": AVANT, "name": "Nom proposé"})
        pid = r.json()["proposition"]
        r = c.post(f"/api/atelier/propositions/{pid}/decision", headers=atelier["valid"],
                   json={"accepter": True, "motif": ""})
        assert r.status_code == 200, r.text
        assert _ligne(atelier, eid)["name"] == "Nom proposé"


def test_la_reecriture_entiere_d_une_fiche_n_existe_plus(atelier):
    """PUT réécrivait sans verrou ni validation ; personne ne l'appelait."""
    eid = atelier["fiche"]()
    r = atelier["client"].put(f"/api/atelier/entities/{eid}", headers=atelier["admin"],
                              json={"name": ""})
    assert r.status_code == 405
    assert _ligne(atelier, eid)["name"] == "Les Amis du Four"
