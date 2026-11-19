"""La note de Gemma sur un site dit quel modèle l'a écrite (revue du 07/11/2026, C11)."""
from __future__ import annotations

from collectors import web_scraper


def _resultat(eid: int) -> dict:
    return {"entity_id": eid, "entity_name": "Association d'épreuve", "url": "https://asso.example",
            "page_title": "Accueil", "contact": {}, "gemma_note": "Association sportive."}


def test_la_note_porte_le_modele_et_son_empreinte(base, entite, monkeypatch):
    eid = entite("Association d'épreuve", "association")
    monkeypatch.setattr(web_scraper, "_empreinte_modele", lambda: "a1b2c3d4e5f6")
    web_scraper.update_entity_from_scrape(base, _resultat(eid))
    source, confiance = base.execute(
        "SELECT source, confidence FROM entity_notes WHERE entity_id=?", (eid,)).fetchone()
    assert source == f"gemma_web:{web_scraper.MODELE_WEB}@a1b2c3d4e5f6"
    assert confiance == "unverified"


def test_sans_empreinte_la_note_garde_le_nom_seul(base, entite, monkeypatch):
    eid = entite("Association d'épreuve", "association")
    monkeypatch.setattr(web_scraper, "_empreinte_modele", lambda: None)
    web_scraper.update_entity_from_scrape(base, _resultat(eid))
    assert base.execute("SELECT source FROM entity_notes WHERE entity_id=?",
                        (eid,)).fetchone()[0] == f"gemma_web:{web_scraper.MODELE_WEB}"
