"""Un titre d'acte est un objet, pas un morceau de phrase ; et la rubrique où
l'exécutif rend compte de ses décisions par délégation n'est pas une
délibération.

Relevé le 15/11/2026 sur les `events.json` servis par les trois sites, et rejoué
sur leurs pièces publiques :

- conseil communautaire du 04/03/2026 de Lasalle : cinq « délibérations »
  intitulées « en date du 2/4/2025 et de la commune de… ». Le régime `numerote`
  prenait le renvoi d'un visa coupé en fin de ligne (« N° ⏎ 54/2025 ») pour
  l'en-tête d'un acte ;
- « Décision(s) du Président » comptée en délibération communautaire, trois
  fois à Lasalle et une à Brassac : dernier point numéroté de l'ordre du jour
  (`liste`) ou intertitre en capitales (`capitales`) ;
- « à temps complet », « ET PLATEAUX 2025 », « Val d'Agout » (`acte_final`), « LA
  COMPETENCE EXTRASCOLAIRE A UNE COMMUNE » (`capitales`) : d'un objet écrit sur
  deux lignes, le découpage ne gardait que la seconde.

Les PV de `tests/fixtures/conseils/decoupage/` sont inventés ; ils gardent la
forme des pièces d'origine. Aucun nom n'est réel.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from collectors.pv_parsers import (_acte_final, _capitales, deliberations,
                                   rubrique_de_decisions, titre_en_fragment)

FIXTURES = Path(__file__).parent / "fixtures" / "conseils" / "decoupage"


# ── Le prédicat ─────────────────────────────────────────────────────────────

# Servis comme titres au 15/11/2026 (Lasalle, Saillans, Brassac).
FRAGMENTS_SERVIS = [
    "en date du 2/4/2025 et de la commune de Dourbies en date du 31/3/2025",
    "Avant le 31 Janvier",
    "DE RESULTAT",
    "ET CULTUREL LA CURE",
    "DES EAUX PLUVIALES – RUES DU PONT VIEUX, BASSE, LA CALADETTE",
    "DU CONSEIL MUNICIPAL",
    "D’ELECTRICITE DU GARD",
    "En cas de temps partiel thérapeutique",
    "l'UNANIMITÉ",
    "c RÉSRARRIE 1 | AGENT DE MAITRISE PRINCIPAL 35h pourvu G1",
    "d'y jouer, boire, manger et dormir",
    "à temps complet",
    "ET PLATEAUX 2025",
    "du SCOT \" HTO \" - DE_2022_100_BIS",
    "LE CONSEIL, APRES EN AVOIR DELIBERE, DECIDE",
    "11 ADOPTEE A L'UNANIMITE",
    "3 Cévennes - Terres Solidaires et l’association « La Filature » ADOPTEE AVEC 19 VOIX POUR",
]

# De vrais objets, servis eux aussi, que la règle ne doit pas toucher.
OBJETS = [
    "LA BERGERIE : CESSION DE FONDS ET BAIL COMMERCIAL",
    "vote de crédits supplémentaires fonctionnement Budget annexe Enfance",
    "renouvellement des membres de la commission de contrôle des listes électorales",
    "Délégations consenties au Maire par le Conseil Municipal",
    "Décision modificative n°1 - Budget annexe ENFANCE 2023",
    "Avant-projet sommaire de la traverse du bourg",
    "Adhésion à la Mission Locale Garrigues et Cévennes",
    "N°4182 : Délégations consenties au Maire par le Conseil Municipal",
    "RIFSEEP",
    "Désignation des délégués au syndicat d'électrification",
]


@pytest.mark.parametrize("titre", FRAGMENTS_SERVIS)
def test_un_morceau_de_phrase_est_reconnu(titre):
    assert titre_en_fragment(titre)


@pytest.mark.parametrize("titre", OBJETS)
def test_un_objet_n_est_pas_un_fragment(titre):
    assert not titre_en_fragment(titre)
    assert not rubrique_de_decisions(titre)


RUBRIQUES = [
    "Décision du Président",
    "Décisions du président",
    "DECISIONS DU PRESIDENT",
    "Décisions de la Présidente",
    "Décisions du Maire",
    "Compte rendu des décisions du Maire",
    "Information sur les décisions prises par délégation",
    "Décisions prises en vertu de l'article L.2122-22 du CGCT",
    "Décisions du Bureau",
    "12. Décisions du Maire",
    "Information au conseil : décisions prises par le maire",
    # Reconnues à 686afb1 : la règle avec espaces ne perd rien.
    "Décisions sur délégation du conseil municipal",
    "Décision sur délégation",
    "Information sur les décisions sur délégation",
    "Décisions sur le fondement de l'article L2122-22",
]

PAS_DES_RUBRIQUES = [
    "Délégations au Président",
    "DELEGATION DE SIGNATURE DU CONSEIL MUNICIPAL AU MAIRE",
    "Décision modificative n°2 – Budget principal",
    "DECISION DU CONSEIL COMMUNAUTAIRE DE LA CCSVP POUR LA ZONE D'ACTIVITE",
    "Délibération relative aux délégations consenties au Maire par le Conseil Municipal",
    # Une collectivité peut intituler ainsi ses délibérations : seule la suite
    # d'une rubrique la lit comme un point de la rubrique.
    "Décision n°3 : approbation du compte administratif 2024",
    "Décisions modificatives n°2 – délégation de service public",
]

# Préfixe libre et queue collée : reconnues à 686afb1 par la règle avec espaces.
RUBRIQUES_A_QUEUE_COLLEE = [
    "Information au conseil : décisions prises par le maire — signaturedelaconventionavecl'association",
    "Point 5 : décisions du maire, renouvellementdelaconventiondepartenariat",
    "Décisions sur la base de la délégation : renouvellementdelaconventiondepartenariat",
    "Décisions sur les marchés conclus en vertu de la délégationduconseilmunicipal",
]


@pytest.mark.parametrize("titre", RUBRIQUES)
def test_la_rubrique_des_decisions_est_reconnue(titre):
    assert rubrique_de_decisions(titre)


@pytest.mark.parametrize("titre", PAS_DES_RUBRIQUES)
def test_une_deliberation_sur_les_delegations_n_est_pas_la_rubrique(titre):
    assert not rubrique_de_decisions(titre)


# ── Le découpage, à la source ───────────────────────────────────────────────

def test_le_renvoi_d_un_visa_n_ouvre_pas_un_acte():
    """« N° ⏎ 54/2025 en date du… » : le régime numéroté s'efface, et l'ordre du
    jour numéroté découpe la séance."""
    actes = deliberations((FIXTURES / "pv-cc-visa-coupe.txt").read_text())
    titres = [a["titre"] for a in actes]
    assert not any(titre_en_fragment(t) for t in titres), titres
    assert not any(a.get("numero_seance") in ("54/2025", "58/2025") for a in actes)
    assert len(actes) == 4
    assert titres[3] == "Tarifs du service public d'assainissement non collectif 2026"


def test_un_vrai_en_tete_numerote_reste_un_acte():
    texte = """
