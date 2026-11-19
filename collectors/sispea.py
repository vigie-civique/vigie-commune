"""
sispea.py — Le prix de l'eau du robinet, et l'état du réseau qui l'apporte.

Prix TTC du mètre cube, rendement du réseau, taux de renouvellement des
canalisations, conformité sanitaire : les chiffres qu'un habitant cherche quand
il pense « eau », et que personne ne va lire dans un rapport annexé au compte
administratif.

Deux sources pour une seule donnée — l'observatoire national SISPEA :

  - **La composition communale des services** (Observatoire, un classeur par
    année et par compétence) dit DE QUEL service une commune dépend. Elle
    remplace Hub'Eau, dont l'API `indicateurs_services` a été décommissionnée
    le 10/09/2026 (410 Gone sur toutes ses routes) : la collecte rendait alors
    zéro service sans échouer, et les dossiers perdaient l'eau en silence.
  - **Les extractions de l'OFB** donnent les indicateurs des exercices 2015 à
    2024, France entière, en archives 7z de classeurs Excel : elles ne portent
    aucune liste de communes — elles les COMPTENT (« 6 ») sans les nommer.

Ensemble : une série de 2015 à 2024, service par service (les exercices
2008-2014 qu'apportait Hub'Eau restent en base là où ils ont été collectés).
C'est ce que le site d'une commune peut montrer et qu'aucun aperçu national ne
montrera — non pas un prix, mais son évolution.

Usage :
  python3 -m collectors.sispea
  python3 -m collectors.sispea --stats
  python3 -m collectors.sispea --sans-millesimes   # rattachement seul, sans les indicateurs

─────────────────────────────────────────────────────────────────────────────
CE QUI REND LA JOINTURE POSSIBLE

`Id SISPEA de l'entité de gestion`, dans les fichiers de l'OFB, EST
l'« Identifiant SISPEA de l'entité de gestion » de la composition communale
(et était le `code_service` de Hub'Eau). Vérifié sur plusieurs services, dont un syndicat
intercommunal et deux régies communales. Sans cette égalité, les extractions
nationales seraient inexploitables commune par commune.

⚠️ CINQ PIÈGES, tous rencontrés en lisant ces fichiers, aucun visible à la
relecture du code :

1. (Hub'Eau, pour mémoire) `/indicateurs` N'A PAS DE PARAMÈTRE `code_commune` et l'accepte
   quand même : il rend la France entière — 68 064 lignes — en commençant par un
   autre département. On passe par `/services` et `/communes`, qui filtrent.

2. `/communes` REND CHAQUE LIGNE EN DOUBLE, une fois nommée, une fois anonyme.

3. LES EN-TÊTES DES CLASSEURS CHANGENT DE CONVENTION À CHAQUE GÉNÉRATION :
   « Id SISPEA du service » (2015-2019), `id_sispea_serv` (2020-2023),
   « Id SISPEA de l'entité de gestion » (2024) — et l'un des fichiers écrit
   `N_iNSEE_si_commune`, avec une capitale au milieu d'un mot. D'où une
   comparaison sur forme normalisée, des alias déclarés, et un ARRÊT NET quand
   une colonne manque : un index à demi rempli, écrit en silence, serait pire.

4. L'EXTENSION MENT — une archive livre un `.xls` qui est un `.xlsx`. Et
   reconnaître les octets ne suffit pas : `openpyxl` refuse d'après l'EXTENSION
   sans regarder le contenu, d'où le passage par un flux.

5. ZÉRO N'EST PAS UN PRIX. Des services déclarent 0 €/m³ : une case laissée
   vide, pas une eau gratuite. Le zéro reste légitime partout ailleurs — un
   service peut n'avoir renouvelé aucune canalisation dans l'année.

🔴 ET LES CLASSEURS PORTENT UNE COLONNE DE COURRIELS, une adresse par service et
souvent nominative. Les colonnes lues sont une LISTE BLANCHE, jamais un retrait
de ce qui gêne : une colonne ajoutée par le producteur n'entre pas dans la base
sans qu'on l'ait écrite ici.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

from . import mesure
from .config import COMMUNE_INSEE, HEADERS, NATIONAL_STORE, communes_du_step
from .db import get_conn
from .national_store import (ecrire_atomiquement, est_frais, magasin_ecrivable,
                             perime_lisible)

SOURCE = "sispea"
COMPOSITION = ("https://www.services.eaufrance.fr/documents/serviceMembers/"
               "CompositionCommunaleServices_{competence}_{annee}.zip")
CATALOGUE = "https://data.ofb.fr/catalogue/srv/api/records/{record}/attachments"
CACHE = NATIONAL_STORE / "sispea"

# Les exercices que l'OFB publie. Les anciens apportent la couverture : un
# service qui a cessé de déclarer en 2017 n'est dans aucun fichier récent.
MILLESIMES = tuple(range(2015, 2025))
# Les années de composition lues : celles des exercices, et la suivante, qui dit
# le service d'aujourd'hui (la composition est publiée avant les indicateurs).
COMPOSITIONS = tuple(range(MILLESIMES[0], MILLESIMES[-1] + 2))

# Un millésime ne change plus une fois publié ; seul le dernier peut être
# complété en cours d'année.
MILLESIME_CACHE_JOURS = 180

COMPETENCES = {
    "AEP": {
        "record": "7d6a3010-cf19-42c3-8a38-9823074185ce",
        "marque": "_AEP",
        "libelle": "eau potable",
    },
    "AC": {
        "record": "5feec4e9-03a6-409a-a522-d51346d5f4c9",
        "marque": "_AC",
        "libelle": "assainissement collectif",
    },
}

# LISTE BLANCHE des colonnes des classeurs. Chaque entrée : le nom retenu, puis
# les formes normalisées sous lesquelles les générations d'export l'écrivent.
COLONNES = {
    "service": ("id_sispea_de_l_entite_de_gestion", "id_sispea_serv", "id_sispea_du_service"),
    "collectivite": ("nom_collectivite", "nom_coll"),
    "type": ("type_collectivite", "type_coll"),
    "gestion": ("mode_de_gestion", "mode_gestion"),
}

# LISTE BLANCHE de la composition communale (même en-tête de 2015 à 2025, eau
# potable et assainissement). L'opérateur et les dates du mode de gestion
# entrent en base : « délégation à la SAUR jusqu'en 2031 » est ce qu'un habitant
# cherche. Leur PUBLICATION est une autre décision (`snapshot/territoire.py`
# nomme ses colonnes une à une, et ne les lit pas).
COLONNES_COMPOSITION = {
    "insee": "code_insee_de_la_commune_adherente",
    "service": "identifiant_sispea_de_l_entite_de_gestion_a_laquelle_la_commune_adhere",
    "libelle": "nom_de_l_entite_de_gestion_a_laquelle_la_commune_adhere",
    "nom": "nom_de_la_collectivite_de_l_entite_de_gestion_a_laquelle_la_commune_adhere",
    "type_collectivite": "type_de_collectivite",
    "siren": "n_siren",
    "mode_gestion": "type_du_mode_de_gestion",
    "operateur": "nom_de_l_operateur",
    "statut_operateur": "statut_de_l_operateur",
    "debut_gestion": "date_de_debut_du_mode_de_gestion",
    "fin_gestion": "date_de_fin_du_mode_de_gestion",
}

# `annee_operateur` : l'année de la composition qui l'a déclaré. Une date de
# fin de contrat n'a de sens que rapportée à l'année où elle a été déclarée
# (avis DPO du 26/10/2026 : beaucoup sont dépassées dans les fichiers anciens).
CHAMPS_OPERATEUR = ("operateur", "statut_operateur", "debut_gestion", "fin_gestion",
                    "annee_operateur")

# Les indicateurs retenus, et ce qu'ils disent. Les mêmes des deux sources : une
# série qui changerait de contenu selon l'année d'où elle vient ne serait pas
# une série. Codes de l'arrêté du 2 mai 2007.
INDICATEURS = {
    "AEP": {
        "D102.0": ("Prix TTC du m³ pour 120 m³", "€/m³"),
        "P104.3": ("Rendement du réseau de distribution", "%"),
        "P107.2": ("Taux moyen de renouvellement des réseaux", "%"),
        "P101.1": ("Taux de conformité microbiologique", "%"),
        "P102.1": ("Taux de conformité physico-chimique", "%"),
        "D101.0": ("Habitants desservis", "hab."),
    },
    "AC": {
        "D204.0": ("Prix TTC du m³ pour 120 m³", "€/m³"),
        "P201.1": ("Taux de desserte par les réseaux de collecte", "%"),
        "P205.3": ("Conformité de la performance des ouvrages d'épuration", "%"),
    },
}

# Le prix est le seul indicateur dont zéro n'est pas une valeur.
INDICATEURS_SANS_ZERO = {"D102.0", "D204.0"}


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sispea_services (
            code_service    TEXT PRIMARY KEY,
            competence      TEXT NOT NULL,          -- AEP | AC
            nom             TEXT,                   -- la collectivité (extractions OFB)
            libelle         TEXT,                   -- le service (composition) : « eau potable »
            type_collectivite TEXT,
            mode_gestion    TEXT,                   -- Régie | Délégation
            siren           TEXT,
            communes        TEXT,                   -- codes INSEE desservis, séparés par des virgules
            operateur       TEXT,                   -- SAUR, VEOLIA… (composition)
            statut_operateur TEXT,                  -- Entreprise privée | SPL | Régie simple…
            debut_gestion   TEXT,                   -- AAAA-MM-JJ, début du mode de gestion
            fin_gestion     TEXT,                   -- AAAA-MM-JJ, fin du contrat de délégation
            annee_operateur TEXT,                   -- année de la composition qui déclare l'opérateur
            derniere_composition INTEGER,           -- dernière année où il dessert une commune du périmètre
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sispea_indicateurs (
            code_service    TEXT NOT NULL REFERENCES sispea_services(code_service),
            annee           INTEGER NOT NULL,
            code            TEXT NOT NULL,
            libelle         TEXT,
            unite           TEXT,
            valeur          REAL,
            origine         TEXT,                   -- hubeau (jusqu'au 10/09/2026) | ofb
            PRIMARY KEY (code_service, annee, code)
        );
        CREATE INDEX IF NOT EXISTS idx_sispea_indicateurs_annee
            ON sispea_indicateurs(annee, code);
    """)
    # Base d'avant les colonnes de l'opérateur (26/10/2026).
    colonnes = {r[1] for r in conn.execute("PRAGMA table_info(sispea_services)")}
    for colonne in CHAMPS_OPERATEUR:
        if colonne not in colonnes:
            conn.execute(f"ALTER TABLE sispea_services ADD COLUMN {colonne} TEXT")
    # Base d'avant le rattachement actuel (09/11/2026).
    if "derniere_composition" not in colonnes:
        conn.execute("ALTER TABLE sispea_services ADD COLUMN derniere_composition INTEGER")
    conn.commit()


