"""« Le conseil en clair » : le squelette ne fournit pas de fait faux au rédacteur.

Relecture à l'aveugle de cinq séances d'une intercommunalité (10/11/2026) : les
mêmes défauts revenaient d'une séance à l'autre. Les suffrages exprimés pris
pour les votants ; « unanimité » sur un acte dont le corps porte une
abstention ; la date du fichier prise pour la date de publication ; le PV de la
séance précédente, annexé à la délibération qui l'approuve, pris pour une
anomalie ou pour la séance ; le lieu lu dans la ligne de date de la lettre.

Textes et noms fictifs ; les formes imitent les pièces publiées.
"""
from __future__ import annotations

import json

import pytest

from collectors.en_clair.brouillon import MARQUE_CC, _vote, actes, completer, squelette
from collectors.en_clair.rendu import erreurs
from collectors.en_clair.verifier import trier_anomalies, verifier


def _tampon(n: int, jour: str = "20260923", publie: str = "30/09/2026") -> str:
    return (f"Envoyé en préfecture le {publie}\nReçu en préfecture le {publie}\n"
            f"Publié le {publie}\nID : 030-200000001-{jour}-D{n}-DE\n")


def _cartouche(presents: str = "Présents : 25 dont 2 suppléants") -> str:
    return ("Nombre de membres en exercice : 28\n"
            f"{presents}\n"
            "Procurations : 2\n"
            "Nombre de suffrages exprimés : 24\n"
            "Quorum : 11\n")


# Une liasse de trois actes télétransmis. Le n°112 approuve le PV du 9 juillet
# et le reproduit : ses présents (29 + 3), son lieu, ses votes, ses montants et
# un acte télétransmis de juillet n'appartiennent pas au 23 septembre.
LIASSE = (
    _tampon(112)
    + "N°112/2026\nDEPARTEMENT : GARD\nCOMMUNAUTE DE COMMUNES DE FICTIVAL\n" + _cartouche()
    + "OBJET : APPROBATION DU PROCES-VERBAL\n"
    "Le conseil communautaire, après en avoir délibéré, à l’unanimité :\n"
    "APPROUVE le procès-verbal de la séance du 9 juillet 2026, annexé à la présente.\n"
    "PROCES-VERBAL DE LA SEANCE\nDU 9 JUILLET 2026\n"
    "Le conseil communautaire s’est réuni le 9 juillet 2026 à 18 h au foyer rural de Fictiville.\n"
    "Nombre de membres en exercice : 28\nPrésents : 29\nProcurations : 3\nQuorum : 11\n"
    "Délibération n°100/2026 BUDGET ANNEXE\n"
    "après en avoir délibéré, avec 20 voix pour et 2 abstentions,\n"
    "APPROUVE le budget annexe de 48 300 €.\n"
    "Publié le 15/07/2026\nID : 030-200000001-20260709-D100-DE\n"
    + _tampon(113)
    + "N°113/2026\nDEPARTEMENT : GARD\n" + _cartouche()
    + "OBJET : FONDS DE PEREQUATION\n"
    "Le conseil communautaire s’est réuni le 23 septembre 2026 à 18 h, à la salle des fêtes "
    "de Saint-Fictif-de-Causse, sous la présidence de M. le Président.\n"
    "Après en avoir délibéré, à l’unanimité des suffrages exprimés\n"
    "Pour : 24 Contre : 0 Abstentions : 3\n"
    "APPROUVE la répartition dérogatoire libre.\n"
    + _tampon(114)
    + "N°114/2026\nDEPARTEMENT : GARD\n" + _cartouche()
    + "Pour : 27 Contre : 0 Abstention : 0\n"
    "OBJET : DESIGNATION AU SYNDICAT D’EAU\n"
    "Le conseil communautaire, après en avoir délibéré, avec 26 voix pour et 1 abstention,\n"
    "DESIGNE un représentant au syndicat d’eau potable.\n"
    "Adopté à l’unanimité\n"
)


