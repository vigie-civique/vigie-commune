"""
fiscalite.py — Taux d'imposition locaux votés (data.economie.gouv.fr, ODS).

**Pourquoi.** La base connaissait le budget (DGFiP) et les comparatifs (OFGL),
mais pas les **taux votés** ni la pression fiscale — alors que « de combien ont
augmenté mes impôts locaux, et comment on se situe » est la première question
que se pose un habitant. Le chiffre existe, gratuit, à la commune, depuis 2021.

Deux jeux ODS complémentaires, 174 668 enregistrements chacun :
  - `fiscalite-locale-des-particuliers` : TFB, TFNB, TH résiduelle, TEOM
  - `fiscalite-locale-des-entreprises`  : CFE (hors zone / ZAE / éolien)

**Voie écartée.** Le fichier REI (Recensement des Éléments d'Imposition) est la
source primaire mais n'est diffusé qu'en **ZIP annuels** à colonnes codées, à
parser intégralement. Ces deux jeux ODS en sont l'exposition exploitable, sur la
même API que `budget.py` — et ils portent déjà les taux *globaux* (commune +
EPCI + syndicats), qui sont ce que le contribuable paie réellement.

Distinction à ne jamais perdre à l'affichage :
  - `*_vote`  = la part votée par la commune → sa responsabilité politique
  - `taux_global_*` = ce que paie le contribuable, EPCI et syndicats inclus
Attribuer le taux global au conseil municipal serait une erreur factuelle.

**Situer un taux.** « 19,92 % » ne dit rien seul. Pour les indicateurs de
`REPERES`, la même API rend la médiane des communes qui lèvent la taxe et le
nombre de celles dont le taux est au moins aussi élevé — en France et dans le
département — pour chaque commune suivie en profondeur (`fiscalite_reperes`).
Une commune sans taux (la redevance remplace alors la taxe) n'a pas de repère :
l'absence de ligne n'est pas un rang.

**Avant 2021.** En 2021, la part départementale de foncier bâti est passée aux
communes (suppression de la taxe d'habitation sur les résidences principales) :
le taux communal voté « bondit » du taux départemental de 2020 sans hausse pour
le contribuable. Le montrer demande le taux communal d'avant et le taux du
département en 2020, que les jeux ODS ci-dessus n'ont pas. Ils sont dans les
**comptes individuels** des communes et des départements (DGFiP, même API,
Licence Ouverte) : colonne `tfb`, « Taux voté (%) » selon la maquette jointe à
chaque jeu. Voie retenue plutôt que le REI : mêmes taux, même API, sans ZIP.
Le taux départemental a sa propre portée (`departement`) : comme le taux
global, il ne s'attribue jamais au conseil municipal.

Usage :
  python3 -m collectors.fiscalite
  python3 -m collectors.fiscalite --insee 30140
  python3 -m collectors.fiscalite --stats
"""
from __future__ import annotations

import argparse
import json
import urllib.parse

from .archive import fetch_json
from .config import COMMUNES, COMMUNES_FOND_INSEE, COMMUNES_INSEE
from .db import get_conn

ODS_BASE = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"

# (dataset, colonne ODS, indicateur, libellé, portée)
# portee : 'commune' = part votée par le conseil municipal ;
#          'global'  = total acquitté par le contribuable (commune+EPCI+syndicats).
INDICATEURS = [
    ("fiscalite-locale-des-particuliers", "e12vote",         "TFB_VOTE",   "Taxe foncière bâti — taux voté par la commune", "commune"),
    ("fiscalite-locale-des-particuliers", "taux_global_tfb", "TFB_GLOBAL", "Taxe foncière bâti — taux global acquitté",     "global"),
    ("fiscalite-locale-des-particuliers", "b12vote",         "TFNB_VOTE",  "Taxe foncière non bâti — taux voté par la commune", "commune"),
    ("fiscalite-locale-des-particuliers", "taux_global_tfnb","TFNB_GLOBAL","Taxe foncière non bâti — taux global acquitté",  "global"),
    ("fiscalite-locale-des-particuliers", "taux_plein_teom", "TEOM",       "Taxe d'enlèvement des ordures ménagères — taux plein", "global"),
    ("fiscalite-locale-des-particuliers", "h12vote",         "TH_VOTE",    "Taxe d'habitation (résidences secondaires) — taux voté", "commune"),
    ("fiscalite-locale-des-particuliers", "taux_global_th",  "TH_GLOBAL",  "Taxe d'habitation (résidences secondaires) — taux global", "global"),
    ("fiscalite-locale-des-entreprises",  "taux_global_cfe_hz",  "CFE_GLOBAL", "Cotisation foncière des entreprises — taux global", "global"),
    ("fiscalite-locale-des-entreprises",  "taux_global_cfe_zae", "CFE_ZAE",    "CFE en zone d'activité économique", "global"),
]

