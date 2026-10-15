"""Lecture des présents : les formes rencontrées, et ce qui les faisait rater.

Les blocs de ce fichier sont copiés d'actes réels, à la ponctuation près — c'est
elle qui décide. Un conseil de trente-deux présents publié avec un seul n'a rien
d'un cas limite : c'est ce que rendait le moteur le 24/08/2026 sur les actes du
conseil communautaire d'une instance, faute d'un deux-points.
"""
from __future__ import annotations

from collectors.pv_parsers import noms, presences

# Mise en TABLEAU : la légende occupe une cellule, la liste la suivante. Aucun
# deux-points, des points-virgules pour séparer, un « et » pour le dernier — et
# un deux-points fautif au milieu, tel quel dans la pièce.
EN_TETE_TABLEAU = """DELIBERATION DU CONSEIL COMMUNAUTAIRE
Séance du 25 juin 2026 à 18h00
Le 25 juin 2026, le Conseil Communautaire s'est réuni sous la présidence de
Alix MARAGNOL, Président.
Présents BRUNAVEL Jordan ; NEVAGNOL Jordan ; MARAGNOL Alix ; CALIVERT Jordan ;
CALOURET Charlie : VELAUDIN Céleste ; SALAGNOL Sacha et MARAVEL Ange.
Pouvoirs FENESQUE Charlie à BRUNAVEL Jordan ; VELOMBE Yael à CALIVERT Jordan
Absents
Secrétaire de séance | SALAGNOL Sacha
"""

# La forme classique, avec deux-points : elle ne doit pas changer de rendu.
EN_TETE_DEUX_POINTS = """Séance du 12 mai 2026
Présents : Jordan BRUNAVEL, Jordan NEVAGNOL, Jordan CALIVERT
Absents : Charlie CALOURET
Secrétaire de séance : Sacha SALAGNOL
"""


def test_bloc_des_presents_sans_deux_points():
    lus = presences(EN_TETE_TABLEAU, "nom_prenom")["presents"]

    assert len(lus) == 8, lus
    assert "Jordan BRUNAVEL" in lus and "Ange MARAVEL" in lus


def test_le_point_virgule_separe_autant_que_la_virgule():
    lus = noms("BRUNAVEL Jordan ; NEVAGNOL Jordan ; CALIVERT Jordan", "nom_prenom")

    assert lus == ["Jordan BRUNAVEL", "Jordan NEVAGNOL", "Jordan CALIVERT"]


def test_le_dernier_nom_amene_par_et_nest_pas_perdu():
    lus = noms("SALAGNOL Sacha et MARAVEL Ange.", "nom_prenom")

    assert lus == ["Sacha SALAGNOL", "Ange MARAVEL"]


def test_un_deux_points_fautif_ne_mange_pas_le_nom_suivant():
    """« CALOURET Charlie : VELAUDIN Céleste » — coquille de saisie, deux élus."""
    lus = noms("CALOURET Charlie : VELAUDIN Céleste", "nom_prenom")

    assert lus == ["Charlie CALOURET", "Céleste VELAUDIN"]


def test_le_president_ne_compte_pas_deux_fois_pour_un_accent():
    """« Alix MARAGNOL » en ouverture, « MARAGNOL Alix » dans la liste."""
    lus = presences(EN_TETE_TABLEAU, "nom_prenom")["presents"]

    assert [n for n in lus if "MARAGNOL" in n.upper()] == ["Alix MARAGNOL"]


def test_la_forme_a_deux_points_est_inchangee():
    lu = presences(EN_TETE_DEUX_POINTS, "prenom_nom")

    assert lu["presents"] == ["Jordan BRUNAVEL", "Jordan NEVAGNOL", "Jordan CALIVERT"]
    assert lu["absents"] == ["Charlie CALOURET"]


def test_le_mot_presents_dans_une_phrase_nouvre_pas_de_bloc():
    """Sans quoi n'importe quelle phrase du corps fabriquerait des présents."""
    texte = ("Séance du 12 mai 2026\n"
             "Les conseillers présents ont approuvé le compte administratif "
             "présenté par Jordan BRUNAVEL.\n")

    assert presences(texte, "nom_prenom")["presents"] == []
