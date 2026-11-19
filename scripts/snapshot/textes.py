"""Les textes qui sortent : libellés présentables, personnes masquées.

Deux familles, qui servent à plusieurs étapes du snapshot :

- la PRÉSENTATION des libellés — un nom de registre en capitales, un intitulé
  de flux, un titre d'acte préfixé — qui ne change rien à ce qui est dit ;
- le MASQUAGE de ce qui désigne une personne qui n'a pas à l'être : le nom d'un
  particulier hors des actes délibérés (`compilateur_redaction`), le domicile
  et la naissance dans le texte d'une délibération (`masquer_donnees_personnelles`).

Elles vivaient au milieu de `build_public_snapshot.py`. Elles n'y changent
pas : ce module les porte, la façade les réexporte.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from scripts.snapshot.popolo import POPOLO_ROLES
from scripts.snapshot.socle import ROLES_SANS_TITRE_PROPRE, RULES, rows, safe_url


# ── Présentation des libellés publics ────────────────────────────────────────
# Les noms viennent bruts du RNA / SIRENE, donc en capitales, et les libellés de
# flux viennent des collecteurs, donc sans casse ni accent homogènes. Publier
# « ASSOCIATION DE PARENTS D'ELEVES DES ECOLES PUBLIQUES DE … » à côté de
# « Le Petit Fictif » donne un flux qui a l'air cassé alors que la donnée est juste.

MOTS_LIAISON = {"DE", "DES", "DU", "D", "LA", "LE", "LES", "L", "ET", "EN",
                "AU", "AUX", "POUR", "SUR", "PAR", "UN", "UNE", "A", "AVEC",
                "SOUS", "SANS", "OU"}

# Sigles à conserver en capitales : la règle « ≤ 3 lettres ou pas de voyelle »
# ne les attrape pas tous, et un sigle title-casé (« Ccas ») est illisible.
ACRONYMES = {
    "ADMR", "AFPPA", "ANS", "APE", "ASART", "ASL", "ATSEM", "BOAMP", "CAC",
    "CAF", "CCAS", "CCAS", "CC", "CD30", "CDG", "CERISE", "CIAS", "CLIC",
    "CNRS", "CVN", "DDFIP", "DETR", "DGCL", "DGF", "DSP", "EHPAD", "EPCI",
    "EPI", "ESAT", "EVEN", "FCTVA", "FSC", "MJC", "MSA", "OFGL", "ONF",
    "PLU", "PNC", "RAM", "RNA", "RPI", "SAS", "SARL", "SCI", "SCIC", "SCOP",
    "SIVU", "SPANC", "SYMTOMA", "UFOLEP", "USEP", "USPOP", "VTT", "ZAD",
}


def joli_nom(nom: str | None) -> str:
    """Nom d'acteur présentable : casse humaine, sigles préservés.

    N'intervient QUE sur les noms tout en capitales (ceux du RNA/SIRENE) : un
    nom déjà saisi avec une casse mixte a été arbitré par un humain, on n'y
    touche pas.
    """
    nom = (nom or "").strip().rstrip(".")
    if not nom or nom != nom.upper():
        return nom

    def bloc(nu: str, premier: bool) -> str:
        if nu in ACRONYMES:
            return nu
        if nu in MOTS_LIAISON:
            return nu.capitalize() if premier else nu.lower()
        # Sigle probable : trop court pour être un mot, ou sans voyelle (HJH).
        # Le seuil est à 2 et non 3 : « NEZ », « ART », « VIV » sont des mots.
        if len(nu) <= 2 or not set(nu) & set("AEIOUY"):
            return nu
        return nu.capitalize()

    sortie = []
    for i, mot in enumerate(nom.split()):
        avant = mot[:len(mot) - len(mot.lstrip("(«\"'"))]
        apres = mot[len(mot.rstrip(")».,;:\"")):]
        nu = mot[len(avant):len(mot) - len(apres)] if apres else mot[len(avant):]
        if not nu:
            sortie.append(mot)
            continue
        # Un mot seul entre parenthèses est le sigle de ce qui précède —
        # « (CCAS) », « (CEPLR) » : le title-case en ferait « Ccas ».
        if "(" in avant and ")" in apres:
            sortie.append(mot)
            continue
        # Apostrophe : élision (« D'ACTION » → « d'Action ») ou nom composé
        # (« VIV'ALTO » → « Viv'Alto »). Les deux parts sont traitées à part.
        parts = re.split(r"(['’])", nu)
        if len(parts) == 3 and parts[0] in {"D", "L", "N", "S", "C", "J", "QU"}:
            rendu = ((parts[0].capitalize() if not i else parts[0].lower())
                     + parts[1] + bloc(parts[2], False))
        elif len(parts) == 3:
            rendu = bloc(parts[0], not i) + parts[1] + bloc(parts[2], False)
        else:
            rendu = bloc(nu, not i)
        sortie.append(avant + rendu + apres)
    return " ".join(sortie)


# « 11 637,87 € » en fin de libellé alors que le montant est déjà affiché en
# face : deux écritures du même chiffre, arrondies différemment.
_MONTANT_FINAL = re.compile(
    r"\s*[—–-]\s*[\d   ]+(?:[.,]\d+)?\s*€\s*$")


def nettoyer_libelle(texte: str | None, acteur: str | None = None,
                     montant_affiche=None) -> str:
    """Libellé de flux débarrassé de ce que la ligne affiche déjà par ailleurs.

    Le nom du bénéficiaire apparaissait jusqu'à trois fois sur une même ligne
    (« USEP Écoles — Subvention communale 2026 — USEP Écoles », plus le lien
    acteur en dessous) parce que chaque collecteur préfixait le libellé qu'il
    écrivait. On retire les répétitions plutôt que le contexte.
    """
    t = " ".join((texte or "").split())
    if not t:
        return t
    if acteur:
        cible = norm_nom(acteur)
        # Les collecteurs écrivent l'acteur tantôt en sigle (« ASART »), tantôt
        # sans apostrophe (« La Scene fictive ») : la comparaison se fait sur les
        # tokens, pas sur la chaîne.
        jetons = lambda s: {m for m in norm_nom(s).replace("'", " ")
                            .replace("’", " ").split() if len(m) >= 2}
        cible_jetons = jetons(acteur)

        def redondant(seg: str) -> bool:
            j = jetons(seg)
            return bool(j) and j <= cible_jetons

        def sans_acteur(seg: str) -> str:
            # « USEP Écoles (320 + 4 044 + …) » : le nom préfixe le détail, on
            # ne garde que le détail — l'acteur est déjà en face de la ligne.
            if norm_nom(seg).startswith(cible + " "):
                reste = seg.strip()[len(acteur):].strip()
                if reste.startswith("("):
                    return reste
            return seg

        for sep in (" — ", " – ", " - "):
            morceaux = t.split(sep)
            if len(morceaux) > 1:
                gardes = [sans_acteur(m) for m in morceaux
                          if norm_nom(m) != cible and not redondant(m)]
                if gardes:
                    t = sep.join(gardes)
    if montant_affiche is not None:
        t = _MONTANT_FINAL.sub("", t)
    return t.strip(" —–-")


# Titres qui ne portent aucune information hors du document dont ils sont
# extraits : dans un flux d'actualité, ils occupent une ligne pour rien.
TITRES_VIDES = {"questions diverses", "divers", "informations diverses",
                "informations et questions diverses", "point divers",
                "questions et informations diverses", "(sans titre)"}

# « CM du 2026-05-28 - Conseil municipal du 28 Mai 2026 - … » : le préfixe de
# classement interne du collecteur, redondant avec la date affichée.
_PREFIXE_CM = re.compile(r"^CM\s+du\s+\d{4}-\d{2}-\d{2}\s*[-—–]\s*")


def nettoyer_titre_evenement(titre: str | None) -> str:
    return " ".join(_PREFIXE_CM.sub("", titre or "").split())


def norm_nom(s: str | None) -> str:
    """Nom normalisé pour comparaison : sans accents, majuscules, compacté."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.upper().replace("-", " ").split())


MENTION_PARTICULIER = "un particulier"
# « M » sans point est exclu : sans lui, « TEAM SET INVESTIGATION » devenait
# « TEAun particulier INVESTIGATION » (le motif « M SET » mordait à l'intérieur
# du mot). Une civilité abrégée s'écrit avec son point.
_CIVILITES = r"(?:M\.|Mme|Mlle|Melle|Monsieur|Madame|Mademoiselle)"

