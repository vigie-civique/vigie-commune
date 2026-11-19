"""Ce que la SOURCE a rendu, mesuré à l'appel : lot 1 de la sentinelle.

`collector_runs` compte ce que la TABLE a gagné. Avec `INSERT OR IGNORE`, une
source tarie et une source sans nouveauté y laissent le même zéro, et une API
qui renvoie la page d'accueil HTML de son nouveau portail laisse un step vert
(data.culture.gouv.fr, 22/10/2026). La mesure se prend donc à l'appel, pas
dans la base. Deux côtés :

  * les RÉPONSES, par hôte, sans toucher aux collecteurs : le gestionnaire
    `Mesure` est posé dans l'ouvreur d'urllib que `reseau.garder()` installe
    au démarrage de la collecte. Il voit chaque échange HTTP, sauts de
    redirection et erreurs compris : statut, type de contenu, taille annoncée,
    hôte vers lequel on est renvoyé. Il voit les en-têtes, pas le corps :
    envelopper la réponse casserait les collecteurs qui la lisent en flux ;
  * les ÉLÉMENTS lus, par source déclarée : `lu()`, appelé au point où le
    collecteur a compris la réponse. `archive.fetch_json` l'appelle seul quand
    la forme ne laisse aucun doute. `forme` (clés JSON, en-têtes CSV) est
    l'empreinte du lot 2, comparée d'une passe à l'autre : voir `forme_de`.

Le volume n'alerte que pour une source déclarée `complete` : celle qu'on
relit en entier à chaque passe. Une source interrogée « depuis la dernière
fois » (BODACC, Hub'Eau) rend zéro un jour sur deux sans que rien n'ait cassé.

Rien n'est noté hors d'une passe ouverte par `run_all.run_step` (`debut()`) :
le service de l'atelier, qui tourne des mois, n'accumule rien.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import unicodedata
import urllib.request
from collections import Counter
from urllib.parse import urljoin, urlsplit

# Part des éléments qu'une source complète peut perdre d'une passe à l'autre
# sans alerte. Même seuil que la table témoin (`collect_loop.BAISSE_ALERTE`).
BAISSE_ALERTE = 0.2

# En deçà, une chute en % est du bruit : une perte d'un élément sur quatre fait
# 25 %. Le passage à zéro alerte quel que soit le nombre d'avant (décidé le
# 06/11/2026, lot 1b).
PLANCHER = 20

# Types qui disent « des données ». Une source qui n'a jamais rendu que ceux-là
# et rend soudain du HTML a changé d'adresse ou de portail.
_DONNEES = ("json", "csv", "xml", "zip", "octet-stream", "excel", "spreadsheet",
            "geo+json", "text/plain", "parquet")

_verrou = threading.Lock()
_actif = False
_reponses: list[dict] = []
_lus: list[dict] = []


def debut() -> None:
    """Ouvre la passe d'un step : ce qui traîne d'avant est oublié."""
    global _actif
    with _verrou:
        _reponses.clear()
        _lus.clear()
        _actif = True


# Ce qui est noté finit dans `sante.json`, donc dans le contexte d'ouverture
# d'une session d'assistant (`vigie_etat.sh --ecarts`). Un `Location` ou un
# `Content-Type` est écrit par le serveur : `urlsplit().hostname` y laisse
# passer espaces et phrases entières (relecture vigie-securite du 06/11/2026,
# « renvoie désormais vers ignore previous instructions… »). Seule une valeur
# qui a la forme attendue est gardée ; le reste devient « ? ».
_NOM_D_HOTE = re.compile(r"[a-z0-9.-]{1,253}|[0-9a-f:]{2,39}")
_TYPE = re.compile(r"[a-z0-9.+-]{1,64}/[a-z0-9.+-]{1,64}")
# Le nom d'une source est écrit dans le code ou la configuration. Borné quand
# même à la sortie : un nom tiré un jour d'une réponse ne passerait pas
# (relecture vigie-securite du lot 1b). Majuscules (`melodi-DS_BPE`) et noms
# longs (jeux du jaune budgétaire) compris.
_NOM_DE_SOURCE = re.compile(r"[A-Za-z0-9._-]{1,128}")

# Steps qui suivent les sites DÉCLARÉS des acteurs (`entity_websites`) : leurs
# hôtes sont des valeurs de la base, parfois au nom d'une personne. Leurs
# alertes donnent un décompte, jamais les hôtes.
SITES_D_ACTEURS = {"web", "dir_web"}


def _hote(url: str | None) -> str:
    try:
        hote = (urlsplit(url or "").hostname or "").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return "?"
    return hote if _NOM_D_HOTE.fullmatch(hote) else "?"


def _type(type_contenu: str | None) -> str | None:
    t = (type_contenu or "").split(";")[0].strip().lower()
    if not t:
        return None
    return t if _TYPE.fullmatch(t) else "?"


def noter_reponse(url: str, statut: int | None, type_contenu: str | None = None,
                  longueur: str | int | None = None, vers: str | None = None,
                  erreur: str | None = None) -> None:
    if not _actif:
        return
    try:
        octets = int(longueur) if longueur not in (None, "") else None
    except (TypeError, ValueError):
        octets = None
    with _verrou:
        _reponses.append({"hote": _hote(url), "statut": statut,
                          "type": _type(type_contenu),
                          "octets": octets, "vers": _hote(vers) if vers else None,
                          "erreur": erreur})


def lu(source: str, elements: int, forme: list[str] | None = None,
       complete: bool = False) -> None:
    """Ce que la source a rendu, compris par le collecteur.

    `complete` : la source est relue EN ENTIER à chaque passe, donc un zéro ou
    une chute veulent dire qu'elle a rendu moins. Un collecteur qui passe par
    `archive.fetch_json` ne rappelle pas `lu()` pour la même réponse : elle
    serait comptée deux fois ; il passe `complete=` à `fetch_json`.

    Règles de pose (lot 1b) :
      * un fichier NATIONAL se compte avant le filtre local : des dizaines de
        milliers de lignes sont stables, trois lignes d'une commune ne le sont
        pas ;
      * un fichier gardé au magasin se compte là où il est LU, cache compris :
        une passe servie par le cache rend le même nombre, et la copie neuve
        se compare à l'ancienne le jour où elle arrive. Compté au seul
        téléchargement, il manquerait une passe sur deux et `alertes()` ne le
        comparerait jamais ;
      * une source interrogée « depuis la dernière fois » reste `complete=False` :
        sa forme sert le lot 2, son volume n'alerte pas.
    """
    if not _actif:
        return
    try:
        elements = int(elements)
    except (TypeError, ValueError):
        return                    # une mesure ne fait pas échouer la collecte
    with _verrou:
        _lus.append({"source": source, "elements": elements,
                     "forme": _forme(forme), "complete": bool(complete)})


# ── L'empreinte de forme (lot 2) ────────────────────────────────────────────
#
# Une forme est {"v": 2, "union": [...], "noyau": [...]} : les clés vues dans
# AU MOINS UN des 50 premiers éléments, et celles vues dans TOUS. Les clés du
# seul premier élément ne suffisaient pas : data-fair (ADEME) omet les champs
# vides, et sur 50 lignes du DPE on compte 50 formes distinctes, 145 clés sur
# la première, 208 sur l'union, 68 communes à toutes (essai du 06/11/2026 ;
# Hub'Eau, Géorisques, ODS et tabular-api écrivent leurs nuls et n'ont qu'une
# forme). La comparaison (`_ecart_de_forme`) ne dit « perdue » qu'une clé du
# NOYAU d'avant absente de toute l'UNION d'après : un champ épars ne peut pas
# alerter. Pour un CSV, noyau = union.
#
# `VERSION_FORME` change dès que le calcul change : deux formes de versions
# différentes ne se comparent jamais, sinon la première passe après un
# déploiement crierait sur toutes les sources. Une forme du lot 1 (liste brute)
# n'a pas de version : jamais comparée.
VERSION_FORME = 2
ECHANTILLON = 50
# Au-delà, la forme n'est pas gardée : une borne de stockage, pas un seuil. Le
# débordement est une marque qui RESTE (`_fondre`) : rendu en None, il se
# confondait avec « pas encore de forme » et la page suivante devenait la forme
# de la passe, d'où une fausse perte à la passe d'après (relecture
# vigie-securite du lot 2).
MAX_CLES = 1000
TROP = {"v": VERSION_FORME, "trop": True}
# Une clé plus longue n'est pas un nom de colonne : elle devient « ? » AVANT le
# NFKD, qui peut la multiplier par 18 (U+FDFA). Une clé de 50 Mo coûtait 300 Mo
# de mémoire et s'écrivait en base à chaque passe (relecture vigie-securite).
LONGUEUR_MAX = 200


def normaliser(cle) -> str:
    """Minuscules, sans accents (NFKD), espaces et tirets en `_`."""
    texte = str(cle)
    if len(texte) > LONGUEUR_MAX:
        return "?"
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore")
    return re.sub(r"[\s-]+", "_", texte.decode("ascii").lower().strip())


def _forme(valeur) -> dict | None:
    """Une forme v2 normalisée, à partir d'une forme v2 ou d'une liste de clés
    (en-têtes CSV : noyau = union). Ne lève jamais."""
    try:
        if isinstance(valeur, dict):
            if valeur.get("v") != VERSION_FORME:
                return None
            if valeur.get("trop"):
                return dict(TROP)
            union, noyau = valeur["union"], valeur["noyau"]
        elif isinstance(valeur, (list, tuple)):
            union = noyau = valeur
        else:
            return None
        if len(union) > MAX_CLES:
            return dict(TROP)
        union = {normaliser(c) for c in union}
        noyau = {normaliser(c) for c in noyau} & union
        if not union:
            return None
        return {"v": VERSION_FORME, "union": sorted(union), "noyau": sorted(noyau)}
    except Exception:
        return None


def forme_de(liste, sous: str | None = None) -> dict | None:
    """La forme des 50 premiers éléments d'une liste rendue par une source (ou
    de leur champ `sous`, `properties` d'un GeoJSON). Ne lève jamais : une
    mesure ne doit pas faire échouer la collecte qu'elle observe (relecture
    vigie-securite du lot 1b, 06/11/2026)."""
    try:
        union: set[str] = set()
        noyau: set[str] | None = None
        for element in liste[:ECHANTILLON]:
            if sous is not None and isinstance(element, dict):
                element = element.get(sous)
            if not isinstance(element, dict):
                continue
            if len(element) > MAX_CLES:
                return dict(TROP)
            cles = {normaliser(c) for c in element}
            union |= cles
            noyau = cles if noyau is None else noyau & cles
        if noyau is None:
            return None
        return _forme({"v": VERSION_FORME, "union": sorted(union), "noyau": sorted(noyau)})
    except Exception:
        return None


def compter_json(data) -> tuple[int | None, dict | None]:
    """Éléments et forme d'une réponse JSON, quand sa forme ne laisse aucun
    doute : une liste au sommet, ou `results` à côté de `total_count`
    (Opendatasoft). Ailleurs, (None, None) : au collecteur de dire."""
    if isinstance(data, list):
        liste = data
    elif isinstance(data, dict) and isinstance(data.get("results"), list) \
            and "total_count" in data:
        liste = data["results"]
    else:
        return None, None
    return len(liste), forme_de(liste)


def _fondre(a: dict | None, b: dict | None) -> dict | None:
    """Deux formes d'une même source dans une passe (pages, communes) :
    union des unions, intersection des noyaux. Un débordement reste."""
    if a is None or b is None:
        return a or b
    if a.get("trop") or b.get("trop"):
        return dict(TROP)
    union = set(a["union"]) | set(b["union"])
    if len(union) > MAX_CLES:
        return dict(TROP)
    return {"v": VERSION_FORME, "union": sorted(union),
            "noyau": sorted(set(a["noyau"]) & set(b["noyau"]))}


def resume() -> list[dict]:
    """Les lignes à écrire pour la passe en cours, agrégées par hôte et par source."""
    with _verrou:
        reponses, lus = list(_reponses), list(_lus)
    lignes: dict[tuple, dict] = {}
    for r in reponses:
        ligne = lignes.setdefault(("reponses", r["hote"]), {
            "nature": "reponses", "cle": r["hote"], "requetes": 0,
            "statuts": Counter(), "types": Counter(), "vers": set(),
            "octets": 0, "sans_taille": 0})
        ligne["requetes"] += 1
        ligne["statuts"][str(r["statut"]) if r["erreur"] is None else f"erreur:{r['erreur']}"] += 1
        if r["type"]:
            ligne["types"][r["type"]] += 1
        if r["vers"] and r["vers"] != r["hote"]:
            ligne["vers"].add(r["vers"])
        if r["statut"] is not None and not 300 <= r["statut"] < 400:
            if r["octets"] is None:
                ligne["sans_taille"] += 1
            else:
                ligne["octets"] += r["octets"]
    for l in _lus_agreges(lus):
        lignes[("elements", l["cle"])] = l
    sortie = []
    for ligne in lignes.values():
        if ligne["nature"] == "reponses":
            # Une somme partielle se lirait comme la taille de la réponse.
            octets = None if ligne.pop("sans_taille") else ligne["octets"]
            ligne.update(octets=octets, statuts=dict(ligne["statuts"]),
                         types=dict(ligne["types"]), vers=sorted(ligne["vers"]))
        sortie.append(ligne)
    return sortie


def _lus_agreges(lus: list[dict]) -> list[dict]:
    par_source: dict[str, dict] = {}
    for l in lus:
        a = par_source.setdefault(l["source"], {
            "nature": "elements", "cle": l["source"], "elements": 0,
            "forme": None, "complete": False})
        a["elements"] += l["elements"]
        a["forme"] = _fondre(a["forme"], l["forme"])
        a["complete"] = a["complete"] or l["complete"]
    return list(par_source.values())


def fin(conn, run_id: int | None) -> None:
    """Écrit la passe dans `collector_mesures` et la ferme. Ne lève jamais :
    une mesure manquée ne doit pas faire échouer une collecte."""
    global _actif
    try:
        if conn is not None and run_id is not None:
            for l in resume():
                conn.execute(
                    "INSERT INTO collector_mesures (run_id, nature, cle, requetes,"
                    " statuts, types, vers, octets, elements, forme, complete)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, l["nature"], l["cle"], l.get("requetes"),
                     _j(l.get("statuts")), _j(l.get("types")), _j(l.get("vers")),
                     l.get("octets"), l.get("elements"), _j(l.get("forme")),
                     int(l["complete"]) if "complete" in l else None))
            conn.commit()
    except Exception as e:
        # Une passe à moitié écrite ne doit pas partir avec la clôture du journal.
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"  ⚠ mesure de la source non écrite : {e}")
    finally:
        with _verrou:
            _actif = False
            _reponses.clear()
            _lus.clear()


def _j(valeur) -> str | None:
    return None if valeur is None else json.dumps(valeur, ensure_ascii=False, sort_keys=True)


# ── Les deux voies par où passent les requêtes ──────────────────────────────

class Mesure(urllib.request.BaseHandler):
    """Note chaque réponse d'urllib. Ordre 500 < 1000 (`HTTPErrorProcessor`) :
    il voit la réponse brute AVANT que les 3xx soient suivis et les 4xx/5xx
    levés, donc chaque saut et chaque refus."""

    def http_response(self, requete, reponse):
        # Un `Location` malformé (`http://[x`) fait lever `urljoin` : la mesure
        # ne change jamais ce que l'appelant reçoit.
        try:
            statut = reponse.status if hasattr(reponse, "status") else reponse.getcode()
            vers = reponse.headers.get("Location") if 300 <= (statut or 0) < 400 else None
            if vers:
                vers = urljoin(requete.full_url, vers)
            noter_reponse(requete.full_url, statut, reponse.headers.get("Content-Type"),
                          reponse.headers.get("Content-Length"), vers=vers)
        except Exception:
            pass
        return reponse

    https_response = http_response


class Ouvreur(urllib.request.OpenerDirector):
    """Note aussi ce qui n'a pas répondu (nom introuvable, délai, refus TLS),
    que certains collecteurs avalent. Un seul relevé par appel : une
    redirection rappelle `open` de l'intérieur."""

    _profondeur = threading.local()

    def open(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        p = getattr(self._profondeur, "n", 0)
        self._profondeur.n = p + 1
        try:
            return super().open(fullurl, data, timeout)
        except urllib.request.HTTPError:
            raise                      # déjà noté par `Mesure`, avec son statut
        except Exception as e:
            if p == 0:
                url = fullurl if isinstance(fullurl, str) else fullurl.full_url
                noter_reponse(url, None, erreur=type(e).__name__)
            raise
        finally:
            self._profondeur.n = p


def ouvreur(*gestionnaires) -> urllib.request.OpenerDirector:
    """`build_opener` avec la mesure en plus."""
    o = urllib.request.build_opener(*gestionnaires, Mesure)
    o.__class__ = Ouvreur          # même état, `open` qui note les échecs
    return o


def suivre(session):
    """Pose la mesure sur une session `requests` (trois collecteurs)."""
    def _noter(reponse, *args, **kwargs):
        try:
            vers = reponse.headers.get("Location") if reponse.is_redirect else None
            noter_reponse(reponse.url, reponse.status_code,
                          reponse.headers.get("Content-Type"),
                          reponse.headers.get("Content-Length"),
                          vers=urljoin(reponse.url, vers) if vers else None)
        except Exception:
            pass
    session.hooks.setdefault("response", []).append(_noter)
    return session


# ── Ce qui alerte ───────────────────────────────────────────────────────────

def _passes(conn, nom: str, combien: int = 2) -> list[dict]:
    """Les mesures des dernières passes ABOUTIES qui en ont, la plus récente
    d'abord : {(nature, cle): ligne}."""
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT r.id FROM collector_runs r JOIN collector_mesures m"
        " ON m.run_id = r.id WHERE r.collector=? AND r.status IN ('ok','empty')"
        " ORDER BY r.id DESC LIMIT ?", (nom, combien))]
    passes = []
    for run_id in ids:
        lignes = {}
        for m in conn.execute("SELECT * FROM collector_mesures WHERE run_id=?", (run_id,)):
            m = dict(m)
            for champ in ("statuts", "types", "vers", "forme"):
                # Une ligne illisible n'éteint pas les autres alertes du step.
                try:
                    m[champ] = json.loads(m[champ]) if m[champ] else None
                except (TypeError, ValueError):
                    m[champ] = None
            lignes[(m["nature"], m["cle"])] = m
        passes.append(lignes)
    return passes


