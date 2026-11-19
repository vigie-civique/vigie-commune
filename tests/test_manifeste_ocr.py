"""Le manifeste du texte de référence — `x.ocr.json` à côté de `x.ocr.txt`.

24/10/2026, étape 2 du plan de l'architecte pour la lecture des montants. Un
rapport lu sur le Mac doit prouver qu'il a lu LE texte que tient la base ; la
date d'un fichier ne prouve rien. Quand le manifeste est là, c'est lui qui
décide : empreinte, rôle, taille du PDF océrisé. Sans lui, la règle d'avant.

Aucun PDF réel : ce qui se teste est la règle de choix, pas la lecture.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import cm_ocr, conseils  # noqa: E402

PAGES = ["ID : 030-1-20261015-DEL2610_01-DE\nDÉLIBÉRATION 1", "suite de la 1", "DÉLIBÉRATION 2\n3 500,00 €"]


@pytest.fixture
def ocerise(tmp_path, monkeypatch):
    """Un original, son PDF océrisé, et une lecture qui rend trois pages."""
    original = tmp_path / "recueil.pdf"
    original.write_bytes(b"%PDF-1.4 original")
    cible = original.with_suffix(".ocr.pdf")
    cible.write_bytes(b"%PDF-1.4 ocr")
    monkeypatch.setattr(conseils, "_pages_natives", lambda c: [])
    monkeypatch.setattr(conseils, "pages_pdf", lambda c: list(PAGES))
    monkeypatch.setattr(shutil, "which", lambda nom: None)
    cm_ocr.versions_outils.cache_clear()
    return original, cible


def _manifeste(cible):
    return json.loads(cible.with_suffix(".json").read_text())


def test_la_lecture_ecrit_un_manifeste_de_reference(ocerise):
    original, cible = ocerise
    texte = conseils.ocr(original)
    m = _manifeste(cible)
    assert m["format"] == "vigie-ocr-texte/1" and m["role"] == "reference"
    assert m["provenance"] == "collecte"
    assert m["texte_sha256"] == cm_ocr.empreinte(texte.encode())
    assert m["original_sha256"] == cm_ocr.empreinte(b"%PDF-1.4 original")
    assert len(m["texte_sha256"]) == 64 and m["ocr_pdf_taille"] == len(b"%PDF-1.4 ocr")


def test_les_positions_redonnent_chaque_page(ocerise):
    original, cible = ocerise
    texte = conseils.ocr(original)
    debuts = _manifeste(cible)["pages"] + [len(texte) + 1]
    assert [texte[debuts[k]:debuts[k + 1] - 1] for k in range(len(PAGES))] == PAGES


def test_un_texte_retouche_est_ecarte(ocerise):
    original, cible = ocerise
    conseils.ocr(original)
    cible.with_suffix(".txt").write_text("TEXTE RETOUCHÉ À LA MAIN")
    assert cm_ocr.texte_garde(cible) is None


def test_avec_manifeste_la_date_ne_compte_plus(ocerise):
    """Une copie (`rsync`, sauvegarde) inverse les dates : le texte reste le même."""
    original, cible = ocerise
    texte = conseils.ocr(original)
    os.utime(cible.with_suffix(".txt"), (1_000_000, 1_000_000))
    assert cm_ocr.texte_garde(cible) == texte


def test_une_reocerisation_adoptee_desavoue_le_texte(ocerise):
    original, cible = ocerise
    conseils.ocr(original)
    cible.write_bytes(b"%PDF-1.4 ocr refait, plus long")
    assert cm_ocr.texte_garde(cible) is None


def test_un_role_autre_que_reference_n_est_jamais_lu(ocerise):
    original, cible = ocerise
    conseils.ocr(original)
    m = _manifeste(cible)
    m["role"] = "seconde-lecture"
    cible.with_suffix(".json").write_text(json.dumps(m))
    assert cm_ocr.texte_garde(cible) is None


def test_un_manifeste_illisible_vaut_desaveu(ocerise):
    original, cible = ocerise
    conseils.ocr(original)
    cible.with_suffix(".json").write_text("{tronqué")
    assert cm_ocr.texte_garde(cible) is None


def test_un_texte_ancien_sans_manifeste_se_lit_comme_avant(tmp_path):
    cible = tmp_path / "pv.ocr.pdf"
    cible.with_suffix(".txt").write_text("TEXTE D'AVANT LE MANIFESTE")
    assert cm_ocr.texte_garde(cible) == "TEXTE D'AVANT LE MANIFESTE"


def test_des_pages_qui_ne_font_pas_le_texte_ne_donnent_pas_de_positions(tmp_path):
    cible = tmp_path / "pv.ocr.pdf"
    m = cm_ocr.ecrire_manifeste(cible, "AUTRE CHOSE", ["a", "b"], outils={})
    assert m["pages"] is None


def test_le_manifeste_retrospectif_n_invente_ni_outil_ni_machine(tmp_path, monkeypatch):
    """Décision du 24/10 : provenance « antérieure au manifeste », outils inconnus.
    Un texte périmé n'est pas figé comme référence ; le rejeu ne réécrit rien."""
    from scripts import manifester_textes_ocr as M
    pv = tmp_path / "pv"
    pv.mkdir()
    (pv / "ancien.ocr.txt").write_text("TEXTE D'AVANT")
    perime = pv / "perime.ocr.txt"
    perime.write_text("TEXTE PÉRIMÉ")
    os.utime(perime, (1_000_000, 1_000_000))
    (pv / "perime.ocr.pdf").write_bytes(b"%PDF-1.4 refait")
    monkeypatch.setattr(M, "DOSSIERS", (pv,))
    monkeypatch.setattr(shutil, "which", lambda nom: None)

    assert M.traiter(appliquer=False)["manifestes"] == 1
    assert not (pv / "ancien.ocr.json").exists()

    bilan = M.traiter(appliquer=True)
    assert (bilan["manifestes"], bilan["perimes"], bilan["sans_pages"]) == (1, 1, 1)
    m = json.loads((pv / "ancien.ocr.json").read_text())
    assert (m["provenance"], m["outils"], m["machine"]) == ("anterieure-au-manifeste", "inconnu", None)
    assert cm_ocr.texte_garde(pv / "ancien.ocr.pdf") == "TEXTE D'AVANT"
    assert not (pv / "perime.ocr.json").exists()
    assert M.traiter(appliquer=True)["deja"] == 1


def test_les_pages_gardees_ne_valent_que_pour_le_texte_du_manifeste(ocerise):
    original, cible = ocerise
    texte = conseils.ocr(original)
    assert cm_ocr.pages_gardees(cible, texte) == PAGES
    assert cm_ocr.pages_gardees(cible, texte + "retouche") is None
    m = _manifeste(cible)
    m["pages"] = None
    cible.with_suffix(".json").write_text(json.dumps(m))
    assert cm_ocr.pages_gardees(cible, texte) is None

