"""Sitadel : une colonne qui disparaît de la source arrête le relevé.

DIDO ne publie pas les mêmes colonnes dans ses deux fichiers : le fichier des
locaux non résidentiels n'a aucune colonne de logement, et c'est prévu
(`ABSENTES_PREVUES`). Jusqu'au 06/11/2026, TOUTE absence était imprimée puis
ignorée : un renommage au SDES aurait importé des autorisations amputées d'une
colonne, sans erreur. Chantier « sentinelle de forme des sources », lot 0.
"""
from __future__ import annotations

import io
import json
import sqlite3
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

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
        monkeypatch.setattr(sitadel, "datafile_columns", lambda rid, millesime=None: colonnes)
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


FICHES = Path(__file__).parent / "fixtures" / "sitadel_locaux"


def _meta(monkeypatch, fiches: dict):
    """DIDO factice : `fiches[millesime]` est la fiche (None = courant) ou une exception."""
    def lire(rid, millesime=None):
        f = fiches[millesime]
        if isinstance(f, Exception):
            raise f
        return f
    monkeypatch.setattr(sitadel, "_datafile_meta", lire)


def _fiche_2026_10():
    """En-tête réel du millésime 2026-10 (relevé DIDO du 25/11/2026), colonnes réduites."""
    return json.loads((FICHES / "fiche_2026-10.json").read_text())


EMBARGO = {"millesime": "2026-11", "published": "2026-11-27T07:45:00.000Z"}
AVANT = datetime(2026, 11, 25, tzinfo=timezone.utc)
APRES = datetime(2026, 11, 28, tzinfo=timezone.utc)


def _404():
    return urllib.error.HTTPError("u", 404, "Not Found", {}, None)


def test_millesime_courant_non_diffuse_on_lit_le_precedent(monkeypatch):
    """Constat du 25/11/2026 : la fiche 2026-11 n'a pas `columns` (embargo au 27/11)."""
    _meta(monkeypatch, {None: EMBARGO, "2026-10": _fiche_2026_10()})
    assert "CJ_DEM" in sitadel.datafile_columns("rid", "2026-10")
    assert sitadel.resolve_millesime("rid", AVANT) == "2026-10"


def test_published_sans_fuseau_est_lu_en_utc(monkeypatch):
    sans_fuseau = {"millesime": "2026-11", "published": "2026-11-27T07:45:00"}
    _meta(monkeypatch, {None: sans_fuseau, "2026-10": _fiche_2026_10()})
    assert sitadel.resolve_millesime("rid", AVANT) == "2026-10"
    assert sitadel.resolve_millesime("rid", APRES) is None


def test_fiche_sans_colonnes_deja_publiee_n_est_pas_un_repli(monkeypatch):
    """Sans embargo, l'absence de colonnes est une dérive : la garde doit refuser."""
    _meta(monkeypatch, {None: EMBARGO})
    assert sitadel.resolve_millesime("rid", APRES) is None


def test_millesime_courant_publie_reste_le_courant(monkeypatch):
    _meta(monkeypatch, {None: _fiche_2026_10()})
    assert sitadel.resolve_millesime("rid", AVANT) is None


def test_changement_d_annee(monkeypatch):
    _meta(monkeypatch, {
        None: {"millesime": "2027-01", "published": "2027-01-27T07:45:00.000Z"},
        "2026-12": _fiche_2026_10()})
    assert sitadel.resolve_millesime("rid", AVANT) == "2026-12"


def test_un_seul_mois_en_arriere_puis_message_clair(monkeypatch):
    _meta(monkeypatch, {None: EMBARGO, "2026-10": _404()})
    with pytest.raises(RuntimeError, match="2026-10 introuvable.*404"):
        sitadel.resolve_millesime("rid", AVANT)


