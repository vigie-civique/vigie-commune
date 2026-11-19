"""Sitadel : une colonne qui disparaît de la source arrête le relevé.

DIDO ne publie pas les mêmes colonnes dans ses deux fichiers : le fichier des
locaux non résidentiels n'a aucune colonne de logement, et c'est prévu
(`ABSENTES_PREVUES`). Jusqu'au 06/11/2026, TOUTE absence était imprimée puis
ignorée : un renommage au SDES aurait importé des autorisations amputées d'une
colonne, sans erreur. Chantier « sentinelle de forme des sources », lot 0.
"""
from __future__ import annotations

import io

import pytest

from collectors import urbanisme_sitadel as sitadel


class _Reponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def source(monkeypatch):
    """Une source DIDO factice : on choisit les colonnes qu'elle déclare."""
    def poser(colonnes: list[str]):
        monkeypatch.setattr(sitadel, "datafile_columns", lambda rid: colonnes)
        monkeypatch.setattr(sitadel, "archive_fetch", lambda *a, **k: None)
        entete = ";".join(c for c in sitadel.COLONNES if c in colonnes)
        monkeypatch.setattr(sitadel.urllib.request, "urlopen",
                            lambda req, timeout=None: _Reponse(f"{entete}\n".encode()))
    return poser


def test_les_absences_prevues_des_locaux_passent(source):
    prevues = sitadel.ABSENTES_PREVUES["locaux"]
    source([c for c in sitadel.COLONNES if c not in prevues])
    assert sitadel.fetch_rows("rid", ["30140"], "locaux") == []


def test_une_colonne_disparue_arrete_le_releve(source):
    source([c for c in sitadel.COLONNES if c != "SIREN_DEM"])
    with pytest.raises(RuntimeError, match="SIREN_DEM"):
        sitadel.fetch_rows("rid", ["30140"], "logements")


def test_une_absence_prevue_ailleurs_ne_vaut_pas_pour_les_logements(source):
    """Les logements sans `NB_LGT_TOT_CREES`, c'est la donnée qui manque."""
    source([c for c in sitadel.COLONNES if c != "NB_LGT_TOT_CREES"])
    with pytest.raises(RuntimeError, match="NB_LGT_TOT_CREES"):
        sitadel.fetch_rows("rid", ["30140"], "logements")


def test_les_absences_prevues_sont_des_colonnes_demandees():
    """Une absence déclarée pour une colonne qu'on ne demande plus est morte."""
    for categorie, absentes in sitadel.ABSENTES_PREVUES.items():
        assert categorie in sitadel.RESSOURCES.values()
        assert absentes <= set(sitadel.COLONNES), absentes - set(sitadel.COLONNES)