# Siéger, c'est exercer un mandat. La collecte pose ces rôles sur les séances et
# sur les actes qu'elles votent, à partir des listes de présents.
_ROLES_DE_SEANCE = ("présent", "absent")
_TYPES_DE_SEANCE = ("conseil_municipal", "conseil_communautaire",
                    "deliberation", "deliberation_cc")


def _formes_du_nom(p: dict) -> tuple[list[str], str]:
    """Les formes complètes d'un nom de personne, et son patronyme nu."""
    nom_complet = " ".join((p["name"] or "").split())
    prenom = " ".join((p["firstname"] or "").split())
    # Le patronyme peut porter un nom d'usage : « PELORLY (CALIVERT) ».
    patronyme = " ".join((p["lastname"] or "").split())
    if not patronyme and nom_complet:
        morceaux = nom_complet.split(" ", 1)
        patronyme = morceaux[1] if len(morceaux) > 1 else ""
    formes = [f for f in (nom_complet, f"{prenom} {patronyme}".strip(),
                          f"{patronyme} {prenom}".strip()) if len(f.split()) >= 2]
    return formes, patronyme.split("(")[0].strip()


def _ids_nommables(conn, ids_publics: set[int]) -> set[int]:
    """Les personnes publiques : publiées en fiche, ou qui ont SIÉGÉ ou tenu un
    mandat — un mandat clos reste un mandat. Siéger en commission n'en est
    pas un : la requête ne filtre pas la confiance, et un membre non élu lu
    dans un PV (`probable`) serait devenu nommable dans tous les textes."""
    mandats = sorted((set(POPOLO_ROLES) | set(RULES["people"]["publish_only_with_relation_types"]))
                     - ROLES_SANS_TITRE_PROPRE)
    return ids_publics | {r["entity_id"] for r in rows(conn, f"""
        SELECT ee.entity_id
        FROM event_entities ee JOIN events ev ON ev.id = ee.event_id
        WHERE ee.role IN ({",".join("?" * len(_ROLES_DE_SEANCE))})
          AND ev.type IN ({",".join("?" * len(_TYPES_DE_SEANCE))})
        UNION
        SELECT from_id FROM relations
        WHERE relation_type IN ({",".join("?" * len(mandats))})
    """, [*_ROLES_DE_SEANCE, *_TYPES_DE_SEANCE, *mandats])}


def _personnes(conn) -> list[dict]:
    return rows(conn, """
        SELECT e.id, e.name, p.firstname, p.lastname
        FROM entities e LEFT JOIN persons p ON p.entity_id = e.id
        WHERE e.type = 'person'
    """)


def noms_des_personnes_publiques(conn, ids_publics: set[int]) -> set[str]:
    """Les formes complètes, en capitales, du nom de chaque personne publique."""
    nommables = _ids_nommables(conn, ids_publics)
    return {f.upper() for p in _personnes(conn) if p["id"] in nommables
            for f in _formes_du_nom(p)[0]}


def compilateur_redaction(conn, ids_publics: set[int]):
    """Masque les personnes physiques non publiables citées DANS LES TEXTES.

    Le filtre entités écarte bien un particulier du graphe, mais son nom
    ressortait quand même par les libellés : « Aide façade — M. Zanavel »,
    « Cession … à Prénom NOM (veuve NOM) ». Le texte est une sortie
    comme une autre, il doit passer le même filtre.

    On ne masque que des formes non ambiguës — nom complet, ou civilité + nom
    de famille. Un patronyme seul est trop souvent aussi un toponyme ou un nom
    de société d'ici pour être remplacé sans arbitrage.

    ⚖️ Qui a SIÉGÉ ou tenu un mandat n'est pas un particulier. `ids_publics` ne
    connaît que les mandats que les règles publient : un titre court n'en
    souffrait pas, le texte entier d'une délibération si. Relevé sur les
    extraits de la première commune portée, avant correction — 8 548 « un
    particulier », dont les conseillers des mandats précédents à chaque prise de
    parole, un ancien maire « Sous la présidence de Monsieur un particulier », et
    le maire en exercice dans chaque liste de présents : « Secrétaire de séance :
    un particulier » partait en ligne.

    ⚖️ Les DÉLIBÉRATIONS ne passent plus par ici (arbitré par Julien le 16/09) :
    un particulier cité dans un acte officiel est cité. Cf. `texte_publiable`,
    qui en masque le domicile et la naissance, jamais le nom.
    """
    nommables = _ids_nommables(conn, ids_publics)
    personnes = _personnes(conn)

    # Ce qu'une forme désigne n'est pas écrit dans la forme : « M. GALAVEL »,
    # ou « Loïs QUILOURET » quand la base porte deux fiches du même homme, peut
    # être l'élu autant que son homonyme privé. Une forme partagée avec une
    # personne nommable est ambiguë, et une personne publique ne se masque pas
    # sur un doute.
    formes_nommables, patronymes_nommables = set(), set()
    for p in personnes:
        if p["id"] in nommables:
            formes, premier = _formes_du_nom(p)
            formes_nommables.update(f.upper() for f in formes)
            patronymes_nommables.add(premier.upper())

    complets: set[str] = set()
    courts: set[str] = set()
    for p in personnes:
        if p["id"] in nommables:
            continue
        formes, premier = _formes_du_nom(p)
        for forme in formes:
            if forme.upper() not in formes_nommables:
                complets.add(re.escape(forme))
        # Un patronyme précédé d'une civilité désigne une personne — pas
        # forcément celle-ci, s'il est aussi celui d'un élu. Et pas si un NOM en
        # capitales le suit : c'est alors un prénom. « M. Jordan LOROMBE »
        # devenait « un particulier GALAVEL » parce qu'un particulier de la
        # base s'appelle SACHA — la comparaison ignore la casse, d'où le
        # `(?-i:…)` qui la rétablit pour ce seul contrôle.
        if len(premier) >= 3 and premier.upper() not in patronymes_nommables:
            courts.add(rf"{_CIVILITES}\s+{re.escape(premier)}"
                       r"(?-i:(?!\s+[A-ZÀ-Ÿ][A-ZÀ-Ÿ'’\-]+\b))")

    if not complets and not courts:
        return (lambda texte: texte), Counter()

    # Les formes longues d'abord : sinon « Prénom NOM » consomme le texte
    # avant que « Céleste VAL (veuve VAL) » ait sa chance.
    # `(?<!\w)` / `(?!\w)` plutôt que `\b` : certains noms d'usage finissent par
    # une parenthèse — « PELORLY (CALIVERT) » — devant laquelle `\b` ne matche pas.
    motif = re.compile(
        r"(?<!\w)(?:" + "|".join(sorted(complets | courts, key=len, reverse=True)) + r")(?!\w)",
        re.IGNORECASE)
    compteur = Counter()

    def redige(texte: str | None) -> str | None:
        if not texte:
            return texte
        sortie, n = motif.subn(MENTION_PARTICULIER, texte)
        if n:
            compteur["remplacements"] += n
        return sortie

    return redige, compteur


# ⚖️ Ce qu'une délibération publie, arbitré par Julien le 16/09/2026 : un
# particulier cité dans un acte officiel est CITÉ — titres et textes des
# délibérations ne passent plus par `redige()`. Ce qui ne sort pas, c'est ce qui
# situe ou date une personne : son domicile, sa date et son lieu de naissance.
# L'âge d'une personne publique n'est pas interdit et a du sens : la date de
# naissance d'un élu devient son âge à la date de l'acte.
#
# Ces données sont MASQUÉES dans le texte, qui reste publié. Formes relevées le
# 16/09 sur les trois instances : « Eden LORORLY né le 05/03/1961 ; » (un
# délégué), « née le 5 octobre 1961 est nommée », « né le 05/03/1961 à
# Testonville », « demeurant au 12 Grand Rue à Testonville », « domiciliée 3, rue ⏎ des
# Lilas », « domicilié 7, ⏎ chemin des Merles à Testonville », « résidant à
# l'adresse « … – 8 Impasse … » à Fictiville (99 000) », « M. et Mme X domiciliés à
# Mas Fictif », et le tableau du conseil « Date de naissance Adresse CP
# Ville » que l'océrisation mêle sur plusieurs lignes. Un courriel nominatif
# (prénom.nom@) est masqué au titre des coordonnées des personnes.
#
# Formes ajoutées le 06/10 après une revue externe, qui les faisait toutes sortir :
# « né à Castres le 5 mars 1961 » (le lieu avant la date), « Date de naissance :
# 05/03/1961 », « né en 1961 », « demeurant à Testonville, 5 rue des Lilas » (le
# masque s'arrêtait à la virgule), « domicilié au lieu-dit Mas de la Combe ».
# Elles vivent dans tests/fixtures/pieges_textes.txt : une forme nouvelle s'y
# ajoute d'abord.
#
# 🔴 Le filet : ce que ces règles n'ont pas su masquer est cherché une seconde
# fois, et l'EXTRAIT n'est alors pas publié — l'acte, son titre et le lien vers
# la pièce restent. Il ne cherche PAS les mêmes formes que le masque, sinon il
# en partage les angles morts : il cherche un voisinage — « né » puis une date
# ou une année du XXe siècle dans les quarante caractères, un verbe de domicile puis un numéro de voie.
_MOIS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
         "aout", "septembre", "octobre", "novembre", "decembre")