def plat(nom: object) -> str:
    """La forme sur laquelle on compare deux en-têtes : sans casse ni accents."""
    sans = unicodedata.normalize("NFD", str(nom if nom is not None else ""))
    sans = "".join(c for c in sans if unicodedata.category(c) != "Mn")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", sans.lower())).strip("_")


def nombre(valeur: object) -> float | None:
    """Un nombre, quelle que soit la façon dont le millésime l'a écrit.

    Le vide s'écrit « . » dans ces classeurs, les nombres sont tantôt des
    nombres et tantôt des chaînes, et la virgule décimale apparaît selon le
    poste qui a produit l'export.
    """
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    texte = str(valeur).strip().replace(",", ".")
    if texte in ("", ".", "-", "nan", "None"):
        return None
    try:
        return float(texte)
    except ValueError:
        return None


def _texte(valeur: object) -> str | None:
    if valeur is None:
        return None
    net = " ".join(str(valeur).split())
    return net if net and net != "." else None


# ── Composition communale : QUI dessert la commune ───────────────────────────

def _insee(valeur: object) -> str | None:
    """Le code INSEE tel que le classeur l'écrit : 1001.0 pour 01001, « 2A004 »."""
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        return f"{int(valeur):05d}"
    texte = _texte(valeur)
    return texte.zfill(5) if texte else None


