"""
incendie.py — Le feu et la forêt : ce qui est boisé, ce qui a brûlé, et qui est
tenu de débroussailler.

Quatre relevés, par commune suivie en profondeur :
  1. `foret_boisement` — surface boisée et taux de boisement de la commune
     (IGN, Observatoire des forêts ; hectares).
  2. `foret_publique` — les forêts publiques qui ont un pied dans la commune
     (BD TOPO, d'après l'ONF). Interrogées par l'emprise rectangulaire puis
     triées sur le contour : l'emprise seule ferait entrer la forêt du voisin.
  3. `debroussaillement` — combien d'adresses de la commune (Base adresse
     nationale) tombent dans le zonage des obligations légales de
     débroussaillement publié sur la Géoplateforme.
     ⚠️ Le code `zonage` de ces polygones est LOCAL : dans le Gard il distingue
     le massif (1) de son enveloppe à 200 m (2), le Tarn ne publie que des 2.
     On ne relève donc que l'appartenance au zonage, qui se lit partout pareil.
  4. `feux` — les feux recensés par la BDIFF (ministère de l'agriculture, IGN).
     La BDIFF n'a pas d'API : c'est un tableau HTML paginé, dont les critères
     vivent dans la session.

⚖️ Un zéro n'est un fait que si la source a RÉPONDU. Chaque relevé qui aboutit
s'inscrit dans `incendie_suivi`, avec ce qu'il a couvert : « aucune forêt
publique » et « aucun feu de 2006 à 2025 » ne se publient que sur cette ligne.
Sans elle, rien n'est dit.

Ne sont PAS relevés ici : les centres de secours (OpenStreetMap les porte déjà,
step `osm`) ni les temps de trajet — le seul calculateur ouvert sans clé est un
serveur de démonstration, et la durée d'une voiture n'est pas un délai
d'intervention.

Usage :
  python3 -m collectors.incendie
  python3 -m collectors.incendie --stats
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import html
import io
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
import urllib.parse
import urllib.request

import certifi

from . import mesure, reseau
from .archive import _ssl_ctx
from .config import BBOX, HEADERS, NATIONAL_STORE, communes_du_step
from .db import get_conn
from .national_store import (CODE_INSEE, DEPARTEMENT, copier_atomiquement, departement,
                             ecrire_atomiquement, est_frais, magasin_ecrivable,
                             perime_lisible)

WFS = "https://data.geopf.fr/wfs/ows"
BAN = "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-{}.csv.gz"
BDIFF = "https://bdiff.agriculture.gouv.fr/incendies"
# L'intermédiaire que la BDIFF ne présente pas (cf. `_racines_bdiff`).
INTERMEDIAIRE_BDIFF = Path(__file__).parent / "certificats" / "harica-geant-tls-rsa-1.pem"
CACHE = NATIONAL_STORE / "ban"
# La BAN se republie chaque semaine ; les adresses d'une commune, elles, bougent
# peu. Un trimestre suffit, et le fichier d'un département sert à ses voisines.
BAN_JOURS = 90
# Le passage de l'écrivain du magasin : la BAN de toute la France, découpée en
# fichiers départementaux. Le témoin dit quand elle a été lue.
TEMOIN_FRANCE = CACHE / "france.json"
# Moins que cela dans le fichier national, c'est qu'il n'est pas entier :
# 101 départements, dont les cinq d'outre-mer.
DEPARTEMENTS_MIN = 95
# Des lignes au code INSEE invalide, il y en a peut-être une poignée ; au-delà,
# le fichier n'est pas celui qu'on croit, et rien n'est remplacé.
REJETS_MAX = 1000
# Le pic de disque d'une découpe : le fichier national, ses découpes (un peu
# plus grosses, compressées plus vite) et un `.part`. Mesuré en multiple du
# fichier national, au-dessus de la marge de l'écrivain.
PIC_DISQUE = 2.5
MARGE_DISQUE = 3 << 30
# La BDIFF commence en 2006 sur tout le territoire (1973 en zone
# méditerranéenne seulement) : partir de 2006 rend les communes comparables.
FEUX_DEBUT = 2006
BOISEMENT = ("surface_commune", "surface_foret", "taux_boisement", "feuillus",
             "coniferes", "mixtes", "sans_couvert", "annee_pva")


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS foret_boisement (
            insee           TEXT PRIMARY KEY,
            surface_commune REAL,   -- hectares
            surface_foret   REAL,
            taux_boisement  REAL,   -- %
            feuillus        REAL,
            coniferes       REAL,
            mixtes          REAL,
            sans_couvert    REAL,
            annee_pva       INTEGER -- année des prises de vue aériennes
        );
        CREATE TABLE IF NOT EXISTS foret_publique (
            insee   TEXT NOT NULL,
            nom     TEXT NOT NULL,
            nature  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (insee, nom, nature)
        );
        CREATE TABLE IF NOT EXISTS debroussaillement (
            insee        TEXT PRIMARY KEY,
            adresses     INTEGER NOT NULL,   -- adresses de la commune dans la BAN
            dans_zonage  INTEGER NOT NULL,
            polygones    INTEGER NOT NULL,   -- polygones du zonage dans l'emprise
            arrete       TEXT,               -- date de l'arrêté préfectoral
            millesime    TEXT,
            url          TEXT                -- la page de la préfecture
        );
        CREATE TABLE IF NOT EXISTS feux (
            insee       TEXT NOT NULL,
            cle         TEXT NOT NULL,   -- numéro de fiche BDIFF, sinon l'alerte
            alerte      TEXT,            -- AAAA-MM-JJ HH:MM
            surface_ha  REAL,
            foret_ha    REAL,
            cause       TEXT,
            PRIMARY KEY (insee, cle)
        );
        -- Ce que chaque relevé a couvert la dernière fois qu'il a abouti.
        CREATE TABLE IF NOT EXISTS incendie_suivi (
            insee      TEXT NOT NULL,
            releve     TEXT NOT NULL,   -- boisement | forets_publiques | debroussaillement | feux
            debut      INTEGER,         -- feux : première année interrogée
            fin        INTEGER,         -- feux : dernière année interrogée
            releve_le  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, releve)
        );
    """)
    conn.commit()