DATASETS = sorted({d for d, *_ in INDICATEURS})

# Avant 2021 : comptes individuels, un jeu par exercice ou groupe d'exercices.
# On n'y prend que 2015-2020 ; la suite vient de `e12vote`, qui rend en 2021 le
# même taux que leur `tfb` pour Lasalle, Saillans et Brassac (relevé du 09/11/2026).
COMPTES_COMMUNES = {
    "comptes-individuels-des-communes-fichier-global-2011-2015": (2015,),
    "comptes-individuels-des-communes-fichier-global-2016": (2016,),
    "comptes-individuels-des-communes-fichier-global-2017": (2017,),
    "comptes-individuels-des-communes-fichier-global-2018": (2018,),
    "comptes-individuels-des-communes-fichier-global-2019-2020": (2019, 2020),
}
COMPTES_DEPARTEMENTS = ("comptes-individuels-des-departements-et-des-collectivites"
                        "-territoriales-uniques-fichier-global-2019-2020")
ANNEE_TRANSFERT = 2021          # la part départementale de TFB passe aux communes
LIB_TFB_VOTE = next(lib for _, _, ind, lib, _ in INDICATEURS if ind == "TFB_VOTE")
LIB_TFB_DEP = "Taxe foncière bâti — taux voté par le département"

# Les indicateurs qu'on situe parmi les autres communes. La taxe d'enlèvement
# des ordures ménagères d'abord : c'est le seul taux de la page qu'aucune part
# communale n'explique, et le premier qu'un habitant compare.
REPERES = ("TEOM",)


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fiscalite_taux (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            insee       TEXT NOT NULL,
            commune     TEXT,
            annee       INTEGER NOT NULL,
            indicateur  TEXT NOT NULL,
            libelle     TEXT,
            portee      TEXT,            -- commune | global | departement
            taux        REAL,
            epci        TEXT,
            source      TEXT DEFAULT 'data.economie-fiscalite',
            created_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(insee, annee, indicateur)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fisc_insee"
                 " ON fiscalite_taux(insee, annee)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fiscalite_reperes (
            insee           TEXT NOT NULL,
            annee           INTEGER NOT NULL,
            indicateur      TEXT NOT NULL,
            portee          TEXT NOT NULL,   -- france | departement
            code            TEXT NOT NULL DEFAULT '',
            taux            REAL NOT NULL,   -- celui de la commune, tel que comparé
            communes        INTEGER,         -- celles qui lèvent la taxe (taux > 0)
            mediane         REAL,
            au_moins_autant INTEGER,         -- dont le taux est ≥ celui de la commune
            PRIMARY KEY (insee, annee, indicateur, portee)
        )
    """)
    conn.commit()


def fetch_dataset(dataset: str, insee_list: list[str]) -> list[dict]:
    """Enregistrements d'un jeu pour les communes visées (filtre côté serveur)."""
    where = " or ".join(f'insee_com="{c}"' for c in insee_list)
    url = f"{ODS_BASE}/{dataset}/records?" + urllib.parse.urlencode(
        {"where": where, "limit": 100, "order_by": "exercice desc"})
    data = fetch_json(url, source="data.economie-fiscalite", timeout=60, complete=True)
    return data.get("results", [])


