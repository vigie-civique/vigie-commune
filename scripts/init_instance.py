#!/usr/bin/env python3
"""
init_instance.py — Amorce une instance à partir d'un code INSEE.

Écrit `config/instance.json` et adapte `config/publication_rules.json` en
interrogeant les référentiels nationaux :

  geo.api.gouv.fr                     commune, code postal, population, EPCI,
                                      communes membres, contour, centroïde
  recherche-entreprises.api.gouv.fr   SIREN et SIRET de la collectivité,
                                      SIREN de l'EPCI, anciennes communes
                                      encore inscrites au répertoire

Ce que le script NE devine pas, et qu'il laisse à renseigner :

  - l'adresse du site de la mairie et celle de l'intercommunalité. Aucun
    référentiel national ne les publie de façon fiable ; le script propose des
    candidats et vérifie lesquels répondent, mais c'est un humain qui tranche.
  - le connecteur à utiliser pour les lire (cf. collectors/connecteurs/).
  - le chemin des recueils de la préfecture, dont l'arborescence change d'un
    département à l'autre.

Ce partage est le résultat du premier portage vers une deuxième commune :
tout ce qui est national s'automatise, les sites officiels non.

Ce que le script n'écrit PAS, volontairement : la profondeur de collecte. Elle
est une règle du moteur — la commune en entier, l'intercommunalité comme
institution, ses communes membres en contexte — et une instance l'hérite sans
rien déclarer. L'écrire ici en ferait un particulier de commune, c'est-à-dire
une valeur qu'on oublie de reporter au portage suivant. On ne déclare
`collecte.fond` que pour s'en écarter : suivre plusieurs communes en propre, ou
descendre sur l'intercommunalité entière. Cf. `collectors/config.py`.

Usage :
  python3 scripts/init_instance.py 81037
  python3 scripts/init_instance.py 81037 --dry-run
  python3 scripts/init_instance.py 81037 --force     # écrase une instance
  python3 scripts/init_instance.py 81037 --regles    # rafraîchit les règles
                                                     # après avoir complété les
                                                     # adresses des sites
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# La racine du dépôt sur le chemin d'import. `python3 scripts/init_instance.py`
# met `scripts/` en tête de `sys.path` et JAMAIS la racine : l'import de
# `collectors.marches_publics`, plus bas, échouait donc sur un dépôt fraîchement
# cloné — c'est-à-dire dans le seul cas où ce script sert. Il ne marchait que
# dans un shell qui avait déjà la racine dans son chemin.
sys.path.insert(0, str(ROOT))
INSTANCE = ROOT / "config" / "instance.json"
REGLES = ROOT / "config" / "publication_rules.json"
REGLES_EXEMPLE = ROOT / "config" / "publication_rules.exemple.json"

GEO = "https://geo.api.gouv.fr"
ENTREPRISES = "https://recherche-entreprises.api.gouv.fr/search"
COG = "https://api.insee.fr/metadonnees/geo/commune"
UA = {"User-Agent": "VigieCivique/1.0 (amorçage d'instance, données publiques)"}


def _get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"  [erreur] {url} → {e}", file=sys.stderr)
        return None


def _repond(url: str) -> bool:
    """Le site répond-il ? Un candidat qui ne répond pas n'est pas proposé."""
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status < 400
    except Exception:
        return False


def _bbox(contour: dict) -> list[float]:
    """(sud, ouest, nord, est) depuis le contour communal, arrondi à l'extérieur."""
    coords = contour["coordinates"]
    points = []
    pile = list(coords)
    while pile:
        item = pile.pop()
        if isinstance(item[0], (int, float)):
            points.append(item)
        else:
            pile.extend(item)
    lats = [p[1] for p in points]
    lngs = [p[0] for p in points]
    marge = 0.01
    return [round(min(lats) - marge, 4), round(min(lngs) - marge, 4),
            round(max(lats) + marge, 4), round(max(lngs) + marge, 4)]


