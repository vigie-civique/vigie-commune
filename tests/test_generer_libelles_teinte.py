"""La teinte de la commune : clé facultative de `config/instance.json`.

Liste fermée de huit (collectors/config.py), pétrole par défaut, valeur
inconnue refusée avec un message clair, émise dans `instance.js`.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from collectors import config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def gl():
    spec = importlib.util.spec_from_file_location(
        "generer_libelles", ROOT / "scripts" / "generer_libelles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _instance(tmp_path, **cles):
    inst = json.loads(Path(config.INSTANCE).read_text(encoding="utf-8"))
    inst.pop("teinte", None)
    inst.update(cles)
    chemin = tmp_path / "instance.json"
    chemin.write_text(json.dumps(inst, ensure_ascii=False), encoding="utf-8")
    return chemin


def test_la_liste_est_fermee_a_huit():
    assert config.TEINTES == ("petrole", "prusse", "indigo", "violet",
                              "prune", "mure", "framboise", "mousse")
    assert config.TEINTE_DEFAUT == "petrole"


def test_sans_cle_la_teinte_est_petrole():
    assert config.teinte_valide(None) == "petrole"


@pytest.mark.parametrize("teinte", config.TEINTES)
def test_une_teinte_de_la_liste_est_gardee(teinte):
    assert config.teinte_valide(teinte) == teinte


@pytest.mark.parametrize("valeur", ["rouge", "Petrole", "#1f6683", "", 3])
def test_une_teinte_hors_liste_est_refusee(valeur):
    with pytest.raises(ValueError) as erreur:
        config.teinte_valide(valeur)
    message = str(erreur.value)
    assert "teinte" in message and "petrole" in message and "mousse" in message


def test_instance_js_porte_petrole_par_defaut(gl, tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "INSTANCE", _instance(tmp_path))
    valeurs = gl.construire()
    assert valeurs["TEINTE"] == "petrole"
    cible = tmp_path / "instance.js"
    gl.ecrire(valeurs, [cible])
    assert "export const TEINTE = 'petrole'" in cible.read_text(encoding="utf-8")


def test_instance_js_porte_la_teinte_declaree(gl, tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "INSTANCE", _instance(tmp_path, teinte="mousse"))
    assert gl.construire()["TEINTE"] == "mousse"


def test_instance_js_refuse_une_teinte_inconnue(gl, tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "INSTANCE", _instance(tmp_path, teinte="turquoise"))
    with pytest.raises(SystemExit, match="turquoise"):
        gl.construire()
