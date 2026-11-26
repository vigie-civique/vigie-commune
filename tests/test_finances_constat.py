"""Le constat de /finances ne compte plus les flux sans nom comme un bénéficiaire.

Base synthétique (scripts/donnees_synthetiques.py), dont une part des flux est
déliée comme le fait le filtre d'EI (`to_name` nul). Le constat JS est comparé à
un second chemin : un recompte en Python, sans la même structure.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSTAT = ROOT / "public" / "src" / "routes" / "finances" / "constat.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


def _constat(flows):
    code = ("import('%s').then(m=>{const f=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "console.log(JSON.stringify(m.constat(f)))})" % CONSTAT.as_uri())
    r = subprocess.run(["node", "-e", code], input=json.dumps(flows),
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def flux(tmp_path_factory):
    spec = importlib.util.spec_from_file_location(
        "synth", ROOT / "scripts" / "donnees_synthetiques.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = tmp_path_factory.mktemp("synth") / "synthetique.db"
    import sys
    argv = sys.argv
    sys.argv = ["donnees_synthetiques.py", str(db)]
    try:
        mod.main()
    finally:
        sys.argv = argv
    conn = sqlite3.connect(db)
    lignes = conn.execute(
        "SELECT f.type, f.year, f.amount, e.name FROM financial_flows f "
        "JOIN entities e ON e.id = f.to_id ORDER BY f.id").fetchall()
    conn.close()
    out = []
    for i, (typ, an, montant, nom) in enumerate(lignes):
        # un flux sur trois sort délié, comme après moteur#317
        out.append({"sens": "sortant", "type": typ, "year": an, "amount": montant,
                    "to_name": None if i % 3 == 0 else nom, "etat": "vote"})
    return out


def _recompte(flux):
    nommes = {}
    nn_nb = nn_tot = 0
    for f in flux:
        if f["to_name"]:
            nommes[f["to_name"]] = nommes.get(f["to_name"], 0) + f["amount"]
        else:
            nn_nb += 1
            nn_tot += f["amount"]
    montants = sorted(nommes.values(), reverse=True)
    seuil = 0.8 * sum(montants)
    cumul = 0
    n80 = 0
    for m in montants:
        cumul += m
        n80 += 1
        if cumul >= seuil:
            break
    return len(nommes), n80, nn_nb, nn_tot


def test_flux_sans_nom_hors_du_nombre_de_beneficiaires(flux):
    c = _constat(flux)
    benef, n80, nn_nb, nn_tot = _recompte(flux)
    assert nn_nb > 0
    assert c["beneficiaires"] == benef
    assert c["pour80"] == n80
    assert c["nonNommes"]["nb"] == nn_nb
    assert c["nonNommes"]["total"] == pytest.approx(nn_tot)
    assert c["nb"] == len(flux)
    assert c["totalNommes"] + nn_tot == pytest.approx(c["total"])
    assert c["total"] == pytest.approx(sum(f["amount"] for f in flux))


def test_noms_vides_equivalents_a_nul():
    # même règle que beneficiaire_inconnu (argent.py) : vide, ?, ∅, espaces seuls
    base = {"sens": "sortant", "type": "subvention", "year": 2024, "amount": 10}
    noms = [None, "", "?", "∅", "   ", "\t\n"]
    c = _constat([{**base, "to_name": n} for n in noms] + [{**base, "to_name": "Réel"}])
    assert c["beneficiaires"] == 1 and c["pour80"] == 1
    assert c["nonNommes"] == {"nb": len(noms), "total": 10 * len(noms)}
    assert c["totalNommes"] == 10


def test_que_des_flux_sans_nom():
    c = _constat([{"sens": "sortant", "type": "marché", "year": 2024, "amount": 100,
                   "to_name": None}])
    assert c["beneficiaires"] == 0 and c["pour80"] == 0
    assert c["nonNommes"] == {"nb": 1, "total": 100}


def test_cession_demande_et_annule_exclus():
    base = {"sens": "sortant", "year": 2024, "amount": 50, "to_name": None}
    c = _constat([{**base, "type": "cession_patrimoine"}, {**base, "type": "subvention", "etat": "demande"},
                  {**base, "type": "subvention", "etat": "annule"}, {**base, "type": "subvention"}])
    assert c["nb"] == 1 and c["nonNommes"]["nb"] == 1


def _phrase(flux):
    code = ("import('%s').then(m=>{const f=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "console.log(m.phraseNonNommes(m.constat(f), n=>n+' EUR'))})" % CONSTAT.as_uri())
    r = subprocess.run(["node", "-e", code], input=json.dumps(flux),
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def test_phrase_sans_aucun_beneficiaire_nomme():
    base = {"sens": "sortant", "type": "subvention", "year": 2024, "to_name": None}
    p = _phrase([{**base, "amount": 100}, {**base, "amount": 50}])
    assert p == ("Aucun bénéficiaire nommé : les 150 EUR (2 flux) vont tous à des bénéficiaires "
                 "non nommés (nom non publié sur ce site, ou absent de la source).")
    assert "restants" not in p and "ci-dessus" not in p


def test_phrase_avec_beneficiaires_nommes():
    base = {"sens": "sortant", "type": "subvention", "year": 2024}
    p = _phrase([{**base, "amount": 100, "to_name": "A"}, {**base, "amount": 50, "to_name": None}])
    assert p.startswith("Les 50 EUR restants (1 flux) vont à des bénéficiaires non nommés")
    assert p.endswith("ils ne comptent pas parmi les bénéficiaires ci-dessus.")


def test_phrase_vide_sans_flux_non_nomme():
    assert _phrase([{"sens": "sortant", "type": "subvention", "year": 2024,
                     "amount": 5, "to_name": "A"}]) == ""
