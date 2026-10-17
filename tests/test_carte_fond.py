"""L'emprise du fond de carte : ce qu'elle doit couvrir.

Elle se déduit des calques de /carte. Les mutations DVF y sont entrées le
30/08/2026, quand /urbanisme passait sur le fond local, et en sont sorties le
17/10/2026 : DVF ne publie plus que des agrégats, sans coordonnées.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def fond():
    spec = importlib.util.spec_from_file_location(
        "carte_fond", ROOT / "scripts" / "carte_fond.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot(fond, tmp_path, monkeypatch):
    """Un répertoire `data` comme en écrit `build_public_snapshot.py`."""
    def _ecrire(calques: list[tuple[float, float]]):
        donnees = tmp_path / "data"
        (donnees / "layers").mkdir(parents=True)
        (donnees / "layers" / "places.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {}} for lat, lon in calques],
        }), encoding="utf-8")
        monkeypatch.setattr(fond, "DONNEES", donnees)
        monkeypatch.setattr(fond, "LAYERS", donnees / "layers")
        return donnees
    return _ecrire


def test_l_emprise_couvre_les_calques(fond, snapshot):
    snapshot(calques=[(44.00, 3.80), (44.05, 3.85)])
    lat_min, _, lat_max, _ = fond.emprise_du_snapshot()
    assert lat_min < 44.00 and lat_max > 44.05


def test_un_dvf_publie_ne_compte_plus_dans_l_emprise(fond, snapshot):
    """Un `dvf.json` resté d'un ancien snapshot n'élargit rien : aucune page ne
    dessine plus de mutation."""
    donnees = snapshot(calques=[(44.00, 3.80), (44.05, 3.85)])
    (donnees / "dvf.json").write_text(
        json.dumps({"dvf": [{"lat": 44.30, "lng": 3.82}]}), encoding="utf-8")
    assert len(fond.points_cartographies()) == 2


def test_un_snapshot_vide_refuse_de_conclure(fond, snapshot):
    """Aucun point du tout : construire un fond « du monde entier » ferait un
    fichier de plusieurs gigaoctets. On s'arrête, on le dit."""
    snapshot(calques=[])
    with pytest.raises(SystemExit):
        fond.emprise_du_snapshot()