@pytest.fixture
def instance(tmp_path):
    racine = tmp_path / "instance"
    (racine / "collectors").mkdir(parents=True)
    (racine / "data").mkdir()
    (racine / "data" / "pv.txt").write_text(LIASSE)
    (racine / "data" / "conseils" / "2026-09-23-cc").mkdir(parents=True)
    return racine


@pytest.fixture
def releve():
    r = squelette("2026-09-23-cc", LIASSE, None, {"pv": "data/pv.txt"}, {},
                  comme={"seance": {"assemblee": "CC de Fictival", "assemblee_court": "CC de Fictival"}},
                  depots={"pv": "2026-10-01"})
    return r


def _acte(r, n):
    return next(a for a in r["actes"] if a["n"] == n)


def _textes(r):
    return [x["texte"] for x in r["anomalies_seance"]]


def test_la_liasse_passe_le_verificateur_par_construction(instance, releve):
    chemin = instance / "data" / "conseils" / "2026-09-23-cc" / "releve.json"
    chemin.write_text(json.dumps(releve, ensure_ascii=False))
    assert verifier(chemin) == []
    assert [a["n"] for a in releve["actes"]] == [112, 113, 114]


def test_les_ecarts_lus_sur_un_cartouche_attendent_l_image(instance, releve):
    """Quorum, votants, corps contre cartouche : lus sur l'OCR, de nature
    « cartouche » et sans `vu_sur_image` ; le vérificateur les signale, le
    rendu ne les montre pas. Une fois revus sur l'image, ils sont rendus."""
    anomalies = releve["anomalies_seance"] + [a["anomalie"] for a in releve["actes"] if a.get("anomalie")]
    assert len(anomalies) == 2
    assert all(x["nature"] == "cartouche" and "vu_sur_image" not in x for x in anomalies)
    retenues, a_verifier, fautes = trier_anomalies(releve)
    assert (retenues, fautes, len(a_verifier)) == ([], [], 2)
    assert erreurs(releve) == []
    chemin = instance / "data" / "conseils" / "2026-09-23-cc" / "releve.json"
    chemin.write_text(json.dumps(releve, ensure_ascii=False))
    assert verifier(chemin) == []
    assert sum("ANOMALIE à vérifier sur l'image" in x for x in verifier.avis) == 2

    for x in anomalies:
        x["vu_sur_image"] = "p. 2 de data/pv.pdf, cartouche relu à l'image"
    assert len(erreurs(releve)) == 2
    assert any(x.startswith("Délibération n°114 : Le corps de l'acte") for x in erreurs(releve))


# ── 1. votants et quorum ─────────────────────────────────────────────────────

def test_les_exprimes_ne_sont_pas_les_votants(releve):
    """Piège : 24 exprimés imprimés, 25 présents + 2 procurations = 27 votants."""
    s = releve["seance"]
    assert s["votants"] == 27
    assert "exprim" not in s["citation_quorum"].lower()
    assert s["citation_quorum"] == "Présents : 25 dont 2 suppléants Procurations : 2"
    e = s["effectif"]
    assert (e["en_exercice"], e["presents"], e["dont_suppleants"], e["procurations"],
            e["exprimes"]) == (28, 25, 2, 2, 24)


def test_le_quorum_mal_compte_par_la_piece_est_une_anomalie_sans_conclusion(releve):
    e = releve["seance"]["effectif"]
    assert (e["quorum_dit"], e["quorum_calcule"]) == (11, 15)
    quorum = [x for x in _textes(releve) if "Quorum" in x]
    assert quorum == ["La pièce indique « Quorum : 11 » ; la majorité des 28 membres "
                      "en exercice est 15."], "une seule fois, pas une par cartouche"
    assert not any(m in quorum[0].lower() for m in ("valide", "irrégul", "atteint", "nulle"))
    assert {"valeur": "15", "formule": "28 // 2 + 1",
            "dit": "quorum : majorité des 28 membres en exercice"} in releve["calculs"]


