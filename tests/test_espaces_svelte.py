"""Les espaces au bord intérieur d'un bloc Svelte — scripts/espaces_svelte5.py.

Svelte 5 supprime l'espace qui ouvre ou ferme un `{#if}` / `{:else}` ; Svelte 4
la gardait. À la montée du 15/10/2026, le site public perdait ainsi une
vingtaine d'espaces visibles (« Procès-verbal: », « votés.Ce »), sans un
avertissement au build. Une espace voulue à cet endroit s'écrit `{' '}`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from espaces_svelte5 import corriger  # noqa: E402


@pytest.mark.parametrize("paquet", ["public", "dashboard"])
def test_aucune_espace_de_bord_de_bloc_n_est_implicite(paquet):
    fautes = []
    for f in sorted((ROOT / paquet / "src").rglob("*.svelte")):
        _, lignes = corriger(f.read_text(encoding="utf-8"))
        fautes += [f"{f.relative_to(ROOT)}:{l}" for l in lignes]
    assert not fautes, ("espace que Svelte 5 supprimerait — l'écrire {' '} "
                        "(python3 scripts/espaces_svelte5.py <paquet>/src --appliquer) :\n"
                        + "\n".join(fautes))


def test_le_controle_voit_les_deux_bords():
    _, lignes = corriger("<p>{#if a} ou à <a href=\"/x\">x</a>{/if}{#if i}, {/if}</p>")
    assert len(lignes) == 2
    # Entre deux blocs de mise en page, l'espace ne se voyait pas : rien à dire.
    _, lignes = corriger("{#if a}\n  <div>x</div>\n{/if}")
    assert lignes == []