def _prefixe_siren(code: str) -> str:
    """Le SIREN d'une commune historique, sans sa clé finale.

    « 21 », puis le département sur deux chiffres, puis le numéro de commune
    sur quatre (complété d'un zéro). Vérifié sur trois communes de trois
    départements :

        30140 → 213001407      81037 → 218100378      26289 → 212602890

    La règle ne vaut pas pour la Corse ni l'outre-mer, dont les codes ne
    s'alignent pas ainsi : vide.
    """
    if not code[:2].isdigit() or code.startswith(("97", "98")):
        return ""
    return "21" + code[:2] + "0" + code[2:]


_PETITS_MOTS = {"de", "du", "des", "la", "le", "les", "sur", "sous", "en", "et",
                "aux", "au", "l", "d"}


def _nom_commune(nom_complet: str) -> str:
    """« COMMUNE DE SAINT-MARTIN-DE-LA-PLAINE » → « Saint-Martin-de-la-Plaine ».

    Le répertoire écrit en capitales sans accents : le nom obtenu est
    approché, et marqué « à confirmer » par l'appelant. Les collecteurs le
    comparent sous forme normalisée (sans accents ni casse), où il suffit.
    """
    nom = nom_complet.strip()
    for prefixe in ("COMMUNE DE ", "COMMUNE D'", "COMMUNE D’", "COMMUNE "):
        if nom.upper().startswith(prefixe):
            nom = nom[len(prefixe):]
            break
    morceaux = []
    for i, mot in enumerate(re.split(r"([ \-'’])", nom.lower())):
        if i and mot in _PETITS_MOTS:
            morceaux.append(mot)
        else:
            morceaux.append(mot[:1].upper() + mot[1:])
    return "".join(morceaux)


def _est_la_commune(r: dict, insee: str, nom: str) -> bool:
    """L'entité du répertoire est-elle la commune elle-même ?"""
    if (r.get("siege") or {}).get("commune") == insee:
        return True
    return _nom_commune(r.get("nom_complet", "")).lower() == nom.lower()


def _noms_cog(insee: str) -> dict[str, str]:
    """Code → nom de chaque commune qui a précédé celle-ci, au COG de l'Insee.

    geo.api ne connaît que les communes actuelles : il rend les `anciensCodes`,
    pas leur nom. Le COG les garde (« Véronne », « Le Margnès »), accents
    compris. Vide si l'API ne répond pas : rien n'est deviné.
    """
    data = _get(f"{COG}/{urllib.parse.quote(insee)}/precedents")
    if not isinstance(data, list):
        return {}
    return {d["code"]: d["intitule"] for d in data
            if isinstance(d, dict) and d.get("code") and d.get("intitule")}


def _siren_par_ancien_nom(code: str, nom_ancien: str) -> str:
    """Le SIREN d'une absorbée cherché sous SON nom, quand le répertoire ne la
    rend pas sous celui de la commune actuelle (Véronne, sortie seulement sous
    « commune de Véronne »).

    Seule une entité CESSÉE dont le SIREN porte le préfixe calculé du code est
    retenue, et seulement si elle est seule : sinon rien, jamais deviner.
    """
    prefixe = _prefixe_siren(code)
    if not prefixe:
        return ""
    q = urllib.parse.urlencode({"q": f"commune de {nom_ancien}"})
    data = _get(f"{ENTREPRISES}?{q}") or {}
    trouves = {r["siren"] for r in data.get("results", [])
               if r.get("etat_administratif") == "C"
               and r.get("siren", "").startswith(prefixe)
               and r.get("nom_complet", "").upper().startswith("COMMUNE")}
    return trouves.pop() if len(trouves) == 1 else ""