def _date(valeur: object) -> str | None:
    """La date d'un mode de gestion : le classeur rend un datetime à midi."""
    if hasattr(valeur, "date"):
        return valeur.date().isoformat()
    return _texte(valeur)


def _mode(valeur: object) -> str | None:
    """« Régie à autonomie financière » → « Régie ».

    🔴 La composition détaille le mode, les extractions de l'OFB écrivent
    « Regie » ou « Delegation ». Sous deux formes différentes, `_mieux_ecrit`
    garde la plus récente — l'OFB, sans ses accents. Ramené au mot, le mode
    se compare, et la graphie accentuée l'emporte.
    """
    texte = _texte(valeur)
    if texte and plat(texte).startswith("regie"):
        return "Régie"
    if texte and plat(texte).startswith("delegation"):
        return "Délégation"
    return texte


def composition(competence: str, annee: int) -> dict:
    """Une année de composition, réduite à {communes: {insee: [codes]}, services: {code: identité}}.

    Gardée au magasin national comme les millésimes : le classeur pèse sept
    mégaoctets, la forme réduite sert à toutes les instances. Une erreur
    réseau ou une réponse 404/410 LÈVE : c'est la seule source du rattachement,
    et un rattachement vide n'est pas une collecte.
    """
    reduit = CACHE / "composition" / f"{competence}-{annee}.json"
    if est_frais(reduit, MILLESIME_CACHE_JOURS):
        return json.loads(reduit.read_bytes())

    url = COMPOSITION.format(competence=competence, annee=annee)
    requete = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(requete, timeout=300) as reponse:
        archive = zipfile.ZipFile(BytesIO(reponse.read()))
    classeurs = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
    if len(classeurs) != 1:
        raise RuntimeError(f"{url} : un classeur attendu, trouvé {archive.namelist()}")

    import openpyxl
    cahier = openpyxl.load_workbook(BytesIO(archive.read(classeurs[0])),
                                    read_only=True, data_only=True)
    lignes = cahier.worksheets[0].iter_rows(values_only=True)
    entetes = [plat(x) for x in next(lignes)]
    colonnes = {c: _indice(entetes, (forme,), c, classeurs[0])
                for c, forme in COLONNES_COMPOSITION.items()}
    communes: dict[str, list[str]] = {}
    services: dict[str, dict] = {}
    for ligne in lignes:
        insee, code = _insee(ligne[colonnes["insee"]]), nombre(ligne[colonnes["service"]])
        if not insee or code is None:
            continue
        code = str(int(code))
        if code not in communes.setdefault(insee, []):
            communes[insee].append(code)
        if code not in services:
            siren = nombre(ligne[colonnes["siren"]])
            services[code] = {
                "libelle": _texte(ligne[colonnes["libelle"]]),
                "nom": _texte(ligne[colonnes["nom"]]),
                "type_collectivite": _texte(ligne[colonnes["type_collectivite"]]),
                "mode_gestion": _mode(ligne[colonnes["mode_gestion"]]),
                "siren": str(int(siren)) if siren else None,
                "operateur": _texte(ligne[colonnes["operateur"]]),
                "statut_operateur": _texte(ligne[colonnes["statut_operateur"]]),
                "debut_gestion": _date(ligne[colonnes["debut_gestion"]]),
                "fin_gestion": _date(ligne[colonnes["fin_gestion"]]),
                "annee_operateur": str(annee),
                "communes": [],
            }
        services[code]["communes"].append(insee)
    cahier.close()
    if not communes:
        raise RuntimeError(f"{url} : aucune commune lue — le classeur a changé de forme")
    for fiche in services.values():
        fiche["communes"] = ",".join(sorted(set(fiche["communes"])))
    resultat = {"communes": communes, "services": services}
    ecrire_atomiquement(reduit, json.dumps(resultat, ensure_ascii=False,
                                           separators=(",", ":")).encode())
    return resultat


