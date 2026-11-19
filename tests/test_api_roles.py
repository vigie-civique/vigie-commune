"""Rôles emboîtés et comptes sur invitation — tenus par l'API, pas par l'interface.

Arbitré par Julien le 17/09/2026 :

  * le contributeur propose sans trancher ; le validateur fait tout ce que fait
    le contributeur, tranche et voit les analyses ; l'admin fait tout ce que fait
    le validateur, gère les comptes et publie ;
  * on n'entre dans l'atelier que sur invitation d'un admin, qui choisit le rôle.

Constaté avant : `contributor` et `validator` avaient les mêmes droits — un
contributeur rendait une fiche publiable, rejetait une délibération, lisait
l'analyse « familles » ; et les comptes ne se créaient qu'en ligne de commande.

Ces tests passent par de VRAIS jetons, obtenus à la connexion : le verrou global
de `api.py`, les dépendances et le contrôle de session sont tous traversés.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

MDP = "mot-de-passe-de-test"
AVANT = "2026-01-01 00:00:00"


@pytest.fixture
def atelier(tmp_path, monkeypatch, schema_sql):
    chemin_db = tmp_path / "instance.db"
    conn = sqlite3.connect(chemin_db)
    conn.executescript(schema_sql)
    conn.close()

    import api
    import api_auth
    from collectors import db as db_mod
    for module in (api, api_auth, db_mod):
        monkeypatch.setattr(module, "DB_PATH", chemin_db)
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
        r = client.post("/api/auth/login", json={"email": email, "password": MDP})
        # Le jeton de rafraîchissement ne vit que dans le cookie.
        j = {**r.json(), "refresh_token": r.cookies.get("atelier_refresh")}
        return {"Authorization": f"Bearer {j['access_token']}"}, j

    def fiche():
        _, eid = sql("INSERT INTO entities(type, name, updated_at) "
                     "VALUES('association', 'Les Amis du Four', ?)", (AVANT,))
        return eid

    return {"client": client, "sql": sql, "compte": compte, "fiche": fiche}


# ─── La matrice des rôles ─────────────────────────────────────────────────────

class TestContributeur:
    """Propose : corrige, note, saisit en `probable` — ne tranche rien."""

    def test_corrige_une_fiche_mais_ne_la_rend_pas_publiable(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = c.patch(f"/api/atelier/entities/{eid}", headers=h,
                    json={"updated_at": AVANT, "address": "1 rue Quilivert"})
        # 202 depuis le 02/10/2026 : la fiche est publiée (`verified` par
        # défaut), la correction est une PROPOSITION — cf. test_api_propositions.
        assert r.status_code == 202
        u = r.json()["updated_at"]
        r = c.patch(f"/api/atelier/entities/{eid}", headers=h,
                    json={"updated_at": u, "confidence": "probable"})
        assert r.status_code == 403
        assert "validateur" in r.json()["detail"]["message"]

    def test_ne_tranche_ni_statut_ni_revue(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        assert c.patch(f"/api/atelier/entities/{eid}/status", headers=h,
                       json={"validation_status": "verified"}).status_code == 403
        _, did = atelier["sql"]("INSERT INTO events(type, title, date, source) "
                                "VALUES('deliberation', 'Cantine', '2026-09-10', 'x')")
        assert c.patch(f"/api/atelier/annotations/deliberation/{did}", headers=h,
                       json={"review_status": "rejected"}).status_code == 403
        # …mais il annote : une note n'est pas un verdict
        assert c.patch(f"/api/atelier/annotations/deliberation/{did}", headers=h,
                       json={"note": "à vérifier sur le PV papier"}).status_code == 200

    def test_ne_saisit_pas_une_donnee_confirmee(self, atelier):
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].post("/api/atelier/saisies", headers=h, json={
            "objet": "flux", "valeurs": {}, "source": {}, "confidence": "confirmed"})
        assert r.status_code == 403

    def test_un_site_quil_ajoute_attend_un_validateur(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = c.post(f"/api/atelier/entities/{eid}/websites", headers=h,
                   json={"url": "https://four.fr"})
        assert r.status_code == 200 and r.json()["status"] == "candidate"
        assert c.patch(f"/api/atelier/websites/{r.json()['id']}", headers=h,
                       json={"status": "validated"}).status_code == 403

    def test_ne_voit_pas_les_analyses(self, atelier):
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        for route in ("familles", "mandats-croises", "adresses-partagees"):
            assert atelier["client"].get(f"/api/analyses/{route}", headers=h).status_code == 403

    def test_ne_gere_pas_les_comptes(self, atelier):
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        assert atelier["client"].get("/api/admin/comptes", headers=h).status_code == 403


class TestValidateur:
    def test_fait_ce_que_fait_le_contributeur_et_tranche(self, atelier):
        c, eid = atelier["client"], atelier["fiche"]()
        h, _ = atelier["compte"]("valid@exemple.fr", "validator")
        r = c.patch(f"/api/atelier/entities/{eid}", headers=h,
                    json={"updated_at": AVANT, "address": "1 rue Quilivert", "confidence": "confirmed"})
        assert r.status_code == 200
        assert c.patch(f"/api/atelier/entities/{eid}/status", headers=h,
                       json={"validation_status": "verified"}).status_code == 200
        assert c.get("/api/analyses/familles", headers=h).status_code == 200

    def test_ne_gere_pas_les_comptes_ni_ne_publie(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("valid@exemple.fr", "validator")
        assert c.get("/api/admin/comptes", headers=h).status_code == 403
        assert c.post("/api/admin/comptes/invitations", headers=h,
                      json={"email": "x@exemple.fr"}).status_code == 403
        assert c.post("/api/admin/publication/publier", headers=h).status_code == 403


# ─── Invitations ──────────────────────────────────────────────────────────────

def _inviter(atelier, h, email="nouvelle@exemple.fr", role="validator"):
    r = atelier["client"].post("/api/admin/comptes/invitations", headers=h,
                               json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


class TestInvitation:
    def test_seul_linvite_cree_son_compte_avec_le_role_choisi(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        inv = _inviter(atelier, h, "Nouvelle@Exemple.fr", "validator")
        assert inv["invitation"]["etat"] == "en_attente"

        lu = c.post("/api/auth/invitation/lire", json={"jeton": inv["jeton"]}).json()
        assert (lu["email"], lu["role"], lu["invite_par"]) == (
            "nouvelle@exemple.fr", "validator", "admin@exemple.fr")

        r = c.post("/api/auth/invitation/accepter", json={"jeton": inv["jeton"], "motdepasse": MDP})
        assert r.status_code == 200
        nouvelle = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert c.get("/api/auth/me", headers=nouvelle).json()["role"] == "validator"
        assert c.post("/api/auth/login", json={"email": "nouvelle@exemple.fr",
                                               "password": MDP}).status_code == 200

        # le jeton n'est jamais stocké en clair
        stocke, _ = atelier["sql"]("SELECT jeton_sha256 FROM invitations")
        assert inv["jeton"] not in str(stocke)

    def test_un_lien_ne_sert_quune_fois(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        jeton = _inviter(atelier, h)["jeton"]
        assert c.post("/api/auth/invitation/accepter",
                      json={"jeton": jeton, "motdepasse": MDP}).status_code == 200
        r = c.post("/api/auth/invitation/accepter", json={"jeton": jeton, "motdepasse": MDP})
        assert r.status_code == 410

    def test_lien_inconnu_expire_annule_ou_remplace(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        assert c.post("/api/auth/invitation/lire", json={"jeton": "au-hasard"}).status_code == 404

        premier = _inviter(atelier, h, "a@exemple.fr")
        second = _inviter(atelier, h, "a@exemple.fr")          # remplace le premier
        assert c.post("/api/auth/invitation/lire", json={"jeton": premier["jeton"]}).status_code == 410
        assert c.post("/api/auth/invitation/lire", json={"jeton": second["jeton"]}).status_code == 200

        atelier["sql"]("UPDATE invitations SET expire_le=datetime('now', '-1 minute') WHERE id=?",
                       (second["invitation"]["id"],))
        r = c.post("/api/auth/invitation/accepter", json={"jeton": second["jeton"], "motdepasse": MDP})
        assert r.status_code == 410 and "expiré" in r.json()["detail"]

        troisieme = _inviter(atelier, h, "b@exemple.fr")
        assert c.delete(f"/api/admin/comptes/invitations/{troisieme['invitation']['id']}",
                        headers=h).status_code == 200
        assert c.post("/api/auth/invitation/lire", json={"jeton": troisieme["jeton"]}).status_code == 410

    def test_on_ninvite_pas_une_adresse_qui_a_deja_un_compte(self, atelier):
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        r = atelier["client"].post("/api/admin/comptes/invitations", headers=h,
                                   json={"email": "ADMIN@exemple.fr", "role": "contributor"})
        assert r.status_code == 409

    def test_mot_de_passe_trop_court_refuse_et_lien_intact(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        jeton = _inviter(atelier, h)["jeton"]
        assert c.post("/api/auth/invitation/accepter",
                      json={"jeton": jeton, "motdepasse": "court"}).status_code == 400
        assert c.post("/api/auth/invitation/lire", json={"jeton": jeton}).status_code == 200


class TestComptes:
    def test_lien_de_mot_de_passe_clot_les_anciennes_sessions(self, atelier):
        c = atelier["client"]
        h_admin, _ = atelier["compte"]("admin@exemple.fr", "admin")
        h_val, j_val = atelier["compte"]("valid@exemple.fr", "validator")
        vid = atelier["sql"]("SELECT id FROM users WHERE email='valid@exemple.fr'")[0][0]["id"]

        r = c.post(f"/api/admin/comptes/{vid}/lien-mot-de-passe", headers=h_admin)
        assert r.status_code == 201
        assert c.post("/api/auth/invitation/accepter",
                      json={"jeton": r.json()["jeton"], "motdepasse": "nouveau-mot-de-passe"}
                      ).status_code == 200

        assert c.get("/api/atelier/stats", headers=h_val).status_code == 401
        assert c.post("/api/auth/refresh",
                      json={"refresh_token": j_val["refresh_token"]}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "valid@exemple.fr",
                                               "password": "nouveau-mot-de-passe"}).status_code == 200

    def test_changer_son_mot_de_passe_garde_la_session_qui_le_change(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("valid@exemple.fr", "validator")
        r = c.post("/api/auth/mot-de-passe", headers=h,
                   json={"actuel": MDP, "nouveau": "un-autre-mot-de-passe"})
        assert r.status_code == 200
        neuf = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert c.get("/api/auth/me", headers=neuf).status_code == 200
        assert c.get("/api/auth/me", headers=h).status_code == 401

    def test_un_compte_desactive_perd_laccès_tout_de_suite(self, atelier):
        c = atelier["client"]
        h_admin, _ = atelier["compte"]("admin@exemple.fr", "admin")
        h_val, _ = atelier["compte"]("valid@exemple.fr", "validator")
        vid = atelier["sql"]("SELECT id FROM users WHERE email='valid@exemple.fr'")[0][0]["id"]
        assert c.patch(f"/api/admin/comptes/{vid}", headers=h_admin,
                       json={"actif": False}).status_code == 200
        assert c.get("/api/atelier/stats", headers=h_val).status_code == 401
        # 401 comme un mot de passe faux : un 403 disait que le mot de passe
        # était juste (audit du 15/11/2026).
        assert c.post("/api/auth/login", json={"email": "valid@exemple.fr",
                                               "password": MDP}).status_code == 401

    def test_le_dernier_admin_reste_admin(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        aid = atelier["sql"]("SELECT id FROM users")[0][0]["id"]
        assert c.patch(f"/api/admin/comptes/{aid}", headers=h,
                       json={"role": "validator"}).status_code == 409

    def test_les_invitations_dun_admin_retrograde_tombent(self, atelier):
        c = atelier["client"]
        h1, _ = atelier["compte"]("admin1@exemple.fr", "admin")
        h2, _ = atelier["compte"]("admin2@exemple.fr", "admin")
        jeton = _inviter(atelier, h2, "par-admin2@exemple.fr")["jeton"]
        a2 = atelier["sql"]("SELECT id FROM users WHERE email='admin2@exemple.fr'")[0][0]["id"]
        assert c.patch(f"/api/admin/comptes/{a2}", headers=h1,
                       json={"role": "contributor"}).status_code == 200
        assert c.post("/api/auth/invitation/lire", json={"jeton": jeton}).status_code == 410

    def test_tout_passe_au_journal(self, atelier):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        jeton = _inviter(atelier, h, "nouvelle@exemple.fr", "contributor")["jeton"]
        c.post("/api/auth/invitation/accepter", json={"jeton": jeton, "motdepasse": MDP})
        journal = c.get("/api/atelier/journal?quoi=users", headers=h).json()
        faits = [(l["par"], l["action"], l["champ"]) for l in journal["lignes"]]
        assert ("admin@exemple.fr", "invitation", "nouvelle@exemple.fr") in faits
        assert ("nouvelle@exemple.fr", "inscription", "nouvelle@exemple.fr") in faits
        assert journal["lignes"][0]["quoi_libelle"] == "compte"


# ─── Aperçu et publication ───────────────────────────────────────────────────

@pytest.fixture
def flux(atelier, tmp_path, monkeypatch):
    """Le flux de publication sur des répertoires jetables, builder et contrôle factices."""
    import json
    from scripts import publication as pub
    for nom in ("PUBLIE", "SITE", "BROUILLON", "APERCUS"):
        (tmp_path / nom).mkdir()
        monkeypatch.setattr(pub, nom, tmp_path / nom)
    monkeypatch.setattr(pub, "ETAT", tmp_path / "etat.json")
    monkeypatch.setattr(pub, "VERSIONS", tmp_path / "versions")
    monkeypatch.setattr(pub, "PUBLIEES", tmp_path / "publiees")
    monkeypatch.setattr(pub, "VERROU", tmp_path / "publication.lock")

    def build_snapshot(out):
        (out / "stats.json").write_text(json.dumps({"entities_public": 1}), encoding="utf-8")
        return {"entities_public": 1}
    monkeypatch.setattr(pub, "build_snapshot", build_snapshot)
    monkeypatch.setattr(pub, "controler", lambda cible: {
        "ok": True, "compte_erreurs": 0, "compte_avertissements": 0,
        "erreurs": [], "avertissements": [], "fichiers": 1, "rapport": "OK"})
    return pub


class TestApercuEtPublication:
    def test_la_publication_ne_souvre_pas_au_contributeur(self, atelier, flux):
        """Ni l'état, ni l'aperçu, ni la liste à relire (Julien, 07/10/2026)."""
        c = atelier["client"]
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        assert c.get("/api/admin/publication", headers=h).status_code == 403
        assert c.get("/api/admin/publication").status_code in (401, 403)
        assert c.get("/api/admin/publication/modifications", headers=h).status_code == 403
        assert c.post("/api/admin/publication/apercu", headers=h).status_code == 403
        assert c.post("/api/admin/publication/apercu/serveur", headers=h,
                      json={"action": "demarrer"}).status_code == 403
        assert not flux.APERCUS.exists() or not any(flux.APERCUS.iterdir())

    def test_le_validateur_genere_son_apercu(self, atelier, flux):
        c = atelier["client"]
        h, j = atelier["compte"]("valid@exemple.fr", "validator")
        r = c.post("/api/admin/publication/apercu", headers=h)
        assert r.status_code == 200, r.text
        etat = r.json()
        assert etat["peut_apercevoir"] and not etat["peut_agir"]
        assert etat["brouillon"]["genere_par"] == "valid@exemple.fr"
        assert etat["brouillon"]["compte"] == j["user"]["id"]
        assert etat["etape"] == "pret_a_publier"
        assert (flux.APERCUS / str(j["user"]["id"]) / "donnees" / "stats.json").is_file()
        assert not (flux.BROUILLON / "stats.json").exists()

    def test_un_compte_ne_relance_pas_son_apercu_en_boucle(self, atelier, flux):
        """Un aperçu reconstruit tout le snapshot sous le verrou de publication."""
        c = atelier["client"]
        h, _ = atelier["compte"]("valid@exemple.fr", "validator")
        assert c.post("/api/admin/publication/apercu", headers=h).status_code == 200
        r = c.post("/api/admin/publication/apercu", headers=h)
        assert r.status_code == 429 and "attendre" in r.json()["detail"]
        # Le délai est par compte : un autre n'attend pas.
        h2, _ = atelier["compte"]("valid2@exemple.fr", "validator")
        assert c.post("/api/admin/publication/apercu", headers=h2).status_code == 200

    def test_ni_contributeur_ni_validateur_ne_publient(self, atelier, flux):
        c = atelier["client"]
        for email, role in (("contrib@exemple.fr", "contributor"), ("valid@exemple.fr", "validator")):
            h, _ = atelier["compte"](email, role)
            c.post("/api/admin/publication/apercu", headers=h)
            assert c.post("/api/admin/publication/publier", headers=h).status_code == 403
            assert c.post("/api/admin/publication/mettre-en-ligne", headers=h).status_code == 403
        assert not (flux.PUBLIE / "stats.json").exists()

    def test_ladmin_publie_depuis_son_propre_apercu_seulement(self, atelier, flux):
        c = atelier["client"]
        h_val, _ = atelier["compte"]("valid@exemple.fr", "validator")
        h_admin, _ = atelier["compte"]("admin@exemple.fr", "admin")
        c.post("/api/admin/publication/apercu", headers=h_val)

        r = c.post("/api/admin/publication/publier", headers=h_admin)
        assert r.status_code == 409                       # l'aperçu d'un autre ne compte pas
        assert "aperçu" in r.json()["detail"]["message"]

        c.post("/api/admin/publication/apercu", headers=h_admin)
        r = c.post("/api/admin/publication/publier", headers=h_admin)
        assert r.status_code == 200, r.text
        assert r.json()["publie"]["apercu_genere_par"] == "admin@exemple.fr"
        assert (flux.PUBLIE / "stats.json").is_file()

    def test_la_liste_a_relire_dit_quoi_qui_quand(self, atelier, flux):
        """Ce qu'un contributeur propose et ce qu'un validateur écrit se lisent
        dans la liste, avec auteur, champs et verdict — et s'y tranchent."""
        c, eid = atelier["client"], atelier["fiche"]()
        h_c, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        h_v, _ = atelier["compte"]("valid@exemple.fr", "validator")
        r = c.patch(f"/api/atelier/entities/{eid}", headers=h_c,
                    json={"updated_at": AVANT, "address": "1 rue Quilivert"})
        assert r.status_code == 202, r.text

        vu = c.get("/api/admin/publication/modifications", headers=h_v).json()
        [prop] = vu["propositions"]
        assert (prop["nature"], prop["propose_par"], prop["charge"], prop["entity_id"]) == (
            "fiche", "contrib@exemple.fr", {"address": "1 rue Quilivert"}, eid)
        assert vu["nouvelles"] == [] and vu["marches_a_relire"] == 0

        assert c.patch(f"/api/atelier/entities/{eid}/status", headers=h_v,
                       json={"verdict": "ecarte", "note": "adresse privée"}).status_code == 200
        [ligne] = c.get("/api/admin/publication/modifications", headers=h_v).json()["contributions"]
        assert (ligne["id"], ligne["par"], ligne["verdict"], ligne["note"]) == (
            eid, "valid@exemple.fr", "ecarte", "adresse privée")

    def test_arreter_le_serveur_dapercu_est_reserve_a_ladmin(self, atelier, flux):
        h, _ = atelier["compte"]("valid@exemple.fr", "validator")
        r = atelier["client"].post("/api/admin/publication/apercu/serveur", headers=h,
                                   json={"action": "arreter"})
        assert r.status_code == 403


