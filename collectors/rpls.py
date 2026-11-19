"""
rpls.py — Logements sociaux de la commune, comptés par nombre de pièces,
financement, étiquette énergétique et décennie de construction (répertoire RPLS,
SDES).

Chaque bailleur social déclare au 1er janvier chacun de ses logements ; le SDES
publie le répertoire AU LOGEMENT. « Se loger » n'en a besoin que par commune :
combien, de quelle taille, financés comment (PLAI, PLUS, PLS…), dans quel état
énergétique.

─────────────────────────────────────────────────────────────────────────────
AUCUNE ADRESSE N'ENTRE

Le fichier porte la voie, le numéro, l'ÉTAGE et les coordonnées de chaque
logement : dans un village de trente logements sociaux, c'est désigner un
foyer. L'API DiDo rend les seules colonnes demandées (`COLONNES`), filtrées
par commune côté serveur ; le collecteur compte, et ne garde que les comptes.
Le fichier national (5,4 millions de lignes) n'est pas téléchargé.

UN MILLÉSIME PAR PASSE

DiDo sert un millésime à la fois (`2025-01` = au 1er janvier 2025). Le filtre par
commune ne répond que sur le dernier (les précédents nomment la colonne
`DEPCOM_CODE` et rendent une page d'erreur) : la série se constitue en base,
millésime après millésime, la clé portant le millésime.

UNE COMMUNE SANS LIGNE N'EST PAS UNE LACUNE

Le répertoire est exhaustif (obligation des bailleurs, CCH art. L411-10) : une
commune absente n'a aucun logement social déclaré. C'est un fait, noté comme un
total à zéro, pas une absence de la source.

Source : SDES, « Données détaillées au logement du répertoire des logements
locatifs des bailleurs sociaux (RPLS) », jeu repris sur data.gouv.fr, Licence
Ouverte v1.0 (`fr-lo` au catalogue).

Usage :
  python3 -m collectors.rpls
  python3 -m collectors.rpls --stats
"""
import argparse
import csv
import io
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from . import mesure
from .archive import HEADERS, fetch_json
from .config import communes_du_step
from .db import get_conn, transaction

SOURCE = "rpls"
RID = "f3c2f2cb-8fb1-40fd-8733-964247744c9a"
API = f"https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles/{RID}"
PAGE_JEU = ("https://www.data.gouv.fr/datasets/"
            "donnees-detaillees-au-logement-du-repertoire-des-logements-locatifs-des-bailleurs-sociaux-rpls")

# Les seules colonnes demandées : aucune ne situe un logement dans la commune.
COLONNES = ("NBPIECE", "SURFHAB", "CONSTRUCT", "FINAN_CODE", "FINAN_LIBELLE", "DPEENERGIE")
TRANCHES_SURFACE = ((30, "moins de 30 m²"), (50, "30 à 49 m²"), (70, "50 à 69 m²"),
                    (90, "70 à 89 m²"))


def _surface(brut: str) -> str | None:
    try:
        m2 = float(brut)
    except ValueError:
        return None
    return next((libelle for borne, libelle in TRANCHES_SURFACE if m2 < borne), "90 m² et plus")


def _pieces(brut: str) -> str | None:
    try:
        n = int(brut)
    except ValueError:
        return None
    return "5 et plus" if n >= 5 else str(n)


def _decennie(brut: str) -> str | None:
    return f"{brut[:3]}0" if len(brut) == 4 and brut.isdigit() else None