_MOIS_RE = r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)"
_DATE_COMPLETE = (r"(?:\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{4}"
                  rf"|\d{{1,2}}(?:er)?\s+{_MOIS_RE}\s+\d{{4}})")
# Une commune, un lieu-dit : des mots à majuscule reliés par les petits mots d'un
# toponyme. `(?-i:…)` : sous IGNORECASE, « à la maison de retraite » serait un lieu.
_MAJ, _MIN = "A-ZÀ-ÖØ-ÞŒŠŽŸ", "a-zß-öø-ÿœšž"
# Suite d'un toponyme : en casse de titre seulement — « à Castres PRÉCISE » ne
# doit pas emporter le verbe qui suit, écrit en capitales.
_LIEU = (rf"(?-i:[{_MAJ}])[\w'’\-]+"
         rf"(?:[ \-](?:sur|sous|en|de|du|des|la|le|les|lès|(?-i:[{_MAJ}][{_MIN}'’\-]+)))*"
         # L'océrisation lit souvent la parenthèse fermante comme une accolade :
         # « Testonville (99001} » laissait le code postal derrière le masque.
         r"(?:\s*[({]\s*\d[\d\s]*[)}\]])?")
_NAISSANCE = re.compile(
    rf"\b(?P<est>est\s+)?n(?P<genre>[ée](?:\(e\)|e)?)\s+(?:"
    rf"le\s+(?P<date>{_DATE_COMPLETE})(?P<lieu>\s+à\s+{_LIEU})?"
    # Avec la date d'abord : seul, `_LIEU` emporte le « le » qui la précède.
    rf"|(?P<lieu_avant>(?:à|au|aux|en)\s+{_LIEU})\s*,?\s*le\s+(?P<date_apres>{_DATE_COMPLETE})"
    rf"|(?P<lieu_seul>(?:à|au|aux|en)\s+{_LIEU})"
    r"|en\s+(?P<annee>(?:19|20)\d{2})\b"
    r")", re.I)
_DATE_DE_NAISSANCE = re.compile(
    rf"(date\s+(?:et\s+lieu\s+)?de\s+naissance\s*:?\s*){_DATE_COMPLETE}(?:\s+à\s+{_LIEU})?", re.I)
# « Né à Castres » ou « né en 1961 », sans date complète, ne dit une naissance
# que d'une PERSONNE : « le projet est né à Lasalle », « la communauté de
# communes, née en 2017 de la fusion » restent. Une civilité, ou un « Prénom
# NOM », dans ce qui précède.
_PRENOM_NOM_AVANT = re.compile(rf"(?-i:[{_MAJ}][{_MIN}\-]{{2,}}\s+[{_MAJ}][{_MAJ}'’\-]+)[^\n]{{0,30}}$")
_VOIE = (r"(?:grande\s+rue|rue|chemin|all[ée]e|impasse|route|rte|avenue|place|"
         r"boulevard|quai|lotissement|mont[ée]e|traverse|passage|hameau|"
         r"quartier|domaine|r[ée]sidence)")
_ADRESSE_NUMEROTEE = (r"\s*(?:au|à|:)?\s*\d{1,4}\s*(?:bis|ter)?\s*,?[^\d,.;]{0,25}?"
                      rf"\b{_VOIE}\b")
_CP_VILLE = rf"(?:\s*,?\s*\d{{2}}\s?\d{{3}}\s+{_LIEU})?"
_DOMICILE = re.compile(
    r"\b(?P<verbe>domicili[ée]e?s?|demeurant|r[ée]sidant)(?:"
    rf"\s+à\s+l['’]adresse\s*«[^»]{{0,160}}»(?:\s+à\s+{_LIEU})?"
    rf"|{_ADRESSE_NUMEROTEE}\s*[^,.;\n]{{0,80}}?(?=\s+à\s|\s*[,.;]|\s*\n|\s*$)"
    rf"(?:\s+à\s+{_LIEU})?{_CP_VILLE}"
    # La commune d'abord, la voie ensuite : « demeurant à Testonville, 5 rue des
    # Lilas ». La voie numérotée est un domicile, civilité ou non.
    # Une incise en minuscules peut séparer le verbe du lieu : « demeurant au
    # moment de la transaction à Testonville (99001) » (acte de la CC publié
    # en clair, relevé par l'avis DPO du 26/10/2026). Le lieu exige toujours
    # une majuscule, et une civilité avant le verbe (`domicile`).
    rf"|(?P<lieudit>(?:\s+(?-i:[{_MIN}'’]+)(?:\s+(?-i:[{_MIN}'’]+)){{0,5}}?)?"
    rf"\s+(?:à|au|aux|en)\s+(?:lieu[- ]dit\s+)?{_LIEU})"
    rf"(?P<voie_apres>\s*,{_ADRESSE_NUMEROTEE}\s*[^,.;\n]{{0,80}}?(?=\s*[,.;]|\s*\n|\s*$))?"
    r")", re.I)
# « Mme FENAUDIN Lou, 14 rue des Tilleuls à Fictiville » : l'adresse suit le nom sans
# verbe, donc `_DOMICILE` ne la voit pas. Relevé dans les extraits publiés d'aides
# façade (12/10/2026). Voie numérotée seulement, comme pour `_DOMICILE`.
_ADRESSE_DE_PARTICULIER = re.compile(
    r"(?P<qui>\b(?:Monsieur|Madame|Mademoiselle|M\.|Mme|Mlle)\s+[^\n,.;:–—]{1,50}?)"
    rf"\s*[,–—\-]\s*(?P<adr>\d{{1,4}}\s*(?:bis|ter)?\s*,?\s*{_VOIE}\b[^,.;:\n]{{0,60}}?)"
    rf"(?=\s+à\s|\s*[,.;:]|\s*\n|\s*$)(?:\s+à\s+{_LIEU})?", re.I)
# ⚖️ L'adresse du BIEN aidé (arbitrage de Julien du 14/11/2026, sur avis du DPO) :
# une aide à la rénovation d'une façade se délibère « pour le 220 route vieille »,
# « façades du 7 chemin Fictif », « la façade située 35 rue Fictive », ou sous
# un intertitre « NOM Prénom – 162 chemin du Pigeonnier ». Aucun verbe de domicile,
# pas toujours de civilité devant : ni `_DOMICILE` ni `_ADRESSE_DE_PARTICULIER`
# ne la voyaient, et sept extraits la servaient en clair (audit du 14/11). Le
# bien aidé est presque toujours le domicile du demandeur. Dans un texte qui
# parle d'une telle aide, TOUTE adresse est donc masquée, où qu'elle soit ; le
# nom, la décision et le montant restent.
#
# Le déclencheur est large à dessein (relecture sécurité du 14/11) : exiger
# « aide » puis « façade » dans la même phrase laissait sortir « Opération
# façades : participation pour le 12 rue … », « une aide ⏎ pour la façade du … »
# et toute aide dite d'un autre mot. « Façade » ou « ravalement » suffit, où que
# ce soit ; pour un toit, un logement ou un immeuble, il faut en plus le
# vocabulaire d'une aide. Le prix : l'adresse d'un bâtiment public est masquée
# aussi dans ces textes.
_AIDE_AU_BIEN = re.compile(
    r"fa[çc]ades?|\bravalement|\bOPAH\b|prime\s+(?:à\s+l['’])?habitat"
    r"|\b(?:aide|prime|subvention|participation)s?\b[^\n]{0,200}?"
    r"\b(?:toiture|logement|immeuble|habitation|maison)s?\b", re.I)
