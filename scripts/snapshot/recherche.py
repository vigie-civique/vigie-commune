"""Les index de recherche : ce que le navigateur télécharge pour chercher sans serveur.

`entity_index.json` liste les fiches publiées, les plus citées d'abord ;
`recherche_index.json` mêle acteurs, actes, marchés et versements ;
`recherche_texte.json` dit dans quels actes un mot courant est écrit. Écrits
compacts : l'indentation y pèserait sans servir personne.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

from scripts.snapshot.actes import PORTEE_PAR_PERIMETRE, TYPES_DELIBERES, TYPES_SEANCE
from scripts.snapshot.socle import write_json_compact


def write_search_index(out: Path, public_entities, communes: dict[int, str],
                       liens_count: dict[int, int]) -> int:
    """Index de recherche léger, et liste des ids pour le prérendu.

    Le site public exposait 2 673 acteurs sans le moindre champ de recherche :
    pour trouver une association il fallait la repérer à l'œil sur la carte ou
    dans une grille. L'index tient dans ~150 Ko et se filtre côté client, sans
    backend (le public est statique).

    `nb` (nombre d'actes rattachés) sert à classer les résultats : un acteur
    présent dans dix délibérations passe avant un homonyme dormant.
    """
    index = [
        {
            "id": e["id"],
            "n": e["name"],
            "t": e["type"],
            "s": e.get("short_name") or None,
            "c": communes.get(e["id"]) or None,
            # `p` : la commune ou l'intercommunalité. Un caractère de plus par
            # ligne, et l'annuaire peut trier les deux sans charger
            # `entities.json` (1,1 Mo) juste pour lire un champ.
            "p": PORTEE_PAR_PERIMETRE.get(e.get("perimetre") or "") or "territoire",
            # `a` : 1 en activité, 0 cessée, absent si les registres se taisent.
            **({"a": 1 if e["actif"] else 0} if e.get("actif") is not None else {}),
            **({"na": e["nature"]} if e.get("nature") else {}),
            **({"dt": e["derniere_trace"]} if e.get("derniere_trace") else {}),
            "nb": liens_count.get(e["id"], 0),
        }
        for e in public_entities
    ]
    index.sort(key=lambda r: (-r["nb"], r["n"] or ""))
    write_json_compact(out / "entity_index.json", {"entities": index, "total": len(index)})
    return len(index)


# ── Le texte des actes : des mots courants, jamais un nom ──────────────────
#
# La recherche ne lisait que les titres : un sujet traité dans le corps d'une
# délibération intitulée « Délibération n° 140 » restait introuvable. Le texte
# de chaque acte est pourtant publié (`extrait/<id>.json`).
#
# Mais ce texte garde les noms qu'il cite, parce qu'il se lit sous son titre,
# en contexte (règle du 27/10/2026). Un index de TOUS ses mots rendrait chaque
# personne citée retrouvable d'une seule requête, sur dix ans d'actes : c'est
# l'exposition d'un titre, sans le filtre des titres. D'où la règle, posée par
# l'avis du DPO du 13/11/2026, et tenue par construction :
#
#   1. un mot n'entre que s'il est dans le lexique des mots courants
#      (`lexique/mots_courants.txt`, dérivé de Morphalou 3.1 qui ne contient
#      aucun nom propre) : pas de lexique, pas d'index ;
#   2. et seulement pour un acte où TOUTES ses occurrences sont en minuscules,
#      aucune ne suivant une civilité. « boucher » écrit en minuscules trouve
#      l'acte qui parle du boucher ; l'acte qui cite « M. Boucher », ou
#      « BOUCHER Jean », ne sort pas pour ce mot, même s'il parle aussi du
#      commerce. Le doute retire le mot, pas l'inverse.
#
# `scripts/verify_snapshot.py` le recontrôle sur les fichiers écrits, avec son
# propre code, et bloque la publication.
LEXIQUE = Path(__file__).resolve().parent / "lexique" / "mots_courants.txt"

_MOT = re.compile(r"[^\W\d_]+")
# Derrière une civilité, les trois « mots » qui suivent sortent de l'acte, quelle
# que soit leur casse et la ponctuation qui les sépare (« Mme ZANCHI, responsable
# du pôle ») : plus large que ce que le contrôle vérifie, pour que le doute
# retire toujours le mot. « me » (maître) attrape aussi le pronom : tant pis.
# La suite est lue sans être consommée (`(?=…)`) : dans « M. X et Madame Y »,
# la seconde civilité compte aussi.
_CIVILITE = re.compile(
    r"(?<![^\W\d_])(?:monsieur|madame|mademoiselle|messieurs|mesdames|mm\.|m\.|mmes?|mlle"
    r"|me|ma[iî]tre|dr|docteur|consorts|veuve|famille|[ée]poux|[ée]pouse)\.?\s+"
    r"(?=(\S+(?:\s+\S+){0,2}))", re.IGNORECASE)
# Les mêmes que `public/src/lib/recherche.js` : jamais cherchés, donc jamais écrits.
_VIDES = frozenset(("le la les l de des du d un une et en au aux a ou que qui "
                    "pour par sur dans avec ce cette ces son sa ses leur leurs").split())


def cle_de_recherche(mot: str) -> str:
    """Le mot tel que la page le cherche : sans accent ni casse, pluriel replié.

    Doit rendre ce que `plier` puis `racine` rendent dans
    `public/src/lib/recherche.js` (essai croisé : tests/test_recherche_site.py).
    """
    m = mot.lower().replace("œ", "oe").replace("æ", "ae")
    m = "".join(c for c in unicodedata.normalize("NFD", m) if not unicodedata.combining(c))
    return m[:-1] if len(m) > 3 and m[-1] in "sx" else m


def lire_lexique(chemin: Path | None = None) -> frozenset[str] | None:
    """Les mots courants, une forme par ligne, en minuscules ; `None` si absent."""
    chemin = chemin or LEXIQUE
    if not chemin.is_file():
        return None
    return frozenset(l.strip() for l in chemin.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("#"))


def mots_indexables(texte: str, lexique: frozenset[str]) -> set[str]:
    """Les clés de recherche qu'un acte peut porter (règle en tête de section)."""
    gardes: set[str] = set()
    ecartes: set[str] = set()
    for m in _MOT.finditer(texte):
        mot = m.group(0)
        if len(mot) < 3:
            continue
        cle = cle_de_recherche(mot)
        if mot != mot.lower():
            ecartes.add(cle)
        elif mot in lexique and mot not in _VIDES:
            gardes.add(cle)
    for m in _CIVILITE.finditer(texte):
        ecartes.update(cle_de_recherche(mot) for mot in _MOT.findall(m.group(1)))
    return gardes - ecartes


def write_texte_index(out: Path, textes: dict[int, str],
                      lexique: frozenset[str] | None) -> int:
    """`recherche_texte.json` : pour chaque mot courant, les actes qui l'écrivent.

    Rend le nombre de mots. Sans lexique, n'écrit rien ET retire le fichier
    d'une construction précédente : un index périmé citerait des actes retirés.
    """
    fichier = out / "recherche_texte.json"
    if not lexique or not textes:
        fichier.unlink(missing_ok=True)
        return 0
    mots: dict[str, list[int]] = {}
    for i in sorted(textes):
        for cle in mots_indexables(textes[i] or "", lexique):
            mots.setdefault(cle, []).append(i)
    write_json_compact(fichier, {"format": "vigie-recherche-texte/1",
                                 "actes": len(textes),
                                 "mots": dict(sorted(mots.items()))})
    return len(mots)


def write_recherche_index(out: Path, public_entities, public_events,
                          marches_data, public_flows, communes: dict[int, str],
                          liens_count: dict[int, int], seances_relues=(),
                          dossiers_publies=(), avec_texte=frozenset()) -> int:
    """Index de recherche transversal : acteurs, actes, marchés, versements.

    La recherche ne portait que sur les acteurs. Or on ne cherche pas seulement
    « qui » : on cherche « piscine », « école », « assainissement », « 15 000 »,
    une parcelle, une année. Chercher un mot et ne trouver que des noms
    d'entreprises donne l'impression que le site ne sait rien d'un sujet dont
    il a pourtant les actes.

    Format court volontaire (`k`, `t`, `n`, `d`, `u`, `m`) : l'index est
    embarqué dans la page et chaque clé est répétée à chaque ligne.
    Les champs :
      k  catégorie  acteur | acte | marche | versement | dossier | seance
      t  titre affiché
      n  poids de tri (plus grand = remonte)
      d  date, quand elle existe
      u  destination : une page du site, ou l'adresse d'origine d'un
         événement que le site n'affiche pas acte par acte ; absente quand il
         n'y en a aucune — jamais une adresse qui ne montre pas le résultat
      m  montant, quand il y en a un
      c  commune ou contexte
      s  sigle d'un acteur
      i  identifiant d'un acte dont le texte est publié (`extrait/<i>.json`) :
         c'est par lui que `recherche_texte.json` désigne l'acte
    """
    idx: list[dict] = []

    for e in public_entities:
        idx.append({
            "k": "acteur", "t": e["name"], "u": f"/entite/{e['id']}",
            # Le sigle : « ADMR » doit trouver l'association que son nom complet
            # ne laisse pas deviner (il était dans `entity_index.json` seul).
            **({"s": e["short_name"]} if e.get("short_name") else {}),
            "c": communes.get(e["id"]) or None,
            "n": 1000 + liens_count.get(e["id"], 0),
        })

    # Les textes relus d'abord : ce sont eux qui répondent à une question.
    for d in dossiers_publies:
        idx.append({"k": "dossier", "t": d["titre"], "u": f"/dossiers/{d['slug']}",
                    "n": 2000})
    for s in seances_relues:
        page = s["fichier"].removeprefix("conseils/").removesuffix(".html")
        idx.append({"k": "seance", "t": f"{s['assemblee']} — {s['titre']}",
                    "u": f"/conseils/{page}", "d": s["date"], "n": 1900})
    for ev in public_events:
        annee = (ev.get("date") or "")[:4] or "sans-date"
        # Seuls les actes d'assemblée ont une ligne sur /deliberations/<année>.
        # Les autres événements (BODACC, permis, agenda) y pointaient aussi :
        # ≈ 1 400 résultats à Lasalle menaient à une ancre absente, parfois à
        # une année sans page (relevé du 04/10/2026). Ils mènent désormais à
        # leur source d'origine (refonte du contenu, décision 9).
        if ev.get("type") in TYPES_DELIBERES + TYPES_SEANCE:
            # L'ancre est la clé datée de l'acte quand elle est stable : un
            # résultat de recherche copié et partagé doit survivre au rejeu.
            u = f"/deliberations/{annee}#{ev.get('ancre') or 'a' + str(ev['id'])}"
        else:
            u = ev.get("source_url") or ev.get("page_url")
        idx.append({
            "k": "acte", "t": ev.get("title") or "(sans titre)",
            **({"u": u} if u else {}),
            **({"i": ev["id"]} if ev["id"] in avec_texte else {}),
            "d": ev.get("date"), "m": ev.get("montant_principal"),
            "c": ev.get("source"),
            # Un acte portant un montant est plus souvent ce qu'on cherche.
            "n": 500 + (200 if ev.get("montant_principal") else 0),
        })

    for m in marches_data:
        titre = m.get("objet") or "Marché"
        if m.get("titulaire_nom"):
            titre = f"{titre} — {m['titulaire_nom']}"
        idx.append({
            "k": "marche", "t": titre, "u": "/marches",
            "d": m.get("date_notif"), "m": m.get("montant"),
            "c": m.get("acheteur_nom"), "n": 600,
        })

    for f in public_flows:
        if not f.get("to_name"):
            continue
        idx.append({
            "k": "versement",
            "t": f"{f.get('type_norm') or f.get('type') or 'Flux'} — {f['to_name']}",
            "u": "/finances", "d": str(f["year"]) if f.get("year") else None,
            "m": f.get("amount"), "c": f.get("from_name"), "n": 400,
        })

    idx.sort(key=lambda r: (-r["n"], r["t"] or ""))
    write_json_compact(out / "recherche_index.json",
                       {"index": idx, "total": len(idx)})
    return len(idx)


def etape_index_recherche(out, entity_rows, public_entities, public_events,
                          public_links, marches_data, public_flows,
                          seances_relues, dossiers_publies, textes_extraits) -> dict:
    communes = {r["id"]: r.get("commune") for r in entity_rows}
    liens_count = Counter(l["entity_id"] for l in public_links)
    indexed = write_search_index(out, public_entities, communes, liens_count)
    recherche = write_recherche_index(out, public_entities, public_events,
                                      marches_data, public_flows,
                                      communes, liens_count, seances_relues,
                                      dossiers_publies,
                                      avec_texte=frozenset(textes_extraits))
    write_texte_index(out, textes_extraits, lire_lexique())
    return {"indexed": indexed, "recherche": recherche}