12/2026 : n° 4713 : Budget primitif 2026
Vu la délibération N°
11/2026 en date du 3 février 2026 fixant les tarifs ;
Le conseil adopte le budget primitif.
13/2026 : n° 4714 : Tarifs de la cantine
Le conseil fixe les tarifs.
14/2026 : n° 4715 : Subventions aux associations
Le conseil attribue les subventions.
"""
    actes = deliberations(texte)
    assert [a["numero_seance"] for a in actes] == ["12/2026", "13/2026", "14/2026"]
    assert "11/2026 en date du 3 février" in actes[0]["texte"]


def test_la_rubrique_des_decisions_part_entiere():
    """Ni acte, ni texte recollé à l'acte d'avant : le nom de l'agent recruté
    par décision ne doit sortir nulle part (avis DPO du 14/11/2026)."""
    actes = deliberations((FIXTURES / "pv-cc-rubrique-decisions.txt").read_text())
    titres = [a["titre"] for a in actes]
    assert not any(rubrique_de_decisions(t) for t in titres), titres
    assert "Création d'un emploi permanent d'agent d'entretien" in titres
    assert not any("FICTIVE" in a["texte"] or "Paysages Fictifs" in a["texte"] for a in actes)


def test_la_suite_de_la_rubrique_ne_se_recolle_pas_a_l_acte_d_avant():
    """Relecture vigie-securite du 15/11/2026 : après la rubrique jetée, ses
    alinéas (« de louer le logement… à M. … ») étaient recollés à l'acte publié
    juste AVANT elle, noms et montants compris."""
    texte = """Secrétaire de séance : Dominique BRUNESQUE
• Tarifs de la cantine scolaire : le conseil fixe les tarifs pour 2026.
• Décisions du Maire : le Maire rend compte de ses décisions.
• de louer le logement de l'école : à M. Albert FICTIF pour 420 € par mois.
• de recruter un agent contractuel : Mme Claude EXEMPLE.
• Subventions aux associations : le conseil attribue les subventions.
"""
    from collections import Counter
    ecarts = Counter()
    actes = deliberations(texte, pagine=False, ecarts=ecarts)
    assert [a["titre"] for a in actes] == ["Tarifs de la cantine scolaire",
                                          "Subventions aux associations"]
    assert not any("FICTIF" in a["texte"] or "EXEMPLE" in a["texte"] or "420" in a["texte"]
                   for a in actes)
    assert ecarts == Counter({"rubrique_des_decisions": 1, "suite_de_rubrique": 2})


def test_une_decision_numerotee_ne_compte_que_dans_la_rubrique():
    texte = """Secrétaire de séance : Dominique BRUNESQUE
• Tarifs de la cantine scolaire : le conseil fixe les tarifs pour 2026.
• Décisions du Maire : le Maire rend compte de ses décisions.
• Décision n°2026-12 : bail du logement de l'école à M. Albert FICTIF.
• Subventions aux associations : le conseil attribue les subventions.
• Décision n°3 de la commission : avis sur le marché.
"""
    titres = [a["titre"] for a in deliberations(texte, pagine=False)]
    assert titres == ["Tarifs de la cantine scolaire", "Subventions aux associations",
                      "Décision n°3 de la commission"]


def test_la_rubrique_en_capitales_ne_devient_pas_un_acte():
    texte = """Secrétaire de séance : Dominique BRUNESQUE
DECISIONS DU PRESIDENT
Le Président rend compte des décisions prises dans le cadre de sa délégation.
Décision n°4 : recrutement d'un agent contractuel.
"""
    assert _capitales(texte, pagine=False) and deliberations(texte, pagine=False) == []


def test_un_titre_en_capitales_sur_deux_lignes_reste_entier():
    texte = """Secrétaire de séance : Dominique BRUNESQUE