# Les voies qu'on ne cherche qu'ici, derrière un numéro : « cours » ou « bd »
# seraient trop larges pour le masque général des domiciles.
_VOIE_DU_BIEN = _VOIE[:-1] + r"|bd|av|ch|faubourg|fg|grand['’]?rue|cours)"
_NUMERO = r"(?:n°\s*)?\d{1,4}(?:\s*(?:bis|ter)\b|[A-Da-d]\b)?"
# Un numéro puis un mot de voie (« 12 rue », « n°12 de la rue ») ; ou, derrière
# « le », « du », « au », « située », un numéro puis un toponyme en casse de
# titre (« pour le 256 Camp de Fiction »). Sans ce qui précède, un numéro de page
# suivi d'un mot à majuscule sur la ligne suivante (« 2 ⏎ Envoyé en
# préfecture », « 2025, ⏎ Le Conseil ») passait pour une adresse : mesuré le
# 14/11/2026 sur les extraits servis. Pas un mois (« le 15 Novembre 2023 »), pas
# un mot de séance (« le 15 Conseillers », « au 30 Pour cent »), pas une route
# classée (« la 999 route départementale »).
_DEBUT_D_ADRESSE = re.compile(
    rf"(?<![\w.,/]){_NUMERO}\s*,?\s+"
    rf"(?=(?:(?:de\s+la|du|de\s+l['’])\s*)?(?i:{_VOIE_DU_BIEN})\b"
    r"(?!\.?\s+(?i:d[ée]partementale|nationale|communale|foresti[èe]re)\b))"
    r"|(?P<avant>\b(?:le|du|au|situ[ée]e?s?|sise?)\s+)"
    rf"{_NUMERO}\s+"
    rf"(?!(?i:{_MOIS_RE}|le|la|les|l|pour|conseil\w*|pr[ée]sents?|votants?|voix|euros?|membres?)\b)"
    rf"(?=(?-i:[{_MAJ}][{_MIN}]))")
# La voie d'abord, le numéro ensuite : « rue des Lilas n° 12 », « rue des Lilas, 12 – ».
# Le numéro doit FINIR l'adresse : derrière un « n° » écrit, ou suivi d'une
# ponctuation. « Travaux rue de la Gare, 3 lots » et « chemin de Ronde, 15 voix »
# perdaient leur nombre (relecture sécurité du 14/11/2026).
_VOIE_PUIS_NUMERO = re.compile(
    rf"\b{_VOIE_DU_BIEN}\b\.?(?:[ \t]+[\w'’\-]+){{1,5}}?\s*"
    r"(?:,?\s*n°\s*\d{1,4}\b(?!\s*\d)|,\s*\d{1,4}(?=\s*(?:[–—;:)]|-\s|\.(?!\d)|,(?!\d)|\n|$)))", re.I)
# Sans numéro : un lieu-dit, un mas, un hameau (la forme courante hors des
# bourgs), et la parcelle cadastrale, qui situe aussi bien qu'une adresse.
_LIEU_DIT_DU_BIEN = re.compile(
    r"\b(?:lieu[- ]dit|mas|hameau)\s+(?:(?:de|du|des|la|le|les)\s+|l['’]\s*)*"
    rf"(?-i:[{_MAJ}])[\w'’\-]+(?:[ \-](?:de|du|des|la|le|les|(?-i:[{_MAJ}][{_MIN}'’\-]+)))*", re.I)
# Les numéros suivants n'entrent que derrière leur propre « n° » : « section AB
# n° 123, 1 500 € accordés » devenait « [adresse masquée] 500 € », un montant faux.
_PARCELLE = re.compile(r"\bsection\s+[A-Z]{1,2}\s*,?\s*n°\s*\d+\b(?:\s*(?:,|et)\s*n°\s*\d+\b)*")
_MOT_D_ADRESSE = re.compile(r"[\w'’\-]+")
_SAUT_D_ADRESSE = re.compile(r"([ \t]*\n[ \t]*|[ \t]+)?")
_VOIE_SEULE = re.compile(_VOIE_DU_BIEN, re.I)
_LIAISON_D_ADRESSE = {"de", "du", "des", "la", "le", "les", "d", "l", "aux", "au"}
MENTION_ADRESSE = "[adresse masquée]"


def _fin_d_adresse(texte: str, debut: int) -> int:
    """Où s'arrête le nom de la voie qui commence à `debut` (après le numéro).

    Mot à mot plutôt que par un motif : l'océrisation coupe la ligne au milieu
    (« 162 chemin du ⏎ Pigeonnier ») et la phrase continue sans ponctuation (« pour
    le 220 route vieille à 479 € »). Un mot entre s'il est une
    liaison, s'il porte une majuscule, ou s'il suit le mot de voie (« route
    neuve ») ; un retour à la ligne ne se franchit qu'après une liaison, ou du
    mot de voie vers une liaison (« rue ⏎ de la Gravière »).
    """
    fin, precedent, n, voie_vue, apres_voie, nom_vu, date = debut, None, 0, False, False, False, 0
    while n < 8:
        saut = _SAUT_D_ADRESSE.match(texte, fin).group(0) or ""
        if precedent is not None and not saut:
            break
        mot = _MOT_D_ADRESSE.match(texte, fin + len(saut))
        if not mot:
            break
        w = mot.group(0)
        liaison = w.lower().rstrip("'’") in _LIAISON_D_ADRESSE
        est_voie = bool(_VOIE_SEULE.fullmatch(w))
        # « rue de l'Abbaye », « rue d'Alès » : l'article élidé tient au nom.
        elide = bool(re.match(rf"[ldLD]['’][{_MAJ}]", w))
        if precedent is not None:
            apres_liaison = precedent.lower().rstrip("'’") in _LIAISON_D_ADRESSE
            if "\n" in saut and not (apres_liaison or (apres_voie and liaison)):
                break
            # « rue du 8 Mai 1945 » : le jour n'entre que juste après la voie et
            # ses liaisons, devant un mois écrit ; puis le mois, puis l'année.
            # Tout autre nombre arrête la course : « 9 rue des Essais
            # 1 200 € » devenait « [adresse masquée] 200 € », et « rue de la Gare
            # du 12 mars 2024 » perdait son jour (troisième relecture du 14/11).
            suite = texte[mot.end():mot.end() + 14]
            if date == 0 and w.isdigit() and apres_liaison and not nom_vu and re.match(
                    rf"\s+{_MOIS_RE}\b", suite, re.I):
                date_de_voie, date = True, 1
            elif date == 1 and re.fullmatch(_MOIS_RE, w, re.I):
                date_de_voie, date = True, 2
            elif date == 2 and re.fullmatch(r"(?:1[89]|20)\d{2}", w):
                date_de_voie, date = True, 3
            else:
                date_de_voie = False
                if w.isdigit():
                    break
            # « n°12 de la rue … » : la voie peut venir après ses liaisons.
            if not (liaison or elide or w[0].isupper() or apres_voie or date_de_voie
                    or (est_voie and not voie_vue)):
                break
        fin, precedent, n = mot.end(), w, n + 1
        # « route neuve » : un mot en minuscules n'entre qu'après le PREMIER mot
        # de voie. Après un nom qui en est un aussi (« 4 chemin de la Place
        # le montant »), la phrase reprend.
        apres_voie = est_voie and not voie_vue
        nom_vu = nom_vu or (voie_vue and not liaison and not est_voie)
        voie_vue = voie_vue or est_voie
        if est_voie and texte[fin:fin + 1] == ".":    # « av. », « ch. », « bd. »
            fin += 1
    # Une liaison en fin de course n'appartient pas à l'adresse (« … de la »).
    while True:
        m = re.search(r"\s+([\w'’]+)$", texte[debut:fin])
        if not m or m.group(1).lower().rstrip("'’") not in _LIAISON_D_ADRESSE:
            return fin
        fin = debut + m.start()


def masquer_adresses_du_bien(texte: str, compteur: Counter) -> str:
    """Masque toute adresse d'un texte qui parle d'une aide à un bien."""
    if not _AIDE_AU_BIEN.search(texte):
        return texte
    morceaux, curseur = [], 0
    for m in _DEBUT_D_ADRESSE.finditer(texte):
        if m.start() < curseur:
            continue
        fin = _fin_d_adresse(texte, m.end())
        if fin == m.end():
            continue
        morceaux += [texte[curseur:m.start()], m.group("avant") or "", MENTION_ADRESSE]
        curseur = fin
        compteur["adresse_du_bien"] += 1
    sortie = "".join(morceaux) + texte[curseur:]
    for motif in (_VOIE_PUIS_NUMERO, _PARCELLE):
        sortie, k = motif.subn(MENTION_ADRESSE, sortie)
        compteur["adresse_du_bien"] += k

    # Un hameau ou un lieu-dit ne situe quelqu'un que si quelqu'un est nommé à
    # côté : « l'assainissement du hameau de X » reste (mesure du 14/11/2026).
    def lieu_dit(m):
        autour = sortie[max(0, m.start() - VOISINAGE_D_UNE_PERSONNE):m.end() + VOISINAGE_D_UNE_PERSONNE]
        if not _PERSONNE_DANS_UN_TITRE.search(autour):
            return m.group(0)
        compteur["adresse_du_bien"] += 1
        return MENTION_ADRESSE
    return _LIEU_DIT_DU_BIEN.sub(lieu_dit, sortie)


