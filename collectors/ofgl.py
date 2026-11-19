"""
ofgl.py — Collecteur OFGL (Observatoire des Finances et de la Gestion publique Locales)

Importe les agrégats financiers pré-calculés de la commune depuis data.ofgl.fr :
- Recettes/dépenses fonctionnement et investissement
- Épargne brute, nette, CAF
- Encours de dette, annuité, DGF, fiscalité
- Euros par habitant (comparaison strate)

Source : https://data.ofgl.fr — API ODS, pas de clé requise.
Données disponibles : 2018-2025 (le millésime courant n'est publié qu'avec ~1 an de décalage). Budget principal ; budgets annexes additionnés
(suffixe SUFFIXE_ANNEXES) ; et, quand la commune a des annexes, les comptes
CONSOLIDÉS de l'OFGL (suffixes SUFFIXE_TOUS et SUFFIXE_FLUX).

Usage :
  python3 -m collectors.ofgl              # toutes les années
  python3 -m collectors.ofgl --year 2024
  python3 -m collectors.ofgl --dry-run
  python3 -m collectors.ofgl --stats
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date

from .archive import fetch_json
from .db import get_conn

from .config import COMMUNE_NAME, COMMUNES_ABSORBEES, HEADERS, releve_et_siren
DATASET = "ofgl-base-communes"
API_BASE = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets"
# OFGL publie les comptes exécutés avec ~1 an de décalage. On balaie jusqu'à
# l'année courante : les millésimes non encore publiés renvoient 0 (sans erreur).
YEARS = list(range(2017, date.today().year + 1))


# Les budgets annexes (eau, assainissement, régies…) portent une part de la dette
# et de l'épargne qui n'apparaît pas dans le budget principal. Leurs agrégats sont
# additionnés par nature et rangés sous le nom de l'agrégat suivi de ce suffixe :
# le même nom sert de clé à l'affichage (budgets/+page.svelte), à garder aligné.
SUFFIXE_ANNEXES = " - budgets annexes"
TYPE_PRINCIPAL = "Budget principal"

# Principal + annexes ne s'additionnent pas : la subvention d'équilibre, le
# personnel remboursé par une régie sont une dépense d'un budget et une recette
# de l'autre (431 k€ à Lasalle en 2025, 23 % des recettes du principal). L'OFGL
# publie la somme qui les retire — « comptes consolidés » — et le montant de ces
# flux, qui dit pourquoi le total n'est pas la somme.
DATASET_CONSOLIDE = "ofgl-base-communes-consolidee"
SUFFIXE_TOUS = " - tous budgets"
SUFFIXE_FLUX = " - flux entre budgets"
PAGE = 100


def fetch_year(year: int) -> list[dict]:
    """Tous les budgets de la commune pour l'exercice (principal et annexes)."""
    return fetch_year_siren(year)[1]


def fetch_year_siren(year: int) -> tuple[str | None, list[dict]]:
    """`(SIREN qui a répondu, lignes)`. Plusieurs budgets multiplient les
    lignes par agrégat : on pagine."""
    # `None` (erreur de lecture) ne passe pas à l'ancien SIREN ; l'appelant
    # n'attend qu'une liste.
    siren, lignes = releve_et_siren(lambda s: _fetch_year(year, s))
    return siren, lignes or []


def _fetch_year(year: int, siren: str) -> list[dict] | None:
    results: list[dict] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "where": f'siren="{siren}" AND year(exer)={year}',
            "order_by": "type_de_budget, agregat",
            "limit": PAGE,
            "offset": offset,
        })
        url = f"{API_BASE}/{DATASET}/records?{params}"
        try:
            data = fetch_json(url, source="ofgl", timeout=30, headers=HEADERS,
                              complete=True)
        except Exception as e:
            print(f"  [erreur] {year} : {e}")
            return None
        page = data.get("results", [])
        results.extend(page)
        if len(page) < PAGE:
            return results
        offset += PAGE