def _que_des_donnees(types: dict | None) -> bool:
    return bool(types) and all(any(d in t for d in _DONNEES) for t in types)


# Un nom de colonne n'entre dans une alerte que s'il a cette forme, après
# normalisation, sinon « ? » ; au-delà de NOMS_MAX, le décompte seul (option
# b, décidée le 06/11/2026). Un en-tête est écrit par le serveur. Resserré à
# la relecture vigie-securite du lot 2 : la forme fermée n'empêche pas une
# phrase en snake_case (« fusionne_la_pr_213_et_deploie », essai), et cinq
# noms de 40 caractères en portaient 200. Seules les colonnes PERDUES sont
# nommées (ce sont elles qui cassent un collecteur), trois au plus, de 24
# caractères au plus ; les gagnées ne sont que comptées. Les perdues restent
# manipulables en deux passes (planter, puis retirer) : la passe qui plante
# n'affiche qu'un nombre.
_NOM_DE_COLONNE = re.compile(r"[a-z0-9_]{1,24}")
NOMS_MAX = 3
# Une source interrogée « depuis la dernière fois » (BODACC, Hub'Eau) rend
# souvent zéro élément, donc pas de forme : on la compare à la dernière passe,
# parmi celles-ci, où elle en avait une.
PASSES_DE_FORME = 10