def test_embargo_et_mois_precedent_en_404_arrete_run(monkeypatch):
    """Bout en bout : embargo + précédent absent → `run` s'arrête, rien n'est importé."""
    reel = sitadel.resolve_millesime
    _meta(monkeypatch, {None: EMBARGO, "2026-10": _404()})
    monkeypatch.setattr(sitadel, "resolve_datafile", lambda titre: ("rid", "2026-10-28"))
    monkeypatch.setattr(sitadel, "resolve_millesime", lambda rid: reel(rid, AVANT))
    monkeypatch.setattr(sitadel, "get_conn", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(sitadel, "croisement_cadastre", lambda c: [])
    importe = []
    monkeypatch.setattr(sitadel, "import_rows", lambda *a: importe.append(a))
    with pytest.raises(RuntimeError, match="relevés en échec"):
        sitadel.run(["30140"])
    assert importe == []


def test_pendant_un_repli_la_publication_n_est_pas_affirmee(monkeypatch):
    reel = sitadel.resolve_millesime
    _meta(monkeypatch, {None: EMBARGO, "2026-10": _fiche_2026_10()})
    monkeypatch.setattr(sitadel, "resolve_datafile", lambda titre: ("rid", "2026-10-28"))
    monkeypatch.setattr(sitadel, "resolve_millesime", lambda rid: reel(rid, AVANT))
    monkeypatch.setattr(sitadel, "get_conn", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(sitadel, "croisement_cadastre", lambda c: [])
    lus = []
    monkeypatch.setattr(sitadel, "fetch_rows",
                        lambda rid, cibles, cat, mil=None: lus.append(mil) or [])
    monkeypatch.setattr(sitadel, "import_rows", lambda *a: {
        "lignes": 0, "events": 0, "lies_par_siren": 0, "siren_inconnus": set()})
    total = sitadel.run(["30140"])
    assert lus == ["2026-10", "2026-10"]
    assert total["locaux"]["publication"] is None


def test_le_millesime_est_demande_et_cj_dem_est_lu(monkeypatch):
    """Le CSV est demandé au millésime lu, avec CJ_DEM dans les colonnes."""
    vues = []
    monkeypatch.setattr(sitadel, "datafile_columns",
                        lambda rid, millesime=None: [c for c in sitadel.COLONNES
                                                     if c not in sitadel.ABSENTES_PREVUES["locaux"]])
    monkeypatch.setattr(sitadel, "archive_fetch", lambda *a, **k: None)

    def urlopen(req, timeout=None):
        vues.append(req.full_url)
        return _Reponse(b'COMM;NUM_DAU;CJ_DEM\n30140;A1;1000\n')
    monkeypatch.setattr(sitadel.urllib.request, "urlopen", urlopen)
    rows = sitadel.fetch_rows("rid", ["30140"], "locaux", "2026-10")
    assert "millesime=2026-10" in vues[0] and "CJ_DEM" in vues[0]
    assert rows[0]["CJ_DEM"] == "1000"


def test_la_garde_refuse_un_fichier_sans_aucune_colonne(source):
    """Le schéma constaté le 25/11 (fiche sans colonnes) reste refusé."""
    source([])
    with pytest.raises(RuntimeError, match="CJ_DEM"):
        sitadel.fetch_rows("rid", ["30140"], "locaux")


def test_fiche_reelle_2026_10_passe_la_garde_et_cj_dem_est_lu(monkeypatch):
    """Les 141 colonnes réelles de 2026-10 : la garde passe, CJ_DEM est demandé et lu."""
    fiche = _fiche_2026_10()
    noms = [c["name"] for c in fiche["columns"]]
    assert len(noms) == 141
    _meta(monkeypatch, {"2026-10": fiche})
    monkeypatch.setattr(sitadel, "archive_fetch", lambda *a, **k: None)
    demandees = [c for c in sitadel.COLONNES if c in noms]
    # En-tête et ligne du CSV d'essai : tirés de la fixture, pas de COLONNES.
    entete = ["COMM", "NUM_DAU", "CJ_DEM", "DENOM_DEM"]
    assert set(entete) <= set(noms)
    vues = []

    def urlopen(req, timeout=None):
        vues.append(req.full_url)
        return _Reponse((";".join(entete) + "\n30140;A1;5499;ACME\n").encode())
    monkeypatch.setattr(sitadel.urllib.request, "urlopen", urlopen)
    rows = sitadel.fetch_rows("rid", ["30140"], "locaux", "2026-10")
    assert "CJ_DEM" in demandees and "CJ_DEM" in vues[0]
    assert rows[0]["CJ_DEM"] == "5499"