def _sirens(insee: str, nom: str, anciens_codes: list[str] | tuple = (),
            siren_geo: str = ""
            ) -> tuple[str, str, dict[str, str], list[str], dict[str, str]]:
    """SIREN et SIRET de la collectivité, noms des communes qu'elle a absorbées,
    ses anciens SIREN, et le SIREN de chaque absorbée d'un autre code.

    On calcule le SIREN attendu (`_prefixe_siren`) et on le cherche en
    priorité, parce que le répertoire peut contenir DEUX « COMMUNE DE X » :
    prendre le premier résultat, c'était fausser ensuite tout le filtrage des
    marchés publics par SIREN acheteur, sans qu'aucun contrôle ne s'en aperçoive.

    Une commune NOUVELLE (fusion) reçoit un SIREN en 200…, hors de la règle,
    et les communes fusionnées restent au répertoire, CESSÉES, avec leur siège
    réadressé dans la commune nouvelle. Quand la commune nouvelle a gardé le
    code INSEE d'une ancienne, le SIREN « attendu » est celui de l'ancienne :
    Vallouise-Pelvoux (05101) recevait celui de Pelvoux, cessée en 2017, et
    l'OFGL, les balances DGFiP et les marchés de la commune revenaient vides
    (relevé national du 01/11/2026). Une entité cessée n'est donc jamais
    retenue ; si une seule entité active reste, c'est la commune.

    Les cessées servent à deux choses :
      - leur nom est l'ancien nom que le JO des associations et BODACC écrivent
        encore. Rendu par code INSEE, celui de la commune elle-même compris
        (Pelvoux sous 05101) ;
      - celles du MÊME code INSEE sont la même collectivité sous un SIREN
        antérieur : Saillans, 212602890 jusqu'en 2024, 938438454 depuis la
        fusion du 01/01/2025. Ses comptes et ses marchés d'avant y restent
        (`commune_sirens_anciens`). Une commune absorbée sous un autre code
        (Vallouise, 05175) n'en est pas : ses comptes ne sont pas ceux de la
        commune actuelle. Son SIREN est rendu à part (code → SIREN) : ses
        comptes se relèvent à côté, jamais additionnés (tranché le 02/11/2026).

    `siren_geo` est le SIREN que geo.api.gouv.fr rend pour la commune : il fait
    foi AVANT le calcul, qui ne sait rien d'une fusion (26289 → 938438454,
    05101 → 200064657, 30140 → 213001407). Il est cherché actif au répertoire ;
    s'il n'y est pas, on le dit et on retombe sur le calcul. Vide (geo.api
    passe par des 504), rien ne change : un échec de lecture n'est pas un écart.
    """
    attendu = _prefixe_siren(insee)
    q = urllib.parse.urlencode({"q": f"commune de {nom}", "code_commune": insee})
    data = _get(f"{ENTREPRISES}?{q}") or {}
    candidats = [r for r in data.get("results", [])
                 if r.get("nom_complet", "").upper().startswith("COMMUNE")]
    actifs = [r for r in candidats if r.get("etat_administratif") != "C"]
    cessees = [r for r in candidats if r.get("etat_administratif") == "C"]

    meme_code = _prefixe_siren(insee)
    sirens_anciens = [r["siren"] for r in cessees
                      if meme_code and r.get("siren", "").startswith(meme_code)]
    anciens_noms, sirens_absorbees = {}, {}
    for code in (insee, *anciens_codes):
        prefixe = _prefixe_siren(code)
        for r in cessees:
            if prefixe and r.get("siren", "").startswith(prefixe):
                anciens_noms[code] = _nom_commune(r["nom_complet"])
                if code != insee:
                    sirens_absorbees[code] = r["siren"]

    # Une absorbée que la recherche sous le nom actuel n'a pas rendue : on la
    # cherche sous son propre nom, lu au COG (point 5 du chantier, 02/11/2026).
    manquantes = [c for c in anciens_codes if c != insee and c not in sirens_absorbees]
    noms_cog = _noms_cog(insee) if manquantes else {}
    for code in manquantes:
        if not noms_cog.get(code):
            continue
        siren_ancien = _siren_par_ancien_nom(code, noms_cog[code])
        if siren_ancien:
            sirens_absorbees[code] = siren_ancien
            anciens_noms.setdefault(code, noms_cog[code])

    choisi = _actif_par_siren(siren_geo, actifs) if siren_geo else None
    if choisi is None:
        choisi = next((r for r in actifs
                       if attendu and r.get("siren", "").startswith(attendu)), None)
        if choisi is not None and siren_geo:
            print(f"  ⚠ SIREN de geo.api ({siren_geo}) écarté, calcul retenu "
                  f"({choisi['siren']}) : à vérifier à la main")
    if choisi is None:
        # Hors de la règle de calcul (commune nouvelle, Corse, outre-mer), une
        # entité n'est retenue que si elle EST la commune : son siège dans ce
        # code INSEE, ou son nom exact. La recherche est plein texte : une
        # autre collectivité y figure dès qu'elle a un établissement ici, et
        # ses marchés seraient publiés comme ceux de la commune (revue
        # sécurité de la PR #195). Avant, l'erreur vidait la collecte.
        surs = [r for r in actifs if _est_la_commune(r, insee, nom)]
        if len(surs) > 1:
            print(f"  ⚠ {len(surs)} entités actives se disent la commune : "
                  "SIREN à vérifier à la main")
        choisi = surs[0] if surs else None
        if choisi is None and actifs:
            print(f"  ⚠ {len(actifs)} entité(s) « COMMUNE … » active(s), aucune "
                  f"dont le siège ou le nom désigne {nom} : SIREN à renseigner")
    if choisi is None:
        if cessees and not actifs:
            print(f"  ⚠ seules des entités cessées au répertoire pour {nom} : "
                  "SIREN à renseigner à la main")
        return "", "", anciens_noms, sirens_anciens, sirens_absorbees
    return (choisi.get("siren", ""), (choisi.get("siege") or {}).get("siret", ""),
            anciens_noms, [s for s in sirens_anciens if s != choisi.get("siren")],
            sirens_absorbees)