# ─── Brancher une IA depuis l'atelier ─────────────────────────────────────────

class TestBrancherUneIA:
    @pytest.fixture
    def ia(self, atelier, tmp_path, monkeypatch):
        import api
        # Posés par monkeypatch pour être RENDUS après l'essai : le réglage
        # écrit des variables du module.
        for nom in ("_IA_URL", "_IA_MODELE", "_IA_CLE", "_IA_PROTOCOLE",
                    "RAG_ENABLED", "_CHAT_MODEL"):
            monkeypatch.setattr(api, nom, getattr(api, nom))
        monkeypatch.setattr(api, "_IA_DU_SERVEUR", False)
        monkeypatch.setattr(api, "_RAG_DU_SERVEUR", False)
        monkeypatch.setattr(api, "REGLAGE_IA", tmp_path / "ia_locale.json")
        monkeypatch.setattr(api, "_modeles_ollama",
                            lambda: ["nomic-embed-text:latest", "qwen2.5:14b"])
        return api

    def test_reserve_a_ladmin(self, atelier, ia):
        c = atelier["client"]
        for email, role in (("contrib@exemple.fr", "contributor"), ("valid@exemple.fr", "validator")):
            h, _ = atelier["compte"](email, role)
            assert c.get("/api/admin/ia", headers=h).status_code == 403
            assert c.put("/api/admin/ia", headers=h, json={"modele": "qwen2.5:14b"}).status_code == 403
        assert not ia.REGLAGE_IA.exists()

    def test_ladmin_branche_un_modele_local_et_la_recherche(self, atelier, ia):
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        assert c.get("/api/ia/config", headers=h).json()["configuree"] is False
        r = c.put("/api/admin/ia", headers=h, json={"modele": "qwen2.5:14b", "recherche": True})
        assert r.status_code == 200, r.text
        vu = c.get("/api/ia/config", headers=h).json()
        assert (vu["configuree"], vu["modele"], vu["locale"]) == (True, "qwen2.5:14b", True)
        assert c.get("/api/rag/config", headers=h).json()["enabled"] is True
        assert atelier["sql"]("SELECT table_name, action FROM audit_log")[0] == [
            {"table_name": "reglages", "action": "ia"}]
        # Débrancher rend l'atelier à son état d'avant.
        assert c.put("/api/admin/ia", headers=h, json={}).status_code == 200
        assert c.get("/api/ia/config", headers=h).json()["configuree"] is False
        assert c.get("/api/rag/config", headers=h).json()["enabled"] is False

    def test_ni_adresse_ni_modele_libres(self, atelier, ia, monkeypatch):
        """Le nom vient de la liste d'Ollama ; aucune adresse ne se saisit."""
        c = atelier["client"]
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        r = c.put("/api/admin/ia", headers=h, json={"modele": "gpt-x", "url": "https://ailleurs.test/v1"})
        assert r.status_code == 400 and "n'est pas installé" in r.json()["detail"]
        assert ia._IA_URL == "" and not ia.REGLAGE_IA.exists()
        monkeypatch.setattr(ia, "_modeles_ollama", lambda: None)
        r = c.put("/api/admin/ia", headers=h, json={"modele": "qwen2.5:14b"})
        assert r.status_code == 409 and "ne répond pas" in r.json()["detail"]

    def test_un_modele_regle_par_le_serveur_nest_pas_defait(self, atelier, ia, monkeypatch):
        monkeypatch.setattr(ia, "_IA_DU_SERVEUR", True)
        monkeypatch.setattr(ia, "_IA_URL", "https://fournisseur.test/v1")
        monkeypatch.setattr(ia, "_IA_MODELE", "distant")
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        r = atelier["client"].put("/api/admin/ia", headers=h, json={"modele": "qwen2.5:14b"})
        assert r.status_code == 200 and r.json()["serveur"]["ia"] is True
        assert (ia._IA_URL, ia._IA_MODELE) == ("https://fournisseur.test/v1", "distant")


