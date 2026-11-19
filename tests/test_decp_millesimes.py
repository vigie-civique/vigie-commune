"""Un zéro de marchés dit les années que les consolidés DECP lus ne couvrent pas.

Le relevé (`enregistrer_millesimes`) et sa lecture par le site. Comment un
millésime est dit couvert : `test_decp_millesimes_lus.py`.
"""
from __future__ import annotations

from collectors.marches_publics import enregistrer_millesimes
from scripts.snapshot.couverture import millesimes_decp


def test_les_annees_manquantes_entre_la_premiere_et_larret(base):
    enregistrer_millesimes(base, {2019: 1, 2022: 1, 2024: 13, 2025: 13, 2026: 11})
    assert millesimes_decp(base, 2026) == {"lus": [2019, 2022, 2024, 2025, 2026],
                                           "absents": [2020, 2021, 2023]}


def test_une_annee_sans_fichier_jusqu_a_l_arret_est_absente(base):
    enregistrer_millesimes(base, {2024: 1})
    assert millesimes_decp(base, 2026)["absents"] == [2025, 2026]


def test_le_releve_est_remplace_et_non_cumule(base):
    enregistrer_millesimes(base, {2019: 1, 2020: 1})
    enregistrer_millesimes(base, {2019: 1})
    assert millesimes_decp(base, 2020) == {"lus": [2019], "absents": [2020]}


def test_sans_releve_le_site_ne_sait_pas(base):
    assert millesimes_decp(base, 2026) is None
    enregistrer_millesimes(base, {})
    assert millesimes_decp(base, 2026) is None


def test_une_annee_aberrante_n_ouvre_pas_des_siecles_d_absents(base):
    """Relecture vigie-securite du 08/11 : une année « 0001 » relevée faisait
    énumérer deux mille années absentes sur /marches."""
    enregistrer_millesimes(base, {1: 1, 2024: 1, 2089: 1})
    assert millesimes_decp(base, 2026) == {"lus": [2024], "absents": [2025, 2026]}


def test_la_date_de_notification_est_bornee():
    from collectors.marches_publics import annee_notification
    assert annee_notification("2023-05-02") == 2023
    assert annee_notification("0001-01-01") is None
    assert annee_notification("2089-01-01") is None
    assert annee_notification("") is None
