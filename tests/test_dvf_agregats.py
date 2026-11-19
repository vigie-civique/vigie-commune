"""DVF ne sort qu'en agrégats : aucune vente à l'unité dans le snapshot.

Les conditions de la DGFiP interdisent la ré-identification, même indirecte, des
vendeurs et l'indexation des mutations par les moteurs de recherche (audit du
17/10/2026). Une parcelle et une date suffisent, dans une petite commune.
"""
from __future__ import annotations

from scripts.snapshot.argent import DVF_SEUIL_MEDIANE, agreger_dvf


import itertools

_rang = itertools.count(1)


def mutation(date, nature="Maison", price=150000, m2=1500, ident=None, parcelle="AB0001"):
    """Une LIGNE DVF. Sans `ident`, chaque ligne est une mutation à elle seule."""
    return {"id_mutation": ident or f"m{next(_rang)}", "date": date, "nature_bien": nature,
            "nature_mutation": "Vente", "price": price, "cadastre_ref": parcelle,
            "surface_bati": round(price / m2, 2) if m2 else None,
            "price_per_m2": m2}


def test_aucune_vente_a_l_unite_ne_sort():
    sortie = agreger_dvf([mutation("2024-03-01") for _ in range(6)])
    assert set(sortie) == {"seuil_mediane", "synthese", "par_annee", "par_nature"}
    texte = repr(sortie)
    for champ in ("cadastre_ref", "lieu_dit", "lat", "lng", "2024-03-01", "AB0001"):
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


def _une_vente_en_quatre_lignes(ident="2025-1", prix=200000):
    """Une maison de 100 m² vendue avec son garage, son jardin et un bois : DVF
    en fait quatre lignes, toutes au prix de l'acte, la maison répétée."""
    base = {"id_mutation": ident, "date": "2025-04-02", "nature_mutation": "Vente",
            "price": prix, "cadastre_ref": "AC0010", "price_per_m2": None}
    return [{**base, "nature_bien": "Maison", "surface_bati": 100.0, "surface_terrain": 800.0},
            {**base, "nature_bien": "Maison", "surface_bati": 100.0, "surface_terrain": 300.0},
            {**base, "nature_bien": "Dépendance", "surface_bati": None},
            {**base, "nature_bien": "taillis simples", "surface_bati": None,
             "cadastre_ref": "AC0011"}]


def test_une_vente_en_plusieurs_lignes_compte_pour_une():
    """Le défaut du 14/11/2026 : 112 « ventes » affichées à Lasalle pour 2025,
    34 mutations au fichier."""
    sortie = agreger_dvf(_une_vente_en_quatre_lignes())
    s = sortie["synthese"]
    assert (s["mutations"], s["lignes"]) == (1, 4)
    assert (s["maisons"], s["dependances"], s["non_bati"]) == (1, 0, 0)
    assert sortie["par_annee"][0]["mutations"] == 1
    assert {n["nature"]: n["mutations"] for n in sortie["par_nature"]} \
        == {"Maison": 1, "Dépendance": 1, "taillis simples": 1}


def test_le_prix_au_m2_se_rapporte_au_bati_une_fois_par_vente():
    """Il était divisé par la surface du terrain, et compté une fois par ligne."""
    ventes = [l for i in range(DVF_SEUIL_MEDIANE)
              for l in _une_vente_en_quatre_lignes(ident=f"v{i}")]
    sortie = agreger_dvf(ventes)
    assert sortie["par_annee"][0] == {"annee": 2025, "mutations": DVF_SEUIL_MEDIANE,
                                      "ventes_bati": DVF_SEUIL_MEDIANE, "mediane_m2": 2000}
    assert sortie["synthese"]["mediane_prix_maison"] == 200000


def test_deux_logements_dans_un_acte_ne_donnent_pas_de_prix_au_m2():
    base = {"id_mutation": "x", "date": "2025-01-01", "nature_mutation": "Vente",
            "price": 300000, "cadastre_ref": "AB0001"}
    sortie = agreger_dvf([{**base, "nature_bien": "Maison", "surface_bati": 90.0},
                          {**base, "nature_bien": "Appartement", "surface_bati": 40.0}])
    assert sortie["par_annee"][0]["ventes_bati"] == 0
    assert sortie["synthese"]["maisons"] == 1 and sortie["synthese"]["appartements"] == 0


def test_sans_identifiant_les_lignes_d_un_acte_se_retrouvent_par_date_et_prix():
    """Les lignes entrées avant la colonne `id_mutation` n'ont pas d'identifiant."""
    lignes = [{**l, "id_mutation": None} for l in _une_vente_en_quatre_lignes()]
    autre = [{**l, "id_mutation": None, "price": 90000}
             for l in _une_vente_en_quatre_lignes()[:1]]
    assert agreger_dvf(lignes + autre)["synthese"]["mutations"] == 2


def test_l_identifiant_se_reporte_sur_une_ligne_deja_en_base(tmp_path):
    import sqlite3
    from pathlib import Path
    from collectors.dvf import _parse_record, insert_transaction
    conn = sqlite3.connect(tmp_path / "essai.db")
    conn.executescript(Path("db/schema.sql").read_text(encoding="utf-8"))
    ligne = {"date_mutation": "2025-04-02", "valeur_fonciere": "200000", "type_local": "Maison",
             "id_parcelle": "30140000AC0010", "surface_reelle_bati": "100", "nature_mutation": "Vente"}
    insert_transaction(conn, _parse_record(ligne), "30140")            # avant : sans identifiant
    insert_transaction(conn, _parse_record({**ligne, "id_mutation": "2025-77"}), "30140")
    assert conn.execute("SELECT id_mutation FROM dvf_transactions").fetchall() == [("2025-77",)]