def _noter(conn, insee: str, releve: str, debut: int | None = None, fin: int | None = None):
    conn.execute("INSERT OR REPLACE INTO incendie_suivi (insee, releve, debut, fin)"
                 " VALUES (?,?,?,?)", (insee, releve, debut, fin))
    conn.commit()


# ── Lecture ──────────────────────────────────────────────────────────────────

def _lire(url: str, params: dict | None = None, delai: int = 90) -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    requete = urllib.request.Request(url, headers=HEADERS)
    with reseau.ouvrir(requete, timeout=delai, context=_ssl_ctx()) as reponse:
        return reponse.read()


def wfs(couche: str, *, cql: str | None = None, bbox: tuple | None = None,
        maxi: int = 5000) -> list[dict]:
    """Objets d'une couche de la Géoplateforme, en WGS84 (lon, lat)."""
    params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
              "TYPENAMES": couche, "OUTPUTFORMAT": "application/json",
              "SRSNAME": "EPSG:4326", "COUNT": maxi}
    if cql:
        params["CQL_FILTER"] = cql
    if bbox:  # (lat_min, lon_min, lat_max, lon_max), comme dans instance.json
        params["BBOX"] = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},EPSG:4326"
    return json.loads(_lire(WFS, params)).get("features", [])


def contour(insee: str) -> dict:
    return json.loads(_lire(f"https://geo.api.gouv.fr/communes/{insee}",
                            {"fields": "contour", "format": "json"}))["contour"]


