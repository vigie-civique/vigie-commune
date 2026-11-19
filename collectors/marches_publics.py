"""
marches_publics.py — Marchés publics de la commune et de son EPCI

Sources :
  1. DECP (Données Essentielles de la Commande Publique) — data.gouv.fr
     Fichiers JSON mensuels, filtrés par SIRET acheteur.
     Ce que la loi oblige à publier : art. R2196-1 du code de la commande
     publique, rédaction du décret n° 2022-767 du 2 mai 2022 (art. 4), en
     vigueur depuis le 01/01/2024, relue sur Légifrance le 07/11/2026
     (LEGIARTI000045739606) :
       - marché d'un besoin ≥ 40 000 € HT : données essentielles sur le
         portail national de données ouvertes, dans les deux mois suivant
         la notification ou la modification ;
       - marché conclu SANS publicité ni mise en concurrence (R2122-8) d'une
         valeur ≥ 25 000 € HT : la même obligation, ou, au choix de
         l'acheteur, une liste annuelle publiée au premier trimestre, sur le
         support de son choix (objet, montant HT, date, attributaire et son
         code postal) ; cette liste n'arrive donc pas forcément au DECP ;
       - en dessous : l'article ne dit rien. Ne pas écrire « aucune
         obligation » : c'est une lecture, pas le texte.
     Le décret n° 2025-1386 du 29/12/2025 (JO du 30/12/2025, relu sur
     Légifrance le 07/11/2026) ne touche pas R2196-1 : il relève le seuil de
     dispense de R2122-8 à 100 000 € HT pour les travaux (01/01/2026) et à
     60 000 € HT pour les fournitures et services (01/04/2026).
     Note : une commune de 1 300 hab. publie peu dans le DECP — l'EPCI est la
     source principale. Ces trois requêtes (DECP consolidé, DECP v3, DECP
     augmenté) sont nationales : elles fonctionnent sans adaptation locale.
  2. Sites officiels de la commune et de l'intercommunalité, via le connecteur
     déclaré par l'instance (cf. collectors/connecteurs/).

Usage :
  python3 -m collectors.marches_publics --dry-run
  python3 -m collectors.marches_publics
  python3 -m collectors.marches_publics --source decp      # DECP uniquement
  python3 -m collectors.marches_publics --source site      # sites officiels
  python3 -m collectors.marches_publics --years 2022-2025  # notifications de ces années
"""

import argparse
import contextlib
import gzip
import hashlib
import json
import re
import os
import shutil
import sqlite3
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import (
    COMMUNE_INSEE, COMMUNE_NAME, COMMUNES_ADRESSE, COMMUNES_CP, DEPARTEMENT,
    COMMUNE_SIREN as COMMUNE_SIREN_CFG, COMMUNE_SIRENS, COMMUNES_ABSORBEES,
    COMMUNE_SIRET as COMMUNE_SIRET_CFG, EPCI_NOM, EPCI_SIREN,
    HEADERS, NATIONAL_STORE, REQUEST_DELAY
)
from . import mesure
from .db import transaction, upsert_entity
from .national_store import (copier_atomiquement, ecrivain, est_frais,
                             magasin_ecrivable, perime_lisible, publier_atomiquement)

# ── Constantes ────────────────────────────────────────────────────────────────

# SIRET/SIREN de la commune : ceux que déclare l'instance (config/instance.json).
# À relever sur recherche-entreprises.api.gouv.fr, au SIRET du siège de la mairie.
COMMUNE_SIRET  = COMMUNE_SIRET_CFG
COMMUNE_SIREN  = COMMUNE_SIREN_CFG

# EPCI — SIREN depuis config
CAC_SIREN      = EPCI_SIREN

# Les acheteurs relevés au titre de la commune : elle-même sous tous ses SIREN,
# et chaque commune absorbée d'un autre code (tranché le 02/11/2026). Ces
# dernières ne se rattachent PAS à la fiche de la commune (`acheteur_entity_id`
# ne connaît que COMMUNE_SIRENS) : le snapshot les marque « avant fusion ».
def sirens_acheteur() -> tuple[str, ...]:
    return (*COMMUNE_SIRENS, *COMMUNES_ABSORBEES)