# Un domicile sans numéro n'en est un que pour une PERSONNE : « M. et Mme X
# domiciliés à Croix de Castres » — mais « l'association domiciliée à Viane ».
_CIVILITE_AVANT = re.compile(r"(?:\bM\.|\bMM\.|\bMmes?\b|\bMonsieur\b|\bMadame\b)[^\n]{0,60}$")
# Une initiale suffit : « e.lorvane@ », « d.galombere@ » sont des agents nommés. Le
# « c.c@ » de la communauté de communes, dont le second morceau n'a qu'une lettre,
# reste : c'est une boîte de service. L'océrisation coupe parfois un domaine après
# un tiret (« pays-du- fictif.fr ») : le motif le suit.
_COURRIEL_NOMINATIF = re.compile(r"\b[a-z]{1,}\.[a-z]{2,}@[\w]+(?:-\s?[\w]+)*(?:\.[\w\-]+)+", re.I)
_COURRIEL = re.compile(r"[\w.+\-]+@[\w]+(?:-\s?[\w]+)*(?:\.[\w\-]+)+")
# Un portable (06/07) n'est celui d'une PERSONNE que si un nom le précède sur la
# même ligne : « Ariel NEVANDIN, Cheffe de projet 06 39 … ». Seul, il est celui
# d'un lieu — « Tiers-lieu, 12 rue Haute / Contact … 06 39 98 97 33 » — et reste.
_PORTABLE = re.compile(r"(?<![\d.])0[67](?:[ .\-]?\d{2}){4}(?![\d.])")
_PERSONNE_AVANT_PORTABLE = re.compile(
    rf"(?:\b(?:Monsieur|Madame|M\.|Mme)\s+|(?-i:[{_MAJ}][{_MIN}\-]{{2,}}\s+[{_MAJ}][{_MAJ}'’\-]{{2,}}\b))"
    r"[^\n]{0,70}$")
# Le téléphone qui accompagne un courriel nominatif est la même coordonnée : sur
# la même ligne, il est masqué. Seul, il est le plus souvent celui d'un lieu —
# un tiers-lieu, une mairie — et reste.
_TELEPHONE = re.compile(r"(?<![\d.])0[1-9](?:[ .\-]?\d{2}){4}(?![\d.])")
_TABLEAU_DES_ELUS = re.compile(
    r"^[^\n]*date\s+de\s+naissance\s+adresse[\s\S]{0,3000}?"
    r"(?=apr[èe]s\s+en\s+avoir\s+d[ée]lib[ée]r|\bD[ÉE]CIDE\b|^\s*Le\s+(?:maire|conseil)\b|\Z)",
    re.I | re.M)
_NOM_AVEC_CIVILITE = re.compile(
    rf"(?:Monsieur|Madame|M\.|Mme)\s+(?:[{_MAJ}][{_MIN}'’\-]+\s+){{0,2}}[{_MAJ}][{_MAJ}'’\-]+"
    rf"(?:\s+[{_MAJ}][{_MIN}'’\-]+)?")
_FONCTION_D_ELU = re.compile(
    r"\bMaire\b|\b\d+\s*(?:er|ère|e|ème|nd|nde)\s+adjointe?\b"
    r"|\bConseill(?:er|ère)\s+municipal(?:e)?\b", re.I)
# « né » exige ici son accent : « ne le fera qu'après le 12 mars 2022 » n'est
# pas une naissance. Le crochet arrête la recherche : « née le [date masquée]
# est nommée à compter du 1er janvier 2022 » est déjà masqué.
_RESIDU = re.compile(
    rf"\bn[ée]e?\s+le\s+{_DATE_COMPLETE}|date\s+de\s+naissance\s+adresse"
    rf"|\bné(?:\(e\)|e)?s?\b[^.;\[]{{0,40}}?(?:{_DATE_COMPLETE}|\b19\d{{2}}\b)"
    r"|date\s+(?:et\s+lieu\s+)?de\s+naissance\b[^.;\[]{0,40}?\d"
    rf"|\b(?:domicili[ée]e?s?|demeurant|r[ée]sidant){_ADRESSE_NUMEROTEE}"
    r"|\b(?:domicili[ée]e?s?|demeurant|r[ée]sidant)\b[^.;]{0,80}?"
    rf"\b\d{{1,4}}\s*(?:bis|ter)?\s*,?\s*{_VOIE}\b", re.I)

# Le filet de l'adresse du bien : il ne cherche pas comme le masque (mot à mot
# depuis le numéro) mais un voisinage, un numéro puis un mot de voie.
_VOIE_NUMEROTEE = re.compile(
    rf"(?<![\w.,/])\d{{1,4}}\s*(?:bis|ter)?\s*,?\s+{_VOIE_DU_BIEN}\b"
    r"(?!\.?\s+(?:d[ée]partementale|nationale|communale|foresti[èe]re)\b)", re.I)


def _age(naissance: str, jour: str | None) -> int | None:
    """L'âge à la date de l'acte, ou rien si l'une des deux dates ne se lit pas."""
    from datetime import date
    m = re.match(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})", naissance)
    if m:
        j, mois, annee = (int(x) for x in m.groups())
    else:
        m = re.match(rf"(\d{{1,2}})(?:er)?\s+({_MOIS_RE})\s+(\d{{4}})", naissance, re.I)
        if not m:
            return None
        cle = unicodedata.normalize("NFKD", m.group(2).lower()).encode("ascii", "ignore").decode()
        j, mois, annee = int(m.group(1)), _MOIS.index(cle) + 1, int(m.group(3))
    try:
        ne, acte = date(annee, mois, j), date.fromisoformat((jour or "")[:10])
    except ValueError:
        return None
    age = acte.year - ne.year - ((acte.month, acte.day) < (ne.month, ne.day))
    return age if 0 <= age < 120 else None


def _nomme_une_personne_publique(avant: str, noms_publics: set[str]) -> bool:
    """Le nom écrit juste avant « né le » est-il celui d'une personne publique ?"""
    mots = re.findall(r"[\w'’\-]+", avant[-100:].upper())[-6:]
    return any(" ".join(mots[i:j]) in noms_publics
               for i in range(len(mots)) for j in range(i + 2, len(mots) + 1))


def convocation_publique(c: dict, jour: str | None, noms_publics, masquages, redige) -> dict:
    """Ce qu'une convocation annonce, tel qu'il peut sortir.

    Un point de l'ordre du jour n'est pas un acte : rien n'est encore délibéré.
    « Demande de M. X » y passe donc par `redige()`, comme le titre de la séance
    — l'arbitrage du 16/09 ne vaut que pour ce que le conseil a voté.
    """
    sortie = {k: c[k] for k in ("heure", "lieu", "convoque_le") if c.get(k)}
    url = safe_url(c.get("url"))
    if url and url.lower().startswith(("http://", "https://")):
        sortie["url"] = url
    points = [redige(masquer_donnees_personnelles(p, jour, noms_publics, masquages))
              for p in c.get("ordre_du_jour") or []]
    if points:
        sortie["ordre_du_jour"] = points
    return sortie


# ⚖️ Le TITRE d'un acte ne nomme plus un particulier (règle du 27/10/2026, qui
# reprend l'arbitrage du 16/09 pour les titres seulement ; le texte de l'acte,
# déplié dessous, reste cité). Un titre sort partout : fiches, recherche, fil,
# flux RSS, fichiers ODbL, et un flux repris par un agrégateur ne se rectifie
# plus. La personne y est donc désignée par son rôle, et l'acte garde sa nature,
# son montant et son lien. Sont nommés : qui siège ou tient un mandat
# (`noms_publics`). Cf. SECURITY.md, « Une donnée publiée à tort ».
MENTION_AGENT = "un agent"
# Les façons de désigner une personne devant son nom. Élargies le 14/11/2026
# (relecture sécurité) : « Mr », « Melle », « Vve », les capitales, « M.NOM »
# sans espace, et ce qui désigne un foyer (« consorts », « époux », « famille »,
# « indivision ») sortaient intacts.
_CIVILITE_DE_TITRE = (r"(?:Monsieur|Madame|Mademoiselle|MONSIEUR|MADAME|MADEMOISELLE"
                      r"|MM\.|M\.|Mr\.?|MR\.?|Mmes?|MMES?|Mlle|MLLE|Melle|MELLE|Vve|VVE|Veuve|VEUVE)")