def fetch_consolide(year: int, siren: str | None = None) -> list[dict]:
    """Les agrégats consolidés de la commune pour l'exercice (une ligne par agrégat).

    `siren` : celui sous lequel les budgets de l'exercice ont été lus — le
    consolidé se prend sous le même, jamais sous un autre."""
    if siren:
        return _fetch_consolide(year, siren) or []
    return releve_et_siren(lambda s: _fetch_consolide(year, s))[1] or []


def _fetch_consolide(year: int, siren: str) -> list[dict] | None:
    params = urllib.parse.urlencode({
        "where": f'siren="{siren}" AND year(exer)={year}',
        "limit": PAGE,
    })
    try:
        data = fetch_json(f"{API_BASE}/{DATASET_CONSOLIDE}/records?{params}",
                          source="ofgl", timeout=30, headers=HEADERS, complete=True)
    except Exception as e:
        print(f"  [erreur] consolidé {year} : {e}")
        return None
    return data.get("results", [])


def consolides(records: list[dict]) -> list[dict]:
    """« agrégat - tous budgets » (flux retirés) et « agrégat - flux entre budgets ».

    Le flux n'est gardé que non nul : un zéro dit seulement que l'agrégat
    s'additionne tel quel (la dette, l'épargne).
    """
    out = []
    for r in records:
        if r.get("montant") is None:
            continue
        commun = {"ptot": r.get("ptot"), "tranche_population": r.get("tranche_population"),
                  "rural": r.get("rural")}
        out.append({**commun, "agregat": r["agregat"] + SUFFIXE_TOUS,
                    "montant": r["montant"], "euros_par_habitant": r.get("euros_par_habitant")})
        if r.get("montant_flux"):
            ptot = r.get("ptot")
            out.append({**commun, "agregat": r["agregat"] + SUFFIXE_FLUX,
                        "montant": r["montant_flux"],
                        "euros_par_habitant": r["montant_flux"] / ptot if ptot else None})
    return out