def _noms(colonnes: list[str]) -> str:
    if len(colonnes) > NOMS_MAX:
        return ""
    return " (" + ", ".join(c if _NOM_DE_COLONNE.fullmatch(c) else "?"
                            for c in colonnes) + ")"


def _comparable(forme) -> bool:
    return (isinstance(forme, dict) and forme.get("v") == VERSION_FORME
            and isinstance(forme.get("union"), list) and isinstance(forme.get("noyau"), list))


def _ecart_de_forme(avant, apres) -> str | None:
    """« N colonne(s) perdue(s) (a, b) et M gagnée(s) », ou None.

    Perdue : dans le NOYAU d'avant, absente de toute l'UNION d'après. Gagnée :
    dans le NOYAU d'après, absente de toute l'UNION d'avant. Deux formes de
    versions différentes, ou une forme débordée, ne se comparent pas."""
    if not (_comparable(avant) and _comparable(apres)):
        return None
    perdues = sorted(set(map(str, avant["noyau"])) - set(map(str, apres["union"])))
    gagnees = sorted(set(map(str, apres["noyau"])) - set(map(str, avant["union"])))
    if perdues and gagnees:
        return (f"{len(perdues)} colonne(s) perdue(s){_noms(perdues)} "
                f"et {len(gagnees)} gagnée(s)")
    if perdues:
        return f"{len(perdues)} colonne(s) perdue(s){_noms(perdues)}"
    if gagnees:
        return f"{len(gagnees)} colonne(s) gagnée(s)"
    return None


