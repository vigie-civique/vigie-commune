#!/usr/bin/env python3
"""Le squelette d'un relevé : ce qui se lit dans la pièce sans rien juger.

    python -m collectors.en_clair.brouillon <date>-<cm|cc> --pv data/pv/X.ocr.txt \\
        [--convocation data/pv/Y.ocr.txt] [--url-pv URL] [--url-convocation URL] \\
        [--comme data/conseils/<autre séance>/releve.json] [--releve-par MODELE] \\
        [--depot-pv AAAA-MM-JJ] [--depot-convocation AAAA-MM-JJ]
    python -m collectors.en_clair.brouillon <date>-<cm|cc> --pv data/pv/X.ocr.txt --completer

À lancer depuis la racine de l'instance. Écrit `data/conseils/<code>/releve.json`
et refuse s'il existe : un relevé déjà là a peut-être été retenu à l'atelier, et
sa relecture tient à son empreinte.

Rempli ici, mécaniquement : les numéros (les marques de la pièce, donc la
complétude passe par construction), pour chaque acte l'objet présumé (la ligne
qui précède la marque), le vote lu, la première ligne de décision comme
citation (prise entre sa marque et la suivante : les bornes passent aussi), les
montants suivis de « € », l'identifiant @ctes et la mention « Publié le » ; dans
la séance, l'effectif imprimé (présents, suppléants, procurations, votants,
exprimés, quorum), le lieu lu dans une phrase de réunion, et ce que la pièce
reproduit d'une AUTRE séance. L'objet et la citation sont des PROPOSITIONS : le
rédacteur les relit. Les écarts que la pièce porte (quorum mal compté, vote du
corps contre celui du cartouche) sont proposés comme anomalies de nature
« cartouche », sans `vu_sur_image` : lus sur l'OCR, ils ne sont rendus qu'une
fois revus sur l'image (cf. `verifier.trier_anomalies`).

Laissé au rédacteur (un LLM sur le poste de l'opérateur, ou une main) : les
feuilles `en_clair`, les calculs, les anomalies. Sans `--pv` (seule la
convocation est publiée), le relevé n'a aucun acte et la feuille « après »
porte `statut: non_publie`.

`--completer` : la séance a déjà un relevé fait sur la seule convocation ; les
délibérations ont paru. Les actes s'ajoutent, « avant » reste tel quel, « après »
est remise à rédiger.

`--comme` reprend d'un relevé de la même assemblée ses libellés et sa marque
d'acte. `--depot-*` : la date où le FICHIER a été déposé (médiathèque du site,
en-tête Last-Modified), rangée dans `depot_fichier` ; ce n'est jamais une date
de publication, que seule la mention portée sur l'acte donne. Rien n'est envoyé
nulle part : ce module ne lit que des fichiers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from .seances import dossier
from .verifier import _cle_nombre, _nombres, _norm

#: Les procès-verbaux de l'intercommunalité : « Délibération n°12/2026 ».
MARQUE_CC = r"D[ée]lib[ée]rations?\s*n\s*°?\s*(\d+)\s*/\s*20\d\d"
_CODE = re.compile(r"^\d{4}-\d{2}-\d{2}-(cm|cc)$")
_DECISION = re.compile(
    r"^\W{0,4}(APPROUVE|D[ÉE]CIDE|AUTORISE|ADOPTE|VALIDE|FIXE|D[ÉE]SIGNE|ACCORDE|SOLLICITE|"
    r"PREND ACTE|[ÉE]LIT|CHARGE|ATTRIBUE|ACCEPTE|N[’']APPROUVE|EMET|ÉMET|DONNE)\b")
_NUMERAL = re.compile(r"^[IVXL]+\.?$|^\d+\.?$|^e$")
# Le tampon @ctes, répété sur chaque page télétransmise : jamais un titre.
_TAMPON = re.compile(r"^(?:ID\s*:|Envoy[ée] en pr[ée]fecture|Re[çc]u en pr[ée]fecture|Publi[ée] le)", re.I)
_MONTANT = re.compile(r"(\d{1,3}(?:[   ]\d{3})+|\d+)(?:,\d{1,2})?(?=\s*(?:€|euros?\b|EUR\b))",
                      re.I)
# Le deux-points est exigé : « pour 2025 » n'est pas un vote. C'est la forme du
# CARTOUCHE ; le corps de l'acte écrit le vote en prose (« 26 voix pour »).
_VOIX = {k: re.compile(rf"\b{m}\s*:\s*(\d{{1,3}})\b", re.I) for k, m in
         (("pour", "pour"), ("contre", "contre"), ("abstentions", r"abstentions?"))}
_PROSE_POUR = re.compile(r"\b(\d{1,3})\s+voix\s+pour\b", re.I)
_PROSE = {"contre": re.compile(r"\b(\d{1,3})\s+(?:voix\s+)?contre\b", re.I),
          "abstentions": re.compile(r"\b(\d{1,3})\s+abstentions?\b", re.I)}
# « à l'unanimité » ou « Vote : unanimité » ; pas « exige l'unanimité » d'un exposé.
_UNANIME = re.compile(r"(?:\b[àa]\s+l[’']\s?|:\s*)unanimit[ée]"
                      r"(\s+des\s+(?:suffrages\s+)?exprim[ée]s)?", re.I)
_SANS_CONTRE = re.compile(r"\b(?:aucune|sans)\s+voix\s+contre\b|\bsans\s+opposition\b", re.I)

#: L'effectif imprimé, « libellé : nombre ». Les suffrages exprimés ne sont pas
#: les votants : une abstention vote sans s'exprimer, et un relevé qui les
#: prenait pour les votants comptait 20 votants sur un acte où 27 avaient voté.
#: « Présents et représentés » est un nombre de votants, pas de procurations.
_EFFECTIF = {k: re.compile(v, re.I) for k, v in (
    ("en_exercice", r"\ben\s+exercice\s*:\s*(\d{1,3})\b"),
    ("presents", r"\bpr[ée]sent(?:e|s|es|\(e?s\))?\s*:\s*(\d{1,3})\b"),
    ("dont_suppleants", r"\bdont\s+(\d{1,3})\s+suppl[ée]ant(?:e|s|es)?\b"),
    ("suppleants", r"\bsuppl[ée]ant(?:e|s|es|\(e?s\))?\s*:\s*(\d{1,3})\b"),
    ("procurations", r"\b(?:procurations?|pouvoirs?|absents?\s+repr[ée]sent[ée]s?)(?:\(s\))?"
                     r"\s*:\s*(\d{1,3})\b"),
    ("votants", r"\b(?:votants|pr[ée]sents\s+et\s+repr[ée]sent[ée]s)\s*:\s*(\d{1,3})\b"),
    ("exprimes", r"\bexprim[ée]s\s*:\s*(\d{1,3})\b"),
    ("abstentions", r"\babstentions?\s*:\s*(\d{1,3})\b"),
    ("quorum", r"\bquorum\s*(?::|(?:est\s+)?(?:fix[ée]\s+)?[àa]\b)\s*(\d{1,3})\b"))}

#: L'identifiant @ctes : département, SIREN, date de la DÉCISION, numéro interne.
#: La date de l'identifiant rattache l'acte à sa séance, quel que soit le
#: fichier qui le porte.
_ID_ACTES = re.compile(r"\b\d{3}-\d{9}-(\d{8})-([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)")
_PUBLIE = re.compile(r"Publi[ée]\s+le\s*:?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", re.I)
_MOIS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
         "septembre", "octobre", "novembre", "decembre")
_DATE = (r"(\d{1,2})(?:er)?\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|"
         r"septembre|octobre|novembre|d[ée]cembre)\s+(\d{4})|(\d{1,2})\s*[/.]\s*(\d{1,2})\s*[/.]\s*(\d{4})")
_DU_DATE = re.compile(r"\bdu\s+(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?"
                      rf"(?:{_DATE})", re.I)
# La tête d'un procès-verbal, en début de ligne : « APPROUVE le procès-verbal de
# la séance du… » (une décision) ou « Approbation du procès-verbal » (un titre)
# n'en sont pas une.
_TETE_PV = re.compile(r"(?m)^\W{0,4}(?:LE\s+)?(?:PROC[ÈEÉ]S[\s-]*VERBAL|P\.?\s?V\.?\s+DU)\b", re.I)

#: Le lieu : dans une phrase de réunion (« s'est réuni… », « se tiendra… »), ou
#: sous un libellé « Lieu : ». Jamais la ligne de date d'une lettre
#: (« L'Espérou, le 14 septembre ») : elle donne le siège, pas la séance.
_REUNION = re.compile(r"s[’']\s?est\s+r[ée]uni(?:e|s)?\b|se\s+r[ée]unira\b|se\s+tiendra\b|"
                      r"aura\s+lieu\b|s[’']\s?est\s+tenue?\b|est\s+convoqu[ée]e?\b", re.I)
_LIEU_LIBELLE = re.compile(r"(?m)^\W{0,4}Lieu\s*:\s*(\S[^\n]{2,120})")
_LIEU_NOM = re.compile(
    r"(?:\b[àa]\s+la\s+|\b[àa]\s+l[’']\s?|\bau\s+|\baux\s+|\bdans\s+(?:la\s+|le\s+|les\s+|l[’']\s?)|"
    r"\ben\s+(?:la\s+)?)(?:salle|foyer|mairie|maison|si[èe]ge|h[ôo]tel|espace|centre|locaux|"
    r"lieu\s+habituel|gymnase|halle|ancienne|chapelle|temple|[ée]cole|auditorium|"
    r"amphith[ée][âa]tre)\b[^,;]*", re.I)
_LIEU_VILLE = re.compile(r"\b[àaÀ]\s+((?:L[’']\s?|Le\s+|La\s+|Les\s+)?[A-ZÉÈÂÎ][\w’'\-]+)")
_PAS_VILLE = re.compile(r"^(?:Monsieur|Madame|Mesdames|Messieurs|Mme|M|Mlle|Ma[iî]tre|"
                        r"Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche)$")
_FIN_LIEU = re.compile(r"\s+(?:le\s+\d|[àa]\s+\d|sous\s|en\s+s[ée]ance|sur\s+(?:la\s+)?convocation|"
                       r"apr[eè]s\s+convocation|pour\s|afin\s|et\s+non\b|à\s+partir\b)|\s*\.\s", re.I)

def _marques(texte: str, releve: dict) -> list[tuple[int, int]]:
    """(numéro, position) de chaque acte, lus comme le vérificateur les lit.

    Lui cherche dans le texte aux blancs fondus en une espace ; ici les lignes
    sont gardées (titres, décisions), donc une espace du motif vaut tout blanc.
    Sans cela « après en avoir↵délibéré » manquait ici et pas chez lui : un acte
    de moins, et toutes les bornes suivantes décalées."""
    def souple(motif: str) -> str:
        return motif.replace(r"\ ", r"\s+").replace(" ", r"\s+")
    if releve.get("marque_acte"):
        motif = souple(re.escape(releve["marque_acte"])).replace(r"\{n\}", r"(\d+)")
        return [(int(m.group(1)), m.start()) for m in re.finditer(motif, texte)]
    if releve.get("marque_acte_regex"):
        return [(int(m.group(1)), m.start())
                for m in re.finditer(souple(releve["marque_acte_regex"]), texte)]
    if releve.get("marque_acte_ordinale"):
        return [(k, m.start()) for k, m in
                enumerate(re.finditer(souple(releve["marque_acte_ordinale"]), texte, re.I), 1)]
    return []


def _capitales(ligne: str) -> bool:
    lettres = [c for c in ligne if c.isalpha()]
    return len(lettres) >= 6 and sum(c.isupper() for c in lettres) >= 0.8 * len(lettres)


#: Le libellé d'objet d'un acte télétransmis : « Objet : … », en tête de ligne.
#: Pas « a pour objet : » au fil d'un exposé.
_LIBELLE_OBJET = re.compile(r"^\W{0,4}Objet\s*:\s*(.*)$", re.I)


#: Ce qui n'est jamais la suite d'un titre « Objet : » : l'entrée en matière de
#: l'acte (« Vu », « Considérant », « Le Conseil… », « Après en avoir
#: délibéré », l'adresse aux élus), un cartouche ou un vote. Liste fermée.
_FIN_TITRE = re.compile(
    r"^\W{0,4}(?:Vus?\b|Consid[ée]rants?\b|Le\s+Conseil\b|Apr[èe]s\b|Expos[ée]\b|"
    r"Rapporteur\b|Monsieur\b|Madame\b|Mesdames\b|Messieurs\b|M\.\s+le\b|Mme\s+la\b|"
    r"Le\s+Pr[ée]sident\b|Nombre\s+de\b|Pr[ée]sent|Absent|Procuration|Quorum|Votants|"
    r"Suffrages|Secr[ée]taire|N\s*°\s*\d)", re.I)


def _suite_du_titre(ligne: str) -> bool:
    """La ligne d'après un « Objet : » prolonge le titre, majuscule ou non."""
    return bool(ligne.strip() and not _TAMPON.match(ligne.strip())
                and not _DECISION.match(ligne.strip()) and not _FIN_TITRE.match(ligne.strip())
                and not _ligne_de_vote(ligne) and not _LIBELLE_OBJET.match(ligne.strip()))


