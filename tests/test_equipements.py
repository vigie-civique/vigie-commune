"""Base permanente des équipements : les trois niveaux, et le secret statistique.

Aucun appel réseau. Les observations reproduites ont la forme exacte que Melodi
a rendue le 02/09/2026 sur `DS_BPE` et `DS_BPE_EVOLUTION`.

Ce que ces essais protègent :
  1. une observation de la BPE porte À LA FOIS un domaine, un sous-domaine et un
     type — les compter tous comme des équipements les compterait trois fois ;
  2. une valeur retenue au titre du secret statistique arrive vide : la lire
     comme un zéro ferait disparaître un équipement qui existe.
"""
from __future__ import annotations

from collectors.equipements import _valeur, classer


def _obs(dom="_T", sdom="_T", typ="_T", valeur=1.0):
    mesure = {"value": valeur} if valeur is not None else {}
    return {"dimensions": {"GEO": "2026-COM-30140", "BPE_MEASURE": "FACILITIES",
                           "FACILITY_DOM": dom, "FACILITY_SDOM": sdom,
                           "FACILITY_TYPE": typ, "TIME_PERIOD": "2025",
                           "UNIT_MEASURE": "NR"},
            "measures": {"OBS_VALUE_NIVEAU": mesure}}


class TestClassement:
    def test_le_type_est_le_niveau_le_plus_fin(self):
        assert classer(_obs("A", "A1", "A129")["dimensions"]) == ("type", "A129")

    def test_un_total_de_sous_domaine_est_reconnu(self):
        assert classer(_obs("A", "A1", "_T")["dimensions"]) == ("sous_domaine", "A1")

    def test_un_total_de_domaine_est_reconnu(self):
        assert classer(_obs("A", "_T", "_T")["dimensions"]) == ("domaine", "A")

    def test_le_total_general_est_reconnu(self):
        assert classer(_obs()["dimensions"]) == ("total", "_T")

    def test_l_evolution_n_a_pas_de_domaine(self):
        """`DS_BPE_EVOLUTION` ne porte que le type : pas de dimension de domaine.

        Sans ce cas, une observation d'évolution serait rangée en « total » et
        toute la série se serait écrasée sur une seule ligne.
        """
        dims = {"GEO": "2026-EPCI-200034601", "BPE_MEASURE": "FACILITIES",
                "FACILITY_TYPE": "A206", "TIME_PERIOD": "2015"}
        assert classer(dims) == ("type", "A206")


class TestValeur:
    def test_une_valeur_sous_secret_n_est_pas_un_zero(self):
        assert _valeur(_obs("A", "A1", "A129", valeur=None)) is None

    def test_une_valeur_publiee_est_lue(self):
        assert _valeur(_obs("A", "A1", "A129", valeur=6.0)) == 6.0

    def test_un_zero_publie_reste_un_zero(self):
        """L'INSEE publie parfois 0 : c'est une mesure, pas une absence."""
        assert _valeur(_obs("A", "A1", "A129", valeur=0.0)) == 0.0


# ── Melodi par lots de communes (31/10/2026) ─────────────────────────────────

def test_un_lot_de_communes_tient_en_une_requete(monkeypatch):
    import urllib.parse
    from collectors import equipements as eq
    appels = []

    def melodi(url, **_):
        geo = [v for k, v in urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query) if k == "GEO"]
        appels.append(geo)
        return {"observations": [{"dimensions": {"GEO": f"2025-{g}"}} for g in geo if g != "COM-30003"]}
    monkeypatch.setattr(eq, "fetch_json", melodi)
    par_commune = eq.observations_par_commune(["30001", "2A004", "30003"])
    assert appels == [["COM-30001", "COM-2A004", "COM-30003"]]
    assert sorted(par_commune) == ["2A004", "30001"]


def test_une_reponse_au_plafond_est_refusee(monkeypatch):
    """Au plafond, Melodi coupe sans annoncer de page suivante."""
    import pytest
    from collectors import equipements as eq
    monkeypatch.setattr(eq, "fetch_json", lambda url, **_: {"observations": [{}] * 3})
    with pytest.raises(RuntimeError, match="tronquée"):
        eq._observations("DS_BPE", ["COM-30001"], maxi=3)
