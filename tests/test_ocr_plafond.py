"""La passe planifiée lit l'OCR déjà fait, et ne lance la reconnaissance que dans
la limite d'un plafond de pages (audit d'architecture du 26/10/2026)."""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import conseils  # noqa: E402


@pytest.fixture
def scan(tmp_path, monkeypatch):
    """Un PDF dont la couche native est trop mince ; compte les reconnaissances."""
    pdf = tmp_path / "pv.pdf"
    pdf.write_bytes(b"%PDF-1.4 scan")
    lances = []
    monkeypatch.setattr(conseils, "texte_pdf", lambda c: "cachet")
    monkeypatch.setattr(conseils, "_trop_mince", lambda c, t: True)
    monkeypatch.setattr(conseils, "_nb_pages", lambda c: 30)
    monkeypatch.setattr(conseils, "ocr", lambda c: lances.append(c.name) or "TEXTE OCR")
    return pdf, lances


def test_sans_ocr_demande_on_garde_la_couche_native(scan):
    pdf, lances = scan
    assert conseils._lire_pdf(pdf, False, None) == ("cachet", "pdf", False)
    assert lances == []


def test_une_reconnaissance_deja_faite_sert_meme_sans_ocr_demande(scan):
    pdf, lances = scan
    pdf.with_suffix(".ocr.pdf").write_bytes(b"%PDF-1.4 ocr")
    assert conseils._lire_pdf(pdf, False, None) == ("TEXTE OCR", "pdf", True)


def test_le_plafond_reporte_ce_qui_ne_tient_pas(scan):
    pdf, lances = scan
    budget = conseils.nouveau_budget_ocr(40)
    assert conseils._lire_pdf(pdf, True, budget)[2] is True      # 30 pages sur 40
    assert budget["pages"] == 10
    assert conseils._lire_pdf(pdf, True, budget)[2] is False     # 30 > 10 restantes
    assert budget["reportes"] == 1 and len(lances) == 1


def test_un_document_plus_long_que_le_plafond_reste_a_la_main(scan):
    pdf, lances = scan
    budget = conseils.nouveau_budget_ocr(20)
    assert conseils._lire_pdf(pdf, True, budget)[2] is False
    assert budget["trop_longs"] == 1 and lances == []


def test_a_la_main_sans_plafond(scan):
    pdf, lances = scan
    assert conseils.nouveau_budget_ocr(None) is None
    assert conseils._lire_pdf(pdf, True, None)[2] is True
