"""Un nom d'instance s'écrit par sa variable, jamais par son gabarit entre guillemets.

Au paramétrage du site, « 'Lasalle' » est devenu « '{COMMUNE}' » à deux endroits :
une CHAÎNE, que Svelte n'interprète pas. Le titre des fiches sans commune —
syndicats, PETR — servait « PETR Causses et Cévennes - {COMMUNE} », et la page
des élections ne mettait plus jamais la commune en avant (relevé le 24/10/2026).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GABARIT_EN_CHAINE = re.compile(r"""['"`]\{[A-Z][A-Z_]*\}['"`]""")


@pytest.mark.parametrize("paquet", ["public", "dashboard"])
def test_aucun_gabarit_nest_ecrit_entre_guillemets(paquet):
    fautes = [f"{f.relative_to(ROOT)}:{n}"
              for f in sorted((ROOT / paquet / "src").rglob("*"))
              if f.suffix in (".svelte", ".js")
              for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
              if GABARIT_EN_CHAINE.search(ligne)]
    assert not fautes, "gabarit écrit en chaîne — utiliser la variable importée :\n" + "\n".join(fautes)


def test_le_controle_voit_la_chaine_et_pas_la_variable():
    assert GABARIT_EN_CHAINE.search("r.commune === '{COMMUNE}'")
    assert not GABARIT_EN_CHAINE.search("r.commune === COMMUNE")
    assert not GABARIT_EN_CHAINE.search("<h2>{COMMUNE} en chiffres</h2>")