# ─── Minimisation : ce qui porte sur des personnes se lit par qui le tranche ──

class TestMinimisation:
    def test_les_liens_presumes_ne_se_lisent_qu_en_validateur(self, atelier):
        c = atelier["client"]
        h_c, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        h_v, _ = atelier["compte"]("valid@exemple.fr", "validator")
        assert c.get("/api/candidates", headers=h_c).status_code == 403
        assert c.get("/api/candidates", headers=h_v).status_code == 200

    def test_les_notes_sur_une_personne_sont_reservees_aux_validateurs(self, atelier):
        c = atelier["client"]
        h_c, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        h_v, _ = atelier["compte"]("valid@exemple.fr", "validator")
        _, pid = atelier["sql"]("INSERT INTO entities(type, name) VALUES('person', 'Jeanne Exemple')")
        asso = atelier["fiche"]()

        r = c.post(f"/api/atelier/entities/{pid}/notes", headers=h_v, json={"note": "à recouper"})
        assert r.status_code == 200, r.text
        nid = r.json()["id"]
        vu = c.get(f"/api/atelier/entities/{pid}", headers=h_v).json()
        assert [n["note"] for n in vu["notes"]] == ["à recouper"] and not vu["notes_reservees"]

        vu = c.get(f"/api/atelier/entities/{pid}", headers=h_c).json()
        assert vu["notes"] == [] and vu["notes_reservees"] is True
        assert c.post(f"/api/atelier/entities/{pid}/notes", headers=h_c,
                      json={"note": "x"}).status_code == 403
        assert c.put(f"/api/atelier/notes/{nid}", headers=h_c, json={"note": "x"}).status_code == 403
        # Sur un organisme, rien ne change : le contributeur note.
        assert c.post(f"/api/atelier/entities/{asso}/notes", headers=h_c,
                      json={"note": "siège déplacé"}).status_code == 200
        assert c.get(f"/api/atelier/entities/{asso}", headers=h_c).json()["notes_reservees"] is False