def _actif_par_siren(siren: str, actifs: list[dict]) -> dict | None:
    """L'entité active du répertoire qui porte ce SIREN, ou None (avec un avertissement).

    Parmi les candidats de la recherche « commune de <nom> » d'abord, puis par
    une recherche sur le SIREN lui-même : le nom courant d'une commune nouvelle
    n'est pas toujours celui du répertoire.
    """
    r = next((r for r in actifs if r.get("siren") == siren), None)
    if r is not None:
        return r
    data = _get(f"{ENTREPRISES}?{urllib.parse.urlencode({'q': siren})}")
    if data is None:
        return None  # répertoire injoignable : le calcul prend le relais
    r = next((r for r in data.get("results", []) if r.get("siren") == siren), None)
    if r is None:
        print(f"  ⚠ SIREN de geo.api ({siren}) absent du répertoire des entreprises")
        return None
    if r.get("etat_administratif") == "C":
        print(f"  ⚠ SIREN de geo.api ({siren}) cessé au répertoire des entreprises")
        return None
    return r


def _autres_cp(codes: list[str] | None) -> dict:
    """`codes_postaux` quand la commune en a plusieurs, rien sinon.

    `cp` reste le premier, pour tout ce qui n'en attend qu'un. Les collecteurs
    qui INTERROGENT par code postal (RNA, BODACC) les prennent tous : une
    commune nouvelle garde souvent ceux de ses anciennes communes, et le JO ne
    rangeait sous le premier code de Vallouise-Pelvoux (05290) aucune
    association écrite « Vallouise-Pelvoux » (relevé du 01/11/2026).
    """
    return {"codes_postaux": list(codes)} if codes and len(codes) > 1 else {}


def _candidats_site(nom: str) -> list[str]:
    """Adresses plausibles pour le site d'une mairie, testées une à une."""
    base = (nom.lower().replace("'", "").replace("’", "")
            .replace(" ", "-").replace("--", "-"))
    formes = [f"https://www.{base}.fr", f"https://{base}.fr",
              f"https://www.mairie-{base}.fr", f"https://www.ville-{base}.fr",
              f"https://{base}.com"]
    return [u for u in formes if _repond(u)]