RENOUVELLEMENT CONVENTION DE DELEGATION PARTIELLE DE
LA COMPETENCE EXTRASCOLAIRE A UNE COMMUNE
Monsieur le maire rappelle que la commune délibère chaque année sur cette délégation.
Le conseil municipal approuve la convention.
AVENANT N° 5 CONVENTION DE DELEGATION – EAU ET
ASSAINISSEMENT
Monsieur le Maire présente l'avenant.
Le conseil municipal approuve l'avenant.
"""
    titres = [a["titre"] for a in deliberations(texte, pagine=False)]
    assert titres == [
        "RENOUVELLEMENT CONVENTION DE DELEGATION PARTIELLE DE LA COMPETENCE "
        "EXTRASCOLAIRE A UNE COMMUNE",
        "AVENANT N° 5 CONVENTION DE DELEGATION – EAU ET ASSAINISSEMENT",
    ]


def test_une_ligne_de_vote_en_capitales_ne_rejoint_pas_le_titre_suivant():
    """Relecture vigie-securite du 15/11/2026 : la ligne capitale d'un corps se
    collait devant l'objet suivant, nom compris."""
    texte = """Secrétaire de séance : Dominique BRUNESQUE
TARIFS DE LA CANTINE SCOLAIRE
Le conseil fixe les tarifs.
VOTE : UNANIMITE DES PRESENTS SAUF FICTIF ALBERT
RENOUVELLEMENT CONVENTION DE DELEGATION
Le conseil renouvelle la convention.
SECRETAIRE DE SEANCE FICTIF ALBERT
ET DESIGNATION DES DELEGUES AU SYNDICAT
Le conseil désigne ses délégués.
LISTE DES MEMBRES : FICTIF ALBERT, EXEMPLE BERNARD
DE LA COMMISSION DES FINANCES
Le conseil arrête la composition de la commission.
EXONERATION PERMANENTE TAXE D'HABITATION POUR LES GITES
ET MEUBLES DE TOURISME
Le conseil exonère les gîtes.
"""
    titres = [a["titre"] for a in deliberations(texte, pagine=False)]
    assert "RENOUVELLEMENT CONVENTION DE DELEGATION" in titres
    assert not any("FICTIF" in t for t in titres), titres
    assert "EXONERATION PERMANENTE TAXE D'HABITATION POUR LES GITES ET MEUBLES DE TOURISME" in titres


def test_un_entete_de_tableau_n_est_pas_rejoint_en_titre():
    """Cinq lignes capitales de suite sont un tableau : la dernière, « DE
    RESULTAT », est refusée et son texte rendu à l'acte qu'elle coupait."""
    texte = """Secrétaire de séance : Dominique BRUNESQUE
AFFECTATIONS DES COMPTES DE RESULTATS 2025
Le conseil affecte les résultats comme suit.
SECTIONS RESULTAT RESULTAT CHIFFRES A IMPUTATION
CA 2024 DE PRENDRE EN REPRENDRE
L’EXERCICE COMPTE POUR
2025 L’AFFECTATION
DE RESULTAT
Fonctionnement 85 274,30 € reporté.
"""
    actes = deliberations(texte, pagine=False)
    assert [a["titre"] for a in actes] == ["AFFECTATIONS DES COMPTES DE RESULTATS 2025"]
    assert "85 274,30" in actes[0]["texte"]


def test_un_objet_sur_deux_lignes_avant_l_identifiant():
    texte = """Délibérations du conseil :
Avis en tant qu'autorité organisatrice concernant la crèche associative de
Fictiville (N° DE_2026_001)
Le Conseil communautaire émet un avis favorable.
Délibération sur le compte unique financier - CTE DE CNES DES TROIS VALLEES
ET PLATEAUX 2025 (N° DE_2026_002)
Le Conseil communautaire approuve le compte.
Création d'un emploi permanent d'agent technique – grade d'adjoint technique contractuel
à temps complet (N° DE_2026_003)
Le Conseil communautaire crée l'emploi.
Délibération approuvant la modification n°1 du PLUi des
Trois Vallées (N° DE_2026_004)
Le Conseil communautaire approuve.
"""
    titres = [a["titre"] for a in _acte_final(texte)]
    assert titres == [
        "Avis en tant qu'autorité organisatrice concernant la crèche associative de Fictiville",
        "Délibération sur le compte unique financier - CTE DE CNES DES TROIS VALLEES "
        "ET PLATEAUX 2025",
        "Création d'un emploi permanent d'agent technique – grade d'adjoint technique "
        "contractuel à temps complet",
        "Délibération approuvant la modification n°1 du PLUi des Trois Vallées",
    ]


def test_une_ligne_de_vote_ou_de_personne_n_entre_pas_dans_le_titre():
    """Relecture vigie-securite du 15/11/2026 : `_acte_final` remontait sur la
    ligne de vote, ou sur une ligne qui nomme quelqu'un, d'un acte court."""
    texte = """Délibérations du conseil :
Tarifs de la piscine (N° DE_2026_020)
Le Conseil communautaire fixe les tarifs.
Abstention : Fictif Albert, Exemple Bernard
Recrutement saisonnier (N° DE_2026_021)
Le Conseil communautaire crée deux emplois saisonniers.
Indemnité versée à Mme Fictive Claire
Voirie communale (N° DE_2026_022)
Le Conseil communautaire approuve le programme.
Secrétaire de séance Fictif Albert
Fonds de concours (N° DE_2026_023)
Le Conseil communautaire attribue les fonds.
Bail consenti à Albert Fictif, artisan
Tarifs des gîtes (N° DE_2026_024)
Le Conseil communautaire fixe les tarifs.
"""
    assert [a["titre"] for a in _acte_final(texte)] == [
        "Tarifs de la piscine", "Recrutement saisonnier", "Voirie communale",
        "Fonds de concours", "Tarifs des gîtes"]


def test_un_titre_d_une_ligne_ne_prend_pas_la_fin_de_l_acte_d_avant():
    texte = """Délibérations du conseil :
Vote des taux de la fiscalité directe locale (N° DE_2026_010)
Le Conseil communautaire fixe les taux.
DONNE POUVOIR à la Présidente pour signer tout acte afférent.
Tarifs (N° DE_2026_011)
Le Conseil communautaire fixe les tarifs.
"""
    assert [a["titre"] for a in _acte_final(texte)] == [
        "Vote des taux de la fiscalité directe locale", "Tarifs"]


