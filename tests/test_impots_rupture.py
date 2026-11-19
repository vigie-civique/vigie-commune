"""Rupture de 2021 sur la taxe foncière bâtie (part du département passée à la
commune) : écart corrigé, jamais l'écart brut. Module JS pur, exécuté par node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / "public/src/routes/impots/rupture.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


def js(expr: str):
    code = f"import * as m from {json.dumps(MODULE.as_uri())}; console.log(JSON.stringify({expr}))"
    out = subprocess.run(["node", "--input-type=module", "-e", code],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def serie(*paires):
    return [{"x": a, "y": y} for a, y in paires]


def tuile(points, dep):
    return js(f"m.evolutionTuile({json.dumps(points)}, {json.dumps(dep)})")


def page(points, dep):
    return js(f"m.evolutionPage({json.dumps(points)}, {json.dumps(dep)})")


# Commune à 20,00 % + département 15,50 % en 2020 : 35,50 % en 2021, payé inchangé.
NUL = serie((2019, 20.0), (2020, 20.0), (2021, 35.5), (2022, 35.5))


def test_ecart_corrige_nul_taux_dit_inchange():
    t = tuile(NUL, 15.5)
    assert "relevé" not in t and "+" not in t
    assert t.startswith("inchangé hors transfert de 2019 à 2022")
    assert "la part du département est passée à la commune" in t and "taux cumulé commune et département inchangé" in t
    assert "—" not in t


def test_ecart_corrige_non_nul_releve_de_l_ecart_corrige():
    # 20 + 15,5 = 35,5 ; voté 36,5 en 2021 : +1 point, pas +16,5.
    t = tuile(serie((2020, 20.0), (2021, 36.5), (2022, 36.5)), 15.5)
    assert "relevé en 2021 (+1,00 point" in t
    assert "16,50" not in t and "hors transfert" in t
    b = tuile(serie((2020, 20.0), (2021, 34.5), (2022, 34.5)), 15.5)
    assert "baissé en 2021 (−1,00 point" in b


def test_arrondi_affiche_ecart_nul():
    # 35,504 contre 35,5 : nul au centième affiché.
    assert tuile(serie((2020, 20.0), (2021, 35.504)), 15.5).startswith("inchangé hors transfert")


def test_dep_absent_rupture_dite_sans_chiffre():
    t = tuile(NUL, None)
    assert "écart non chiffré" in t and "part du département" in t
    assert "taux transféré non recoupé" in t
    assert "relevé" not in t and "+" not in t
    p = page(NUL, None)
    assert p == {"pct": None, "rupture": "non-chiffree", "debut": 2019}


def test_serie_sans_2020_pas_de_correction_inventee():
    # 2019 puis 2021 : la rupture est traversée mais 2020 manque, rien à corriger.
    assert "non chiffré" in tuile(serie((2019, 20.0), (2021, 35.5)), 15.5)


def test_serie_sans_rupture_ecart_brut_inchange():
    # Série entièrement après 2021 ou avant : écart brut, rupture non mentionnée.
    t = tuile(serie((2021, 35.5), (2022, 35.5), (2023, 36.5)), 15.5)
    assert t == "relevé en 2023 (+1,00 point)"
    assert tuile(serie((2017, 20.0), (2018, 21.0), (2019, 21.0)), None) == "relevé en 2018 (+1,00 point)"
    assert tuile(serie((2022, 30.0), (2023, 30.0)), None) == "inchangé de 2022 à 2023"
    assert page(serie((2022, 30.0), (2023, 33.0)), None)["rupture"] is None


def test_palier_apres_rupture_ecart_brut():
    # Relevé en 2023, après la rupture : brut, sans mention.
    assert tuile(serie((2020, 20.0), (2021, 35.5), (2022, 35.5), (2023, 37.0)), 15.5) == "relevé en 2023 (+1,50 point)"


def test_page_pourcentage_sur_base_comparable():
    p = page(serie((2019, 20.0), (2022, 35.5)), 15.5)  # sans 2020 -> non chiffrable
    assert p["pct"] is None
    p = page(serie((2020, 20.0), (2021, 35.5), (2022, 35.5)), 15.5)
    assert p["pct"] == pytest.approx(0.0) and p["rupture"] == "corrigee"
    p = page(serie((2020, 20.0), (2022, 39.05)), 15.5)  # 2022 sans 2021 : non chiffrable
    assert p["pct"] is None
    p = page(serie((2020, 20.0), (2021, 35.5), (2022, 39.05)), 15.5)
    assert p["pct"] == pytest.approx(10.0)


def test_avant_2021_est_fusionne_avec_le_nom_de_commune():
    d = {"taux": [{"insee": "99001", "commune": "Testonville", "annee": 2021, "indicateur": "TFB_VOTE", "taux": 35.5}],
         "avant_2021": [{"insee": "99001", "annee": 2020, "indicateur": "TFB_DEP", "taux": 15.5},
                        {"insee": "99009", "annee": 2020, "indicateur": "TFB_VOTE", "taux": 1.0}]}
    out = js(f"m.tauxAvecAvant2021({json.dumps(d)})")
    assert len(out) == 2 and out[1]["commune"] == "Testonville" and out[1]["indicateur"] == "TFB_DEP"
    # clé absente ou vide : `taux` tel quel
    assert js(f"m.tauxAvecAvant2021({json.dumps({'taux': d['taux']})})") == d["taux"]
    assert js("m.tauxAvecAvant2021({})") == []


def test_palier_ne_avant_2021_traverse_la_rupture_sans_etre_corrige():
    # Brassac (valeurs du tableau de #246) : 15,09 -> 15,70 jusqu'en 2020, 45,61 dès 2021 (= 15,70 + 29,91).
    b = serie((2015, 15.09), (2016, 15.24), (2017, 15.39), (2018, 15.54), (2019, 15.70), (2020, 15.70),
              (2021, 45.61), (2022, 45.61))
    t = tuile(b, 29.91)
    assert t.startswith("relevé en 2019 (+0,16 point)")
    assert "hors transfert" not in t and "taux cumulé commune et département inchangé" in t
    assert page(b, 29.91)["pct"] == pytest.approx((45.61 - (15.09 + 29.91)) / (15.09 + 29.91) * 100)
    # Lasalle : 18,22 partout, 42,87 dès 2021 (= 18,22 + 24,65), Gard.
    assert tuile(serie(*[(a, 18.22) for a in range(2015, 2021)], (2021, 42.87), (2022, 42.87)), 24.65).startswith("inchangé hors transfert de 2015 à 2022")


def test_courbe_de_la_tuile_ne_trace_que_depuis_2021():
    pts = serie((2019, 18.22), (2020, 18.22), (2021, 42.87), (2022, 42.87), (2023, 44.16))
    assert [p["x"] for p in js(f"m.pointsATracer({json.dumps(pts)})")] == [2021, 2022, 2023]
    avant = serie((2018, 15.0), (2019, 15.5))
    assert js(f"m.pointsATracer({json.dumps(avant)})") == avant


def test_page_dit_depuis_quand_court_la_serie():
    assert page(serie((2021, 30.0), (2024, 33.0)), None)["debut"] == 2021
