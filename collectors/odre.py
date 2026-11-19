"""
odre.py — Installations de production et de stockage d'électricité de la
commune : solaire, éolien, hydraulique… (registre national, ODRÉ / RTE).

RTE tient le registre de toute installation raccordée au réseau public (code de
l'énergie, art. L142-9-1), au dernier jour d'un mois, et le publie sur Open Data
Réseaux Énergies. C'est la version NATIONALE (`…-agrege`), pas les déclinaisons
d'outre-mer publiées à part. Le dossier énergie y lit ce que la commune produit,
par filière, et peut relier une centrale à la délibération qui l'a autorisée
(date de mise en service, puissance).

─────────────────────────────────────────────────────────────────────────────
AUCUN NOM D'INSTALLATION

Sous 36 kW, le registre agrège déjà les installations par commune
(« Agrégation des installations de moins de 36KW », avec leur nombre). Au-dessus,
`nominstallation` peut porter le nom de l'exploitant : un toit photovoltaïque
de hangar agricole porte celui de l'agriculteur. Le nom n'est pas lu ; le code
EIC, la filière, la puissance et les dates suffisent à rapprocher un acte.
Il n'est même pas DEMANDÉ : `fetch_json` archive la réponse brute, et un nom
reçu entrerait dans `raw_documents`. L'API rend les seuls champs de `CHAMPS`
(`select`), et l'agrégat se reconnaît par une requête à part
(`startswith(nominstallation, …)` côté serveur).

LA COMMUNE DU RACCORDEMENT N'EST PAS TOUJOURS CELLE DE L'IMPLANTATION

`codeinseecommune` est la commune de rattachement, `codeinseecommuneimplantation`
celle où se trouve l'installation quand elle diffère (un parc éolien raccordé
au poste du village voisin). Une ligne est retenue si l'une OU l'autre est une
commune suivie, et les deux codes sont gardés.

UNE COMMUNE SANS LIGNE N'EST PAS UNE LACUNE

Le registre couvre les réseaux de RTE, d'Enedis, d'EDF SEI et des régies qui
lui transmettent leurs données ; les autres gestionnaires y entrent « au fur et
à mesure » (fiche du jeu). Une commune sans ligne n'y a donc aucune installation
déclarée, ce que la page dit en nommant cette couverture. Elle n'est pas notée
dans `sources_absentes` : le jeu la couvre, il n'y trouve rien.

Source : ODRÉ (RTE, Enedis, ELD), jeu
`registre-national-installation-production-stockage-electricite-agrege`,
Licence Ouverte v2. Interrogé par l'API Explore, commune par commune : le
fichier national n'est pas téléchargé.

Usage :
  python3 -m collectors.odre
  python3 -m collectors.odre --stats
"""
import argparse
import re
import urllib.parse

from .archive import fetch_json
from .config import communes_du_step
from .db import get_conn, transaction

SOURCE = "odre"
JEU = "Registre national des installations de production et de stockage d'électricité (ODRÉ)"
DATASET = "registre-national-installation-production-stockage-electricite-agrege"
API = f"https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{DATASET}"
PAGE_JEU = f"https://odre.opendatasoft.com/explore/dataset/{DATASET}/"

# Champs lus. `nominstallation` n'y est pas, à dessein (cf. docstring).
CHAMPS = {
    "codeeicresourceobject": "code_eic",
    "codeinseecommune": "insee",
    "codeinseecommuneimplantation": "insee_implantation",
    "filiere": "filiere",
    "technologie": "technologie",
    "typestockage": "type_stockage",
    "puismaxinstallee": "puissance_kw",
    "nbinstallations": "nb_installations",
    "datemiseenservice_date": "mise_en_service",
    "tensionraccordement": "tension",
    "regime": "regime",
    "energieannuelleglissanteinjectee": "energie_injectee_kwh",
    "energieannuelleglissanteproduite": "energie_produite_kwh",
}
AGREGAT = "Agrégation des installations"
_DATE_TITRE = re.compile(r"au (\d{2})/(\d{2})/(\d{4})")


