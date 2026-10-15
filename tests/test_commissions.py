"""L'extraction des commissions communales depuis un procès-verbal.

Le matériau est un PDF converti en texte : les noms y sont coupés par la mise
en colonnes, les listes tronquées en fin de ligne, les numéros de page
intercalés, et le débat de séance encadre la composition. Ces tests reproduisent
ces formes, relevées dans un PV réel — les noms, eux, sont fictifs : le moteur
a vocation à être forké, et il ne doit nommer aucun habitant d'aucune commune.

Rien de ce que ce collecteur produit n'est publié : les commissions naissent en
`probable` et les sièges en file d'arbitrage. Une commission mal peuplée serait
une information fausse sur qui décide quoi.
"""
from __future__ import annotations

import pytest

from collectors.commissions import _noms, _recoller, _resoudre, extraire

# Formes relevées sur un PV réel, noms remplacés : le débat ouvre, un titre déborde sur deux
# lignes, un nom est coupé en fin de ligne, une liste est tronquée, une page
# s'intercale, et de la prose s'immisce entre un titre et sa composition.
PV = """M. CALESQUE : Nous allons désigner les membres des commissions
communales. Je vous propose de passer en revue toutes les commissions.
Développement économique – Commerces et artisanats + Agriculture
Responsables : Jordan CALIVERT
Membres : Maxime PELESQUE, Ange BORESQUE (volet agricole), Ange-
Loïs VELELLAC (volet agricole), Sacha CALIMET, Charlie
Finances – Budgets
Membres : Dominique NEVORLY, Sacha CALIMET, Maxime ROSOMBE,
10
Cadre de vie et espace public, sécurité et prévention, travaux services
techniques
Responsable : Andréa TARESQUE
Membres : Jordan QUILAGNOL, Ange
BORESQUE
Environnement :
⮚ Il est acté que cette thématique est transversale et concerne plusieurs
commissions. Les membres de cette commission seront intégrés au sein des
autres commissions.
Responsable : Jordan PELELLAC
Membres : Loïs GALANDIN - Sacha CALIMET
Mme MARTIN : Je fais part de ma déception à Maxime ROSOMBE.
"""


@pytest.fixture(scope="module")
def commissions():
    return {c["titre"]: c for c in extraire(PV)}


def test_le_debat_ouvre_le_bloc_sans_le_fermer(commissions):
    """Casser à la première prise de parole ne rendait rien : elle est en tête."""
    assert commissions, "aucune commission extraite"


def test_les_quatre_commissions_sont_trouvees(commissions):
    assert len(commissions) == 4, list(commissions)


def test_un_titre_qui_deborde_est_recolle(commissions):
    assert "Cadre de vie et espace public, sécurité et prévention, travaux " \
           "services techniques" in commissions


def test_la_prose_de_seance_nest_pas_un_titre(commissions):
    """« … seront intégrés au sein des autres commissions. » a été prise pour
    un titre de commission tant que la ponctuation de phrase n'était pas lue."""
    assert "Environnement" in commissions
    assert not any("intégrés" in t for t in commissions)


def test_un_nom_coupe_par_la_mise_en_page_est_recolle(commissions):
    membres = [n for n, _ in commissions["Environnement"]["membres"]]
    assert "Loïs GALANDIN" in membres          # prénom composé coupé après le tiret
    membres = [n for n, _ in commissions[
        "Cadre de vie et espace public, sécurité et prévention, travaux "
        "services techniques"]["membres"]]
    assert "Ange BORESQUE" in membres              # prénom et nom sur deux lignes


def test_le_responsable_est_identifie(commissions):
    assert commissions["Environnement"]["responsables"] == ["Jordan PELELLAC"]


def test_une_precision_ne_fait_pas_partie_du_nom(commissions):
    membres = dict(commissions[
        "Développement économique – Commerces et artisanats + Agriculture"]["membres"])
    assert membres["Ange BORESQUE"] == "volet agricole"


def test_le_numero_de_page_nest_pas_un_membre(commissions):
    for c in commissions.values():
        assert not any(n.strip().isdigit() for n, _ in c["membres"])


def test_une_liste_tronquee_ne_perd_pas_les_noms_lus(commissions):
    """Le PDF coupe après « Charlie » : les précédents restent exploitables."""
    membres = [n for n, _ in commissions["Finances – Budgets"]["membres"]]
    assert "Dominique NEVORLY" in membres and "Sacha CALIMET" in membres


# ── Résolution des noms ──────────────────────────────────────────────────────

def test_resolution_par_nom_complet_tranche_les_homonymes():
    complet = {"JORDAN PELAUDIN": 10, "CAMILLE VELELLAC": 11}
    patronyme = {}                                  # BORAVEL ambigu : écarté
    assert _resoudre("Jordan PELAUDIN", complet, patronyme) == 10
    assert _resoudre("Camille VELELLAC", complet, patronyme) == 11


def test_resolution_par_patronyme_quand_il_est_unique():
    assert _resoudre("M. Sacha CALIMET", {}, {"CALIMET": 7}) == 7


def test_une_civilite_ne_bloque_pas_la_resolution():
    assert _resoudre("Mme Ange GALANDIN", {"ANGE GALANDIN": 3}, {}) == 3


def test_un_inconnu_reste_non_resolu():
    """On ne crée jamais une personne depuis une expression régulière."""
    assert _resoudre("Quelqu'un DAILLEURS", {}, {"CALIMET": 7}) is None


def test_un_patronyme_ambigu_nest_pas_devine():
    assert _resoudre("TARANDIN", {}, {}) is None


# ── Briques ──────────────────────────────────────────────────────────────────

def test_recollage_des_traits_dunion():
    assert "Ange-Camille" in _recoller("Ange-\nCamille MARIVERT")


def test_separation_des_noms_sur_tiret_ou_virgule():
    noms = [n for n, _ in _noms("Loïs GALANDIN - Sacha CALIMET, Jordan PELELLAC")]
    assert noms == ["Loïs GALANDIN", "Sacha CALIMET", "Jordan PELELLAC"]
