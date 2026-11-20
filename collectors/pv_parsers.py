"""
pv_parsers.py — Lire un procès-verbal de séance : présences et délibérations.

Ces analyseurs ne connaissent ni CMS ni commune : ils prennent du texte extrait
d'un PDF et rendent des structures. Ce qui varie d'une collectivité à l'autre,
c'est la MISE EN FORME du document, pas la nature de ce qu'on y cherche.

Trois régimes ont été rencontrés sur un même corpus de 217 procès-verbaux
couvrant vingt-deux ans, et un quatrième sur celui de l'intercommunalité :

    actes_teletransmis
               une LIASSE d'actes, chacun tamponné à chaque page par le cachet
               @ctes du contrôle de légalité, dont le numéro change d'un acte au
               suivant. Découpage fiable — c'est la seule preuve qui survive à
               une océrisation, et elle ne doit rien à la mise en page.
    numerote   « 48/2026 : n° 4713 : Objet de la délibération »
               numéro dans la séance, puis numéro de l'acte transmis au
               contrôle de légalité. Découpage fiable.
    acte_final « Objet de la délibération (N° DE_2026_086) »
               l'identifiant clôt la ligne du titre. Découpage fiable.
    puces      compte rendu synthétique, un point par puce.
               Découpage acceptable.
    suivi      texte suivi dont les titres ne se distinguent que par la graisse,
               perdue à l'extraction. AUCUN découpage — et c'est le cas
               important : fabriquer des délibérations en devinant où elles
               commencent produirait des actes qui n'ont jamais existé. La
               séance est alors enregistrée avec son texte intégral et le fait
               que le découpage a échoué.

    liste      un ordre du jour NUMÉROTÉ : « 1. », « 01. », « III. », « XVI. »,
               le corps de chaque délibération en dessous. Découpage fiable —
               c'est la suite qui l'autorise, et elle s'arrête d'elle-même.
    capitales  titres en capitales, séparés du corps par un filet ou un saut.
               Découpage acceptable, mais silencieux sur ses limites : un
               intertitre en capitales devient une délibération de plus.

`deliberations()` essaie les six régimes dans l'ordre et rend une liste vide
plutôt qu'un découpage inventé. L'ordre compte : un identifiant d'acte est une
preuve, une mise en capitales n'est qu'un indice. `actes_teletransmis` passe en
premier parce que sa preuve est la plus forte — un cachet apposé par un tiers,
le contrôle de légalité — et parce qu'il se refuse de lui-même dès que le
document n'est pas une liasse.

Le régime `liste` a tenu jusqu'au 21/08/2026 son autorisation d'un EN-TÊTE —
« Délibérations : » — et non de sa numérotation. La mesure sur 389 procès-verbaux
de trois communes a montré que les 21 documents qu'il découpait l'étaient TOUS à
tort, pour 4 337 des 5 529 actes du corpus de Lasalle. Le détail est sous
`_liste`. C'est la leçon du module, vérifiée une fois de plus : ce qui autorise
un découpage est une preuve interne au document, jamais une formule d'annonce.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter

from .cm_parser import (categorize, extract_amounts, extract_vote, is_section_header,
                        split_into_deliberations)

# ── Régime « numéroté » : NN/AAAA [: n° NNNN] ────────────────────────────────
# Le séparateur entre les deux numéros a été « : », « – n° », « - n° » selon les
# années, et le numéro d'acte manque sur certains procès-verbaux.
ENTETE_NUMEROTE = re.compile(
    r"^[  ]*(\d{1,3})\s*/\s*(20\d{2})\s*(?:[:–—-]\s*)?(?:n[°ºo]\s*(\d{3,5}))?\s*:?\s*",
    re.M)
# Fin de titre : la formule de transmission, ou le premier visa.
FIN_TITRE = re.compile(r"(Acte rendu exécutoire|^\s*(?:Vu|Considérant|Monsieur|"
                       r"Madame|Le Conseil|Après)\b)", re.M)

# ── Régime « acte final » : … (N° DE_2026_086) ───────────────────────────────
ACTE_FINAL = re.compile(r"\(\s*N[°ºo]?\s*(?:DE\s*)?([A-Z]{2}_\d{4}_\d+)\s*\)")
LIGNE_CAPITALES = re.compile(r"^[^a-z]{8,}$")

# ── Régime « puces » ─────────────────────────────────────────────────────────
# `(cid:N)` est la façon dont pdfplumber rend une puce d'une police non standard.
PUCE = re.compile(r"^\s*(?:\(cid:\d+\)|[•▪–])\s*(.{6,150}?)\s*:\s", re.M)


# Un chiffre qui compte dans un tableau : montant ou pourcentage. Les deux
# notations décimales cohabitent dans un même procès-verbal — « 8 800,00 € » et
# « 52 655.52 € » — et n'en reconnaître qu'une laissait passer la moitié des
# lignes de tableau : « DETR 52 655.52 € 27.62 % » était publiée comme un acte.
_MONTANT_TITRE = re.compile(
    r"\d{1,3}(?:[  ]\d{3})*[.,]\d{1,2}\s*[€%]|\d[\d  ]*\s*[€%]")
# Un mot porteur de sens : au moins quatre lettres, pas de chiffre dedans.
_MOT_PORTEUR = re.compile(r"\b[^\W\d_]{4,}\b", re.UNICODE)
# Un nombre À DÉCIMALES. Une ligne de tableau en aligne plusieurs — « LANUEJOLS
# ETAGE N° 9 39,60 318.37 190 € » en porte deux, avec assez de mots pour passer
# la règle précédente. Compter TOUS les nombres refusait de vraies délibérations :
# « N°4280 : Vente parcelles cadastrées section A n°836 et n°838 » en aligne trois,
# et « Vu la loi n° 2005-102 du 11 février 2005 » aussi. Un numéro d'acte, une
# parcelle, une année sont des entiers ; une colonne de tableau porte des centimes.
# Les décimales se lisent AU BORD du nombre : sans les deux gardes, « la loi
# n° 82.213 » et « 01.01.2016 au 31.12.2016 » passaient pour des montants, et
# deux vraies délibérations de Brassac disparaissaient.
_DECIMAL = re.compile(r"(?<![\d.,])\d{1,3}(?:[  ]\d{3})*[.,]\d{1,2}(?![\d.,])")
# Le vocabulaire des en-têtes de colonnes. Un titre dont TOUS les mots porteurs
# viennent d'ici nomme une colonne, jamais l'objet d'une délibération : le
# conseil ne délibère pas sur « DEPENSES D'INVESTISSEMENT ». Les termes qui
# servent aussi de vrais intitulés — budget, compte, exercice, section — n'y
# figurent pas : « BUDGET PRIMITIF 2026 » est une délibération.
_MOTS_DE_COLONNE = {
    "DEPENSES", "DÉPENSES", "DEPENSE", "DÉPENSE", "RECETTES", "RECETTE",
    "CHARGES", "PRODUITS", "MONTANT", "MONTANTS", "TOTAL", "TOTAUX", "CHAP",
    "LIBELLE", "LIBELLÉ", "INTITULE", "INTITULÉ", "FONCTIONNEMENT",
    "INVESTISSEMENT", "REALISE", "RÉALISÉ", "PREVU", "PRÉVU", "SOLDE",
    "CREDIT", "CREDITS", "CRÉDIT", "CRÉDITS",
    # L'en-tête du tableau des crédits modifiés d'une décision budgétaire,
    # et celui du tableau des emplois d'une création de poste. Les deux
    # précèdent la formule de vote et échappaient donc à la borne de
    # `_titre_dacte` : « EMPLOI GRADE CATEGORIE DURÉE HEBDOMADAIRE ».
    "ANCIEN", "ANCIENNE", "NOUVEL", "NOUVELLE", "NOUVEAU",
    "EMPLOI", "EMPLOIS", "GRADE", "GRADES", "CATEGORIE", "CATÉGORIE",
    "EFFECTIF", "EFFECTIFS", "DUREE", "DURÉE", "HEBDOMADAIRE",
    "OUVRIR", "REDUIRE", "RÉDUIRE", "COMPTE", "COMPTES",
}
# L'appel nominal d'un vote : « M. NEVIGNAC : Oui ». Chaque conseiller devenait
# une délibération, avec le reste de la séance pour contenu.
_APPEL_NOMINAL = re.compile(
    r"^(?:MM?|Mme|Mmes|Mlle)\.?\s+[A-ZÀ-Ÿ][\wÀ-ÿ'’\-]*"
    r"(?:\s+[A-ZÀ-Ÿ][\wÀ-ÿ'’\-]*)?\s*:\s*"
    r"(?:oui|non|abstention|pour|contre|ne\s+prend\s+pas\s+part)\b", re.I)


# ── Ce qui n'est pas l'objet d'un acte ───────────────────────────────────────
#
# La rubrique d'un procès-verbal où l'exécutif RAPPORTE les décisions qu'il a
# prises par délégation du conseil (CGCT L2122-22, L5211-10). Le conseil n'y
# délibère de rien : il en prend acte. Le découpage en faisait une délibération
# — « Décisions du Président », trois fois à Lasalle et une à Brassac au
# 14/11/2026, sous l'en-tête d'un ordre du jour numéroté ou en capitales. Et ces
# décisions ne sont pas publiées une à une depuis un procès-verbal (Julien, 14
# et 15/11/2026) : la rubrique part entière, titre et texte.
#
# « Délégations consenties au Maire », « Décision modificative n° 2 » sont de
# vraies délibérations : seul « décision(s) » suivi de qui l'a prise, ou de la
# délégation qui la fonde, désigne la rubrique.
_RUBRIQUE_DES_DECISIONS = re.compile(
    r"^\W*(?:\d{1,3}\s*[.)/–-]?\s*)?"
    r"(?:(?:compte[\s-]*rendu|information|relev[ée]|liste|point|rapport|communication)s?"
    r"\b[^\n]{0,60}?(?:\b(?:des|les|aux)\s+|:\s*))?"
    r"d[ée]cisions?\s+(?:(?:prises?\s+)?"
    r"(?:(?:du|de\s+la|de\s+m(?:\.|onsieur|adame)?\s*(?:le|la)?|par\s+(?:le|la|m(?:\.|onsieur|adame)"
    r"\s*(?:le|la)?)?)\s*(?:pr[ée]sidente?|maire|bureau)\b"
    r"|(?:prises?\s+)?(?:par|en\s+vertu|dans\s+le\s+cadre|au\s+titre|sur)\b[^\n]{0,80}?"
    r"(?:d[ée]l[ée]gation|L\.?\s*2122-2[23]|L\.?\s*5211-10)))", re.I)


# « Décision n° 2021-12 : attribution du marché… » : un point de la rubrique,
# qui la continue. Lu SEULEMENT à sa suite : une collectivité peut intituler ses
# délibérations « Décision n° » (relecture vigie-securite du 15/11/2026).
_DECISION_NUMEROTEE = re.compile(r"^\W*d[ée]cisions?\s+n[°ºo]\s*\d", re.I)


def rubrique_de_decisions(titre: str | None) -> bool:
    """Le titre est-il celui de la rubrique des décisions prises par délégation ?"""
    return bool(_RUBRIQUE_DES_DECISIONS.match(titre or ""))


# Un TITRE qui commence au milieu d'une phrase. Les découpages en produisent par
# trois chemins, relevés le 15/11/2026 sur les pièces publiques des trois sites :
#
#   - `numerote` prend pour un en-tête le renvoi d'un visa coupé en fin de
#     ligne — « Vu la délibération … N° ⏎ 54/2025 en date du 2/4/2025 et de la
#     commune de… » : cinq « délibérations » au conseil communautaire du
#     04/03/2026 de Lasalle ;
#   - `acte_final` et `capitales` ne gardent que la DERNIÈRE ligne d'un objet
#     écrit sur deux : « à temps complet », « DE MASSE 2025 », « DE RESULTAT » ;
#   - `puces` prend les alinéas d'un texte suivi : « En cas de temps partiel
#     thérapeutique ».
#
# Un objet d'acte ne commence ni par une conjonction ni par une préposition qui
# le rattache à ce qui précède. Les articles n'en sont pas : « LA BERGERIE :
# CESSION DE FONDS » est un objet. En minuscule, la règle s'étend aux articles
# et aux lettres isolées de l'océrisation (« l'UNANIMITÉ », « c RÉSRARRIE ») ;
# un nom en minuscule reste un objet (« vote de crédits supplémentaires »).
#
# Les articles INDÉFINIS en font partie depuis le 16/11/2026 : « une veille
# technologique en vue d'optimiser la diffusion d'informations… » (Saillans,
# 03/10/2014) est le treizième item d'une énumération dont le redécoupage a
# retiré les douze autres (« la mise à disposition de matériels… », « la
# formation des élus… »). « des » y était déjà (`_MOTS_DE_SUITE`). Seule la
# minuscule compte : « Une convention… » ou « UNE CONVENTION… » ouvre un objet.
_MOTS_DE_SUITE = (r"et|ou|ni|mais|donc|car|que|qu['’]|qui|dont|de|des|du|d['’]|à|a|au|aux"
                  r"|en|avec|dans|sans|soit|ayant|actant|afin|lors|puis|ainsi"
                  r"|avant\s+(?:le|la|les|l['’])|apr[èe]s\s+(?:le|la|les|l['’])"
                  r"|sur\s+proposition|conform[ée]ment")
_DEBUT_DE_SUITE = re.compile(rf"^(?:{_MOTS_DE_SUITE})(?:\s|$)|^(?:d|qu)['’]", re.I)
_DEBUT_MINUSCULE_DE_SUITE = re.compile(
    rf"^(?:(?:{_MOTS_DE_SUITE}|la|le|les|une?|[a-zà-ÿ])(?:\s|$)|[a-z]['’])")
# La formule de vote n'est pas un objet. « LE CONSEIL, APRES EN AVOIR DELIBERE,
# DECIDE » (Brassac) compte quand elle OUVRE le titre, à un débris près
# (« DE_2020_132) LE CONSEIL… ») : plus loin, elle finit un objet que le
# découpage a pris trop long, et l'acte reste un acte (Brassac, 22/03/2016,
# « 3749 Mise en péril : frais propriétaires … après en avoir délibéré »). Le
# résultat du vote, lui, compte où qu'il soit : c'est la ligne d'une LISTE des
# délibérations dont les colonnes se sont mêlées — « 3 Cévennes - Terres
# Solidaires et l'association … ADOPTEE AVEC 19 VOIX POUR » (Lasalle, conseil
# communautaire du 28/10/2026).
_FORMULE_POUR_TITRE = re.compile(
    r"^[^\n]{0,20}?\ble\s+conseil\b[^\n]{0,60}?\b(?:d[ée]cide|d[ée]lib[èée]r)"
    r"|^[^\n]{0,20}?\bapr[èe]s\s+en\s+avoir\s+d[ée]lib[ée]r"
    r"|\badopt[ée]e?s?\s+(?:[àa]\s+l['’]\s*unanimit|avec\s+\d+\s+voix|par\s+\d+\s+voix)", re.I)


def titre_en_fragment(titre: str | None) -> bool:
    """Le titre est-il un morceau de phrase plutôt que l'objet d'un acte ?"""
    t = (titre or "").strip(" \t\n:;,.-–—")
    if not t:
        return False
    return bool(_DEBUT_MINUSCULE_DE_SUITE.match(t) or _DEBUT_DE_SUITE.match(t)
                or _FORMULE_POUR_TITRE.search(t))


# ── Mots collés et débris de tableau ────────────────────────────────────────
#
# Ces deux gardes vivent AU SNAPSHOT SEULEMENT (`scripts/snapshot/actes.py`,
# `motif_de_titre_douteux`), pas dans `_titre_plausible`. Un titre refusé par
# `_titre_plausible` est RECOLLÉ à l'acte d'avant (`_recoller`) : son titre et son
# texte rejoignent l'acte précédent, dont le vote et les montants sont relus.
# Pour un morceau de phrase ou une ligne de tableau, c'est voulu. Pour des mots
# collés, ce serait faux : « Ventedemobiliercommunal », « Décisionsmodificatives
# au budget… » sont de VRAIS objets à l'océrisation défectueuse, et la page sans
# espaces en porte plusieurs à la suite : le texte d'une aliénation serait publié
# sous le titre de l'acte d'avant (relecture vigie-securite du 16/11/2026). Même
# prudence pour « DE_2022_061) » : on ne sait pas si c'est un renvoi ou l'en-tête
# d'un vrai acte. Le snapshot écarte l'acte sans rien recoller.
#
# Deux défauts de l'océrisation que `titre_en_fragment` ne voit pas, parce qu'il
# ne lit que le DÉBUT du titre. Relevés le 16/11/2026 sur les `events.json`
# servis des trois sites ; chaque règle porte le cas qui l'a fait naître.
#
# MOTS COLLÉS — une page dont l'espace entre les mots a disparu (Saillans,
# 05/02/2016 et 10/07/2015) :
#
#     « uneparticipation pourlebranchementau futurréseau de chaleur »
#     « laparticipationau raccordementà l'égout »
#     « lesrecettesfiscales issues de laTaxed'Aménagement »
#     « Acceptationetsignatureaccordcadrepourl'égliseSaintGéraud »
#     « Conventiondemiseà dispositiond^unlocal »
#
# Pas de dictionnaire : le moteur n'en embarque pas, et un nom propre ou un
# sigle ne doit pas tomber (« McDonald », « SICTOBA », « eARDECHE », « PayFiP »,
# « VéloDrôme », « LaFilature »). Trois indices, choisis pour qu'aucun mot
# français ne les porte seul ; UN suffit pour les deux premiers, il en faut DEUX
# pour le troisième (« plusieurs soudures », le brief du 16/11) :
#   1. un mot d'au moins 22 lettres : les plus longs mots courants des actes
#      relevés en comptent 20 (« Professionnalisation », « PLURIPROFESSIONNELLE ») ;
#      les collés vont de 22 à 54 ;
#   2. un titre qui OUVRE par un mot en minuscule d'au moins 15 lettres commençant
#      par un article ou « pour » : « laparticipationau », « lesrecettesfiscales »,
#      « uneparticipation », « pourlebranchementau ». Les mots français qui
#      commencent ainsi sont plus courts (« labellisation » en a 13) ;
#   3. des soudures plus courtes, qui seules ne prouvent rien : un mot de huit
#      lettres ou plus terminé par « à » (« raccordementà » ; déjà, voilà, delà,
#      deçà n'ont pas huit lettres), un article en minuscule soudé à un mot à
#      capitale initiale (« laTaxed'Aménagement » ; un nom propre commence par sa
#      capitale). Une seule ne tombe pas : « Création d'un emploi permanent … à
#      temps non completà raison de 10h » (Lasalle, 14/12/2022) est un vrai objet
#      avec une faute d'océrisation. Un mot en « à » de 15 lettres ou plus en
#      porte deux à lui seul (« Conventiondemiseà »).
_MOT_DE_LETTRES = re.compile(r"[^\W\d_]+")
_ARTICLE_SOUDE_EN_TETE = re.compile(r"^(?:la|le|les|une?|pour|aux)[a-zà-ÿ]+$")
_ARTICLE_SOUDE_A_UN_NOM = re.compile(r"^(?:la|le|les|du|des|au|aux|une?)[A-ZÀ-Ý][a-zà-ÿ]{3,}")


def titre_en_mots_colles(titre: str | None) -> bool:
    """Le titre porte-t-il des mots que l'océrisation a soudés ?"""
    mots = _MOT_DE_LETTRES.findall(titre or "")
    if not mots:
        return False
    if any(len(m) >= 22 for m in mots):
        return True
    if len(mots[0]) >= 15 and _ARTICLE_SOUDE_EN_TETE.match(mots[0]):
        return True
    if any(len(m) >= 15 and m[-1] in "àÀ" for m in mots):
        return True
    soudures = sum(1 for m in mots
                   if (len(m) >= 8 and m[-1] in "àÀ") or _ARTICLE_SOUDE_A_UN_NOM.match(m))
    return soudures >= 2


