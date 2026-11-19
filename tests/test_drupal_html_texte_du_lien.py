"""La date d'une pièce de conseil se lit à défaut dans le texte de son lien.

Page HTML synthétique, jamais d'extrait d'une vraie page ; pas de réseau.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import conseils  # noqa: E402
from collectors.connecteurs.base import DocumentPublie  # noqa: E402
from collectors.connecteurs.drupal_html import ConnecteurDrupal, _ancres  # noqa: E402

BASE = "https://cc.exemple.invalid"
PDF = BASE + "/wp-content/uploads/2026/11/"

PAGE = f"""
<html><body>
<a href="{PDF}deliberations-visees-2810.pdf">Délibérations du 28.10.2026</a>
<a href="{PDF}piece-sans-date.pdf">Pièces diverses</a>
<a href="{PDF}pv-du-05-fevrier-2026.pdf">Compte rendu du 12 mars 2026</a>
<a href="{PDF}ordre-2810.pdf">Ordre du jour du 28.10</a>
<a href="{PDF}convoc.pdf"><span>Délibérations du</span> 28 octobre 2026</a>
<a href="https://autre.invalid/x.pdf">Délibérations du 01.01.2026</a>
</body></html>
"""


@pytest.fixture
def connecteur(monkeypatch):
    c = ConnecteurDrupal.__new__(ConnecteurDrupal)
    c.pages = {"epci": {"conseil": "/pv/"}}
    c.bases = {"commune": "", "epci": BASE}
    monkeypatch.setattr(c, "_html", lambda url: PAGE if "page=" not in url else None)
    monkeypatch.setattr(
        c, "_listing_pagine",
        lambda url, extraire: [o for _, o in extraire(url, PAGE)])
    return c


def _retenus(c):
    return {d.url: d.date for d in c.catalogue_pv("epci")}


def test_date_lue_dans_le_texte_du_lien(connecteur):
    assert _retenus(connecteur)[PDF + "deliberations-visees-2810.pdf"] == "2026-10-28"


def test_lien_sans_date_ignore(connecteur):
    assert PDF + "piece-sans-date.pdf" not in _retenus(connecteur)


def test_la_date_de_l_adresse_gagne(connecteur):
    assert _retenus(connecteur)[PDF + "pv-du-05-fevrier-2026.pdf"] == "2026-02-05"


def test_texte_sans_annee_ignore(connecteur):
    assert PDF + "ordre-2810.pdf" not in _retenus(connecteur)


def test_balise_interne_dans_l_ancre(connecteur):
    assert _retenus(connecteur)[PDF + "convoc.pdf"] == "2026-10-28"
    assert _ancres('<a href="x"><span>Délibérations du</span>\n  28 octobre&nbsp;2026</a>'
                   ) == [("x", "Délibérations du 28 octobre 2026")]


def test_hors_base_ignore(connecteur):
    assert "https://autre.invalid/x.pdf" not in _retenus(connecteur)


def test_seconde_collecte_ne_duplique_pas_la_seance(base):
    """Import manuel du 28/10, puis la collecte reprend la même pièce."""
    doc = DocumentPublie(date="2026-10-28", url=PDF + "deliberations-visees-2810.pdf",
                         libelle="deliberations visees 2810", source="cc.exemple.invalid")
    a = conseils.enregistrer_seance(base, doc, "epci")
    b = conseils.enregistrer_seance(base, doc, "epci")
    assert a == b
    assert base.execute("SELECT COUNT(*) FROM events WHERE type='conseil_communautaire'"
                        ).fetchone()[0] == 1