def construire(insee: str) -> dict:
    print(f"[init] commune {insee}")
    commune = _get(f"{GEO}/communes/{insee}?fields=nom,code,codesPostaux,"
                   f"population,centre,contour,epci,anciensCodes,siren")
    if not commune:
        raise SystemExit(f"Code INSEE inconnu : {insee}")

    nom = commune["nom"]
    cp = commune["codesPostaux"][0]
    dept = insee[:3] if insee.startswith(("97", "98")) else insee[:2]
    epci = commune.get("epci") or {}
    # Le NOM du département, pas seulement son code : `crc` compare au titre
    # d'un rapport (« Commune de X (Gard) »), qui ne porte jamais le code.
    # Il vient de la même API que le reste du périmètre — pas d'une table des
    # cent-un départements embarquée, pas d'une déduction sur `prefecture_nom`.
    dept_nom = ((_get(f"{GEO}/departements/{dept}?fields=nom") or {}).get("nom") or "")
    print(f"  {nom} ({cp}), département {dept}"
          f"{f' — {dept_nom}' if dept_nom else ' (nom indisponible)'}, "
          f"{commune.get('population', '?')} habitants")

    membres = {}
    if epci.get("code"):
        print(f"  EPCI : {epci['nom']} ({epci['code']})")
        for c in _get(f"{GEO}/epcis/{epci['code']}/communes"
                      f"?fields=nom,code,codesPostaux,population") or []:
            membres[c["code"]] = {
                "nom": c["nom"],
                "cp": (c.get("codesPostaux") or [""])[0],
                "population": c.get("population"),
                **_autres_cp(c.get("codesPostaux")),
            }
        # Une liste vide (geo.api muet, 16/11/2026) ou sans la commune écrirait
        # une configuration que `collectors/config.py` refuse ensuite à chaque
        # step : on s'arrête ici, en échec, plutôt que de l'écrire.
        if insee not in membres:
            raise SystemExit(
                f"Communes de l'EPCI {epci['code']} illisibles ou sans {insee} "
                f"({len(membres)} reçue(s)) : configuration non écrite.")
        print(f"  {len(membres)} communes membres")
    else:
        membres[insee] = {"nom": nom, "cp": cp,
                          "population": commune.get("population"),
                          **_autres_cp(commune.get("codesPostaux"))}
        print("  commune isolée : aucun EPCI déclaré")

    siren, siret, anciens_noms, sirens_anciens, sirens_absorbees = _sirens(
        insee, nom, commune.get("anciensCodes") or [], commune.get("siren") or "")
    if anciens_noms.get(insee) and insee in membres \
            and anciens_noms[insee].lower() != nom.lower():
        # L'ancien nom porté par le code de la commune elle-même : il
        # n'a pas d'entrée parmi les anciennes communes, dont les clés sont des
        # codes INSEE distincts.
        membres[insee]["anciens_noms"] = [anciens_noms[insee]]
    # Le « code » d'un EPCI chez geo.api.gouv.fr EST son SIREN : inutile
    # d'interroger le répertoire des entreprises pour le retrouver, et risqué
    # (deux intercommunalités peuvent porter des noms proches).
    epci_siren = epci.get("code", "")

    sites = _candidats_site(nom)
    print(f"  site de la mairie : {sites[0] if sites else 'à renseigner'}"
          + (f"  (autres candidats : {', '.join(sites[1:])})" if len(sites) > 1 else ""))

    # Les fusions concernent le PÉRIMÈTRE, pas seulement la commune-siège :
    # sur le territoire de portage, la commune nouvelle était une commune
    # membre. Ne regarder que la commune de collecte, c'était manquer les trois
    # anciennes communes que SIRENE et BODACC indexent encore.
    deleguees = {}
    for code_membre in membres:
        detail = (_get(f"{GEO}/communes/{code_membre}?fields=nom,anciensCodes")
                  or {})
        for ancien in detail.get("anciensCodes") or []:
            # Le nom ne se lit que pour la commune de collecte, au répertoire
            # des entreprises (`_sirens`) ; ailleurs il reste à renseigner.
            lu = anciens_noms.get(ancien, "") if code_membre == insee else ""
            # Le SIREN fait d'une ancienne commune une absorbée relevée à part
            # (OFGL, balances, marchés) : seulement pour la commune de collecte.
            siren_ancien = sirens_absorbees.get(ancien) if code_membre == insee else None
            deleguees[ancien] = {
                "nom": lu, "cp": membres[code_membre]["cp"], "population": 0,
                "commune": code_membre,
                **({"siren": siren_ancien} if siren_ancien else {}),
                "_a_confirmer": f"ancienne commune fusionnée dans "
                                f"{detail.get('nom', code_membre)}"
                                + (" ; nom lu au répertoire SIRENE (sans ses "
                                   "accents) ou au COG" if lu else ""),
            }
    if deleguees:
        print(f"  {len(deleguees)} ancienne(s) commune(s) détectée(s) — "
              "nom à confirmer, cf. _a_faire")

    centre = commune.get("centre", {}).get("coordinates", [None, None])

    return {
        "_doc": ("Périmètre de l'instance. Écrit par scripts/init_instance.py, "
                 "relu par un humain. C'est le SEUL endroit du dispositif où "
                 "une donnée de commune a le droit d'exister."),
        "_source": "geo.api.gouv.fr + recherche-entreprises.api.gouv.fr",
        "commune_insee": insee,
        "commune_nom": nom,
        "code_postal": cp,
        "departement": dept,
        "departement_nom": dept_nom,
        "commune_siren": siren,
        "commune_siret": siret,
        "commune_sirens_anciens": sirens_anciens,
        "communes": membres,
        "anciennes_communes": deleguees,
        "epci_nom": epci.get("nom", ""),
        "epci_siren": epci_siren,
        "commune_url": sites[0] if sites else "",
        "epci_url": "",
        "connecteur": "wordpress_rest",
        "pages": {},
        "prefecture_nom": f"Préfecture ({dept})",
        "prefecture_url": f"https://www.{_nom_departement(dept)}.gouv.fr"
                          if _nom_departement(dept) else "",
        "prefecture_raa_path": "",
        "centroid": [centre[1], centre[0]] if centre[0] else [],
        "bbox": _bbox(commune["contour"]) if commune.get("contour") else [],
        # Dépôt d'où vient ce code. Cité par la page « répliquer » du site :
        # une instance qui a forké doit renvoyer vers SON dépôt, pas vers celui
        # dont elle est partie. Vide, la page invite à écrire plutôt que de
        # pointer une adresse fausse.
        "depot_url": "",
        # Ce que ce site EST pour un lecteur qui y arrive sans rien savoir.
        # Le défaut est le plus modeste, et il est VRAI : une instance qu'on
        # vient d'amorcer est une démonstration tant que personne ne l'a prise
        # en charge. Se déclarer tenue est un geste, pas un état automatique —
        # et le contrôle de publication refuse « tenue » sans dire par qui.
        "statut": {
            "_doc": "type : demonstration | constitution | tenue. "
                    "tenue_par : qui relit avant publication (exigé si tenue).",
            "type": "demonstration",
            "tenue_par": "",
        },
        "_a_faire": [
            "vérifier commune_url et renseigner epci_url",
            "choisir le connecteur (cf. collectors/connecteurs/) et ses pages",
            "renseigner prefecture_raa_path (chemin des recueils des actes)",
            *([] if dept_nom else
              ["renseigner departement_nom (le nom, pas le code) — sans lui, "
               "crc ne peut pas rapprocher un rapport de la collectivité"]),
            "confirmer le nom des anciennes communes, s'il y en a",
            "renseigner depot_url si le code est publié quelque part",
            "déclarer le statut de l'instance (« statut ») dès qu'un "
            "collectif la tient sur place : le site annonce « portage de "
            "démonstration » tant que rien n'est déclaré",
        ],
    }