# ── Le garde du snapshot, pour ce que la base garde d'avant ────────────────

def _acte(base, type_, titre, date, texte, numero_seance=None):
    from scripts.snapshot.socle import RULES
    source = sorted(RULES["events"]["public_sources"])[0]
    meta = {"regime": "liste", **({"numero_seance": numero_seance} if numero_seance else {})}
    return base.execute(
        "INSERT INTO events (type, date, title, content, source, source_url, metadata)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (type_, date, titre, texte, source, "https://exemple.invalid/pv-cc.pdf",
         json.dumps(meta))).lastrowid


def test_le_snapshot_ecarte_fragments_et_rubrique_avec_un_motif(base):
    """Les fiches écrites avant la correction restent en base jusqu'au
    redécoupage : le snapshot ne les publie pas, et compte pourquoi."""
    from scripts.snapshot.actes import etape_actes
    rubrique = _acte(base, "deliberation_cc", "Décisions du Président", "2021-05-26",
                     "Décision n°2021-13 : recrutement de Mme Claude FICTIVE.")
    visa = _acte(base, "deliberation_cc",
                 "en date du 2/4/2025 et de la commune de Fictiville en date du 31/3/2025",
                 "2026-03-04", "actant l'avenant n°2 à la convention de délégation.",
                 numero_seance="54/2025")
    capitale = _acte(base, "deliberation", "DE RESULTAT", "2026-04-13", "Fonctionnement.")
    vrai = _acte(base, "deliberation_cc", "Fonds de concours aux communes – Investissements 2021",
                 "2021-05-26", "Le conseil attribue les fonds de concours.")
    # Un flux lu dans la rubrique (une décision du maire) et un flux lu dans le
    # fragment (attesté par le procès-verbal).
    from scripts.snapshot.socle import RULES
    flux_rubrique = base.execute(
        "INSERT INTO financial_flows (type, year, amount, event_id, description, source,"
        " confidence) VALUES ('bail', 2021, 420, ?, 'Loyer', 'CM', ?)",
        (rubrique, sorted(RULES["confidence"]["public"])[0])).lastrowid
    flux_fragment = base.execute(
        "INSERT INTO financial_flows (type, year, amount, event_id, description, source,"
        " confidence) VALUES ('subvention', 2026, 42000, ?, 'Budget', 'CM', ?)",
        (visa, sorted(RULES["confidence"]["public"])[0])).lastrowid
    # Une décision par délégation venue de son portail garde son propre chemin.
    decision = _acte(base, "decision_delegation_cc", "Décision du Président : marché de voirie",
                     "2026-03-04", "")
    base.commit()

    exclusions = defaultdict(Counter)
    sortie = etape_actes(base, {}, set(), lambda t: t, exclusions)
    publies = {e["id"] for e in sortie["public_events"]}

    assert vrai in publies
    assert not {rubrique, visa, capitale} & publies
    assert not {rubrique, visa, capitale} & set(sortie["textes_extraits"])
    assert exclusions["events"]["rubrique_decisions_par_delegation"] == 1
    assert exclusions["events"]["titre_fragment_de_phrase"] == 2
    motifs = {e["id"]: e["reason"] for e in sortie["event_exclusions"]}
    assert motifs[rubrique] == "rubrique_decisions_par_delegation"
    assert motifs[visa] == motifs[capitale] == "titre_fragment_de_phrase"
    # Le garde ne touche que les délibérations : la décision n'est pas écartée
    # pour ce motif (elle suit les règles de `collectors/decisions.py`).
    assert motifs.get(decision) not in ("rubrique_decisions_par_delegation",
                                        "titre_fragment_de_phrase")

    from scripts.snapshot.argent import etape_flux
    flux = etape_flux(base, {}, [], set(), set(), set(), lambda t: t, exclusions,
                      sortie["public_events"], sortie["pieces_des_fragments"],
                      sortie["actes_de_rubrique"])
    sortis = {f["id"]: f for f in flux["flow_rows"]}
    assert flux_rubrique not in sortis
    assert exclusions["flows"]["lu_dans_une_rubrique_de_decisions"] == 1
    assert sortie["pieces_des_fragments"][visa] == "https://exemple.invalid/pv-cc.pdf"
    assert flux_fragment in sortis


# ── Mots collés, débris de tableau, item d'énumération, nom de la commune ───
#
# Relevé du 16/11/2026 sur les `events.json` servis (Saillans surtout). Chaque
# titre ci-dessous est servi tel quel par un des trois sites.

ITEMS_D_ENUMERATION = [
    "une veille technologique en vue d’optimiser la diffusion d'informations "
    "relatives aux techniques multimédias",
    "un logiciel de gestion des cimetières",
    # « des » était déjà pris par `_MOTS_DE_SUITE`.
    "des travaux de voirie",
]

MOTS_COLLES = [
    "uneparticipation pourlebranchementau futurréseau de chaleur",
    "laparticipationau raccordementà l'égout",
    "lesrecettesfiscales issues de laTaxed'Aménagement",
    "Acceptationetsignatureaccordcadrepourl'égliseSaintGéraud",
    "Conventiondemiseà dispositiond^unlocal",
    "Décisionsmodificativesau budgetgénéralfM14l",
    "TABLEAUDESEMPLOISPERMANENTSATEMPSNONCOMPLET",
]