# ─── Garde de route : publication et décision (14/11/2026) ────────────────────
# Ces routes tenaient leur rôle d'un contrôle dans leur corps ; la garde est
# remontée dans leur déclaration (`require_compte_ou_cle`). Le rôle du dessous
# est refusé en le disant, le rôle requis passe, et la clé admin locale passe
# encore ici — et seulement ici : les lectures lui sont fermées.

#: (méthode, chemin, corps, rôle minimal). Pour `apercu/serveur`, le corps
#: choisit le rôle : démarrer, validateur ; arrêter (celui de tout le monde), admin.
GARDEES = [
    ("GET", "/api/admin/publication", None, "validator"),
    ("POST", "/api/admin/publication/apercu", None, "validator"),
    ("POST", "/api/admin/publication/publier", None, "admin"),
    ("POST", "/api/admin/publication/verifier-en-ligne", None, "validator"),
    ("POST", "/api/admin/publication/mettre-en-ligne", None, "admin"),
    ("POST", "/api/admin/publication/revenir", None, "admin"),
    ("GET", "/api/admin/publication/modifications", None, "validator"),
    ("POST", "/api/admin/publication/apercu/serveur", {"action": "demarrer"}, "validator"),
    ("POST", "/api/admin/publication/apercu/serveur", {"action": "arreter"}, "admin"),
    ("POST", "/api/candidates/{cid}/review", {"action": "accept"}, "validator"),
]
DESSOUS = {"validator": "contributor", "admin": "validator"}


