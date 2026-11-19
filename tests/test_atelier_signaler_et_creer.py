"""Signaler une fiche, créer une fiche : ce que fait chaque rôle.

Audit du 15/11/2026, constats 7 et 8 : aucun moyen de demander le retrait
d'une fiche ni de signaler un doublon ; la création d'une fiche jetait le
SIREN, rejoignait une fiche existante sans le dire et s'inscrivait au journal
sans fiche. Décision de Julien du même jour : le signalement est une
proposition, tranchée par le validateur avec les gestes qu'il a déjà.
Vrais jetons, vraie base SQLite au schéma réel.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from test_atelier_corrections_qui_tiennent import (SOURCE, _collecter,  # noqa: E402,F401
                                                  _relations, atelier)

# Neuf chiffres dont la clé de Luhn tient ; le même, dernier chiffre changé.
SIREN_OK = "732829320"
SIREN_FAUX = "732829321"


def _signaler(atelier, qui, eid, **corps):
    return atelier["client"].post(f"/api/atelier/entities/{eid}/signalement",
                                  headers=atelier[qui], json=corps)


def _file(atelier, qui="valid"):
    rep = atelier["client"].get("/api/atelier/propositions?nature=signalement",
                                headers=atelier[qui])
    assert rep.status_code == 200, rep.text
    return rep.json()["propositions"]


def _verdict(atelier, eid):
    lignes, _ = atelier["sql"]("SELECT review_status FROM annotations WHERE "
                               "object_type='entity' AND object_id=?", (eid,))
    return lignes[0]["review_status"] if lignes else None


# ─── Signaler ────────────────────────────────────────────────────────────────

class TestSignaler:
    @pytest.mark.parametrize("qui", ["contrib", "valid"])
    def test_tout_compte_signale_et_la_fiche_ne_bouge_pas(self, atelier, qui):
        eid = atelier["fiche"]("Les Amis du Four")
        rep = _signaler(atelier, qui, eid, motif="retirer", texte="  Association  dissoute ")
        assert rep.status_code == 202, rep.text
        assert _verdict(atelier, eid) is None, "un signalement n'écarte rien"
        [p] = _file(atelier)
        assert (p["nature"], p["entity_id"], p["propose_par"]) == (
            "signalement", eid, f"{'contrib' if qui == 'contrib' else 'valid'}@exemple.fr")
        assert p["charge"] == {"motif": "retirer", "doublon_de": None,
                               "texte": "Association dissoute"}
        assert p["actuel"] == {"verdict": "jamais_relu"}

    def test_doublon_nomme_l_autre_fiche(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Amis du four (Les)")
        assert _signaler(atelier, "contrib", a, motif="doublon").status_code == 400
        assert _signaler(atelier, "contrib", a, motif="doublon",
                         doublon_de=a).status_code == 400
        assert _signaler(atelier, "contrib", a, motif="doublon",
                         doublon_de=99999).status_code == 404
        assert _signaler(atelier, "contrib", a, motif="doublon",
                         doublon_de=b).status_code == 202
        [p] = _file(atelier)
        assert p["doublon"]["id"] == b and p["doublon"]["name"] == "Amis du four (Les)"

    def test_motif_inconnu_ou_fiche_absente(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four")
        assert _signaler(atelier, "contrib", eid, motif="supprimer").status_code == 400
        assert _signaler(atelier, "contrib", 99999, motif="revoir").status_code == 404
        assert _signaler(atelier, "contrib", eid, motif="revoir",
                         texte="x" * 501).status_code == 422

    def test_deux_onglets_le_second_reprend_le_premier(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Amis du four")
        _signaler(atelier, "contrib", a, motif="doublon", doublon_de=b)
        rep = _signaler(atelier, "contrib", a, motif="retirer", texte="Dissoute en 2019")
        assert rep.json()["reprend"], rep.text
        [p] = _file(atelier)
        # Remplacé, pas fusionné : l'ancienne fiche visée ne reste pas accrochée.
        assert p["charge"] == {"motif": "retirer", "doublon_de": None,
                               "texte": "Dissoute en 2019"}
        # Un autre auteur, lui, ne remplace rien.
        _signaler(atelier, "valid", a, motif="revoir")
        assert len(_file(atelier)) == 2

    def test_le_contributeur_ne_voit_que_les_siens_et_ne_tranche_pas(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four")
        _signaler(atelier, "valid", eid, motif="revoir")
        assert _file(atelier, "contrib") == []
        _signaler(atelier, "contrib", eid, motif="retirer")
        [p] = _file(atelier, "contrib")
        rep = atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                                     headers=atelier["contrib"],
                                     json={"accepter": True, "issue": "ecarte"})
        assert rep.status_code == 403
        assert _verdict(atelier, eid) is None
        # Il peut retirer le sien.
        assert atelier["client"].delete(f"/api/atelier/propositions/{p['id']}",
                                        headers=atelier["contrib"]).status_code == 200


class TestTrancherUnSignalement:
    def _un(self, atelier, **corps):
        eid = atelier["fiche"]("Les Amis du Four")
        _signaler(atelier, "contrib", eid, **(corps or {"motif": "retirer"}))
        [p] = _file(atelier)
        return eid, p

    def _decider(self, atelier, p, **corps):
        return atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                                      headers=atelier["valid"], json=corps)

    def test_ecarter_pose_le_verdict_et_journalise(self, atelier):
        eid, p = self._un(atelier)
        assert self._decider(atelier, p, accepter=True).status_code == 400, \
            "accepter sans dire écarter ou à revoir"
        rep = self._decider(atelier, p, accepter=True, issue="ecarte", motif="Dissoute")
        assert rep.status_code == 200, rep.text
        assert rep.json()["etat"] == "acceptee"
        assert rep.json()["motif"] == "fiche écartée — Dissoute"
        assert _verdict(atelier, eid) == "ecarte"
        journal, _ = atelier["sql"]("SELECT table_name, action, field FROM audit_log "
                                    "WHERE entity_id=? ORDER BY id", (eid,))
        actions = [(j["table_name"], j["action"]) for j in journal]
        assert ("propositions", "proposer") in actions
        assert ("propositions", "acceptee") in actions
        assert any(t == "annotations" or j["field"] == "review_status"
                   for (t, _), j in zip(actions, journal)), journal

    def test_a_revoir(self, atelier):
        eid, p = self._un(atelier, motif="revoir", texte="Adresse douteuse")
        rep = self._decider(atelier, p, accepter=True, issue="a_revoir")
        assert rep.status_code == 200, rep.text
        assert _verdict(atelier, eid) == "a_revoir"

    def test_refuser_demande_un_motif_et_ne_touche_pas_la_fiche(self, atelier):
        eid, p = self._un(atelier)
        assert self._decider(atelier, p, accepter=False).status_code == 400
        rep = self._decider(atelier, p, accepter=False, motif="Toujours active")
        assert rep.json()["etat"] == "refusee"
        assert _verdict(atelier, eid) is None

    def test_verdict_refuse_hors_signalement(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four")
        _, pid = atelier["sql"](
            "INSERT INTO propositions(nature, object_type, object_id, entity_id, charge, "
            "avant, propose_par) VALUES('fiche','entity',?,?,'{\"name\":\"X\"}',"
            "'{\"name\":\"Les Amis du Four\"}', 1)", (eid, eid))
        rep = self._decider(atelier, {"id": pid}, accepter=True, issue="ecarte")
        assert rep.status_code == 400


class TestFusionAFaire:
    """Décision de Julien, 16/11/2026 : un troisième geste sur un signalement de
    doublon. La fiche n'est ni écartée ni à revoir ; la fusion est demandée,
    pas faite, et se retrouve dans la file (`issue=fusion_a_faire`)."""

    def _doublon(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Amis du four (Les)")
        _signaler(atelier, "contrib", a, motif="doublon", doublon_de=b, texte="Même asso")
        [p] = _file(atelier)
        return a, b, p

    def _decider(self, atelier, qui, p, **corps):
        return atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                                      headers=atelier[qui], json=corps)

    def test_validateur_garde_la_fiche_et_la_fusion_se_retrouve(self, atelier):
        a, b, p = self._doublon(atelier)
        rep = self._decider(atelier, "valid", p, accepter=True, issue="fusion_a_faire")
        assert rep.status_code == 200, rep.text
        assert (rep.json()["etat"], rep.json()["issue"]) == ("acceptee", "fusion_a_faire")
        assert rep.json()["motif"] == f"fiche gardée, fusion à faire avec la fiche n° {b}"
        # Ni écartée ni à revoir : aucune décision posée sur l'une ou l'autre.
        assert _verdict(atelier, a) is None and _verdict(atelier, b) is None
        # Au journal des deux fiches, avec la fiche visée.
        journal, _ = atelier["sql"]("SELECT entity_id, new_value FROM audit_log WHERE "
                                    "action='fusion_a_faire' ORDER BY entity_id")
        assert [j["entity_id"] for j in journal] == sorted([a, b])
        assert all(f'"doublon_de": {b}' in j["new_value"] for j in journal)
        # Les fusions demandées : une liste dans la file.
        rep = atelier["client"].get("/api/atelier/propositions?etat=acceptee"
                                    "&issue=fusion_a_faire", headers=atelier["valid"])
        [f] = rep.json()["propositions"]
        assert (f["entity_id"], f["doublon"]["id"]) == (a, b)
        assert atelier["client"].get("/api/atelier/propositions?etat=acceptee&issue=x",
                                     headers=atelier["valid"]).status_code == 400

    def test_contributeur_403(self, atelier):
        _, _, p = self._doublon(atelier)
        rep = self._decider(atelier, "contrib", p, accepter=True, issue="fusion_a_faire")
        assert rep.status_code == 403
        assert _file(atelier)[0]["etat"] == "en_attente"

    @pytest.mark.parametrize("motif", ["retirer", "revoir"])
    def test_refuse_hors_doublon(self, atelier, motif):
        eid = atelier["fiche"]("Les Amis du Four")
        _signaler(atelier, "contrib", eid, motif=motif)
        [p] = _file(atelier)
        rep = self._decider(atelier, "valid", p, accepter=True, issue="fusion_a_faire")
        assert rep.status_code == 400
        assert _file(atelier)[0]["etat"] == "en_attente"

    def test_issue_refusee_sur_un_refus(self, atelier):
        _, _, p = self._doublon(atelier)
        rep = self._decider(atelier, "valid", p, accepter=False, motif="Non",
                            issue="fusion_a_faire")
        assert rep.status_code == 400


# ─── /api/search ─────────────────────────────────────────────────────────────

def test_recherche_marque_une_fiche_ecartee(atelier):
    from collectors.db import init_db  # noqa: F401  (l'index plein texte vient du schéma)
    eid = atelier["fiche"]("Les Amis du Four")
    atelier["fiche"]("Les Amis du Moulin")
    atelier["client"].patch(f"/api/atelier/entities/{eid}/status", headers=atelier["valid"],
                            json={"verdict": "ecarte"})
    rep = atelier["client"].get("/api/search?q=Amis", headers=atelier["contrib"])
    assert rep.status_code == 200, rep.text
    par_nom = {r["name"]: r for r in rep.json()}
    assert par_nom["Les Amis du Four"]["ecartee"] is True
    assert par_nom["Les Amis du Four"]["verdict"] == "ecarte"
    assert par_nom["Les Amis du Moulin"]["ecartee"] is False


# ─── Créer une fiche ─────────────────────────────────────────────────────────

def _creer(atelier, qui, **valeurs):
    return atelier["client"].post("/api/atelier/saisies", headers=atelier[qui], json={
        "objet": "entite", "source": SOURCE,
        "confidence": "confirmed" if qui == "valid" else "probable",
        "valeurs": {"type": "association", **valeurs}})


class TestCreer:
    @pytest.mark.parametrize("qui", ["contrib", "valid"])
    def test_la_reponse_rend_l_identifiant_et_le_journal_porte_la_fiche(self, atelier, qui):
        rep = _creer(atelier, qui, name="Comité des fêtes de Valmorin")
        assert rep.status_code == 201, rep.text
        eid = rep.json()["entity_id"]
        assert eid == rep.json()["entite"]["id"] and rep.json()["entite"]["cree"]
        journal, _ = atelier["sql"]("SELECT table_name, action FROM audit_log WHERE "
                                    "entity_id=? AND table_name='saisies'", (eid,))
        assert journal == [{"table_name": "saisies", "action": "create"}]
        # …donc dans l'historique de la fiche.
        fiche = atelier["client"].get(f"/api/atelier/entities/{eid}", headers=atelier[qui]).json()
        assert any(a.get("table_name") == "saisies" or a.get("field") == "entite"
                   for a in fiche["audit"]), fiche["audit"]

    def test_siren_valide_garde(self, atelier):
        rep = _creer(atelier, "valid", name="Les Amis du Four", siren="732 829 320")
        assert rep.status_code == 201, rep.text
        eid = rep.json()["entity_id"]
        lignes, _ = atelier["sql"]("SELECT siren FROM associations WHERE entity_id=?", (eid,))
        assert lignes == [{"siren": SIREN_OK}]
        # Une seconde saisie au même SIREN rejoint la fiche, quel que soit le nom.
        rep2 = _creer(atelier, "valid", name="AMIS FOUR", siren=SIREN_OK)
        assert rep2.json()["entity_id"] == eid and not rep2.json()["entite"]["cree"]

    def test_siren_d_une_entreprise_garde(self, atelier):
        rep = _creer(atelier, "valid", name="Boulangerie Testal", type="business",
                     siren="73282932000074")
        assert rep.status_code == 201, rep.text
        lignes, _ = atelier["sql"]("SELECT siren FROM businesses WHERE entity_id=?",
                                   (rep.json()["entity_id"],))
        assert lignes == [{"siren": SIREN_OK}]

    @pytest.mark.parametrize("siren", [SIREN_FAUX, "12345", "SIREN 732829320", "7328293201"])
    def test_siren_invalide_refuse_avec_un_message(self, atelier, siren):
        rep = _creer(atelier, "contrib", name="Les Amis du Four", siren=siren)
        assert rep.status_code == 400
        assert "SIREN" in rep.json()["detail"]
        lignes, _ = atelier["sql"]("SELECT 1 FROM entities")
        assert lignes == [], "rien de créé"

    def test_siren_refuse_pour_une_personne(self, atelier):
        rep = _creer(atelier, "contrib", name="Jean TESTAL", type="person", siren=SIREN_OK)
        assert rep.status_code == 400

    def test_une_variante_de_nom_le_dit_avant_d_enregistrer(self, atelier):
        c = atelier["client"]
        eid = atelier["fiche"]("Les Amis du Four")
        rep = c.get("/api/atelier/saisies/entite/existante",
                    params={"type": "association", "name": "LES AMIS DU FOUR"},
                    headers=atelier["contrib"])
        assert rep.status_code == 200, rep.text
        assert rep.json()["fiche"]["id"] == eid and rep.json()["meme_nom"] is False
        rep = c.get("/api/atelier/saisies/entite/existante",
                    params={"type": "association", "name": "Les Amis du Four"},
                    headers=atelier["contrib"])
        assert rep.json()["meme_nom"] is True
        rep = c.get("/api/atelier/saisies/entite/existante",
                    params={"type": "association", "name": "Club de pétanque"},
                    headers=atelier["contrib"])
        assert rep.json() == {"fiche": None}
        # Rien n'a été écrit par la question.
        lignes, _ = atelier["sql"]("SELECT count(*) AS n FROM entities")
        assert lignes[0]["n"] == 1
        assert c.get("/api/atelier/saisies/entite/existante",
                     params={"type": "association", "name": "x"}).status_code == 401


# ─── Supprimer un lien : le motif ────────────────────────────────────────────

class TestMotifDeSuppression:
    def test_lien_de_collecteur_exige_un_motif(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL",
                                                                       type_="person")
        _collecter(b, a)
        [r1, r2] = _relations(atelier, b, a)
        rep = c.delete(f"/api/atelier/relations/{r1['id']}", headers=atelier["valid"])
        assert rep.status_code == 400 and "motif" in rep.json()["detail"]
        assert len(_relations(atelier, b, a)) == 2, "rien de supprimé"
        rep = c.delete(f"/api/atelier/relations/{r1['id']}?motif=Homonyme",
                       headers=atelier["valid"])
        assert rep.status_code == 200, rep.text
        # Le même lien reste par l'autre source : dit, avec laquelle.
        assert rep.json()["sources_restantes"] == [
            {"id": r2["id"], "source": r2["source"], "confidence": r2["confidence"]}]
        journal, _ = atelier["sql"]("SELECT new_value FROM audit_log WHERE "
                                    "table_name='relations' AND action='delete'")
        assert journal == [{"new_value": "Homonyme"}]

    def test_le_contributeur_ne_supprime_pas(self, atelier):
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL",
                                                                       type_="person")
        _collecter(b, a)
        [r1, _] = _relations(atelier, b, a)
        rep = atelier["client"].delete(f"/api/atelier/relations/{r1['id']}?motif=x",
                                       headers=atelier["contrib"])
        assert rep.status_code == 403

    def test_lien_pose_a_l_atelier_sans_motif(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL",
                                                                       type_="person")
        rep = c.post(f"/api/atelier/entities/{a}/relations", headers=atelier["valid"],
                     json={"direction": "to", "other_entity_id": b,
                           "relation_type": "président", "confidence": "verified"})
        assert rep.status_code == 200, rep.text
        rep = c.delete(f"/api/atelier/relations/{rep.json()['id']}", headers=atelier["valid"])
        assert rep.status_code == 200 and rep.json()["refus_id"] is None


# ─── Avis de vigie-securite sur la #285 ──────────────────────────────────────

class TestAvisSecurite:
    def test_siren_d_un_contributeur_n_est_pas_ecrit(self, atelier):
        """Sans quoi la collecte rangerait les données du registre sur la fiche
        du contributeur, sous le nom qu'il a choisi."""
        from collectors.db import transaction, upsert_entity
        rep = _creer(atelier, "contrib", name="Mon asso inventée", siren=SIREN_OK)
        assert rep.status_code == 201, rep.text
        eid = rep.json()["entity_id"]
        assert rep.json()["entite"]["siren_a_confirmer"] is True
        assert rep.json()["entite"]["non_repris"] == ["siren"]
        # Le SIREN reste dans la saisie.
        saisie = rep.json()["saisie"]
        assert saisie["valeurs"]["siren"] == SIREN_OK and "siren_confirme_par" not in saisie
        for table in ("associations", "businesses"):
            lignes, _ = atelier["sql"](f"SELECT 1 FROM {table} WHERE entity_id=?", (eid,))
            assert lignes == []
        # Un collecteur qui écrit ce SIREN ne retrouve pas la fiche du contributeur.
        with transaction() as conn:
            autre = upsert_entity(conn, type="association", name="ASSOCIATION DU REGISTRE",
                                  siren=SIREN_OK, commune="Testonville")
        assert autre != eid
        lignes, _ = atelier["sql"]("SELECT 1 FROM associations WHERE entity_id=?", (eid,))
        assert lignes == []

    def test_un_validateur_reprend_le_siren_en_ressaisissant_la_fiche(self, atelier):
        rep = _creer(atelier, "contrib", name="Mon asso inventée", siren=SIREN_OK)
        eid = rep.json()["entity_id"]
        rep = _creer(atelier, "valid", name="Mon asso inventée", siren=SIREN_OK)
        assert rep.status_code == 201, rep.text
        assert rep.json()["entity_id"] == eid and "siren" in rep.json()["entite"]["modifies"]
        lignes, _ = atelier["sql"]("SELECT siren FROM associations WHERE entity_id=?", (eid,))
        assert lignes == [{"siren": SIREN_OK}]
        journal, _ = atelier["sql"]("SELECT new_value FROM audit_log WHERE entity_id=? "
                                    "AND field='siren'", (eid,))
        assert journal == [{"new_value": SIREN_OK}]

    def test_le_validateur_ne_remplace_pas_un_siren_porte_ailleurs(self, atelier):
        a = _creer(atelier, "valid", name="Asso A", siren=SIREN_OK).json()["entity_id"]
        b = _creer(atelier, "contrib", name="Asso B").json()["entity_id"]
        rep = _creer(atelier, "valid", name="Asso B", siren=SIREN_OK)
        # Le SIREN désigne déjà A : la saisie rejoint A, B reste sans SIREN.
        assert rep.json()["entity_id"] == a
        lignes, _ = atelier["sql"]("SELECT 1 FROM associations WHERE entity_id=?", (b,))
        assert lignes == []

    def test_plafond_de_signalements_en_attente(self, atelier, monkeypatch):
        from collectors import propositions
        monkeypatch.setattr(propositions, "PLAFOND_SIGNALEMENTS", 3)
        fiches = [atelier["fiche"](f"Asso {i}") for i in range(5)]
        for eid in fiches[:3]:
            assert _signaler(atelier, "contrib", eid, motif="revoir").status_code == 202
        # Remplacer le sien sur une fiche déjà signalée reste permis.
        assert _signaler(atelier, "contrib", fiches[0], motif="retirer").status_code == 202
        rep = _signaler(atelier, "contrib", fiches[3], motif="revoir")
        assert rep.status_code == 409 and "Attendez" in rep.json()["detail"]
        # Un autre compte n'est pas touché.
        assert _signaler(atelier, "valid", fiches[3], motif="revoir").status_code == 202
        # Une fois l'un d'eux relu, la place se libère.
        p = next(x for x in _file(atelier) if x["entity_id"] == fiches[1])
        atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                               headers=atelier["valid"], json={"accepter": False, "motif": "Non"})
        assert _signaler(atelier, "contrib", fiches[3], motif="revoir").status_code == 202

    def test_re_signaler_apres_un_tranchage_ne_touche_pas_l_ancien(self, atelier):
        eid = atelier["fiche"]("Les Amis du Four")
        _signaler(atelier, "contrib", eid, motif="retirer", texte="Dissoute")
        [p] = _file(atelier)
        atelier["client"].post(f"/api/atelier/propositions/{p['id']}/decision",
                               headers=atelier["valid"], json={"accepter": False,
                                                               "motif": "Toujours active"})
        rep = _signaler(atelier, "contrib", eid, motif="revoir", texte="Adresse")
        assert rep.status_code == 202 and rep.json()["reprend"] == []
        assert rep.json()["proposition"] != p["id"]
        ancien = atelier["client"].get("/api/atelier/propositions?etat=refusee",
                                       headers=atelier["valid"]).json()["propositions"]
        assert [(x["id"], x["charge"], x["motif"]) for x in ancien] == [
            (p["id"], {"motif": "retirer", "doublon_de": None, "texte": "Dissoute"},
             "Toujours active")]

    def test_lever_un_refus_exige_un_motif(self, atelier):
        c = atelier["client"]
        a, b = atelier["fiche"]("Les Amis du Four"), atelier["fiche"]("Jean TESTAL",
                                                                       type_="person")
        _collecter(b, a)
        [r1, _] = _relations(atelier, b, a)
        refus_id = c.delete(f"/api/atelier/relations/{r1['id']}?motif=Homonyme",
                            headers=atelier["valid"]).json()["refus_id"]
        for q in ("", "?motif=", "?motif=%20%20"):
            assert c.delete(f"/api/atelier/relations/refus/{refus_id}{q}",
                            headers=atelier["valid"]).status_code == 400
        lignes, _ = atelier["sql"]("SELECT 1 FROM relations_refusees WHERE id=?", (refus_id,))
        assert lignes, "le refus tient"
        assert c.delete(f"/api/atelier/relations/refus/{refus_id}?motif=Erreur de ma part",
                        headers=atelier["valid"]).status_code == 200
