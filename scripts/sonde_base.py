#!/usr/bin/env python3
"""Sonde d'agrégats : interroger une base Vigie sans en lire le contenu.

Une base d'instance mêle des données de sources publiques et ce que l'atelier y
ajoute (hypothèses, liens présumés, notes, comptes). Pour établir un fait sur
elle — « combien de flux en 2024 ? », « la colonne `perimetre` est-elle
remplie ? » — il faut des NOMBRES, pas des fiches. Cet outil ne rend que des
nombres, et refuse de rendre autre chose :

  • lecture seule (`mode=ro` + autorisation SQLite limitée à SELECT) ;
  • regroupement seulement sur une colonne CATÉGORIELLE : pas de nom, d'adresse,
    de texte libre, de note ; au plus 40 valeurs distinctes de 40 caractères ;
  • tout effectif inférieur à 5 est masqué (« <5 ») : un groupe d'une personne
    n'est pas une statistique, c'est une fiche ;
  • les tables de l'atelier (comptes, notes, hypothèses…) ne se comptent que
    globalement.

    sonde_base.py tables      db/x.db
    sonde_base.py schema      db/x.db [TABLE]
    sonde_base.py compter     db/x.db TABLE [--par COL] [--ou "SQL"]
    sonde_base.py somme       db/x.db TABLE COL [--par COL] [--ou "SQL"]
    sonde_base.py remplissage db/x.db TABLE

Sur une base fictive (`donnees_synthetiques.py`) rien de tout cela ne protège
quoi que ce soit : l'outil sert aussi à la lire, c'est voulu.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys

SEUIL_K = 5
MAX_VALEURS = 40
MAX_LONGUEUR = 40

# Ce que l'atelier ajoute à la donnée publique, plus les comptes : comptage
# global uniquement, jamais de regroupement ni de filtre.
TABLES_ATELIER = {
    "users", "invitations", "revoked_tokens", "audit_log", "atelier_comments",
    "contacts", "entity_notes", "annotations", "propositions", "taches",
    "taches_journal", "alerts", "relation_candidates", "citations_relues",
}

# Colonnes dont les valeurs identifient ou racontent : jamais en regroupement.
COLONNE_REFUSEE = re.compile(
    r"nom|name|prenom|firstname|lastname|adresse|address|mail|tel|phone|"
    r"content|texte|text|note|metadata|hash|token|login|siren|siret|birth|"
    r"naiss|url|comment|description|objet|title|titre|saisi_par",
    re.I,
)


def ouvrir(chemin: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    permis = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
              sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_PRAGMA}
    conn.set_authorizer(
        lambda action, *_: sqlite3.SQLITE_OK if action in permis
        else sqlite3.SQLITE_DENY)
    return conn


def tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def colonnes(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    if table not in tables(conn):
        sys.exit(f"table inconnue : {table}")
    # `table` est validée contre sqlite_master : l'interpolation est sûre.
    return [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info("{table}")')]


def valider_colonne(conn, table: str, col: str) -> str:
    if col not in {c for c, _ in colonnes(conn, table)}:
        sys.exit(f"colonne inconnue : {table}.{col}")
    return col


def valider_ou(ou: str | None) -> str:
    if not ou:
        return "1=1"
    if ";" in ou or "--" in ou or "/*" in ou:
        sys.exit("clause --ou refusée : ni point-virgule ni commentaire")
    # Les colonnes d'identité, refusées en regroupement, le sont aussi en
    # filtre : sinon deux comptages suffisent à savoir si une personne est dans
    # la base (`--ou "name != 'X'"` rend N−1, au-dessus du seuil, donc lisible).
    if COLONNE_REFUSEE.search(ou):
        sys.exit("clause --ou refusée : elle porte sur une colonne d'identité ou de texte")
    return f"({ou})"


def masquer(n: int) -> str:
    return f"<{SEUIL_K}" if n < SEUIL_K else str(n)


def valider_regroupement(conn, table: str, col: str) -> None:
    if table in TABLES_ATELIER:
        sys.exit(f"{table} : table de l'atelier, comptage global seulement")
    if COLONNE_REFUSEE.search(col):
        sys.exit(f"{table}.{col} : colonne d'identité ou de texte, pas de regroupement")
    distinctes, longueur = conn.execute(
        f'SELECT COUNT(DISTINCT "{col}"), COALESCE(MAX(LENGTH("{col}")),0) '
        f'FROM "{table}"').fetchone()
    if distinctes > MAX_VALEURS or longueur > MAX_LONGUEUR:
        sys.exit(f"{table}.{col} : {distinctes} valeurs distinctes, "
                 f"jusqu'à {longueur} caractères — pas une colonne catégorielle")


def cmd_tables(conn, _a) -> None:
    for t in tables(conn):
        n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"{t:32} {masquer(n)}")


def cmd_schema(conn, a) -> None:
    for t in ([a.table] if a.table else tables(conn)):
        print(f"{t}: " + ", ".join(f"{c} {ty}" for c, ty in colonnes(conn, t)))


def regrouper(conn, a, expr: str, etiquette: str) -> None:
    ou = valider_ou(a.ou)
    if a.table in TABLES_ATELIER and a.ou:
        sys.exit(f"{a.table} : table de l'atelier, pas de filtre")
    if not a.par:
        n, s = conn.execute(
            f'SELECT COUNT(*), {expr} FROM "{a.table}" WHERE {ou}').fetchone()
        print(f"{etiquette:8} {masquer(n) if n < SEUIL_K else s}   "
              f"(lignes : {masquer(n)})")
        return
    valider_colonne(conn, a.table, a.par)
    valider_regroupement(conn, a.table, a.par)
    for valeur, n, s in conn.execute(
            f'SELECT "{a.par}", COUNT(*), {expr} FROM "{a.table}" WHERE {ou} '
            f'GROUP BY "{a.par}" ORDER BY COUNT(*) DESC'):
        petit = n < SEUIL_K
        print(f"{str(valeur)[:MAX_LONGUEUR]!s:42} {masquer(n):>6}"
              f"{'' if petit else '  ' + etiquette + ' ' + str(s)}")


def cmd_compter(conn, a) -> None:
    colonnes(conn, a.table)
    regrouper(conn, a, "COUNT(*)", "n")


def cmd_somme(conn, a) -> None:
    ty = dict(colonnes(conn, a.table)).get(a.col)
    if ty is None:
        sys.exit(f"colonne inconnue : {a.table}.{a.col}")
    if a.table in TABLES_ATELIER:
        sys.exit(f"{a.table} : table de l'atelier, comptage global seulement")
    regrouper(conn, a, f'ROUND(SUM("{a.col}"),2)', "somme")


def cmd_remplissage(conn, a) -> None:
    total = conn.execute(f'SELECT COUNT(*) FROM "{a.table}"').fetchone()[0]
    print(f"{a.table} : {masquer(total)} lignes")
    if total < SEUIL_K:
        return
    for c, _ in colonnes(conn, a.table):
        vides = conn.execute(
            f'SELECT COUNT(*) FROM "{a.table}" WHERE "{c}" IS NULL OR "{c}" = \'\''
        ).fetchone()[0]
        print(f"  {c:30} {100 * (total - vides) / total:5.1f} % remplie")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    for nom, fn, args in (
        ("tables", cmd_tables, ()),
        ("schema", cmd_schema, (("table", "?"),)),
        ("compter", cmd_compter, (("table", None),)),
        ("somme", cmd_somme, (("table", None), ("col", None))),
        ("remplissage", cmd_remplissage, (("table", None),)),
    ):
        s = sp.add_parser(nom)
        s.add_argument("db")
        for arg, nargs in args:
            s.add_argument(arg, **({"nargs": nargs} if nargs else {}))
        if nom in ("compter", "somme"):
            s.add_argument("--par")
            s.add_argument("--ou")
        s.set_defaults(fn=fn)
    a = p.parse_args()
    try:
        conn = ouvrir(a.db)
        a.fn(conn, a)
    except sqlite3.Error as e:
        sys.exit(f"sqlite : {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