def _libelle_objet(apres: str) -> str:
    """Le titre donné par « Objet : » entre la marque et la première décision.

    Une liasse d'actes télétransmis ouvre l'acte par sa marque (« N°114/2026 »,
    « DEPARTEMENT : GARD »), puis le cartouche, puis « Objet : … » : le titre
    vient APRÈS la marque, et la ligne au-dessus est l'acte précédent. La borne
    est la première ligne de décision : un « Objet : » plus loin est dans
    l'exposé, et `apres` s'arrête déjà à la marque de l'acte suivant.

    Le titre est recollé à la ligne d'après (deux au plus) même si elle
    commence par une majuscule (« … des ressources / Communales 2026 (FPIC) »),
    sauf si c'est une ligne vide, un tampon, une décision, une entrée en
    matière, un cartouche ou un vote (`_suite_du_titre`)."""
    lignes = apres.splitlines()
    for i, brut in enumerate(lignes):
        l = brut.strip()
        if _DECISION.match(l):
            return ""
        m = _LIBELLE_OBJET.match(l)
        if not m:
            continue
        titre, suite = m.group(1).strip(), lignes[i + 1:]
        if not titre:
            while suite and not suite[0].strip():
                suite = suite[1:]
            if not suite or not _suite_du_titre(suite[0]):
                return ""
            titre, suite = suite[0].strip(), suite[1:]
        for l2 in suite[:2]:
            if not _suite_du_titre(l2):
                break
            titre = f"{titre} {l2.strip()}"
        return titre[:160]
    return ""


