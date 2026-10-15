"""
budgets_annexes.py — Un budget annexe, un exercice : dette, produits, charges
==============================================================================
Les balances comptables de la DGFiP donnent, pour chaque budget d'une commune,
son nom (`lbudg`) et ses comptes. L'OFGL n'en fournit que la SOMME (cf.
`collectors/ofgl.py`, agrégats suffixés « budgets annexes ») : ici on garde le
détail, pour qu'une dette qui change de budget se lise avec le nom du budget.

Par budget annexe (`cbudg` ≠ 1) et par exercice :
  - encours de dette : comptes 16x hors 165 (cautionnements reçus) et 169, solde
    créditeur net ; None quand le budget n'a AUCUN de ces comptes (absence ≠ zéro) ;
  - produits / charges de fonctionnement : classes 7 et 6, soldes nets ;
  - solde de fonctionnement : produits − charges.

Source : https://data.economie.gouv.fr — « balances-comptables-des-communes-en-AAAA »,
sans clé. Un exercice non publié renvoie 404 : il est passé sous silence.

Lien avec une entité de la base — facultatif, jamais créé. Un budget annexe se
publie sous son nom DGFiP ; il se lie à une fiche EXISTANTE quand
  1. `config/seed_local.json` le déclare :
     "budgets_annexes": [{"libelle": "<lbudg DGFiP>", "entite": "<nom exact de la fiche>"}]
  Un budget qui change de nom se déclare à la main, sans fiche :
     {"libelle": "<ancien lbudg>", "poursuivi_sous": "<nouveau lbudg>"}
  2. ou quand une seule fiche (hors personne) porte exactement le même nom,
     casse et accents mis à part.
Un nom ambigu ou absent laisse le budget sans lien.

Usage :
  python3 -m collectors.budgets_annexes              # tous les exercices
  python3 -m collectors.budgets_annexes --year 2023
  python3 -m collectors.budgets_annexes --dry-run
  python3 -m collectors.budgets_annexes --stats
"""

import argparse
import json
import time
import unicodedata
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

from .archive import fetch_json
from .config import COMMUNE_NAME, COMMUNE_SIREN as SIREN, HEADERS
from .db import get_conn

