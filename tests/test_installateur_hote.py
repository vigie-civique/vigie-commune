"""L'assistant d'installation n'écoute que la machine, sous ses trois noms.

`"[::1]:8790".split(":")[0]` rendait « [ » : un navigateur qui joignait
l'assistant en IPv6 était refusé à tous les coups (audit du 17/10/2026).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def assistant():
    spec = importlib.util.spec_from_file_location(
        "assistant_installateur", ROOT / "installateur" / "assistant.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def autorise(assistant, hote):
    poignee = object.__new__(assistant.Poignee)
    poignee.headers = {"Host": hote, "X-Jeton": assistant.JETON}
    poignee.path = "/"
    poignee._json = lambda *a, **k: None
    return poignee._autorise()


@pytest.mark.parametrize("hote", ["127.0.0.1:8790", "localhost:8790", "[::1]:8790", "localhost"])
def test_la_machine_est_admise_sous_ses_trois_noms(assistant, hote):
    assert autorise(assistant, hote)


@pytest.mark.parametrize("hote", ["exemple.fr:8790", "127.0.0.1.exemple.fr", "[::2]:8790",
                                  "", "[::1", "localhost:abc@exemple.fr"])
def test_un_autre_hote_est_refuse(assistant, hote):
    assert not autorise(assistant, hote)