def test_seuls_les_exprimes_imprimes_ne_donnent_pas_de_votants():
    """L'ancienne lecture prenait « Nombre de suffrages exprimés » pour les votants."""
    pv = ("PROCES-VERBAL\nNombre de suffrages exprimés : 20\n"
          "SUBVENTION\nLe conseil, après en avoir délibéré à l’unanimité :\nDECIDE d’aider.\n")
    r = squelette("2026-01-10-cm", pv, None, {"pv": "data/pv.txt"}, {})
    assert "votants" not in r["seance"] and "citation_quorum" not in r["seance"]


def test_des_votants_imprimes_faux_sont_une_anomalie_de_la_piece():
    pv = ("PROCES-VERBAL\nPrésents : 24\nSuppléant : 1\nPouvoirs : 3\nVotants : 24\n"
          "Nombre de suffrages exprimés : 30\n"
          "SUBVENTION\nLe conseil, après en avoir délibéré à l’unanimité :\nDECIDE d’aider.\n")
    r = squelette("2026-01-10-cm", pv, None, {"pv": "data/pv.txt"}, {})
    s = r["seance"]
    assert s["votants"] == 28 and s["effectif"]["votants_dit"] == 24
    assert s["citation_quorum"] == "Présents : 24 Suppléant : 1 Pouvoirs : 3"
    assert ("La pièce indique « Votants : 24 » ; 24 présents, 1 suppléant et 3 procurations "
            "font 28 votants.") in _textes(r)
    assert any("exprimés ne peuvent dépasser" in x for x in _textes(r))


def test_des_votants_justes_se_citent_tels_quels():
    pv = ("PROCES-VERBAL\nPrésents : 20\nProcurations : 1\nVotants : 21\n"
          "SUBVENTION\nLe conseil, après en avoir délibéré à l’unanimité :\nDECIDE d’aider.\n")
    r = squelette("2026-01-10-cm", pv, None, {"pv": "data/pv.txt"}, {})
    assert (r["seance"]["votants"], r["seance"]["citation_quorum"]) == (21, "Votants : 21")
    assert r["anomalies_seance"] == []


# ── 2. le vote, acte par acte ───────────────────────────────────────────────

def test_un_corps_partage_sous_un_cartouche_unanime_n_est_pas_unanime(releve):
    """Piège : cartouche 27-0-0 et « Adopté à l'unanimité », corps « 26 voix pour et 1 abstention »."""
    a = _acte(releve, 114)
    assert a["vote"] == {"pour": 26, "abstentions": 1}
    assert a["vote_cite"]["corps"] == "26 voix pour et 1 abstention"
    assert a["vote_cite"]["cartouche"] == "Pour : 27 Contre : 0 Abstention : 0"
    texte = a["anomalie"]["texte"]
    assert "« 26 voix pour et 1 abstention »" in texte
    assert "« Pour : 27 Contre : 0 Abstention : 0 »" in texte
    assert "pas unanime" in texte


def test_l_unanimite_des_exprimes_avec_abstentions_n_est_ni_unanime_ni_une_anomalie(releve):
    a = _acte(releve, 113)
    assert a["vote"] == {"pour": 24, "contre": 0, "abstentions": 3, "forme": "unanimite_exprimes"}
    assert "anomalie" not in a


@pytest.mark.parametrize("corps, attendu", [
    ("après en avoir délibéré à l’unanimité :\nDECIDE.", {"unanimite": True}),
    ("Pour : 27 Contre : 0 Abstention : 0\nAdopté à l’unanimité",
     {"pour": 27, "contre": 0, "abstentions": 0, "unanimite": True}),
    ("adopte, aucune voix contre, la motion.", {"forme": "aucune_voix_contre"}),
    ("par 20 voix pour, 2 contre et 1 abstention.", {"pour": 20, "contre": 2, "abstentions": 1}),
    ("Le fonds exige l’unanimité du conseil. DECIDE de reporter.", None),
])
def test_les_formes_du_vote_sont_distinguees(corps, attendu):
    vote, _, anomalie = _vote(corps)
    assert vote == attendu and anomalie == ""