def adresses_ban(insee: str) -> list[tuple[float, float]]:
    """(lon, lat) de chaque adresse de la commune. Le fichier est départemental,
    et l'écrivain du magasin les dépose tous (`remplir_magasin`).

    Le repli suit la règle unique du magasin (`perime_lisible`) : un lecteur
    qui ne peut pas écrire lit la copie de moins de 2 × `BAN_JOURS`, et le dit,
    plutôt que d'appeler la BAN ; si l'appel échoue, même règle. Au-delà, le
    relevé échoue : la BAN a rendu 503 du 21 au 27/10/2026, mais une copie de
    huit mois ne se publie pas en silence."""
    chemin = CACHE / f"adresses-{departement(insee)}.csv.gz"
    if est_frais(chemin, BAN_JOURS):
        return points_de_la_commune(chemin.read_bytes(), insee)
    ecrivable = magasin_ecrivable(CACHE, chemin.name)
    if not ecrivable and perime_lisible(chemin, BAN_JOURS):
        return points_de_la_commune(chemin.read_bytes(), insee)
    try:
        brut = _lire(BAN.format(departement(insee)), delai=300)
    except OSError as e:
        print(f"  [incendie] BAN {departement(insee)} injoignable ({type(e).__name__}: {e})")
        if perime_lisible(chemin, BAN_JOURS):
            return points_de_la_commune(chemin.read_bytes(), insee)
        raise
    ecrire_atomiquement(chemin, brut)
    return points_de_la_commune(brut, insee)


def remplir_magasin(departements: list[str] | None = None) -> list[str]:
    """Pour l'écrivain du magasin : la BAN de TOUTE la France, une fois par
    `BAN_JOURS`, découpée en `adresses-<département>.csv.gz`.

    Un seul téléchargement couvre tous les départements, demandés ou non : un
    dossier n'appelle plus la BAN, et une panne de celle-ci (21-27/10, puis le
    30/10/2026) ne touche aucune génération pendant un trimestre.
    `departements` est ignoré ; il est là pour la signature commune.

    Le travail se fait dans `.adresses-france.part`, à côté des fichiers :
    même disque, et l'écrivain purge ce dossier s'il est abandonné. Chaque
    fichier départemental passe `copier_atomiquement`, donc `valider_contenu`
    et la règle de la fonte, avant de remplacer l'ancien. Un département
    refusé garde son ancien fichier ; il est noté au témoin et rendu en échec
    à chaque passe jusqu'au prochain téléchargement, sans retélécharger 2 Go
    chaque nuit.
    """
    if est_frais(TEMOIN_FRANCE, BAN_JOURS):
        restes = json.loads(TEMOIN_FRANCE.read_text(encoding="utf-8")).get("echecs", [])
        print(f"  [incendie] BAN France lue il y a moins de {BAN_JOURS} jours"
              + (f" ; départements refusés alors : {', '.join(restes)}" if restes else ""))
        return restes
    travail = CACHE / ".adresses-france.part"
    shutil.rmtree(travail, ignore_errors=True)
    travail.mkdir(parents=True)
    echecs: list[str] = []
    try:
        france = travail / "adresses-france.csv.gz"
        _telecharger_france(france)
        _exiger_disque(france.stat().st_size, "avant la découpe")
        decoupes = decouper_france(france, travail)
        if len(decoupes) < DEPARTEMENTS_MIN:
            raise RuntimeError(f"BAN France : {len(decoupes)} départements, "
                               f"{DEPARTEMENTS_MIN} attendus au moins")
        france.unlink()
        for dep, (chemin, _) in sorted(decoupes.items()):
            try:
                with chemin.open("rb") as flux:
                    copier_atomiquement(CACHE / f"adresses-{dep}.csv.gz", flux)
            except ValueError as e:     # ContenuInvalide : la fonte, un fichier illisible
                print(f"  ✗ [incendie] BAN {dep} refusée, l'ancienne reste : {e}")
                echecs.append(dep)
            chemin.unlink()
        ecrire_atomiquement(TEMOIN_FRANCE, json.dumps({
            "lueLe": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "source": BAN.format("france"),
            "adresses": {dep: n for dep, (_, n) in sorted(decoupes.items())},
            "echecs": echecs,
        }, ensure_ascii=False).encode())
    finally:
        shutil.rmtree(travail, ignore_errors=True)
    print(f"  [incendie] BAN France découpée : {len(decoupes) - len(echecs)}"
          f"/{len(decoupes)} départements remplacés")
    return echecs


