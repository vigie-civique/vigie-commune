"""L'avis d'un marché, sur une base dont `marches_publics` n'a pas `confidence`.

Point 3 du brief « avis-marche-apres-revue » : sans la colonne, la confiance ne
filtre rien (comme `etape_finances`), mais un marché ÉCARTÉ à l'atelier retire
quand même son avis. L'ancien filtre SQL n'existait pas sans la colonne : l'avis
d'un marché écarté sortait.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_reference import ROOT, construire  # noqa: E402


def _sans_confiance(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE marches_publics DROP COLUMN confidence")
    for titre, verdict in (("Avis PIEGE_AVIS_SANS_CONFIANCE_ECARTE", "ecarte"),
                           ("Avis TEMOIN_AVIS_SANS_CONFIANCE", None)):
        acte = conn.execute("INSERT INTO events (type, date, title, source) VALUES"
                            " ('marché_public', '2026-03-09', ?, 'BOAMP')", (titre,)).lastrowid
        mid = conn.execute(
            "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, objet, montant,"
            " date_notif, source, event_id) VALUES ('219900010', 'Testonville', ?, 2950,"
            " '2026-03-09', 'BOAMP', ?)", (f"Marché de {titre}", acte)).lastrowid
        if verdict:
            conn.execute("INSERT INTO annotations (object_type, object_id, review_status)"
                         " VALUES ('marche', ?, ?)", (mid, verdict))
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    return construire(ROOT, tmp_path_factory.mktemp("sans_confiance"),
                      apres_amorcage=_sans_confiance)


def test_l_avis_d_un_marche_ecarte_ne_sort_pas_sans_colonne_confidence(snapshot):
    titres = [e["title"] for e in json.loads(
        (snapshot / "events.json").read_text(encoding="utf-8"))["events"]]
    assert "Avis TEMOIN_AVIS_SANS_CONFIANCE" in titres, "avis sans verdict absent"
    assert "Avis PIEGE_AVIS_SANS_CONFIANCE_ECARTE" not in titres, "avis d'un marché écarté publié"
