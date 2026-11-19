"""Origine des fiches, liens et journal au millième — audit de l'atelier du 15/11/2026.

Un essai par constat : la garde d'origine d'une fiche institutionnelle, les
liens absurdes (soi-même, doublon par la source, dates, bouts inexistants,
source réécrite, suppression sans trace), l'encadré de conflit qui disait
« — » parce que le journal était à la seconde. La note d'un contributeur née
`probable` est déjà couverte par `test_atelier_droits.py`. Vrais jetons, vraie
base SQLite, schéma réel.
"""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

from collectors.origine import ATELIER, INSTITUTIONNEL, VERBATIM  # noqa: E402

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

    def fiche(confidence="verified", nom="Les Amis du Four", origine=INSTITUTIONNEL,
              type_="association"):
        _, eid = sql("INSERT INTO entities(type, name, confidence, origine, updated_at) "
                     "VALUES(?, ?, ?, ?, ?)", (type_, nom, confidence, origine, AVANT))
        return eid

    def personne(origine=INSTITUTIONNEL, confidence="probable", nom="Aldéric TESTAL"):
        eid = fiche(confidence, nom, origine, "person")
        sql("INSERT INTO persons(entity_id, firstname, lastname, birth_year) "
            "VALUES(?, 'Aldéric', 'TESTAL', 1970)", (eid,))
        return eid

    return {"client": client, "sql": sql, "fiche": fiche, "personne": personne,
            "chemin_db": chemin_db,
            "contrib": compte("contrib@exemple.fr", "contributor"),
            "valid": compte("valid@exemple.fr", "validator"),
            "admin": compte("admin@exemple.fr", "admin")}


def _ligne(atelier, eid):
    return atelier["sql"]("SELECT * FROM entities WHERE id=?", (eid,))[0][0]


def _journal(atelier, eid, **filtres):
    cond = " AND ".join(f"{k}=?" for k in filtres)
    return atelier["sql"](
        f"SELECT * FROM audit_log WHERE entity_id=?{' AND ' + cond if cond else ''} ORDER BY id",
        (eid, *filtres.values()))[0]


def _patch(atelier, eid, headers, **corps):
    return atelier["client"].patch(f"/api/atelier/entities/{eid}", headers=headers,
                                   json={"updated_at": _ligne(atelier, eid)["updated_at"],
                                         **corps})


# ─── Constat 2 : la fiche institutionnelle ne se renomme pas sans trace ───────