def test_corps_et_cartouche_qui_divergent_sans_mention_d_unanimite():
    vote, cites, anomalie = _vote("Pour : 25 Contre : 0 Abstentions : 0\n"
                                  "après en avoir délibéré, par 23 voix pour et 2 contre,\nDECIDE.")
    assert vote == {"pour": 23, "contre": 2}
    assert anomalie == ("Le corps de l'acte porte « 23 voix pour et 2 contre », "
                        "le cartouche « Pour : 25 Contre : 0 Abstentions : 0 ».")


# ── 3. la date de publication ───────────────────────────────────────────────

def test_la_mention_publie_le_fait_foi_et_le_depot_reste_a_part(releve):
    s = releve["seance"]
    assert (s["publie_le"], s["citation_publication"]) == ("2026-09-30", "Publié le 30/09/2026")
    assert releve["depot_fichier"] == {"pv": "2026-10-01"}
    assert "2026-10-01" not in json.dumps(releve["seance"])
    a = _acte(releve, 113)
    assert (a["publie_le"], a["identifiant"]) == ("2026-09-30", "030-200000001-20260923-D113-DE")


def test_deux_dates_de_publication_restent_acte_par_acte():
    pv = "".join(_tampon(n, publie=p) + f"N°{n}/2026\nDEPARTEMENT : GARD\nOBJET {n}\nAPPROUVE le point {n}.\n"
                 for n, p in ((1, "30/09/2026"), (2, "30/09/2026"), (3, "02/10/2026")))
    r = squelette("2026-09-23-cc", pv, None, {"pv": "data/pv.txt"}, {})
    assert "publie_le" not in r["seance"]
    assert [a.get("publie_le") for a in r["actes"]] == ["2026-09-30", "2026-09-30", "2026-10-02"]


def test_sans_tampon_la_mention_du_pv_suffit():
    pv = ("PROCES-VERBAL DU 4 FEVRIER 2026\nPublié le 12/02/2026\n"
          "SUBVENTION\nLe conseil, après en avoir délibéré à l’unanimité :\nDECIDE d’aider.\n")
    r = squelette("2026-02-04-cm", pv, None, {"pv": "data/pv.txt"}, {}, depots={"pv": "2026-03-01"})
    assert r["seance"]["publie_le"] == "2026-02-12" and r["depot_fichier"] == {"pv": "2026-03-01"}


# ── 4. le rattachement par l'identifiant @ctes ──────────────────────────────

def test_le_pv_anterieur_annexe_n_est_ni_une_anomalie_ni_la_seance(releve):
    """Piège : la liasse reproduit le PV du 9 juillet (29 présents, 3 procurations,
    un vote partagé, un acte télétransmis de juillet)."""
    a = _acte(releve, 112)
    assert a["annexe_pv"]["seance"] == "2026-07-09"
    assert a["vote"] == {"unanimite": True} and "anomalie" not in a
    assert "montants" not in a, "les montants du PV annexé ne sont pas ceux de l'acte"
    assert a["annexe_pv"]["citation"] == "PROCES-VERBAL DE LA SEANCE DU 9 JUILLET 2026"
    assert "budget" not in _acte(releve, 113)["objet"].lower(), "ni le titre de l'acte suivant"
    assert releve["seance"]["date"] == "2026-09-23"
    assert releve["seance"]["votants"] == 27, "pas les 32 du PV annexé"
    assert _textes(releve) and not any("juillet" in x or "07" in x for x in _textes(releve))
    autres = {(x["date"], x["nature"]) for x in releve["autres_seances"]}
    assert autres == {("2026-07-09", "acte télétransmis"), ("2026-07-09", "procès-verbal reproduit")}