# ── OFB : les indicateurs, exercice par exercice ─────────────────────────────

def _archives(competence: str) -> dict[int, str]:
    """Les URL des archives, relevées dans le catalogue plutôt que devinées.

    Les noms changent d'une année à l'autre — `…_2022_AEP_260124_Rapport_SISPEA.7z`
    puis `…_2024_AEP.7z`. Les composer à la main aurait fait échouer la collecte
    au prochain changement de convention, et ce producteur en change souvent.

    ⚠️ Le catalogue écrit `filename`, pas `fileName` : la mauvaise graphie ne
    lève rien, elle rend une liste VIDE. D'où le refus explicite plus bas.
    """
    reglage = COMPETENCES[competence]
    url = CATALOGUE.format(record=reglage["record"])
    requete = urllib.request.Request(url, headers={**HEADERS, "accept": "application/json"})
    with urllib.request.urlopen(requete, timeout=60) as reponse:
        pieces = json.load(reponse)
    pieces = pieces if isinstance(pieces, list) else pieces.get("items", [])
    mesure.lu(f"ofb-catalogue-{competence}", len(pieces), mesure.forme_de(pieces),
              complete=True)
    trouvees: dict[int, str] = {}
    for piece in pieces:
        nom = str(piece.get("filename") or "")
        if not nom.endswith(".7z") or reglage["marque"] not in nom:
            continue
        for annee in MILLESIMES:
            if f"_{annee}_" in nom or nom.endswith(f"_{annee}{reglage['marque']}.7z"):
                trouvees[annee] = piece["url"]
    if not trouvees:
        raise RuntimeError(
            f"aucune archive {reglage['marque']} dans le catalogue de l'OFB ({url}) — "
            "le catalogue a changé de forme ; une collecte muette serait pire")
    return trouvees


