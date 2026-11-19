"""Une anomalie de cartouche ou de numérotation lue à l'OCR ne part dans la page
« en clair » que si l'image de la pièce a été regardée (`vu_sur_image`).

Sans cette marque : mise à part (« à vérifier »), signalée par le vérificateur
sans bloquer, absente du rendu. Les autres natures et l'ancienne forme (chaîne
seule) ne changent pas.
"""
from __future__ import annotations

import json

import pytest

from collectors.en_clair.rendu import erreurs, page_erreurs
from collectors.en_clair.verifier import trier_anomalies, verifier
from test_en_clair import _chemin, _releve, instance  # noqa: F401  (fixture)

VU = "p. 3 de data/pv.pdf, cartouche relu à l'image"


def _ecrire(instance, **champs):
    r = _releve()
    r.update(champs)
    _chemin(instance).write_text(json.dumps(r, ensure_ascii=False))
    return r


@pytest.mark.parametrize("nature", ["cartouche", "numerotation"])
def test_sans_marque_l_anomalie_est_ecartee_et_signalee_sans_bloquer(instance, nature):
    r = _ecrire(instance, anomalies_seance=[{"texte": "Cartouche daté du 31 février.", "nature": nature}])
    assert verifier(_chemin(instance)) == []
    assert any("à vérifier" in a and "31 février" in a for a in verifier.avis)
    assert erreurs(r) == []
    assert "31 février" not in page_erreurs(r)


@pytest.mark.parametrize("vu", [None, "", "   ", True])
def test_une_marque_vide_ou_non_textuelle_ne_vaut_pas_marque(instance, vu):
    r = _ecrire(instance, anomalies_seance=[
        {"texte": "Numéro saute de 4 à 6.", "nature": "numerotation", "vu_sur_image": vu}])
    assert erreurs(r) == []


@pytest.mark.parametrize("nature", ["cartouche", "numerotation"])
def test_avec_marque_l_anomalie_est_rendue(instance, nature):
    r = _ecrire(instance, anomalies_seance=[
        {"texte": "Cartouche daté du 31 février.", "nature": nature, "vu_sur_image": VU}])
    assert verifier(_chemin(instance)) == []
    assert not any("à vérifier" in a for a in verifier.avis)
    assert erreurs(r) == ["Cartouche daté du 31 février."]
    assert "31 février" in page_erreurs(r)


def test_une_autre_nature_est_inchangee_sans_marque(instance):
    r = _ecrire(instance, anomalies_seance=[{"texte": "Le PV ne dit pas qui préside.", "nature": "autre"},
                                            {"texte": "Sans nature."}])
    assert erreurs(r) == ["Le PV ne dit pas qui préside.", "Sans nature."]
    assert verifier(_chemin(instance)) == []


def test_ancien_format_chaine_seule_est_lu_sans_erreur(instance):
    r = _releve()
    assert r["anomalies_seance"] == ["Le PV ne dit pas qui préside."]
    assert verifier(_chemin(instance)) == []
    assert erreurs(r) == ["Le PV ne dit pas qui préside."]


def test_l_anomalie_d_un_acte_suit_la_meme_regle(instance):
    r = _releve()
    r["actes"][0]["anomalie"] = {"texte": "Numéro lu 7 au lieu de 1.", "nature": "numerotation"}
    r["actes"][1]["anomalie"] = {"texte": "Cartouche sans date.", "nature": "cartouche", "vu_sur_image": VU}
    _ecrire(instance, actes=r["actes"])
    assert verifier(_chemin(instance)) == []
    assert erreurs(r) == ["Le PV ne dit pas qui préside.", "Délibération n°2 : Cartouche sans date."]
    assert any("n°1 (numerotation)" in a for a in verifier.avis)


def test_une_forme_ou_une_nature_inconnue_est_une_faute(instance):
    _ecrire(instance, anomalies_seance=[{"texte": "x", "nature": "cartouches"}, {"nature": "autre"}, 3])
    fautes = verifier(_chemin(instance))
    assert len([f for f in fautes if f.startswith("ANOMALIE")]) == 3


def test_une_incoherence_de_votes_doit_etre_dite_dans_une_anomalie_rendue(instance):
    r = _releve()
    r["actes"][0]["vote"] = {"pour": 9, "contre": 0, "abstentions": 0}
    _ecrire(instance, actes=r["actes"], anomalies_seance=[
        {"texte": "n°1 : 9 voix pour 3 votants.", "nature": "cartouche"}])
    assert any(f.startswith("VOTES") for f in verifier(_chemin(instance)))
    _ecrire(instance, actes=r["actes"], anomalies_seance=[
        {"texte": "n°1 : 9 voix pour 3 votants.", "nature": "cartouche", "vu_sur_image": VU}])
    assert not any(f.startswith("VOTES") for f in verifier(_chemin(instance)))


def test_trier_sans_anomalie():
    assert trier_anomalies({"actes": []}) == ([], [], [])