_FOYER_DE_TITRE = (r"(?:[Cc]onsorts|CONSORTS|[ÉEée]poux|[ÉE]POUX|[Ff]amille|FAMILLE"
                   r"|[Ii]ndivision|INDIVISION)")
_ET_CIVILITE = r"(?:\s+(?:et|ET)\s+(?:Madame|Monsieur|Mme|MME|MADAME|MONSIEUR|M\.))?"
_PERSONNE_DANS_UN_TITRE = re.compile(
    rf"(?<!\w)(?:{_CIVILITE_DE_TITRE}{_ET_CIVILITE}\s*|{_FOYER_DE_TITRE}\s+)"
    # Pas « Monsieur Le Maire », « Madame La Présidente ».
    r"(?!(?:Le|La|Les|L['’])\b)(?:"
    # Un patronyme en capitales, prénom avant ou après.
    rf"(?:[{_MAJ}][{_MIN}'’\-]+\s+){{0,2}}[{_MAJ}][{_MAJ}'’\-]+(?:\s+[{_MAJ}][{_MAJ}'’\-]+)*"
    rf"(?:\s+[{_MAJ}][{_MIN}'’\-]+)?"
    # Ou tout en casse de titre : « M. et Mme Martinot », « Monsieur Martinot Paul ».
    rf"|[{_MAJ}][{_MIN}'’\-]+(?:\s+[{_MAJ}][{_MIN}'’\-]+)?)")
_MOTS_DE_CIVILITE = {"M", "MM", "MR", "MME", "MMES", "MLLE", "MELLE", "VVE", "VEUVE", "MONSIEUR",
                     "MADAME", "MADEMOISELLE", "CONSORTS", "EPOUX", "ÉPOUX", "FAMILLE",
                     "INDIVISION", "ET"}
# Une mesure individuelle de ressources humaines vise un agent, pas un usager.
_MESURE_RH = re.compile(
    r"\bagents?\b|mise\s+à\s+disposition|recrutement|\brecruter\b|stagiaire|titularisation"
    r"|avancement\s+de\s+grade|\bvacataire|\bapprenti", re.I)
# Le numéro d'une autorisation d'urbanisme joint au nom d'un particulier, c'est
# son terrain : les permis Sitadel le retirent déjà (`_sources_note`).
_NUMERO_D_AUTORISATION = re.compile(
    r"\b(?:PC|DP|PA|PD|CU[ab]?)\s*(?:n°\s*)?\d{3}\s*\d{3}(?:\s*\d{2}\s*[A-Z]?\s*\d{3,5})?\b")
# Ce qui n'est pas un titre d'acte, mais un morceau de tableau ou de tampon que
# la découpe du procès-verbal a pris pour tel (avis DPO du 27/10/2026).
_DEBUT_DE_FRAGMENT = re.compile(
    r"^\s*(?:\||AR\s+CONTR[ÔO]LE\b|D[ée]lib[ée]r[ée]\b|R[ée]sultat\s+du\s+vote\b)", re.I)


# Un titre qui parle d'un MANDAT : là seulement, un patronyme seul peut être
# l'élu. Des tournures propres au mandat, pas des mots courants : « commission »,
# « titulaire », « représentant », « maire » gardaient « M. NOM » dans « Aide aux
# façades sur avis de la commission – M. NOM » (relecture sécurité du 14/11/2026).
_ACTE_DE_MANDAT = re.compile(
    r"d[ée]signation|[ée]lection|\b[ée]lue?s?\b|\bd[ée]l[ée]gu[ée]e?s?\b"
    r"|d[ée]l[ée]gations?\s+(?:de|du|des|au|aux|à)\b|indemnit[ée]s?\s+de\s+fonction"
    r"|installation\s+du\s+conseil|\bd[ée]mission|\bprocuration|secr[ée]taire\s+de\s+s[ée]ance"
    r"|\badjointe?s?\b|\bsuppl[ée]ante?s?\b", re.I)
# Et un titre qui parle d'un bien ou d'une aide ne nomme jamais par un doute.
_ACTE_ENVERS_UN_PARTICULIER = re.compile(
    r"\baides?\b|\bsubvention|\bprime\b|fa[çc]ade|\bvente\b|\bcession\b|\bacquisition\b"
    r"|\bbail\b|\bloyer|\bterrain\b|\bparcelle|\bpermis\b|\bsecours\b|\bremise\b", re.I)


def _est_publique(personne: str, noms_publics: set[str], titre: str = "") -> bool:
    """La personne écrite dans ce TITRE est-elle publique ?

    Prénom et nom reconnus : oui. Un patronyme seul (« M. NOM », « M. et Mme
    NOM ») est un homonyme possible ; jusqu'au 14/11/2026 le doute gardait le
    nom, et le couple bénéficiaire d'une aide sortait dans le titre, la
    recherche et les fichiers parce qu'un élu portait le même nom. Les deux
    erreurs n'ont pas le même prix (avis DPO du 14/11) : taire un élu dans un
    titre ne coûte presque rien, le texte cité dessous le nomme ; nommer un
    particulier part partout et ne se rattrape plus. Le doute profite donc au
    rôle, sauf dans un titre qui parle d'un mandat et de rien d'autre, et
    jamais pour un couple ou un foyer.
    """
    haut = personne.upper()
    mots = [m for m in re.findall(r"[\w'’\-]+", haut) if m not in _MOTS_DE_CIVILITE]
    if any(" ".join(mots[i:j]) in noms_publics
           for i in range(len(mots)) for j in range(i + 2, len(mots) + 1)):
        return True
    foyer = bool(re.search(r"\bET\s+(?:MME|MADAME|M\.|MONSIEUR)|CONSORTS|[ÉE]POUX|FAMILLE|INDIVISION", haut))
    return (len(mots) == 1 and not foyer
            and bool(_ACTE_DE_MANDAT.search(titre))
            and not _ACTE_ENVERS_UN_PARTICULIER.search(titre)
            and any(mots[0] in n.split() for n in noms_publics))


def titre_d_acte(titre: str, jour: str | None, noms_publics: set[str],
                 compteur: Counter) -> tuple[str | None, str | None]:
    """Le titre publiable d'un acte, ou `(None, motif)` s'il n'est pas un titre."""
    if _DEBUT_DE_FRAGMENT.match(titre):
        return None, "titre_fragment"
    texte = masquer_donnees_personnelles(titre, jour, noms_publics, compteur)
    role = MENTION_AGENT if _MESURE_RH.search(texte) else MENTION_PARTICULIER
    remplaces = 0

    def personne(m):
        nonlocal remplaces
        if _est_publique(m.group(0), noms_publics, texte):
            return m.group(0)
        remplaces += 1
        return role

    sortie = _PERSONNE_DANS_UN_TITRE.sub(personne, texte)
    if not remplaces:
        return sortie, None
    compteur["titre_designe_par_son_role"] += remplaces
    sortie = _NUMERO_D_AUTORISATION.sub("[numéro retiré]", sortie)
    sortie = re.sub(rf"\b([Dd])e ({role})\b", r"\1'\2", sortie)
    # « | M. Prénom NOM », tiré d'un tableau : il ne reste aucune décision.
    if len(re.findall(r"[^\W\d_]{3,}", sortie.replace(role, " "))) < 2:
        return None, "titre_reduit_a_un_nom"
    return sortie, None