def _exiger_disque(taille_france: int, quand: str) -> None:
    """Le disque du magasin est celui des bases des ateliers : une découpe qui
    le remplirait est un incident pour Lasalle, pas seulement pour la BAN."""
    libre = shutil.disk_usage(CACHE).free
    besoin = int(PIC_DISQUE * taille_france) + MARGE_DISQUE
    if libre < besoin:
        raise OSError(f"BAN France {quand} : {libre >> 20} Mio libres, "
                      f"{besoin >> 20} nécessaires ; rien n'est remplacé")


def _telecharger_france(cible: Path, essais: int = 3) -> None:
    """Le fichier national en flux, taille annoncée vérifiée. La BAN coupe
    parfois au bout d'une minute (30/10/2026) : trois essais espacés."""
    for essai in range(essais):
        try:
            requete = urllib.request.Request(BAN.format("france"), headers=HEADERS)
            # Un contexte VÉRIFIÉ, sans repli : ce fichier alimente tous les
            # dossiers (`_ssl_ctx` se rabat sur un contexte non vérifié).
            contexte = ssl.create_default_context(cafile=certifi.where())
            with reseau.ouvrir(requete, timeout=600, context=contexte) as reponse:
                annoncee = reponse.headers.get("Content-Length")
                if annoncee and annoncee.isdigit():
                    _exiger_disque(int(annoncee), "avant le téléchargement")
                copier_atomiquement(cible, reponse)
            return
        except (OSError, EOFError) as e:
            if essai == essais - 1:
                raise
            print(f"  [incendie] BAN France coupée ({type(e).__name__}: {e}), "
                  f"nouvel essai dans {60 * (essai + 1)} s")
            time.sleep(60 * (essai + 1))


def decouper_france(france: Path, dossier: Path) -> dict[str, tuple[Path, int]]:
    """{département: (fichier compressé, nombre d'adresses)}, en flux : le
    fichier national ne tient jamais en mémoire, une ligne à la fois."""
    sorties: dict[str, list] = {}
    rejets = 0
    with gzip.open(france, "rt", encoding="utf-8", newline="") as flux:
        lecteur = csv.reader(flux, delimiter=";")
        entete = next(lecteur)
        colonne = entete.index("code_insee")
        try:
            for ligne in lecteur:
                code = ligne[colonne] if len(ligne) > colonne else ""
                # Le code vient de la source et nomme un fichier : rien d'autre
                # qu'un code INSEE, et un département connu.
                if not CODE_INSEE.fullmatch(code) or not DEPARTEMENT.fullmatch(departement(code)):
                    rejets += 1
                    if rejets > REJETS_MAX:
                        raise RuntimeError(f"BAN France : plus de {REJETS_MAX} lignes sans "
                                           "code INSEE valide ; rien n'est remplacé")
                    continue
                dep = departement(code)
                if dep not in sorties:
                    chemin = dossier / f"adresses-{dep}.csv.gz"
                    fichier = gzip.open(chemin, "wt", encoding="utf-8", newline="", compresslevel=6)
                    ecrivain = csv.writer(fichier, delimiter=";", lineterminator="\n")
                    ecrivain.writerow(entete)
                    sorties[dep] = [chemin, fichier, ecrivain, 0]
                sortie = sorties[dep]
                sortie[2].writerow(ligne)
                sortie[3] += 1
        finally:
            for _, fichier, _, _ in sorties.values():
                fichier.close()
    if rejets:
        print(f"  [incendie] BAN France : {rejets} ligne(s) sans code INSEE valide, ignorées")
    return {dep: (chemin, n) for dep, (chemin, _, _, n) in sorties.items()}