def _format_reel(chemin) -> str:
    """Le format d'après SES OCTETS, jamais d'après son nom."""
    tete = chemin.open("rb").read(4)
    if tete[:2] == b"PK":
        return "xlsx"
    if tete == b"\xd0\xcf\x11\xe0":
        return "xls"
    raise RuntimeError(f"{chemin.name} : ni xlsx ni xls (octets de tête {tete!r})")


def _lignes_du_classeur(chemin):
    """Les lignes du classeur, en-têtes normalisés, quel que soit son format."""
    if _format_reel(chemin) == "xlsx":
        import openpyxl
        # 🔴 On passe un FLUX : `openpyxl` refuse d'après l'EXTENSION — « does
        # not support the old .xls file format » — sans regarder le contenu.
        with chemin.open("rb") as flux:
            cahier = openpyxl.load_workbook(flux, read_only=True, data_only=True)
            feuille = cahier.worksheets[0]
            iterateur = feuille.iter_rows(values_only=True)
            entetes = [plat(x) for x in next(iterateur)]
            for ligne in iterateur:
                yield entetes, ligne
            cahier.close()
        return
    try:
        import xlrd
    except ModuleNotFoundError as absent:
        raise RuntimeError(
            "xlrd manque, et l'un des millésimes est un classeur d'ancienne "
            "génération : pip install xlrd") from absent
    cahier = xlrd.open_workbook(str(chemin))
    feuille = cahier.sheet_by_index(max(0, cahier.nsheets - 1))
    entetes = [plat(feuille.cell_value(0, j)) for j in range(feuille.ncols)]
    for i in range(1, feuille.nrows):
        yield entetes, [feuille.cell_value(i, j) for j in range(feuille.ncols)]


def _indice(entetes: list[str], noms: tuple[str, ...], quoi: str, nom_fichier: str) -> int:
    for nom in noms:
        if nom in entetes:
            return entetes.index(nom)
    raise RuntimeError(
        f"{nom_fichier} : colonne « {quoi} » introuvable sous {noms}. "
        f"En-têtes lus : {entetes[:24]}. Le producteur a changé de convention : "
        "ajouter l'alias dans COLONNES ou INDICATEURS.")


def _extraire_archive(archive):
    """Le classeur contenu dans l'archive 7z, extrait à côté d'elle.

    Extrait dans un dossier voisin, puis renommé : un lecteur qui arrive
    pendant l'extraction ne voit jamais un classeur à moitié écrit. Quand le
    magasin refuse l'écriture, l'extraction se fait dans un temporaire, que
    `millesime` efface après lecture.
    """
    dossier = archive.with_suffix("")
    deja = [c for c in dossier.iterdir() if c.suffix.lower() in (".xls", ".xlsx")] \
        if dossier.is_dir() else []
    if deja:
        return deja[0]
    if shutil.which("bsdtar") is None:
        raise RuntimeError(
            "bsdtar est introuvable et les extractions de l'OFB sont des archives 7z "
            "(macOS le fournit ; Debian : apt install libarchive-tools)")
    partage = magasin_ecrivable(archive.parent, dossier.name)
    voisin = Path(tempfile.mkdtemp(prefix=f".{dossier.name}.", suffix=".part",
                                   dir=archive.parent if partage else None))
    if partage:
        # `mkdtemp` crée en 0700 : renommé tel quel, le dossier restait fermé aux
        # lecteurs du groupe.
        os.chmod(voisin, 0o2750)
    try:
        subprocess.run(["bsdtar", "-xf", str(archive), "-C", str(voisin)], check=True)
        if not any(c.suffix.lower() in (".xls", ".xlsx") for c in voisin.iterdir()):
            raise RuntimeError(f"{archive.name} ne contient aucun classeur")
        if partage:
            shutil.rmtree(dossier, ignore_errors=True)
            os.replace(voisin, dossier)
        else:
            dossier = voisin
    except BaseException:
        shutil.rmtree(voisin, ignore_errors=True)
        raise
    return next(c for c in dossier.iterdir() if c.suffix.lower() in (".xls", ".xlsx"))


