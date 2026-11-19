"""Tous les montants d'un acte, à leur position — `citations.montants_a_positions`.

Lot 1 du chantier « lire les montants des PV » (24/10/2026). Le relevé est
mécanique et exhaustif ; le modèle ne fait plus que qualifier une liste fermée.
Textes fictifs, à la forme des tableaux de subventions de la CC.
"""
from __future__ import annotations

from collectors.citations import montants_a_positions, montants_cites

TABLEAU = """DÉLIBÉRATION N° 97/2024 DU 3 AVRIL 2024
Le conseil décide d'attribuer les subventions suivantes :
ASSOCIATION        DEMANDÉ      PROPOSÉ      VOTÉ
CLUB DES AINES     2 500,00     2 000,00     2 000,00
FOYER RURAL        1 700,00     1 700,00     1 700,00
TOTAL              4 200,00     3 700,00     3 700,00
Soit un montant total de 3 700 € inscrit au budget 2024."""


def test_chaque_cellule_d_un_tableau_est_relevee():
    valeurs = [m["valeur"] for m in montants_a_positions(TABLEAU)]
    assert valeurs == [2500.0, 2000.0, 2000.0, 1700.0, 1700.0, 1700.0,
                       4200.0, 3700.0, 3700.0, 3700.0]


def test_les_positions_designent_le_montant_dans_le_texte():
    for m in montants_a_positions(TABLEAU):
        assert " ".join(TABLEAU[m["debut"]:m["fin"]].split()) == m["texte"]


def test_l_unite_renvoyee_a_la_ligne_suivante():
    """L'OCR coupe « 6 821 € » avant l'unité : le montant reste relevé."""
    [m] = montants_a_positions("une dépense de 6 821\n€ au chapitre 011")
    assert (m["valeur"], m["ligne"]) == (6821.0, 0)


def test_un_montant_nul_est_releve():
    """Un zéro de tableau est une valeur à qualifier, pas une absence."""
    assert [m["valeur"] for m in montants_a_positions("Reste à réaliser : 0,00 €")] == [0.0]


def test_des_points_de_suite_ne_cachent_pas_le_montant():
    [m] = montants_a_positions("Total des dépenses ...1 232 269,88 €")
    assert m["valeur"] == 1232269.88


def test_ni_annee_ni_date_ni_numero_d_acte():
    texte = "Délibération 97/2024 du 03/04/2024, séance de 2024, article 6574."
    assert montants_a_positions(texte) == []


def test_l_euro_avant_le_nombre_C5():
    """La CC 2025 écrit « €2 500,00 » : le lecteur du moteur rendait 0."""
    [m] = montants_a_positions("Subvention accordée : €2 500,00 au foyer.")
    assert (m["valeur"], m["forme"]) == (2500.0, "euro")


def test_les_centimes_sont_gardes_C10():
    [m] = montants_a_positions("Montant : 13 766,98 € TTC")
    assert m["valeur"] == 13766.98


def test_une_cellule_isolee_n_est_pas_un_montant():
    """Un nombre seul sur sa ligne, sans unité, peut être n'importe quoi."""
    assert montants_a_positions("Population : 1 234,00 habitants") == []


def test_la_page_vient_des_debuts_du_manifeste():
    texte = "page un\n500 €\npage deux\n700 €"
    pages = [0, len("page un\n500 €\n")]
    assert [m["page"] for m in montants_a_positions(texte, pages)] == [1, 2]


def test_tout_ce_que_montants_cites_voit_est_releve():
    """Le relevé est un sur-ensemble du lecteur du moteur, jamais moins."""
    for texte in (TABLEAU, "Un marché de 13 766.98 € et 1.234 €.", "12 euros puis 5 €",
                  "soit 0,00 € et ...1 232 269,88 €", "de 6 821\n€"):
        assert montants_cites(texte) <= {m["valeur"] for m in montants_a_positions(texte)}


def test_les_identifiants_suivent_l_ordre_du_texte():
    ids = [m["id"] for m in montants_a_positions(TABLEAU)]
    assert ids == [f"M{i}" for i in range(1, len(ids) + 1)]