@pytest.fixture
def sans_effet(atelier, tmp_path, monkeypatch):
    """Les emplacements de publication sous `tmp_path`, et les gestes qui
    construisent, téléversent, interrogent le site ou lancent un serveur
    remplacés par un témoin : seule la garde est éprouvée ici, le flux a ses
    propres essais (`test_publication_flux`, `test_apercus_par_compte`)."""
    import api
    from montages import reloger
    racine = tmp_path / "opt"
    reloger(api.pub, racine, monkeypatch)
    monkeypatch.setattr(api.pub, "APERCUS", racine / "audits" / "apercus")
    gestes = []
    for nom in ("generer_apercu_du_compte", "generer_apercu", "publier", "verifier_en_ligne",
                "mettre_en_ligne", "revenir_a_la_version_precedente",
                "demarrer_serveur_apercu_du_compte", "demarrer_serveur_apercu",
                "arreter_serveur_apercu"):
        monkeypatch.setattr(api.pub, nom, lambda *a, _nom=nom, **k: gestes.append(_nom) or {"dest": "témoin"})
    _, a = atelier["sql"]("INSERT INTO entities(type, name) VALUES('person', 'A')")
    _, b = atelier["sql"]("INSERT INTO entities(type, name) VALUES('person', 'B')")
    _, cid = atelier["sql"](
        "INSERT INTO relation_candidates(from_id, to_id, relation_type, confidence, signal) "
        "VALUES(?, ?, 'famille_présumé', 'probable', 'same_surname')", (a, b))
    return {"gestes": gestes, "cid": cid}