def masquer_donnees_personnelles(texte: str, jour: str | None,
                                 noms_publics: set[str], compteur: Counter) -> str:
    """Masque domicile, date et lieu de naissance ; garde les noms."""
    def tableau(m):
        # L'océrisation mêle les colonnes sur plusieurs lignes : on ne garde de
        # chaque ligne que le nom de l'élu et sa fonction.
        compteur["tableau_des_elus"] += 1
        lignes = []
        for ligne in m.group(0).splitlines():
            noms = _NOM_AVEC_CIVILITE.findall(ligne)
            if noms:
                lignes.append(" ".join([*noms, *_FONCTION_D_ELU.findall(ligne)]))
            elif re.search(r"liste\s+des|\bélus\b|date\s+de\s+naissance", ligne, re.I):
                lignes.append(re.sub(
                    r"date\s+de\s+naissance\s+adresse(?:\s+CP)?(?:\s+Ville)?(?:\s+Titre)?",
                    "(dates de naissance et adresses masquées)", ligne, flags=re.I))
        return "\n".join(lignes) + "\n"

    def naissance(m):
        genre = m.group("genre").lower()
        feminin = "(e)" if "(" in genre else "e" if genre.endswith("e") else ""
        est = m.group("est") or ""
        avant = courant[:m.start()]
        date = m.group("date") or m.group("date_apres")
        if not date:
            # Sans date complète : « ne » sans accent est une négation, et il
            # faut une personne devant.
            if genre[0] == "e" or not (
                    _CIVILITE_AVANT.search(avant) or _PRENOM_NOM_AVANT.search(avant)
                    or _nomme_une_personne_publique(avant, noms_publics)):
                return m.group(0)
            compteur["naissance"] += 1
            if m.group("annee"):
                return f"{est}né{feminin} en [année masquée]"
            return f"{est}né{feminin} à [lieu masqué]"
        if _nomme_une_personne_publique(avant, noms_publics):
            age = _age(date, jour)
            if age is not None:
                compteur["naissance_en_age"] += 1
                return f"{est}âgé{feminin} de {age} ans"
        compteur["naissance"] += 1
        lieu = m.group("lieu") or m.group("lieu_avant")
        masque = "[date et lieu masqués]" if lieu else "[date masquée]"
        return f"{est}né{feminin} le {masque}"

    def date_de_naissance(m):
        compteur["naissance"] += 1
        return f"{m.group(1)}[date masquée]"

    def domicile(m):
        if (m.group("lieudit") is not None and m.group("voie_apres") is None
                and not _CIVILITE_AVANT.search(courant[:m.start()])):
            return m.group(0)
        compteur["domicile"] += 1
        return f"{m.group('verbe')} [domicile masqué]"

    def adresse_de_particulier(m):
        compteur["domicile"] += 1
        return f"{m.group('qui')}, [domicile masqué]"

    courant = masquer_adresses_du_bien(texte, compteur)
    courant = _TABLEAU_DES_ELUS.sub(tableau, courant)
    courant = _NAISSANCE.sub(naissance, courant)
    courant = _DATE_DE_NAISSANCE.sub(date_de_naissance, courant)
    courant = _DOMICILE.sub(domicile, courant)
    courant = _ADRESSE_DE_PARTICULIER.sub(adresse_de_particulier, courant)
    courant, n = _COURRIEL_NOMINATIF.subn("[courriel masqué]", courant)
    compteur["courriel"] += n
    if n:
        lignes = []
        for ligne in courant.split("\n"):
            if "[courriel masqué]" in ligne:
                ligne, k = _TELEPHONE.subn("[téléphone masqué]", ligne)
                compteur["telephone"] += k
            lignes.append(ligne)
        courant = "\n".join(lignes)
    lignes = []
    for ligne in courant.split("\n"):
        if _PORTABLE.search(ligne):
            def portable(m, ligne=ligne):
                if _PERSONNE_AVANT_PORTABLE.search(ligne[max(0, m.start() - 130):m.start()]):
                    compteur["telephone"] += 1
                    return "[téléphone masqué]"
                return m.group(0)
            neuve = _PORTABLE.sub(portable, ligne)
            # Le portable d'une personne et son courriel sont la même coordonnée.
            if neuve != ligne:
                neuve, k = _COURRIEL.subn("[courriel masqué]", neuve)
                compteur["courriel"] += k
            ligne = neuve
        lignes.append(ligne)
    return "\n".join(lignes)


def porte_une_donnee_personnelle(texte: str | None) -> bool:
    """Le masque aurait-il quelque chose à retirer de ce texte ?

    Pour ce qui sort TEL QU'IL A ÉTÉ RELU — un dossier, une feuille de séance.
    Le masquer à la publication publierait un autre texte que celui qu'on a
    retenu ; on ne le masque donc pas, on refuse de le sortir tant qu'il porte
    une naissance ou un domicile.

    Pas le courriel : le motif « mot.mot@ » prend aussi une adresse de service
    (`contact.eau@…`), qu'un dossier cite à bon droit — un refus pour si peu
    retirerait du site un dossier entier.
    """
    if not texte:
        return False
    compteur = Counter()
    masquer_donnees_personnelles(texte, None, set(), compteur)
    return bool(_RESIDU.search(texte)) or any(
        compteur[c] for c in ("naissance", "naissance_en_age", "domicile", "tableau_des_elus"))


#: Ce qui remplace un champ que le filet refuse : la ligne reste, le champ non.
CHAMP_RETIRE = "[texte retiré : il porte une donnée personnelle]"


def champ_publiable(valeur, jour: str | None, noms_publics: set[str],
                    compteur: Counter | None = None):
    """Un champ court saisi ou extrait — objet d'un marché, citation, note,
    précision d'un lien — tel qu'il sort.

    Ces champs sortaient tels qu'ils étaient en base : seuls les titres et les
    textes des actes passaient par le masque. Même règle ici : domicile et
    naissance masqués, et le champ retiré si le filet en trouve encore.
    """
    if not isinstance(valeur, str) or not valeur.strip():
        return valeur
    texte, refus = texte_publiable(valeur, jour, noms_publics,
                                   compteur if compteur is not None else Counter())
    return CHAMP_RETIRE if refus else texte


# ⚖️ Les actes dont le TEXTE ne sort pas (arbitrage de Julien du 14/11/2026, sur
# avis du DPO et de l'avocat du conseil). Le texte d'une délibération est cité
# tel que la collectivité l'a publié, noms compris ; mais quand la décision porte
# sur la situation d'une personne, le site n'en reproduit pas le texte : il garde
# le titre (qui désigne la personne par son rôle), la date, le montant et le lien
# vers la pièce de la collectivité. Un masque par motifs a déjà manqué ces textes
# plusieurs fois ; une catégorie entière tient par construction.
#
# Deux régimes (relecture sécurité du 14/11) :
# - TOUJOURS : ce qui liste des débiteurs ou des personnes protégées, souvent
#   dans un tableau sans civilité (« NOM Prénom 45,00 € », que rien ne
#   distingue d'une ligne de budget) ;
# - SI UNE PERSONNE Y EST NOMMÉE qui n'est pas publique : un règlement qui
#   décrit le licenciement, un régime indemnitaire qui cite le congé de longue
#   maladie, une subvention au CCAS pour l'aide sociale, la protection
#   fonctionnelle du maire se lisent ; la mesure prise envers quelqu'un, non.
_SANS_EXTRAIT_TOUJOURS = (
    ("creance_ou_impaye", re.compile(
        r"admissions?\s+en\s+non[- ]valeurs?|cr[ée]ances?\s+(?:[ée]teintes?|irr[ée]couvrables?)"
        r"|surendettement|effacement\s+de\s+(?:la\s+|sa\s+)?dette", re.I)),
    # Ce qui se PRONONCE envers quelqu'un, nom écrit avec ou sans civilité : le
    # régime « si une personne est nommée » laissait sortir « NOM Prénom, adjoint
    # technique, est placé en congé de longue maladie » (seconde relecture
    # sécurité du 14/11/2026), une donnée de santé. La règle générale qui ne
    # fait que citer ces mots (un règlement, un régime indemnitaire) n'emploie
    # pas ces tournures.
    ("mesure_individuelle_agent", re.compile(
        r"sanction\s+disciplinaire|conseil\s+de\s+discipline|exclusion\s+temporaire"
        r"|plac[ée]e?s?\s+en\s+(?:cong[ée]|disponibilit[ée])"
        r"|prononce\s+(?:le\s+licenciement|la\s+r[ée]vocation)|licenciement\s+pour\s+inaptitude", re.I)),
    ("logement_d_habitation", re.compile(
        r"bail\s+d['’]habitation|\bexpulsion\s+d[eu]\b", re.I)),
    # Pas « tuteur » (de stage, d'apprentissage) ni « sous tutelle du ministère » :
    # les treize occurrences des extraits servis le 14/11/2026 étaient celles-là.
    ("personne_protegee", re.compile(
        r"\bcuratelle\b|sauvegarde\s+de\s+justice|\b(?:plac[ée]e?s?|mise?s?)\s+sous\s+tutelle"
        r"|\btutelle\s+d[e'’]\s*(?:M\.|Mme|Monsieur|Madame)", re.I)),
)
_SANS_EXTRAIT_SI_PERSONNE = (
    ("aide_sociale", re.compile(
        # Pas « secours » seul (le service d'incendie et de secours), ni
        # « situation financière » seule (celle de la commune, à chaque budget).
        r"\baides?\s+sociales?\b|\bsecours\s+(?:exceptionnels?|d['’]urgence|financiers?|alimentaires?)"
        r"|\bun\s+secours\b|\baides?\s+(?:alimentaires?|d['’]urgence)\b|\bbons?\s+alimentaires?"
        r"|\baides?\s+(?:financi[èe]res?\s+)?exceptionnelles?"
        r"|situation\s+(?:familiale|sociale|personnelle)"
        r"|situation\s+financi[èe]re\s+(?:difficile|pr[ée]caire|d[ée]licate)", re.I)),
    ("creance_ou_impaye", re.compile(
        r"remises?\s+gracieuses?|\bimpay[ée]s?\b|retards?\s+de\s+(?:loyers?|paiement)"
        r"|\bd[ée]gr[èe]vement", re.I)),
    ("logement_d_habitation", re.compile(
        r"\bexpulsion\b"
        r"|\b(?:logement|appartement)s?\b[^\n]{0,160}?\b(?:bail|lou[ée]e?s?|loyers?|locataires?|occupante?s?)\b"
        r"|\b(?:bail|loyers?|locataires?|occupante?s?)\b[^\n]{0,160}?\b(?:logement|appartement)s?\b", re.I)),
    ("mesure_individuelle_agent", re.compile(
        r"disciplinaire|conseil\s+de\s+discipline|\blicenciement\b|rupture\s+conventionnelle"
        r"|\br[ée]vocation\b|\binaptitude\b|cong[ée]s?\s+(?:de\s+)?(?:longue\s+|grave\s+)?maladie"
        r"|maladie\s+ordinaire|cong[ée]s?\s+de\s+longue\s+dur[ée]e|protection\s+fonctionnelle"
        r"|temps\s+partiel\s+th[ée]rapeutique|retraite\s+pour\s+invalidit[ée]"
        r"|mise\s+en\s+disponibilit[ée]|\breclassement\b|(?:comit[ée]|conseil)\s+m[ée]dical", re.I)),
    # Pas « tribunal » ni « recours » seuls : presque chaque délibération finit
    # par la mention des voies de recours (« peut faire l'objet d'un recours pour
    # excès de pouvoir devant le Tribunal administratif », 430 occurrences sur
    # 682 extraits mesurés le 14/11/2026). Il faut le vocabulaire d'un différend.
    ("litige_avec_un_particulier", re.compile(
        r"ester\s+en\s+justice|protocole\s+transactionnel|\bassignation\b|\blitige\b"
        r"|\bcontentieux\s+(?:opposant|avec|engag[ée]|en\s+cours)"
        r"|d[ée]fendre\s+(?:les\s+int[ée]r[êe]ts\s+de\s+)?la\s+(?:commune|communaut[ée])"
        r"|\brequ[êe]te\s+(?:de|d[ée]pos[ée]e|pr[ée]sent[ée]e|introduite)\b"
        r"|\bmise\s+en\s+demeure\b|\baffaire\s+(?:opposant|l['’]opposant)"
        # « peut être saisi par l'application Télérecours » est au pied de 790
        # extraits sur 3 356 : seul « a été saisi par » dit un différend engagé.
        r"|\ba\s+[ée]t[ée]\s+saisie?\s+par\b|recours\s+gracieux"
        r"|\bindemnisation\s+d[eu]\b|\bpr[ée]judice\s+subi", re.I)),
)


