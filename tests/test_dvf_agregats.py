"""DVF ne sort qu'en agrégats : aucune vente à l'unité dans le snapshot.

Les conditions de la DGFiP interdisent la ré-identification, même indirecte, des
vendeurs et l'indexation des mutations par les moteurs de recherche (audit du
17/10/2026). Une parcelle et une date suffisent, dans une petite commune.
"""
from __future__ import annotations

from scripts.snapshot.argent import DVF_SEUIL_MEDIANE, agreger_dvf


def mutation(date, nature="Maison", price=150000, m2=1500):
    return {"date": date, "nature_bien": nature, "price": price, "price_per_m2": m2}


def test_aucune_vente_a_l_unite_ne_sort():
    sortie = agreger_dvf([mutation("2024-03-01") for _ in range(6)])
    assert set(sortie) == {"seuil_mediane", "synthese", "par_annee", "par_nature"}
    texte = repr(sortie)
    for champ in ("cadastre_ref", "lieu_dit", "lat", "lng", "2024-03-01"):
        assert champ not in texte


def test_une_mediane_sous_le_seuil_n_est_pas_publiee():
    """Deux ventes : la médiane désignerait un vendeur. Le nombre reste, pour
    dire pourquoi elle manque."""
    sortie = agreger_dvf([mutation("2023-05-01", m2=900), mutation("2023-06-01", m2=1100)])
    annee = sortie["par_annee"][0]
    assert annee == {"annee": 2023, "mutations": 2, "ventes_bati": 2, "mediane_m2": None}
    assert sortie["synthese"]["mediane_prix_maison"] is None
    assert sortie["synthese"]["mediane_m2_bati"] is None


def test_au_seuil_la_mediane_est_publiee():
    ventes = [mutation("2022-01-01", m2=1000 + i) for i in range(DVF_SEUIL_MEDIANE)]
    assert agreger_dvf(ventes)["par_annee"][0]["mediane_m2"] == 1002


def test_la_composition_retombe_sur_le_total():
    ventes = [mutation("2024-01-01"), mutation("2024-01-02", "Appartement"),
              mutation("2024-01-03", "Dépendance", m2=None),
              mutation("2024-01-04", "Local industriel. commercial ou assimilé", m2=None),
              mutation("2024-01-05", None, m2=None)]
    s = agreger_dvf(ventes)["synthese"]
    assert (s["maisons"], s["appartements"], s["dependances"], s["locaux"], s["non_bati"]) \
        == (1, 1, 1, 1, 1)
    assert s["mutations"] == 5 and (s["debut"], s["fin"]) == (2024, 2024)


def test_une_commune_sans_mutation():
    sortie = agreger_dvf([])
    assert sortie["par_annee"] == [] and sortie["par_nature"] == []
    assert sortie["synthese"]["mutations"] == 0 and sortie["synthese"]["debut"] is None