class TestOrigineDeLaFiche:
    def test_le_validateur_renomme_avec_un_motif_et_le_motif_est_journalise(self, atelier):
        eid = atelier["fiche"]()
        r = _patch(atelier, eid, atelier["valid"], name="Amis du Four de Valmorin",
                   motif="Le RNA a corrigé la dénomination (JO du 12/11)")
        assert r.status_code == 200, r.text
        assert _ligne(atelier, eid)["name"] == "Amis du Four de Valmorin"
        [m] = _journal(atelier, eid, action="motif")
        assert (m["field"], m["new_value"]) == ("name", "Le RNA a corrigé la dénomination (JO du 12/11)")
        [n] = _journal(atelier, eid, action="update", field="name")
        assert (n["old_value"], n["new_value"]) == ("Les Amis du Four", "Amis du Four de Valmorin")

    @pytest.mark.parametrize("motif", [None, "", "   "])
    def test_sans_motif_le_validateur_est_refuse_et_rien_n_est_ecrit(self, atelier, motif):
        eid = atelier["fiche"]()
        r = _patch(atelier, eid, atelier["valid"], name="Autre nom", motif=motif)
        assert r.status_code == 400 and "motif" in r.json()["detail"]
        assert _ligne(atelier, eid)["name"] == "Les Amis du Four"
        assert _journal(atelier, eid) == []

    @pytest.mark.parametrize("confiance", ["verified", "probable"])
    def test_le_contributeur_recoit_un_403_qui_dit_le_role(self, atelier, confiance):
        """Sur un objet publié il proposerait d'ordinaire (202) ; ici le nom ne
        passe ni en écriture directe ni en proposition."""
        eid = atelier["fiche"](confidence=confiance)
        r = _patch(atelier, eid, atelier["contrib"], name="Autre nom", motif="parce que")
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["role_requis"] == "validator"
        assert "validateur" in r.json()["detail"]["message"]
        assert _ligne(atelier, eid)["name"] == "Les Amis du Four"
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM propositions")[0][0]["n"] == 0

    @pytest.mark.parametrize("champ, valeur, fabrique", [
        ("lastname", "AUTRENOM", "personne"), ("firstname", "Autre", "personne"),
        ("birth_month", 7, "personne"), ("rna_id", "W999999999", "fiche")])
    def test_prenom_nom_mois_et_rna_sont_gardes_aussi(self, atelier, champ, valeur, fabrique):
        """Avis de vigie-securite : `_CHAMPS_FICHE` laisse modifier ces champs."""
        eid = atelier[fabrique]()
        assert _patch(atelier, eid, atelier["contrib"], **{champ: valeur}).status_code == 403
        r = _patch(atelier, eid, atelier["valid"], **{champ: valeur})
        assert r.status_code == 400 and "motif" in r.json()["detail"]
        r = _patch(atelier, eid, atelier["valid"], **{champ: valeur, "motif": "pièce lue"})
        assert r.status_code == 200, r.text
        [m] = _journal(atelier, eid, action="motif")
        assert m["field"] == champ

    def test_le_motif_est_borne(self, atelier):
        eid = atelier["fiche"](origine=None)
        r = _patch(atelier, eid, atelier["valid"], name="Autre", motif="x" * 1001)
        assert r.status_code == 422

    def test_l_annee_de_naissance_est_gardee_comme_le_nom(self, atelier):
        eid = atelier["personne"]()
        assert _patch(atelier, eid, atelier["contrib"], birth_year=1980).status_code == 403
        assert _patch(atelier, eid, atelier["valid"], birth_year=1980).status_code == 400
        r = _patch(atelier, eid, atelier["valid"], birth_year=1980, motif="acte de naissance lu")
        assert r.status_code == 200, r.text
        assert atelier["sql"]("SELECT birth_year FROM persons WHERE entity_id=?",
                              (eid,))[0][0]["birth_year"] == 1980

    def test_le_reste_de_la_fiche_reste_editable_sans_motif(self, atelier):
        eid = atelier["fiche"](confidence="probable")
        r = _patch(atelier, eid, atelier["contrib"], address="1 place de la Mairie")
        assert r.status_code == 200, r.text
        assert _ligne(atelier, eid)["address"] == "1 place de la Mairie"
        # Un champ identitaire renvoyé inchangé (l'écran renvoie tout) n'est pas un changement.
        r = _patch(atelier, eid, atelier["contrib"], name="Les Amis du Four",
                   address="2 place de la Mairie")
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("origine", [None, VERBATIM, ATELIER])
    def test_hors_institutionnel_la_garde_ne_s_applique_pas(self, atelier, origine):
        """Une origine NULL n'est pas concernée : 3 025 fiches non classées à
        Lasalle, une garde pleine en bloquerait la moitié."""
        eid = atelier["fiche"](confidence="probable", origine=origine)
        assert _patch(atelier, eid, atelier["contrib"], name="Nom un").status_code == 200
        assert _patch(atelier, eid, atelier["valid"], name="Nom deux").status_code == 200
        assert _journal(atelier, eid, action="motif") == []

    def test_accepter_une_proposition_sur_une_fiche_devenue_institutionnelle_demande_un_motif(
            self, atelier):
        c, eid = atelier["client"], atelier["fiche"](origine=None)
        r = _patch(atelier, eid, atelier["contrib"], name="Amis du Four")
        assert r.status_code == 202, r.text
        pid = r.json()["proposition"]
        atelier["sql"]("UPDATE entities SET origine=? WHERE id=?", (INSTITUTIONNEL, eid))
        url = f"/api/atelier/propositions/{pid}/decision"
        r = c.post(url, headers=atelier["valid"], json={"accepter": True})
        assert r.status_code == 400 and "motif" in r.json()["detail"]
        assert _ligne(atelier, eid)["name"] == "Les Amis du Four"
        r = c.post(url, headers=atelier["valid"],
                   json={"accepter": True, "motif": "dénomination du RNA"})
        assert r.status_code == 200, r.text
        assert _ligne(atelier, eid)["name"] == "Amis du Four"
        assert [m["new_value"] for m in _journal(atelier, eid, action="motif")] == \
            ["dénomination du RNA"]


# ─── Constat 9 : les liens ────────────────────────────────────────────────────