def test_la_tete_du_pv_de_la_seance_n_est_pas_une_autre_seance():
    pv = ("N°1/2026\nDEPARTEMENT : GARD\nOBJET 1\nAPPROUVE le point 1.\n"
          "PROCES-VERBAL\nConvocation du 16/09/2026 pour la séance du 23 septembre 2026\n"
          "N°2/2026\nDEPARTEMENT : GARD\nOBJET 2\nAPPROUVE le point 2.\n"
          "N°3/2026\nDEPARTEMENT : GARD\nOBJET 3\nAPPROUVE le point 3.\n")
    r = squelette("2026-09-23-cc", pv, None, {"pv": "data/pv.txt"}, {})
    assert "autres_seances" not in r and not any("annexe_pv" in a for a in r["actes"])


# ── 5. le lieu ───────────────────────────────────────────────────────────────

def test_le_lieu_vient_de_la_phrase_de_reunion_pas_du_pv_annexe(releve):
    s = releve["seance"]
    assert s["lieu"] == "salle des fêtes de Saint-Fictif-de-Causse"
    assert s["citation_lieu"] == "à la salle des fêtes de Saint-Fictif-de-Causse"
    assert s["source_lieu"] == "pv"


CONVOCATION = ("COMMUNAUTE DE COMMUNES DE FICTIVAL\nSiège : place du Fort, Bourg-Fictif\n"
               "Bourg-Fictif, le 14 septembre 2026\n\nMadame, Monsieur,\n"
               "J’ai l’honneur de vous informer que le conseil communautaire se réunira le "
               "mercredi 23 septembre 2026 à 18 h 00{lieu}.\nOrdre du jour :\n1. Finances\n")


def test_la_ligne_de_date_de_la_lettre_ne_donne_pas_le_lieu():
    """Piège : « Bourg-Fictif, le 14 septembre » est le siège ; la convocation ne
    nomme pas la salle → lieu vide, le rédacteur ne le devine pas."""
    r = squelette("2026-09-23-cc", None, CONVOCATION.format(lieu=""), {"convocation": "c.txt"}, {})
    assert "lieu" not in r["seance"] and "citation_lieu" not in r["seance"]


def test_le_lieu_se_lit_dans_le_corps_de_la_convocation():
    conv = CONVOCATION.format(lieu=" à la salle polyvalente de Saint-Fictif")
    r = squelette("2026-09-23-cc", None, conv, {"convocation": "c.txt"}, {})
    s = r["seance"]
    assert (s["lieu"], s["source_lieu"]) == ("salle polyvalente de Saint-Fictif", "convocation")
    assert s["citation_lieu"] in conv.replace("\n", " ")


@pytest.mark.parametrize("phrase, lieu", [
    ("Le conseil communautaire s’est réuni le 30 avril 2026 à 18 heures au foyer Louis Fictif "
     "à Valfictif, sous la présidence de Mme la Présidente.", "foyer Louis Fictif à Valfictif"),
    ("L’an deux mille vingt-six, le trente avril, le conseil s’est réuni, sous la présidence "
     "de M. le Maire, en la salle du conseil.", "salle du conseil"),
    ("Le conseil s’est réuni le 3 juin 2026 à Saint-Fictif-de-Majencoules.", "Saint-Fictif-de-Majencoules"),
    ("Date : 3 juin 2026\nLieu : Salle des fêtes de Fictiville\n", "Salle des fêtes de Fictiville"),
    ("Le conseil s’est réuni le 3 juin 2026 à 18 h, Monsieur le Président ouvre la séance.", None),
])
def test_les_formes_du_lieu(phrase, lieu):
    pv = phrase + "\nSUBVENTION\nLe conseil, après en avoir délibéré à l’unanimité :\nDECIDE d’aider.\n"
    r = squelette("2026-06-03-cm", pv, None, {"pv": "data/pv.txt"}, {})
    assert r["seance"].get("lieu") == lieu