# Sur une même ligne, et pas le tampon de la préfecture (« GARD Envoyé en
# préfecture », « VIGAN Reçu le », 300 occurrences sur les extraits servis).
_NOM_PUIS_PRENOM = re.compile(
    rf"(?<![\w'’\-])([{_MAJ}][{_MAJ}'’\-]{{2,}})[ \t]+"
    r"(?!(?:Envoy[ée]|Reçu|Date|Abstentions?|Pour|Contre|Nombre|Causses)\b)"
    rf"[{_MAJ}][{_MIN}]{{2,}}(?:-[{_MAJ}][{_MIN}]+)?\b")
_CAPITALES_D_UN_ACTE = {
    "CONSEIL", "COMMUNE", "COMMUNAUTE", "COMMUNAUTÉ", "MAIRE", "MAIRIE", "DECIDE", "DÉCIDE",
    "APPROUVE", "AUTORISE", "DIT", "PRECISE", "PRÉCISE", "CHARGE", "ADOPTE", "FIXE", "ACCEPTE",
    "DEMANDE", "VALIDE", "PREND", "EMET", "ÉMET", "VOTE", "RAPPELLE", "CONSIDERANT",
    "CONSIDÉRANT", "VU", "ARTICLE", "BUDGET", "ANNEXE", "OBJET", "DELIBERATION", "DÉLIBÉRATION",
    "SEANCE", "SÉANCE", "POUR", "CONTRE", "ABSTENTION", "ABSTENTIONS", "TOTAL", "MONTANT",
    "ANNEE", "ANNÉE", "SECTION", "CHAPITRE", "COMPTE", "DEPENSES", "DÉPENSES", "RECETTES",
    "SARL", "SAS", "SCI", "EURL", "SA", "SCOP", "EARL", "GAEC", "ASSOCIATION", "SYNDICAT",
    "SOCIETE", "SOCIÉTÉ", "ENTREPRISE", "ETS", "CCAS", "SDIS", "EPCI", "RIFSEEP", "DETR",
    "PLU", "CDG", "HT", "TTC", "TVA", "IFSE", "CIA", "PRESENTS", "PRÉSENTS", "ABSENTS",
    "EXCUSES", "EXCUSÉS", "VOTANTS", "SECRETAIRE", "SECRÉTAIRE", "PRESIDENT", "PRÉSIDENT",
}
#: Autour du mot, la distance où une personne nommée est celle que la mesure
#: vise. Un texte entier nomme presque toujours quelqu'un qui n'est pas élu (un
#: agent, un notaire, un demandeur d'un autre point) : sans cette borne, un
#: règlement qui cite le licenciement perdait son texte pour un nom écrit trois
#: pages plus loin (mesure du 14/11/2026 sur les extraits servis).
VOISINAGE_D_UNE_PERSONNE = 300


def _nomme_une_personne_privee(texte: str, noms_publics: set[str]) -> bool:
    """Le texte nomme-t-il, derrière une civilité, quelqu'un qui n'est pas public ?

    Un patronyme seul partagé avec une personne publique compte pour elle : dans
    un procès-verbal, « M. NOM expose » est d'abord l'adjoint qui rapporte, et
    le traiter en particulier retirerait le texte de la moitié des actes.
    """
    trouvees = [m.group(0) for m in _PERSONNE_DANS_UN_TITRE.finditer(texte)]
    # « DUPONT Paul 150 € », dans un tableau : un patronyme en capitales puis un
    # prénom, sans civilité. Les mots d'un acte écrits en capitales n'en sont pas.
    trouvees += [m.group(0) for m in _NOM_PUIS_PRENOM.finditer(texte)
                 if m.group(1) not in _CAPITALES_D_UN_ACTE]
    for forme in trouvees:
        mots = [w for w in re.findall(r"[\w'’\-]+", forme.upper())
                if w not in _MOTS_DE_CIVILITE]
        if not mots:
            continue
        if any(" ".join(mots[i:j]) in noms_publics
               for i in range(len(mots)) for j in range(i + 2, len(mots) + 1)):
            continue
        if len(mots) == 1 and any(mots[0] in n.split() for n in noms_publics):
            continue
        return True
    return False


def motif_sans_extrait(titre: str | None, texte: str | None,
                       noms_publics: set[str]) -> str | None:
    """Pourquoi le texte de cet acte ne sort pas, ou None s'il peut sortir."""
    ensemble = f"{titre or ''}\n{texte or ''}"
    for motif, marque in _SANS_EXTRAIT_TOUJOURS:
        if marque.search(ensemble):
            return f"sans_extrait_{motif}"
    for motif, marque in _SANS_EXTRAIT_SI_PERSONNE:
        for m in marque.finditer(ensemble):
            # Autour du mot, et en tête : un intertitre « Situation de M. X »
            # porte le nom, la mesure vient trois paragraphes plus bas.
            autour = (ensemble[:VOISINAGE_D_UNE_PERSONNE] + "\n"
                      + ensemble[max(0, m.start() - VOISINAGE_D_UNE_PERSONNE):
                                 m.end() + VOISINAGE_D_UNE_PERSONNE])
            if _nomme_une_personne_privee(autour, noms_publics):
                return f"sans_extrait_{motif}"
    return None


def texte_publiable(texte: str, jour: str | None, noms_publics: set[str],
                    compteur: Counter) -> tuple[str | None, str | None]:
    """Le texte d'une délibération tel qu'il sort — ou le motif de son refus."""
    masque = masquer_donnees_personnelles(texte.strip(), jour, noms_publics, compteur)
    if _RESIDU.search(masque):
        return None, "donnee_personnelle_non_masquee"
    if _AIDE_AU_BIEN.search(masque) and _VOIE_NUMEROTEE.search(masque):
        return None, "adresse_du_bien_non_masquee"
    return masque, None