def compter(texte: str, colonne_commune: str) -> dict[str, Counter]:
    """Par commune : logements comptés par (dimension, modalité)."""
    comptes: dict[str, Counter] = {}
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    lues = 0
    for rang in lecteur:
        lues += 1
        insee = (rang.get(colonne_commune) or "").strip().zfill(5)
        c = comptes.setdefault(insee, Counter())
        c[("total", "")] += 1
        for dimension, modalite in (
            ("pieces", _pieces((rang.get("NBPIECE") or "").strip())),
            ("surface", _surface((rang.get("SURFHAB") or "").strip())),
            ("construction", _decennie((rang.get("CONSTRUCT") or "").strip())),
            ("financement", (rang.get("FINAN_LIBELLE") or "").strip() or None),
            ("dpe", (rang.get("DPEENERGIE") or "").strip() or None),
        ):
            c[(dimension, modalite or "non renseigné")] += 1
    # Le millésime du périmètre, filtré par DiDo : un 400 « fichier vide »
    # (aucun logement social) rend l'en-tête seul, donc zéro.
    mesure.lu(SOURCE, lues, lecteur.fieldnames, complete=True)
    return comptes


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rpls_communes (
            insee       TEXT NOT NULL,
            millesime   TEXT NOT NULL,   -- « 2025-01 » : au 1er janvier 2025
            dimension   TEXT NOT NULL,   -- total | pieces | surface | construction | financement | dpe
            modalite    TEXT NOT NULL,   -- « » pour le total
            logements   INTEGER NOT NULL,
            source_url  TEXT,
            releve_le   TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, millesime, dimension, modalite)
        )
    """)
    conn.commit()


_INSEE = re.compile(r"^(?:\d{2}|2[AB])\d{3}$")


def run() -> int:
    codes = sorted(communes_du_step(SOURCE))
    if not all(_INSEE.match(c) for c in codes):
        raise ValueError(f"RPLS : code INSEE invalide dans {codes}")
    meta = fetch_json(API, source=SOURCE, timeout=60)
    millesime = meta["millesime"]
    noms = {c["name"] for c in meta.get("columns", [])}
    colonne_commune = "DEPCOM" if "DEPCOM" in noms else "DEPCOM_CODE"
    manquantes = [c for c in COLONNES if c not in noms]
    if manquantes:
        raise RuntimeError(f"RPLS {millesime} : colonne(s) disparue(s) {manquantes}")
    url = f"{API}/csv?" + urllib.parse.urlencode({
        "millesime": millesime,
        "withColumnName": "true",
        colonne_commune: "in:" + ",".join(codes),
        "columns": ",".join((colonne_commune, *COLONNES)),
    })
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            texte = r.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        # DiDo répond 400 « Le fichier est vide. » quand aucune des communes
        # n'a de logement social (27222 et 27334, 31/10) : ce n'est pas une panne.
        if e.code != 400 or "fichier est vide" not in e.read().decode("utf-8", "replace"):
            raise
        texte = f'"{colonne_commune}"\n'
    if not texte.startswith(f'"{colonne_commune}"'):
        raise RuntimeError(f"RPLS {millesime} : réponse inattendue ({texte[:60]!r})")
    # Le corps n'est pas archivé dans `raw_documents` : seuls les comptes restent.
    comptes = compter(texte, colonne_commune)
    with transaction() as conn:
        ensure_table(conn)
        conn.execute(f"DELETE FROM rpls_communes WHERE millesime=? AND insee IN"
                     f" ({', '.join('?' * len(codes))})", (millesime, *codes))
        for insee in codes:
            # Une commune absente du répertoire n'a aucun logement social : total 0.
            c = comptes.get(insee) or Counter({("total", ""): 0})
            conn.executemany(
                "INSERT INTO rpls_communes (insee, millesime, dimension, modalite,"
                " logements, source_url) VALUES (?,?,?,?,?,?)",
                [(insee, millesime, d, m, n, PAGE_JEU) for (d, m), n in c.items()])
    total = sum(c[("total", "")] for c in comptes.values())
    print(f"  [rpls] {total} logement(s) social(aux) au {millesime}"
          f" sur {len(comptes)}/{len(codes)} commune(s)")
    return total


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, millesime, dimension, modalite, logements
          FROM rpls_communes ORDER BY insee, millesime, dimension, modalite
    """):
        print(f"  {r['insee']} {r['millesime']}  {r['dimension']:<13} {r['modalite']:<20}"
              f" {r['logements']:>5}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run()