API_BASE = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"
YEARS = list(range(2018, date.today().year + 1))
SOURCE = "dgfip-balances"
CBUDG_PRINCIPAL = "1"
PAGE = 100


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budget_annexe_balance (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            year                    INTEGER NOT NULL,
            budget                  TEXT NOT NULL,
            nomenclature            TEXT,
            entity_id               INTEGER REFERENCES entities(id) ON DELETE SET NULL,
            encours_dette           REAL,
            produits_fonctionnement REAL,
            charges_fonctionnement  REAL,
            solde_fonctionnement    REAL,
            source                  TEXT NOT NULL DEFAULT 'dgfip-balances',
            created_at              TEXT DEFAULT (datetime('now')),
            UNIQUE(year, budget)
        )
    """)
    conn.commit()


def fetch_year(year: int) -> list[dict] | None:
    """Toutes les lignes des balances de la commune. None = non publié, ou lecture incomplète."""
    dataset = f"balances-comptables-des-communes-en-{year}"
    out: list[dict] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "where": f'siren="{SIREN}"',
            "order_by": "lbudg, compte",
            "limit": PAGE,
            "offset": offset,
        })
        url = f"{API_BASE}/{dataset}/records?{params}"
        try:
            data = fetch_json(url, source="budgets-annexes-dgfip", timeout=30, headers=HEADERS)
        except urllib.error.HTTPError as e:
            if offset == 0 and e.code == 404:
                return None
            print(f"  [erreur] {year} offset={offset} : {e}")
            return None
        except Exception as e:
            # Un lot partiel donnerait des totaux faux, écrits à la place de l'exercice.
            print(f"  [erreur] {year} offset={offset} : {e}")
            return None
        page = data.get("results", [])
        out.extend(page)
        if len(page) < PAGE:
            return out
        offset += PAGE
        time.sleep(0.2)


def _num(v) -> float:
    return float(v or 0)


def synthese(records: list[dict]) -> dict[str, dict]:
    """Un dict par budget annexe : dette (None si aucun compte), produits, charges, solde."""
    budgets: dict[str, dict] = {}
    for r in records:
        if str(r.get("cbudg", CBUDG_PRINCIPAL)) == CBUDG_PRINCIPAL:
            continue
        compte = str(r.get("compte", ""))
        b = budgets.setdefault(r["lbudg"], {
            "nomenclature": r.get("nomen"),
            "encours_dette": None, "produits": 0.0, "charges": 0.0, "a_des_comptes": False,
        })
        sd, sc = _num(r.get("sd")), _num(r.get("sc"))
        if compte.startswith("16") and not compte.startswith(("165", "169")):
            b["encours_dette"] = (b["encours_dette"] or 0.0) + sc - sd
        elif compte.startswith("7"):
            b["produits"] += sc - sd
        elif compte.startswith("6"):
            b["charges"] += sd - sc
        b["a_des_comptes"] = True
    for b in budgets.values():
        b["solde"] = b["produits"] - b["charges"]
    return budgets


def _declares() -> list[dict]:
    chemin = Path(__file__).resolve().parent.parent / "config" / "seed_local.json"
    try:
        conf = json.loads(chemin.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    decl = list(conf.get("budgets_annexes") or [])
    if conf.get("budget_annexe"):
        decl.append(conf["budget_annexe"])
    return decl


def declarations() -> dict[str, str]:
    """{libellé DGFiP: nom de fiche} déclarés pour l'instance (liste, ou l'ancien bloc unique)."""
    return {d["libelle"]: d["entite"] for d in _declares() if d.get("libelle") and d.get("entite")}


def suites() -> dict[str, str]:
    """{libellé DGFiP: libellé du budget qui le poursuit}, déclaré à la main : jamais déduit."""
    return {d["libelle"]: d["poursuivi_sous"] for d in _declares()
            if d.get("libelle") and d.get("poursuivi_sous")}


def _norme(nom: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", nom.casefold())
                   if unicodedata.category(c) != "Mn").strip()


def resoudre_entites(conn, decl: dict[str, str], budgets=()) -> dict[str, int]:
    """Nom de budget → id de fiche : déclaration d'abord, nom identique ensuite.

    Une déclaration introuvable ou ambiguë est signalée ; un rapprochement par le
    nom ne se fait que sur une fiche unique et jamais sur une personne.
    """
    ids: dict[str, int] = {}
    for budget, nom in decl.items():
        row = conn.execute("SELECT id FROM entities WHERE name = ? LIMIT 2", (nom,)).fetchall()
        if len(row) == 1:
            ids[budget] = row[0][0]
        else:
            print(f"  ⚠ « {budget} » → fiche « {nom} » : "
                  f"{'introuvable' if not row else 'plusieurs fiches'}, budget sans lien")
    restants = {_norme(b): b for b in budgets if b not in ids}
    if restants:
        trouvees: dict[str, list[int]] = {}
        for eid, nom in conn.execute("SELECT id, name FROM entities WHERE type <> 'person'"):
            if _norme(nom) in restants:
                trouvees.setdefault(restants[_norme(nom)], []).append(eid)
        ids.update({b: v[0] for b, v in trouvees.items() if len(v) == 1})
    return ids


def import_year(conn, year: int, decl: dict[str, str], dry_run: bool = False) -> int:
    records = fetch_year(year)
    if not records:
        print(f"  {year} : aucune donnée")
        return 0
    budgets = synthese(records)
    ids = resoudre_entites(conn, decl, budgets)
    print(f"  {year} : {len(budgets)} budget(s) annexe(s)")
    for nom, b in sorted(budgets.items()):
        dette = "aucun compte" if b["encours_dette"] is None else f"{b['encours_dette']:,.0f} €"
        print(f"    {nom:36s} dette {dette:>13s}  solde {b['solde']:>10,.0f} €"
              + (f"  → fiche #{ids[nom]}" if nom in ids else ""))
    if dry_run:
        return len(budgets)
    # Idempotent par exercice : un budget disparu des balances ne doit pas survivre.
    conn.execute("DELETE FROM budget_annexe_balance WHERE year = ? AND source = ?", (year, SOURCE))
    for nom, b in budgets.items():
        conn.execute(
            "INSERT INTO budget_annexe_balance (year, budget, nomenclature, entity_id,"
            " encours_dette, produits_fonctionnement, charges_fonctionnement,"
            " solde_fonctionnement, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (year, nom, b["nomenclature"], ids.get(nom), None if b["encours_dette"] is None else round(b["encours_dette"], 2),
             round(b["produits"], 2), round(b["charges"], 2), round(b["solde"], 2), SOURCE))
    conn.commit()
    return len(budgets)


def run(year: int | None = None, dry_run: bool = False, stats_only: bool = False):
    conn = get_conn()
    ensure_table(conn)
    if stats_only:
        for y, n, d in conn.execute(
                "SELECT year, COUNT(*), SUM(encours_dette) FROM budget_annexe_balance"
                " GROUP BY year ORDER BY year"):
            print(f"  {y} : {n} budget(s) annexe(s) | dette {d:,.0f} €")
        conn.close()
        return
    decl = declarations()
    total = 0
    for y in ([year] if year else YEARS):
        total += import_year(conn, y, decl, dry_run=dry_run)
    print(f"\nBudgets annexes : {total} ligne(s) budget × exercice.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Budgets annexes — {COMMUNE_NAME}")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    run(year=args.year, dry_run=args.dry_run, stats_only=args.stats)