def millesime(competence: str, annee: int, url: str) -> dict[str, dict]:
    """Un exercice entier, réduit aux colonnes retenues, et gardé au magasin.

    Le fichier réduit pèse un mégaoctet là où le classeur en pèse quatorze, et
    il est PARTAGÉ : la deuxième instance qui collecte ne relit rien.
    """
    reduit = CACHE / "millesimes" / f"{competence}-{annee}.json"
    if est_frais(reduit, MILLESIME_CACHE_JOURS):
        return json.loads(reduit.read_bytes())
    if (not magasin_ecrivable(reduit.parent, reduit.name)
            and perime_lisible(reduit, MILLESIME_CACHE_JOURS)):
        return json.loads(reduit.read_bytes())

    archive = CACHE / "archives" / urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    temporaire = None
    if not est_frais(archive, None):
        requete = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(requete, timeout=300) as reponse:
            contenu = reponse.read()
        if not ecrire_atomiquement(archive, contenu):
            # Magasin en lecture seule : l'archive se lit hors du magasin.
            # L'ignorer faisait manquer le millésime sans erreur de step.
            temporaire = Path(tempfile.mkdtemp(prefix="vigie-sispea-"))
            archive = temporaire / archive.name
            archive.write_bytes(contenu)

    try:
        return _reduire_millesime(competence, archive, reduit)
    finally:
        if temporaire is not None:
            shutil.rmtree(temporaire, ignore_errors=True)


def _reduire_millesime(competence: str, archive: Path, reduit: Path) -> dict[str, dict]:
    classeur = _extraire_archive(archive)
    services: dict[str, dict] = {}
    colonnes: dict[str, int] = {}
    entetes_vus: list[str] = []
    for entetes, ligne in _lignes_du_classeur(classeur):
        if entetes is not entetes_vus:
            entetes_vus = entetes
            colonnes = {c: _indice(entetes, formes, c, classeur.name)
                        for c, formes in COLONNES.items()
                        if c != "gestion" or competence == "AEP"}
            colonnes |= {code: _indice(entetes, (plat(code),), code, classeur.name)
                         for code in INDICATEURS[competence]}
        identifiant = nombre(ligne[colonnes["service"]])
        if identifiant is None:
            continue
        fiche = {
            "nom": _texte(ligne[colonnes["collectivite"]]),
            "type_collectivite": _texte(ligne[colonnes["type"]]),
            "mode_gestion": _texte(ligne[colonnes["gestion"]]) if competence == "AEP" else None,
            "indicateurs": {},
        }
        for code in INDICATEURS[competence]:
            valeur = nombre(ligne[colonnes[code]])
            if valeur is None or (valeur == 0 and code in INDICATEURS_SANS_ZERO):
                continue
            fiche["indicateurs"][code] = valeur
        services[str(int(identifiant))] = fiche

    if ecrire_atomiquement(
            reduit, json.dumps(services, ensure_ascii=False, separators=(",", ":")).encode()):
        # Le classeur de quatorze mégaoctets ne sert plus : le fichier réduit en
        # pèse un et dit la même chose. L'archive, elle, reste — c'est la pièce
        # d'origine, et la reconstruire depuis elle doit rester possible.
        shutil.rmtree(classeur.parent, ignore_errors=True)
    elif classeur.parent.name.endswith(".part"):
        # Extrait hors du magasin : rien ne le réutilisera.
        shutil.rmtree(classeur.parent, ignore_errors=True)
    return services


# ── Écriture ─────────────────────────────────────────────────────────────────

def _mieux_ecrit(neuf: str | None, ancien: str | None) -> str | None:
    """Entre deux graphies d'une même valeur, celle qui porte ses accents.

    🔴 Hub'Eau écrivait « Régie » et « Délégation », les extractions de l'OFB
    écrivent « Regie » et « Delegation ». La source qui passe en dernier
    l'emportant, la base se remplissait de mots français sans leurs accents —
    et deux services d'une même commune s'affichaient sous deux graphies du
    même mot.

    La règle ne réécrit rien qu'on n'ait pas vu écrit : à forme normalisée
    identique on garde la mieux accentuée, sinon la plus récente l'emporte,
    parce qu'une valeur qui CHANGE est une information.
    """
    if not neuf:
        return ancien
    if not ancien or plat(neuf) != plat(ancien):
        return neuf
    accentuation = lambda mot: sum(1 for c in mot if ord(c) > 127)  # noqa: E731
    return neuf if accentuation(neuf) >= accentuation(ancien) else ancien