# DÉBRIS DE TABLEAU — le découpage coupe dans un tableau de comptes et prend
# pour un titre ce qui reste d'une cellule (Saillans, 13/12/2019) :
#
#     « M 49). | | »      « _ PRINCIPAL »      « HT TVA TIC »      « AT TVA TIC »
#
# et dans un renvoi de pièce (Brassac, 2018-2023) : « DE_2022_061) » et ses
# trente pareils, un numéro d'acte pris pour un titre. Le signe d'un débris est
# une barre verticale, un tiret bas ou une parenthèse fermante sans ouvrante ;
# SEUL, il ne suffit pas, car de vrais objets en portent un : « DM n°3 |
# SUPPLEMENT RENOVATION APPARTEMENT » (Saillans, 2017), « Acquisition de la
# parcelle Section AD n°78_Commune de Vabre » (Brassac, 2025), « _INDEMNITÉ DU
# RECEVEUR MUNICIPAL POUR 2016 » (Lasalle, 2017). Le titre n'est un débris que
# s'il ne lui reste pas deux mots de trois lettres ou plus. Deux exceptions de
# lecture, pour ne pas toucher aux titres courts sans débris (décision de Julien,
# 16/11/2026) : la numérotation de tête (« 1) PLU », « 2) TARIFS ») et la
# ponctuation finale (« TARIFS | », « CCAS) », « SIVOM) ») ne sont pas des signes ;
# elles ne comptent que si, ôtées, il ne reste aucun mot (« M 49). | | »,
# « DE_2022_061) », « L 2123-24-1 – II) »). « HT », « TVA », « TTC » et leurs
# lectures fautives « TIC » et « AT » forment un en-tête de colonnes de montants :
# deux ou plus, et rien d'autre (« HT TVA TIC L » : la lettre isolée est du bruit).
# « ¦ » (U+00A6) : la barre cassée, lecture fautive de « | ».
_SIGNE_DE_DEBRIS = re.compile(r"[|¦_]")
# Chiffres (avec ou sans « ° »), chiffres romains (sans D ni M) ou lettre seule :
# « 1) », « 3°) », « IV) », « a) ».
NUMEROTATION_DE_TETE = re.compile(r"^\s*(?:\d{1,3}(?:\s*°)?|[IVXLCivxlc]{1,5}|[A-Za-z])\s*\)\s*")
_MOT_DE_TROIS_LETTRES = re.compile(r"[^\W\d_]{3,}")
_MOT_DE_DEUX_LETTRES = re.compile(r"[^\W\d_]{2,}")
_CHIFFRE_ROMAIN = re.compile(r"[IVXLCivxlc]+")
_MOTS_OUTILS = {"DE", "LA", "LE", "ET", "EN", "AU", "DU", "OU", "UN", "SUR", "PAR"}
_SIGLES_DE_MONTANT = {"HT", "HTVA", "TVA", "TTC", "TIC", "AT", "TT"}


def titre_en_debris_de_tableau(titre: str | None) -> bool:
    """Le titre est-il ce qui reste d'une cellule de tableau ?"""
    t = (titre or "").strip()
    coeur = NUMEROTATION_DE_TETE.sub("", t).rstrip(" \t|¦).")
    mots = len(_MOT_DE_TROIS_LETTRES.findall(coeur))
    # Le signe se cherche dans le titre ENTIER pour un titre sans mot (« M 49). | | »,
    # « L 2123-24-1 – II) »), dans le cœur pour un titre qui en garde un : la
    # ponctuation finale et la numérotation de tête ne sont pas des signes.
    barre = bool(_SIGNE_DE_DEBRIS.search(t))
    signe = barre or t.count(")") > t.count("(")
    signe_du_coeur = bool(_SIGNE_DE_DEBRIS.search(coeur)) or coeur.count(")") > coeur.count("(")
    # Une `)` seule, sans barre ni tiret bas, ne suffit pas quand le cœur porte un
    # sigle de deux lettres ou plus, en capitales, qui n'est ni un chiffre romain, ni
    # un en-tête de montant (HT, TTC…), ni un mot-outil (DE, ET…), et s'il n'y a pas
    # plus d'une `)` en trop (« DM) », « CR) »,
    # « BP 2026) », « 2) DM n°3 ») : titres courts sans débris, gardés (relecteur du
    # 17/11/2026). « M57) » (une lettre) et « L 2123-24-1 – II) » (« II » est romain)
    # restent des débris.
    sans_numero = NUMEROTATION_DE_TETE.sub("", t)
    sigle = (sans_numero.count(")") - sans_numero.count("(") <= 1
             and any(m.isupper() and not _CHIFFRE_ROMAIN.fullmatch(m)
                     and m not in _SIGLES_DE_MONTANT and m not in _MOTS_OUTILS
                     for m in _MOT_DE_DEUX_LETTRES.findall(coeur)))
    if (signe and mots == 0 and (barre or not sigle)) or (signe_du_coeur and mots < 2):
        return True
    sigles = [m.upper() for m in _MOT_DE_DEUX_LETTRES.findall(t)]
    return len(sigles) >= 2 and all(m in _SIGLES_DE_MONTANT for m in sigles)


# ── En-tête de tampon d'un extrait, point sans vote ─────────────────────────
#
# Deux titres que le découpage prend pour un objet. Même place que les gardes
# d'au-dessus : le snapshot, sans recollage (`_titre_plausible` ne les voit pas).
#
# EN-TÊTE DE TAMPON — un extrait de délibération est un SCAN : la préfecture y
# appose son tampon, dont les deux premières lignes sont « DEPARTEMENT : <nom> »
# et « ARRONDISSEMENT : <nom> », avec le numéro de l'acte à la suite. L'océrisation
# les lit comme du texte, et le découpage en fait un titre (Lasalle, extraits
# de 2021 à 2023, 52 actes servis le 17/11/2026) :
#
#     « DEPARTEMENT : GARD N°132/2023 »      « ARRONDISSEMENT: LE VIGAN »
#     « Æ, — DEPARTEMENT : GARD Na°64/2021 » « AN°138/2022 À oual DEPARTEMENT : GARD »
#     « T£ g oual ARRONDISSEME:N : LTE VIGAN » « Zoual DEPARTEM:E GNARTD »
#
# Les débris de tête sont les restes du logo (« Aigoual » devient « oual »,
# « goual »). La règle porte sur la FORME, jamais sur un lieu : Saillans et
# Brassac ont leur tampon (« DEPARTEMENT : DROME », « ARRONDISSEMENT : CASTRES »),
# et d'autres communes viendront. Cinq conditions, toutes requises :
#   1. un mot-clé EN CAPITALES, lu de travers au plus de quelques lettres
#      (« ARRONDISSEME:N », « DEPARTEM:E ») : `DEPARTEM` ou `ARRONDISS`, puis au
#      plus 4 ou 6 lettres ou deux-points, sans lettre derrière. « Syndicat
#      Départemental », « DEPARTEMENTALE » (« AGENCE DEPARTEMENTALE ») ne
#      sont pas le mot-clé ;
#   2. un DEUX-POINTS à lui, dans ce mot ou juste derrière : « DEMANDE DE
#      SUBVENTION AU DEPARTEMENT » et « … A LA REGION ET AU DEPARTEMENT » n'en
#      portent pas ; « Convention avec le Département : transport scolaire »
#      en porte un, mais en minuscules et derrière un vrai mot ;
#   3. AVANT le mot-clé, des débris seulement : 24 caractères au plus, aucun mot
#      de plus de six lettres (« Baüal », « ISoual » ; « Convention »,
#      « SUBVENTION » ne sont pas des débris), au plus un de quatre lettres ou plus
#      (« Compte rendu DEPARTEMENT : GARD » : deux vrais mots), ni deux-points, ni numérotation de
#      tête (« 1) », « Objet : »), ni mot-outil ou d'objet (« AIDE DU DEPARTEMENT : 
#      VOIRIE ») ;
#   4. APRÈS, un nom court et au plus un numéro d'acte : un à trois mots (les
#      particules « de », « du », « et »… ne comptent pas : « ALPES DE HAUTE
#      PROVENCE »), chacun fait de pièces d'au plus quatorze lettres jointes par
#      des traits d'union (« SAINT-JEAN-DE-MAURIENNE »), sans chiffre, sans mot
#      de quatre lettres ou plus en minuscules, quarante caractères au plus, aucun mot d'objet (SUBVENTION, CONVENTION, PLAN, DM…),
#      et le numéro (« N°43/2021 », « Na°64/2021 », « IN°107/2021 », « N°%61/2021 »,
#      « N°2023-132 ») s'il y est ferme le titre ;
#   5. un tampon lu sur UNE ligne porte son second bloc : « DEPARTEMENT : GARD
#      ARRONDISSEMENT : LE VIGAN » compte, chaque bloc suivant les mêmes règles.
# La liste des mots d'objet est une garde, pas un inventaire : elle ne sait pas
# tout. Le résidu connu : un titre en capitales « DEPARTEMENT : <un à trois mots
# courts> » dont aucun mot n'est dans la liste serait écarté même si c'était un
# objet. Aucun n'est servi (mesure du 17/11/2026, trois sites).
_ENTETE_DE_TAMPON = re.compile(
    r"(?<![A-Za-z])(?:DEPARTEM[A-Za-z:]{0,4}|ARRONDISS[A-Za-z:]{0,6})(?![A-Za-z])\s?:?")
# Avec année (« N°43/2021 », « N°2023-132 ») ou, sans année, avec un chiffre
# (« N°43 », lu « N43 » : `_sans_accent` efface le « ° »). Sans année ni chiffre,
# « NIMES » serait pris pour un numéro.
_NUMERO_D_ACTE = re.compile(
    r"\b[IN]{1,2}[a-z]?\W{0,2}[%\w]{1,5}\s?[/-]\s?\d{2,4}$|\b[IN]{1,2}[a-z]?[o%*]?\s?\d[%\w]{0,4}$")
_PIECE_DE_NOM = re.compile(r"^[A-Za-z']{1,14}$")
_PARTICULES_DE_NOM = {"DE", "DU", "DES", "D", "ET", "LA", "LE", "LES", "EN", "SUR", "SOUS", "AU", "AUX", "L"}
_MOTS_D_OBJET = {
    "SUBVENTION", "SUBVENTIONS", "CONVENTION", "CONVENTIONS", "ADHESION", "PLAN", "DM", "BP", "CA",
    "BUDGET", "DEMANDE", "AIDE", "AIDES", "PARTICIPATION", "TRAVAUX", "VOIRIE", "ECLAIRAGE", "AMENDES",
    "POLICE", "TRANSPORT", "FONDS", "MOTION", "REDECOUPAGE", "AVENANT", "MARCHE", "TARIF", "TARIFS",
    "OBJET", "PROJET", "SCHEMA", "CONSEIL", "DECISION", "MODIFICATION", "DELIBERATION", "DIVERSES"}
_MOTS_DE_PREFIXE = (_PARTICULES_DE_NOM - {"L", "D"}) | _MOTS_D_OBJET


