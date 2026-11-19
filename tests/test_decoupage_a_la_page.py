"""Une liasse se découpe au début de la page où un acte commence, plus sur la ligne du cachet.

Lot 2 du chantier « lire les montants des PV ». L'audit du 23/10/2026 a trouvé
13 actes sur 213 fusionnés ou décalés dans les recueils de l'intercommunalité :
la coupure tombait sur la LIGNE du premier cachet d'un nouveau numéro, donc au
milieu d'une page quand le cachet n'est pas en tête, et une page trop loin
quand l'océrisation a perdu le cachet de la page d'ouverture.

Mesuré le 25/10/2026 sur les liasses de Saillans (7 documents, 36 actes) : mêmes
actes, mêmes numéros, un seul début qui change — l'acte 10 du 30/06/2022, dont
la page 17 porte l'ouverture et un cachet illisible.
"""
from __future__ import annotations

from collectors.pv_parsers import _actes_teletransmis, actes_par_page, deliberations_a_la_page

CACHET = "ID : 030-200034601-20240529-{numero}-DE\n"


def _page(numero: str | None, objet: str = "", corps: str = "") -> str:
    page = "DEPARTEMENT : GARD\n" + (CACHET.format(numero=numero) if numero else "Affiché le\n")
    if objet:
        page += f"Objet : {objet}\n"
    return page + (corps or "Le Conseil communautaire, après en avoir délibéré,\n")


def test_un_acte_commence_au_debut_de_sa_page_et_non_sur_son_cachet():
    """Le cachet en bas de page : la coupure au cachet laissait l'objet à l'acte d'avant."""
    pages = [
        _page("107_2024", "Approbation du procès-verbal du 6 mars 2024"),
        "Objet : Convention de mise à disposition de services\nLe Conseil, après en avoir délibéré,\n"
        + CACHET.format(numero="108_2024"),
        _page("109_2024", "Tarifs de la déchèterie intercommunale"),
    ]
    au_cachet = _actes_teletransmis("\n".join(pages))
    assert "Convention de mise à disposition" in au_cachet[0]["texte"]

    actes = actes_par_page(pages)["actes"]
    assert [a["numero_acte"] for a in actes] == ["107", "108", "109"]
    assert [a["pages"] for a in actes] == [[1, 1], [2, 2], [3, 3]]
    assert actes[1]["titre"] == "Convention de mise à disposition de services"
    assert "Convention" not in actes[0]["texte"]


def test_une_page_douverture_sans_cachet_revient_a_son_acte():
    """Saillans, 30/06/2022 : l'acte 10 s'ouvre page 17, cachet illisible."""
    pages = [
        _page("107_2024", "Approbation du procès-verbal"),
        _page(None, "Fresque murale de l'école", "Réalisation de la fresque 3 000,00 €\n"),
        _page("108_2024", corps="Plan de financement prévisionnel HT\n"),
        _page("109_2024", "Tarifs de la déchèterie"),
    ]
    r = actes_par_page(pages)
    assert [a["pages"] for a in r["actes"]] == [[1, 1], [2, 3], [4, 4]]
    assert r["actes"][1]["titre"] == "Fresque murale de l'école"
    assert r["controle"]["ouvertures_deplacees"] == [{"numero": 108, "de": 3, "a": 2}]


def test_une_page_de_cachet_qui_porte_son_ouverture_ne_remonte_pas():
    """Saillans, 07/04/2022 : la page sans cachet lu ouvre l'acte d'AVANT.

    Remonter jusqu'à elle faisait avaler à l'acte suivant l'acte précédent et son
    tableau des subventions.
    """
    pages = [
        _page("107_2024", "Approbation du procès-verbal"),
        _page(None, "Subventions aux associations"),
        _page("108_2024", "Affectation du résultat"),
        _page("109_2024", "Tarifs de la déchèterie"),
    ]
    r = actes_par_page(pages)
    assert [a["pages"] for a in r["actes"]] == [[1, 2], [3, 3], [4, 4]]
    assert r["controle"]["ouvertures_deplacees"] == []