def date_du_registre(titre: str) -> str | None:
    """« … (au 31/08/2026) » → « 2026-08-31 »."""
    m = _DATE_TITRE.search(titre or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def lire(enregistrements: list[dict], agregat: bool) -> list[dict]:
    """Une ligne par enregistrement, aux seuls champs de `CHAMPS`."""
    return [{**{nouveau: e.get(ancien) for ancien, nouveau in CHAMPS.items()},
             "agregat_moins_36kw": int(agregat)} for e in enregistrements]


_INSEE = re.compile(r"^(?:\d{2}|2[AB])\d{3}$")


def _url(codes: list[str], agregat: bool, offset: int) -> str:
    # Les codes entrent dans une requête ODSQL entre guillemets : un code qui
    # n'en est pas un est refusé avant d'y entrer.
    if not all(_INSEE.match(c) for c in codes):
        raise ValueError(f"ODRÉ : code INSEE invalide dans {codes}")
    liste = ", ".join(f'"{c}"' for c in codes)
    test = f'startswith(nominstallation, "{AGREGAT}")'
    params = {
        "where": (f"(codeinseecommune in ({liste}) or codeinseecommuneimplantation in ({liste}))"
                  f" and {'' if agregat else 'not '}{test}"),
        "select": ", ".join(CHAMPS),
        "limit": 100,
        "offset": offset,
        "order_by": "datemiseenservice_date",
    }
    return f"{API}/records?{urllib.parse.urlencode(params)}"


def _tout(codes: list[str], agregat: bool) -> list[dict]:
    enregistrements, offset = [], 0
    while True:
        page = fetch_json(_url(codes, agregat, offset), source=SOURCE, timeout=60,
                          complete=True)
        lot = page.get("results") or []
        enregistrements += lot
        offset += len(lot)
        if not lot or offset >= (page.get("total_count") or 0):
            return lire(enregistrements, agregat)


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS odre_installations (
            rang                 INTEGER NOT NULL,   -- rang dans la réponse, faute d'identifiant pour les agrégats
            insee                TEXT NOT NULL,      -- commune de rattachement
            insee_implantation   TEXT,               -- quand elle diffère
            code_eic             TEXT,
            filiere              TEXT,
            technologie          TEXT,
            type_stockage        TEXT,
            puissance_kw         REAL,
            nb_installations     INTEGER,
            agregat_moins_36kw   INTEGER NOT NULL,   -- 1 : ligne d'agrégat du registre
            mise_en_service      TEXT,
            tension              TEXT,
            regime               TEXT,
            energie_injectee_kwh REAL,               -- sur douze mois glissants
            energie_produite_kwh REAL,
            date_registre        TEXT,               -- dernier jour du mois que le registre arrête
            source_url           TEXT,
            releve_le            TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date_registre, rang)
        )
    """)
    conn.commit()


def run() -> int:
    codes = sorted(communes_du_step(SOURCE))
    meta = fetch_json(API, source=SOURCE, timeout=60)
    date_registre = date_du_registre(((meta.get("metas") or {}).get("default") or {})
                                     .get("title", ""))
    if not date_registre:
        raise RuntimeError("ODRÉ : date du registre introuvable dans le titre du jeu")
    lignes = _tout(codes, agregat=True) + _tout(codes, agregat=False)
    # Le registre écrit parfois une chaîne vide là où il n'y a rien : vide = absent.
    for l in lignes:
        l["insee_implantation"] = l["insee_implantation"] or None
    trouvees = {l["insee"] for l in lignes} | {l["insee_implantation"] for l in lignes}
    with transaction() as conn:
        ensure_table(conn)
        # Le registre se relit en entier : une installation démantelée en sort.
        conn.execute("DELETE FROM odre_installations")
        conn.executemany(
            f"INSERT INTO odre_installations (rang, {', '.join(CHAMPS.values())},"
            " agregat_moins_36kw, date_registre, source_url)"
            f" VALUES (?, {', '.join('?' * len(CHAMPS))}, ?, ?, ?)",
            [(i, *l.values(), date_registre, PAGE_JEU) for i, l in enumerate(lignes)])
    print(f"  [odre] {len(lignes)} ligne(s) au {date_registre}"
          f" sur {len(trouvees & set(codes))}/{len(codes)} commune(s)")
    return len(lignes)


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, filiere, COUNT(*) n, SUM(COALESCE(nb_installations, 1)) inst,
               ROUND(SUM(puissance_kw)) kw, MAX(date_registre) au
          FROM odre_installations GROUP BY insee, filiere ORDER BY insee, kw DESC
    """):
        print(f"  {r['insee']}  {r['filiere'] or '—':<14} {r['inst']:>4} installation(s)"
              f"  {r['kw']:>8} kW  (registre au {r['au']})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run()