#: Une fin d'abréviation n'est pas une fin de phrase : « SARL. », « M. », « etc. ».
_ABREVIATION = re.compile(r"(?:\b(?:\w\.){2,}|\b(?:M|MM|Mme|Mmes|Mlle|St|Ste|Dr|Pr|Me|art|etc|cf|av|bd)\.)$",
                          re.I)


def _prose(ligne: str) -> bool:
    """Une ligne de discussion, pas un titre : elle finit une phrase (« … arrêté. »).

    Définition étroite : un point, un point d'exclamation ou d'interrogation, des
    points de suspension ou un point-virgule en fin de ligne, hors fin
    d'abréviation. Un titre de rubrique n'en porte pas ; une discussion qui
    s'arrête sans ponctuation n'est pas reconnue (l'objet reste alors proposé)."""
    l = ligne.strip().rstrip("»”\"')] ").rstrip()
    return bool(l[-1:] in (".", "!", "?", "…", ";") and not _ABREVIATION.search(l))


def _ligne_de_vote(ligne: str) -> bool:
    """Un cartouche ou un vote : « Pour : 27 », « Votants : 25 », « à l'unanimité »."""
    return bool(any(r.search(ligne) for r in _VOIX.values())
                or _EFFECTIF["votants"].search(ligne) or _UNANIME.search(ligne))


def _objet(avant: str, ordinale: bool, premier: bool, apres: str = "") -> str:
    """Le titre que le PV donne à l'acte.

    Marque numérotée (la CC) : « Objet : » après la marque s'il y est (liasse
    d'actes télétransmis) ; sinon le titre est juste au-dessus de « Délibération
    n° », parfois sur deux lignes. Cette ligne n'est un titre ni quand elle
    est la décision de l'acte précédent (ou sa suite), ni quand elle est une
    ligne de vote ou de cartouche, ni quand c'est de la prose (la fin d'une
    discussion, `_prose`) : objet vide, le rédacteur le complète. (Pas
    « tout ce qui suit la décision de l'acte d'avant » : dans un PV, le titre
    suit lui aussi la décision et le vote précédents.)
    Marque ordinale (le PV municipal) : le titre
    est en capitales, suivi de l'exposé, souvent avec des tableaux aux lignes
    en capitales aussi (« RECETTES », « SOLDE D'EXECUTION 0.00 ») ; c'est donc
    le PREMIER bloc de capitales depuis l'acte précédent, et pour le premier
    acte le dernier bloc (l'en-tête du PV est en capitales). Une proposition :
    le rédacteur la relit."""
    if not ordinale and (titre := _libelle_objet(apres)):
        return titre
    lignes = [l.strip() for l in avant.splitlines() if l.strip()]
    lignes = [l for l in lignes if not _NUMERAL.match(l) and not _TAMPON.match(l)]
    if not lignes:
        return ""
    if not ordinale:
        j = len(lignes) - 1
        while j > 0 and lignes[j][:1].islower() and not _DECISION.match(lignes[j]):
            j -= 1
        if _DECISION.match(lignes[j]) or _ligne_de_vote(lignes[-1]):
            return ""
        titre = lignes[-1]
        if titre[:1].islower() and len(lignes) > 1:
            if _prose(lignes[-2]):
                return ""
            titre = f"{lignes[-2]} {titre}"
        return "" if _prose(titre) else titre[:160]
    blocs, bloc = [], []
    for l in lignes:
        if _capitales(l) and len(re.findall(r"\d+", l)) <= 1 and not _DECISION.match(l):
            bloc.append(l)
        elif bloc:
            blocs.append(bloc)
            bloc = []
    if bloc:
        blocs.append(bloc)
    if not blocs:
        return ""
    return " ".join(blocs[-1] if premier else blocs[0])[:160]