def test_la_suite_des_numeros_est_controlee():
    pages = [_page(f"{n}_2024", f"Délibération numéro {n}") for n in (107, 108, 110, 111)]
    pages.insert(2, _page("29_2022", corps="Page abîmée.\n"))
    c = actes_par_page(pages)["controle"]
    assert (c["premier"], c["dernier"], c["manquants"]) == (107, 111, [109])
    assert c["hors_suite"] == [{"cachet": "29_2022", "page": 3}]
    assert c["suite_lisible"] is True


def test_un_document_qui_nest_pas_une_liasse_rend_rien():
    assert actes_par_page([_page("PV2024_03") for _ in range(12)])["actes"] == []
    assert actes_par_page(["Compte rendu de séance\n", "Objet : budget\n"]) == {
        "actes": [], "controle": {}}


def test_le_point_dentree_ne_decoupe_a_la_page_que_sur_des_pages_qui_recomposent_le_texte():
    pages = [_page(f"{n}_2024", f"Délibération numéro {n}") for n in (107, 108, 109)]
    texte = "\n".join(pages)
    actes, suite = deliberations_a_la_page(texte, pages)
    assert [a["pages"] for a in actes] == [[1, 1], [2, 2], [3, 3]]
    assert suite["manquants"] == []
    assert deliberations_a_la_page(texte, None) == ([], {})
    assert deliberations_a_la_page(texte + " ", pages) == ([], {})     # pas CE texte


def test_le_titre_se_lit_depuis_le_cachet_quand_la_page_le_porte():
    """Lasalle, 09/02/2022 : le bandeau de tête relu « N°2/2022 » pour l'acte 9.

    Lu depuis le haut de la page, la garde d'appartenance refusait l'objet ; lu
    depuis le cachet, comme le régime l'a toujours fait, il reste le sien.
    """
    pages = [
        _page("107_2024", "Approbation du procès-verbal"),
        _page("108_2024", "Tarifs de la déchèterie"),
        "N°2/2024\n" + _page("109_2024", "Convention pour l'utilisation du quai de transit"),
    ]
    actes = actes_par_page(pages)["actes"]
    assert actes[2]["titre"] == "Convention pour l'utilisation du quai de transit"
    assert actes[2]["texte"].startswith("N°2/2024")



def test_un_rang_mal_lu_en_tete_de_la_suite_n_est_pas_un_acte():
    """CC du 29/10/2025 : « D141_2025 » océrisé « Di41_2025 », devant 120…135.

    41 est plus petit que tout ce qui suit : il tenait dans la suite croissante,
    en prenait la tête et se publiait en double du 141.
    """
    pages = [_page("41_2025", "Modification du régime du temps partiel")]
    pages += [_page(f"{n}_2025", f"Délibération numéro {n}") for n in (120, 121, 122, 123)]
    r = actes_par_page(pages)
    assert [a["numero_acte"] for a in r["actes"]] == ["120", "121", "122", "123"]
    assert r["controle"]["premier"] == 120 and r["controle"]["manquants"] == []
    assert r["controle"]["hors_suite"] == [{"cachet": "41_2025", "page": 1}]
    assert [a["numero_acte"] for a in _actes_teletransmis("\n".join(pages))] == [
        "120", "121", "122", "123"]


def test_un_rang_mal_lu_en_queue_de_la_suite_n_est_pas_un_acte():
    """Saillans, 08/07/2021 : « DELIB3CMO807 » donnait un acte 380721 après 1, 2, 3."""
    pages = [_page(f"{n}_2024", f"Délibération numéro {n}") for n in (1, 2, 3, 4)]
    pages.append(_page("380_2024", corps="Suite de l'acte 4.\n"))
    assert [a["numero_acte"] for a in actes_par_page(pages)["actes"]] == ["1", "2", "3", "4"]


def test_une_suite_aux_ecarts_reguliers_garde_ses_bords():
    """Un millésime resté collé fait de grands écarts PARTOUT : aucun n'est isolé."""
    from collectors.pv_parsers import _suite_croissante
    assert _suite_croissante([1, 370422, 470422, 570422, 670422]) == [0, 1, 2, 3, 4]
    assert _suite_croissante([1, 16, 106]) == [0, 1, 2]
    assert _suite_croissante([57, 59, 60, 63, 64]) == [0, 1, 2, 3, 4]