def _appel(atelier, sans_effet, methode, chemin, corps, **kw):
    return atelier["client"].request(methode, chemin.format(cid=sans_effet["cid"]),
                                     json=corps, **kw)


class TestGardeDeRoute:
    @pytest.mark.parametrize("methode, chemin, corps, role", GARDEES)
    def test_le_role_du_dessous_est_refuse_en_le_disant(self, atelier, sans_effet,
                                                         methode, chemin, corps, role):
        h, _ = atelier["compte"]("dessous@exemple.fr", DESSOUS[role])
        r = _appel(atelier, sans_effet, methode, chemin, corps, headers=h)
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["role_requis"] == role
        assert "vous êtes" in detail["message"]
        assert "ADMIN_KEY" not in r.text
        assert sans_effet["gestes"] == []

    @pytest.mark.parametrize("methode, chemin, corps, role", GARDEES)
    def test_le_role_requis_passe(self, atelier, sans_effet, methode, chemin, corps, role):
        h, _ = atelier["compte"]("requis@exemple.fr", role)
        r = _appel(atelier, sans_effet, methode, chemin, corps, headers=h)
        assert r.status_code == 200, r.text

    def test_le_contributeur_porteur_de_la_cle_ne_tranche_toujours_pas(
            self, atelier, sans_effet, monkeypatch):
        """Avant la garde, `exiger` passait avant la clé dans le corps de
        `review` : un compte connu trop faible est refusé, clé ou non."""
        import api
        monkeypatch.setattr(api, "ADMIN_KEY", "cle-de-test")
        h, _ = atelier["compte"]("contrib@exemple.fr", "contributor")
        local = TestClient(api.app, client=("127.0.0.1", 50000))
        r = local.post(f"/api/candidates/{sans_effet['cid']}/review",
                       headers={**h, "x-admin-key": "cle-de-test"}, json={"action": "accept"})
        assert r.status_code == 403