def _decompte_cartouche(corps: str) -> tuple[dict, str]:
    trouves = {k: m for k, r in _VOIX.items() if (m := r.search(corps))}
    if "pour" not in trouves:
        return {}, ""
    debut = min(m.start() for m in trouves.values())
    fin = max(m.end() for m in trouves.values())
    return {k: int(m.group(1)) for k, m in trouves.items()}, _norm(corps[debut:fin])


def _decompte_prose(corps: str) -> tuple[dict, str]:
    """« avec 26 voix pour et 1 abstention », « par 20 voix pour, 2 contre »."""
    if not (m := _PROSE_POUR.search(corps)):
        return {}, ""
    suite = corps[m.end(): m.end() + 120]
    if (arret := re.search(r"\.\s|;|\n\s*\n", suite)):
        suite = suite[:arret.start()]
    voix, fin = {"pour": int(m.group(1))}, m.end()
    for k, r in _PROSE.items():
        if x := r.search(suite):
            voix[k] = int(x.group(1))
            fin = max(fin, m.end() + x.end())
    return voix, _norm(corps[m.start():fin])


def _vote(corps: str) -> tuple[dict | None, dict, str]:
    """Le vote d'un acte, ses citations, et l'écart que la pièce porte.

    « Unanimité » ne se donne qu'à un acte sans voix contre ni abstention : le
    corps d'un acte de la CC (« 26 voix pour et 1 abstention ») démentait son
    cartouche (27 pour), et le relevé disait « unanimité ». Trois formes
    distinctes : `unanimite` (vrai), `forme: unanimite_exprimes` (« à
    l'unanimité des suffrages exprimés » : des abstentions sont possibles, et
    ne la contredisent pas), `forme: aucune_voix_contre`. Le décompte du corps
    passe avant celui du cartouche : c'est la délibération."""
    cartouche, cit_cartouche = _decompte_cartouche(corps)
    prose, cit_prose = _decompte_prose(corps)
    u, sans_contre = _UNANIME.search(corps), _SANS_CONTRE.search(corps)
    decompte = prose or cartouche
    partage = any(decompte.get(k) for k in ("contre", "abstentions"))
    cites = {k: v for k, v in (("corps", cit_prose), ("cartouche", cit_cartouche),
                               ("mention", _norm(u.group(0)) if u else "")) if v}
    ecarts = []
    if prose and cartouche and any(prose.get(k, 0) != cartouche.get(k, 0)
                                   for k in ("pour", "contre", "abstentions")):
        ecarts.append(f"le corps de l'acte porte « {cit_prose} », "
                      f"le cartouche « {cit_cartouche} »")
    v = dict(decompte)
    if u and not u.group(1):
        if partage:
            quoi = cit_prose if prose and prose is decompte else cit_cartouche
            ecarts.append(f"l'acte dit « {cites['mention']} » et porte « {quoi} » : "
                          f"le vote n'est pas unanime")
        else:
            v["unanimite"] = True
    elif u:
        v["forme"] = "unanimite_exprimes"
    elif sans_contre and not decompte.get("contre"):
        v["forme"] = "aucune_voix_contre"
        cites["mention"] = _norm(sans_contre.group(0))
    anomalie = (ecarts[0][0].upper() + " ; ".join(ecarts)[1:] + ".") if ecarts else ""
    return (v or None), cites, anomalie


def _citation(corps: str) -> str:
    """La première ligne de décision ; à défaut, la marque elle-même."""
    for ligne in corps.splitlines():
        if _DECISION.match(ligne.strip()):
            return ligne.strip()
    return corps.strip().splitlines()[0].strip()


def _iso(m: re.Match, decalage: int = 0) -> str | None:
    """La date ISO d'une correspondance de `_DATE` (groupes à partir de `decalage`)."""
    g = m.groups()[decalage:decalage + 6]
    try:
        if g[0]:
            mois = unicodedata.normalize("NFKD", g[1].lower()).encode("ascii", "ignore").decode()
            return date(int(g[2]), _MOIS.index(mois) + 1, int(g[0])).isoformat()
        return date(int(g[5]), int(g[4]), int(g[3])).isoformat()
    except (ValueError, TypeError):
        return None


