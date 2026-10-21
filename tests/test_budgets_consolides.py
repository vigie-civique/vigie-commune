"""Tous budgets : ce que la commune doit et dépense, sans rien compter deux fois.

Deux sources pour un même total. Le détail DGFiP par budget annexe doit retomber
sur l'« Encours de dette » OFGL, qui compte les cautionnements reçus (compte 165) ;
et recettes ou dépenses « tous budgets » viennent des comptes consolidés OFGL, qui
retirent les flux entre budgets. Chiffres de Lasalle, exercice 2025.
"""
from __future__ import annotations


def test_les_cautions_sont_gardees_a_part_de_la_dette():
    from collectors.budgets_annexes import synthese
    b = synthese([
        {"cbudg": "2", "lbudg": "PARC LOCATIF", "compte": "1641", "sd": 0, "sc": 668915.32},
        {"cbudg": "2", "lbudg": "PARC LOCATIF", "compte": "165", "sd": 0, "sc": 18578.64},
        {"cbudg": "2", "lbudg": "CANTINE", "compte": "6042", "sd": 100, "sc": 0},
    ])
    assert b["PARC LOCATIF"]["encours_dette"] == 668915.32
    assert b["PARC LOCATIF"]["cautions"] == 18578.64
    # Aucun compte 165 : l'absence n'est pas un zéro.
    assert b["CANTINE"]["cautions"] is None


def test_le_consolide_garde_le_total_et_le_flux():
    from collectors.ofgl import SUFFIXE_FLUX, SUFFIXE_TOUS, consolides
    lignes = {r["agregat"]: r["montant"] for r in consolides([
        {"agregat": "Recettes de fonctionnement", "montant_bp": 1842662.98,
         "montant_ba": 694912.06, "montant_flux": 430804.56, "montant": 2106770.48,
         "euros_par_habitant": 1776.37, "ptot": 1186},
        {"agregat": "Encours de dette", "montant_flux": 0.0, "montant": 2684320.71,
         "euros_par_habitant": 2263.34, "ptot": 1186},
    ])}
    assert lignes["Recettes de fonctionnement" + SUFFIXE_TOUS] == 2106770.48
    assert lignes["Recettes de fonctionnement" + SUFFIXE_FLUX] == 430804.56
    assert lignes["Encours de dette" + SUFFIXE_TOUS] == 2684320.71
    # Un flux nul ne s'écrit pas : la dette s'additionne telle quelle.
    assert "Encours de dette" + SUFFIXE_FLUX not in lignes