def test_completer_relit_l_effectif_et_garde_le_lieu_de_la_convocation():
    conv = CONVOCATION.format(lieu=" à la salle polyvalente de Saint-Fictif")
    r = squelette("2026-09-23-cc", None, conv, {"convocation": "c.txt"}, {})
    c = completer(r, LIASSE, "data/pv.txt", None)
    assert c["seance"]["lieu"] == "salle polyvalente de Saint-Fictif"
    assert c["seance"]["votants"] == 27 and c["seance"]["publie_le"] == "2026-09-30"
    assert any("Quorum : 11" in x for x in _textes(c))


# ── 5. l'objet d'un acte numéroté : jamais le titre de l'acte voisin ─────────
# Forme lue sur une liasse publiée (CC Causses Aigoual Cévennes, séance du
# 23/09/2026, actes 112 à 124) : la marque « N°113/2026 / DEPARTEMENT : GARD »
# ouvre l'acte, puis le cartouche, puis « Objet : … » ; le titre vient APRÈS.

MARQUE_LIASSE = r"N°\s*(\d+)\s*/\s*2026"


def _objets(texte, marque, cle="marque_acte_regex"):
    return {a["n"]: a["objet"] for a in actes(texte, {cle: marque, "seance": {"date": "2026-09-23"}})}


def test_liasse_la_decision_du_voisin_n_est_pas_le_titre(releve):
    """Piège liasse : au-dessus de la marque du 114, la décision du 113."""
    assert _acte(releve, 113)["objet"] == "FONDS DE PEREQUATION"
    assert _acte(releve, 114)["objet"] == "DESIGNATION AU SYNDICAT D’EAU"
    assert "répartition" not in " ".join(a["objet"] for a in releve["actes"])


def test_liasse_sans_libelle_l_objet_reste_vide_plutot_que_faux():
    texte = ("N°10/2026\nDEPARTEMENT : GARD\nLe conseil, après en avoir délibéré,\n"
             "APPROUVE la répartition dérogatoire libre\nsur trois ans.\n"
             "N°11/2026\nDEPARTEMENT : GARD\nLe conseil, après en avoir délibéré,\n"
             "DECIDE de vendre la parcelle.\n"
             "N°12/2026\nDEPARTEMENT : GARD\nLe conseil,\nADOPTE le règlement.\n")
    assert _objets(texte, MARQUE_LIASSE)[11] == "", "la décision et sa suite du n°10"
    assert _objets(texte, MARQUE_LIASSE)[12] == ""


def test_liasse_le_libelle_se_lit_apres_le_cartouche_et_se_poursuit_en_minuscule():
    texte = ("N°10/2026\nDEPARTEMENT : GARD\nPrésents : 25\n" + _tampon(10)
             + "Objet : Fonds national de péréquation des ressources\ncommunales 2026 (FPIC)\n"
             "Le conseil, après en avoir délibéré,\nAPPROUVE le FPIC.\n")
    assert _objets(texte, MARQUE_LIASSE) == {10: "Fonds national de péréquation des ressources "
                                                 "communales 2026 (FPIC)"}


@pytest.mark.parametrize("ligne", ["OBJET : Titre en capitales", "Objet: Titre en capitales",
                                   "objet :  Titre en capitales"])
def test_liasse_le_libelle_ignore_la_casse(ligne):
    texte = f"N°10/2026\n{ligne}\nAPPROUVE le titre.\n"
    assert _objets(texte, MARQUE_LIASSE)[10].lower() == "titre en capitales"


def test_liasse_un_vote_au_dessus_de_la_marque_n_est_pas_un_titre():
    texte = ("N°10/2026\nObjet : Un premier acte\nAPPROUVE le premier acte.\n"
             "Pour : 27 Contre : 0 Abstentions : 0\n"
             "N°11/2026\nDEPARTEMENT : GARD\nLe conseil, après en avoir délibéré,\nDECIDE ceci.\n"
             "Votants : 27\nN°12/2026\nLe conseil,\nDECIDE cela.\n"
             "Adopté à l'unanimité\nN°13/2026\nLe conseil,\nDECIDE autre chose.\n")
    objets = _objets(texte, MARQUE_LIASSE)
    assert objets[10] == "Un premier acte" and objets[11] == "" and objets[12] == ""
    assert objets[13] == ""