def _etrangeres(texte: str, jour: str, positions: list[int]) -> list[tuple[int, int, str, str]]:
    """(début, fin, date, tête) de chaque procès-verbal d'une AUTRE séance que la
    pièce reproduit : le PV de la séance précédente annexé à la délibération qui
    l'approuve, dans une liasse. Il porte ses présents, ses votes, son lieu ; rien
    de tout cela n'est à cette séance, et l'annexe n'est pas une anomalie.

    Seules les têtes placées APRÈS la première marque d'acte comptent : la tête
    du PV lui-même ouvre la pièce. Une tête qui porte la date de la séance est
    la sienne (« Convocation du 16/09 … séance du 23/09 »). Le PV reproduit va
    jusqu'à la marque d'acte suivante."""
    if not positions or not jour:
        return []
    sortie = []
    for m in _TETE_PV.finditer(texte, positions[0]):
        fenetre = texte[m.start(): m.end() + 160]
        lues = [(x, d) for x in _DU_DATE.finditer(fenetre) if (d := _iso(x))]
        dates = {d for _, d in lues}
        if not dates or jour in dates:
            continue
        fin = next((p for p in positions if p > m.start()), len(texte))
        if sortie and m.start() < sortie[-1][1]:
            continue
        sortie.append((m.start(), fin, min(dates), _norm(fenetre[:lues[0][0].end()])))
    return sortie


def _hors(pos: int, zones: list[tuple]) -> bool:
    return not any(z[0] <= pos < z[1] for z in zones)


