"""Les millésimes d'un consolidé DECP se lisent dans ses dates de notification.

Relevé du 07/11/2026 : `decp-2019.json` porte les notifications de 2018 à 2023.
Lu par son titre, il faisait dire à la page que 2020, 2021 et 2023 n'étaient
couverts par aucun fichier, et `--years 2021` ne lisait aucun consolidé.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

pytest.importorskip("ijson", reason="job « tests-deps »")

from collectors import marches_publics as marches  # noqa: E402


def _marche(i, annee, acheteur="99999999900011"):
    return {"id": f"{annee}-{i}", "acheteur": {"id": acheteur, "nom": "X"},
            "dateNotification": f"{annee}-06-01", "montant": 1}


# 300 marchés : 2018-2023 en nombre, une date aberrante et un marché ancien
# isolés (moins d'un sur cent), deux marchés de la commune en 2021 et 2023.
HISTORIQUE = {"marches": (
    [_marche(i, a) for a in range(2018, 2024) for i in range(49)]
    + [_marche(0, "0001"), _marche(0, 2017)]
    + [_marche(1, 2021, "21300140700011"), _marche(1, 2023, "21300140700011")]
)}


@pytest.fixture
def magasin(tmp_path, monkeypatch):
    cache = tmp_path / "decp"
    cache.mkdir()
    (cache / "decp-2019.json").write_text(json.dumps(HISTORIQUE), encoding="utf-8")
    monkeypatch.setattr(marches, "DECP_CACHE", cache)
    monkeypatch.setattr(marches, "COMMUNE_SIRENS", ["213001407"])
    monkeypatch.setattr(marches, "COMMUNES_ABSORBEES", {})
    monkeypatch.setattr(marches, "CAC_SIREN", "")
    monkeypatch.setattr(marches, "ACHETEUR_JETONS", ())
    monkeypatch.setattr(marches, "_index_ouvert", {})
    monkeypatch.setattr(marches, "REQUEST_DELAY", 0)
    monkeypatch.setattr(marches, "discover_decp_resources",
                        lambda: [{"title": "decp-2019.json", "url": "https://example.test/x"}])
    monkeypatch.delenv("VIGIE_ECRIVAIN_MAGASIN", raising=False)
    return cache


def test_les_millesimes_viennent_des_notifications_pas_du_titre(magasin):
    marches.fetch_decp_marches()
    assert marches.millesimes_lus == {a: 1 for a in range(2018, 2024)}


def test_l_index_rend_les_memes_millesimes_que_la_lecture_en_flux(magasin):
    en_flux = Counter()
    marches.extract_marches_from_file("https://example.test/x", "decp-2019.json", en_flux)
    assert marches.remplir_magasin() == []
    indexe = Counter()
    assert marches.marches_indexes("decp-2019.json", indexe) is not None
    assert indexe == en_flux
    marches.fetch_decp_marches()
    assert marches.millesimes_lus == {a: 1 for a in range(2018, 2024)}


def test_years_borne_les_notifications_et_non_les_fichiers(magasin):
    trouves = marches.fetch_decp_marches(years=[2021])
    assert [m["raw_id"] for m in trouves] == ["2021-1"]


def test_un_fichier_sans_marche_ne_couvre_rien():
    assert marches.millesimes_couverts(Counter()) == set()