def _lier(atelier, eid, autre, headers=None, **corps):
    return atelier["client"].post(
        f"/api/atelier/entities/{eid}/relations", headers=headers or atelier["valid"],
        json={"direction": "from", "other_entity_id": autre, "relation_type": "membre",
              "confidence": "verified", **corps})


class TestLiens:
    def test_un_lien_d_une_fiche_vers_elle_meme_est_refuse(self, atelier):
        eid = atelier["fiche"]()
        r = _lier(atelier, eid, eid)
        assert r.status_code == 400 and "elle-même" in r.json()["detail"]
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM relations")[0][0]["n"] == 0

    def test_le_doublon_qui_ne_differe_que_par_la_source_est_refuse(self, atelier):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        assert _lier(atelier, a, b, source="manual").status_code == 200
        r = _lier(atelier, a, b, source="presse du 12/11")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["relation_id"]
        # Dates différentes : un autre mandat, pas un doublon. Types différents, idem.
        assert _lier(atelier, a, b, source="x", since="2020-01-01").status_code == 200
        assert _lier(atelier, a, b, source="y", relation_type="dirigeant").status_code == 200
        # NULL et "" se valent : l'écran envoie "".
        assert _lier(atelier, a, b, source="z", since="", until="").status_code == 409
        # Même couple, même type, même source, dates différentes : l'unicité de la base.
        r = _lier(atelier, a, b, source="manual", since="1999-01-01")
        assert r.status_code == 409 and "même type + source" in r.json()["detail"]

    @pytest.mark.parametrize("since, until", [
        ("n'importe quoi", None), ("2020-13-01", None), ("2020-02-30", None),
        ("20", None), ("1066", None), ("2020", "n/a"), ("2021-06-01", "2020-01-01"),
        ("2021", "2020-12"),
    ])
    def test_une_date_absurde_est_refusee(self, atelier, since, until):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        r = _lier(atelier, a, b, since=since, until=until)
        assert r.status_code == 400, r.text
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM relations")[0][0]["n"] == 0

    @pytest.mark.parametrize("since, until", [
        ("2020", "2020-06"), ("2020-06", "2020"), ("2020-06-01", "2020-06-01"),
        ("2014-03-29", None), (None, "2026-05-31"), ("", ""),
    ])
    def test_une_date_valide_est_gardee_telle_quelle(self, atelier, since, until):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        r = _lier(atelier, a, b, since=since, until=until)
        assert r.status_code == 200, r.text
        assert (r.json()["since"], r.json()["until"]) == (since or None, until or None)

    def test_une_modification_aussi_valide_ses_dates(self, atelier):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        rid = _lier(atelier, a, b, since="2020-01-01").json()["id"]
        put = lambda **corps: atelier["client"].put(
            f"/api/atelier/relations/{rid}?motif=Motif d essai", headers=atelier["valid"], json=corps)
        assert put(until="demain").status_code == 400
        assert put(until="2019-01-01").status_code == 400   # avant `since`, qui est en base
        assert put(until="2022-01-01").status_code == 200

    def test_une_fiche_d_origine_inexistante_est_un_404_pas_un_existe_deja(self, atelier):
        b = atelier["fiche"](nom="B")
        r = _lier(atelier, 99999, b)
        assert r.status_code == 404, r.text
        r = _lier(atelier, b, 99999)
        assert r.status_code == 404, r.text

    def test_le_contributeur_ne_reecrit_pas_la_source(self, atelier):
        a, b = atelier["fiche"](nom="A", confidence="probable"), atelier["fiche"](nom="B")
        rid = _lier(atelier, a, b, headers=atelier["contrib"], confidence="probable",
                    source="manual").json()["id"]
        url = f"/api/atelier/relations/{rid}?motif=Motif d essai"
        r = atelier["client"].put(url, headers=atelier["contrib"], json={"source": "sirene"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["role_requis"] == "validator"
        # Renvoyer la source telle quelle (l'écran la renvoie) ne change rien : permis.
        r = atelier["client"].put(url, headers=atelier["contrib"],
                                  json={"source": "manual", "until": "2024-01-01"})
        assert r.status_code == 200, r.text
        r = atelier["client"].put(url, headers=atelier["valid"], json={"source": "presse"})
        assert r.status_code == 200 and r.json()["source"] == "presse"

    @pytest.mark.parametrize("source", ["sirene", "cm", "rna", "Dgfip 2024"])
    def test_le_contributeur_ne_cite_pas_un_registre_en_creant_un_lien(self, atelier, source):
        a, b = atelier["fiche"](nom="A", confidence="probable"), atelier["fiche"](nom="B")
        r = _lier(atelier, a, b, headers=atelier["contrib"], confidence="probable", source=source)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["role_requis"] == "validator"
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM relations")[0][0]["n"] == 0
        # Le défaut « manual » et une source libre passent ; le validateur cite ce qu'il veut.
        for libre in ("manual", "presse du 12/11"):
            assert _lier(atelier, a, b, headers=atelier["contrib"], confidence="probable",
                         source=libre, relation_type="dirigeant" if libre == "manual"
                         else "président").status_code == 200
        assert _lier(atelier, a, b, source=source, relation_type="membre").status_code == 200

    def test_une_date_refusee_ne_renvoie_pas_tout_ce_qui_a_ete_envoye(self, atelier):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        r = _lier(atelier, a, b, since="z" * 5000)
        assert r.status_code == 400 and len(r.json()["detail"]) < 200

    def test_la_suppression_journalise_le_lien_entier(self, atelier):
        a, b = atelier["fiche"](nom="A"), atelier["fiche"](nom="B")
        rid = _lier(atelier, a, b, source="presse du 12/11", since="2020-01-01",
                    until="2024-06-30", relation_type="président").json()["id"]
        r = atelier["client"].delete(f"/api/atelier/relations/{rid}?motif=Motif d essai", headers=atelier["valid"])
        assert r.status_code == 200, r.text
        assert atelier["sql"]("SELECT COUNT(*) AS n FROM relations")[0][0]["n"] == 0
        [suppr] = _journal(atelier, a, action="delete")
        assert json.loads(suppr["old_value"]) == {
            "id": rid, "from_id": a, "to_id": b, "relation_type": "président",
            "since": "2020-01-01", "until": "2024-06-30", "source": "presse du 12/11",
            "confidence": "verified", "metadata": None}
        # De quoi la rétablir à la main : les mêmes valeurs repassent par la création.
        lien = json.loads(suppr["old_value"])
        r = _lier(atelier, lien["from_id"], lien["to_id"], relation_type=lien["relation_type"],
                  since=lien["since"], until=lien["until"], source=lien["source"])
        assert r.status_code == 200, r.text


# ─── Journal au millième : l'encadré de conflit dit la vraie valeur d'avant ───

class TestJournalAuMillieme:
    def test_une_ligne_de_journal_est_datee_au_millieme(self, atelier):
        eid = atelier["fiche"]()
        assert _patch(atelier, eid, atelier["valid"], address="1 rue du Four").status_code == 200
        [ligne] = _journal(atelier, eid, field="address")
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}", ligne["at"]), ligne["at"]

    def test_une_date_posee_a_la_main_n_est_pas_touchee(self, atelier):
        _, i = atelier["sql"]("INSERT INTO audit_log(action, at) VALUES('x', '2026-01-01 10:00:00')")
        assert atelier["sql"]("SELECT at FROM audit_log WHERE id=?", (i,))[0][0]["at"] \
            == "2026-01-01 10:00:00"

    def test_l_encadre_de_conflit_donne_la_valeur_que_l_editeur_avait_sous_les_yeux(self, atelier):
        """Reproduction du 15/11 : A écrit, B réécrit dans la même seconde, A
        (resté sur sa lecture) enregistre. L'encadré disait « avant : — » —
        la valeur d'AVANT l'écriture de A — au lieu de celle que A avait lue."""
        eid = atelier["fiche"]()
        r1 = _patch(atelier, eid, atelier["valid"], address="12 rue du Four")
        lu_par_a = r1.json()["updated_at"]
        r2 = atelier["client"].patch(
            f"/api/atelier/entities/{eid}", headers=atelier["admin"],
            json={"updated_at": lu_par_a, "address": "14 rue du Four"})
        assert r2.status_code == 200, r2.text
        r3 = atelier["client"].patch(
            f"/api/atelier/entities/{eid}", headers=atelier["valid"],
            json={"updated_at": lu_par_a, "address": "16 rue du Four"})
        assert r3.status_code == 409, r3.text
        detail = r3.json()["detail"]
        assert detail["par"] == "admin@exemple.fr"
        [champ] = detail["champs"]
        assert (champ["champ"], champ["avant"], champ["apres"]) == \
            ("address", "12 rue du Four", "14 rue du Four")

    def test_une_ligne_de_motif_n_est_pas_un_champ_de_l_encadre(self, atelier):
        eid = atelier["fiche"]()
        lu = _patch(atelier, eid, atelier["valid"], name="Nom un", motif="registre").json()
        r = atelier["client"].patch(
            f"/api/atelier/entities/{eid}", headers=atelier["admin"],
            json={"updated_at": lu["updated_at"], "name": "Nom deux", "motif": "autre registre"})
        assert r.status_code == 200, r.text
        r = atelier["client"].patch(
            f"/api/atelier/entities/{eid}", headers=atelier["valid"],
            json={"updated_at": lu["updated_at"], "address": "x"})
        assert r.status_code == 409
        assert [c["champ"] for c in r.json()["detail"]["champs"]] == ["name"]

    def test_la_migration_pose_le_declencheur_sur_une_base_existante(self, atelier):
        from collectors import db as db_mod
        c = sqlite3.connect(atelier["chemin_db"])
        c.execute("DROP TRIGGER audit_log_at_milliemes")
        c.execute("INSERT INTO audit_log(action) VALUES('ancienne')")   # à la seconde
        c.commit()
        c.close()
        db_mod.init_db()
        atelier["sql"]("INSERT INTO audit_log(action) VALUES('nouvelle')")
        lignes = {l["action"]: l["at"] for l in
                  atelier["sql"]("SELECT action, at FROM audit_log")[0]}
        assert len(lignes["ancienne"]) == 19      # l'existant n'est pas réécrit
        assert len(lignes["nouvelle"]) == 23
        # À jour, il n'est plus reconstruit : aucun DROP au second passage.
        vus = []
        conn = sqlite3.connect(atelier["chemin_db"])
        conn.set_trace_callback(vus.append)
        db_mod._rattraper_declencheurs(conn, db_mod.SCHEMA_PATH.read_text())
        conn.close()
        assert not [v for v in vus if v.startswith("DROP TRIGGER")]


# ─── Ajout au brief (orchestration, 15/11) : /api/marches filtre sur l'objet ──

class TestMarchesParObjet:
    def _marches(self, atelier):
        for i, (objet, acheteur, titulaire, date) in enumerate((
                ("Travaux de voirie rue du Four", "Mairie A", "Routes SA", "2024-03-01"),
                ("Fourniture de repas de cantine", "Mairie A", "Repas SA", "2024-05-01"),
                ("Voirie communale 100% enrobé", "Mairie B", "Routes SA", "2023-02-01"))):
            atelier["sql"]("INSERT INTO marches_publics(raw_id, objet, acheteur_siren, acheteur_nom,"
                           " titulaire_nom, date_notif, source) VALUES(?,?,?,?,?,?,'essai')",
                           (f"m{i}", objet, f"99900000{i}", acheteur, titulaire, date))

    def _objets(self, atelier, **params):
        r = atelier["client"].get("/api/marches", headers=atelier["contrib"], params=params)
        assert r.status_code == 200, r.text
        return sorted(m["objet"] for m in r.json())

    def test_trouve_par_un_mot_de_l_objet_pas_trouve_sinon(self, atelier):
        self._marches(atelier)
        assert self._objets(atelier, objet="cantine") == ["Fourniture de repas de cantine"]
        assert self._objets(atelier, objet="VOIRIE") == [
            "Travaux de voirie rue du Four", "Voirie communale 100% enrobé"]
        assert self._objets(atelier, objet="piscine") == []
        assert len(self._objets(atelier)) == 3          # sans le paramètre : tout

    def test_se_combine_avec_acheteur_titulaire_et_annee(self, atelier):
        self._marches(atelier)
        assert self._objets(atelier, objet="voirie", acheteur="Mairie B") == \
            ["Voirie communale 100% enrobé"]
        assert self._objets(atelier, objet="voirie", titulaire="Routes", year=2024) == \
            ["Travaux de voirie rue du Four"]
        assert self._objets(atelier, objet="cantine", titulaire="Routes") == []

    def test_les_jokers_saisis_sont_des_caracteres(self, atelier):
        self._marches(atelier)
        assert self._objets(atelier, objet="100%") == ["Voirie communale 100% enrobé"]
        assert self._objets(atelier, objet="%") == ["Voirie communale 100% enrobé"]
        assert self._objets(atelier, objet="_") == []

    def test_l_objet_est_borne(self, atelier):
        r = atelier["client"].get("/api/marches", headers=atelier["contrib"],
                                  params={"objet": "x" * 201})
        assert r.status_code == 422