def _ecrire_service(conn, code: str, competence: str, identite: dict) -> None:
    """Enregistre le service, en complétant ce qui manque sans écraser ce qui est.

    Les deux sources ne portent pas les mêmes champs : la composition donne le
    SIREN et la liste des communes desservies, l'OFB les indicateurs et parfois
    un autre nom de collectivité. Un `INSERT OR REPLACE` ferait perdre à chaque passage ce que
    l'autre source avait apporté.
    """
    completes = ("nom", "libelle", "type_collectivite", "mode_gestion", "siren", "communes")
    champs = (*completes, *CHAMPS_OPERATEUR, "derniere_composition")
    ancien = conn.execute(
        f"SELECT {', '.join(champs)} FROM sispea_services WHERE code_service=?",
        (code,)).fetchone()
    valeurs = {c: ancien[c] if ancien else None for c in champs}
    valeurs |= {c: _mieux_ecrit(identite.get(c), valeurs[c]) for c in completes}
    # L'opérateur, lui, n'est complété par personne : la composition est sa
    # seule source, et son VIDE dit quelque chose. Un service repassé en régie
    # garderait sinon le nom de son ancien délégataire. De même l'année de la
    # dernière composition : seule la composition la connaît, l'OFB n'y touche pas.
    valeurs |= {c: identite[c] for c in (*CHAMPS_OPERATEUR, "derniere_composition")
                if c in identite}
    if ancien:
        conn.execute(
            f"UPDATE sispea_services SET {', '.join(f'{c}=?' for c in champs)}"
            " WHERE code_service=?", (*[valeurs[c] for c in champs], code))
    else:
        conn.execute(
            f"INSERT INTO sispea_services (code_service, competence, {', '.join(champs)})"
            f" VALUES ({', '.join('?' * (len(champs) + 2))})",
            (code, competence, *[valeurs[c] for c in champs]))


def _ecrire_indicateur(conn, code_service: str, annee: int, competence: str,
                       code: str, valeur: float, origine: str) -> bool:
    """Écrit une mesure, sauf si elle y est déjà. Rend True si elle est neuve.

    ⚠️ Les deux sources se recouvrent sur 2015-2019. La clé primaire fait le
    tri, et l'OFB passe APRÈS : à valeur discordante, c'est le fichier publié
    par le producteur qui fait foi sur l'API qui le republie.
    """
    libelle, unite = INDICATEURS[competence][code]
    if origine == "hubeau":
        curseur = conn.execute(
            "INSERT OR IGNORE INTO sispea_indicateurs"
            " (code_service, annee, code, libelle, unite, valeur, origine)"
            " VALUES (?,?,?,?,?,?,?)",
            (code_service, annee, code, libelle, unite, valeur, origine))
    else:
        curseur = conn.execute(
            "INSERT INTO sispea_indicateurs"
            " (code_service, annee, code, libelle, unite, valeur, origine)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(code_service, annee, code) DO UPDATE SET"
            " valeur=excluded.valeur, origine=excluded.origine",
            (code_service, annee, code, libelle, unite, valeur, origine))
    return curseur.rowcount > 0


def _moisson_composition() -> dict[str, tuple[str, dict]]:
    """Les services qui ont desservi les communes du périmètre, AVANT d'ouvrir la base.

    Le réseau d'abord, base fermée, l'écriture ensuite (cf. la leçon de
    `archive_fetch` et du « database is locked »). Une commune sans aucun
    service d'eau potable sur toutes les années lues n'existe pas : c'est la
    source qui a changé, et on le dit en échouant.
    """
    services: dict[str, tuple[str, dict]] = {}
    communes = communes_du_step("sispea")
    for competence in COMPETENCES:
        trouvees: set[str] = set()
        for annee in COMPOSITIONS:
            annee_lue = composition(competence, annee)
            # Communes de France de la composition, neuve ou du magasin : la
            # forme réduite est la nôtre, seule la quantité dit la source.
            mesure.lu(f"sispea-composition-{competence}", len(annee_lue["communes"]),
                      complete=True)
            for insee in communes:
                for code in annee_lue["communes"].get(insee, []):
                    trouvees.add(insee)
                    # L'année la plus récente l'emporte : c'est l'identité
                    # d'aujourd'hui. `derniere_composition` dit si ce service
                    # dessert ENCORE la commune : la régie de Lasalle, reprise
                    # par la communauté de communes, s'arrête à 2022 quand le
                    # service de la CC va jusqu'à la dernière année lue. La
                    # tuile de l'accueil ne garde que ces derniers ; la page
                    # /environnement garde tout, c'est l'historique.
                    services[code] = (competence, {**annee_lue["services"][code],
                                                   "derniere_composition": annee})
        manquantes = sorted(set(communes) - trouvees)
        if competence == "AEP" and manquantes:
            raise RuntimeError(
                f"aucun service d'eau potable pour {', '.join(manquantes)} dans la composition "
                f"communale {COMPOSITIONS[0]}-{COMPOSITIONS[-1]} — la source a changé de forme")
    return services