def _tampons(texte: str) -> list[dict]:
    """Chaque identifiant @ctes de la pièce, avec la mention « Publié le » du
    même tampon (avant lui, ou juste après)."""
    publies = list(_PUBLIE.finditer(texte))
    sortie = []
    for m in _ID_ACTES.finditer(texte):
        interne = re.sub(r"-[A-Z]{2}$", "", m.group(2))
        chiffres = re.findall(r"\d+", interne)
        t = {"id": m.group(0), "pos": m.start(), "n": int(chiffres[-1]) if chiffres else None,
             "date": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"}
        avant = [p for p in publies if m.start() - 200 <= p.end() <= m.start()]
        apres = [p for p in publies if m.end() <= p.start() <= m.end() + 200]
        if p := (avant[-1] if avant else apres[0] if apres else None):
            t["publie_le"] = date(int(p.group(3)), int(p.group(2)), int(p.group(1))).isoformat() \
                if 1 <= int(p.group(2)) <= 12 and 1 <= int(p.group(1)) <= 31 else None
            t["citation_publication"] = _norm(p.group(0))
        sortie.append(t)
    return sortie


def actes(texte: str, releve: dict) -> list[dict]:
    marques = _marques(texte, releve)
    jour = (releve.get("seance") or {}).get("date", "")
    zones = _etrangeres(texte, jour, sorted(p for _, p in marques))
    tampons = [t for t in _tampons(texte) if t["date"] == jour and t["n"] is not None]
    # Un montant n'est gardé que si le vérificateur le retrouvera : « CD30 150 000 € »
    # se lit chez lui « 30 150 000 », et « 6.277,57 » n'a pas de forme commune.
    lisibles = _nombres(_norm(texte))
    vus, sortie = set(), []
    for i, (n, debut) in enumerate(marques):
        if n in vus:          # un numéro répété : le vérificateur le dira, on ne double pas
            continue
        vus.add(n)
        precedente = marques[i - 1][1] if i else max(0, debut - 3000)
        fin = marques[i + 1][1] if i + 1 < len(marques) else len(texte)
        # Le PV d'une autre séance annexé à cet acte n'est pas cet acte : ses
        # votes et ses montants s'arrêtent à sa tête.
        annexe = next((z for z in zones if debut <= z[0] < fin), None)
        corps = texte[debut:annexe[0] if annexe else fin]
        if not corps.strip():
            corps = texte[debut:fin]
        # Le titre ne se cherche pas dans le PV d'une autre séance qui précède.
        borne = max([precedente, debut - 3000] + [z[1] for z in zones if z[1] <= debut])
        a = {"n": n, "objet": _objet(texte[borne:debut],
                                bool(releve.get("marque_acte_ordinale")), not i, corps),
             "citation": _citation(corps)}
        vote, cites, anomalie = _vote(corps)
        if vote:
            a["vote"] = vote
        if cites and (vote or anomalie):
            a["vote_cite"] = cites
        if anomalie:
            a["anomalie"] = _a_voir_sur_image(anomalie)
        if annexe:
            a["annexe_pv"] = {"seance": annexe[2], "citation": annexe[3]}
        # Le tampon de l'acte se reconnaît à son identifiant (date de la séance,
        # numéro de l'acte), pas à sa place : il précède souvent la marque.
        if t := next((t for t in tampons if t["n"] == n), None):
            a["identifiant"] = t["id"]
            if t.get("publie_le"):
                a["publie_le"], a["citation_publication"] = t["publie_le"], t["citation_publication"]
        # Marque ordinale : l'exposé chiffré PRÉCÈDE « après en avoir délibéré »,
        # donc tombe dans l'acte d'avant (CM du 02/10/2025 : tous les montants
        # décalés d'un acte). Seuls ceux des lignes de décision sont sûrs ici ;
        # un montant manquant vaut mieux qu'un montant prêté au voisin.
        lieu = corps
        if releve.get("marque_acte_ordinale"):
            lieu = "\n".join(l for l in corps.splitlines() if _DECISION.match(l.strip()))
        montants = list(dict.fromkeys(
            re.sub(r"[  ]", " ", m.group(0)) for m in _MONTANT.finditer(lieu)
            if _cle_nombre(m.group(0)) in lisibles))
        if montants:
            a["montants"] = montants[:12]
        sortie.append(a)
    return sortie


def squelette(code: str, pv: str | None, convocation: str | None, chemins: dict,
              urls: dict, comme: dict | None = None, par: str = "",
              depots: dict | None = None) -> dict:
    """`depots` : {source: AAAA-MM-JJ}, la date de dépôt du fichier (médiathèque,
    Last-Modified), gardée à part dans `depot_fichier` : jamais une publication."""
    comme = comme or {}
    seance = {k: v for k, v in (comme.get("seance") or {}).items()
              if k in ("assemblee", "assemblee_court")}
    seance["date"] = code[:10]
    releve = {"code": code[11:], "seance": seance,
              "origine": {"releve": par, "le": date.today().isoformat(),
                          "statut": "a_relire", "relu_par": None},
              "sources": chemins, "sources_url": urls}
    if depots:
        releve["depot_fichier"] = dict(depots)
    _marque(releve, [comme], code, pv)
    if pv is None:
        releve["source_actes"] = "convocation"
        releve["actes"] = []
        releve["en_clair"] = {"avant": {}, "apres": {
            "statut": "non_publie", "titre": "", "chapeau": "", "pied": "",
            "texte": "Les délibérations de cette séance ne sont pas encore en ligne."}}
    else:
        releve["actes"] = actes(pv, releve)
        releve["en_clair"] = {"avant": {}, "apres": {}}
    releve["anomalies_seance"] = []
    releve["calculs"] = _compte(releve["actes"])
    if pv is not None:
        _seance_depuis_pv(releve, pv)
    if convocation and not seance.get("lieu"):
        lieu, citation = _lieu(convocation)
        if lieu:
            seance["lieu"], seance["citation_lieu"], seance["source_lieu"] = \
                lieu, citation, "convocation"
    return releve


def completer(releve: dict, pv: str, chemin: str, url: str | None,
              comme: dict | None = None, par: str = "") -> dict:
    """Le relevé d'une séance dont seule la convocation était publiée, complété
    des délibérations parues depuis. La feuille « avant » et ses sources ne
    bougent pas ; la feuille « après » est remise à rédiger, et le relevé
    redevient « à relire » : l'atelier devra le RETENIR de nouveau."""
    if (releve.get("en_clair", {}).get("apres") or {}).get("statut") != "non_publie" \
            or releve.get("actes"):
        raise ValueError("ce relevé a déjà ses actes : rien à compléter")
    r = {k: v for k, v in releve.items() if k != "source_actes"}
    r["seance"] = dict(releve["seance"])
    r["origine"] = {"releve": par or releve["origine"].get("releve", ""),
                    "le": date.today().isoformat(), "statut": "a_relire", "relu_par": None}
    r["sources"] = {**releve["sources"], "pv": chemin}
    if url:
        r["sources_url"] = {**releve.get("sources_url", {}), "pv": url}
    # La marque posée sur la seule convocation n'a jamais été confrontée à une
    # pièce : elle est rechoisie sur les délibérations, la sienne en premier.
    for k in _MARQUES:
        r.pop(k, None)
    _marque(r, [releve, comme or {}], f"{releve['seance']['date']}-{releve['code']}", pv)
    r["actes"] = actes(pv, r)
    r["en_clair"] = {**releve["en_clair"], "apres": {}}
    r["calculs"] = [c for c in releve.get("calculs", []) if c.get("formule") != "n_actes"] \
        + _compte(r["actes"])
    r["anomalies_seance"] = list(releve.get("anomalies_seance", []))
    _seance_depuis_pv(r, pv)
    return r


_MARQUES = ("marque_acte", "marque_acte_regex", "marque_acte_ordinale")


#: Les marques essayées après celles des relevés modèles, relevées sur les
#: relevés de Lasalle (2026) : numérotées d'abord, ordinales ensuite.
#: Pas de `(?m)^` : le vérificateur lit la pièce aux blancs fondus, sans fins de
#: ligne, et une telle marque n'y trouvait rien. L'en-tête d'un acte télétransmis
#: de la CC porte l'année de la séance et est suivi du cartouche « DEPARTEMENT :
#: GARD » ; un renvoi (« Vu la délibération N°67/2023 ») n'a pas les deux.
NUMEROTEES = (("marque_acte_regex", MARQUE_CC),
              ("marque_acte_regex", r"N°\s*(\d{1,3})\s*/\s*{annee}(?=[\s\S]{0,40}D[ÉE]PARTEMENT)"))
ORDINALES = (("marque_acte_ordinale", r"EXTRAIT DU REG"),
             ("marque_acte_ordinale", r"apr[eè]s (?:en )?avoir d[ée]lib[ée]r[ée]"))


def _marque(releve: dict, modeles: list[dict], code: str, texte: str | None) -> None:
    """Pose la marque d'acte. Sans pièce : celle du modèle, ou celle de la CC.

    Avec la pièce : une marque NUMÉROTÉE dès qu'elle y découpe au moins trois
    actes aux numéros distincts, celle qui atteint le plus grand numéro (la CC
    écrit aussi « après en avoir délibéré », parfois deux fois par acte :
    compter les ordinales la ferait gagner à tort) ; sinon l'ordinale qui
    trouve le plus d'actes. Les modèles passent d'abord à
    égalité, sans s'imposer : « N° DEL2610_ » n'ouvre que le CM d'octobre 2026."""
    candidats = [(k, m[k]) for m in modeles for k in _MARQUES if m.get(k)]
    if texte is None:
        if candidats:
            releve[candidats[0][0]] = candidats[0][1]
        elif code.endswith("cc"):
            releve["marque_acte_regex"] = MARQUE_CC
        return

    def lus(k: str, v: str) -> list[tuple[int, int]]:
        try:
            return _marques(texte, {k: v})
        except re.error:
            return []

    # Numérotées : la suite aux plus GRANDS numéros. Une liasse reproduit
    # souvent le PV de la séance précédente qu'elle approuve, avec ses propres
    # « Délibération n° » : CC du 29/05/2024, n°53-106 (le 3 avril) dans le PV
    # approuvé, n°107-129 en tête des vrais actes. Prendre la suite la plus
    # longue relevait l'autre séance, et le vérificateur n'y voyait rien.
    numerotees = []
    annee = [(k, v.replace("{annee}", code[:4])) for k, v in NUMEROTEES]
    for k, v in [*candidats, *annee]:
        if k != "marque_acte_ordinale" and len(nums := {n for n, _ in lus(k, v)}) >= 3:
            numerotees.append((max(nums), len(nums), k, v))
    if numerotees:
        _, _, k, v = max(numerotees, key=lambda c: (c[0], c[1]))   # premier à égalité
        releve[k] = v
        return
    ordinales = [(len(lus(k, v)), k, v) for k, v in [*candidats, *ORDINALES]
                 if k == "marque_acte_ordinale"]
    n, k, v = max(ordinales, key=lambda c: c[0], default=(0, "", ""))
    if n:
        releve[k] = v


def _cartouches(pv: str, zones: list[tuple]) -> list[dict]:
    """Les blocs d'effectif de la pièce : des « libellé : nombre » voisins (moins
    de 200 caractères entre deux), hors des PV d'autres séances. Un libellé déjà
    lu ouvre le bloc suivant (chaque acte télétransmis a son cartouche)."""
    lus = sorted((m.start(), m.end(), k, int(m.group(1)))
                 for k, r in _EFFECTIF.items() for m in r.finditer(pv) if _hors(m.start(), zones))
    blocs, bloc = [], {}
    for debut, fin, k, v in lus:
        if bloc and (k in bloc or debut - max(x[1] for x in bloc.values()) > 200):
            blocs.append(bloc)
            bloc = {}
        bloc[k] = (debut, fin, v)
    if bloc:
        blocs.append(bloc)
    return [b for b in blocs if "presents" in b or "votants" in b]


def _effectif(pv: str, bloc: dict) -> tuple[dict, str, list[str]]:
    """(effectif lu, citation des votants, écarts de la pièce) d'un bloc.

    Votants = présents + procurations ; un suppléant imprimé à part (« Suppléant :
    1 ») siège pour un titulaire et s'ajoute aux présents, un « dont 2
    suppléants » y est déjà. La citation des votants ne contient jamais les
    suffrages exprimés. Le quorum est la majorité des membres en exercice."""
    e = {k: v for k, (_, _, v) in bloc.items()}
    cite = lambda *ks: _norm(pv[min(bloc[k][0] for k in ks): max(bloc[k][1] for k in ks)])
    ecarts, citation = [], ""
    calcule = None
    if "presents" in e and "procurations" in e:
        calcule = e["presents"] + e.get("suppleants", 0) + e["procurations"]
        ks = [k for k in ("presents", "suppleants", "procurations") if k in bloc]
        citation = cite(*ks)
        if re.search(r"exprim", citation, re.I):
            citation = ""
    dit = e.get("votants")
    if calcule is not None and dit is not None and dit != calcule:
        sup = f", {e['suppleants']} suppléant{'s' if e['suppleants'] > 1 else ''}" \
            if "suppleants" in e else ""
        ecarts.append(f"La pièce indique « {cite('votants')} » ; {e['presents']} présents{sup} "
                      f"et {e['procurations']} procuration{'s' if e['procurations'] > 1 else ''} "
                      f"font {calcule} votants.")
    elif dit is not None and (calcule is None or not citation or dit == calcule):
        citation = cite("votants")
    votants = calcule if calcule is not None and citation else dit if citation else None
    if votants is not None:
        e["votants"] = votants
    if dit is not None and dit != votants:
        e["votants_dit"] = dit
    if "exprimes" in e and votants is not None and e["exprimes"] > votants:
        ecarts.append(f"La pièce indique « {cite('exprimes')} » pour {votants} votants : "
                      f"les suffrages exprimés ne peuvent dépasser les votants.")
    if "en_exercice" in e:
        e["quorum_calcule"] = e["en_exercice"] // 2 + 1
        if "quorum" in e and e["quorum"] != e["quorum_calcule"]:
            ecarts.append(f"La pièce indique « {cite('quorum')} » ; la majorité des "
                          f"{e['en_exercice']} membres en exercice est {e['quorum_calcule']}.")
    if "quorum" in e:
        e["quorum_dit"] = e.pop("quorum")
    e["citation"] = cite(*bloc)
    return e, citation, ecarts


def _lieu(texte: str, zones: list[tuple] = ()) -> tuple[str, str]:
    """(lieu, citation) : le libellé « Lieu : », sinon la première phrase de
    réunion qui nomme un lieu. Rien trouvé : vide — le rédacteur ne le devine
    pas, et la ligne de date d'une lettre n'est jamais lue."""
    if (m := _LIEU_LIBELLE.search(texte)) and _hors(m.start(), zones):
        lieu = _norm(m.group(1)).strip(" .;,")
        return lieu, _norm(m.group(0)).strip()
    for m in _REUNION.finditer(texte):
        if not _hors(m.start(), zones):
            continue
        phrase = _norm(texte[m.end(): m.end() + 300])
        if fin := re.search(r"\.\s+[A-ZÉÈ]|;|\.$", phrase):
            phrase = phrase[:fin.start()]
        x = _LIEU_NOM.search(phrase)
        if not x:
            x = next((v for v in _LIEU_VILLE.finditer(phrase)
                      if not _PAS_VILLE.match(v.group(1))), None)
        if not x:
            continue
        lieu = x.group(0)
        if coupe := _FIN_LIEU.search(lieu, 1):
            lieu = lieu[:coupe.start()]
        lieu = lieu.strip(" .;,")
        nom = re.sub(r"^(?:[àaÀ]\s+(?:la\s+|l[’']\s?)?|aux?\s+|dans\s+(?:la\s+|le\s+|les\s+|l[’']\s?)?|"
                     r"en\s+(?:la\s+)?)", "", lieu)
        return nom, lieu
    return "", ""


def _seance_depuis_pv(releve: dict, pv: str) -> None:
    """Ce que la pièce dit de la séance : effectif, publication, autres séances
    reproduites, lieu (s'il manque), et les écarts qu'elle porte."""
    seance = releve["seance"]
    jour = seance.get("date", "")
    zones = _etrangeres(pv, jour, sorted(p for _, p in _marques(pv, releve)))
    ecarts = []
    # Le bloc aux plus nombreux votants : chaque acte a son cartouche, et
    # l'effectif change avec les arrivées et les départs. Le premier (23 sur un
    # PV de la CC où un acte compte 25 voix) faisait une faute de vote à tort.
    lus = [_effectif(pv, b) for b in _cartouches(pv, zones)]
    for _, _, ec in lus:
        ecarts += ec
    if lus:
        e, citation, _ = max(lus, key=lambda x: (x[1] != "", x[0].get("votants", -1)))
        seance["effectif"] = e
        if citation:
            seance["votants"], seance["citation_quorum"] = e["votants"], citation
        if "quorum_calcule" in e:
            releve.setdefault("calculs", [])
            releve["calculs"] = [c for c in releve["calculs"]
                                 if not c.get("dit", "").startswith("quorum :")] + [
                {"valeur": str(e["quorum_calcule"]), "formule": f"{e['en_exercice']} // 2 + 1",
                 "dit": f"quorum : majorité des {e['en_exercice']} membres en exercice"}]
    # « Publié le » fait foi ; un seul jour pour la séance, sinon chaque acte le sien.
    tampons = _tampons(pv)
    jours = {(t["publie_le"], t["citation_publication"]) for t in tampons
             if t["date"] == jour and t.get("publie_le")}
    if not tampons:
        jours = {(date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat(), _norm(m.group(0)))
                 for m in _PUBLIE.finditer(pv) if _hors(m.start(), zones)
                 and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31}
    if len({j for j, _ in jours}) == 1:
        seance["publie_le"], seance["citation_publication"] = sorted(jours)[0]
    autres = {t["id"]: {"date": t["date"], "nature": "acte télétransmis", "citation": t["id"]}
              for t in tampons if t["date"] != jour}
    autres |= {z[3]: {"date": z[2], "nature": "procès-verbal reproduit", "citation": z[3]}
               for z in zones}
    if autres:
        releve["autres_seances"] = list(autres.values())
    if not seance.get("lieu"):
        lieu, citation = _lieu(pv, zones)
        if lieu:
            seance["lieu"], seance["citation_lieu"], seance["source_lieu"] = lieu, citation, "pv"
    deja = releve.setdefault("anomalies_seance", [])
    deja += [x for x in map(_a_voir_sur_image, dict.fromkeys(ecarts)) if x not in deja]


def _a_voir_sur_image(texte: str) -> dict:
    """Un écart lu dans un cartouche (effectif, quorum, décompte) vient de l'OCR,
    qui se trompe : le n°137 de la CC « Contre : (4) » était lu, la pièce porte
    « Contre : 0 ». Nature « cartouche », sans `vu_sur_image` : il n'est rendu
    qu'une fois quelqu'un a regardé l'image et l'a écrit."""
    return {"texte": texte, "nature": "cartouche"}


def _compte(liste: list) -> list:
    return [{"valeur": str(len(liste)), "formule": "n_actes", "dit": "délibérations"}] if liste else []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("code")
    p.add_argument("--pv")
    p.add_argument("--convocation")
    p.add_argument("--url-pv")
    p.add_argument("--url-convocation")
    p.add_argument("--comme")
    p.add_argument("--releve-par", default="")
    p.add_argument("--depot-pv", help="date de DÉPÔT du fichier (AAAA-MM-JJ), pas de publication")
    p.add_argument("--depot-convocation", help="date de DÉPÔT du fichier (AAAA-MM-JJ)")
    p.add_argument("--completer", action="store_true",
                   help="ajouter les délibérations parues au relevé existant (seule la convocation y était)")
    a = p.parse_args(argv)
    if not _CODE.match(a.code):
        sys.exit(f"Code de séance attendu : AAAA-MM-JJ-cm|cc, pas « {a.code} ».")
    if not (a.pv or a.convocation):
        sys.exit("Il faut au moins une pièce : --pv ou --convocation.")
    racine = Path.cwd()
    sortie = dossier(racine) / a.code / "releve.json"
    if a.completer and not (a.pv and sortie.exists()):
        sys.exit("--completer : il faut --pv et un relevé déjà là.")
    if sortie.exists() and not a.completer:
        sys.exit(f"{sortie} existe déjà : rien n'est écrasé (--completer s'il n'avait que la convocation).")
    chemins, urls, textes = {}, {}, {}
    for cle, chemin, url in (("pv", a.pv, a.url_pv), ("convocation", a.convocation, a.url_convocation)):
        if not chemin:
            continue
        rel = Path(chemin)
        if rel.is_absolute() or ".." in rel.parts:
            sys.exit(f"{chemin} : chemin relatif à l'instance attendu.")
        textes[cle] = (racine / rel).read_text(errors="replace")
        chemins[cle] = str(rel)
        if url:
            urls[cle] = url
    comme = json.loads(Path(a.comme).read_text()) if a.comme else None
    depots = {}
    for cle, jour in (("pv", a.depot_pv), ("convocation", a.depot_convocation)):
        if jour:
            try:
                depots[cle] = date.fromisoformat(jour).isoformat()
            except ValueError:
                sys.exit(f"--depot-{cle} : date AAAA-MM-JJ attendue, pas « {jour} ».")
    if a.completer:
        try:
            releve = completer(json.loads(sortie.read_text()), textes["pv"], chemins["pv"],
                               urls.get("pv"), comme, a.releve_par)
        except ValueError as e:
            sys.exit(f"{sortie} : {e}.")
        if depots:
            releve["depot_fichier"] = {**releve.get("depot_fichier", {}), **depots}
        sortie.write_text(json.dumps(releve, ensure_ascii=False, indent=1) + "\n")
        print(f"{sortie} : complété, {len(releve['actes'])} actes relevés")
        return 0
    releve = squelette(a.code, textes.get("pv"), textes.get("convocation"), chemins, urls,
                       comme, a.releve_par, depots)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(releve, ensure_ascii=False, indent=1) + "\n")
    print(f"{sortie} : {len(releve['actes'])} actes relevés"
          f"{' (aucun : délibérations non publiées)' if 'pv' not in textes else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