# Jetons de reconnaissance de l'acheteur dans les libellés en texte libre du
# DECP et du BOAMP, où l'acheteur n'est pas toujours identifié par son SIREN.
# Dérivés de la config ; pour Brassac, par exemple : « brassac », « sidobre »,
# « vals et plateaux ».
# Les mots significatifs du nom de l'EPCI : « CC », « Communauté », « de »,
# « des » n'identifient personne. Le seuil de quatre lettres écarte les
# articles sans avoir à les énumérer.
def _norme_acheteur(nom: str) -> str:
    """Forme comparable d'un nom d'acheteur : sans accents, casse, ponctuation
    ni mots de structure. « CC Causses Aigoual Cévennes Terres Solidaires » et
    « Communauté de communes Causses-Aigoual-Cévennes Terres solidaires » sont
    le même acheteur ; « Terres australes françaises » ne l'est pas.
    """
    t = unicodedata.normalize("NFD", nom or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    # « mairie » et « ville » sont des façons de nommer la commune elle-même :
    # ils s'effacent. « syndicat », « sivom », « siaep » NON — ce sont d'autres
    # personnes morales, et les effacer reviendrait à confondre le syndicat des
    # eaux avec la mairie dont il porte le nom.
    # « com » : le BOAMP écrit « COM COMMUNES CAUSSES AIGOUAL CEVENNES ».
    # Sans lui, « com » restait collé en tête de la forme normalisée et aucun
    # rapprochement n'était possible — 18 marchés d'une intercommunalité, sur
    # une seule instance, restaient sans acheteur pour ces trois lettres.
    t = re.sub(r"\b(cc|ca|cu|com|communaute|communautes|commune|communes|mairie|mairies"
               r"|ville|villes|de|du|des|d|la|le|les|l)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


# Longueur minimale, en caractères normalisés, d'un nom TRONQUÉ par la source
# pour qu'il puisse désigner une collectivité. En dessous, un fragment attrape
# n'importe quoi : « CC » se normalise en chaîne vide, et « Saint » serait le
# préfixe de la moitié d'un département.
SEUIL_TRONCATURE = 10


def attribution_acheteur(nom: str, commune: str = "", epci: str = "",
                         homonymes: tuple[str, ...] = ()) -> str:
    """'commune', 'epci', ou '' si le nom déclaré ne permet pas de conclure.

    Le nom officiel EN TÊTE du nom déclaré : « CC Machin — service eau » est
    bien la CC. Ce qui est refusé, c'est un PRÉFIXE étranger — « Siaep de
    Lasalle » est le syndicat des eaux, pas la mairie, et « Paris Terres
    d'Envol » est un EPCI de Seine-Saint-Denis.

    Et le cas symétrique, qui manquait : la source TRONQUE le nom officiel.
    L'intercommunalité de la première instance s'appelle « CC Causses Aigoual
    Cévennes Terres Solidaires » ; le BOAMP l'écrit « CC Causses Aigoual
    Cévennes », sans le qualificatif final. Le nom déclaré est alors un préfixe
    du nom officiel, l'inverse de ce qui était testé, et 25 marchés — dont la
    construction d'une crèche dans la commune — restaient sans acheteur.

    Une troncature n'est acceptée que si elle ne convient qu'à UNE cible.
    `homonymes` porte les autres noms du périmètre : deux communes membres
    s'appellent « Saint-André-de-Valborgne » et « Saint-André-de-Majencoules »,
    et « Saint-André » ne désigne ni l'une ni l'autre.

    Le nom passe par les rectifications déclarées de l'instance : le BOAMP
    écrit « CC Causes Aigoual Cévennes » sur dix-huit avis, un S manquant qu'on
    ne devine pas par une règle mais qui se DÉCLARE une fois (cf.
    `nom_normalise.rectifier`).
    """
    from .nom_normalise import rectifier
    n = _norme_acheteur(rectifier(nom))
    if not n:
        return ""
    autres = tuple(_norme_acheteur(h) for h in homonymes)
    for cible, officiel in (("commune", commune or COMMUNE_NAME),
                            ("epci", epci or EPCI_NOM)):
        o = _norme_acheteur(officiel)
        if not o:
            continue
        if n.startswith(o):
            return cible
        if (len(n) >= SEUIL_TRONCATURE and o.startswith(n)
                and not any(a != o and a.startswith(n) for a in autres)):
            return cible
    return ""


# ── Recouper un acheteur avec le territoire ──────────────────────────────────
# Le BOAMP ne publie AUCUN SIREN : vérifié le 23/08/2026 sur les deux schémas
# qu'il sert. Le schéma historique (`Boamp_v230.xsd`) donne un bloc `IDENTITE`
# avec DENOMINATION, CP et VILLE ; le schéma eForms donne des organisations dont
# le `cbc:CompanyID` porte un identifiant européen opaque
# (« 61ABD31C-D0E7-9627-… »), jamais l'immatriculation française. Le recoupement
# par SIREN existe donc là où la source en donne un — les trois chemins DECP
# interrogent par SIREN d'acheteur — et il est IMPOSSIBLE sur le BOAMP.
#
# Ce que le BOAMP donne, en revanche, c'est l'ADRESSE DÉCLARÉE de l'acheteur.
# C'est elle qui sert de recoupement : un acheteur domicilié hors du territoire
# n'est pas l'acheteur qu'on cherche, quel que soit son nom.
#
# Sans ce filtre, la recherche par jetons ramène la France entière dès que le
# nom de l'EPCI contient un mot commun. Mesuré le 23/08/2026 : « coeur » ramène
# 3 668 avis (Cœur Essonne, Cœur de Flandre, Cœur du Var, Cœur de Loire, Cœur de
# Tarentaise…), « terres » 2 441, « cévennes » 626. Saillans en avait 400 en
# base, tous `probable`, tous hors sujet — ils n'atteignaient pas le site mais
# encombraient la file de revue, qui est le vrai goulot du dispositif.
#
# `code_departement` ne suffit pas : c'est le département de l'avis, pas celui
# de l'acheteur. Une recherche bornée au 26 remonte le SYDEO du Pouzin (07).

def _texte(v) -> str:
    """Valeur d'un champ eForms, qu'il soit une chaîne ou un objet `@languageID`."""
    if isinstance(v, dict):
        return str(v.get("#text") or "")
    return str(v or "")


def _adresses_declarees(noeud, trouvees: list | None = None) -> list[tuple[str, str, str]]:
    """(dénomination, code postal, ville) de toutes les organisations d'un avis.

    Les deux schémas du BOAMP sont parcourus par la même fonction : celui qui
    n'est pas là ne produit rien.
    """
    trouvees = [] if trouvees is None else trouvees
    if isinstance(noeud, dict):
        identite = noeud.get("IDENTITE")
        if isinstance(identite, dict):
            trouvees.append((str(identite.get("DENOMINATION") or ""),
                             str(identite.get("CP") or ""),
                             str(identite.get("VILLE") or "")))
        if "cac:PartyName" in noeud:
            adresse = noeud.get("cac:PostalAddress") or {}
            trouvees.append((
                _texte((noeud.get("cac:PartyName") or {}).get("cbc:Name")),
                _texte(adresse.get("cbc:PostalZone")),
                _texte(adresse.get("cbc:CityName")),
            ))
        for valeur in noeud.values():
            _adresses_declarees(valeur, trouvees)
    elif isinstance(noeud, list):
        for valeur in noeud:
            _adresses_declarees(valeur, trouvees)
    return trouvees


def localite_acheteur(avis: dict) -> tuple[str, str] | None:
    """Code postal et ville de l'acheteur d'un avis BOAMP, ou None s'il se tait.

    L'avis décrit plusieurs organisations — l'acheteur, les titulaires, le
    tribunal compétent, la centrale d'achat. On retient celle dont le nom est
    celui de l'acheteur ; à défaut, la première déclarée, qui est l'acheteur
    dans les deux schémas.
    """
    try:
        donnees = json.loads(avis.get("donnees") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    cherche = _norme_acheteur(avis.get("nomacheteur") or "")
    declarees = _adresses_declarees(donnees)
    for nom, cp, ville in declarees:
        if cherche and _norme_acheteur(nom) == cherche and (cp or ville):
            return (cp, ville)
    for nom, cp, ville in declarees:
        if cp or ville:
            return (cp, ville)
    return None


def _norme_lieu(nom: str) -> str:
    t = unicodedata.normalize("NFD", nom or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "", t)


_LIEUX_PERIMETRE = {_norme_lieu(c["nom"]) for c in COMMUNES_ADRESSE.values()}


def acheteur_du_territoire(localite: tuple[str, str] | None) -> bool | None:
    """L'acheteur est-il domicilié dans le périmètre ? None si la source se tait.

    Trois réponses et non deux : une adresse absente n'est pas une adresse
    étrangère. Le doute ne fait pas entrer un acheteur — il laisse la décision
    au nom, qui est l'autre indice.
    """
    if not localite:
        return None
    cp, ville = localite
    cp = (cp or "").strip()[:5]
    if cp and cp in COMMUNES_CP:
        return True
    if ville and _norme_lieu(ville) in _LIEUX_PERIMETRE:
        return True
    return False if (cp or ville) else None


ACHETEUR_JETONS = tuple({COMMUNE_NAME.lower()} | {
    mot.lower() for mot in re.split(r"[\s'’-]+", EPCI_NOM)
    if len(mot) > 4 and mot.lower() not in ("communaute", "communauté", "commune",
                                            "communes", "agglomeration",
                                            "agglomération")
})

# DECP — dataset consolidé officiel data.gouv.fr
DATAGOUV_API   = "https://www.data.gouv.fr/api/1/datasets/"
# Les libellés de source, écrits tels quels dans `events.source` et
# `marches_publics.source`. L'allowlist de publication (`events.public_sources`)
# les compare à l'IDENTIQUE : ils sont donc énumérés ici plutôt que répétés en
# clair, pour qu'un test puisse vérifier qu'aucun n'est absent de l'allowlist —
# c'est ainsi que 53 marchés DECP d'une instance sont restés collectés et jamais
# publiés, la liste des sources publiables ne parlant que de domaines et du BOAMP.
SOURCE_DECP_CONSOLIDE = "DECP data.gouv.fr"
SOURCE_DECP_V3        = "DECP v3 data.economie.gouv.fr"
SOURCE_DECP_ECO       = "DECP data.economie.gouv.fr"
SOURCE_BOAMP          = "BOAMP"
SOURCES = (SOURCE_DECP_CONSOLIDE, SOURCE_DECP_V3, SOURCE_DECP_ECO, SOURCE_BOAMP)

DECP_DATASET   = "donnees-essentielles-de-la-commande-publique-fichiers-consolides"
DECP_SLUG_AWS  = "donnees-essentielles-de-la-commande-publique-decp-de-marches-publics-info-awsolutions"

# Codes CPV fréquents pour communes rurales (pour information dans metadata)
CPV_LABELS = {
    "45": "Travaux de construction",
    "50": "Services de réparation et entretien",
    "55": "Services d'hôtellerie et restauration",
    "60": "Transport",
    "71": "Services d'architecture et ingénierie",
    "72": "Services informatiques",
    "79": "Services aux entreprises",
    "90": "Services d'assainissement/collecte déchets",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

# Échecs réseau accumulés pendant le run. Relevés en fin de main() : un run
# partiel doit finir en status='error' côté collect_loop, pas en 'empty' —
# même leçon que bodacc (17 annonces perdues en silence, 10/08/2026).
_echecs_reseau: list[str] = []


def _relever_echecs_reseau() -> None:
    if _echecs_reseau:
        raise RuntimeError(
            f"{len(_echecs_reseau)} échec(s) réseau pendant la collecte — "
            f"dernier : {_echecs_reseau[-1]}"
        )


def fetch_json(url: str, timeout: int = 30) -> dict | list | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"  [erreur JSON] {url[:60]} → {e}")
        _echecs_reseau.append(f"{url[:80]} → {e}")
        return None


# ── Source 1 : DECP data.gouv.fr ─────────────────────────────────────────────

def discover_decp_resources() -> list[dict]:
    """Récupère la liste des ressources DECP consolidées depuis data.gouv.fr."""
    data = fetch_json(DATAGOUV_API + DECP_DATASET + "/")
    if not data:
        print("  ⚠ Dataset DECP consolidé introuvable")
        return []
    resources = []
    for r in data.get("resources", []):
        title = r.get("title", "")
        url   = r.get("url", "")
        # Garder les fichiers mensuels (decp-YYYY-MM.json) et annuels (decp-YYYY.json)
        # Exclure decp-global.json (trop volumineux)
        if (url.endswith(".json")
                and re.search(r"decp-\d{4}", title)
                and "global" not in title.lower()):
            # L'URL vient du catalogue : `urlopen` lirait aussi bien un `file:`
            # local, que le magasin servirait ensuite à toutes les instances.
            if urllib.parse.urlsplit(url).scheme != "https":
                print(f"  ⚠ DECP : URL refusée, https attendu ({url[:80]})")
                _echecs_reseau.append(f"{url[:80]} → https attendu")
                continue
            resources.append({"title": title, "url": url})
    seen = set()
    unique = []
    for r in resources:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    print(f"  {len(unique)} fichiers DECP consolidés découverts")
    # Le catalogue grandit d'un fichier par mois ; les marchés du périmètre,
    # eux, sont trop peu pour mesurer la source.
    mesure.lu("decp-catalogue", len(unique), None, complete=True)
    return unique


# Les DECP commencent en 2018 ; une année hors de [2000, année courante + 1] est
# une date aberrante, pas un millésime (relecture vigie-securite du 08/11 : assez
# nombreuse, elle faisait énumérer des siècles « absents » sur /marches).
ANNEE_DECP_MIN = 2000


def annee_notification(date) -> int | None:
    """L'année d'une `dateNotification`, ou None si elle ne s'y lit pas."""
    t = str(date or "")[:4]
    if not t.isdigit():
        return None
    a = int(t)
    return a if ANNEE_DECP_MIN <= a <= datetime.now().year + 1 else None


# Un consolidé couvre une année quand au moins un marché sur cent qu'il porte y
# a été notifié. Le TITRE du fichier ne le dit pas : `decp-2019.json` porte les
# notifications de 2018 à 2023 (relevé du 07/11/2026 : 19 k, 144 k, 158 k,
# 193 k, 189 k et 99 k), et data.gouv ne publie aucun `decp-2020`, `-2021` ni
# `-2023`. Le seuil écarte les dates aberrantes (an 0001, 2089) et les marchés
# anciens republiés (969 de 2017 dans le même fichier).
PART_MIN_MILLESIME = 100


def millesimes_couverts(annees: Counter) -> set[int]:
    total = sum(annees.values())
    return {a for a, n in annees.items() if total and n * PART_MIN_MILLESIME >= total}


DECP_CACHE = NATIONAL_STORE / "decp"
DECP_CACHE_JOURS = 7
DECP_ESSAIS = 3


def _nom_cache(titre: str) -> str:
    """Nom de fichier sûr pour le cache — pas de séparateur venu d'une URL."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", titre)[:120] or "decp.json"


@contextlib.contextmanager
def _consolide_decp(url: str, nom: str):
    """Met un consolidé DECP à disposition SUR LE DISQUE, jamais en mémoire.

    Les fichiers annuels pèsent plusieurs centaines de Mo — 944 Mo pour 2019 — et
    static.data.gouv.fr rend la main lentement : un `read()` de 120 s expire
    régulièrement en milieu de transfert. Quatre conséquences, corrigées ici :

      - un seul fichier manquant faisait échouer tout le step en fin de course
        (`_relever_echecs_reseau`), et emportait les quatre steps suivants ;
      - relancer la collecte retéléchargeait les 37 fichiers déjà lus ;
      - l'échec n'était pas réessayé, alors qu'il est presque toujours passager ;
      - **le fichier était intégralement chargé en mémoire** avant d'être parsé.

    Le cache est daté : au-delà de DECP_CACHE_JOURS, le fichier est repris à la
    source. Les fichiers d'années révolues ne bougent plus, mais celui du mois
    en cours est réécrit tous les jours.

    Rend `None` quand la source est injoignable et qu'aucun cache ne subsiste.
    Quand le magasin refuse l'écriture, le fichier vit dans un temporaire, le
    temps de la lecture : un cache partagé en lecture seule n'interrompt pas une
    collecte.
    """
    cache = DECP_CACHE / nom
    if est_frais(cache, DECP_CACHE_JOURS):
        yield cache
        return

    # Décidé AVANT d'ouvrir la connexion : une réponse HTTP ne se rembobine pas.
    partage = magasin_ecrivable(DECP_CACHE, nom)
    # Seul l'écrivain du magasin le rafraîchit : un lecteur lit le périmé borné
    # plutôt que de retélécharger 5 Go par dossier.
    if not partage and perime_lisible(cache, DECP_CACHE_JOURS):
        yield cache
        return
    dossier_temporaire = None if partage else tempfile.mkdtemp(prefix="vigie-decp-")
    destination = cache if partage else Path(dossier_temporaire) / nom

    try:
        dernier = None
        for essai in range(1, DECP_ESSAIS + 1):
            req = urllib.request.Request(url, headers=HEADERS)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    if copier_atomiquement(destination, resp):
                        yield destination
                        return
                    raise OSError(f"écriture impossible dans {destination.parent}")
            except Exception as e:
                dernier = e
                # Un 404 ou un 403 ne s'améliorera pas en attendant : seules les
                # coupures et les erreurs serveur méritent un nouvel essai.
                if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                    break
                if essai < DECP_ESSAIS:
                    attente = 5 * essai
                    print(f"\n    essai {essai}/{DECP_ESSAIS} échoué ({e}), "
                          f"nouvelle tentative dans {attente}s…", end=" ", flush=True)
                    time.sleep(attente)

        # Source injoignable : la même règle bornée que ci-dessus, et le
        # fichier est déclaré périmé dans le rapport. Au-delà, le step échoue.
        if perime_lisible(cache, DECP_CACHE_JOURS):
            print(f"\n    [réseau KO] reprise du cache local ({nom})", end=" ", flush=True)
            yield cache
            return

        print(f"\n  [erreur téléchargement] {url[:60]} → {dernier}")
        _echecs_reseau.append(f"{url[:80]} → {dernier}")
        yield None
    finally:
        if dossier_temporaire:
            shutil.rmtree(dossier_temporaire, ignore_errors=True)


def _ouvrir_decp(chemin: Path):
    """Ouvre un consolidé en flux, qu'il soit gzippé ou non."""
    with chemin.open("rb") as sonde:
        gzippe = sonde.read(2) == b"\x1f\x8b"
    return gzip.open(chemin, "rb") if gzippe else chemin.open("rb")


def _chemin_des_marches(chemin: Path) -> str:
    """Les consolidés ont DEUX formes, et le préfixe ijson en dépend.

        {"marches": [ … ]}                → marches.item      (2019)
        {"marches": {"marche": [ … ]}}    → marches.marche.item (2024, 2025)

    Sondé sur les premiers kilo-octets : la forme se lit à l'ouverture, il n'y a
    aucune raison de parcourir le fichier deux fois pour la découvrir.
    """
    with _ouvrir_decp(chemin) as flux:
        debut = flux.read(4096).decode("utf-8", errors="replace")
    apres = debut.split('"marches"', 1)[-1].lstrip()
    if apres.startswith(":"):
        apres = apres[1:].lstrip()
    return "marches.item" if apres.startswith("[") else "marches.marche.item"


def dans_le_perimetre(acheteur_id: str, acheteur_nom: str) -> bool:
    """Le marché d'un acheteur du périmètre : la commune, l'EPCI (par SIREN), ou
    un acheteur dont le nom (en minuscules) porte un jeton du territoire. La
    même règle sert la lecture en flux et l'index du magasin."""
    # Un SIREN vide (commune isolée, sans EPCI déclaré) ne doit rien ouvrir :
    # `startswith("")` faisait entrer la France entière dans le périmètre.
    return (any(acheteur_id.startswith(s) for s in sirens_acheteur() if s)
            or (bool(CAC_SIREN) and acheteur_id.startswith(CAC_SIREN))
            or any(j and j in acheteur_nom for j in ACHETEUR_JETONS))


def extract_marches_from_file(url: str, nom: str = "",
                              annees: Counter | None = None) -> list[dict]:
    """Filtre un consolidé DECP sur le périmètre, marché par marché.

    Le fichier n'est jamais monté en mémoire. `json.loads` sur le consolidé 2019
    demandait 5,3 Go — 5,6 fois la taille du fichier — et faisait de ce step le
    poste le plus gourmand de toute une collecte, loin devant le build du site.
    Le parcours incrémental retient les quelques marchés du périmètre et laisse
    filer les 803 000 autres.

    `annees`, s'il est donné, compte les années de notification de TOUS les
    marchés du fichier, pour dire quels millésimes il couvre.
    """
    import ijson

    # Le nom de cache vient du titre de la ressource (decp-2025-03.json), pas de
    # l'URL : data.gouv sert ces fichiers derrière un identifiant opaque.
    with _consolide_decp(url, _nom_cache(nom or url)) as chemin:
        if chemin is None:
            return []

        found = []
        try:
            prefixe = _chemin_des_marches(chemin)
            with _ouvrir_decp(chemin) as flux:
                # use_float : sans lui ijson rend des Decimal, que ni SQLite ni
                # json.dumps ne savent écrire — les montants tomberaient plus loin.
                for m in ijson.items(flux, prefixe, use_float=True):
                    if not isinstance(m, dict):
                        continue
                    if annees is not None and (a := annee_notification(m.get("dateNotification"))):
                        annees[a] += 1
                    acheteur = m.get("acheteur") or {}
                    if not isinstance(acheteur, dict):
                        continue
                    if dans_le_perimetre(str(acheteur.get("id", "")),
                                         str(acheteur.get("nom", "")).lower()):
                        found.append(m)
        except Exception as e:
            # Un consolidé qui ne se lit pas jusqu'au bout est une source
            # MANQUÉE, pas une source vide : le step doit finir en erreur. Et le
            # fichier ne doit pas rester « frais » sept jours — retiré du
            # magasin, la passe suivante le reprend à la source. Les marchés lus
            # avant la coupure sont gardés, comme pour tout échec partiel.
            print(f"  [erreur lecture DECP] {e}")
            _echecs_reseau.append(f"{nom or url[:80]} illisible → {str(e).split(chr(10))[0]}")
            if chemin.parent == DECP_CACHE:
                try:
                    chemin.unlink(missing_ok=True)
                    print(f"    [magasin] {chemin.name} retiré : repris à la source à la prochaine passe")
                except OSError:
                    pass

    return found


# ── Index des consolidés par acheteur ────────────────────────────────────────
#
# Chaque dossier relisait les 5,2 Go de consolidés pour en garder une poignée
# de marchés : ~2 min de calcul, le seul vrai poste CPU d'un dossier (audit du
# 30/10/2026, P2). L'écrivain du magasin les indexe une fois par nuit
# (`remplir_magasin`) ; le lecteur interroge l'index, fichier par fichier, et
# relit le consolidé en flux dès que l'index ne lui correspond plus.

# Ce que portent les lignes de l'index : la sortie de `normalize_decp_marche`.
# À RELEVER dès que cette fonction change : un lecteur refuse tout autre numéro
# et relit les consolidés, plutôt que de publier des marchés à l'ancien format.
DECP_INDEX_SCHEMA = 2
DECP_INDEX_NOM = "index-acheteurs.sqlite"
# L'écrivain et les lecteurs ne tournent pas toujours sur la même révision du
# moteur : l'index porte l'empreinte de `normalize_decp_marche` sur un marché
# type, et un lecteur dont la normalisation diffère relit les consolidés.
# Relever le schéma à la main ne suffisait pas : un oubli publiait l'ancienne.
_MARCHE_TYPE = {
    "id": "type", "acheteur": {"id": "acheteur-type", "nom": "Acheteur"},
    "objet": "objet", "nature": "Marché", "procedure": "Procédure adaptée", "montant": 1.5,
    "dateNotification": "2026-01-01", "datePublicationDonnees": "2026-01-02",
    "dureeMois": 12, "codeCPV": "45233141-9", "lieuExecution": {"nom": "Lieu"},
    "titulaires": [{"id": "titulaire-type", "denominationSociale": "Titulaire",
                    "typeIdentifiant": "SIRET"}],
}


def empreinte_normalisation() -> str:
    sortie = json.dumps(normalize_decp_marche(_MARCHE_TYPE), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sortie.encode()).hexdigest()[:16]


# Un marché normalisé tient en quelques Ko : au-delà, le blob n'est pas un marché.
_MARCHE_OCTETS_MAX = 1 << 20
_TABLES_INDEX = {("table", "meta"), ("table", "fichiers"), ("table", "marches"),
                 ("table", "donnees"), ("index", "marches_par_fichier"),
                 ("index", "sqlite_autoindex_meta_1"), ("index", "sqlite_autoindex_fichiers_1"),
                 ("table", "millesimes"), ("index", "sqlite_autoindex_millesimes_1")}
_SCHEMA_INDEX_SQL = """
CREATE TABLE meta (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
-- Le consolidé tel qu'il a été indexé : un lecteur ne se sert de l'index pour
-- un fichier que si celui du magasin a toujours cette taille et cette date.
CREATE TABLE fichiers (nom TEXT PRIMARY KEY, taille INTEGER NOT NULL,
                       mtime_ns INTEGER NOT NULL, marches INTEGER NOT NULL);
-- Les colonnes du filtre, étroites : le parcours d'un fichier ne lit pas les
-- marchés eux-mêmes, rangés à part.
CREATE TABLE marches (id INTEGER PRIMARY KEY, fichier TEXT NOT NULL, rang INTEGER NOT NULL,
                      acheteur_id TEXT NOT NULL, acheteur_nom TEXT NOT NULL);
-- Le marché normalisé, en JSON compressé ; vide si la normalisation a échoué :
-- le lecteur qui le rencontre relit alors le consolidé, comme avant.
CREATE TABLE donnees (id INTEGER PRIMARY KEY, marche BLOB NOT NULL);
-- Les années de notification de TOUS les marchés du consolidé (schéma 2) : le
-- titre du fichier ne dit pas les millésimes qu'il couvre.
CREATE TABLE millesimes (fichier TEXT NOT NULL, millesime INTEGER NOT NULL,
                         marches INTEGER NOT NULL, PRIMARY KEY (fichier, millesime));
CREATE INDEX marches_par_fichier ON marches (fichier, rang);
"""
_index_ouvert: dict[Path, sqlite3.Connection | None] = {}


def _index_decp() -> sqlite3.Connection | None:
    """L'index du magasin en lecture seule, ou None : absent, d'un autre schéma
    ou d'une autre normalisation, d'une forme inattendue, ou qui n'est pas
    celui de l'écrivain du magasin. Ouvert une fois par processus."""
    chemin = DECP_CACHE / DECP_INDEX_NOM
    if chemin in _index_ouvert:
        return _index_ouvert[chemin]
    base = None
    try:
        info = chemin.stat()
        # Le fichier de l'écrivain (propriétaire du dossier), que personne
        # d'autre ne peut réécrire : un index déposé par un lecteur dans un
        # dossier resté inscriptible ne se lit pas.
        if info.st_uid != DECP_CACHE.stat().st_uid or info.st_mode & 0o022:
            print(f"  [DECP] index d'un autre propriétaire ou inscriptible : ignoré")
            raise FileNotFoundError
        # immutable : le magasin est monté en lecture seule chez le lecteur,
        # SQLite n'y pose ni verrou ni journal ; l'écrivain remplace le
        # fichier par renommage, jamais sur place.
        base = sqlite3.connect(chemin.as_uri() + "?mode=ro&immutable=1", uri=True)
        base.execute("PRAGMA trusted_schema = OFF")
        base.execute("PRAGMA cell_size_check = ON")
        if set(base.execute("SELECT type, name FROM sqlite_master")) != _TABLES_INDEX:
            raise sqlite3.DatabaseError("tables inattendues")
        meta = dict(base.execute("SELECT cle, valeur FROM meta"))
        if (meta.get("schema"), meta.get("normalisation")) != (
                str(DECP_INDEX_SCHEMA), empreinte_normalisation()):
            print(f"  [DECP] index d'un autre schéma ou d'une autre normalisation : ignoré")
            raise FileNotFoundError
        base.create_function("perimetre", 2,
                             lambda i, n: dans_le_perimetre(str(i), str(n)), deterministic=True)
    except (FileNotFoundError, sqlite3.Error) as e:
        if isinstance(e, sqlite3.Error):
            print(f"  [DECP] index illisible, ignoré : {e}")
        if base is not None:
            base.close()
        base = None
    _index_ouvert[chemin] = base
    return base


def _decoder(blob: bytes) -> dict | None:
    """Un marché de l'index, ou None : décompression bornée, un dict attendu."""
    if not isinstance(blob, bytes) or not blob:
        return None
    flux = zlib.decompressobj()
    brut = flux.decompress(blob, _MARCHE_OCTETS_MAX)
    if flux.unconsumed_tail:
        return None
    marche = json.loads(brut)
    return marche if isinstance(marche, dict) else None


def marches_indexes(nom: str, annees: Counter | None = None) -> list[dict] | None:
    """Les marchés du périmètre dans le consolidé `nom`, lus dans l'index ;
    None quand il faut relire le consolidé : chez l'écrivain, sans index, quand
    le consolidé du magasin n'est plus celui qui a été indexé, ou sur la
    moindre anomalie de l'index (qui ne doit jamais faire échouer le step).
    `annees` reçoit les années de notification relevées à l'indexation."""
    if ecrivain():
        return None
    cache = DECP_CACHE / nom
    # La fraîcheur du consolidé décide, comme pour la lecture en flux ; un
    # périmé borné se lit dans l'index aussi bien, et se déclare de même.
    if not (est_frais(cache, DECP_CACHE_JOURS)
            or (not magasin_ecrivable(DECP_CACHE, nom) and perime_lisible(cache, DECP_CACHE_JOURS))):
        return None
    base = _index_decp()
    if base is None:
        return None
    try:
        info = cache.stat()
        if base.execute("SELECT taille, mtime_ns FROM fichiers WHERE nom = ?",
                        (nom,)).fetchone() != (info.st_size, info.st_mtime_ns):
            return None
        lignes = base.execute(
            "SELECT d.marche FROM marches m JOIN donnees d ON d.id = m.id"
            " WHERE m.fichier = ? AND perimetre(m.acheteur_id, m.acheteur_nom) ORDER BY m.rang",
            (nom,)).fetchall()
        trouves = [_decoder(marche) for (marche,) in lignes]
        releve = base.execute("SELECT millesime, marches FROM millesimes WHERE fichier = ?",
                              (nom,)).fetchall()
    except (sqlite3.Error, zlib.error, ValueError, OSError) as e:
        print(f"  [DECP] index illisible pour {nom}, relu en flux : {e}")
        return None
    # Le filtre a porté sur les colonnes, la publication porte sur les blobs :
    # chaque marché rendu est de nouveau éprouvé contre le périmètre.
    if any(m is None or not dans_le_perimetre(str(m.get("acheteur_id", "")),
                                              str(m.get("acheteur_nom", "")).lower())
           for m in trouves):
        return None
    if annees is not None:
        annees.update(dict(releve))
    return trouves


def remplir_magasin(departements: list[str] | None = None) -> list[str]:
    """Pour l'écrivain du magasin : l'index des consolidés DECP présents au
    magasin, construit à côté (`.*.part`) puis publié par renommage.

    Joué APRÈS le step `marches`, qui vient de rafraîchir les consolidés. Un
    consolidé illisible n'entre pas dans l'index (ses lecteurs le relisent en
    flux) et revient en échec. `departements` est ignoré.
    """
    import ijson

    echecs: list[str] = []
    fichiers = sorted(p for p in DECP_CACHE.glob("*.json") if p.is_file() and not p.is_symlink())
    if not fichiers:
        print("  [DECP] aucun consolidé au magasin : pas d'index")
        return ["aucun consolidé"]
    final = DECP_CACHE / DECP_INDEX_NOM
    temporaire = DECP_CACHE / f".{DECP_INDEX_NOM}.{os.getpid()}.part"
    temporaire.unlink(missing_ok=True)
    total = 0
    try:
        base = sqlite3.connect(temporaire)
        # MEMORY et non OFF : un consolidé qui casse en cours de lecture est
        # annulé ; sans journal, le ROLLBACK est indéfini et laissait des pages
        # à moitié écrites, puis une base « malformed » pour toute la nuit.
        base.execute("PRAGMA journal_mode = MEMORY")
        base.execute("PRAGMA synchronous = OFF")
        base.executescript(_SCHEMA_INDEX_SQL)
        identifiant = 0
        for chemin in fichiers:
            info = chemin.stat()
            lignes, rang, rates, annees = [], 0, 0, Counter()
            try:
                with _ouvrir_decp(chemin) as flux:
                    for m in ijson.items(flux, _chemin_des_marches(chemin), use_float=True):
                        if not isinstance(m, dict) or not isinstance(m.get("acheteur"), dict):
                            continue
                        acheteur = m["acheteur"]
                        if a := annee_notification(m.get("dateNotification")):
                            annees[a] += 1
                        try:
                            marche = zlib.compress(json.dumps(
                                normalize_decp_marche(m), ensure_ascii=False).encode(), 6)
                        except Exception:
                            marche, rates = b"", rates + 1
                        identifiant += 1
                        rang += 1
                        lignes.append((identifiant, chemin.name, rang, str(acheteur.get("id", "")),
                                       str(acheteur.get("nom", "")).lower(), marche))
                        if len(lignes) >= 20_000:
                            _inserer_index(base, lignes)
                            lignes = []
                _inserer_index(base, lignes)
                base.execute("INSERT INTO fichiers VALUES (?, ?, ?, ?)",
                             (chemin.name, info.st_size, info.st_mtime_ns, rang))
                base.executemany("INSERT INTO millesimes VALUES (?, ?, ?)",
                                 [(chemin.name, a, n) for a, n in sorted(annees.items())])
                base.commit()
                total += rang
                print(f"  [DECP] index : {chemin.name} — {rang} marchés"
                      + (f", {rates} non normalisés (relus en flux)" if rates else ""))
            except Exception as e:
                base.rollback()
                print(f"  ✗ [DECP] {chemin.name} non indexé : {type(e).__name__}: {e}")
                echecs.append(chemin.name)
        base.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("schema", str(DECP_INDEX_SCHEMA)),
            ("normalisation", empreinte_normalisation()),
            ("ecritLe", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        ])
        base.commit()
        base.close()
        if total == 0:
            return echecs + ["index vide"]
        publier_atomiquement(final, temporaire)
    finally:
        temporaire.unlink(missing_ok=True)
    print(f"  [DECP] index publié : {total} marchés, {len(fichiers) - len(echecs)} consolidés")
    return echecs


def _inserer_index(base: sqlite3.Connection, lignes: list[tuple]) -> None:
    base.executemany("INSERT INTO marches VALUES (?, ?, ?, ?, ?)", [l[:5] for l in lignes])
    base.executemany("INSERT INTO donnees VALUES (?, ?)", [(l[0], l[5]) for l in lignes])


def normalize_decp_marche(m: dict) -> dict:
    """Normalise un marché DECP vers notre format interne."""
    acheteur = m.get("acheteur", {})
    titulaires = m.get("titulaires", [])
    cpv = str(m.get("codeCPV", ""))[:2]

    return {
        "source":         SOURCE_DECP_CONSOLIDE,
        "source_type":    "decp",
        "acheteur_id":    str(acheteur.get("id", "")),
        "acheteur_nom":   str(acheteur.get("nom", "")),
        "objet":          str(m.get("objet", "")),
        "nature":         str(m.get("nature", "Marché")),
        "procedure":      str(m.get("procedure", "")),
        "montant":        m.get("montant"),
        "devise":         m.get("devise", "EUR"),
        "date_notif":     str(m.get("dateNotification", "")),
        "date_pub":       str(m.get("datePublicationDonnees", "")),
        "duree_mois":     m.get("dureeMois"),
        "cpv":            str(m.get("codeCPV", "")),
        "cpv_label":      CPV_LABELS.get(cpv, ""),
        "lieu_exec":      str(m.get("lieuExecution", {}).get("nom", "")),
        "titulaires":     [
            {
                "siren": str(t.get("id", ""))[:9],
                "siret": str(t.get("id", "")),
                "nom":   str(t.get("denominationSociale", "")),
                "type":  str(t.get("typeIdentifiant", "")),
            }
            for t in titulaires
        ],
        "raw_id":         str(m.get("id", "")),
    }


# Les millésimes couverts par au moins un consolidé DECP lu en entier pendant la
# passe, avec le nombre de ces fichiers. Un zéro marché d'une année qu'aucun
# consolidé ne couvre ne dit rien de l'acheteur : la page doit pouvoir le dire.
# Le millésime se lit dans les dates de notification, jamais dans le titre
# (`millesimes_couverts`).
millesimes_lus: dict[int, int] = {}


def enregistrer_millesimes(conn, lus: dict[int, int]) -> None:
    """Remplace le relevé des millésimes DECP lus. Appelé seulement après une
    passe sur TOUS les consolidés : une passe bornée (`--years`) le fausserait."""
    conn.execute("""CREATE TABLE IF NOT EXISTS decp_millesimes (
                        millesime INTEGER PRIMARY KEY,
                        fichiers  INTEGER NOT NULL,
                        lu_le     TEXT DEFAULT (datetime('now')))""")
    conn.execute("DELETE FROM decp_millesimes")
    conn.executemany("INSERT INTO decp_millesimes (millesime, fichiers) VALUES (?, ?)",
                     sorted(lus.items()))


def fetch_decp_marches(years: list[int] | None = None) -> list[dict]:
    """Interroge le DECP et retourne tous les marchés du périmètre."""
    print("\n[DECP] Découverte des fichiers…")
    resources = discover_decp_resources()

    all_marches = []
    millesimes_lus.clear()
    for i, res in enumerate(resources):
        title = res["title"]
        print(f"  [{i+1}/{len(resources)}] {title}…", end=" ", flush=True)
        annees = Counter()
        indexes = marches_indexes(_nom_cache(title), annees)
        if indexes is not None:
            print(f"{len(indexes)} trouvé(s) (index)")
            all_marches.extend(indexes)
        else:
            echecs_avant = len(_echecs_reseau)
            marches = extract_marches_from_file(res["url"], title, annees)
            print(f"{len(marches)} trouvé(s)")
            all_marches.extend([normalize_decp_marche(m) for m in marches])
            time.sleep(REQUEST_DELAY)
            # Lu jusqu'au bout seulement : un fichier injoignable ou coupé ne
            # couvre aucun millésime.
            if len(_echecs_reseau) != echecs_avant:
                continue
        for a in millesimes_couverts(annees):
            millesimes_lus[a] = millesimes_lus.get(a, 0) + 1

    # `--years` borne les marchés à leur année de NOTIFICATION. Il bornait les
    # fichiers à l'année de leur titre, et `--years 2021` n'en lisait aucun :
    # les marchés de 2021 sont dans `decp-2019.json`.
    if years:
        all_marches = [m for m in all_marches if annee_notification(m.get("date_notif")) in years]
        print(f"  Filtré aux notifications de {years}")

    # Dédoublonner par raw_id
    seen_ids = set()
    unique = []
    for m in all_marches:
        if m["raw_id"] not in seen_ids:
            seen_ids.add(m["raw_id"])
            unique.append(m)

    print(f"  [DECP] {len(unique)} marchés uniques sur le périmètre")
    return unique


# ── Source 1b : DECP augmenté (Opendatasoft data.economie.gouv.fr) ────────────
# Requête ciblée et directe par SIREN acheteur (pas de téléchargement de fichiers
# consolidés). Plus fiable pour les marchés récents. Champs déjà aplatis +
# titulaire résolu (dénomination + SIRET). Complète fetch_decp_marches().

DECP_ECO_API = ("https://data.economie.gouv.fr/api/explore/v2.1/"
                "catalog/datasets/decp_augmente/records")

# DECP v3 — jeu vivant, mis à jour quotidiennement. `decp_augmente` est figé
# depuis le 05/03/2026 : conservé en repli pour l'historique, mais il ne verra
# plus jamais un marché récent. Vérifié le 26/07/2026 :
#   decp-v3-marches-valides  702 901 enreg., maj du jour
#   decp_augmente            994 123 enreg., maj 2026-03-05
DECP_V3_API = ("https://data.economie.gouv.fr/api/explore/v2.1/"
               "catalog/datasets/decp-v3-marches-valides/records")


def normalize_decp_v3(rec: dict) -> dict:
    """Normalise un record decp-v3-marches-valides vers notre format interne.

    Le schéma v3 diffère de `decp_augmente` : `acheteur_id` au lieu de
    `idacheteur`, `objet` au lieu de `objetmarche`, titulaires numérotés
    `titulaire_id_N` / `titulaire_denominationsociale_N`.
    """
    titulaires = []
    for i in (1, 2, 3):
        tid = str(rec.get(f"titulaire_id_{i}") or "").strip()
        nom = str(rec.get(f"titulaire_denominationsociale_{i}") or "").strip()
        if not tid and not nom:
            continue
        titulaires.append({
            "siren": tid[:9], "siret": tid, "nom": nom,
            "type": str(rec.get(f"titulaire_typeidentifiant_{i}") or "SIRET"),
        })
    division = str(rec.get("codecpv") or "")[:2]
    return {
        "source":       SOURCE_DECP_V3,
        "source_type":  "decp",
        "acheteur_id":  str(rec.get("acheteur_id") or ""),
        "acheteur_nom": str(rec.get("acheteur_nom") or ""),
        "objet":        str(rec.get("objet") or ""),
        "nature":       str(rec.get("nature") or "Marché"),
        "procedure":    str(rec.get("procedure") or ""),
        "montant":      rec.get("montant"),
        "date_notif":   str(rec.get("datenotification") or ""),
        "date_pub":     str(rec.get("datepublicationdonnees") or ""),
        "duree_mois":   rec.get("dureemois"),
        "cpv":          str(rec.get("codecpv") or ""),
        "cpv_label":    CPV_LABELS.get(division, ""),
        "lieu_exec":    str(rec.get("lieuexecution_nom") or ""),
        "titulaires":   titulaires,
        "raw_id":       str(rec.get("id") or ""),
    }


def fetch_decp_v3_marches() -> list[dict]:
    """DECP v3 : marchés dont l'ACHETEUR est la commune ou l'EPCI, plus ceux
    dont le LIEU D'EXÉCUTION tombe dans le périmètre — un marché du Département ou
    de la Région exécuté sur la commune intéresse autant le lecteur.

    Attention au lieu d'exécution : `lieuexecution_code` vaut tantôt un code
    INSEE, tantôt un code postal, et les deux jeux se recouvrent d'un
    département à l'autre. D'où le filtre conjoint sur `lieuexecution_typecode`,
    sans lequel on ramène des marchés étrangers au territoire.
    """
    import urllib.parse
    from .config import CODE_POSTAL, COMMUNES, COMMUNES_CP

    insee = ", ".join(f'"{c}"' for c in COMMUNES)
    # Le code postal ne sert qu'en repli du code commune, et il déborde
    # toujours : on ne retient que ceux dont la commune principale est dans le
    # périmètre — ici le CP de la commune elle-même.
    cp = ", ".join(f'"{c}"' for c in COMMUNES_CP if c == CODE_POSTAL)

    clauses = [f'startswith(acheteur_id, "{siren}")'
               for siren in (*sirens_acheteur(), CAC_SIREN) if siren]
    clauses.append(f'(lieuexecution_typecode = "Code commune" and lieuexecution_code in ({insee}))')
    if cp:
        clauses.append(f'(lieuexecution_typecode = "Code postal" and lieuexecution_code in ({cp}))')

    print("\n[DECP-v3] Requête par acheteur et par lieu d'exécution…")
    results, seen = [], set()
    lus, forme = 0, None
    for clause in clauses:
        offset = 0
        while True:
            params = urllib.parse.urlencode({
                "where": clause, "limit": 100, "offset": offset,
                "order_by": "datenotification DESC",
            })
            data = fetch_json(f"{DECP_V3_API}?{params}", timeout=30)
            if not data:
                break
            hits = data.get("results", [])
            lus += len(hits)
            forme = forme or (mesure.forme_de(hits))
            for rec in hits:
                rid = str(rec.get("id") or "")
                if rid and rid in seen:
                    continue
                seen.add(rid)
                results.append(normalize_decp_v3(rec))
            offset += len(hits)
            if not hits or offset >= data.get("total_count", 0) or offset >= 500:
                break
            time.sleep(REQUEST_DELAY)
    print(f"  [DECP-v3] {len(results)} marchés uniques")
    # Avant la déduplication ; plafond de 500 par clause, donc stable.
    mesure.lu("decp-v3", lus, forme, complete=True)
    return results


def _decp_eco_titulaires(rec: dict) -> list[dict]:
    """Construit la liste des titulaires (principal + co-titulaires) d'un record."""
    titulaires = []
    nom = (rec.get("denominationsocialeetablissement")
           or rec.get("denominationunitelegale") or "").strip()
    siret = str(rec.get("siretetablissement") or "").strip()
    if nom:
        titulaires.append({
            "siren": siret[:9], "siret": siret, "nom": nom,
            "type": str(rec.get("typeidentifiantetablissement") or "SIRET"),
        })
    for i in (1, 2, 3):
        co_nom = (rec.get(f"denominationsociale_cotitulaire{i}") or "").strip()
        if co_nom:
            co_id = str(rec.get(f"id_cotitulaire{i}") or "").strip()
            titulaires.append({
                "siren": co_id[:9], "siret": co_id, "nom": co_nom,
                "type": str(rec.get(f"typeidentifiant_cotitulaire{i}") or ""),
            })
    return titulaires


def normalize_decp_eco(rec: dict) -> dict:
    """Normalise un record decp_augmente vers notre format interne."""
    division = str(rec.get("codecpv_division") or "")[:2]
    cpv_label = (rec.get("referencecpv") or CPV_LABELS.get(division, "") or "")
    return {
        "source":      SOURCE_DECP_ECO,
        "source_type": "decp",
        "acheteur_id": str(rec.get("idacheteur") or ""),
        "acheteur_nom": str(rec.get("nomacheteur") or ""),
        "objet":       str(rec.get("objetmarche") or ""),
        "nature":      str(rec.get("nature") or rec.get("natureobjetmarche") or "Marché"),
        "procedure":   str(rec.get("procedure") or ""),
        "montant":     rec.get("montant"),
        "date_notif":  str(rec.get("datenotification") or ""),
        "date_pub":    str(rec.get("datepublicationdonnees") or ""),
        "duree_mois":  rec.get("dureemois"),
        "cpv":         str(rec.get("codecpv") or ""),
        "cpv_label":   cpv_label,
        "lieu_exec":   str(rec.get("lieuexecutionnom") or ""),
        "titulaires":  _decp_eco_titulaires(rec),
        "raw_id":      str(rec.get("id") or ""),
    }


def fetch_decp_eco_marches() -> list[dict]:
    """Interroge le DECP augmenté Opendatasoft par SIREN acheteur (commune + EPCI)."""
    import urllib.parse
    print("\n[DECP-eco] Requête directe par SIREN acheteur…")
    results, seen = [], set()
    lus, forme = 0, None
    for label, siren in (*((COMMUNE_NAME, s) for s in COMMUNE_SIRENS),
                         *((d["nom"], s) for s, d in COMMUNES_ABSORBEES.items()),
                         (EPCI_NOM, CAC_SIREN)):
        if not siren:       # `startswith("")` rendrait la France entière
            continue
        offset = 0
        while True:
            params = urllib.parse.urlencode({
                # ODSQL : le wildcard '%' ne fonctionne pas sur idacheteur ;
                # startswith() matche le SIREN (préfixe du SIRET acheteur).
                "where": f'startswith(idacheteur, "{siren}")',
                "limit": 100, "offset": offset,
                "order_by": "datenotification DESC",
            })
            data = fetch_json(f"{DECP_ECO_API}?{params}", timeout=30)
            if not data:
                break
            hits = data.get("results", [])
            lus += len(hits)
            forme = forme or (mesure.forme_de(hits))
            for rec in hits:
                rid = str(rec.get("id") or "")
                if rid and rid in seen:
                    continue
                seen.add(rid)
                results.append(normalize_decp_eco(rec))
            total = data.get("total_count", 0)
            offset += len(hits)
            if not hits or offset >= total or offset >= 500:
                break
            time.sleep(REQUEST_DELAY)
        print(f"  [DECP-eco] {label} ({siren}) : {offset} record(s) parcouru(s)")
    print(f"  [DECP-eco] {len(results)} marchés uniques")
    mesure.lu("decp-eco", lus, forme, complete=True)
    return results


# ── Source 2 : BOAMP ─────────────────────────────────────────────────────────

BOAMP_API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

def fetch_boamp_marches() -> list[dict]:
    """
    Interroge l'API BOAMP OpenDataSoft (sans clé) pour la commune et l'EPCI.
    Couvre les avis d'appel public à la concurrence (AAPC) et attributions.
    """
    import urllib.parse
    print(f"\n[BOAMP] Recherche marchés {COMMUNE_NAME} + {EPCI_NOM}…")
    results = []
    seen_ids = set()
    lus, forme = 0, None
    ecartes = Counter()

    # Le nom de l'acheteur est saisi librement par le publicateur : on interroge
    # sur les jetons du périmètre, TOUS bornés au département. Le nom de la
    # commune l'était depuis toujours — « Brassac » désigne aussi des communes
    # de l'Ariège, du Puy-de-Dôme et du Tarn-et-Garonne — mais pas les jetons
    # tirés du nom de l'EPCI, et c'est par là que la France entière entrait :
    # « coeur » ramène 3 668 avis sans borne, 23 avec (mesuré le 23/08/2026).
    #
    # La borne sert aussi à ce que la pagination garde un sens : chaque requête
    # s'arrête à 200 avis, les plus récents d'abord. Sur un jeton non borné, les
    # 200 plus récents de France peuvent ne contenir aucun avis du territoire.
    #
    # Elle ne suffit pas : `code_departement` est celui de l'AVIS, pas celui de
    # l'acheteur — une recherche bornée au 26 remonte le SYDEO du Pouzin (07).
    # D'où le recoupement sur l'adresse déclarée, plus bas.
    queries = [f'nomacheteur like "%{j}%" and code_departement="{DEPARTEMENT}"'
               for j in ACHETEUR_JETONS]

    for where in queries:
        offset = 0
        while True:
            params = urllib.parse.urlencode({
                "where": where,
                "limit": 50,
                "offset": offset,
                "order_by": "dateparution DESC",
            })
            data = fetch_json(f"{BOAMP_API}?{params}", timeout=30)
            if not data:
                break
            hits = data.get("results", [])
            if not hits:
                break
            lus += len(hits)
            forme = forme or mesure.forme_de(hits)
            for hit in hits:
                uid = hit.get("idweb") or hit.get("id") or str(offset)
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                objet = (hit.get("objet") or "").strip()
                if not objet:
                    continue
                date_pub = (hit.get("dateparution") or "")[:10]
                acheteur = hit.get("nomacheteur") or ""
                # ── Qui a passé ce marché ? ──────────────────────────────────
                # Les jetons servent à CHERCHER large, jamais à CONCLURE. Un
                # seul mot commun du nom de l'EPCI suffisait à déclarer que
                # l'intercommunalité était l'acheteur : « terres » attrapait les
                # « Terres australes françaises », « cévennes » le GHT Cévennes
                # Gard Camargue et le CH d'Alès. 711 marchés sur 712 se sont
                # retrouvés attribués à la communauté de communes, qui n'y était
                # pour rien. Affirmer qu'une collectivité a acheté quelque chose
                # demande mieux qu'une coïncidence de vocabulaire.
                #
                # On n'attribue donc que sur le nom COMPLET, normalisé. Le reste
                # entre en base en `probable` et attend l'atelier.
                # EN TÊTE, pas n'importe où. Un organisme tiers met son propre
                # nom devant : « Siaep de Lasalle » est le syndicat des eaux,
                # pas la mairie, et « Paris Terres d'Envol » est un EPCI de
                # Seine-Saint-Denis. Chercher la commune en sous-chaîne les
                # attribuait tous les deux à Lasalle.
                #
                # Un suffixe reste accepté — « CC Machin — service eau » est
                # bien la CC. Ce qui est refusé, c'est un PRÉFIXE étranger.
                # La règle vit dans `attribution_acheteur`, une seule fois :
                # elle était recopiée ici, dans le script de requalification et
                # dans son test, qui vérifiait donc sa propre copie.
                cible = attribution_acheteur(
                    acheteur, homonymes=tuple(c["nom"] for c in COMMUNES_ADRESSE.values()))
                reconnu = {"commune": COMMUNE_SIREN, "epci": CAC_SIREN}.get(cible, "")

                # Le recoupement : l'adresse que l'acheteur déclare lui-même.
                # C'est le seul rattachement au territoire que le BOAMP permette
                # — il ne publie pas de SIREN (cf. `localite_acheteur`).
                du_territoire = acheteur_du_territoire(localite_acheteur(hit))
                if du_territoire is False:
                    # Domicilié ailleurs. Le nom ne rachète pas l'adresse : un
                    # « Brassac » de l'Ariège porte bien le nom de la commune.
                    ecartes["hors_territoire"] += 1
                    continue
                if reconnu:
                    acheteur_id_str, certitude = reconnu, "verified"
                elif du_territoire:
                    # Nom inconnu, adresse dans le périmètre : un syndicat, un
                    # CCAS, une régie. Il garde son nom déclaré, entre en
                    # `probable`, et l'atelier décide — y compris d'en faire une
                    # entité à part entière, ce qu'un syndicat mérite.
                    acheteur_id_str, certitude = "", "probable"
                else:
                    # Ni le nom ni l'adresse ne le rattachent : il n'entre pas.
                    # Une file de revue pleine d'avis d'autres départements est
                    # une file que personne ne relit.
                    ecartes["sans_rattachement"] += 1
                    continue
                # Titulaire (peut être str ou list)
                titulaire_raw = hit.get("titulaire") or ""
                if isinstance(titulaire_raw, list):
                    titulaires = [{"nom": str(t), "siren": "", "siret": "", "type": ""} for t in titulaire_raw if t]
                elif titulaire_raw:
                    titulaires = [{"nom": str(titulaire_raw), "siren": "", "siret": "", "type": ""}]
                else:
                    titulaires = []
                results.append({
                    "source":      SOURCE_BOAMP,
                    "source_type": "boamp",
                    "confidence":  certitude,
                    "acheteur_id": acheteur_id_str,
                    "acheteur_nom": acheteur,
                    "objet":       objet,
                    "nature":      hit.get("nature_libelle") or hit.get("nature") or "Marché",
                    "procedure":   hit.get("procedure_libelle") or hit.get("type_procedure") or "",
                    "montant":     None,
                    "date_pub":    date_pub,
                    "date_notif":  "",
                    "pdf_url":     hit.get("url_avis") or f"https://www.boamp.fr/avis/detail/{uid}",
                    "titulaires":  titulaires,
                    "raw_id":      f"boamp_{uid}",
                    "cpv":         hit.get("descripteur_code") or "",
                    "cpv_label":   hit.get("descripteur_libelle") or "",
                    "lieu_exec":   "",
                })
            total = data.get("total_count", 0)
            offset += len(hits)
            if offset >= total or offset >= 200:
                break
            time.sleep(REQUEST_DELAY)

    print(f"  [BOAMP] {len(results)} avis retenus"
          + (f" — {ecartes['hors_territoire']} acheteurs hors territoire, "
             f"{ecartes['sans_rattachement']} sans rattachement, écartés"
             if ecartes else ""))
    # Avant le recoupement d'adresse : les avis rendus, pas les retenus.
    mesure.lu("boamp", lus, forme, complete=True)
    return results


# ── Source 3 : avis publiés par la collectivité ─────────────────────────────

def fetch_avis_site() -> list[dict]:
    """Avis de publicité publiés sur les sites officiels, via le connecteur.

    La version d'origine découpait le HTML d'un site précis à coups de
    `re.split('<article|<div class=post')` pour retrouver titre, date et PDF.
    Le connecteur rend ces trois champs ; il n'y a plus de structure de page à
    deviner (cf. collectors/connecteurs/).
    """
    from .connecteurs import charger

    avis = charger().avis_marches()
    # Zéro stable sans page « marchés » déclarée ; un site qui ne répond plus
    # rend zéro sans erreur. La forme est celle du connecteur : rien à en tirer.
    mesure.lu("site-avis", len(avis), None, complete=True)
    print(f"\n[site] {len(avis)} avis de publicité")
    return [{
        "source":       a["source"],
        "source_type":  "site_officiel",
        "acheteur_id":  CAC_SIREN if a.get("portee") == "epci" else COMMUNE_SIREN,
        "acheteur_nom": EPCI_NOM if a.get("portee") == "epci" else COMMUNE_NAME,
        "objet":        a["objet"],
        "nature":       "Avis de publicité",
        "procedure":    "Consultation",
        "montant":      None,
        "date_pub":     a.get("date_pub", ""),
        "date_notif":   "",
        "pdf_url":      a.get("pdf_url", ""),
        "titulaires":   [],
        "raw_id":       a.get("raw_id", ""),
    } for a in avis]


# ── Insertion DB ──────────────────────────────────────────────────────────────

def _get_commune_entity_id(conn) -> int:
    """Retourne l'ID de l'entité « Commune de … » (acheteur)."""
    row = conn.execute(
        "SELECT id FROM entities WHERE type='service' AND name=? LIMIT 1",
        (f"Commune de {COMMUNE_NAME}",)
    ).fetchone()
    if row:
        return row["id"]
    return upsert_entity(
        conn, type="service", name=f"Commune de {COMMUNE_NAME}",
        short_name=COMMUNE_NAME, commune=COMMUNE_NAME, confidence="verified"
    )


def _get_cac_entity_id(conn) -> int:
    """Retourne l'ID de l'EPCI (acheteur), cherché par son SIREN d'abord."""
    # Pas de nom court : « CCSVP » était écrit ici, le sigle de l'EPCI d'UNE
    # instance, posé sur la fiche de l'intercommunalité de toutes les autres.
    return upsert_entity(conn, type="service", name=EPCI_NOM, siren=CAC_SIREN,
                         confidence="verified")


def siren_de(valeur) -> str:
    """Les neuf chiffres d'un SIREN, ou "" : un SIRET donne son SIREN, une
    valeur qui n'en a pas neuf ne désigne personne."""
    chiffres = re.sub(r"\D", "", str(valeur or ""))
    return chiffres[:9] if len(chiffres) in (9, 14) else ""


def acheteur_par_siren(conn, siren: str) -> int | None:
    """La fiche de l'acheteur que désigne un SIREN, ou None s'il n'en a pas.

    Le SIREN est la seule clé qui ne dépend pas de l'orthographe : à Lasalle,
    la même communauté de communes s'écrivait de cinq façons dans
    `marches.json`, et seul l'identifiant les réunissait. La commune et
    l'intercommunalité se reconnaissent à celui que déclare l'instance ; un
    autre acheteur (syndicat, département) à celui de sa fiche, s'il en a une.

    Ce qui n'est pas reconnu reste SANS fiche — jamais rattaché par défaut à
    l'intercommunalité, comme le faisait `insert_marche` pour tout SIREN qui
    n'était pas celui de la commune.
    """
    s = siren_de(siren)
    if not s:
        return None
    if s in {siren_de(c) for c in COMMUNE_SIRENS}:
        return _get_commune_entity_id(conn)
    if CAC_SIREN and s == siren_de(CAC_SIREN):
        return _get_cac_entity_id(conn)
    r = conn.execute(
        "SELECT entity_id FROM businesses WHERE siren=? "
        "UNION ALL SELECT entity_id FROM associations WHERE siren=? LIMIT 1",
        (s, s)).fetchone()
    return r[0] if r else None


def _upsert_titulaire(conn, t: dict) -> int | None:
    """Crée ou retrouve l'entité prestataire."""
    nom = t.get("nom", "").strip()
    if not nom:
        return None
    eid = upsert_entity(
        conn, type="business", name=nom,
        confidence="verified"
    )
    # Enrichir le SIREN si disponible
    siren = t.get("siren", "").strip()
    if siren and len(siren) == 9:
        conn.execute(
            "INSERT OR IGNORE INTO businesses (entity_id, siren) VALUES (?,?)",
            (eid, siren)
        )
        conn.execute(
            "UPDATE businesses SET siren=? WHERE entity_id=? AND siren IS NULL",
            (siren, eid)
        )
    return eid


def marche_exists(conn, raw_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM marches_publics WHERE raw_id=? LIMIT 1",
        (raw_id[:100],)
    ).fetchone()
    return row is not None


# `_ensure_marches_table()` a été SUPPRIMÉE le 21/08/2026. Elle recréait ici
# `marches_publics` avec une copie du `CREATE TABLE`, sous la consigne de
# « rester alignée » sur `db/schema.sql`. Elle ne l'était plus : la copie avait
# perdu la contrainte
# `CHECK(confidence IN ('verified','confirmed','probable','hypothesis'))`
# ajoutée au schéma le 20/08/2026.
#
# Et surtout : **elle n'avait aucun appelant**. La table est créée par
# `init_db()` (step `init` de `run_all`), qui joue le schéma entier. La copie ne
# protégeait donc rien — elle ne faisait que porter une seconde définition,
# vouée à diverger, dans un fichier où personne n'allait la relire.
#
# Deux définitions d'une même table sont deux définitions qui divergeront : la
# consigne de les tenir alignées ne suffit pas, seule leur unicité suffit.
# Relevé par un audit externe le 21/08/2026.


def insert_marche(conn, m: dict,
                  commune_id: int, cac_id: int,
                  dry_run: bool = False) -> bool:
    """Insère un marché en DB : events + marches_publics + financial_flows."""
    objet  = m.get("objet", "").strip()
    if not objet:
        return False
    # Les sources qui identifient l'acheteur par son SIREN (DECP) n'ont pas de
    # doute à exprimer : elles ne posent pas la clé, et le marché vaut
    # `verified`. Seul le BOAMP, où le nom de l'acheteur est en saisie libre,
    # rend parfois `probable`.
    certitude = m.get("confidence") or "verified"

    raw_id = m.get("raw_id", "")[:100]
    if marche_exists(conn, raw_id):
        return False

    if dry_run:
        print(f"  [dry-run] {m['acheteur_nom'][:30]} | {objet[:60]} | {m.get('montant','?')} €")
        return False

    # `acheteur_label` est ce que la SOURCE déclare, conservé tel quel. Il
    # portait le nom de l'entité résolue, ce qui écrasait la seule trace de ce
    # qui avait été lu : impossible ensuite de vérifier une attribution, ni de
    # la reprendre. C'est ce qui a rendu les 712 marchés du BOAMP
    # irrécupérables le 20/08/2026 — tous déclaraient le même acheteur, les
    # justes comme les fausses. Un dispositif qui affiche la provenance de
    # chaque acte ne peut pas perdre l'énoncé de sa source.
    acheteur_label = (m.get("acheteur_nom") or "").strip()

    # L'entité n'est rattachée que si l'acheteur a été ÉTABLI. Le `else`
    # accrochait tout le reste à l'EPCI par défaut : un marché dont on ne savait
    # rien devenait un marché de l'intercommunalité.
    acheteur_id_str = m.get("acheteur_id", "")
    acheteur_eid = acheteur_par_siren(conn, acheteur_id_str)
    if acheteur_eid == commune_id:
        acheteur_label = acheteur_label or COMMUNE_NAME
    elif acheteur_eid == cac_id:
        acheteur_label = acheteur_label or EPCI_NOM
    else:
        acheteur_label = acheteur_label or "acheteur non établi"

    titulaires = m.get("titulaires", [])
    tit = titulaires[0] if titulaires else {}
    titulaire_nom   = tit.get("nom", "").strip() or None
    titulaire_siren = tit.get("siren", "").strip()[:9] or None

    # Retrouver ou créer le titulaire
    titulaire_eid = None
    if titulaires:
        for t in titulaires:
            titulaire_eid = _upsert_titulaire(conn, t)
            if titulaire_eid:
                break

    metadata = json.dumps({
        "raw_id":      raw_id,
        "procedure":   m.get("procedure", ""),
        "nature":      m.get("nature", ""),
        "cpv":         m.get("cpv", ""),
        "cpv_label":   m.get("cpv_label", ""),
        "lieu_exec":   m.get("lieu_exec", ""),
        "duree_mois":  m.get("duree_mois"),
        "acheteur_id": acheteur_id_str,
        "source_type": m.get("source_type", ""),
        "pdf_url":     m.get("pdf_url", ""),
        "titulaires":  titulaires,
    }, ensure_ascii=False)

    date       = m.get("date_notif") or m.get("date_pub") or None
    date_pub   = m.get("date_pub") or None
    source_url = m.get("pdf_url", "")
    montant_raw = m.get("montant")

    # Normaliser montant
    montant = None
    if montant_raw is not None:
        try:
            montant = float(str(montant_raw))
        except (ValueError, TypeError):
            pass

    # Normaliser cpv
    cpv_raw = m.get("cpv", "")
    cpv_str = (json.dumps(cpv_raw, ensure_ascii=False) if isinstance(cpv_raw, list)
               else str(cpv_raw) if cpv_raw else None)
    cpv_label = m.get("cpv_label", "")
    cpv_label_str = (json.dumps(cpv_label, ensure_ascii=False) if isinstance(cpv_label, list)
                     else str(cpv_label) if cpv_label else None)

    # ── Événement ────────────────────────────────────────────────────────────
    ev_id = conn.execute(
        "INSERT INTO events (type, date, title, source, source_url, metadata)"
        " VALUES ('marché_public', ?, ?, ?, ?, ?)",
        (date, objet, m["source"], source_url, metadata)
    ).lastrowid

    # Lier acheteur et titulaires aux événements. Pas de lien quand l'acheteur
    # n'est pas établi : un rattachement est une affirmation, et il ferait
    # apparaître le marché sur la fiche d'une collectivité qui n'y est pour rien.
    if acheteur_eid:
        conn.execute(
            "INSERT OR IGNORE INTO event_entities (event_id, entity_id, role) VALUES (?, ?, 'acheteur')",
            (ev_id, acheteur_eid)
        )
    if titulaire_eid:
        conn.execute(
            "INSERT OR IGNORE INTO event_entities (event_id, entity_id, role) VALUES (?, ?, 'prestataire')",
            (ev_id, titulaire_eid)
        )
        # « X est prestataire de Y » suppose que Y soit connu. Sans acheteur
        # établi il n'y a pas de relation à écrire — et `relations.to_id` est
        # NOT NULL, ce qui ferait échouer la collecte entière sur un seul
        # marché mal identifié.
        if acheteur_eid:
            conn.execute(
                "INSERT OR IGNORE INTO relations"
                " (from_id, to_id, relation_type, since, source, confidence, metadata)"
                " VALUES (?, ?, 'prestataire', ?, ?, ?, ?)",
                (titulaire_eid, acheteur_eid, date, m["source"], certitude,
                 json.dumps({"objet": objet[:80], "montant": montant}, ensure_ascii=False))
            )

    # ── Table dédiée marches_publics ─────────────────────────────────────────
    conn.execute("""
        INSERT OR IGNORE INTO marches_publics
          (acheteur_id, acheteur_siren, acheteur_nom,
           titulaire_id, titulaire_siren, titulaire_nom,
           objet, nature, procedure, montant,
           cpv, cpv_label, date_notif, date_pub, duree_mois,
           lieu_exec, source, source_url, raw_id, event_id, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        acheteur_eid, acheteur_id_str[:9] if acheteur_id_str else "", acheteur_label,
        titulaire_eid, titulaire_siren, titulaire_nom,
        objet, m.get("nature"), m.get("procedure"), montant,
        cpv_str, cpv_label_str, date, date_pub, m.get("duree_mois"),
        m.get("lieu_exec"), m["source"], source_url, raw_id, ev_id,
        certitude,
    ))

    # ── Flux financier ───────────────────────────────────────────────────────
    # Le flux hérite de la certitude du marché : un montant attribué à une
    # collectivité dont on n'est pas sûr qu'elle a acheté serait pire publié
    # que le marché lui-même — c'est de l'argent qu'on lui prête.
    if montant:
        year_str = (date or "")[:4]
        year = int(year_str) if year_str.isdigit() else None
        conn.execute(
            "INSERT INTO financial_flows"
            " (type, year, amount, from_id, to_id, event_id, description, source, confidence)"
            " VALUES ('marché', ?, ?, ?, ?, ?, ?, ?, ?)",
            (year, int(montant), acheteur_eid, titulaire_eid, ev_id,
             f"{objet[:80]} ({acheteur_label})", m["source"], certitude)
        )

    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    dry_run: bool = False,
    source: str = "all",
    years: list[int] | None = None,
) -> None:
    print(f"\n[marches_publics] dry_run={dry_run} source={source} years={years}")
    print(f"  Commune SIRET : {COMMUNE_SIRET}")
    print(f"  EPCI SIREN    : {CAC_SIREN} ({EPCI_NOM})")

    all_marches: list[dict] = []

    if source in ("all", "decp"):
        all_marches += fetch_decp_marches(years=years)

    if source in ("all", "decp", "decp-eco"):
        all_marches += fetch_decp_v3_marches()      # jeu vivant
        all_marches += fetch_decp_eco_marches()     # repli historique (figé 03/2026)

    if source in ("all", "boamp"):
        all_marches += fetch_boamp_marches()

    if source in ("all", "site"):
        all_marches += fetch_avis_site()

    print(f"\n  Total : {len(all_marches)} marchés à traiter")

    if dry_run:
        for m in all_marches:
            print(
                f"  [{m['source_type']}] {m['acheteur_nom'][:25]} | "
                f"{m.get('objet','')[:55]} | "
                f"{m.get('montant','?')} € | {m.get('date_pub','')[:10]}"
            )
        _relever_echecs_reseau()
        return

    inserted = 0
    with transaction() as conn:
        if source in ("all", "decp") and years is None and millesimes_lus:
            enregistrer_millesimes(conn, millesimes_lus)
        commune_id = _get_commune_entity_id(conn)
        cac_id     = _get_cac_entity_id(conn)
        for m in all_marches:
            if insert_marche(conn, m, commune_id, cac_id, dry_run=False):
                inserted += 1
                print(f"  [+] {m.get('objet','')[:60]}")

    print(f"\n[marches_publics] {inserted}/{len(all_marches)} insérés")
    if inserted == 0 and source in ("all", "decp"):
        print(
            "  Note : la publication au DECP n'est obligatoire qu'à partir de 40 000 € HT\n"
            "  (25 000 € HT sans publicité ni mise en concurrence, avec option de liste annuelle,\n"
            f"  art. R2196-1) : un marché plus petit de {COMMUNE_NAME} n'a pas à y figurer."
        )
    # Après le commit : les marchés lus sont gardés, mais le run est marqué
    # en erreur si une partie des sources n'a pas pu être lue.
    _relever_echecs_reseau()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Marchés publics — commune + EPCI")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--source",   default="all",
                        choices=["all", "decp", "decp-eco", "boamp", "site"])
    parser.add_argument("--years", type=str, default=None,
                        help="Années de NOTIFICATION à garder, ex: 2022-2025 ou 2024 "
                             "(tous les consolidés sont lus)")
    args = parser.parse_args()

    years = None
    if args.years:
        if "-" in args.years:
            start, end = args.years.split("-")
            years = list(range(int(start), int(end) + 1))
        else:
            years = [int(args.years)]

    main(dry_run=args.dry_run, source=args.source, years=years)