def completer_par_millesimes(conn, connus: dict[str, str], releve: dict) -> str | None:
    """Les indicateurs des exercices publiés par l'OFB, et rend la cause d'un échec.

    Ces extractions demandent un téléchargement national, `bsdtar` pour ouvrir
    une archive 7z, et `xlrd` pour l'un des millésimes. Elles n'emportent pas
    le rattachement déjà écrit ; mais depuis la fin de Hub'Eau elles sont la
    SEULE source d'indicateurs, et `run_sispea` fait échouer le step après
    avoir écrit les services.
    """
    try:
        for competence in COMPETENCES:
            interessants = {c for c, comp in connus.items() if comp == competence}
            if not interessants:
                continue
            for annee, url in sorted(_archives(competence).items()):
                exercice = millesime(competence, annee, url)
                # Services de France, tous exercices du catalogue sommés.
                mesure.lu(f"ofb-indicateurs-{competence}", len(exercice or {}),
                          complete=True)
                if not exercice:
                    continue
                releve["millesimes"] += 1
                for code in interessants & set(exercice):
                    fiche = exercice[code]
                    _ecrire_service(conn, code, competence, fiche)
                    for indicateur, valeur in fiche["indicateurs"].items():
                        if _ecrire_indicateur(conn, code, annee, competence,
                                              indicateur, valeur, "ofb"):
                            releve["mesures"] += 1
                            releve["annees"].add(annee)
    except Exception as erreur:  # noqa: BLE001 — le complément échoue, la série reste
        motif = f"{type(erreur).__name__}: {erreur}"
        print(f"  [sispea] exercices récents indisponibles — {motif}")
        releve["millesimes_erreur"] = motif[:300]
        return motif
    return None


def run_sispea(avec_millesimes: bool = True) -> dict:
    releve = {"services": 0, "mesures": 0, "millesimes": 0, "annees": set()}
    moisson = _moisson_composition()
    with get_conn() as conn:
        ensure_tables(conn)
        # Journal tenu par `run_all.run_step` — s'y inscrire ici aussi
        # laissait deux lignes par passe.
        connus: dict[str, str] = {}
        for code, (competence, identite) in moisson.items():
            connus[code] = competence
            releve["services"] += 1
            _ecrire_service(conn, code, competence, identite)

        if avec_millesimes and connus:
            completer_par_millesimes(conn, connus, releve)
        conn.commit()
    if releve.get("millesimes_erreur"):
        raise RuntimeError(f"{releve['services']} service(s) rattaché(s), mais aucun "
                           f"indicateur : {releve['millesimes_erreur']}")
    return releve


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parseur.add_argument("--stats", action="store_true",
                         help="compte ce qui est en base, sans rien collecter")
    parseur.add_argument("--sans-millesimes", action="store_true",
                         help="rattachement seul : pas d'indicateurs de l'OFB")
    arguments = parseur.parse_args()

    if arguments.stats:
        with get_conn(read_only=True) as conn:
            for ligne in conn.execute(
                    "SELECT s.competence, s.code_service, s.nom, s.mode_gestion,"
                    " MIN(i.annee), MAX(i.annee), COUNT(*)"
                    " FROM sispea_services s LEFT JOIN sispea_indicateurs i"
                    " ON i.code_service = s.code_service"
                    " GROUP BY s.code_service ORDER BY s.competence, s.code_service"):
                comp, code, nom, gestion, debut, fin, combien = ligne
                print(f"  {comp:4} {code:>8} {str(nom)[:34]:36} {str(gestion or ''):11}"
                      f" {debut}-{fin}  {combien} mesures")
        return 0

    releve = run_sispea(avec_millesimes=not arguments.sans_millesimes)
    annees = sorted(releve["annees"])
    print(f"SISPEA — {COMMUNE_INSEE} : {releve['services']} service(s), "
          f"{releve['mesures']} mesure(s) neuve(s), {releve['millesimes']} millésime(s) lu(s)"
          + (f", exercices {annees[0]}-{annees[-1]}" if annees else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