def _sans_accent(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()


def _bloc_de_tampon(texte: str, debris_avant: bool) -> bool:
    """`texte` commence-t-il (à des débris près, si `debris_avant`) par un bloc
    « mot-clé : nom [numéro] » complet, suivi d'autres blocs de même forme ?"""
    # Le premier mot-clé à deux-points : un second ne peut pas être celui du tampon
    # (le premier ferait plus de six lettres de « débris »), et boucler sur chacun
    # rendrait le garde quadratique sur un titre fait de mots-clés répétés.
    m = next((m for m in _ENTETE_DE_TAMPON.finditer(texte) if ":" in m.group(0)), None)
    if m is None:
        return False
    avant, apres = texte[:m.start()], texte[m.end():]
    if debris_avant:
        lettres = _MOT_DE_LETTRES.findall(avant)
        if (len(avant) > 24 or ":" in avant or any(len(x) > 6 for x in lettres)
                or sum(len(x) >= 4 for x in lettres) > 1
                or NUMEROTATION_DE_TETE.match(avant)
                or any(x.upper() in _MOTS_DE_PREFIXE for x in lettres)):
            return False
    elif avant.strip(" \t:;,.|-"):
        return False
    suite = _ENTETE_DE_TAMPON.search(apres)
    nom, reste = (apres[:suite.start()], apres[suite.start():]) if suite else (apres, "")
    # La ponctuation finale d'abord (« N°43/2021. »), le numéro ensuite.
    nom = _NUMERO_D_ACTE.sub("", nom.strip(" \t:;,.|-")).strip(" \t:;,.|-")
    mots = [x for x in nom.split() if x.upper() not in _PARTICULES_DE_NOM]
    if (not mots or len(nom) > 40 or len(mots) > 3
            or any(not all(_PIECE_DE_NOM.match(piece) and not (len(piece) >= 4 and piece.islower())
                           for piece in x.split("-")) for x in mots)
            or any(piece.upper() in _MOTS_D_OBJET for x in mots for piece in x.split("-"))):
        return False
    return not reste or _bloc_de_tampon(reste, False)


def titre_en_entete_de_tampon(titre: str | None) -> bool:
    """Le titre est-il l'en-tête de tampon de la préfecture, lu sur un extrait ?"""
    t = " ".join(_sans_accent(titre or "").split())
    # Deux blocs de tampon, débris et numéros compris, tiennent en moins de 160 signes.
    return len(t) <= 160 and _bloc_de_tampon(t, True)


# QUESTIONS DIVERSES — le dernier point d'un ordre du jour, sans vote ni objet :
# publié comme une délibération, il en a l'air (Lasalle, séance communautaire du
# 04/03/2026). Seul le titre ÉGAL à « Questions diverses » tombe, à la casse, aux
# accents, à la ponctuation et à une numérotation de tête près (« 7) QUESTIONS
# DIVERSES », « 9 - Questions diverses », « IX - Questions diverses », « Point 7 :
# Questions diverses »), avec les trois variantes que la liste de l'actualité
# (`TITRES_VIDES`, scripts/snapshot/textes.py) tient pour vides : « Informations et
# questions diverses », « Questions et informations diverses », « Question
# diverse ». « Questions diverses : vente du tracteur » garde un objet et reste.
#
# Restes de #300, relevés sur les sites servis le 20/11/2026 : « QUESTIONS DIVERSES -
# INFORMATIONS » (Saillans, 07/09/2023) ; le tiret n'est pas un « et », donc la suite
# « questions diverses informations » entre dans la liste, avec les voisines que
# `TITRES_VIDES` tient aussi pour vides et que la liste n'avait pas (« Informations
# diverses », « Point divers », « Divers »). « Questions diverses ET informations »
# reste un acte (essai de #300), « Points divers » aussi : ils ne sont pas dans
# `TITRES_VIDES`.
_NUMERO_DE_TETE_LARGE = re.compile(
    r"^\s*(?:point\s+)?(?:\d{1,3}|[ivxlc]{1,5})\s*[.\-–—:]\s*|^\s*point\s+\d{1,3}\s*[.\-–—:]?\s*")
_QUESTIONS_DIVERSES = {"questions diverses", "question diverse", "informations et questions diverses",
                       "questions et informations diverses", "questions diverses informations",
                       "informations diverses", "point divers", "divers"}


def titre_questions_diverses(titre: str | None) -> bool:
    """Le titre est-il le seul point « Questions diverses » ?"""
    t = _sans_accent(titre or "").lower()
    t = NUMEROTATION_DE_TETE.sub("", t)
    t = _NUMERO_DE_TETE_LARGE.sub("", t)
    return " ".join(re.findall(r"[a-z0-9]+", t)) in _QUESTIONS_DIVERSES


# NUMÉRO D'ACTE SEUL — le tampon de la préfecture (voir plus haut) donne aussi, en fin
# de ligne, un numéro que le découpage prend pour un titre (Lasalle, séances du
# 07/07/2021 et du 29/06/2022, servis le 20/11/2026) :
#
#     « IN°111/2022 »   « AN°102/2022 »   « NN°106/2021 »   « A°113/2022 »
#     « AC118/2021 »    « N°LI4/2021 »
#
# Le titre est UN SEUL jeton : quelques lettres de débris, le « N° » (lu de travers
# ou perdu), un à quatre chiffres, « / », l'année. Une lettre ou un « ° » est
# requis (« 43/2021 » et « 12/2021 », dates ou fractions, ne sont pas touchés). Un
# espace avant le « N° » sort du motif : « DM N°1/2023 » (décision modificative
# numéro 1) est un objet et reste, collé ou non : un sigle budgétaire en tête (« DM1/2023 »,
# « BP2021/2022 », « CA2019/2020 », « M14/2020 ») n'est pas un débris de tampon
# (avis vigie-securite, 20/11/2026).
_NUMERO_D_ACTE_SEUL = re.compile(
    r"(?!(?i:dm|bp|bs|ca|cg|m)\d)(?=[^\d]*[A-Za-z°º])[A-Za-z]{0,3}(?:[°º]\s?)?[A-Za-z]{0,2}\d{1,4}\s?/\s?\d{2,4}")


def titre_numero_d_acte_seul(titre: str | None) -> bool:
    """Le titre se réduit-il à un numéro d'acte (« IN°111/2022 ») ?"""
    t = (titre or "").strip().strip(" \t.,;:|¦_()")
    return bool(_NUMERO_D_ACTE_SEUL.fullmatch(t))


# DÉBRIS DE PONCTUATION — un titre coupé en deux lignes laisse, de part et d'autre de
# la coupure, un guillemet ou une parenthèse sans sa paire (relevé sur les sites
# servis le 20/11/2026, Lasalle, Saillans, Brassac) :
#
#     AGRICULTURE »     ASSOCIATIVE ET SPORTIVE »     0 (RESTES A
#     Informatique » (A.GE.D.I)     "DU CONSEN MUNICIPAL
#
# et le mot du vote pris pour un titre, entre guillemets : « CONTRE » (Lasalle,
# 28/08/2025).
#
# Le signe est un guillemet « » ou “ ” sans pair, un guillemet droit en nombre
# impair, ou une parenthèse OUVRANTE sans fermante. La parenthèse FERMANTE seule
# (« COORDINATION) », « CCAS) ») n'est pas un signe : c'est le cas gardé par
# `titre_en_debris_de_tableau` (titres courts, décision de Julien). SEUL, un signe
# ne suffit pas non plus : le titre doit être trop court pour être un objet, deux
# mots de trois lettres ou plus au plus. Espace culturel « La Marquise
# (Brassac, 2009, 3 mots) et « LINKY : NE PAS SE TROMPER DE CIBLE (Lasalle,
# 2018) restent : un début d'objet, pas un débris. Deux guillemets appariés
# (« C.L.S.H. », BUDGET « COMMUNE ») ne sont pas un signe non plus.
#
# Un guillemet COLLÉ entre deux lettres (ou une apostrophe) est une apostrophe mal
# lue, pas un guillemet : « SUBVENTION 2026 — VIVALT”’O » (Lasalle, 28/05/2026) est
# un vrai objet et reste.
_MOTS_DE_VOTE = {"pour", "contre", "abstention", "abstentions"}
_GUILLEMET = re.compile(r"[«»“”\"]")
_GUILLEMET_DANS_UN_MOT = re.compile(r"(?<=\w)[«»“”\"](?=[\w’'])")


def titre_en_debris_de_ponctuation(titre: str | None) -> bool:
    """Le titre est-il le reste d'une coupure entre guillemets ou parenthèses ?"""
    t = titre or ""
    if _GUILLEMET.search(t):
        mot = " ".join(re.findall(r"[a-z]+", _sans_accent(t).lower()))
        if mot in _MOTS_DE_VOTE:
            return True
    t = _GUILLEMET_DANS_UN_MOT.sub("", t)
    orphelin = (t.count("«") != t.count("»") or t.count("“") != t.count("”")
                or t.count('"') % 2 == 1 or t.count("(") > t.count(")"))
    return orphelin and len(_MOT_DE_TROIS_LETTRES.findall(t)) <= 2


def _titre_plausible(titre: str) -> bool:
    """Un titre de délibération qui porte un montant doit aussi porter des mots.

    Les procès-verbaux contiennent des TABLEAUX de plan de financement, et tous
    les régimes de découpage finissent par couper dedans : chaque ligne du
    tableau devient un « acte ». Le 20/08/2026, la base de Lasalle en comptait
    275, tous publiés, dont un en première page du site :

        « 2 506,51 € TTC (TVA: 20%) »      « CD30 8 800,00 € »
        « FEDER 2021-2027 156 812,54 € 13,0% »

    Aucun n'avait de flux financier extrait : l'information chiffrée était
    perdue ET affichée comme une décision du conseil.

    La règle d'origine ne mordait QUE sur les titres portant un montant à la
    française, et deux mots d'au moins quatre lettres suffisaient à distinguer
    « ASSOCIATION NEVESQUE 3 000,00 € » d'une ligne de tableau. La mesure du
    21/08/2026 sur 389 procès-verbaux de trois communes a montré trois fuites,
    et chacune ajoute ici une ligne :

        « DETR 52 655.52 € 27.62 % »     le point décimal n'était pas reconnu
        « LANUEJOLS ETAGE N° 9 39,60 318.37 190 € »   deux mots, quatre nombres
        « M. NEVIGNAC : Oui »            l'appel nominal d'un vote
        « DEPENSES D'INVESTISSEMENT »    un en-tête de colonne, 34 774 caractères

    Un intitulé sans chiffre reste intact, si court soit-il — « PLU », « TARIFS »,
    « INCIVILITES » sont de vrais objets de délibération. Les plans de
    financement eux-mêmes relèvent d'un extracteur dédié, pas du découpage.
    """
    titre = titre or ""
    if _APPEL_NOMINAL.match(titre):
        return False
    # Un morceau de phrase, ou la rubrique des décisions prises par délégation
    # (15/11/2026) : ni l'un ni l'autre n'est l'objet d'une délibération.
    if titre_en_fragment(titre) or rubrique_de_decisions(titre):
        return False
    mots = _MOT_PORTEUR.findall(titre)
    if mots and all(m.upper() in _MOTS_DE_COLONNE for m in mots):
        return False
    if len(_DECIMAL.findall(titre)) >= 2:
        return False
    if not _MONTANT_TITRE.search(titre):
        return True
    return len(mots) >= 2


# Référence du contrôle de légalité, apposée sur tout acte télétransmis par
# @ctes : département, SIREN de la collectivité, DATE DE L'ACTE, numéro, nature.
#     026-200040509-20260625-DE2026116bis-BF
# C'est la seule date de séance qu'un acte porte de façon mécanique, et elle est
# la bonne : sur le portail relevé le 24/08/2026, six délibérations de la séance
# du 25 juin étaient déposées sous « date d'acte » 30/06 et 06/07 — leur date de
# TÉLÉTRANSMISSION. Le SIREN, lui, permet de vérifier que la pièce émane bien de
# la collectivité attendue, et non d'une homonyme.
_REFERENCE_ACTES = re.compile(
    r"\b(\d{3}[A-Z]?)-(\d{9})-(\d{4})(\d{2})(\d{2})-([A-Za-z0-9_./]{1,30})-([A-Z]{2})\b")


def reference_actes(texte: str) -> dict | None:
    """La référence @ctes portée par un acte télétransmis, ou None.

    Rend `{reference, departement, siren, date, numero, nature}`. La date est
    celle de l'acte, donc de la séance qui l'a pris.
    """
    m = _REFERENCE_ACTES.search(texte or "")
    if not m:
        return None
    dep, siren, annee, mois, jour, numero, nature = m.groups()
    return {
        "reference": m.group(0),
        "departement": dep,
        "siren": siren,
        "date": f"{annee}-{mois}-{jour}",
        "numero": numero,
        "nature": nature,
    }


# ── Régime « actes télétransmis » : la liasse que le contrôle de légalité tamponne
#
# Une collectivité ne publie pas toujours un procès-verbal rédigé : elle dépose
# souvent la LIASSE de ses actes, chacun sur deux ou trois pages, à la suite
# dans un seul PDF. Aucun des cinq autres régimes ne sait la découper — et
# `_capitales`, qui prend le relais, produit un titre par ligne de mobilier :
# 1 035 actes sur les 39 liasses scannées de Lasalle, pour 621 réels.
#
# Ce régime tient d'une PREUVE INTERNE, comme la doctrine l'exige : le cachet
# @ctes apposé sur chaque page d'un acte télétransmis, dont le numéro change
# d'un acte au suivant. `reference_actes` le lit déjà, mais EXACTEMENT — et sur
# un document océrisé, l'exactitude ne trouve presque rien : sur le PV du
# 30/06/2026 de Lasalle, 13 pages sur 47 alors que le cachet est visible sur une
# trentaine. Le lecteur ci-dessous lit la CHARPENTE — trois nombres longs, un
# numéro, deux lettres — et non les valeurs. Les deux ont leur emploi et ne se
# remplacent pas : l'exact sert à DATER un acte (il faut alors que les chiffres
# soient sûrs), le tolérant à RECONNAÎTRE la même page d'un acte à l'autre.
_SEP_OCERISE = r"[\s.:;_,\u2013\u2014-]{1,3}"
_REFERENCE_TOLERANTE = re.compile(
    rf"(\d{{3}}[A-Z]?){_SEP_OCERISE}(\d{{8,10}}){_SEP_OCERISE}(\d{{8}}){_SEP_OCERISE}"
    rf"([A-Za-z0-9_.\s]{{1,18}}?){_SEP_OCERISE}([A-Z]{{2}})\b")

# L'objet de la délibération, tel que la collectivité l'écrit elle-même en tête
# d'acte. C'est le seul titre qui ne soit pas deviné.
# « Obiet : » : l'océrisation lit le j comme un i — relevé sur les liasses
# intercommunales de Lasalle, où l'objet de vingt actes restait sans titre.
_OBJET = re.compile(r"^[^\n]{0,12}?Ob[jil1í]et\s*[:;]\s*(.+)$", re.M | re.I)
# La graphie exacte d'abord : l'océrisation double parfois la ligne, en
# charabia puis lisible — « Obiet : Dénomination desb iné éentanti fPs1e… »,
# puis « Objet: Dénomination des binomes représentant la COMMUNAUTE… ».
_OBJET_EXACT = re.compile(r"^[^\n]{0,12}?Objet\s*[:;]\s*(.+)$", re.M | re.I)
# Un fragment de cachet que l'océrisation a collé dans la ligne de l'objet.
_CACHET_DANS_UN_TITRE = re.compile(r"(?:\s\d[\d .]*)?\s\S*\d{8}[-_]\S*?-?DE\b")
# Du charabia : une lettre prise entre deux chiffres, ou l'inverse (« 0i5 »).
_BANDEAU_SUIVANT = re.compile(r"^[^\w\n]{0,3}\w{0,2}N[°ºoº]\s*\d{1,4}\s*/\s*(?:19|20)\d\d\b", re.M)
_CHARABIA = re.compile(r"[^\W\d_]\d[^\W\d_]|\d[^\W\d_]\d")

# Ce que l'océrisation dépose en marge d'une ligne, avant le mobilier qu'elle
# précède : « Æ£ DEPARTEMENT : GARD », « | N°153/2021 », « ER DEPARTEMENT ».
# Un jeton SÉPARÉ, jamais le début d'un mot — sans l'espace exigé ici,
# « REPUBLIQUE » perdait son « RE » et échappait au filtre. Un chiffre isolé en
# est un aussi : « 1 DEPARTEMENT : GARD » passait pour un objet sur la liasse
# intercommunale du 15/03/2023. Le débris n'est retiré que pour RECONNAÎTRE le
# mobilier — un titre qui commence par un chiffre le garde.
_DEBRIS_DE_MARGE = re.compile(
    r"^[^\w]{1,4}\s*|^[A-ZÆŒÀ-Ÿ]{1,2}[^\w\s]{0,2}\s+|^\d{1,2}[^\w\s]{0,2}\s+")
# Où la délibération commence vraiment. `FIN_TITRE` ne convient pas ici : son
# « Le Conseil » tombe sur « Le conseil municipal de cette commune,
# régulièrement convoqué… », qui appartient au bandeau et PRÉCÈDE l'objet — la
# borne coupait alors avant lui et seize actes perdaient un titre qu'ils avaient.
_FORMULE_DE_VOTE = re.compile(
    r"apr[èe]s\s+en\s+avoir\s+d[ée]lib[ée]r[ée]|\bD[EÉ]CIDE\b|\bD[EÉ]LIB[EÉ]RE\b", re.I)
# Le mobilier qu'un acte télétransmis porte en tête de CHAQUE page : le cachet,
# le timbre de la collectivité, le rappel de séance. Il est en capitales — donc
# éligible au repli de `_titre_dacte` — et il précède l'objet, si bien que le
# titre tombait dessus dès que l'objet manquait. Mesuré le 10/09/2026 sur la
# base servie de Lasalle : 64 des 237 actes du régime, soit 27 %, s'appelaient
# « ID : 030-213001407-20260630-DEL2606_02-DE » ou
# « REPUBLIQUE FRANÇAISE EXTRAIT DU RE D: 030 213001407-… ».
_MOBILIER_DE_CACHET = re.compile(
    r"^(?:"
    r"[I1lD]{1,2}\s*[:;]"
    r"|N[°ºo\u00ba]\s*(?:DEL|DE)?\s*\d"
    r"|R[EÉ]PUBLIQUE\s+FRAN"
    r"|EXTRAIT\b"
    r"|D[EÉ]PARTEMENT\b"
    r"|ARRONDISSEMENT\b"
    r"|NOMBRE\s+DE\s+(?:CONSEILLERS|MEMBRES)"
    r"|S[EÉ]ANCE\s+DU\b"
    r"|ENVOY[EÉ]\s+EN\s+PR[EÉ]FECTURE"
    r"|RE[CÇ]U\s+EN\s+PR[EÉ]FECTURE"
    r"|PUBLI[EÉ]\s+LE\b"
    r"|PAGE\b"
    r"|D[EÉ]LIB[EÉ]RATIONS?\s+DU\s+CONSEIL"
    # Le même bandeau, colonne « DÉPARTEMENT » collée devant et abîmée :
    # « BÉPARTENMRENT DES DELIBERATIONS DU CONSEIL », « EPARTEMENT DES
    # DELIBERATIONS », « HEPARTERENT DES … » — quatre titres des CM de Lasalle
    # des 10/09 et 15/10/2026.
    r"|\S{1,14}\s+DES\s+D[EÉ]LIB[EÉ]RATIONS\b"
    r"|ACTE\s+RENDU\s+EX[EÉ]CUTOIRE"
    r"|POUR\s+COPIE\s+CONFORME"
    # L'en-tête de la collectivité elle-même : « COMMUNAUTE DE COMMUNES CAUSSES
    # AIGOUAL CEVENNES — TERRES SOLIDAIRES » est devenu le titre d'un acte
    # intercommunal le 16/09/2026, en rejouant le découpage.
    r"|COMMUNAUT[EÉ]\s+DE\s+COMMUNES\b"
    r")", re.I)

# Le numéro d'acte tel que le BANDEAU DU REGISTRE l'écrit, juste au-dessus de
# l'objet : « N° DEL260190 », « N°73/2022 ». Il ne découpe rien — c'est le
# cachet de télétransmission qui le fait — mais il ANNONCE l'objet, et c'est la
# seule marque qui le précède toujours. `_MOBILIER_DE_CACHET` le refuse déjà
# comme titre ; ici il sert de point de repère.
#
# 🔴 En TÊTE de ligne. Un préambule CITE d'autres actes — « Vu la délibération
# N°111 du 2 octobre 2019 » — et la première version, ancrée nulle part, prenait
# cette citation pour un bandeau puis l'en-tête du tableau qui suivait pour un
# objet : deux « Délibération n° … » honnêtes devenaient « CATEGORIES TARIFS
# PROPOSES 2025 ». Un titre faux vaut moins qu'un repli qui avoue.
# Mais pas SEUL sur sa ligne : l'océrisation colle au numéro la colonne voisine
# du bandeau — « N° DEL 2606 09 M. …, M. …, Mme … » (la liste des présents) — et
# l'exiger seul perdait six objets justes de la séance du 30/06/2026.
_NUMERO_DE_REGISTRE = re.compile(
    r"^[^\w\n]{0,3}\w{0,2}N[°ºoº][ \t]*(?:DEL|DE)?[ \t]*\d[\d \t._/-]{2,}", re.I | re.M)
# Jusqu'où remonter pour le trouver, et pourquoi une borne. Le bandeau tient
# sur la page qui PRÉCÈDE la décision ; au-delà, le bandeau rencontré est celui
# d'un acte antérieur, et le titre serait celui du voisin. Mesuré sur les
# liasses de la première commune portée : la dernière ancre tombe entre 827 et
# 3 959 caractères de la coupure.
AMONT_DU_BANDEAU = 4000
# L'objet suit le numéro de très près ; ce qui s'intercale est du débris de
# marge. Au-delà, on est entré dans le corps de la délibération.
LIGNES_APRES_LE_NUMERO = 6
# Une liasse qui déclare ses objets « Objet : … » ne les écrit pas en capitales :
# ses lignes capitales sont des tableaux, des logos, des annexes. Mesuré le
# 16/09/2026 sur Lasalle — les liasses de la commune portent 0 « Objet : » et
# leurs lignes capitales sont justes ; celles de l'intercommunalité en portent
# 13 à 26, et 14 de leurs 17 titres pris en capitales étaient faux
# (« VALLERAUGUE », « BASES PRODUIT 2023 TAUX PROPOSE EN 2023 », « 2421114
# NOHDVA »). Trois, et pas un : une citation isolée ne fait pas une convention.
OBJETS_DECLARES_MINIMUM = 3


def _suffixe_de_seance(numeros: list[str]) -> str:
    """Les chiffres que TOUS les numéros d'une même liasse partagent en fin.

    Le numéro d'acte porte deux choses collées : son rang dans la séance et un
    millésime. Lasalle numérote « 53_2024 », Saillans « 1_070422 » — l'année
    dans un cas, la date de séance dans l'autre. Seul le rang fait une suite ;
    le reste est constant et doit s'en aller.

    🔴 Le compte porte sur les numéros DISTINCTS, jamais sur leurs occurrences.
    Une délibération budgétaire traîne ses annexes sur quatre-vingts pages :
    comptées une à une, elles imposent leur propre fin de numéro comme suffixe
    commun (« 72024 », relevé sur la séance du 29/05/2024), et le rang de tous
    les autres actes devient illisible d'un coup. Le document rendait alors UN
    acte au lieu de vingt-trois.
    """
    from collections import Counter
    chiffres = sorted({"".join(re.findall(r"\d", n)) for n in numeros})
    chiffres = [c for c in chiffres if len(c) >= 3]
    if len(chiffres) < 3:
        return ""
    for longueur in range(8, 1, -1):
        fins = Counter(c[-longueur:] for c in chiffres if len(c) > longueur)
        if not fins:
            continue
        fin, n = fins.most_common(1)[0]
        if n >= max(3, len(chiffres) // 3):
            return fin
    return ""


# Au-delà, ce n'est plus un rang : c'est un millésime resté collé. La séance la
# plus chargée du corpus de Lasalle en porte 52 — le seuil garde un facteur dix.
RANG_MAX_PLAUSIBLE = 500


def _prefixe_de_seance(numeros: list[str]) -> str:
    """Les chiffres que TOUS les numéros partagent en TÊTE — l'autre place où
    une collectivité colle sa marque de séance.

    Lasalle écrit « DEL2606_02 » : l'année 26 et le mois 06 sont en PRÉFIXE, le
    rang les suit. `_suffixe_de_seance` n'y voit rien à retirer — les fins
    varient, 02, 03, 06 — si bien que le repli titrait « Délibération
    n° 260602 » le deuxième acte d'une séance qui en porte quinze.

    🔴 Deux garde-fous, pour ne rien casser là où le numéro se lit déjà. Le
    préfixe doit être commun à TOUS les numéros, jamais à une majorité : à un
    tiers près, « 26060 » l'emporterait sur « 2606 », et les actes 12 à 20 de
    la même séance garderaient leur millésime pendant que les autres le
    perdraient. Et son retrait doit rendre TOUS les rangs plausibles, sinon il
    n'est pas retenu : « 53_2024 », dont le millésime est en suffixe, n'est
    ainsi jamais touché.
    """
    chiffres = sorted({"".join(re.findall(r"\d", n)) for n in numeros})
    chiffres = [c for c in chiffres if len(c) >= 3]
    if len(chiffres) < 3:
        return ""
    if max(int(c) for c in chiffres) <= RANG_MAX_PLAUSIBLE:
        return ""  # les rangs se lisent déjà : rien à retirer
    commun = os.path.commonprefix(chiffres)
    for longueur in range(len(commun), 0, -1):
        restes = [c[longueur:] for c in chiffres]
        if all(r and int(r) <= RANG_MAX_PLAUSIBLE for r in restes):
            return commun[:longueur]
    return ""


def _rang_dans_la_seance(numero: str, suffixe: str, prefixe: str = "") -> int | None:
    """Le rang de l'acte, ou None si le numéro n'en laisse rien lire.

    Les chiffres sont RECOLLÉS après retrait du suffixe : l'océrisation sème des
    séparations qui n'existent pas — « 1_10_2024 » est le n° 110, « 11_1_2024 »
    le n° 111. Ne garder que le premier groupe en ferait le n° 1, et deux vrais
    actes se perdraient dans le bruit à chaque séance.
    """
    c = "".join(re.findall(r"\d", numero))
    if prefixe and c.startswith(prefixe) and len(c) > len(prefixe):
        c = c[len(prefixe):]
    if suffixe and c.endswith(suffixe) and len(c) > len(suffixe):
        c = c[:-len(suffixe)]
    return int(c) if c and len(c) <= 6 else None


def _suite_croissante(valeurs: list[int]) -> list[int]:
    """Les indices de la plus longue sous-suite STRICTEMENT croissante.

    C'est ici que le bruit se sépare de la numérotation, et il n'y avait pas
    d'autre moyen : une collectivité numérote ses actes dans l'ordre, et
    l'océrisation, elle, ne respecte aucun ordre. Un « 29 » apparu au milieu des
    quatre-vingt-dix, un « 47 » glissé entre 42 et 43, une année lue « 2029 » —
    tous sortent de la suite, et leurs pages reviennent à l'acte qu'elles
    coupaient. Ce qui reste est ce que la collectivité a réellement numéroté.

    ⚠️ Ce choix SOUS-COMPTE plutôt qu'il ne surcompte : un acte dont le rang est
    trop abîmé pour tenir dans la suite est absorbé par son voisin, avec son
    texte. C'est le sens qu'on veut — un acte manquant se voit et se corrige à
    la saisie, un acte inventé se publie et fait mentir la base.
    """
    if not valeurs:
        return []
    n = len(valeurs)
    longueur, precedent = [1] * n, [-1] * n
    for i in range(n):
        for j in range(i):
            if valeurs[j] < valeurs[i] and longueur[j] + 1 > longueur[i]:
                longueur[i], precedent[i] = longueur[j] + 1, j
    i = max(range(n), key=lambda k: longueur[k])
    chemin = []
    while i >= 0:
        chemin.append(i)
        i = precedent[i]
    chemin.reverse()
    # Un rang abîmé plus petit que tous ceux qui le suivent (ou plus grand que
    # tous ceux qui le précèdent) tient dans la suite : il en prend la TÊTE (ou
    # la queue). CC du 29/10/2025, recueil océrisé : « D141_2025 » lu
    # « Di41_2025 » page 44, devant 120…135 ; l'acte « 41 » avalait les pages
    # 44 à 251, prenait l'objet du 141 et se publiait en double. Au bord, un
    # saut que le reste de la suite ne connaît pas est un rang mal lu.
    while len(chemin) > 2 and _saut_isole(valeurs[chemin[1]] - valeurs[chemin[0]],
                                          [valeurs[k] for k in chemin[1:]]):
        chemin.pop(0)
    while len(chemin) > 2 and _saut_isole(valeurs[chemin[-1]] - valeurs[chemin[-2]],
                                          [valeurs[k] for k in chemin[:-1]]):
        chemin.pop()
    return chemin


# Mesuré le 02/11/2026 sur les 47 liasses océrisées des trois instances : au
# bord de la suite, l'écart légitime le plus grand est 3, au milieu 7. Les deux
# conditions sont exigées : un saut absolu (SAUT_ISOLE), et dix fois le plus
# grand écart du reste — une suite qui garde son millésime collé (Saillans,
# 07/04/2022 : 370422, 470422…) a des écarts de 100 000 partout, et rien n'y
# est un saut isolé.
SAUT_ISOLE = 20
SAUT_RELATIF = 10


def _saut_isole(saut: int, reste: list[int]) -> bool:
    interieur = max((b - a for a, b in zip(reste, reste[1:])), default=1)
    return saut > SAUT_ISOLE and saut > SAUT_RELATIF * max(interieur, 1)


def _ligne_dobjet(ligne: str) -> str | None:
    """La ligne porte-t-elle un objet de délibération ?

    Cette suite de refus n'était écrite qu'une fois, dans `_titre_dacte`. Le
    bandeau du registre la redemande sur un texte qui n'est PAS le corps de
    l'acte : en faire une fonction évite deux copies, qui divergeraient au
    premier ajout.
    """
    ligne = ligne.strip()
    if len(ligne) < 12 or not LIGNE_CAPITALES.match(ligne):
        return None
    # Le cachet lui-même, quelle que soit la graphie que l'océrisation lui
    # donne : c'est la charpente que le régime lit déjà pour découper.
    nu = _DEBRIS_DE_MARGE.sub("", ligne)
    if (_MOBILIER_DE_CACHET.match(ligne) or _MOBILIER_DE_CACHET.match(nu)
            or _REFERENCE_TOLERANTE.search(ligne)):
        return None
    # Un objet de délibération porte des MOTS. « S€£000109#£0007 »,
    # « 3 10/1/200/220222 », « 2021 2021 2022 » n'en portent aucun : ce sont
    # des marges et des tableaux que l'océrisation a laissés en capitales.
    if not _MOT_PORTEUR.search(ligne):
        return None
    # Du bruit d'océrisation qui porte des mots. Un tableau lu à l'envers laisse
    # des jetons d'un caractère (« SNOILOGS LIT 7 6 T 6 8 ») ; un nom de la liste
    # des présents reste seul entre parenthèses (« (O'TTAVLANO) ») ; un schéma
    # laisse des signes orphelins (« DIAGNOSTIC ET STRATÉGIE à) SIGNATURE » MISE
    # EN ŒUVRE »). Relevés le 16/09/2026 en rejouant le découpage des liasses
    # intercommunales de Lasalle : tous auraient été publiés comme titres.
    if sum(1 for jeton in ligne.split() if len(jeton) == 1 and jeton.isalnum()) >= 3:
        return None
    if re.fullmatch(r"\(.*\)", ligne.strip(" :;.-|")):
        return None
    if ligne.count("(") != ligne.count(")") or ligne.count("«") != ligne.count("»"):
        return None
    if not _titre_plausible(ligne):
        return None
    # Seule la ponctuation de marge s'en va : ôter aussi le jeton de tête
    # amputerait « DE LA COMMUNE… » d'un vrai mot.
    return re.sub(r"\s+", " ", ligne).strip(" :;.-|")[:255]


def _objet_declare(texte: str, rang: int | None = None) -> str | None:
    """La ligne « Objet : … » que la collectivité écrit elle-même.

    Un objet long déborde sur la ligne suivante : « …de la station ⏎
    touristique de Prat-Peyrot — Plan Avenir Montagnes ». Une ligne qui
    commence par une minuscule le continue ; « Vu… », « Le Conseil… », non.
    """
    m = _OBJET.search(texte)
    if not m:
        return None
    # Une variante OCR juste avant la graphie exacte : c'est la même ligne, lue
    # deux fois. On ne va pas chercher plus loin — la graphie exacte d'un autre
    # acte, plus bas, n'a rien à dire de celui-ci.
    if not _OBJET_EXACT.match(texte, m.start()):
        m = _OBJET_EXACT.search(texte, m.start() + 1, min(len(texte), m.end() + 300)) or m
    # 🔴 Le numéro prouve l'appartenance : sous un bandeau « N°xx/aaaa » d'un
    # autre acte, cet objet n'est pas le nôtre — relevé le 16/09/2026, l'acte
    # n° 59 prenait l'objet écrit sous le bandeau N°58.
    if rang is not None:
        bandeaux = list(_BANDEAU_SUIVANT.finditer(texte, 0, m.start()))
        if bandeaux and int(re.search(r"(\d{1,4})\s*/", bandeaux[-1].group()).group(1)) != rang:
            return None
    titre = m.group(1)
    suite = texte[m.end():].lstrip("\n").split("\n", 1)[0]
    if (suite[:1].islower() and not titre.rstrip().endswith(".")
            and _MOT_PORTEUR.search(suite) and not _CHARABIA.search(suite)):
        titre = f"{titre} {suite}"
    titre = re.sub(r"\s+", " ", _CACHET_DANS_UN_TITRE.sub(" ", titre)).strip(" :;.-")
    return titre[:255] if len(titre) >= 5 else None


# ── Le dernier recours avant le repli : ce que l'acte DÉCIDE ──────────────────
# La clause suit la formule de vote dans le bloc même de l'acte — elle lui
# appartient — et nomme ce qui a été voté : « DECIDE : — de créer un poste
# permanent d'Agent de crèche ». Seules comptent les clauses qui portent l'objet :
# « transmettre la présente délibération », « acter le plan ci-dessus » ne disent
# rien de ce qui a été voté. Le titre est rendu à l'infinitif, et son origine,
# `clause_decisive`, dit qu'il est LU dans la décision et non écrit en objet.
# Relevé le 16/09/2026 sur les liasses intercommunales de Lasalle : 53 actes
# restaient « Délibération n° … ».
_VERBES_DE_DECISION = {
    "approuver": ("approuve",), "adopter": ("adopte",), "créer": ("crée", "cree"),
    "supprimer": ("supprime",), "modifier": ("modifie",), "fixer": ("fixe",),
    "attribuer": ("attribue",), "octroyer": ("octroie",), "accorder": ("accorde",),
    "allouer": ("alloue",), "verser": ("verse",), "contracter": ("contracte",),
    "instituer": ("institue",), "vendre": ("vend",), "céder": ("cède", "cede"),
    "acquérir": ("acquiert",), "solliciter": ("sollicite",), "demander": ("demande",),
    "lever": ("lève", "leve"), "voter": ("vote",), "désigner": ("désigne", "designe"),
    "élire": ("élit", "elit"), "renouveler": ("renouvelle",), "valider": ("valide",),
    "abroger": ("abroge",), "annuler": ("annule",), "recruter": ("recrute",),
    "transformer": ("transforme",), "prolonger": ("prolonge",), "reconduire": ("reconduit",),
    "emprunter": ("emprunte",), "confier": ("confie",), "mettre": ("met",),
    "dénommer": ("dénomme",), "déclasser": ("déclasse",), "adhérer": ("adhère",),
}
_INFINITIF = {forme: inf for inf, formes in _VERBES_DE_DECISION.items()
              for forme in (inf, inf.replace("é", "e").replace("è", "e"), *formes)}
_FORMULES_DE_TETE = re.compile(
    r"^(?:(?:et\s+)?(?:à|a)\s+l['’]unanimit[ée]|avec\s+\d+\s+voix[^,]*|par\s+\d+\s+voix[^,]*"
    r"|[eo](?=\s)|d[ée]cide|d[ée]lib[èe]re|\d{3}-\d+-\d{8}\S*)[\s,:]*", re.I)
_FIN_DE_CLAUSE = re.compile(
    r",\s*(?:[eo]\s+(?=[A-ZÉ])|à\s+l['’]exception|sauf\b)|\s+de\s+la\s+(?:façon|manière)\s*$", re.I)
_REMPLISSAGE = re.compile(r"\s(?:ci-dessus|ci-après|comme\s+suit|suivant(?:e)?s?)\b", re.I)


def _titre_de_la_decision(corps: str, rang: int | None = None) -> str | None:
    m = _FORMULE_DE_VOTE.search(corps)
    if not m:
        return None
    # 🔴 Ce qui PRÉCÈDE la formule dit à quel acte elle appartient. Une ligne
    # « Objet : » que la tête n'a pas retenue, ou un bandeau d'un autre numéro,
    # signent la décision de l'acte suivant — « Ainsi fait et délibéré » ne se lit
    # pas comme un vote, et le premier « DECIDE » venu était celui du voisin.
    avant = corps[:m.start()]
    bandeaux = list(_BANDEAU_SUIVANT.finditer(avant))
    if _OBJET.search(avant) or (bandeaux and rang is not None and int(
            re.search(r"(\d{1,4})\s*/", bandeaux[-1].group()).group(1)) != rang):
        return None
    zone = corps[m.end():m.end() + 1500]
    # Jamais au-delà du bandeau de l'acte suivant : sa décision n'est pas la nôtre.
    borne = min((x.start() for x in (_BANDEAU_SUIVANT.search(zone), _OBJET.search(zone),
                                     _NUMERO_DE_REGISTRE.search(zone)) if x), default=len(zone))
    for morceau in re.split(r";|\n\s*[\-—–•◦>‒·]\s*|(?<!\bM)\.\s+|:\s*\n", zone[:borne]):
        clause = re.sub(r"\s+", " ", morceau).strip(" ,:;-—.")
        for _ in range(4):
            clause = _FORMULES_DE_TETE.sub("", clause).strip(" ,:;-—.")
        clause = re.sub(r"^(?:de\s+|d['’]\s*)", "", clause, flags=re.I)
        premier, _, reste = clause.partition(" ")
        infinitif = _INFINITIF.get(premier.lower().strip(",:"))
        if not infinitif or len(reste.split()) < 2:
            continue
        reste = _REMPLISSAGE.sub("", f" {reste}").split(":")[0]
        # Ce qui suit est une autre clause, ou l'annonce d'un tableau.
        reste = _FIN_DE_CLAUSE.split(reste, 1)[0].strip(" ,")
        titre = f"{infinitif[:1].upper()}{infinitif[1:]} {reste}"
        if len(titre) > 140:
            titre = titre[:140].rsplit(" ", 1)[0] + "…"
        if len(titre.split()) >= 3 and not _CHARABIA.search(titre) and _titre_plausible(titre):
            return titre
    return None

# Le bandeau des liasses intercommunales porte le numéro de l'acte en clair,
# « N°137/2021 ». Quand ni la tête du bloc ni l'amont n'ont rendu d'objet —
# cachet de tête illisible, vote entre le bandeau et la coupure —, ce numéro,
# identique au rang et à l'année de l'acte, prouve l'appartenance mieux que
# toute position : une citation prouve la présence, un numéro l'identité.
_BANDEAU_NUMEROTE = r"^[^\w\n]{{0,3}}\w{{0,2}}N[°ºoº]\s*{rang}\s*/\s*{annee}\b"


def _objet_par_numero(texte: str, rang: int, annee: str) -> str | None:
    """L'objet écrit sous le bandeau qui porte le numéro de CET acte."""
    for m in re.finditer(_BANDEAU_NUMEROTE.format(rang=rang, annee=annee), texte, re.M):
        suivant = _BANDEAU_SUIVANT.search(texte, m.end())
        objet = _objet_declare(texte[m.end():suivant.start() if suivant else m.end() + 6000])
        if objet:
            return objet
    return None


def _objet_du_bandeau(amont: str, capitales: bool = True, rang: int | None = None) -> str | None:
    """L'objet de l'acte, resté dans le bloc PRÉCÉDENT.

    Le régime coupe sur le cachet de télétransmission, et ce cachet est imprimé
    en tête de la page qui porte la DÉCISION. Or la collectivité écrit l'objet
    dans le bandeau du registre, une page plus haut : il tombe donc à la fin du
    bloc d'avant, et l'acte se retrouvait sans titre alors que son objet était
    écrit noir sur blanc, quelques lignes au-dessus de la coupure.

    Mesuré sur les liasses de la première commune portée : 17 titres sur 246,
    dont les 13 « Délibération n° … » de 2026, deux en-têtes de tableau pris
    pour des titres (« COÛT TOTAL PRÉVISIONNEL (HT) 401 906.00 € ») et un
    charabia d'océrisation (« EMPLO BA DE CAATTEE SORIE EFFECTIF »).

    ⚠️ C'est l'ancre la PLUS PROCHE de la coupure qui vaut, et elle seule : plus
    haut, le bandeau rencontré est celui d'un acte antérieur. Le titre serait
    alors une phrase bien présente dans le texte, mais qui n'appartient pas à
    l'acte qu'elle nommerait — une citation prouve la présence, pas
    l'appartenance.
    """
    fenetre = amont[-AMONT_DU_BANDEAU:]
    dernier = None
    for dernier in _NUMERO_DE_REGISTRE.finditer(fenetre):
        pass
    if dernier is None:
        return None
    # 🔴 Le bandeau doit être celui de CET acte : entre le bandeau d'un acte et
    # la coupure, il n'y a que son préambule. Un cachet intercalé signe un acte
    # déjà tamponné, un vote un acte déjà DÉCIDÉ — le bandeau est alors celui
    # d'un acte d'avant, dont le titre serait cité fidèlement, et faux. Sans ce
    # garde, un acte sans bandeau héritait de l'objet de son prédécesseur.
    # L'ancre elle-même peut porter le numéro d'un AUTRE acte (« N°58/2024 »).
    numero = re.search(r"(\d{1,4})\s*/\s*(?:19|20)\d\d\b", dernier.group())
    if rang is not None and numero and int(numero.group(1)) != rang:
        return None
    suite = fenetre[dernier.end():]
    if _REFERENCE_TOLERANTE.search(suite) or _FORMULE_DE_VOTE.search(suite):
        return None
    # Les liasses intercommunales écrivent l'objet en toutes lettres sous la
    # liste des présents — « Objet : Pôle nature 4 saisons… », vingt lignes
    # sous le numéro et en minuscules : la ligne capitale ne le voyait pas, et
    # le n° 35 du 03/03/2021 portait le titre d'un autre acte.
    objet = _objet_declare(suite, rang)
    if objet or not capitales:
        return objet
    for ligne in suite.splitlines()[:LIGNES_APRES_LE_NUMERO]:
        objet = _ligne_dobjet(ligne)
        if objet:
            return objet
    return None


def _titre_dacte(corps: str, rang: int, amont: str = "",
                 capitales: bool = True) -> tuple[str, str]:
    """L'objet de l'acte, et D'OÙ il vient — les deux, jamais l'un sans l'autre.

    🔴 Le repli ne peut pas prendre la première ligne capitale VENUE : ce régime
    coupe SUR le cachet, donc la première ligne du corps est le cachet, et les
    suivantes sont le timbre de la collectivité. Toutes sont en capitales, et
    toutes passaient. L'acte s'appelait alors du nom de l'ancre qui l'avait
    découpé — une preuve affichée à la place de l'objet qu'elle ne dit pas.

    Quand rien ne reste, le repli numéroté est la bonne réponse : il avoue que
    le découpage a trouvé l'acte sans trouver son objet. C'est la règle du
    régime — un acte tu se corrige à la saisie, un acte inventé se publie.

    ⚖️ L'origine est rendue avec le titre parce qu'un titre deviné et un objet
    lu ne valent pas la même chose, et que rien ne les distingue une fois écrits
    en base. `objet_declare` et `objet_registre` sont écrits par la
    collectivité ; `ligne_capitale` est une lecture ; `repli_numerote` avoue.
    """
    # 🔴 La recherche s'arrête à la formule de vote. Sans cette borne elle
    # traversait tout l'acte et rapportait un en-tête de TABLEAU — « EMPLOI
    # GRADE CATEGORIE DURÉE HEBDOMADAIRE », « CREDIT A OUVRIR COMPTE DEPENSE ».
    # L'objet, lui, est toujours dans le bandeau de tête : au-delà, il n'y en a
    # pas, et c'est le repli numéroté qu'il faut, pas le premier tableau venu.
    coupe = _FORMULE_DE_VOTE.search(corps)
    tete = corps[:coupe.start()] if coupe else corps

    objet = _objet_declare(tete, rang)
    if objet:
        return objet, "objet_declare"

    # Avant la ligne capitale du corps, et non après : sur la liasse du
    # 10/09/2026, la première ligne capitale de deux actes était l'en-tête de
    # leur plan de financement, « COÛT TOTAL PRÉVISIONNEL (HT) 401 906.00 € ».
    # Un acte qui porte son bandeau dans son propre corps n'en trouve pas en
    # amont : le vote de l'acte précédent l'en sépare.
    objet = _objet_du_bandeau(amont, capitales, rang)
    if objet:
        return objet, "objet_registre"

    # Cf. `OBJETS_DECLARES_MINIMUM` : là où la collectivité écrit « Objet : »,
    # une ligne capitale n'est pas son objet.
    for ligne in (tete.splitlines() if capitales else ()):
        objet = _ligne_dobjet(ligne)
        if objet:
            return objet, "ligne_capitale"
    return f"Délibération n° {rang}", "repli_numerote"


def _titrer(corps: str, rang: int, annee: str, texte: str, debut: int,
            capitales: bool) -> tuple[str, str]:
    """Le titre d'un acte de liasse et son origine, quel que soit le découpage."""
    # Tout l'amont, et non le bloc précédent : le PREMIER acte d'une liasse
    # a lui aussi son bandeau au-dessus de son cachet, et aucun bloc avant
    # lui pour le porter.
    titre, origine = _titre_dacte(corps, rang, texte[:debut], capitales)
    if origine == "repli_numerote":
        objet = _objet_par_numero(texte, rang, annee)
        if objet:
            titre, origine = objet, "objet_registre"
        elif (decision := _titre_de_la_decision(corps, rang)):
            titre, origine = decision, "clause_decisive"
    return titre, origine


def _actes_teletransmis(texte: str, pagine: bool = True) -> list[dict]:
    marques = list(_REFERENCE_TOLERANTE.finditer(texte))
    if len(marques) < 3:
        return []
    numeros = [m.group(4) for m in marques]
    suffixe = _suffixe_de_seance(numeros)
    # Cherché SEULEMENT quand aucun suffixe ne s'est trouvé : là où le millésime
    # est en fin, le rang se lit déjà et n'a pas à être retouché.
    prefixe = _prefixe_de_seance(numeros) if not suffixe else ""
    # ⚖️ Deux nombres, et il ne faut pas les confondre. `rang` est l'IDENTITÉ de
    # l'acte — `enregistrer_deliberation` retrouve une délibération par lui, et
    # par lui seul, sans la date. Le retirer du préfixe ferait de « 260602 » un
    # n° 2 qui entrerait en collision avec le n° 2 de toutes les autres séances
    # de l'année : l'acte suivant écraserait le précédent, en silence. `lisible`
    # est le rang POUR L'ŒIL, celui que le repli affiche quand l'objet manque.
    lus = [(m, _rang_dans_la_seance(m.group(4), suffixe),
            _rang_dans_la_seance(m.group(4), suffixe, prefixe))
           for m in marques]
    lus = [(m, r, lisible) for m, r, lisible in lus if r is not None]

    # Les occurrences successives d'un même rang sont les pages d'un seul acte.
    blocs: list[tuple] = []
    for m, rang, lisible in lus:
        if not blocs or blocs[-1][1] != rang:
            blocs.append((m, rang, lisible))
    gardes = _suite_croissante([r for _, r, _ in blocs])

    # 🔴 Un procès-verbal de séance est lui-même télétransmis : il porte UN
    # cachet, parfois deux, et le prendre pour une liasse le réduirait à une
    # seule délibération en écartant les régimes qui savent le découper. Ce
    # régime ne vaut que pour une LIASSE, et trois actes numérotés à la suite
    # sont le moins qu'on puisse en exiger.
    if len(gardes) < 3:
        return []

    capitales = len(_OBJET.findall(texte)) < OBJETS_DECLARES_MINIMUM
    sorties = []
    for i, indice in enumerate(gardes):
        marque, rang, lisible = blocs[indice]
        debut = texte.rfind("\n", 0, marque.start()) + 1
        fin = (texte.rfind("\n", 0, blocs[gardes[i + 1]][0].start()) + 1
               if i + 1 < len(gardes) else len(texte))
        corps = texte[debut:fin].strip()
        if not corps:
            continue
        titre, origine = _titrer(corps, lisible if lisible is not None else rang,
                                 marque.group(3)[:4], texte, debut, capitales)
        sorties.append(_enrichir({
            "regime": "actes_teletransmis",
            "numero_seance": None,
            "numero_acte": str(rang),
            "titre": titre,
            "titre_origine": origine,
            "texte": corps,
        }))
    return sorties


# ── Découpage À LA PAGE d'une liasse (lot 2, chantier « lire les montants ») ──
#
# `_actes_teletransmis` coupe sur la LIGNE du premier cachet d'un nouveau
# numéro. Mesuré par l'audit du 23/10/2026 sur les recueils de l'intercommunalité
# (2022-2026) : 13 actes sur 213 fusionnés ou décalés — l'orientation de 2024 et
# celle de 2025 dans l'acte voisin, un tableau de subventions coupé en deux. Un
# acte télétransmis commence pourtant toujours sur une page NEUVE : quand le
# texte garde ses débuts de page (le manifeste `x.ocr.json`, depuis le 24/10),
# la coupure se fait au début de la page, plus au milieu.
#
# Deux règles, et pas une de plus :
#   - une page reçoit le numéro le plus fréquent de ses cachets ; une page SANS
#     cachet lisible continue l'acte en cours ;
#   - quand la page du premier cachet d'un acte n'en porte pas l'OUVERTURE
#     (« L'an deux mille… », « Objet : », « Extrait du registre »), la page sans
#     cachet la plus proche en amont qui la porte est la première page de cet
#     acte : l'océrisation a perdu le cachet de la page de tête. Saillans,
#     30/06/2022 : l'acte 10 s'ouvre page 17, cachet illisible ; la coupure au
#     cachet donnait sa première page, et son objet, à l'acte 9.
#
# Le contrôle de la suite des numéros est rendu avec le découpage : un numéro
# manquant entre le premier et le dernier, un cachet hors de la suite, une
# ouverture déplacée se COMPTENT. Rien ici n'est encore branché sur la collecte :
# `scripts/mesurer_decoupage_pages.py` le compare au découpage actuel d'abord.
_OUVERTURE_DACTE = re.compile(
    r"L['’]an\s+deux\s+mille|^[^\n]{0,12}?Ob[jil1í]et\s*[:;]|EXTRAIT\s+du\s+registre",
    re.I | re.M)
# La formule d'ouverture se trouve en tête d'acte ; plus bas, c'est une citation.
ZONE_DOUVERTURE = 2500


# Ce qui vaut le mieux, d'abord : écrit par la collectivité, puis lu, puis avoué.
_RANG_DORIGINE = {o: i for i, o in enumerate(
    ("objet_declare", "objet_registre", "clause_decisive", "ligne_capitale", "repli_numerote"))}


def actes_par_page(pages: list[str]) -> dict:
    """Découpe une liasse page par page, et contrôle la suite de ses numéros.

    Rend `{"actes": [...], "controle": {...}}`. Chaque acte a la forme de ceux
    de `_actes_teletransmis`, plus `pages` : [première, dernière], comptées à
    partir de 1. `actes` est vide quand le document n'est pas une liasse (moins
    de trois actes numérotés à la suite) — la même porte que le régime actuel.
    """
    vide = {"actes": [], "controle": {}}
    marques = [list(_REFERENCE_TOLERANTE.finditer(p)) for p in pages]
    numeros = [m.group(4) for ms in marques for m in ms]
    if len(numeros) < 3:
        return vide
    suffixe = _suffixe_de_seance(numeros)
    prefixe = _prefixe_de_seance(numeros) if not suffixe else ""

    from collections import Counter
    lus: list[tuple | None] = []          # par page : (rang, lisible, marque) ou None
    for ms in marques:
        rangs = Counter(r for m in ms
                        if (r := _rang_dans_la_seance(m.group(4), suffixe)) is not None)
        if not rangs:
            lus.append(None)
            continue
        rang = rangs.most_common(1)[0][0]
        marque = next(m for m in ms if _rang_dans_la_seance(m.group(4), suffixe) == rang)
        lus.append((rang, _rang_dans_la_seance(marque.group(4), suffixe, prefixe), marque))

    blocs: list[tuple] = []               # (première page, rang, lisible, marque)
    for i, lu in enumerate(lus):
        if lu is not None and (not blocs or blocs[-1][1] != lu[0]):
            blocs.append((i, *lu))
    gardes = _suite_croissante([b[1] for b in blocs])
    if len(gardes) < 3:
        return vide

    debuts: list[int] = []
    deplacees = []
    for indice in gardes:
        page, rang = blocs[indice][0], blocs[indice][1]
        borne = debuts[-1] + 1 if debuts else 0
        # La page du premier cachet qui porte elle-même l'ouverture EST la
        # première page. Sinon, l'ouverture la plus proche en amont, parmi les
        # pages sans cachet : une plus lointaine ouvre un autre acte, dont le
        # cachet est perdu — Saillans, 07/04/2022 : en prenant la plus lointaine,
        # l'acte 3 avalait l'acte 2 et son tableau des subventions.
        if not _OUVERTURE_DACTE.search(pages[page][:ZONE_DOUVERTURE]):
            j = page
            while j - 1 >= borne and lus[j - 1] is None:
                j -= 1
                if _OUVERTURE_DACTE.search(pages[j][:ZONE_DOUVERTURE]):
                    deplacees.append({"numero": rang, "de": page + 1, "a": j + 1})
                    page = j
                    break
        debuts.append(page)

    texte = "\n".join(pages)
    positions = [sum(len(p) + 1 for p in pages[:i]) for i in range(len(pages))]
    capitales = len(_OBJET.findall(texte)) < OBJETS_DECLARES_MINIMUM
    actes = []
    for k, indice in enumerate(gardes):
        _, rang, lisible, marque = blocs[indice]
        premiere = debuts[k]
        derniere = debuts[k + 1] - 1 if k + 1 < len(debuts) else len(pages) - 1
        bloc = "\n".join(pages[premiere:derniere + 1])
        corps = bloc.strip()
        if not corps:
            continue
        # Deux lectures du titre quand la page d'ouverture porte le cachet : depuis
        # le haut de la page, et depuis le cachet, comme le régime l'a toujours
        # fait. La meilleure origine l'emporte. Le bandeau de tête peut être mal
        # relu — Lasalle, 09/02/2022 : « N°2/2022 » pour l'acte 9, et la garde
        # d'appartenance refusait son objet lu depuis le haut ; un objet écrit
        # AU-DESSUS du cachet, lui, ne se lit que depuis le haut.
        lectures = [(0, corps)]
        if premiere == blocs[indice][0]:
            depart = pages[premiere].rfind("\n", 0, marque.start()) + 1
            if depart:
                lectures.append((depart, bloc[depart:].strip()))
        titres = [_titrer(c, lisible if lisible is not None else rang, marque.group(3)[:4],
                          texte, positions[premiere] + d, capitales) for d, c in lectures]
        titre, origine = min(titres, key=lambda t: _RANG_DORIGINE.get(t[1], len(_RANG_DORIGINE)))
        actes.append(_enrichir({
            "regime": "actes_teletransmis",
            "numero_seance": None,
            "numero_acte": str(rang),
            "titre": titre,
            "titre_origine": origine,
            "texte": corps,
            "pages": [premiere + 1, derniere + 1],
        }))

    # La suite se contrôle sur le rang POUR L'ŒIL : l'identité garde le
    # millésime collé (« 260602 »), et entre 260602 et 260615 il ne manque
    # pas 260 000 actes. Un rang encore invraisemblable rend la suite illisible.
    rangs = [blocs[i][2] if blocs[i][2] is not None else blocs[i][1] for i in gardes]
    lisible = max(rangs) <= RANG_MAX_PLAUSIBLE
    retenus = set(gardes)
    hors_suite = [{"cachet": b[3].group(4).strip(), "page": b[0] + 1}
                  for i, b in enumerate(blocs) if i not in retenus]
    return {"actes": actes, "controle": {
        "premier": min(rangs), "dernier": max(rangs), "suite_lisible": lisible,
        "manquants": sorted(set(range(min(rangs), max(rangs) + 1)) - set(rangs)) if lisible else [],
        "hors_suite": hors_suite,
        "pages_sans_cachet": sum(1 for lu in lus if lu is None),
        "ouvertures_deplacees": deplacees,
    }}


def deliberations_a_la_page(texte: str, pages: list[str] | None,
                            ecarts: Counter | None = None) -> tuple[list[dict], dict]:
    """Le découpage à la page quand le texte garde ses pages, et son contrôle.

    Rend `([], {})` quand les pages manquent, ne recomposent pas le texte, ou que
    le document n'est pas une liasse : l'appelant passe alors à `deliberations`.
    Branché le 25/10/2026 après mesure sur les 38 liasses de Lasalle (868 actes,
    mêmes numéros ; 57 premières pages rendues à leur acte, 34 objets retrouvés).
    """
    if not pages or "\n".join(pages) != texte:
        return [], {}
    decoupe = actes_par_page(pages)
    if not decoupe["actes"]:
        return [], {}
    return _recoller(decoupe["actes"], ecarts), decoupe["controle"]


def deliberations(texte: str, pagine: bool = True,
                  ecarts: Counter | None = None) -> list[dict]:
    """Découpe un procès-verbal, ou rend [] si aucun régime ne s'applique.

    `pagine` dit si le document a des pages — un PDF en a, une page web n'en a
    pas. Deux régimes s'en servent pour ne pas confondre l'en-tête d'une page
    avec la ligne de colonnes d'un tableau ; cf. `_sans_entetes`.

    `ecarts`, s'il est donné, compte ce que le découpage a écarté et pourquoi :
    la fiche de séance le garde, pour que rien ne disparaisse sans motif.
    """
    for analyseur in (_actes_teletransmis, _numerote, _acte_final, _puces,
                      _liste, _capitales):
        sorties = analyseur(texte, pagine)
        if sorties:
            if analyseur is _numerote and ecarts is not None:
                renvois = sum(1 for m in ENTETE_NUMEROTE.finditer(texte)
                              if _est_un_renvoi(texte, m))
                if renvois:
                    ecarts["numero_en_renvoi_ignore"] += renvois
            # Filtré ICI et non dans chaque régime : la règle vaut pour tous, et
            # les faux actes venaient aussi bien des PV de la commune que de ceux
            # de l'intercommunalité.
            return _recoller(sorties, ecarts)
    return []


def acte_unique(titre: str, texte: str, numero_acte: str = "",
                numero_seance: str | None = None) -> dict:
    """Une délibération publiée SEULE, rendue dans la forme des analyseurs.

    Un portail de publicité légale ne dépose pas des séances mais des actes, un
    par fichier, avec leur numéro et leur objet déclarés par la collectivité.
    Il n'y a alors rien à découper, et surtout rien à deviner : le régime
    s'appelle `acte_seul` pour que la base dise d'où vient le découpage — ici,
    d'aucun. L'enrichissement (catégorie, vote, montants) reste le même que
    pour un acte tiré d'un procès-verbal : c'est le même texte de délibération.
    """
    return _enrichir({
        "regime": "acte_seul",
        "numero_seance": numero_seance,
        "numero_acte": numero_acte or "",
        "titre": (re.sub(r"\s+", " ", titre or "").strip(" :;.-") or
                  f"Délibération {numero_acte}".strip())[:255],
        "texte": texte or "",
    })


def _recoller(sorties: list[dict], ecarts: Counter | None = None) -> list[dict]:
    """Écarte les faux titres et REND LEUR TEXTE à l'acte qu'ils coupaient.

    Un titre invraisemblable ne signale pas seulement un acte de trop : il
    signale que le découpage s'est trompé d'endroit, et que les deux blocs n'en
    font qu'un. Les jeter avec leur contenu perdait la matière — le compte rendu
    du 27/04/2026 de Lasalle ouvre un bloc sur la cellule « DEPENSES
    D'INVESTISSEMENT » et lui donne 34 748 caractères, soit les tableaux des huit
    budgets votés ce soir-là. Refuser le titre sans recoller le texte revenait à
    supprimer les huit budgets au lieu de les rattacher à « BUDGET PRINCIPAL ».
    """
    ecarts = Counter() if ecarts is None else ecarts
    gardees: list[dict] = []
    dans_rubrique = False
    for d in sorties:
        # La rubrique des décisions par délégation part ENTIÈRE : recollé à
        # l'acte d'avant, son texte serait publié comme extrait d'une
        # délibération qui n'en dit rien — et il nomme parfois l'agent ou le
        # particulier concerné (avis DPO du 14/11/2026). ENTIÈRE : les blocs qui
        # la suivent sans titre plausible (« de louer le logement… à M. … »)
        # sont encore elle, jusqu'au prochain objet (relecture vigie-securite
        # du 15/11/2026, qui les a vus recollés à l'acte d'AVANT la rubrique).
        if rubrique_de_decisions(d.get("titre")):
            dans_rubrique = True
            ecarts["rubrique_des_decisions"] += 1
            continue
        if dans_rubrique and _DECISION_NUMEROTEE.match(d.get("titre") or ""):
            ecarts["suite_de_rubrique"] += 1
            continue
        if _titre_plausible(d.get("titre", "")):
            dans_rubrique = False
            gardees.append(d)
            continue
        if dans_rubrique:
            ecarts["suite_de_rubrique"] += 1
            continue
        if not gardees:
            ecarts["titre_refuse_sans_acte_avant"] += 1
            continue
        ecarts["titre_refuse_recolle"] += 1
        recolle = "\n".join(x for x in (d.get("titre"), d.get("texte")) if x)
        gardees[-1]["texte"] = f"{gardees[-1]['texte']}\n{recolle}".strip()
        _enrichir(gardees[-1])
    return gardees


def _enrichir(d: dict) -> dict:
    d["categorie"], d["tags"] = categorize(d["titre"])
    d["vote"] = extract_vote(d["texte"])
    d["montants"] = extract_amounts(d["texte"])
    return d


# La fin de ligne d'un visa qui CITE un acte : « Vu la délibération … N° ⏎
# 54/2025 en date du… ». Le numéro qui ouvre la ligne suivante est un renvoi,
# pas l'en-tête d'un acte de la séance (conseil communautaire du 04/03/2026 de
# Lasalle : cinq « délibérations » intitulées « en date du 2/4/2025 et de la
# commune de… »).
_RENVOI_EN_FIN_DE_LIGNE = re.compile(
    r"(?:\bn[°ºo]\.?|\bnum[ée]ros?|\bd[ée]lib[ée]rations?|\bl['’]acte)[ \t]*\n[ \t]*$", re.I)


def _est_un_renvoi(texte: str, m: re.Match) -> bool:
    return bool(_RENVOI_EN_FIN_DE_LIGNE.search(texte, max(0, m.start() - 80), m.start()))


def _numerote(texte: str, pagine: bool = True) -> list[dict]:
    marques = [m for m in ENTETE_NUMEROTE.finditer(texte) if not _est_un_renvoi(texte, m)]
    sorties = []
    for i, m in enumerate(marques):
        fin = marques[i + 1].start() if i + 1 < len(marques) else len(texte)
        corps = texte[m.end():fin].strip()
        if not corps:
            continue
        coupe = FIN_TITRE.search(corps)
        titre = re.sub(r"\s+", " ", corps[:coupe.start()] if coupe else corps[:300])
        titre = titre.strip(" :;.-")
        if len(titre) < 5:
            continue
        sorties.append(_enrichir({
            "regime": "numerote",
            "numero_seance": f"{m.group(1)}/{m.group(2)}",
            "numero_acte": m.group(3),
            "titre": titre[:255],
            "texte": corps,
        }))
    return sorties


# Une ligne de vote ou qui nomme quelqu'un n'appartient jamais à l'objet d'un
# acte : « Abstention : NOM Prénom », « Indemnité versée à Mme … ». Remonter sur
# elle mettait un nom dans le titre (relecture vigie-securite du 15/11/2026).
_LIGNE_DE_VOTE_OU_DE_PERSONNE = re.compile(
    r"\b(?:M\.|MM\.|Mmes?\b|Mlle\b|Monsieur|Madame|Mademoiselle|pour\s*:|contre\s*:"
    r"|abstentions?\b|unanimit|adopt[ée]e?s?\b|votants?\b|exprim[ée]s\b|suffrages?\b"
    r"|pr[ée]sents?\s*:|absents?\b|pouvoirs?\s*:|procurations?\b"
    r"|secr[ée]taire\s+de\s+s[ée]ance|\bmembres\s*:)", re.I)

# Une ligne qui s'arrête sur un mot qui en appelle un autre : la phrase continue
# à la ligne suivante.
_FIN_SUSPENDUE = re.compile(
    r"(?:\b(?:de|du|des|d['’]|la|le|les|l['’]|et|ou|à|au|aux|en|pour|par|sur|avec|dans"
    r"|entre|vers)|[–—\-,])$", re.I)


def _acte_final(texte: str, pagine: bool = True) -> list[dict]:
    """L'identifiant clôt la ligne du titre ; le titre peut déborder au-dessus.

    Deux mises en forme coexistent dans le même corpus : titres en capitales sur
    plusieurs lignes, et titres en casse normale sur une ligne. Ne remonter que
    sur les lignes capitales rendait « Délibération DE_2026_049 » pour tout le
    second format, soit la moitié du corpus sans titre.
    """
    marques = list(ACTE_FINAL.finditer(texte))
    if not marques:
        return []
    debut = 0
    ouverture = re.search(r"Délibérations du conseil\s*:", texte, re.I)
    if ouverture:
        debut = ouverture.end()

    sorties, precedent, curseur = [], None, debut
    for m in marques:
        lignes = texte[curseur:m.start()].splitlines()
        titre_lignes = [lignes.pop().strip()] if lignes else []
        for _ in range(3):
            if not lignes:
                break
            # 🔴 Un objet écrit sur deux lignes ne gardait que la seconde quand
            # elle commençait par une capitale ou une minuscule accentuée :
            # « à temps complet », « ET PLATEAUX 2025 », « Val d'Agout »,
            # « SOLUTIONS » (relevés de décisions de la CC de Brassac, servis
            # au 15/11/2026). La ligne d'au-dessus appartient au titre quand
            # elle ne clôt pas une phrase et que la suite ne se tient pas seule.
            premiere, dessus = titre_lignes[0] or "x", lignes[-1].strip()
            debute_mal = (bool(re.match(r"[a-z0-9«»(,]", premiere))
                          or titre_en_fragment(premiere))
            # Jamais au-dessus d'un montant : « Entreprise SEIHE, pour un
            # montant de 116 084,64 €HT » finit l'acte d'avant, pas le titre.
            # Pas de remontée sur un titre « court » : la ligne d'au-dessus
            # pouvait être un nom sans civilité (« Bail consenti à Albert
            # Fictif »), qui entrait dans le titre (relecture vigie-securite).
            suspendue = (bool(dessus) and not re.search(r"[.;:!?]$", dessus)
                         and not _MONTANT_TITRE.search(dessus)
                         and bool(_FIN_SUSPENDUE.search(dessus)))
            if _LIGNE_DE_VOTE_OU_DE_PERSONNE.search(dessus):
                break
            if LIGNE_CAPITALES.match(dessus) or debute_mal or suspendue:
                titre_lignes.insert(0, lignes.pop().strip())
            else:
                break
        # Remonter peut entrer dans la phrase d'avant (« de prendre en compte
        # les surcoûts liés au marché « Alimentation… »), ou dans un tableau
        # (« TOTAL RECETTES … ADOPTE A L'UNANIMITE »). Ces lignes rendues à
        # l'acte d'avant, il reste l'objet ; s'il ne reste qu'un fragment, le
        # repli numéroté l'avoue. L'identifiant prouve l'acte : on ne le perd
        # pas pour un titre.
        origine = None
        while titre_lignes and titre_en_fragment(" ".join(titre_lignes)):
            lignes.append(titre_lignes.pop(0))
        if not titre_lignes:
            origine = "repli_numerote"
        if precedent is not None:
            precedent["texte"] = "\n".join(lignes).strip()
        titre = re.sub(r"\s+", " ", " ".join(titre_lignes)).strip(" :;.-")
        precedent = {"regime": "acte_final", "numero_seance": None,
                     "numero_acte": m.group(1),
                     "titre": (titre or f"Délibération {m.group(1)}")[:255],
                     "texte": "",
                     **({"titre_origine": origine} if origine else {})}
        sorties.append(precedent)
        curseur = m.end()
    if precedent is not None:
        precedent["texte"] = texte[curseur:].strip()
    return [_enrichir(d) for d in sorties]


def _puces(texte: str, pagine: bool = True) -> list[dict]:
    marques = list(PUCE.finditer(texte))
    sorties = []
    for i, m in enumerate(marques):
        fin = marques[i + 1].start() if i + 1 < len(marques) else len(texte)
        sorties.append(_enrichir({
            "regime": "puces", "numero_seance": None, "numero_acte": None,
            "titre": re.sub(r"\s+", " ", m.group(1)).strip(" :;.-")[:255],
            "texte": texte[m.end():fin].strip(),
        }))
    return sorties


# Ce qu'une ligne garde quand on lui ôte ce que l'océrisation abîme : la casse,
# les accents, la ponctuation, et la VALEUR des nombres — jamais les nombres
# eux-mêmes, réduits à une marque. Les supprimer ferait d'une ligne de tableau
# budgétaire la copie exacte de sa voisine, et `_sans_entetes` les emporterait
# toutes les deux : « Chap 011 Charges de personnel 5 000,00 » et « Chap 012
# Achats 7 000,00 » doivent rester DISTINCTES, et elles le restent par leur
# libellé. Ce qui doit se rejoindre, ce sont les graphies successives d'un même
# en-tête : « ID : 030-213001407-20260630-DEL2606_12-DE » et
# « ID :690-218001407-20260680-DEL2606 19-DE » sont la même ligne, deux fois.
def _forme_normalisee(ligne: str) -> str:
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFKD", ligne)
        if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z#]+", " ", re.sub(r"\d+", "#", sans_accent)).strip()


def _sans_entetes(texte: str, pagine: bool = True) -> str:
    """Retire les lignes répétées d'un document PAGINÉ : en-tête et pied de page.

    Un procès-verbal de vingt-quatre pages répète son en-tête vingt-quatre
    fois. Le découpeur par capitales en fait autant de titres, et la séance
    ressort avec deux fois plus de délibérations qu'elle n'en a pris.

    Une page web n'a pas de pages, donc pas d'en-tête de page — mais elle a des
    TABLEAUX, dont la ligne de colonnes se répète à chaque tableau. Le compte
    rendu du 27/04/2026 de Lasalle vote huit budgets : « Chap / Art », « Intitulé »
    et « BP 2026 » y reviennent vingt fois chacun, et étaient retirés comme un
    en-tête de page. L'acte perdait sa ligne de colonnes, `budgets_votes` n'y
    trouvait plus une seule ancre, et les huit budgets — dont la Cantine à
    162 724,13 € — n'étaient nulle part. Appliquer à une page web le remède d'un
    PDF, c'est soigner un mal qu'elle n'a pas.

    🔴 Le comptage portait sur des chaînes IDENTIQUES, et c'est ce qui le rendait
    inopérant sur un document océrisé : **la reconnaissance optique n'écrit
    jamais deux fois le même en-tête**. Le PV du 30/06/2026 de Lasalle répète le
    sien sur ses 47 pages, en 47 graphies différentes — « ID : 030-213001407-… »,
    « ID :690-218001407-… », « ID :030-213001407-20260680-DEL2608. 14-DE ».
    `Counter` n'en voyait aucune quatre fois, aucune n'était retirée, et
    `_capitales` prenait chacune pour le titre d'une délibération : 37 actes
    produits pour 5 réels. La protection était défaite par le bruit même dont
    elle devait protéger. Le comptage porte désormais sur la FORME NORMALISÉE.

    ⚠️ Le seuil reste ABSOLU, et ce n'est pas par paresse. Normaliser regroupe
    plus largement, et j'ai d'abord cru qu'il fallait proportionner le seuil au
    nombre de pages — du mobilier paraît une fois par page. La mesure dit le
    contraire, sur les deux corpus de Lasalle à la fois : à `max(4, pages//3)`,
    les 22 premiers PV scannés rendent 1 546 actes contre 748 à quatre, et les
    60 PV sains 933 contre 868. Un seuil qui monte avec la longueur du document
    laisse passer le mobilier des documents longs, qui sont précisément les plus
    abîmés. Quatre, mesuré meilleur des deux côtés.

    🔴 Une ligne qui porte un MONTANT n'est jamais du mobilier, et rien ne la
    retire. C'est la garde qui manquait, et son absence a fait revenir le défaut
    de la Cantine par une autre porte. Marquer les nombres rend identiques des
    lignes qui ne diffèrent que par leurs sommes : un procès-verbal qui vote
    huit budgets aligne huit fois « Section de fonctionnement : Dépenses …
    Recettes … », que le comptage voyait alors comme un en-tête répété. Or le
    texte nettoyé ici ne sert pas qu'à trouver les titres — c'est LUI qui
    devient le corps de l'acte, et `extract_amounts` y puise. Mesuré sur les 134
    procès-verbaux textuels de Lasalle avant la garde : 240 600 € du Fonds Vert,
    401 000 € HT d'une opération, 2 200 € de subvention à une association
    disparaissaient du corps où on venait les lire. Du mobilier de page ne porte
    jamais d'euros ; la garde ne coûte donc rien à ce qu'elle doit retirer.
    """
    if not pagine:
        return texte
    from collections import Counter
    lignes = texte.splitlines()
    compte = Counter(_forme_normalisee(l) for l in lignes
                     if l.strip() and not _MONTANT_TITRE.search(l))
    repetees = {f for f, n in compte.items() if n >= 4 and 0 < len(f) < 60}
    return "\n".join(l for l in lignes
                     if not l.strip() or _MONTANT_TITRE.search(l)
                     or _forme_normalisee(l) not in repetees)


def _apres_entete(texte: str) -> str:
    """Le corps commence après le bloc de présences, jamais avant.

    Sans cette borne, la liste des présents — en capitales, elle aussi — ouvre
    une première « délibération » intitulée du nom des conseillers.
    """
    for motif in (r"[Ss]ecrétaire de séance", r"^_{5,}$", r"ORDRE DU JOUR"):
        m = re.search(motif, texte, re.M)
        if m:
            return texte[m.end():]
    return texte


# ── Régime « liste » : une suite d'ordinaux ──────────────────────────────────
#
# Ce régime a longtemps tenu son autorisation d'un EN-TÊTE : « Délibérations : »
# ouvrait la liste, la levée de séance ou la signature la fermait, et chaque
# ligne de l'intervalle devenait un acte. Sa propre note disait : « Découpage
# fiable tant que l'en-tête est là — c'est lui qui l'autorise, pas la mise en
# page. » La mesure du 21/08/2026 sur 389 procès-verbaux de trois communes a
# démenti la prémisse : les 21 documents découpés par ce régime l'étaient TOUS
# à tort, et ils fournissaient 4 337 des 5 529 actes du corpus de Lasalle.
#
# Deux raisons, toutes deux fatales. « Délibération : » AU SINGULIER introduit
# le texte d'UNE délibération à l'intérieur d'un procès-verbal suivi — quatorze
# des seize documents de Lasalle. Et la formule de clôture ne venait jamais :
# les intercommunalités écrivent « La séance se termine à 12h50 ». Les 57 521
# caractères qui restaient après l'en-tête du 02/10/2019 sont donc devenus 497
# « délibérations » d'une ligne : « Vu la saisine du CT », « Risque de
# contentieux », « Camille Velirand ».
#
# Ce qui prouve une liste, ce n'est pas son en-tête, c'est sa NUMÉROTATION.
# Une suite d'ordinaux qui progresse d'un en un — « 1. », « 01. », « III. »,
# « XVI. » — ne se rencontre pas par hasard, et elle s'arrête d'elle-même :
# aucune formule de clôture n'est nécessaire. Une ligne numérotée isolée ne
# prouve rien ; trois qui se suivent, si. Le régime rejoint ainsi `numerote` et
# `acte_final` : il découpe sur une preuve, non sur un indice.

# Les deux alphabets. Le point ou la parenthèse ferme l'ordinal ; ce qui suit ne
# doit pas être un chiffre, sinon « L. 2224-13 du code général » et « IV. 2024 »
# passeraient pour des titres. « L » est exclu des romains pour la même raison :
# c'est le préfixe des articles du code général des collectivités.
ORDINAL_ARABE = re.compile(r"^(\d{1,2})\s*[.)]\s*(?=[^\W\d_])(\S.*)$")
ORDINAL_ROMAIN = re.compile(r"^([IVX]{1,7})\s*[.)]\s+(?=[^\W\d_])(\S.*)$")
_CHIFFRES_ROMAINS = {"I": 1, "V": 5, "X": 10}
# Trois ordinaux qui se suivent : en deçà, c'est une coïncidence.
MIN_SUITE = 3

FERMETURE_LISTE = re.compile(
    r"(La séance est levée|La séance se termine|Fait et délibéré|Le Maire,|"
    r"La Présidente,|Le Président,)", re.I)
# Le vote clôt la ligne : « … approuvé à l'unanimité », « … à la majorité ».
VOTE_EN_FIN = re.compile(
    r"[,\s]+((?:approuv|adopt|rejet|refus|valid)[eé]{1,2}s?\s+)?"
    r"[àa]\s+(?:l['’]unanimité|la majorité)[^.]*$", re.I)


def _valeur_romaine(chiffres: str) -> int:
    total = 0
    for i, c in enumerate(chiffres):
        v = _CHIFFRES_ROMAINS[c]
        suivant = _CHIFFRES_ROMAINS.get(chiffres[i + 1:i + 2] or "", 0)
        total += -v if suivant > v else v
    return total


def _plus_longue_suite(marques: list[tuple]) -> list[tuple]:
    """La plus longue série d'ordinaux qui progressent d'un en un.

    À longueur égale on garde la DERNIÈRE : l'ordre du jour énumère les mêmes
    intitulés en tête du document, mais c'est la seconde énumération qui porte
    le texte des délibérations. Découper sur l'ordre du jour rendrait des actes
    correctement titrés et vides.
    """
    meilleure: list[tuple] = []
    courante: list[tuple] = []
    for marque in marques:
        if courante and marque[1] == courante[-1][1] + 1:
            courante.append(marque)
            continue
        if len(courante) >= len(meilleure):
            meilleure = courante
        courante = [marque]
    return courante if len(courante) >= len(meilleure) else meilleure


# ⚠️ `[A-ZÀ-Ÿ]` n'est PAS « les majuscules ». C'est l'intervalle U+0041→U+005A
# plus U+00C0→U+0178, et cet intervalle-là traverse tout le bloc des minuscules
# accentuées : « à » (U+00E0) est entre « À » (U+00C0) et « Ÿ » (U+0178). La
# classe dite « majuscules » acceptait donc à é è ù ç ö ÿ œ ß ñ.
#
# Deux conséquences, et la seconde est la grave. D'abord elle reconnaît ce
# qu'elle prétend refuser. Ensuite elle RECOUVRE la classe des minuscules, et
# ce recouvrement rend le motif ci-dessous ambigu : dans « -à », le tiret peut
# être avalé par `[minuscules…]+` ou servir de séparateur `[- ]` suivi d'une
# « majuscule » qui accepte « à ». Deux lectures par répétition, et le moteur
# les essaie TOUTES avant de déclarer l'échec. Mesuré : 32 répétitions (67
# caractères) = 0,7 s, et ×2,6 toutes les deux répétitions de plus — une ligne
# de 115 caractères occupe la machine des heures. Ce motif s'applique au texte
# EXTRAIT DES PDF : une page océrisée en charabia suffit à figer la collecte,
# sans rien planter et sans rien dire.
#
# Les deux classes sont donc énumérées, et disjointes. Le × (U+00D7) et le
# ÷ (U+00F7) sont exclus des intervalles : ce sont des signes de
# multiplication, pas des lettres.
_MAJUSCULES = "A-ZÀ-ÖØ-ÞŒŠŽŸ"
_MINUSCULES = "a-zß-öø-ÿœšž"

# Une personne, et rien d'autre : « Noa VELOURET », « MARAGNOL Charlie ».
# Les conseils d'installation numérotent leurs conseillers, et le tableau du
# conseil est une suite d'ordinaux irréprochable — mais c'est un trombinoscope,
# pas un ordre du jour. Sur Brassac, une séance rendait ainsi dix-sept « actes »
# nommés d'après les élus présents.
_ITEM_PERSONNE = re.compile(
    rf"^(?:[{_MAJUSCULES}][{_MINUSCULES}'’\-]+"
    rf"(?:[- ][{_MAJUSCULES}][{_MINUSCULES}'’\-]+)*"
    rf"\s+[{_MAJUSCULES}][{_MAJUSCULES}'’\-]{{2,}}"
    rf"|[{_MAJUSCULES}][{_MAJUSCULES}'’\-]{{2,}}"
    rf"\s+[{_MAJUSCULES}][{_MINUSCULES}'’\-]+)"
    r"(?:\s+\d[\d/.,\s€%]*)?$")
# Au-delà, deux ordinaux consécutifs ne se touchent plus : du texte les sépare.
_ECART_TARANDIN = 2
# Un ordre du jour se lit en tête de document. Une énumération serrée qui arrive
# ensuite est interne à une délibération — « 1. Lui donne acte de la présentation
# du compte administratif, 2. Constate les identités de valeurs… » — et la
# prendre pour un ordre du jour émiette une délibération en cinq.
_TETE_DU_DOCUMENT = 0.25


def _est_un_ordre_du_jour(suite: list[tuple], total_lignes: int) -> bool:
    """La suite énumère-t-elle des délibérations, ou autre chose ?"""
    intitules = [t for _, _, t in suite]
    if sum(1 for t in intitules if _ITEM_PERSONNE.match(t.strip())) * 2 >= len(suite):
        return False
    ecarts = [b[0] - a[0] for a, b in zip(suite, suite[1:])]
    serree = bool(ecarts) and sorted(ecarts)[len(ecarts) // 2] <= _ECART_TARANDIN
    return not serree or suite[0][0] <= total_lignes * _TETE_DU_DOCUMENT


def _liste(texte: str, pagine: bool = True) -> list[dict]:
    """Une suite numérotée de délibérations, le corps de chacune en dessous."""
    lignes = [l.strip() for l
              in _apres_entete(_sans_entetes(texte, pagine)).splitlines() if l.strip()]
    suites = []
    for motif, valeur in ((ORDINAL_ARABE, int), (ORDINAL_ROMAIN, _valeur_romaine)):
        marques = []
        for i, ligne in enumerate(lignes):
            m = motif.match(ligne)
            if m:
                marques.append((i, valeur(m.group(1)), m.group(2)))
        suites.append(_plus_longue_suite(marques))
    suite = max(suites, key=len)
    if len(suite) < MIN_SUITE or not _est_un_ordre_du_jour(suite, len(lignes)):
        return []

    sorties = []
    for rang, (i, _, intitule) in enumerate(suite):
        fin = suite[rang + 1][0] if rang + 1 < len(suite) else len(lignes)
        corps = "\n".join(lignes[i + 1:fin])
        cloture = FERMETURE_LISTE.search(corps)
        if cloture and rang + 1 == len(suite):
            corps = corps[:cloture.start()].strip()
        titre = VOTE_EN_FIN.sub("", re.sub(r"\s+", " ", intitule)).strip(" ,;:.-–—")
        if len(titre) < 8:
            continue
        sorties.append(_enrichir({
            "regime": "liste", "numero_seance": None, "numero_acte": None,
            "titre": titre[:255], "texte": corps or intitule,
        }))
    return sorties


#: Au-delà, des lignes capitales qui se suivent sont un en-tête de TABLEAU, pas
#: un titre sur plusieurs lignes : les rejoindre en ferait un titre de plus.
LIGNES_DE_TITRE_MAX = 3


def _titres_rejoints(lignes: list[str]) -> list[str]:
    """Un titre en capitales écrit sur deux ou trois lignes redevient une ligne.

    Le découpeur ouvre un bloc à chaque ligne capitale et abandonne celui qui
    n'a pas encore de corps : d'un objet sur deux lignes, il ne restait que la
    seconde. « RENOUVELLEMENT CONVENTION DE DELEGATION PARTIELLE DE ⏎ LA
    COMPETENCE EXTRASCOLAIRE A UNE COMMUNE » s'appelait « LA COMPETENCE
    EXTRASCOLAIRE A UNE COMMUNE » (Lasalle, 16/12/2025). Une suite de plus de
    `LIGNES_DE_TITRE_MAX` lignes reste telle quelle : c'est un tableau, et sa
    dernière ligne est refusée plus loin.

    Dans une suite courte, ne se rejoignent que deux lignes qui se SUIVENT :
    celle du dessus s'arrête sur un mot qui appelle la suite, ou celle du
    dessous commence au milieu d'une phrase sous une ligne sans virgule. Sinon la ligne capitale d'un corps
    — « VOTE : UNANIMITE SAUF NOM » — se collait devant l'objet suivant
    (relecture vigie-securite du 15/11/2026). Une ligne de vote ou qui nomme
    quelqu'un ne se rejoint jamais. Limite connue : « ADHESION A LA CONVENTION
    « PREVOYANCE » ⏎ PROPOSEE PAR LE CENTRE DE GESTION » reste coupée.
    """
    def se_suivent(dessus: str, dessous: str) -> bool:
        dessus, dessous = (x.replace("\xa0", " ").strip() for x in (dessus, dessous))
        if (_LIGNE_DE_VOTE_OU_DE_PERSONNE.search(dessus)
                or _LIGNE_DE_VOTE_OU_DE_PERSONNE.search(dessous)):
            return False
        # Ou la ligne du dessous commence au milieu d'une phrase (« ET MEUBLES
        # DE TOURISME ») : refusée seule, elle emporterait l'acte entier dans
        # le texte du précédent. Pas sous une ligne à virgules : une liste de
        # noms en a, un objet rarement.
        return bool(_FIN_SUSPENDUE.search(dessus)
                    or (titre_en_fragment(dessous) and "," not in dessus))

    def rendre(suite: list[str]) -> list[str]:
        if not 1 < len(suite) <= LIGNES_DE_TITRE_MAX:
            return list(suite)
        rendues = [suite[0].strip()]
        for precedente, ligne in zip(suite, suite[1:]):
            if se_suivent(precedente, ligne) and len(rendues[-1]) + len(ligne) < 250:
                rendues[-1] = f"{rendues[-1]} {ligne.strip()}"
            else:
                rendues.append(ligne.strip())
        return rendues

    sortie: list[str] = []
    suite: list[str] = []
    for ligne in lignes:
        if is_section_header(ligne.replace("\xa0", " ").strip()):
            suite.append(ligne)
            continue
        sortie.extend(rendre(suite))
        suite = []
        sortie.append(ligne)
    sortie.extend(rendre(suite))
    return sortie


def _capitales(texte: str, pagine: bool = True) -> list[dict]:
    """Dernier recours : les titres sont en capitales, le corps ne l'est pas.

    S'appuie sur le découpeur historique du dispositif, éprouvé sur les procès-
    verbaux de l'instance de référence — le seul des quatre régimes qui ne
    dispose d'aucun identifiant d'acte pour se vérifier. On l'essaie en dernier
    pour cette raison.
    """
    propre = _apres_entete(_sans_entetes(texte, pagine))
    blocs = split_into_deliberations(_titres_rejoints(
        [l for l in propre.splitlines() if l.strip()]))
    sorties = []
    for bloc in blocs:
        titre = re.sub(r"\s+", " ", bloc["titre"]).strip(" :;.-")
        if len(titre) < 6 or len(re.findall(r"\b(?:M\.|Mme|Mlle)\s", titre)) >= 2:
            continue
        sorties.append(_enrichir({
            "regime": "capitales", "numero_seance": None, "numero_acte": None,
            "titre": titre[:255], "texte": "\n".join(bloc["paragraphes"]),
        }))
    return sorties


# ── Présences ────────────────────────────────────────────────────────────────
#
# Deux conventions d'écriture, qui exigent deux lectures :
#   « Présents : Mesdames Prénom NOM, Prénom NOM et Messieurs Prénom NOM »
#   « Présents : NOM COMPOSÉ Prénom, NOM Prénom, … »
# La seconde met le patronyme d'abord et peut lui donner deux mots.

CIVILITES = ("Mesdames", "Messieurs", "Madame", "Monsieur", "Mmes", "Mme",
             "MM.", "M.", "Melle", "Mesdemoiselles")

# Le prénom composé n'admet le trait d'union QUE devant une nouvelle majuscule :
# avec le tiret dans la classe de caractères, « Jean-Claude » s'arrête sur
# « Jean- », le moteur d'expressions régulières n'ayant aucune raison de revenir
# en arrière quand la capture réussit déjà. Ce piège s'est présenté trois fois.
PRENOM_NOM = re.compile(
    r"([A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][a-zà-ÿ'’]+(?:[- ][A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][a-zà-ÿ'’]+)*)"
    r"\s+([A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ'’\-]{2,})")
NOM_PRENOM = re.compile(
    r"\b([A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\-]{1,}(?:\s+[A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\-]{1,})*)\s+"
    r"([A-ZÀ-Ÿ][a-zà-ÿ'’]+(?:[- ][A-ZÀ-Ÿ][a-zà-ÿ'’]+)*)")
# « PRÉSENTS : M. NOM, Mme NOM, … » — la civilité tient lieu de prénom, qui
# n'est pas donné. Le rapprochement avec la table des personnes se fera sur le
# seul patronyme, ce qui est moins sûr : deux homonymes ne se distinguent plus.
CIVILITE_NOM = re.compile(
    r"\b(?:M\.|Mme|Mlle|MM\.|Mmes)\s+((?:de\s+|d')?[A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\-]{2,}"
    r"(?:\s+[A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\-]{2,})?)")
NOM_INVERSE_CIVILITE = re.compile(
    r"(?:Monsieur|Madame)\s+([A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ'’\-]{2,})"
    r"\s+([A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][a-zà-ÿ]+)")

# « a donné procuration à », « ayant donné procuration à », et — vu en 2019 —
# « ayant donné procuration » sans préposition. Le « à » est donc optionnel.
# Quatre formulations rencontrées : « a donné procuration à », « ayant donné
# procuration à », « ayant donné procuration » sans préposition, et « donne
# pouvoir pour voter en son nom à ».
PROCURATION = re.compile(
    r"(?:Monsieur|Madame|M\.|Mme)?\s*([A-ZÀ-Ÿa-zà-ÿ'’\- ]{4,40}?)\s*\(?\s*"
    r"(?:(?:a|ayant)\s+donné\s+procuration|donne\s+pouvoir(?:\s+pour\s+voter"
    r"\s+en\s+son\s+nom)?)\s+(?:à\s+)?(?:Monsieur|Madame|M\.|Mme)?\s*"
    r"([A-ZÀ-Ÿa-zà-ÿ'’\- ]{4,40})", re.I)
REPRESENTATION = re.compile(
    r"([A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\- ]+ [A-ZÀ-Ÿ][a-zà-ÿ\-]+)\s+représenté?e?\s+par\s+"
    r"([A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\- ]+ [A-ZÀ-Ÿ][a-zà-ÿ\-]+)", re.I)

BLOC_PRESENTS = re.compile(
    r"Présents?\s*:(.*?)(?:Représentés?\s*:|Absente?s?\s*:|Pouvoirs?\s|Secrétaire"
    r"|Date de la publi|Ordre du jour|Délibérations)", re.S | re.I)
# Même bloc, mais mis en TABLEAU : la légende occupe une cellule, la liste la
# suivante, et il n'y a donc pas de deux-points. C'est la forme des actes du
# conseil communautaire relevés le 24/08/2026 — « Présents BRUNAVEL Jordan ; … » —
# où le motif ci-dessus ne trouvait que le président, tiré de la formule
# d'ouverture : un conseil de trente-deux présents publié avec un seul.
# Sans `re.I`, à dessein : la négation exige que le mot suivant ne soit pas en
# minuscules, ce qui écarte « les conseillers présents ont approuvé ».
BLOC_PRESENTS_TABLEAU = re.compile(
    r"(?:^|\n)[ \t]*Présents?[ \t]+(?![a-zà-ÿ])(.*?)"
    r"(?:Représentés?|Absente?s?|Pouvoirs?\s|Secrétaire|Date de la publi"
    r"|Ordre du jour|Délibérations)", re.S)
BLOC_REPRESENTES = re.compile(
    r"Représentés?\s*:(.*?)(?:Absents?|Délibérations|Secrétaire|$)", re.S | re.I)
BLOC_ABSENTS = re.compile(
    r"Absente?s?(?:\s+et\s+excusés?)?\s*:(.*?)(?:Secrétaire|Date de la publi"
    r"|Ordre du jour|Délibérations|$)", re.S | re.I)
PRESIDENCE = re.compile(
    r"sous la [Pp]résidence de\s+(?:Monsieur|Madame|M\.|Mme)?\s*"
    r"([A-ZÀ-Ÿa-zà-ÿ'’\- ]{4,45}?)\s*(?:,|\.|\bMaire\b)")


def _sans_civilite(nom: str) -> str:
    n = re.sub(r"\s+", " ", (nom or "").strip())
    for c in CIVILITES:
        if n.startswith(c + " "):
            n = n[len(c) + 1:]
            break
    return n.strip(" ,;.")


def _borner(bloc: str) -> str:
    """Coupe un bloc d'en-tête là où commence le corps du document.

    « Absents et excusés : X, Y » est parfois suivi directement du titre de la
    première délibération. Sans borne, la liste des absents avalait ce titre et
    le bloc de signature d'en face — d'où des « absents » nommés « Mme AUTORISE ».
    """
    bloc = bloc.split("Délibérations")[0]
    m = ACTE_FINAL.search(bloc)
    if m:
        bloc = bloc[:bloc.rfind("\n", 0, m.start()) + 1 or m.start()]
    return bloc.strip()


def _cle_nom(nom: str) -> str:
    """Clé de comparaison d'un nom : capitales, sans accents.

    Le président figure deux fois dans un procès-verbal — « Alix MARAGNOL » dans
    la formule d'ouverture, « MARAGNOL Alix » dans la liste — et les deux
    graphies ne diffèrent que par un accent. Comparées telles quelles, elles
    faisaient deux présents pour une personne.
    """
    decompose = unicodedata.normalize("NFD", (nom or "").upper())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def noms(bloc: str, ordre: str = "prenom_nom") -> list[str]:
    """Noms d'un bloc de présence, rendus « Prénom NOM ».

    Le rapprochement avec la table des personnes se fait sur le patronyme en
    capitales : deux conventions d'écriture dans la même base finissent toujours
    par produire deux personnes là où il n'y en a qu'une.
    """
    if not bloc:
        return []
    # Le PDF coupe les noms en fin de ligne, y compris au milieu d'un prénom
    # composé : recoller avant de découper, sans quoi le conseiller entre en
    # base sous le prénom « Jean- ».
    bloc = bloc.replace("-\n", "-").replace("\n", " ")

    trouves = []
    if ordre == "civilite_nom":
        trouves += [n.strip() for n in CIVILITE_NOM.findall(bloc)]
    elif ordre == "prenom_nom":
        trouves += [f"{p} {n}" for n, p in NOM_INVERSE_CIVILITE.findall(bloc)]
        bloc = NOM_INVERSE_CIVILITE.sub(" ", bloc)
        for c in CIVILITES:
            bloc = bloc.replace(c + " ", " ")
        trouves += [f"{p} {n}" for p, n in PRENOM_NOM.findall(bloc)]
    else:
        # La virgule n'est pas le seul séparateur : les actes du conseil
        # communautaire relevés le 24/08/2026 alignent « BRUNAVEL Jordan ;
        # NEVAGNOL Jordan ; … », et un bloc entier ne rendait qu'un seul nom.
        # Le deux-points y figure aussi, par coquille de saisie
        # (« CALOURET Charlie : VELAUDIN Céleste ») : le traiter en séparateur
        # rend les deux conseillers au lieu du premier.
        # « … ; SALAGNOL Sacha et MARAVEL Ange. » — le dernier de la liste
        # est amené par « et », et se perdait dans le segment du précédent.
        for segment in re.split(r"[;,:]|\bet\b", bloc):
            m = NOM_PRENOM.search(segment.strip())
            if m:
                trouves.append(f"{m.group(2)} {m.group(1)}")

    vus, sortie = set(), []
    for n in trouves:
        if _cle_nom(n) not in vus:
            vus.add(_cle_nom(n))
            sortie.append(n)
    return sortie


def presences(texte: str, ordre: str = "prenom_nom") -> dict:
    """Présents, absents, procurations, depuis l'en-tête du procès-verbal.

    L'en-tête tient dans les premiers milliers de caractères, et les blocs sont
    bornés : sur un document de 35 000 caractères, un « Absents : » ouvert
    jusqu'au prochain mot-clé ramassait les signatures de fin de document.
    """
    tete = texte[:3000]
    res = {"presents": [], "absents": [], "pouvoirs": []}

    m = BLOC_PRESENTS.search(tete) or BLOC_PRESENTS_TABLEAU.search(tete)
    if m:
        # Les procurations se glissent DANS le bloc des présents autant que dans
        # celui des absents. Les y laisser rendait présent celui qui avait donné
        # pouvoir, c'est-à-dire l'inverse.
        res["presents"] = noms(_borner(PROCURATION.sub(" ", m.group(1)[:900])), ordre)

    # Le maire ou le président ne figure pas dans la liste : seulement dans la
    # formule d'ouverture. L'omettre revenait à publier un conseil sans sa
    # présidence.
    m = PRESIDENCE.search(tete)
    if m:
        president = _sans_civilite(m.group(1))
        if president and _cle_nom(president) not in {_cle_nom(n)
                                                     for n in res["presents"]}:
            res["presents"].insert(0, president)

    for donneur, receveur in PROCURATION.findall(tete):
        res["pouvoirs"].append({"de": _sans_civilite(donneur),
                                "à": _sans_civilite(receveur)})
    m = BLOC_REPRESENTES.search(tete)
    if m:
        for absent, mandataire in REPRESENTATION.findall(_borner(m.group(1)[:600])):
            res["pouvoirs"].append({"de": _retourner(absent, ordre),
                                    "à": _retourner(mandataire, ordre)})

    m = BLOC_ABSENTS.search(tete)
    if m:
        bloc = PROCURATION.sub(" ", _borner(m.group(1)[:600]))
        res["absents"] = noms(bloc, ordre)
    return res


def _retourner(nom: str, ordre: str) -> str:
    if ordre == "prenom_nom":
        return _sans_civilite(nom)
    m = NOM_PRENOM.search((nom or "").strip())
    return f"{m.group(2)} {m.group(1)}" if m else (nom or "").strip()