def _pv(*actes_):
    return "\n".join(actes_)


def test_pv_le_libelle_d_un_autre_acte_n_est_pas_pris_hors_de_sa_borne():
    """Piège PV : le titre est au-dessus de « Délibération n°N » ; un « Objet : » figure
    dans l'exposé d'un AUTRE acte (avant sa décision, et après celle de l'acte voisin)."""
    texte = _pv("Subvention au club de rugby",
                "Délibération n°65/2026",
                "Après en avoir délibéré à l'unanimité,",
                "APPROUVE la subvention.",
                "Objet : Convention du service de déchets (acte suivant)",
                "Subvention à l'école de musique",
                "Délibération n°66/2026",
                "Objet : Convention de mise à disposition",
                "Après en avoir délibéré à l'unanimité,",
                "APPROUVE la convention.",
                "Tarifs de la piscine",
                "Délibération n°67/2026",
                "La convention a pour objet : de fixer les tarifs.",
                "DECIDE les tarifs.")
    objets = _objets(texte, MARQUE_CC, "marque_acte_regex")
    assert objets[65] == "Subvention au club de rugby", "pas l'Objet : de l'exposé du 66, ni du 65"
    assert objets[66] == "Convention de mise à disposition", "son propre libellé, avant sa décision"
    assert objets[67] == "Tarifs de la piscine", "« a pour objet : » n'est pas un libellé"


def test_pv_temoin_le_titre_suit_la_decision_et_le_vote_de_l_acte_precedent():
    """Dans un PV de la CC le titre de N vient après la décision et le vote de N-1 :
    cette position seule ne vide pas le titre."""
    texte = _pv("Subvention au club de rugby",
                "Délibération n°65/2026",
                "Après en avoir délibéré,",
                "APPROUVE la subvention de 1 200 € au club",
                "pour la saison 2026.",
                "Pour : 27 Contre : 0 Abstentions : 0",
                "Tarifs de la piscine municipale",
                "Délibération n°66/2026",
                "DECIDE les tarifs.")
    objets = _objets(texte, MARQUE_CC, "marque_acte_regex")
    assert objets[66] == "Tarifs de la piscine municipale"


def test_pv_le_titre_sur_deux_lignes_au_dessus_de_la_marque_est_conserve():
    texte = _pv("Délibération n°65/2026", "DECIDE ceci.",
                "Subvention exceptionnelle à l'association",
                "des parents d'élèves",
                "Délibération n°66/2026", "DECIDE cela.")
    assert _objets(texte, MARQUE_CC)[66] == "Subvention exceptionnelle à l'association des parents d'élèves"


def test_marque_ordinale_sortie_inchangee():
    """Le PV municipal : premier bloc de capitales ; un « Objet : » n'y change rien."""
    texte = ("PROCES-VERBAL DU CONSEIL\nSéance du 9 juillet.\nSUBVENTION AU CLUB\nObjet : autre chose\n"
             "Après en avoir délibéré à l'unanimité,\nAPPROUVE la subvention.\n"
             "REGLEMENT DU CIMETIERE\nExposé.\n"
             "Après en avoir délibéré à l'unanimité,\nAPPROUVE le règlement.\n")
    objets = _objets(texte, r"après en avoir délibéré", "marque_acte_ordinale")
    assert objets == {1: "SUBVENTION AU CLUB", 2: "REGLEMENT DU CIMETIERE"}


# ── 5 bis. suite d'un titre « Objet : » à majuscule ; prose au-dessus de la marque ──