def points_de_la_commune(brut: bytes, insee: str) -> list[tuple[float, float]]:
    """Le CSV départemental compressé, lu EN FLUX : décompressé d'un bloc, celui
    du Nord (174 Mio) faisait monter le step à 1,5 Gio, contre 56 Mio ainsi
    (mesuré le 27/10/2026)."""
    with gzip.open(io.BytesIO(brut), "rt", encoding="utf-8", newline="") as flux:
        return [(float(l["lon"]), float(l["lat"]))
                for l in csv.DictReader(flux, delimiter=";")
                if l["code_insee"] == insee and l["lon"] and l["lat"]]


# ── Géométrie ────────────────────────────────────────────────────────────────

def anneaux(geom: dict) -> list[list]:
    """Tous les polygones d'une géométrie, chacun avec ses trous : [[ext, trou…], …]."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def dans_anneau(x: float, y: float, anneau: list) -> bool:
    dedans, j = False, len(anneau) - 1
    for i in range(len(anneau)):
        xi, yi, xj, yj = anneau[i][0], anneau[i][1], anneau[j][0], anneau[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans


def dedans(x: float, y: float, geom: dict) -> bool:
    """Dans un polygone et hors de ses trous."""
    return any(dans_anneau(x, y, p[0]) and not any(dans_anneau(x, y, t) for t in p[1:])
               for p in anneaux(geom))


def adresses_dans_le_zonage(points: list[tuple[float, float]], zones: list[dict]) -> tuple[int, set[int]]:
    """Combien de points tombent dans au moins une zone, et lesquelles en
    contiennent — c'est sur elles seules que se lisent l'arrêté et sa date :
    l'emprise ramène aussi les polygones du département voisin."""
    boites = []
    for z in zones:
        xs = [p[0] for poly in anneaux(z["geometry"]) for p in poly[0]]
        ys = [p[1] for poly in anneaux(z["geometry"]) for p in poly[0]]
        boites.append((min(xs), min(ys), max(xs), max(ys)) if xs else None)
    dans, touchees = 0, set()
    for lon, lat in points:
        ici = [i for i, (z, b) in enumerate(zip(zones, boites))
               if b and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]
               and dedans(lon, lat, z["geometry"])]
        dans += bool(ici)
        touchees.update(ici)
    return dans, touchees


# ── Les quatre relevés ───────────────────────────────────────────────────────

def releve_boisement(conn, insee: str) -> str:
    objets = wfs("ObsForets.Ressources.TxBoisement:taux_boisement_communal",
                 cql=f"insee_commune='{insee}'")
    if not objets:
        return "commune absente de la couche de l'IGN"
    p = objets[0]["properties"]
    conn.execute("INSERT OR REPLACE INTO foret_boisement VALUES (?,?,?,?,?,?,?,?,?)",
                 (insee, *(p.get(k) for k in BOISEMENT)))
    _noter(conn, insee, "boisement")
    return f"{p.get('surface_foret')} ha de forêt sur {p.get('surface_commune')}"


def forets_de_la_commune(objets: list[dict], limite: dict) -> list[tuple[str, str]]:
    """Un point de la forêt dans la commune suffit à la nommer."""
    noms = set()
    for o in objets:
        for polygone in anneaux(o["geometry"]):
            if any(dedans(x, y, limite) for x, y in polygone[0][::5]):
                p = o["properties"]
                noms.add((p.get("toponyme") or "sans nom", p.get("nature") or ""))
                break
    return sorted(noms)


def releve_forets_publiques(conn, insee: str) -> str:
    forets = forets_de_la_commune(wfs("BDTOPO_V3:foret_publique", bbox=BBOX), contour(insee))
    conn.execute("DELETE FROM foret_publique WHERE insee=?", (insee,))
    conn.executemany("INSERT INTO foret_publique VALUES (?,?,?)",
                     [(insee, nom, nature) for nom, nature in forets])
    _noter(conn, insee, "forets_publiques")
    return f"{len(forets)} forêt(s) publique(s)"