def _nom_departement(dept: str) -> str:
    """Le sous-domaine préfectoral suit le nom du département, pas son code.
    On ne le devine pas : il est simplement laissé vide s'il est inconnu."""
    return ""


def adapter_regles(inst: dict, dry_run: bool) -> None:
    """Reporte l'identité et la bbox dans les règles de publication.

    Sans cette étape, le filtre de localisation garde la boîte englobante de la
    commune précédente : toutes les entités tombent « hors bbox » et perdent
    leurs coordonnées, sans erreur ni avertissement.
    """
    if not REGLES.exists():
        # Le moteur ne livre que l'exemple : les règles d'une commune ne sont
        # pas versionnées. Une instance neuve part donc de l'exemple, qu'on
        # adapte ensuite comme n'importe quelles règles existantes. Sans ça,
        # l'amorçage laissait l'instance sans filtre de publication.
        if not REGLES_EXEMPLE.exists():
            print("  [regles] ni publication_rules.json ni son exemple — étape ignorée")
            return
        REGLES.write_text(REGLES_EXEMPLE.read_text(encoding="utf-8"),
                          encoding="utf-8")
        print(f"  [regles] créées depuis {REGLES_EXEMPLE.name}")
    r = json.loads(REGLES.read_text(encoding="utf-8"))
    r["project"] = {
        "public_name": f"Vigie Civique {inst['commune_nom']}",
        "private_name": f"Atelier Vigie Civique {inst['commune_nom']}",
        "commune": inst["commune_nom"],
        "insee": inst["commune_insee"],
        "postal_code": inst["code_postal"],
    }
    if inst.get("bbox"):
        sud, ouest, nord, est = inst["bbox"]
        r.setdefault("locations", {})["bbox"] = {
            "lat_min": sud, "lat_max": nord, "lng_min": ouest, "lng_max": est}
    if inst.get("centroid"):
        lat, lng = inst["centroid"]
        # Boîte de repli du géocodeur : une entité posée exactement au centre
        # n'est pas localisée, elle est rattachée à la commune faute de mieux.
        r["locations"]["center_fallback_box"] = {
            "lat_min": round(lat - 0.001, 4), "lat_max": round(lat + 0.001, 4),
            "lng_min": round(lng - 0.001, 4), "lng_max": round(lng + 0.001, 4)}
    r.setdefault("outputs", {})["attribution"] = f"Vigie Civique {inst['commune_nom']}"
    # Les sources NATIONALES sont publiables partout : elles ne dépendent
    # d'aucune commune. Réaffirmées ici parce que le filtre ci-dessous écarte
    # tout ce qui ressemble à un domaine — or « DECP data.gouv.fr » en est un,
    # et il a disparu de toutes les instances, laissant leurs marchés DECP
    # collectés et jamais publiés.
    try:
        from collectors.marches_publics import SOURCES as SOURCES_MARCHES
    except SystemExit:
        # `collectors.config` s'arrête net s'il n'y a pas encore d'instance.
        # C'est le cas d'un `--dry-run` sur un dépôt vierge : la simulation
        # continue, en disant ce qu'elle n'a pas pu faire plutôt qu'en
        # s'arrêtant sur un message qui parle d'autre chose.
        SOURCES_MARCHES = []
        print("  [regles] sources DECP non ajoutées : pas encore d'instance "
              "(sans effet hors --dry-run)")

    sources = [s for s in r.get("events", {}).get("public_sources", [])
               if not s.endswith(".fr") or s in ("data.gouv.fr",)]
    sources += list(SOURCES_MARCHES)
    # Les portails de publicité légale comptent autant que les sites : un EPCI
    # qui dépose ses actes sur un portail tiers publie là et pas ailleurs.
    portails = [(r or {}).get("portail") for r in (inst.get("pages") or {}).values()
                if isinstance(r, dict)]
    for url in (inst.get("commune_url"), inst.get("epci_url"), *portails):
        if url:
            sources.append(urllib.parse.urlparse(url).netloc.removeprefix("www."))
    r.setdefault("events", {})["public_sources"] = sorted(set(sources))
    if not dry_run:
        REGLES.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(f"  [regles] identité, bbox et sources publiables mises à jour"
          + (" (dry-run)" if dry_run else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Amorce une instance depuis un code INSEE")
    ap.add_argument("insee", help="code INSEE de la commune (ex. 81037)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="écrase une instance déjà configurée")
    ap.add_argument("--regles", action="store_true",
                    help="ne remet à jour que publication_rules.json, depuis "
                         "l'instance existante — à lancer après avoir complété "
                         "les adresses des sites officiels")
    args = ap.parse_args()

    if args.regles:
        if not INSTANCE.exists():
            print(f"✖ {INSTANCE.relative_to(ROOT)} absent.", file=sys.stderr)
            return 1
        adapter_regles(json.loads(INSTANCE.read_text(encoding="utf-8")),
                       args.dry_run)
        return 0

    if INSTANCE.exists() and not args.force and not args.dry_run:
        print(f"✖ {INSTANCE.relative_to(ROOT)} existe déjà. "
              "Relancer avec --force pour l'écraser.", file=sys.stderr)
        return 1

    inst = construire(args.insee)

    if args.dry_run:
        adapter_regles(inst, True)
        print("\n" + json.dumps(inst, ensure_ascii=False, indent=2))
        print("\n(dry-run — rien écrit)")
        return 0

    # L'instance s'écrit AVANT que les règles ne s'adaptent, et l'ordre inverse
    # rendait l'amorçage d'une instance NEUVE impossible : `adapter_regles`
    # importe `collectors.marches_publics`, donc `collectors.config`, qui refuse
    # de se charger sans `config/instance.json` — le fichier que ces trois
    # lignes écrivent. Le défaut ne s'est jamais vu parce que les instances
    # existantes ont toutes été amorcées sur une configuration déjà là.
    INSTANCE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCE.write_text(json.dumps(inst, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"\n✓ {INSTANCE.relative_to(ROOT)} écrit")
    # Les saisies de l'atelier vivent hors de `config/`, que le service ne doit
    # pas pouvoir écrire (cf. collectors/saisies.py) : leur répertoire existe dès
    # l'amorçage, sans quoi elles retomberaient dans `config/`.
    (ROOT / "saisies").mkdir(exist_ok=True)

    adapter_regles(inst, False)

    # Les libellés des deux applications SvelteKit sont générés depuis
    # l'instance et ne sont pas versionnés. Sans eux, `pnpm run build` échoue sur
    # un ENOENT illisible : le module manque, mais rien ne dit qu'il se génère.
    # Les écrire ici évite d'avoir à le savoir.
    # Importé sous alias : `construire` est aussi le nom d'une fonction de ce
    # module, appelée plus haut dans main(). Un import local du même nom la rend
    # locale pour TOUTE la fonction, y compris avant l'import — l'appel de la
    # ligne 316 levait alors un UnboundLocalError.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from generer_libelles import (construire as _libelles,
                                      ecrire as _ecrire_libelles, CIBLES)
        _ecrire_libelles(_libelles())
        for cible in CIBLES:
            print(f"✓ {cible.relative_to(ROOT)} généré")
    except Exception as exc:
        print(f"  [libellés] non générés : {exc}\n"
              "  Lancer :  python3 scripts/generer_libelles.py")

    print("\nÀ faire à la main avant la première collecte :")
    for tache in inst["_a_faire"]:
        print(f"  - {tache}")
    print("\nPuis :  python3 -m collectors.run_all")
    print("        (le dernier step, `perimetre`, conditionne la publication)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
