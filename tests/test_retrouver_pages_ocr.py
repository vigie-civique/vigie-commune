"""Les débuts de page d'un texte dont le PDF océrisé est resté sur une autre machine.

25/10/2026 : 78 des 83 textes de Lasalle sont sur le VPS sans leur `x.ocr.pdf`.
Les pages relues ailleurs ne valent que si leur jointure redonne l'empreinte du
texte que tient la base ; le texte, lui, ne bouge pas.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import cm_ocr, conseils  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["ID : 030-1-20261015-DEL2610_01-DE\nDÉLIBÉRATION 1", "suite de la 1", "DÉLIBÉRATION 2\n3 500,00 €"]
TEXTE = "\n".join(PAGES)


def _script():
    spec = importlib.util.spec_from_file_location(
        "retrouver_pages_ocr", ROOT / "scripts" / "retrouver_pages_ocr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    """Un texte et son manifeste SANS pages, le PDF océrisé absent — l'état du VPS."""
    monkeypatch.setattr(shutil, "which", lambda nom: None)
    cm_ocr.versions_outils.cache_clear()
    cible = tmp_path / "recueil.ocr.pdf"
    cible.with_suffix(".txt").write_text(TEXTE, encoding="utf-8")
    cm_ocr.ecrire_manifeste(cible, TEXTE, None, provenance="anterieure-au-manifeste",
                            langue=None, outils="inconnu")
    s = _script()
    monkeypatch.setattr(s, "DOSSIER", tmp_path)
    return s, cible


def test_lister_exporter_appliquer(serveur, tmp_path, monkeypatch, capsys):
    s, cible = serveur
    s.lister()
    liste = tmp_path / "sans_pages.txt"
    liste.write_text(capsys.readouterr().out)
    assert liste.read_text().split() == ["recueil", cm_ocr.empreinte(TEXTE.encode())]

    mac = tmp_path / "mac"
    mac.mkdir()
    (mac / "recueil.ocr.pdf").write_bytes(b"%PDF-1.4 ocr")
    monkeypatch.setattr(conseils, "_pages_natives", lambda c: [])
    monkeypatch.setattr(conseils, "pages_pdf", lambda c: list(PAGES))
    s.exporter(mac, liste)
    export = tmp_path / "pages.json"
    export.write_text(capsys.readouterr().out.strip())

    s.appliquer(export, ecrire=False)
    assert json.loads(cible.with_suffix(".json").read_text())["pages"] is None
    s.appliquer(export, ecrire=True)
    m = json.loads(cible.with_suffix(".json").read_text())
    assert m["pages"] == cm_ocr.positions_pages(PAGES)
    assert cm_ocr.texte_garde(cible) == TEXTE        # le texte se lit toujours


def test_une_relecture_qui_ne_redonne_pas_le_texte_nest_pas_retenue(serveur, tmp_path,
                                                                     monkeypatch, capsys):
    s, _ = serveur
    liste = tmp_path / "sans_pages.txt"
    liste.write_text(f"recueil {cm_ocr.empreinte(TEXTE.encode())}\n")
    (tmp_path / "recueil.ocr.pdf").write_bytes(b"%PDF-1.4 ocr")
    monkeypatch.setattr(conseils, "_pages_natives", lambda c: [])
    monkeypatch.setattr(conseils, "pages_pdf", lambda c: PAGES[:2])
    s.exporter(tmp_path, liste)
    assert json.loads(capsys.readouterr().out.strip()) == {}


def test_des_positions_qui_ne_tombent_pas_sur_un_saut_de_ligne_sont_refusees(serveur, tmp_path,
                                                                             capsys):
    s, cible = serveur
    export = tmp_path / "pages.json"
    export.write_text(json.dumps({"recueil": {
        "texte_sha256": cm_ocr.empreinte(TEXTE.encode()), "pages": [0, 5, 20]}}))
    s.appliquer(export, ecrire=True)
    assert "positions_refusees : 1" in capsys.readouterr().out
    assert json.loads(cible.with_suffix(".json").read_text())["pages"] is None