DEBRIS_DE_TABLEAU = [
    "M 49). | |",
    "HT TVA TIC",
    "AT TVA TIC",
    "HT TVA TIC L",
    "_ PRINCIPAL",
    "TTC HT TVA TIC HT TVA TTC HT TVA TIC",
    "DE_2022_061)",
    "L 2123-24-1 – II)",
    # Un seul mot et un signe, ou un mot fait de lettres romaines : écartés.
    "_TARIFS",
    "M57)",
    "CIVIL)",
    "HT TTC",
    # La barre cassée « ¦ » est une barre verticale mal lue.
    "M 49). ¦ ¦",
    "¦ PRINCIPAL",
]

# Une `)` orpheline seule ne sauve un titre que s'il porte un vrai sigle (avis
# vigie-securite du 17/11/2026) : ni colonne de montant, ni mot-outil, ni unité, ni
# variante à espaces d'un débris, ni double parenthèse. Certains sont déjà refusés
# à la source (`_titre_plausible`) : seul le garde du snapshot est testé ici.
DEBRIS_A_PARENTHESE_SEULE = [
    "HT)", "€ HT)", "12,50 HT)", "DE 2022 061)", "L2123-24 al. II)", "12 ET)",
    "mm)", "12 cm)", "x 12 EF))", "DM n°3))",
]


@pytest.mark.parametrize("titre", DEBRIS_A_PARENTHESE_SEULE)
def test_une_parenthese_orpheline_sans_vrai_sigle_reste_un_debris(titre):
    from collectors.pv_parsers import titre_en_debris_de_tableau
    assert titre_en_debris_de_tableau(titre)


# De vrais objets, dont certains portent un signe de débris ou une faute
# d'océrisation : le garde ne touche ni aux titres courts sans débris, ni aux
# noms propres, ni à une faute isolée.
OBJETS_QUE_LE_GARDE_NE_TOUCHE_PAS = OBJETS + [
    "renouvellement des membres de la commission de contrôle des listes électorales",
    "mise à disposition d’un local – bail civil",
    "Une convention de partenariat avec la commune",
    "UNE CONVENTION DE PARTENARIAT",
    "Un schéma directeur pour l'eau potable",
    "PLU", "TARIFS", "INCIVILITES", "MATERNELLES", "COLUMBARIUM", "EAU POTABLE",
    "SOBRREDUTET", "Parité", "1er adjoint",
    "vote de crédits supplémentaires",
    "LA BERGERIE : CESSION DE FONDS",
    # Noms propres et sigles : une capitale au milieu d'un mot n'est pas une soudure.
    "Adhésion au SICTOBA", "Convention avec McDonald", "Réseau eARDECHE",
    "Mise en place PayFip", "Convention VéloDrôme", "Bail avec l’association LaFilature du Mazel",
    "Convention PayFiP : service de paiement en ligne",
    # Les plus longs mots d'un titre d'acte, sans soudure.
    "Professionnalisation des agents pluridisciplinaire",
    "ÉTUDE PLURIPROFESSIONNELLE DE SANTÉ",
    # Titres courts : ni la ponctuation finale ni une numérotation de tête n'est un
    # signe de débris (décision de Julien, 16/11/2026).
    "IV) BUDGET", "II) TARIFS", "a) TARIFS", "3°) PLU", "1°) TARIFS", "TARIFS |", "PLU |", "Parité |", "1) PLU", "2) TARIFS", "123) TARIFS", "CCAS)", "SIVOM)",
    # Sigles de deux lettres : titres courts sans débris (relecteur du code, 17/11/2026).
    "1) DM", "2) CA", "3) BP", "IV) DM", "DM)", "CR)", "BP 2026)", "2) DM n°3",
    # Un signe de débris dans un titre qui garde son objet.
    "DM n°3 | SUPPLEMENT RENOVATION APPARTEMENT",
    "Acquisition de la parcelle Section AD n°78_Commune de Vabre",
    "_INDEMNITÉ DU RECEVEUR MUNICIPAL POUR 2016",
    "Modification des statuts du PETR Causses et Cévennes |",
    "Budget annexe (assainissement) 2026",
    # Une faute isolée sur un vrai objet.
    "Création d’un emploi permanent de Directeur/trice d’Accueil de Loisirs — "
    "à temps non completà raison de 10h hebdomadaires",
    "Tarif des HT et TTC applicables aux repas",
]


@pytest.mark.parametrize("titre", ITEMS_D_ENUMERATION)
def test_un_item_d_enumeration_en_minuscule_est_un_fragment(titre):
    assert titre_en_fragment(titre)


@pytest.mark.parametrize("titre", MOTS_COLLES)
def test_des_mots_colles_sont_reconnus(titre):
    from collectors.pv_parsers import titre_en_mots_colles
    assert titre_en_mots_colles(titre)


@pytest.mark.parametrize("titre", DEBRIS_DE_TABLEAU)
def test_un_debris_de_tableau_est_reconnu(titre):
    from collectors.pv_parsers import titre_en_debris_de_tableau
    assert titre_en_debris_de_tableau(titre)


@pytest.mark.parametrize("titre", OBJETS_QUE_LE_GARDE_NE_TOUCHE_PAS)
def test_un_vrai_objet_n_est_ni_colle_ni_debris(titre):
    from collectors.pv_parsers import titre_en_debris_de_tableau, titre_en_mots_colles
    assert not titre_en_fragment(titre)
    assert not titre_en_mots_colles(titre)
    assert not titre_en_debris_de_tableau(titre)


@pytest.mark.parametrize("titre", ITEMS_D_ENUMERATION)
def test_le_decoupage_refuse_l_item_d_enumeration_a_la_source(titre):
    from collectors.pv_parsers import _titre_plausible
    assert not _titre_plausible(titre)