def import_records(conn, dataset: str, records: list[dict]) -> int:
    """Insert idempotent — rejouable sans dupliquer."""
    cols = [(c, ind, lib, portee) for d, c, ind, lib, portee in INDICATEURS if d == dataset]
    inserted = 0
    for rec in records:
        insee = str(rec.get("insee_com") or "").strip()
        annee = rec.get("exercice")
        if not insee or annee is None:
            continue
        commune = COMMUNES.get(insee, {}).get("nom") or rec.get("libcom")
        for col, ind, libelle, portee in cols:
            taux = rec.get(col)
            if taux is None:
                continue        # taux non applicable (ex. pas de ZAE) — pas un zéro
            conn.execute(
                "INSERT OR REPLACE INTO fiscalite_taux"
                " (insee, commune, annee, indicateur, libelle, portee, taux, epci)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (insee, commune, int(annee), ind, libelle, portee,
                 float(taux), rec.get("q03")))
            inserted += 1
    conn.commit()
    return inserted


def _agregat(dataset: str, select: str, where: str) -> dict:
    url = f"{ODS_BASE}/{dataset}/records?" + urllib.parse.urlencode(
        {"select": select, "where": where})
    # Un agrégat n'est pas un relevé : il ne s'ajoute pas au compte des taux.
    return (fetch_json(url, source="data.economie-fiscalite", timeout=60, mesurer=False)
            .get("results") or [{}])[0]


def code_dgfip(insee: str) -> tuple[str, str] | None:
    """(dep, icom) des comptes individuels : '30140' → ('030', '140'),
    '2A004' → ('02A', '004'). Outre-mer : None. Les comptes y codent le
    département 101 à 106 dans un ordre qui n'est pas celui de l'INSEE
    (Guyane 102, Martinique 103) : ne pas deviner, aucune instance n'y est."""
    if len(insee) != 5 or insee.startswith("97"):
        return None
    return "0" + insee[:2], insee[2:]


def _records(dataset: str, select: str, where: str) -> list[dict]:
    """Toute la réponse : l'API rend 100 lignes par page et ne dit pas qu'elle
    coupe. On suit `total_count`."""
    out: list[dict] = []
    while True:
        url = f"{ODS_BASE}/{dataset}/records?" + urllib.parse.urlencode(
            {"select": select, "where": where, "limit": 100, "offset": len(out)})
        data = fetch_json(url, source="data.economie-fiscalite", timeout=60, complete=True)
        page = data.get("results") or []
        out += page
        if not page or len(out) >= (data.get("total_count") or 0):
            return out


