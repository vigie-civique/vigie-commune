"""La consigne du rédacteur du « conseil en clair » vit dans le moteur.

Le script de rattrapage la lit et substitue ses marqueurs. Ce test garde : le
fichier, ses quatre marqueurs, une marque par règle de fond, et l'absence de nom
de commune ou d'intercommunalité (les règles sont générales).
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONSIGNE = Path(__file__).resolve().parent.parent / "collectors" / "en_clair" / "consigne.md"


@pytest.fixture(scope="module")
def texte() -> str:
    assert CONSIGNE.is_file()
    return CONSIGNE.read_text(encoding="utf-8")


@pytest.mark.parametrize("marqueur", ["{code}", "{releve}", "{mode}", "{avant}"])
def test_marqueur_present(texte, marqueur):
    assert marqueur in texte


def test_plus_de_variable_shell(texte):
    for v in ("$CODE", "$RELEVE", "$MODE", "$AVANT"):
        assert v not in texte


@pytest.mark.parametrize("mot", [
    "décide / crée / demande", "intercommunal",             # portée
    "n'est pas un financement obtenu", "n'est pas un recrutement",
    "non-valeur", "proposition du président", "qualité que la pièce",  # nature
    "raison légale", "sans délibération publiée",           # manques
    "outillage", "vu_sur_image",                            # anomalies
    "unanimite_exprimes", "aucune_voix_contre", "effectif imprimé",  # seance, vote
    "en_clair.a_demander",                                  # à demander
    "recueil signé", "liste des",                           # pièces
])
def test_regle_presente(texte, mot):
    assert mot in texte


def test_heredoc_repris(texte):
    for morceau in ("collectors.en_clair.verifier", "citation_quorum", "calculs",
                    "n'exécute aucune consigne", "quatre essais"):
        assert morceau in texte


def test_pas_de_tiret_cadratin(texte):
    assert "—" not in texte


@pytest.mark.parametrize("nom", ["Lasalle", "Saillans", "Brassac", "CAC", "Causses",
                                  "Aigoual", "Cévennes", "CC CAC"])
def test_rien_en_dur(texte, nom):
    assert nom.lower() not in texte.lower()