@pytest.mark.parametrize("titre", MOTS_COLLES + DEBRIS_DE_TABLEAU)
def test_le_decoupage_ne_recolle_pas_un_acte_a_mots_colles(titre):
    """Un titre refusé par `_titre_plausible` est recollé à l'acte d'avant : un
    vrai objet à l'océrisation défectueuse y perdrait son acte. Ces titres ne
    sont écartés qu'au snapshot, qui ne recolle rien."""
    from collectors.pv_parsers import _titre_plausible
    assert _titre_plausible(titre)


@pytest.mark.parametrize("titre", OBJETS_QUE_LE_GARDE_NE_TOUCHE_PAS)
def test_le_decoupage_garde_les_vrais_objets(titre):
    from collectors.pv_parsers import _titre_plausible
    assert _titre_plausible(titre)


# En-têtes de tampon servis à Lasalle le 17/11/2026 (extraits scannés, 2021-2023) :
# l'océrisation lit le tampon de la préfecture comme un titre.
ENTETES_DE_TAMPON = [
    "oual ARRONDISSEMENT : LE VIGAN", "goual ARRONDISSEMENT: LE VIGAN",
    "oual ARRONDISSEMENT :| LE VIGAN", "T£ g oual ARRONDISSEME:N : LTE VIGAN",
    "goual ARRONDISSEME:N LTE VIGAN", "G@ PLS oual ARRONDISSEME:N: LTE VIGAN",
    "ARRONDISSEMENT: LE VIGAN", "AN°138/2022 À oual DEPARTEMENT : GARD",
    "Æ DEPARTEMENT : GARD N°43/2021", "Æ, — DEPARTEMENT : GARD Na°64/2021",
    "DEPARTEMENT : GARD N°132/2023", "4 dl DEPARTEMENT : GARD", "L DEPARTEMENT : GARD",
    # Lectures plus abîmées du même tampon.
    "Zoual DEPARTEM:E GNARTD", "ARRONDISSE: MLE EVINGATN", "Æ DEPARTEMENT : GARD N°%61/2021",
    "ER nr DEPARTEMENT : GARD N°49D/2021", "Æ DEPARTEMENT : GARD IN°107/2021",
    # Pas de lieu en dur : le tampon de Saillans ou de Brassac a la même forme.
    "DEPARTEMENT : DROME N°12/2022", "ARRONDISSEMENT : CASTRES", "ARRONDISSEMENT : DIE",
    # Noms composés (avis vigie-securite, 17/11/2026) : la longueur se mesure par pièce.
    "DEPARTEMENT : ILLE-ET-VILAINE N°12/2022", "DEPARTEMENT : BOUCHES-DU-RHONE",
    "DEPARTEMENT : PYRENEES-ATLANTIQUES N°3/2021", "DEPARTEMENT : MEURTHE-ET-MOSELLE",
    "ARRONDISSEMENT : SAINT-JEAN-DE-MAURIENNE", "ARRONDISSEMENT : CHALONS-EN-CHAMPAGNE",
    "DEPARTEMENT : ALPES DE HAUTE PROVENCE", "ARRONDISSEMENT : NIMES",
    # Tampon lu sur une seule ligne ; numéro sans barre oblique.
    "DEPARTEMENT : GARD ARRONDISSEMENT : LE VIGAN",
    "DEPARTEMENT : GARD N°132/2023 ARRONDISSEMENT : LE VIGAN", "DEPARTEMENT : GARD N°2023-132",
    # Ponctuation finale après le numéro ; numéro sans année (relecture de code, 17/11/2026).
    "DEPARTEMENT : GARD N°43/2021.", "DEPARTEMENT : GARD N°43", "DEPARTEMENT : GARD N°43.",
    # Le RÉSIDU déclaré, assumé : un objet en capitales « DEPARTEMENT : 1 à 3 mots courts »
    # dont aucun mot n'est dans `_MOTS_D_OBJET` a la forme d'un tampon et est écarté
    # (titre douteux écarté plutôt que faux). Aucun n'est servi le 17/11/2026 ; la
    # liste de mots d'objet n'est pas étendue, ni une liste de sigles.
    "DEPARTEMENT : RAPPORT ANNUEL", "SDIS DEPARTEMENT : CASERNE",
]

# Des vrais objets qui nomment le département : ni mot-clé seul, ni deux-points
# derrière un vrai mot, ni objet derrière le deux-points.
OBJETS_QUI_NOMMENT_LE_DEPARTEMENT = [
    "DEMANDE DE SUBVENTION AU DEPARTEMENT",
    "Convention avec le Département : transport scolaire",
    "FÊTE DE LA CHÂTAIGNE 2020 – DEMANDE DE SUBVENTION A LA REGION ET AU DEPARTEMENT",
    "Délégués auprès du SDET (Syndicat Départemental d’Energie du Tarn)",
    "AGENCE DEPARTEMENTALE : CONVENTION", "SUBVENTION DEPARTEMENT : AMENAGEMENT ALLEE",
    "DEPARTEMENT : demande de subvention pour la salle des fêtes",
    "N° 3807 : Aménagement Allée du Château : demande de subvention Département",
    "Conseil Départemental du Gard", "DEPARTEMENT", "ARRONDISSEMENT",
    "Modification des limites de l’arrondissement : transfert de la commune",
    # Un mot d'objet ou un mot courant avant le mot-clé : pas un débris (avis vigie-securite).
    "AIDE DU DEPARTEMENT : VOIRIE", "SDE DEPARTEMENT : ECLAIRAGE",
    "Objet : DEPARTEMENT : CONVENTION ATESAT", "1) DEPARTEMENT : AMENDES DE POLICE",
    "DEPARTEMENT : SUBVENTION VOIRIE", "DEPARTEMENT : PLAN NEIGE", "DEPARTEMENT : ADHESION AU SDIS",
    "ARRONDISSEMENT : REDECOUPAGE", "DEPARTEMENT : DM N°1/2023",
    "DEPARTEMENT : GARD ARRONDISSEMENT : subvention voirie des communes",
    # Sans aucun mot de `_MOTS_D_OBJET` : c'est la règle de FORME qui garde (avant le
    # mot-clé, un mot de plus de six lettres, un deux-points ou une numérotation ;
    # après, plus de trois mots, un chiffre ou un mot en minuscules).
    "Rapport annuel du DEPARTEMENT : GARD", "Compte rendu DEPARTEMENT : GARD",
    "1) DEPARTEMENT : GARD", "Point : DEPARTEMENT : GARD",
    "DEPARTEMENT : GARD ET LE VIGAN ET NIMES ET ALES",
    "DEPARTEMENT : rapport annuel", "DEPARTEMENT : GARD 2024",
]