def test_liasse_le_titre_se_poursuit_meme_en_majuscule():
    """Forme de la liasse publiée : le n°113 est coupé sur « Communales 2026 (FPIC) »."""
    texte = ("N°113/2026\nDEPARTEMENT : GARD\nPrésents : 25\n" + _tampon(113)
             + "Objet : Fonds national de Péréquation des ressources\nCommunales 2026 (FPIC)\n"
             "Le Conseil communautaire, après avoir délibéré,\nDECIDE de verser le FPIC.\n")
    assert _objets(texte, MARQUE_LIASSE) == {
        113: "Fonds national de Péréquation des ressources Communales 2026 (FPIC)"}


@pytest.mark.parametrize("suite", [
    "ID : 030-200000001-20260923-D113-DE",
    "Envoyé en préfecture le 30/09/2026",
    "Vu le code général des collectivités territoriales",
    "Considérant que le fonds est dû",
    "DECIDE de verser le FPIC.",
    "Pour : 27 Contre : 0 Abstentions : 0",
    "Votants : 27",
    "Adopté à l'unanimité",
    "Présents : 25",
    "Le Conseil communautaire, après avoir délibéré,",
    "Après en avoir délibéré,",
    "",
])
def test_liasse_la_suite_n_est_pas_recollee_si(suite):
    texte = f"N°113/2026\nObjet : Fonds national de Péréquation\n{suite}\nCommunales 2026\nDECIDE ceci.\n"
    assert _objets(texte, MARQUE_LIASSE)[113] == "Fonds national de Péréquation"


def test_liasse_deux_lignes_de_suite_au_plus():
    texte = "N°113/2026\nObjet : Un\nDeux\nTrois\nQuatre\nDECIDE ceci.\n"
    assert _objets(texte, MARQUE_LIASSE)[113] == "Un Deux Trois"


def test_liasse_la_suite_majuscule_ne_vaut_que_pour_un_libelle():
    """Sans libellé, la ligne au-dessus de la marque : la règle des minuscules reste."""
    texte = ("N°10/2026\nDECIDE ceci.\nFonds de péréquation\nCommunales 2026\n"
             "N°11/2026\nDECIDE cela.\n")
    assert _objets(texte, MARQUE_LIASSE)[11] == "Communales 2026"


def test_pv_rubrique_puis_discussion_l_objet_reste_vide():
    """PV de la CC : « II. Adoption du règlement… », discussion, puis la marque."""
    texte = _pv("II.   Adoption du règlement intérieur du Conseil Communautaire",
                "Le Président présente le projet de règlement. Un échange a lieu sur l'article 5,",
                "qui est modifié puis le texte est",
                "arrêté.",
                "Délibération n°81/2026",
                "DECIDE d'adopter le règlement.")
    assert _objets(texte, MARQUE_CC)[81] == ""


@pytest.mark.parametrize("fin", ["arrêté.", "Quelle suite donner ?", "Le point est clos;"])
def test_pv_la_fin_de_discussion_n_est_pas_un_titre(fin):
    texte = _pv("Rubrique", "Délibération n°80/2026", "DECIDE ceci.", "Discussion sur le sujet.",
                fin, "Délibération n°81/2026", "DECIDE cela.")
    assert _objets(texte, MARQUE_CC)[81] == ""


@pytest.mark.parametrize("titre", ["Convention avec la S.A.R.L.", "Désignation de M.",
                                   "II.   Adoption du règlement intérieur du Conseil Communautaire",
                                   "Subvention 2026 (« club de rugby »)"])
def test_pv_un_titre_que_la_prose_ne_vide_pas(titre):
    texte = _pv("Délibération n°65/2026", "DECIDE ceci.", titre, "Délibération n°66/2026", "DECIDE cela.")
    assert _objets(texte, MARQUE_CC)[66] == titre


def test_pv_prose_puis_suite_en_minuscule_reste_vide():
    texte = _pv("Délibération n°65/2026", "DECIDE ceci.", "Le débat est clos.", "des parents d'élèves",
                "Délibération n°66/2026", "DECIDE cela.")
    assert _objets(texte, MARQUE_CC)[66] == ""