def separer_budgets(records: list[dict]) -> list[dict]:
    """Garde le principal tel quel ; somme les annexes sous « agrégat + suffixe ».

    Une commune sans budget annexe ne produit aucune ligne suffixée : l'absence
    se lit « pas d'annexe », pas « annexes à zéro ».
    """
    principal = [r for r in records if r.get("type_de_budget") == TYPE_PRINCIPAL]
    annexes: dict[str, dict] = {}
    for r in records:
        if r.get("type_de_budget") == TYPE_PRINCIPAL or r.get("montant") is None:
            continue
        a = annexes.setdefault(r["agregat"] + SUFFIXE_ANNEXES, {
            "agregat": r["agregat"] + SUFFIXE_ANNEXES,
            "montant": 0.0, "euros_par_habitant": 0.0,
            "ptot": r.get("ptot"), "tranche_population": r.get("tranche_population"),
            "rural": r.get("rural"),
        })
        a["montant"] += r["montant"]
        # Un €/hab. manquant sur UNE annexe rend la somme inconnue : None, pas 0.
        eph = r.get("euros_par_habitant")
        a["euros_par_habitant"] = (None if eph is None or a["euros_par_habitant"] is None
                                   else a["euros_par_habitant"] + eph)
    return principal + list(annexes.values())


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofgl_agregats (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            year                INTEGER NOT NULL,
            agregat             TEXT NOT NULL,
            montant             REAL,
            euros_par_habitant  REAL,
            population          INTEGER,
            tranche_population  TEXT,
            rural               TEXT,
            source              TEXT DEFAULT 'ofgl',
            created_at          TEXT DEFAULT (datetime('now')),
            siren               TEXT,
            UNIQUE(year, agregat)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofgl_agregats_absorbees (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            siren               TEXT NOT NULL,
            year                INTEGER NOT NULL,
            agregat             TEXT NOT NULL,
            montant             REAL,
            euros_par_habitant  REAL,
            population          INTEGER,
            tranche_population  TEXT,
            rural               TEXT,
            source              TEXT DEFAULT 'ofgl',
            created_at          TEXT DEFAULT (datetime('now')),
            UNIQUE(siren, year, agregat)
        )
    """)
    # Une base d'avant le 02/11/2026 n'a pas la colonne `siren`.
    from .db import _rattraper_colonnes
    _rattraper_colonnes(conn)
    conn.commit()


def _agregats(year: int, siren: str | None, records: list[dict]) -> list[dict]:
    records = separer_budgets(records)
    # Sans annexe, le consolidé redit le principal : rien à ajouter.
    if records and any(r["agregat"].endswith(SUFFIXE_ANNEXES) for r in records):
        records += consolides(fetch_consolide(year, siren))
    return records


def import_year(conn, year: int, dry_run: bool = False) -> int:
    siren, lus = fetch_year_siren(year)
    records = _agregats(year, siren, lus)
    if not records:
        print(f"  {year} : aucune donnée")
        return 0

    print(f"  {year} : {len(records)} agrégats (SIREN {siren})")
    if dry_run:
        for r in sorted(records, key=lambda x: x["agregat"]):
            print(f"    {r['agregat']:50s} {r['montant']:>12,.0f} €  {r.get('euros_par_habitant', 0):>7.1f} €/hab")
        return len(records)

    return _insert_records(conn, year, records, siren)


def import_absorbees(conn, year: int, dry_run: bool = False) -> int:
    """Les agrégats de chaque commune absorbée d'un autre code, sous SON SIREN,
    dans `ofgl_agregats_absorbees` : jamais additionnés à la commune."""
    total = 0
    for siren, d in COMMUNES_ABSORBEES.items():
        records = _agregats(year, siren, _fetch_year(year, siren) or [])
        if not records:
            continue
        print(f"  {year} : {d['nom']} (absorbée, SIREN {siren}) : {len(records)} agrégats")
        if not dry_run:
            total += _insert_records(conn, year, records, siren, table="ofgl_agregats_absorbees")
    return total


def _insert_records(conn, year: int, records: list[dict], siren: str | None = None,
                    table: str = "ofgl_agregats") -> int:
    """Insert idempotent des agrégats d'une année (UNIQUE(year, agregat),
    ou (siren, year, agregat) pour une absorbée)."""
    inserted = 0
    for r in records:
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}"
                " (year, agregat, montant, euros_par_habitant, population, tranche_population, rural, siren)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    year,
                    r["agregat"],
                    r.get("montant"),
                    r.get("euros_par_habitant"),
                    r.get("ptot"),
                    r.get("tranche_population"),
                    r.get("rural"),
                    siren,
                )
            )
            inserted += 1
        except Exception as e:
            print(f"    [erreur] {r.get('agregat')} : {e}")

    conn.commit()
    return inserted


def run(year: int | None = None, dry_run: bool = False, stats_only: bool = False):
    conn = get_conn()
    ensure_table(conn)

    if stats_only:
        years_db = conn.execute(
            "SELECT year, COUNT(*) FROM ofgl_agregats GROUP BY year ORDER BY year"
        ).fetchall()
        if not years_db:
            print("Aucune donnée OFGL en DB.")
            conn.close()
            return
        print("OFGL en DB :")
        for y, n in years_db:
            row = conn.execute(
                "SELECT montant FROM ofgl_agregats WHERE year=? AND agregat='Encours de dette'", (y,)
            ).fetchone()
            dette = f"{row[0]:,.0f} €" if row else "—"
            epargne = conn.execute(
                "SELECT montant FROM ofgl_agregats WHERE year=? AND agregat='Epargne brute'", (y,)
            ).fetchone()
            ep = f"{epargne[0]:,.0f} €" if epargne else "—"
            print(f"  {y} : {n} agrégats | dette {dette} | épargne brute {ep}")
        conn.close()
        return

    years = [year] if year else YEARS
    total = 0
    for y in years:
        total += import_year(conn, y, dry_run=dry_run)
        total += import_absorbees(conn, y, dry_run=dry_run)
        if not dry_run:
            time.sleep(0.3)

    print(f"\nOFGL : {total} agrégats insérés/mis à jour.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Collecteur OFGL — {COMMUNE_NAME}")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    run(year=args.year, dry_run=args.dry_run, stats_only=args.stats)