def fetch_avant_2021(insee_list: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Comptes individuels des communes visées (2015-2020), puis ceux de leurs
    départements (2020). Rien n'est écrit ici."""
    codes = sorted({c for c in map(code_dgfip, insee_list) if c})
    if not codes:
        return {}, []
    communes = " or ".join(f"(dep='{d}' and icom='{c}')" for d, c in codes)
    par_jeu = {}
    for dataset, annees in COMPTES_COMMUNES.items():
        ans = ",".join(f"'{a}'" for a in annees)
        par_jeu[dataset] = _records(dataset, "an,dep,icom,inom,tfb",
                                    f"an in ({ans}) and ({communes})")
    deps = ",".join(sorted({f"'{d}'" for d, _ in codes}))
    departements = _records(COMPTES_DEPARTEMENTS, "exer,dep,lbudg,tfb",
                            f"exer='{ANNEE_TRANSFERT - 1}' and dep in ({deps})")
    return par_jeu, departements


def _vote(r: dict) -> bool:
    """Un `tfb` des comptes individuels à 0 n'est pas un taux voté : Fontrieu
    (81062), commune nouvelle du 01/01/2016, y vaut 0 en 2016 entre deux 13,06.
    Absent, pas zéro (décision du 09/11/2026)."""
    return r.get("tfb") is not None and float(r["tfb"]) != 0


def zeros_avant_2021(par_jeu: dict[str, list[dict]], insee_list: list[str]) -> list[tuple]:
    """(insee, annee) dont la source donne un `tfb` à 0 : la ligne qu'une passe
    antérieure aurait écrite est retirée, pas laissée en base."""
    par_code = {code_dgfip(i): i for i in insee_list if code_dgfip(i)}
    out = set()
    for records in par_jeu.values():
        for r in records:
            insee = par_code.get((r.get("dep"), r.get("icom")))
            if insee and str(r.get("an") or "").isdigit() and r.get("tfb") is not None \
                    and not _vote(r):
                out.add((insee, int(r["an"])))
    return sorted(out)


def lignes_avant_2021(par_jeu: dict[str, list[dict]], departements: list[dict],
                      insee_list: list[str]) -> list[tuple]:
    """Lignes de `fiscalite_taux` : TFB_VOTE 2015-2020 par commune, et TFB_DEP,
    le taux du département en 2020, porté sur chaque commune de ce département.
    `source` = le jeu d'où vient la valeur."""
    par_code = {code_dgfip(i): i for i in insee_list if code_dgfip(i)}
    nom = lambda insee, defaut=None: COMMUNES.get(insee, {}).get("nom") or defaut
    lignes, lu = [], {}
    for dataset, records in par_jeu.items():
        for r in records:
            insee = par_code.get((r.get("dep"), r.get("icom")))
            lu.setdefault(insee, r.get("inom"))
            annee = int(r["an"]) if str(r.get("an") or "").isdigit() else None
            if not insee or annee is None or annee >= ANNEE_TRANSFERT or not _vote(r):
                continue
            lignes.append((insee, nom(insee, r.get("inom")), annee, "TFB_VOTE", LIB_TFB_VOTE,
                           "commune", float(r["tfb"]), dataset))
    taux_dep: dict[str, set[float]] = {}
    for r in departements:
        if r.get("exer") == str(ANNEE_TRANSFERT - 1) and _vote(r):
            taux_dep.setdefault(r["dep"], set()).add(float(r["tfb"]))
    for (dep, _), insee in sorted(par_code.items()):
        # Corse : la Collectivité publie 12,56, mais le taux transféré en 2021
        # n'est pas le même des deux côtés (Ajaccio 30,65 − 18,40 = 12,25 ;
        # Bastia 36,58 − 23,68 = 12,90). Pas de taux à porter qui boucle.
        if dep in ("02A", "02B"):
            continue
        taux = taux_dep.get(dep)
        if not taux:
            continue                # département absent : pas de ligne, pas de zéro
        if len(taux) > 1:
            raise ValueError(f"département {dep} : {len(taux)} taux de TFB en "
                             f"{ANNEE_TRANSFERT - 1} ({sorted(taux)}), aucun retenu")
        lignes.append((insee, nom(insee, lu.get(insee)), ANNEE_TRANSFERT - 1, "TFB_DEP", LIB_TFB_DEP,
                       "departement", taux.pop(), COMPTES_DEPARTEMENTS))
    return lignes


def import_avant_2021(conn, lignes: list[tuple], zeros: list[tuple] = ()) -> int:
    conn.executemany("DELETE FROM fiscalite_taux WHERE insee=? AND annee=?"
                     " AND indicateur='TFB_VOTE'", zeros)
    conn.executemany(
        "INSERT OR REPLACE INTO fiscalite_taux"
        " (insee, commune, annee, indicateur, libelle, portee, taux, source)"
        " VALUES (?,?,?,?,?,?,?,?)", lignes)
    conn.commit()
    return len(lignes)


def releve_reperes(conn, communes: list[str]) -> int:
    """Situe le dernier taux connu de chaque commune parmi celles qui lèvent la
    même taxe, la même année. Se joue après `import_records` : il lit la base."""
    # Tout lire, puis écrire : `fetch_json` archive chaque réponse par sa propre
    # connexion, et une écriture laissée ouverte ici la ferait renoncer.
    reperes = []
    for indicateur in REPERES:
        dataset, colonne = next((d, c) for d, c, ind, *_ in INDICATEURS if ind == indicateur)
        for insee in communes:
            r = conn.execute(
                "SELECT annee, taux FROM fiscalite_taux WHERE insee=? AND indicateur=?"
                " AND taux > 0 ORDER BY annee DESC LIMIT 1", (insee, indicateur)).fetchone()
            if not r:
                continue
            annee, taux = r["annee"], r["taux"]
            base = f"{colonne}>0 and exercice='{annee}'"
            for portee, code, filtre in (("france", "", ""),
                                         ("departement", insee[:2], f" and dep='{insee[:2]}'")):
                a = _agregat(dataset, f"count(*) as communes, median({colonne}) as mediane",
                             base + filtre)
                autant = _agregat(dataset, "count(*) as n", f"{base}{filtre} and {colonne}>={taux}")
                if not a.get("communes"):
                    continue
                reperes.append((insee, annee, indicateur, portee, code, taux, a["communes"],
                                a.get("mediane"), autant.get("n")))
    conn.executemany("INSERT OR REPLACE INTO fiscalite_reperes VALUES (?,?,?,?,?,?,?,?,?)",
                     reperes)
    conn.commit()
    return len(reperes)


def run(insee_list: list[str] | None = None) -> int:
    conn = get_conn()
    ensure_table(conn)
    cibles = insee_list or COMMUNES_INSEE
    total = 0
    echecs = []
    try:
        for dataset in DATASETS:
            try:
                records = fetch_dataset(dataset, cibles)
            except Exception as e:
                # Format lu par le service local du portail (DETAIL / RESUME).
                print(f"  [fiscalite] {dataset} : échec → {type(e).__name__}: {e}")
                echecs.append(dataset)
                continue
            n = import_records(conn, dataset, records)
            total += n
            annees = sorted({r.get("exercice") for r in records if r.get("exercice")})
            print(f"  [fiscalite] {dataset}: {len(records)} enregistrements → "
                  f"{n} taux ({annees[0] if annees else '?'}–{annees[-1] if annees else '?'})")
        try:
            # Tout lire avant d'écrire, comme les repères (archive de `fetch_json`).
            par_jeu, departements = fetch_avant_2021(cibles)
            lignes = lignes_avant_2021(par_jeu, departements, cibles)
            n = import_avant_2021(conn, lignes, zeros_avant_2021(par_jeu, cibles))
            total += n
            print(f"  [fiscalite] avant {ANNEE_TRANSFERT} (comptes individuels) : {n} taux "
                  f"dont {sum(1 for l in lignes if l[3] == 'TFB_DEP')} départementaux")
        except Exception as e:
            print(f"  [fiscalite] avant {ANNEE_TRANSFERT} : échec → {type(e).__name__}: {e}")
            echecs.append(f"avant {ANNEE_TRANSFERT}")
        try:
            n = releve_reperes(conn, [c for c in COMMUNES_FOND_INSEE if c in cibles])
            print(f"  [fiscalite] {n} repère(s) : {', '.join(REPERES)}")
        except Exception as e:
            print(f"  [fiscalite] repères : échec → {type(e).__name__}: {e}")
            echecs.append("repères")
        print(f"[fiscalite] {total} taux en base")
    finally:
        conn.close()
    if echecs:
        raise RuntimeError(f"relevés en échec : {', '.join(echecs)}")
    return total


def stats():
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM fiscalite_taux").fetchone()[0]
        print(f"fiscalite_taux : {total} taux\n")
        annee = conn.execute("SELECT MAX(annee) FROM fiscalite_taux").fetchone()[0]
        if not annee:
            return
        print(f"Taux votés par la commune — exercice {annee} "
              f"(part communale, hors EPCI et syndicats) :")
        print(f"  {'commune':<32}{'TFB':>8}{'TFNB':>8}{'TH 2e rés.':>12}{'TEOM':>8}")
        for r in conn.execute("""
            SELECT commune,
                   MAX(CASE WHEN indicateur='TFB_VOTE'  THEN taux END) tfb,
                   MAX(CASE WHEN indicateur='TFNB_VOTE' THEN taux END) tfnb,
                   MAX(CASE WHEN indicateur='TH_VOTE'   THEN taux END) th,
                   MAX(CASE WHEN indicateur='TEOM'      THEN taux END) teom
            FROM fiscalite_taux WHERE annee=? GROUP BY commune
            ORDER BY tfb DESC
        """, (annee,)):
            f = lambda v: f"{v:.2f}" if v is not None else "—"
            print(f"  {(r['commune'] or '?'):<32}{f(r['tfb']):>8}{f(r['tfnb']):>8}"
                  f"{f(r['th']):>12}{f(r['teom']):>8}")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--insee", action="append", help="limiter à un code INSEE (répétable)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    run(insee_list=args.insee)


if __name__ == "__main__":
    main()
