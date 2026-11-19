"""Melodi interrogé par lots de communes : une requête par jeu et par lot.

Le 31/10/2026, `insee` posait 5 requêtes par commune de l'EPCI et attendait
jusqu'à 470 s sa place sous le quota de 30 par minute."""
from __future__ import annotations

import sqlite3
import urllib.parse

from collectors import insee_social as ins


def _obs(insee: str, valeur: float) -> dict:
    return {"dimensions": {"GEO": f"2025-COM-{insee}", "TIME_PERIOD": "2022",
                           "POPREF_MEASURE": "PMUN"},
            "measures": {"OBS_VALUE_NIVEAU": {"value": valeur}}}


def test_une_requete_par_jeu_et_par_lot(tmp_path, monkeypatch):
    chemin = tmp_path / "b.db"
    monkeypatch.setattr(ins, "get_conn", lambda **_: sqlite3.connect(chemin))
    monkeypatch.setattr(ins, "REQUEST_DELAY", 0)
    monkeypatch.setattr(ins, "LOT", 2)
    appels = []

    def melodi(url, **_):
        geo = [v.removeprefix("COM-") for k, v in
               urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query) if k == "GEO"]
        appels.append(geo)
        # 30003 n'a aucune observation (secret statistique, par exemple).
        return {"observations": [_obs(c, 100 + i) for i, c in enumerate(geo) if c != "30003"]}
    monkeypatch.setattr(ins, "fetch_json", melodi)

    ins.run(["30001", "30002", "30003"], ["DS_POPULATIONS_REFERENCE"])
    assert appels == [["30001", "30002"], ["30003"]]
    lignes = sqlite3.connect(chemin).execute(
        "SELECT insee, valeur FROM insee_indicateurs ORDER BY insee").fetchall()
    assert lignes == [("30001", 100.0), ("30002", 101.0)]


def test_le_code_se_lit_avec_ou_sans_millesime():
    assert ins.GEO_COMMUNE.search("2025-COM-2A004").group(1) == "2A004"
    assert ins.GEO_COMMUNE.search("COM-30140").group(1) == "30140"
    assert ins.GEO_COMMUNE.search("2025-DEP-30") is None