# Le point sans vote, à la casse, aux accents, à la ponctuation et à une
# numérotation de tête près.
QUESTIONS_DIVERSES = [
    "Questions diverses", "QUESTIONS DIVERSES", "Questions Diverses", "questions diverses :",
    "7) QUESTIONS DIVERSES", "9 - Questions diverses", "Questions diverses.", "Questions  diverses |",
    "IX - Questions diverses", "Point 7 : questions diverses", "Question diverse",
    # Les variantes que la liste de l'actualité (`TITRES_VIDES`) tient pour vides.
    "Informations et questions diverses", "Questions et informations diverses",
]
# « Questions diverses » avec un objet reste un acte.
QUESTIONS_DIVERSES_AVEC_OBJET = [
    "Questions diverses : vente du tracteur", "Questions diverses 2024",
    "Questions diverses et vente du tracteur", "Questions", "Réponse aux questions diverses du public",
    "QUESTIONS DIVERSES ET INFORMATIONS", "Points divers",
]


@pytest.mark.parametrize("titre", ENTETES_DE_TAMPON)
def test_un_entete_de_tampon_est_reconnu(titre):
    from collectors.pv_parsers import titre_en_entete_de_tampon
    assert titre_en_entete_de_tampon(titre)


@pytest.mark.parametrize("titre", OBJETS_QUI_NOMMENT_LE_DEPARTEMENT + OBJETS_QUE_LE_GARDE_NE_TOUCHE_PAS)
def test_un_objet_n_est_pas_un_entete_de_tampon(titre):
    from collectors.pv_parsers import titre_en_entete_de_tampon, titre_questions_diverses
    assert not titre_en_entete_de_tampon(titre)
    assert not titre_questions_diverses(titre)


@pytest.mark.parametrize("titre", QUESTIONS_DIVERSES)
def test_le_point_questions_diverses_est_reconnu(titre):
    from collectors.pv_parsers import titre_questions_diverses
    assert titre_questions_diverses(titre)


@pytest.mark.parametrize("titre", QUESTIONS_DIVERSES_AVEC_OBJET)
def test_questions_diverses_avec_un_objet_reste_un_acte(titre):
    from collectors.pv_parsers import titre_questions_diverses
    assert not titre_questions_diverses(titre)


@pytest.mark.parametrize("titre", ENTETES_DE_TAMPON + QUESTIONS_DIVERSES)
def test_le_decoupage_ne_recolle_pas_un_entete_de_tampon(titre):
    """Comme les mots collés : écarté au snapshot, jamais recollé à l'acte d'avant."""
    from collectors.pv_parsers import _titre_plausible
    assert _titre_plausible(titre)


def test_le_snapshot_ecarte_l_entete_de_tampon_et_le_point_sans_vote():
    from collectors.config import COMMUNE_NAME
    from scripts.snapshot.actes import motif_de_titre_douteux as motif

    for titre in ENTETES_DE_TAMPON:
        assert motif(titre, COMMUNE_NAME) == "titre_entete_de_tampon", titre
    for titre in QUESTIONS_DIVERSES:
        assert motif(titre, COMMUNE_NAME) == "titre_questions_diverses", titre
    for titre in OBJETS_QUI_NOMMENT_LE_DEPARTEMENT + QUESTIONS_DIVERSES_AVEC_OBJET:
        assert motif(titre, COMMUNE_NAME) is None, titre


def test_le_snapshot_ecarte_chaque_famille_avec_son_motif():
    """Le nom de la commune se lit dans la configuration de l'instance, à la
    casse et aux accents près ; un titre court sans débris reste publié."""
    from collectors.config import COMMUNE_NAME
    from scripts.snapshot.actes import motif_de_titre_douteux as motif

    assert motif("une veille technologique en vue d’optimiser la diffusion", COMMUNE_NAME) \
        == "titre_fragment_de_phrase"
    assert motif("laparticipationau raccordementà l'égout", COMMUNE_NAME) == "titre_mots_colles"
    assert motif("M 49). | |", COMMUNE_NAME) == "titre_debris_de_tableau"
    assert motif("HT TVA TIC", COMMUNE_NAME) == "titre_debris_de_tableau"
    for nom in (COMMUNE_NAME, COMMUNE_NAME.upper(), COMMUNE_NAME.lower(), f" {COMMUNE_NAME} :",
                f"2) {COMMUNE_NAME.upper()}", f"1) {COMMUNE_NAME}"):
        assert motif(nom, COMMUNE_NAME) == "titre_nom_de_la_commune"
    assert motif("Testonville", "Rochefort-en-Terre") is None
    assert motif("Rochefort en terre", "Rochefort-en-Terre") == "titre_nom_de_la_commune"
    assert motif("Convention avec la commune de Testonville", COMMUNE_NAME) is None
    for titre in OBJETS_QUE_LE_GARDE_NE_TOUCHE_PAS:
        assert motif(titre, COMMUNE_NAME) is None, titre


