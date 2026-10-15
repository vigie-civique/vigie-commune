"""La base fictive et la sonde d'agrégats — scripts/donnees_synthetiques.py,
scripts/sonde_base.py.

Ce sont les deux outils qui permettent de travailler sur le moteur sans lire
une base réelle (CONTRIBUTING.md, « Travailler sans donnée réelle »). Leurs
garanties sont des refus : on les éprouve en les provoquant.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATEUR = ROOT / "scripts" / "donnees_synthetiques.py"
SONDE = ROOT / "scripts" / "sonde_base.py"


def lancer(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, cwd=ROOT)


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    chemin = tmp_path_factory.mktemp("synth") / "essai-synth.db"
    r = lancer(GENERATEUR, chemin)
    assert r.returncode == 0, r.stderr
    return chemin


def test_la_base_se_declare_fictive_et_n_a_que_des_codes_fictifs(base):
    conn = sqlite3.connect(base)
    assert conn.execute("SELECT avertissement FROM _synthetique").fetchone()[0].startswith("Base FICTIVE")
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE type='person'").fetchone()[0] == 60
    sirens = [s for (s,) in conn.execute("SELECT siren FROM businesses")]
    assert sirens and all(s.startswith("999") for s in sirens)


def test_meme_graine_meme_base(tmp_path, base):
    autre = tmp_path / "autre-synth.db"
    assert lancer(GENERATEUR, autre).returncode == 0
    noms = lambda b: [n for (n,) in sqlite3.connect(b).execute("SELECT name FROM entities ORDER BY id")]
    assert noms(autre) == noms(base)


@pytest.mark.parametrize("nom, refus", [
    ("reel.db", "synth"),                              # une base fictive se reconnaît à son nom
    ("public/static/data/x-synth.db", "zone publique"),
])
def test_le_generateur_refuse(tmp_path, nom, refus):
    r = lancer(GENERATEUR, tmp_path / nom if not nom.startswith("public/") else ROOT / nom)
    assert r.returncode != 0 and refus in r.stderr


def test_il_n_ecrase_pas_une_base_qui_n_est_pas_fictive(tmp_path):
    vraie = tmp_path / "vraie-synth.db"
    sqlite3.connect(vraie).execute("CREATE TABLE entities (id INTEGER)")
    r = lancer(GENERATEUR, vraie, "--force")
    assert r.returncode != 0 and "PAS une base fictive" in r.stderr


def test_la_sonde_compte_et_masque_les_petits_effectifs(base):
    r = lancer(SONDE, "compter", base, "entities", "--par", "type")
    assert r.returncode == 0
    lignes = dict(l.split()[:2] for l in r.stdout.splitlines())
    assert lignes["person"] == "60" and lignes["service"] == "<5"


@pytest.mark.parametrize("args", [
    ("compter", "entities", "--par", "name"),           # regrouper par nom
    ("compter", "entities", "--ou", "name != 'x'"),     # filtrer par nom : N−1 trahit X
    ("compter", "persons", "--ou", "lastname LIKE 'A%'"),
    ("compter", "users", "--ou", "1=1"),                # table de l'atelier
    ("compter", "entities", "--ou", "1=1; DROP TABLE entities"),
])
def test_la_sonde_refuse(base, args):
    commande, table, *reste = args
    r = lancer(SONDE, commande, base, table, *reste)
    assert r.returncode != 0, r.stdout