def alertes(conn, nom: str) -> list[str]:
    """Ce que la dernière passe aboutie de `nom` a d'anormal face à la précédente."""
    try:
        passes = _passes(conn, nom, PASSES_DE_FORME)
    except Exception:
        return []                     # base antérieure au lot 1 : rien à comparer
    if len(passes) < 2:
        return []
    derniere, precedente = passes[0], passes[1]
    hotes, sources, formes = [], [], []
    for (nature, cle), m in sorted(derniere.items()):
        if nature != "elements" or not m["forme"]:
            continue
        avant = next((p[(nature, cle)]["forme"] for p in passes[1:]
                      if p.get((nature, cle)) and p[(nature, cle)]["forme"]), None)
        ecart = _ecart_de_forme(avant, m["forme"])
        if ecart:
            nom_source = cle if _NOM_DE_SOURCE.fullmatch(cle or "") else "?"
            # L'écart AVANT le nom de la source : `vigie_etat.sh` coupe une
            # alerte à 200 caractères, et un nom de source en compte 128.
            formes.append(f"{nom} : {ecart}, source {nom_source}")
    en_html, renvoyes = 0, 0
    for (nature, cle), m in sorted(derniere.items()):
        avant = precedente.get((nature, cle))
        if avant is None:
            continue
        if nature == "reponses":
            types = m["types"] or {}
            if _que_des_donnees(avant["types"]) and any("html" in t for t in types):
                en_html += 1
                hotes.append(f"{nom} : {cle} rend du HTML là où il rendait "
                             f"{', '.join(sorted(avant['types']))} (page d'accueil, "
                             "portail refondu ?)")
            neufs = sorted(set(m["vers"] or []) - set(avant["vers"] or []))
            if neufs:
                renvoyes += 1
                hotes.append(f"{nom} : {cle} renvoie désormais vers {', '.join(neufs)}")
        elif m["complete"] and avant["elements"]:
            cle = cle if _NOM_DE_SOURCE.fullmatch(cle or "") else "?"
            if m["elements"] == 0:
                sources.append(f"{nom} : la source {cle} ne rend plus rien "
                               f"({avant['elements']} éléments à la passe précédente)")
            elif avant["elements"] >= PLANCHER \
                    and m["elements"] < avant["elements"] * (1 - BAISSE_ALERTE):
                sources.append(
                    f"{nom} : la source {cle} est passée de {avant['elements']} à "
                    f"{m['elements']} éléments "
                    f"({(avant['elements'] - m['elements']) / avant['elements']:.0%})")
    if nom in SITES_D_ACTEURS:
        hotes = [f"{nom} : {n} site(s) d'acteurs {quoi} (hôtes en base, non recopiés)"
                 for n, quoi in ((en_html, "rendent du HTML là où ils rendaient des données"),
                                 (renvoyes, "renvoient désormais vers un autre hôte"))
                 if n]
    return hotes + sources + formes