def releve_debroussaillement(conn, insee: str) -> str:
    zones = wfs("DEBROUSSAILLEMENT:debroussaillement", bbox=BBOX, maxi=20000)
    mesure.lu("ign-debroussaillement", len(zones),
              mesure.forme_de(zones, "properties"), complete=True)
    points = adresses_ban(insee)
    if not points:
        # Une commune sans adresse n'existe pas : c'est la BAN qui n'a pas
        # répondu ce qu'on attend, et « 0 adresse sur 0 » ne dirait rien.
        raise RuntimeError("aucune adresse de la commune dans la Base adresse nationale")
    dans, touchees = adresses_dans_le_zonage(points, zones)
    references = sorted({(p.get("dat_ap_old"), p.get("millesime"), p.get("url"))
                         for p in (zones[i]["properties"] for i in touchees)}, key=str)
    arrete, millesime, url = references[0] if references else (None, None, None)
    conn.execute("INSERT OR REPLACE INTO debroussaillement VALUES (?,?,?,?,?,?,?)",
                 (insee, len(points), dans, len(zones), arrete, millesime, url))
    _noter(conn, insee, "debroussaillement")
    return f"{dans} adresse(s) sur {len(points)} dans le zonage"


def lire_page_bdiff(page: str, insee: str) -> list[dict]:
    """Les feux d'une page du tableau. Lève si la page n'est pas celle de la
    commune : le filtre vit dans la session, et le perdre rend la France entière."""
    if "<table" not in page:
        raise RuntimeError("la BDIFF n'a pas rendu de tableau")
    feux = []
    for ligne in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cases = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", ligne, re.S)]
        if len(cases) < 8 or not re.fullmatch(r"\d{4}", cases[2]):
            continue
        if insee not in cases[5]:
            raise RuntimeError(f"la BDIFF a rendu une autre commune ({cases[5]}) : filtre perdu")
        fiche = re.search(r"fiche (\d{4}-\d+)", cases[1])
        surface = re.match(r"([\d.]+)", cases[6])
        foret = re.search(r"Forêt : ([\d.]+)", cases[6])
        jour = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4}) (\d{2}:\d{2})", cases[3])
        feux.append({
            "cle": fiche.group(1) if fiche else cases[3],
            "alerte": f"{jour[3]}-{jour[2]}-{jour[1]} {jour[4]}" if jour else cases[3],
            "surface_ha": float(surface.group(1)) if surface else None,
            "foret_ha": float(foret.group(1)) if foret else None,
            "cause": None if cases[7] in ("", "-") else cases[7]})
    return feux


def _racines_bdiff(dossier: str) -> list[str]:
    """Les options `curl` qui lui donnent de quoi vérifier la BDIFF.

    Le serveur ne présente pas le bon intermédiaire : son certificat est signé
    par « GEANT TLS RSA 1 » (HARICA) et il sert « GEANT OV RSA CA 4 ». Le curl
    du Mac retrouve le bon par l'extension AIA du certificat ; celui d'un
    Debian (OpenSSL) ne le fait pas, et sort en code 60. C'était invisible sur
    le poste et fatal sur le VPS : le relevé « feux » échouait à chaque
    génération du portail (constaté le 26/10/2026).

    On remet donc l'intermédiaire nous-mêmes, à côté des racines de `certifi`,
    plutôt que de désactiver la vérification. Il remonte à « HARICA TLS RSA
    Root CA 2021 » et vaut jusqu'en 2039 ; si la BDIFF change d'émetteur, le
    code 60 reviendra et le certificat est à relever dans l'extension AIA de
    celui que sert le site (`openssl s_client -showcerts`).
    """
    try:
        import certifi
        racines = Path(certifi.where()).read_bytes()
    except ImportError:
        return []
    paquet = Path(dossier) / "racines-bdiff.pem"
    paquet.write_bytes(racines + b"\n" + INTERMEDIAIRE_BDIFF.read_bytes())
    return ["--cacert", str(paquet)]