def test_le_snapshot_compte_les_nouveaux_motifs_et_garde_le_renvoi_a_la_piece(base):
    from collectors.config import COMMUNE_NAME
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    from scripts.snapshot.socle import RULES

    ecartes = {
        "titre_fragment_de_phrase": _acte(base, "deliberation", "une veille technologique en vue de "
                                          "diffuser des informations", "2014-10-03", "Texte A."),
        "titre_mots_colles": _acte(base, "deliberation", "laparticipationau raccordementà l'égout",
                                   "2016-02-05", "Texte B."),
        "titre_debris_de_tableau": _acte(base, "deliberation", "M 49). | |", "2019-12-13", "Texte C."),
        "titre_nom_de_la_commune": _acte(base, "deliberation_cc", COMMUNE_NAME.upper(),
                                         "2024-10-01", "Texte D."),
        "titre_entete_de_tampon": _acte(base, "deliberation_cc", "Æ DEPARTEMENT : GARD N°43/2021",
                                        "2021-04-14", "Texte E."),
        "titre_questions_diverses": _acte(base, "deliberation_cc", "Questions diverses",
                                          "2026-03-04", "Texte F."),
    }
    gardes = [_acte(base, "deliberation", t, "2024-10-01", "Texte.")
              for t in ("MATERNELLES", "PLU", "Parité", "mise à disposition d’un local – bail civil",
                        "Questions diverses : vente du tracteur", "DEMANDE DE SUBVENTION AU DEPARTEMENT")]
    flux = {
        motif: base.execute(
            "INSERT INTO financial_flows (type, year, amount, event_id, description, source,"
            " confidence) VALUES ('subvention', 2024, 1000, ?, 'Aide', 'CM', ?)",
            (id_, sorted(RULES["confidence"]["public"])[0])).lastrowid
        for motif, id_ in ecartes.items()}
    base.commit()

    exclusions = defaultdict(Counter)
    sortie = etape_actes(base, {}, set(), lambda t: t, exclusions)
    publies = {e["id"] for e in sortie["public_events"]}

    assert not set(ecartes.values()) & publies
    assert set(gardes) <= publies
    motifs = {e["id"]: e["reason"] for e in sortie["event_exclusions"]}
    for motif, id_ in ecartes.items():
        assert motifs[id_] == motif
        assert exclusions["events"][motif] == 1
        assert sortie["pieces_des_fragments"][id_] == "https://exemple.invalid/pv-cc.pdf"
    # Un flux lu dans un acte écarté pour son titre reste attesté par la pièce.
    sortis = etape_flux(base, {}, [], set(), set(), set(), lambda t: t, exclusions,
                        sortie["public_events"], sortie["pieces_des_fragments"],
                        sortie["actes_de_rubrique"])
    assert set(flux.values()) <= {f["id"] for f in sortis["flow_rows"]}


@pytest.mark.parametrize("titre", RUBRIQUES_A_QUEUE_COLLEE)
def test_la_rubrique_a_queue_collee_reste_reconnue(titre):
    assert rubrique_de_decisions(titre)


def test_le_snapshot_retire_la_rubrique_a_queue_collee_et_ses_flux(base):
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    from scripts.snapshot.socle import RULES

    rubrique = _acte(base, "deliberation", "Décisions sur les marchés conclus en vertu de la délégationduconseilmunicipal",
                     "2021-05-26", "Décision n°2021-13 : recrutement de Mme Claude FICTIVE.")
    flux = base.execute(
        "INSERT INTO financial_flows (type, year, amount, event_id, description, source,"
        " confidence) VALUES ('bail', 2021, 420, ?, 'Loyer', 'CM', ?)",
        (rubrique, sorted(RULES["confidence"]["public"])[0])).lastrowid
    base.commit()
    exclusions = defaultdict(Counter)
    sortie = etape_actes(base, {}, set(), lambda t: t, exclusions)
    assert {e["id"]: e["reason"] for e in sortie["event_exclusions"]}[rubrique] \
        == "rubrique_decisions_par_delegation"
    assert exclusions["events"]["titre_mots_colles"] == 0
    sortis = etape_flux(base, {}, [], set(), set(), set(), lambda t: t, exclusions,
                        sortie["public_events"], sortie["pieces_des_fragments"],
                        sortie["actes_de_rubrique"])
    assert flux not in {f["id"] for f in sortis["flow_rows"]}
    assert exclusions["flows"]["lu_dans_une_rubrique_de_decisions"] == 1


def test_un_titre_que_la_rubrique_reconnait_n_est_pas_un_titre_douteux():
    """La rubrique passe avant les mots collés : elle part avec ses flux
    (`rubrique_decisions_par_delegation`), pas sous le motif des mots collés."""
    from collectors.config import COMMUNE_NAME
    from scripts.snapshot.actes import motif_de_titre_douteux
    titre = "Décisions sur les marchés conclus en vertu de la délégationduconseilmunicipal"
    assert rubrique_de_decisions(titre)
    assert motif_de_titre_douteux(titre, COMMUNE_NAME) is None


def test_un_titre_hostile_ne_ralentit_pas_le_garde():
    """Pas de backtracking : une suite de blancs ou de mots-clés reste instantanée
    (avis vigie-securite, 17/11/2026), sur le titre brut comme sur le titre nettoyé."""
    import time
    from collectors.pv_parsers import titre_en_entete_de_tampon, titre_questions_diverses
    debut = time.monotonic()
    for titre in ("DEPARTEMENT :" + " " * 100_000 + "x", "DEPARTEMENT : " * 20_000,
                  "ARRONDISSEMENT:" + "N" * 100_000, "a: " * 50_000):
        titre_en_entete_de_tampon(titre)
        titre_questions_diverses(titre)
    assert time.monotonic() - debut < 5
