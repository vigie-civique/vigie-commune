"""Les subventions lues dans les actes entrent par l'atelier, jamais d'elles-mêmes.

Le chemin des marchés lus (`test_marches_extraits.py`), pour les flux : une ligne
n'est proposée que si sa citation se lit dans son acte ; rien n'atteint
`financial_flows` avant qu'un validateur l'accepte ; l'assemblée qui a voté se
lit dans l'acte, jamais dans le rapport ; le modèle suit la ligne jusqu'à la
saisie (charte IA, engagement 4).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from collectors import flux_extraits as fx
from collectors.config import EPCI_NOM
from tests.test_marches_extraits import MDP

TEXTE = ("Le conseil communautaire, après en avoir délibéré, décide d'attribuer à "
         "l'association Les Amis du Moulin une subvention de 2 500 € pour "
         "l'organisation de la fête du moulin.")
CITATION = ("décide d'attribuer à l'association Les Amis du Moulin une subvention "
            "de 2 500 €")
IA = {"modele": "qwen2.5:14b", "empreinte": "7cdf5a0187d5", "locale": True}


def _acte(conn, type_="deliberation_cc", date="2025-04-09"):
    eid = conn.execute(
        "INSERT INTO events(type, date, title, content, source, source_url, cle_acte) "
        "VALUES(?,?,?,?,?,?,?)",
        (type_, date, "Subvention aux Amis du Moulin", TEXTE, "cc.exemple.invalid",
         f"https://cc.exemple.invalid/{date}.pdf", f"cc-{date[:4]}-12")).lastrowid
    conn.commit()
    return eid


def _rapport(eid, **autres):
    ligne = {"event_id": eid, "date": "2025-04-09", "citation": CITATION, "sens": "verse",
             "tiers": "Les Amis du Moulin", "montant": 2500, "nature": "attribution",
             "titre_acte": "Subvention aux Amis du Moulin", "confiance": "haute"}
    ligne.update(autres)
    return {"format": fx.FORMAT, "modele": IA, "lignes": [ligne]}


@pytest.fixture
def depot(base):
    base.execute("INSERT INTO users(email, password_hash, role) "
                 "VALUES('valid@exemple.fr', 'x', 'validator')")
    auteur = {"id": base.execute("SELECT id FROM users").fetchone()[0],
              "email": "valid@exemple.fr"}

    def deposer(rapport):
        bilan = fx.deposer(base, rapport, auteur, "rapport-essai")
        base.commit()
        return bilan
    return deposer


class TestDepot:
    def test_une_ligne_attestee_devient_une_proposition_et_rien_d_autre(self, base, depot):
        eid = _acte(base)
        bilan = depot(_rapport(eid))
        assert bilan["proposees"] == 1 and bilan["refusees"] == []
        assert bilan["par_assemblee"] == {"epci": 1}
        assert bilan["cles_ignorees"] == ["confiance"]
        [p] = base.execute("SELECT nature, object_id, charge FROM propositions")
        charge = json.loads(p[2])
        assert (p[0], p[1]) == ("flux", eid)
        assert charge["assemblee"] == "epci" and charge["montant_dans_citation"] is True
        assert charge["ia"] == IA and charge["sens"] == "verse"
        assert base.execute("SELECT COUNT(*) FROM financial_flows").fetchone()[0] == 0

    def test_l_assemblee_se_lit_dans_l_acte(self, base, depot):
        eid = _acte(base, type_="deliberation")
        depot(_rapport(eid))
        [c] = base.execute("SELECT charge FROM propositions")
        assert json.loads(c[0])["assemblee"] == "commune"

    def test_rejouer_le_meme_rapport_ne_propose_rien_de_plus(self, base, depot):
        eid = _acte(base)
        depot(_rapport(eid))
        bilan = depot(_rapport(eid, tiers="LES AMIS  DU MOULIN"))
        assert (bilan["proposees"], bilan["deja_proposees"]) == (0, 1)

    @pytest.mark.parametrize("modif, motif", [
        ({"citation": "le conseil décide d'acheter une pelleteuse neuve pour la voirie"},
         "citation introuvable"),
        ({"date": "2024-01-01"}, "autre base"),
        ({"sens": "donne"}, "sens"),
        ({"montant": None}, "montant : champ obligatoire"),
        ({"event_id": 99999}, "absent de cette base"),
        ({"annee": 20252025202520252025}, "annee : hors"),
        ({"montant": 5e12}, "invraisemblable"),
        ({"type_tiers": "personne"}, "type_tiers"),
    ])
    def test_ce_qui_ne_s_atteste_pas_est_refuse_avec_sa_raison(self, base, depot, modif, motif):
        eid = _acte(base)
        [r] = depot(_rapport(eid, **modif))["refusees"]
        assert motif in r["motif"]

    def test_un_autre_format_est_refuse_entier(self, base, depot):
        with pytest.raises(fx.RapportRefuse):
            depot({"format": "vigie-marches-extraits/1", "lignes": []})
        with pytest.raises(fx.RapportRefuse):
            depot([])


@pytest.fixture
def atelier(tmp_path, monkeypatch, schema_sql):
    pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")
    from fastapi.testclient import TestClient

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
    monkeypatch.setattr(api, "RAPPORTS_FLUX_EXTRAITS", tmp_path / "extraits")
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.setattr(api, "ADMIN_KEY", "")
    client = TestClient(api.app)

    def sql(requete, params=()):
        c = sqlite3.connect(chemin_db)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(requete, params)
            c.commit()
            return [dict(r) for r in cur.fetchall()]
        finally:
            c.close()

    def compte(email, role):
        sql("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
            (email, api_auth._hash_pw(MDP), role))
        j = client.post("/api/auth/login", json={"email": email, "password": MDP}).json()
        return {"Authorization": f"Bearer {j['access_token']}"}

    c = sqlite3.connect(chemin_db)
    eid = _acte(c)
    c.close()
    return {"client": client, "sql": sql, "eid": eid, "tmp": tmp_path,
            "contrib": compte("contrib@exemple.fr", "contributor"),
            "valid": compte("valid@exemple.fr", "validator")}


def _attente(a):
    r = a["client"].get("/api/atelier/propositions?nature=flux", headers=a["valid"])
    assert r.status_code == 200, r.text
    return r.json()["propositions"]


class TestAtelier:
    def test_un_contributeur_ne_depose_pas_un_rapport(self, atelier):
        r = atelier["client"].post("/api/atelier/flux-extraits", headers=atelier["contrib"],
                                   json=_rapport(atelier["eid"]))
        assert r.status_code == 403

    def test_accepter_ecrit_le_flux_de_l_intercommunalite_avec_sa_trace(self, atelier):
        c = atelier["client"]
        r = c.post("/api/atelier/flux-extraits", headers=atelier["valid"],
                   json=_rapport(atelier["eid"]))
        assert r.status_code == 201, r.text
        assert list((atelier["tmp"] / "extraits").iterdir())
        [p] = _attente(atelier)
        assert p["acte"]["extrait"]["cite"] and p["doublons"] == []
        r = c.post(f"/api/atelier/propositions/{p['id']}/decision", headers=atelier["valid"],
                   json={"accepter": True, "corrections": {"montant": "2 600",
                                                           "type_tiers": "association"}})
        assert r.status_code == 200, r.text
        assert r.json()["motif"] == "corrigée : montant, type_tiers"

        [f] = atelier["sql"](
            "SELECT f.*, e.name AS de FROM financial_flows f JOIN entities e ON e.id=f.from_id")
        assert (f["amount"], f["year"], f["statut"], f["confidence"]) == (2600, 2025, "vote",
                                                                          "confirmed")
        assert f["de"] == EPCI_NOM
        from collectors import saisies
        [s] = saisies.charger()["saisies"]
        assert s["objet"] == "flux" and s["source"]["ia"] == IA
        assert s["source"]["acte_cle"] == "cc-2025-12"

        # Rejouer : ni le rapport, ni les saisies ne doublent rien.
        r = c.post("/api/atelier/flux-extraits", headers=atelier["valid"],
                   json=_rapport(atelier["eid"]))
        assert r.json()["deja_proposees"] == 1
        assert saisies.import_saisies()["ecrites"] == 0

    def test_la_citation_ne_se_corrige_pas(self, atelier):
        c = atelier["client"]
        c.post("/api/atelier/flux-extraits", headers=atelier["valid"], json=_rapport(atelier["eid"]))
        [p] = _attente(atelier)
        r = c.post(f"/api/atelier/propositions/{p['id']}/decision", headers=atelier["valid"],
                   json={"accepter": True, "corrections": {"citation": "autre chose"}})
        assert r.status_code == 400
        assert atelier["sql"]("SELECT id FROM financial_flows") == []

    def test_sans_fiche_rapprochee_le_type_du_tiers_est_exige(self, atelier):
        c = atelier["client"]
        c.post("/api/atelier/flux-extraits", headers=atelier["valid"], json=_rapport(atelier["eid"]))
        [p] = _attente(atelier)
        assert "tiers_id" not in p["charge"]
        r = c.post(f"/api/atelier/propositions/{p['id']}/decision", headers=atelier["valid"],
                   json={"accepter": True})
        assert r.status_code == 400 and "type du tiers" in r.json()["detail"]
        assert atelier["sql"]("SELECT id FROM financial_flows") == []

    def test_la_fiche_montree_est_celle_retenue_et_doit_encore_exister(self, atelier):
        c = atelier["client"]
        atelier["sql"]("INSERT INTO entities(type, name, name_norm, confidence) "
                       "VALUES('association', 'Les Amis du Moulin', 'les amis du moulin', 'verified')")
        [fiche] = atelier["sql"]("SELECT id FROM entities WHERE name='Les Amis du Moulin'")
        c.post("/api/atelier/flux-extraits", headers=atelier["valid"], json=_rapport(atelier["eid"]))
        [p] = _attente(atelier)
        assert p["charge"]["tiers_id"] == fiche["id"]
        atelier["sql"]("DELETE FROM entities WHERE id=?", (fiche["id"],))
        r = c.post(f"/api/atelier/propositions/{p['id']}/decision", headers=atelier["valid"],
                   json={"accepter": True})
        assert r.status_code == 400 and "n'existe plus" in r.json()["detail"]