def feux_bdiff(insee: str, debut: int, fin: int) -> list[dict]:
    # Python refuse la chaîne de la BDIFF même avec certifi (intermédiaire
    # absent) : on passe par curl, avec l'intermédiaire en main, plutôt que de
    # désactiver la vérification.
    criteres = urllib.parse.urlencode(
        {"if[dateAlerteDeb][date]": f"01/01/{debut}", "if[dateAlerteFin][date]": f"31/12/{fin}",
         "if[commune]": insee})
    feux: dict[str, dict] = {}
    with tempfile.NamedTemporaryFile() as pot, tempfile.TemporaryDirectory() as dossier:
        racines = _racines_bdiff(dossier)
        # Dix lignes par page. La page 1 pose les critères, les suivantes se
        # demandent SANS eux (les renvoyer rejoue la page 1) et avec le pot à
        # cookies (sans lui, c'est la France entière).
        for numero in range(1, 60):
            page = subprocess.run(
                ["curl", "-s", "-m", "60", *racines, "-A", HEADERS["User-Agent"], "-b", pot.name, "-c", pot.name,
                 f"{BDIFF}?" + (criteres if numero == 1 else f"page={numero}")],
                capture_output=True, check=True).stdout.decode("utf-8", "replace")
            lus = lire_page_bdiff(page, insee)
            nouveaux = [f for f in lus if f["cle"] not in feux]
            feux.update({f["cle"]: f for f in nouveaux})
            if len(nouveaux) < 10:
                break
    # `curl` échappe à la mesure des réponses : ce compte est la seule trace.
    # Complet : les saisons closes ne se retirent pas de la base.
    fiches = list(feux.values())
    mesure.lu("bdiff-feux", len(fiches), mesure.forme_de(fiches),
              complete=True)
    return fiches


def releve_feux(conn, insee: str) -> str:
    # Jusqu'à la dernière année CLOSE : l'année en cours n'est versée à la
    # BDIFF qu'après sa saison, et « aucun feu en 2026 » lu en juin serait faux.
    fin = dt.date.today().year - 1
    feux = feux_bdiff(insee, FEUX_DEBUT, fin)
    # Le relevé couvre toute la période : il REMPLACE ce qu'on savait, pour
    # qu'une fiche retirée de la BDIFF ne survive pas ici.
    conn.execute("DELETE FROM feux WHERE insee=?", (insee,))
    conn.executemany("INSERT INTO feux VALUES (?,?,?,?,?,?)",
                     [(insee, f["cle"], f["alerte"], f["surface_ha"], f["foret_ha"], f["cause"])
                      for f in feux])
    _noter(conn, insee, "feux", FEUX_DEBUT, fin)
    return f"{len(feux)} feu(x) de {FEUX_DEBUT} à {fin}"


def run():
    conn = get_conn()
    ensure_tables(conn)
    etapes = (("boisement", releve_boisement), ("forêts publiques", releve_forets_publiques),
              ("débroussaillement", releve_debroussaillement), ("feux", releve_feux))
    echecs = []
    for insee in communes_du_step("incendie"):
        for nom, etape in etapes:
            try:
                print(f"  [incendie] {insee} {nom} : {etape(conn, insee)}")
            except Exception as e:      # une source muette n'empêche pas les autres
                echecs.append(f"{insee} {nom}")
                print(f"  [incendie] {insee} {nom} : échec → {type(e).__name__}: {e}")
    conn.close()
    if echecs:
        raise RuntimeError(f"relevés en échec : {', '.join(echecs)}")


def show_stats():
    conn = get_conn(read_only=True)
    for r in conn.execute("SELECT insee, releve, debut, fin, releve_le FROM incendie_suivi"
                          " ORDER BY insee, releve"):
        periode = f" ({r[2]}–{r[3]})" if r[2] else ""
        print(f"  {r[0]} {r[1]}{periode} — relevé le {r[4]}")
    for table in ("foret_publique", "feux"):
        print(f"  {table} : {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]} ligne(s)")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Forêt, feux et débroussaillement")
    ap.add_argument("--stats", action="store_true")
    show_stats() if ap.parse_args().stats else run()