class TestAnnexesDuTri:
    """Les quatre annexes du tri des gardes (Julien, 14/11/2026)."""

    def test_l_etat_du_snapshot_public_est_retire(self, atelier):
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        assert atelier["client"].get("/api/admin/public-snapshot", headers=h).status_code == 404

    @pytest.mark.parametrize("action", ["", "Arreter", "redemarrer", "stop"])
    def test_le_serveur_d_apercu_n_accepte_que_deux_actions(self, atelier, sans_effet, action):
        """Toute valeur autre que `arreter` valait démarrage."""
        h, _ = atelier["compte"]("admin@exemple.fr", "admin")
        r = atelier["client"].post("/api/admin/publication/apercu/serveur", headers=h,
                                   json={"action": action})
        assert r.status_code == 422, r.text
        assert sans_effet["gestes"] == []

    def test_marches_borne_limit(self, atelier):
        h, _ = atelier["compte"]("lit@exemple.fr", "contributor")
        c = atelier["client"]
        assert c.get("/api/marches", headers=h, params={"limit": 200}).status_code == 200
        assert c.get("/api/marches", headers=h, params={"limit": 500}).status_code == 200
        for trop in (501, 10**9, 0, -1):
            assert c.get("/api/marches", headers=h, params={"limit": trop}).status_code == 422, trop

    def test_pivots_lit_sans_creer(self, atelier):
        """La route créait l'entité manquante (`upsert_entity`) : une lecture
        écrivait en base."""
        h, _ = atelier["compte"]("lit@exemple.fr", "contributor")
        avant = atelier["sql"]("SELECT count(*) AS n FROM entities")[0][0]["n"]
        r = atelier["client"].get("/api/pivots", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == {"commune": None, "epci": None, "etat": None, "prefecture": None}
        assert atelier["sql"]("SELECT count(*) AS n FROM entities")[0][0]["n"] == avant

    def test_pivots_rend_ce_que_les_collecteurs_ont_cree(self, atelier):
        from collectors.db import get_conn, pivot_ids
        conn = get_conn()
        try:
            crees = pivot_ids(conn)
            conn.commit()
        finally:
            conn.close()
        h, _ = atelier["compte"]("lit@exemple.fr", "contributor")
        assert atelier["client"].get("/api/pivots", headers=h).json() == crees


#: Les 17 lectures de l'atelier, que la clé ouvrait sans identité ni journal.
#: Fermées à la clé le 14/11/2026 : `require_auth`, jeton de tout compte.
LECTURES = [
    "/api/stats",
    "/api/pivots",
    "/api/entities",
    "/api/entities/{entity_id}",
    "/api/search",
    "/api/layers/{layer_type}",
    "/api/graph",
    "/api/events",
    "/api/events/search",
    "/api/dvf",
    "/api/flows",
    "/api/marches",
    "/api/budget",
    "/api/ofgl",
    "/api/syntheses",
    "/api/syntheses/{entity_id}",
    "/api/budget-annexe",
]


def _chemin(chemin, cid=1, eid=1):
    return (chemin.replace("{candidate_id}", str(cid)).replace("{cid}", str(cid))
            .replace("{layer_type}", "associations").replace("{entity_id}", str(eid)))


class TestCleAdminLocale:
    """La clé admin, admise depuis la machine elle-même, ne passe plus que sur
    les routes de publication et de décision, où elle vaut admin."""

    @staticmethod
    def _routes_a_cle():
        import api
        from fastapi.routing import APIRoute

        def porte(dependant):
            return any(getattr(d.call, "__qualname__", "") == "require_compte_ou_cle.<locals>._dep"
                       or porte(d) for d in dependant.dependencies)
        return [(m, r.path) for r in api.app.routes if isinstance(r, APIRoute)
                and porte(r.dependant) for m in sorted(r.methods - {"HEAD", "OPTIONS"})]

    #: Les 9 routes de publication et de décision. Une liste, pas un compte : un
    #: échange (une route perd la garde, une autre la gagne) ne passe pas.
    D_HIER = sorted({(m, c.replace("{cid}", "{candidate_id}")) for m, c, _, _ in GARDEES})

    def test_seules_la_publication_et_la_decision_admettent_la_cle(self):
        assert sorted(self._routes_a_cle()) == self.D_HIER
        assert len(self.D_HIER) == 9

    def test_la_cle_passe_depuis_la_machine(self, atelier, sans_effet, monkeypatch):
        import api
        monkeypatch.setattr(api, "ADMIN_KEY", "cle-de-test")
        local = TestClient(api.app, client=("127.0.0.1", 50000))
        refusees = []
        for methode, chemin in self.D_HIER:
            chemin = _chemin(chemin, sans_effet["cid"])
            corps = {"action": "accept"} if "review" in chemin else None
            r = local.request(methode, chemin, headers={"x-admin-key": "cle-de-test"}, json=corps)
            if r.status_code != 200:
                refusees.append(f"{methode} {chemin} → {r.status_code}")
        assert refusees == []

    @pytest.mark.parametrize("chemin", LECTURES)
    def test_la_cle_seule_est_refusee_sur_les_lectures(self, atelier, monkeypatch, chemin):
        """Depuis la machine, avec la bonne clé : 401. Le verrou global la laisse
        passer ; c'est la garde de route qui refuse."""
        import api
        monkeypatch.setattr(api, "ADMIN_KEY", "cle-de-test")
        local = TestClient(api.app, client=("127.0.0.1", 50000))
        r = local.get(_chemin(chemin), headers={"x-admin-key": "cle-de-test"},
                      params={"q": "four"} if "search" in chemin else None)
        assert r.status_code == 401, r.text

    @pytest.mark.parametrize("chemin", LECTURES)
    def test_un_jeton_de_contributeur_lit(self, atelier, chemin):
        """La contre-épreuve : le 401 vient de la clé, pas d'une route cassée."""
        h, _ = atelier["compte"]("lit@exemple.fr", "contributor")
        eid = atelier["fiche"]()
        r = atelier["client"].get(_chemin(chemin, eid=eid), headers=h,
                                  params={"q": "four"} if "search" in chemin else None)
        assert r.status_code not in (401, 403) and r.status_code < 500, r.text

    def test_les_lectures_sont_sous_require_auth(self):
        import api
        from api_auth import require_auth
        from fastapi.routing import APIRoute
        par_chemin = {r.path: r for r in api.app.routes
                      if isinstance(r, APIRoute) and "GET" in r.methods}
        sans = [c for c in LECTURES
                if not any(d.call is require_auth for d in par_chemin[c].dependant.dependencies)]
        assert sans == []

    def test_la_cle_venue_d_ailleurs_est_refusee(self, atelier, monkeypatch):
        import api
        monkeypatch.setattr(api, "ADMIN_KEY", "cle-de-test")
        dehors = TestClient(api.app, client=("203.0.113.7", 50000))
        r = dehors.post("/api/admin/publication/publier", headers={"x-admin-key": "cle-de-test"})
        assert r.status_code == 403
