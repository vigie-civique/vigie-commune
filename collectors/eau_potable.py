"""
eau_potable.py — L'eau du robinet : les réseaux qui desservent la commune, et le
contrôle sanitaire de ce qui en sort (Hub'Eau, API qualité de l'eau potable).

À ne pas confondre avec `qualite_eau.py`, qui mesure les RIVIÈRES : une station
de cours d'eau ne dit rien de l'eau que l'on boit. Le 30/09/2026, les 91 600
analyses de l'instance de Lasalle étaient toutes des mesures de rivières, prises
côté Aigoual — aucune au robinet, aucune dans la commune.

Deux temps :
  1. `communes_udi` : quels réseaux (UDI) desservent chaque commune suivie en
     profondeur, quartier par quartier, année par année. Une commune peut en
     avoir plusieurs, chacun avec son maître d'ouvrage et son prix — c'est le
     cas de Lasalle (bourg en régie, écarts au syndicat).
  2. `resultats_dis` : les prélèvements du contrôle sanitaire (ARS) sur chacun
     de ces réseaux, et leurs résultats paramètre par paramètre. Interrogés par
     RÉSEAU et non par commune : un réseau intercommunal est souvent prélevé
     dans une commune voisine, et le chercher par commune le rend vide.

Clés naturelles, INSERT OR IGNORE — jamais d'écrasement. Collecte incrémentale :
chaque réseau repart du dernier prélèvement que SA requête a rapporté, noté dans
`eau_potable_suivi`. Pas du dernier prélèvement qui lui est rattaché : un
prélèvement partagé, arrivé par la requête d'un réseau voisin, lui donnerait une
date récente, et sa propre histoire ne serait jamais collectée — c'est arrivé au
premier essai, le 30/09/2026 : le réseau de Thoiras avait perdu 2016-2020.

Une requête par LOT de réseaux (`code_reseau` accepte une liste), sur toute la
période : 9 réseaux depuis 2016 coûtaient 99 requêtes et 469 s (dossier du
26/10/2026), 5 réseaux tiennent en 2 requêtes. Le découpage par année venait
d'un plafond de pagination à 20 000 résultats que Hub'Eau n'applique plus
(499 566 résultats lus en 25 pages sur Lyon, le 31/10/2026) ; le total lu est
quand même comparé au `count` annoncé, et l'écart fait échouer le lot.

Usage :
  python3 -m collectors.eau_potable                     # collecte incrémentale
  python3 -m collectors.eau_potable --since 2016-01-01  # historique complet
  python3 -m collectors.eau_potable --stats             # état par réseau
"""
import argparse
import urllib.parse

from . import mesure
from .config import communes_du_step
from .db import get_conn
from .qualite_eau import _get_json

API = "https://hubeau.eaufrance.fr/api/v1/qualite_eau_potable"
PAGE_SIZE = 5000
# Réseaux ou communes par requête : une URL courte, et un échec n'emporte qu'un lot.
LOT = 20
# Le contrôle sanitaire publié par Hub'Eau commence en 2016.
DEBUT = "2016-01-01"

CHAMPS_RESULTAT = (
    "code_prelevement,date_prelevement,code_commune,nom_commune,nom_uge,"
    "nom_distributeur,nom_moa,conclusion_conformite_prelevement,"
    "conformite_limites_bact_prelevement,conformite_limites_pc_prelevement,"
    "conformite_references_bact_prelevement,conformite_references_pc_prelevement,"
    "reseaux,code_parametre,libelle_parametre,code_type_parametre,"
    "code_lieu_analyse,resultat_alphanumerique,resultat_numerique,libelle_unite,"
    "limite_qualite_parametre,reference_qualite_parametre"
)


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eau_potable_udi (
            annee           TEXT NOT NULL,
            code_commune    TEXT NOT NULL,
            nom_quartier    TEXT NOT NULL DEFAULT '',
            code_reseau     TEXT NOT NULL,
            nom_reseau      TEXT,
            debut_alim      TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (annee, code_commune, nom_quartier, code_reseau)
        );
        CREATE TABLE IF NOT EXISTS eau_potable_prelevements (
            code_prelevement    TEXT PRIMARY KEY,
            date_prelevement    TEXT,
            code_commune        TEXT,   -- commune du POINT de prélèvement
            nom_commune         TEXT,
            nom_uge             TEXT,   -- unité de gestion
            nom_distributeur    TEXT,   -- l'exploitant (régie, délégataire)
            nom_moa             TEXT,   -- le maître d'ouvrage (commune, syndicat, EPCI)
            conclusion          TEXT,
            limites_bact        TEXT,   -- C conforme, N non conforme, S sans objet
            limites_pc          TEXT,
            references_bact     TEXT,
            references_pc       TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        );
        -- Un prélèvement peut valoir pour plusieurs réseaux.
        CREATE TABLE IF NOT EXISTS eau_potable_prelevement_reseaux (
            code_prelevement    TEXT NOT NULL
                REFERENCES eau_potable_prelevements(code_prelevement),
            code_reseau         TEXT NOT NULL,
            PRIMARY KEY (code_prelevement, code_reseau)
        );
        CREATE TABLE IF NOT EXISTS eau_potable_resultats (
            code_prelevement    TEXT NOT NULL
                REFERENCES eau_potable_prelevements(code_prelevement),
            code_parametre      TEXT NOT NULL,
            code_lieu_analyse   TEXT NOT NULL DEFAULT '',   -- L labo, T terrain
            libelle_parametre   TEXT,
            code_type_parametre TEXT,
            resultat_alpha      TEXT,   -- tel que publié : « <0,005 », « 12 »
            resultat_numerique  REAL,   -- ⚠ 0.0 pour « <seuil » : lire resultat_alpha
            unite               TEXT,
            limite_qualite      TEXT,
            reference_qualite   TEXT,
            -- Le libellé est dans la clé : une microcystine totale, dissoute ou
            -- dans la biomasse porte le MÊME code paramètre, et seul le libellé
            -- les distingue. Sans lui, deux mesures sur trois disparaissaient.
            PRIMARY KEY (code_prelevement, code_parametre, code_lieu_analyse,
                         libelle_parametre)
        );
        -- Jusqu'où la requête de CE réseau a été lue en entier.
        CREATE TABLE IF NOT EXISTS eau_potable_suivi (
            code_reseau         TEXT PRIMARY KEY,
            dernier_prelevement TEXT NOT NULL,
            releve_le           TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_eau_potable_prel_date
            ON eau_potable_prelevements(date_prelevement);
        CREATE INDEX IF NOT EXISTS idx_eau_potable_res_param
            ON eau_potable_resultats(code_parametre);
    """)
    conn.commit()


def ligne_udi(u: dict) -> tuple:
    return (str(u["annee"]), u["code_commune"], u.get("nom_quartier") or "",
            u["code_reseau"], u.get("nom_reseau"), u.get("debut_alim"))


def ligne_prelevement(r: dict) -> tuple:
    return (r["code_prelevement"], r.get("date_prelevement"), r.get("code_commune"),
            r.get("nom_commune"), r.get("nom_uge"), r.get("nom_distributeur"),
            r.get("nom_moa"), r.get("conclusion_conformite_prelevement"),
            r.get("conformite_limites_bact_prelevement"),
            r.get("conformite_limites_pc_prelevement"),
            r.get("conformite_references_bact_prelevement"),
            r.get("conformite_references_pc_prelevement"))


def ligne_resultat(r: dict) -> tuple:
    return (r["code_prelevement"], r["code_parametre"], r.get("code_lieu_analyse") or "",
            r.get("libelle_parametre"), r.get("code_type_parametre"),
            r.get("resultat_alphanumerique"), r.get("resultat_numerique"),
            r.get("libelle_unite"), r.get("limite_qualite_parametre"),
            r.get("reference_qualite_parametre"))


class LotAmbigu(Exception):
    """Une ligne d'un lot ne cite aucun des réseaux demandés : on ne sait plus
    au titre duquel elle est venue. Le lot se relit réseau par réseau."""


def codes_reseaux(r: dict) -> set[str]:
    return {x["code"] for x in r.get("reseaux") or [] if x.get("code")}


def demandeurs(r: dict, lot: list[str]) -> set[str]:
    """Les réseaux du lot dont la requête aurait rendu cette ligne.

    Seul, le réseau interrogé est toujours rattaché, même si la réponse n'en
    liste pas : c'est à son titre que le prélèvement a été demandé. En lot, la
    ligne vaut pour les réseaux du lot qu'elle cite ; si elle n'en cite aucun,
    rien ne dit lequel l'a demandée."""
    if len(lot) == 1:
        return set(lot)
    cites = codes_reseaux(r) & set(lot)
    if not cites:
        raise LotAmbigu(r.get("code_prelevement"))
    return cites


def enregistrer_resultats(conn, lignes: list[dict], lot: str | list[str]) -> int:
    """Écrit une page de `resultats_dis`. Rend le nombre de résultats nouveaux."""
    lot = [lot] if isinstance(lot, str) else lot
    rattaches = [codes_reseaux(r) | demandeurs(r, lot) for r in lignes]   # lève avant d'écrire
    nouveaux = 0
    for r, reseaux in zip(lignes, rattaches):
        conn.execute(
            "INSERT OR IGNORE INTO eau_potable_prelevements VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))", ligne_prelevement(r))
        for code in reseaux:
            conn.execute(
                "INSERT OR IGNORE INTO eau_potable_prelevement_reseaux VALUES (?,?)",
                (r["code_prelevement"], code))
        cur = conn.execute(
            "INSERT OR IGNORE INTO eau_potable_resultats VALUES (?,?,?,?,?,?,?,?,?,?)",
            ligne_resultat(r))
        nouveaux += cur.rowcount
    return nouveaux


def lire_pages(url: str):
    """Les pages d'une requête, jusqu'au bout de `next`. Lève si le total lu
    n'est pas celui que Hub'Eau annonce : une pagination coupée en silence
    laisserait croire à un historique complet."""
    attendu, lus = None, 0
    while url:
        data = _get_json(url)
        lignes = data.get("data") or []
        if attendu is None:
            attendu = data.get("count")
        lus += len(lignes)
        yield lignes
        url = data.get("next")
    if attendu is not None and lus != attendu:
        raise RuntimeError(f"Hub'Eau annonçait {attendu} ligne(s), {lus} lue(s)")


def fetch_udi(conn, communes: list[str]) -> list[str]:
    """Les réseaux qui desservent ces communes, toutes années confondues.
    `code_commune` accepte une liste : une requête par lot de communes."""
    for i in range(0, len(communes), LOT):
        params = {"code_commune": ",".join(communes[i:i + LOT]), "size": PAGE_SIZE}
        for lignes in lire_pages(f"{API}/communes_udi?{urllib.parse.urlencode(params)}"):
            # Toutes années, sans date : relu en entier. Zéro réseau ferait
            # un step vert « 0 réseau ».
            mesure.lu("hubeau-communes-udi", len(lignes),
                      mesure.forme_de(lignes), complete=True)
            conn.executemany(
                "INSERT OR IGNORE INTO eau_potable_udi"
                " (annee, code_commune, nom_quartier, code_reseau, nom_reseau, debut_alim)"
                " VALUES (?,?,?,?,?,?)",
                [ligne_udi(u) for u in lignes])
        conn.commit()
    marques = ",".join("?" * len(communes))
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT code_reseau FROM eau_potable_udi"
        f" WHERE code_commune IN ({marques}) ORDER BY code_reseau", communes)]


def depart(conn, code_reseau: str, since: str | None) -> str:
    """Date de reprise : `since`, sinon le dernier prélèvement lu par la
    requête de ce réseau — jamais celui d'un rattachement (cf. en-tête)."""
    if since:
        return since
    row = conn.execute("SELECT dernier_prelevement FROM eau_potable_suivi"
                       " WHERE code_reseau=?", (code_reseau,)).fetchone()
    return (row[0] if row else DEBUT)[:10]


def avancer_suivi(conn, code_reseau: str, plus_recent: str | None) -> None:
    """Avance le suivi du réseau jusqu'à `plus_recent` ; ne le recule jamais."""
    if plus_recent:
        conn.execute(
            "INSERT INTO eau_potable_suivi (code_reseau, dernier_prelevement)"
            " VALUES (?,?) ON CONFLICT(code_reseau) DO UPDATE SET"
            " dernier_prelevement=max(dernier_prelevement, excluded.dernier_prelevement),"
            " releve_le=datetime('now')", (code_reseau, plus_recent))


def fetch_resultats(conn, lot: list[str], since: str) -> int:
    """Le contrôle sanitaire d'un lot de réseaux, de `since` à aujourd'hui, en
    une requête paginée. Le suivi de chaque réseau n'avance qu'une fois la
    requête lue en entier : une page qui échoue laisse la reprise où elle était."""
    params = {"code_reseau": ",".join(lot), "date_min_prelevement": since,
              "size": PAGE_SIZE, "fields": CHAMPS_RESULTAT}
    nouveaux = 0
    plus_recent: dict[str, str] = {}
    for lignes in lire_pages(f"{API}/resultats_dis?{urllib.parse.urlencode(params)}"):
        # Depuis la dernière date lue : la forme sert, le volume n'alerte pas.
        mesure.lu("hubeau-resultats", len(lignes), mesure.forme_de(lignes))
        nouveaux += enregistrer_resultats(conn, lignes, lot)
        conn.commit()
        for r in lignes:
            date = r.get("date_prelevement")
            for code in demandeurs(r, lot) if date else ():
                plus_recent[code] = max(plus_recent.get(code, date), date)
    for code in lot:
        avancer_suivi(conn, code, plus_recent.get(code))
    conn.commit()
    return nouveaux


def lire_lot(conn, lot: list[str], since: str) -> int:
    try:
        return fetch_resultats(conn, lot, since)
    except LotAmbigu as e:
        print(f"  [eau_potable] prélèvement {e} sans réseau du lot : relu réseau par réseau")
        return sum(fetch_resultats(conn, [code], since) for code in lot)


def run(since: str | None = None):
    conn = get_conn()
    ensure_tables(conn)
    communes = communes_du_step("eau_potable")
    reseaux = fetch_udi(conn, communes)
    print(f"[eau_potable] {len(reseaux)} réseau(x) pour {len(communes)} commune(s)")
    # Un lot partage une date de reprise : celle de chacun de ses réseaux.
    par_depart: dict[str, list[str]] = {}
    for code in reseaux:
        par_depart.setdefault(depart(conn, code, since), []).append(code)
    total, echecs = 0, []
    for debut, codes in sorted(par_depart.items()):
        for i in range(0, len(codes), LOT):
            lot = codes[i:i + LOT]
            noms = ", ".join(f"{c} {nom_reseau(conn, c)}" for c in lot)
            try:
                n = lire_lot(conn, lot, debut)
            except Exception as e:
                echecs += lot
                print(f"  [eau_potable] échec depuis {debut} : {noms} → {e}")
                continue
            total += n
            print(f"  ✓ depuis {debut} : {noms} — {n} nouveau(x) résultat(s)")
    conn.close()
    # Un réseau qui n'a pas répondu ne publie pas « aucun résultat » : le
    # 27/10/2026, le seul réseau de Mascaras rendait 503 et le step finissait
    # « OK — 0 résultats insérés ».
    if echecs:
        raise RuntimeError(f"réseau(x) en échec : {', '.join(echecs)}")
    print(f"[eau_potable] OK — {total} résultats insérés")


def nom_reseau(conn, code: str) -> str:
    row = conn.execute("SELECT nom_reseau FROM eau_potable_udi WHERE code_reseau=?"
                       " ORDER BY annee DESC LIMIT 1", (code,)).fetchone()
    return (row[0] if row else None) or "—"


def show_stats():
    conn = get_conn(read_only=True)
    print("Réseaux desservant les communes suivies (dernière année publiée) :")
    for r in conn.execute("""
        SELECT code_commune, code_reseau, nom_reseau,
               group_concat(DISTINCT NULLIF(nom_quartier,'')), MIN(annee), MAX(annee)
        FROM eau_potable_udi GROUP BY code_commune, code_reseau"""):
        print(f"  {r[0]} {r[1]} {r[2] or '—':42} {r[4]}→{r[5]}  quartiers : {r[3] or '—'}")
    print("\nContrôle sanitaire par réseau :")
    for r in conn.execute("""
        SELECT r.code_reseau, COUNT(DISTINCT p.code_prelevement),
               MIN(p.date_prelevement), MAX(p.date_prelevement),
               SUM(p.limites_bact='N'), SUM(p.limites_pc='N'),
               group_concat(DISTINCT p.nom_moa), group_concat(DISTINCT p.nom_distributeur)
        FROM eau_potable_prelevement_reseaux r JOIN eau_potable_prelevements p
             USING(code_prelevement)
        GROUP BY r.code_reseau"""):
        print(f"  {r[0]}  {r[1]:>4} prélèvements  {(r[2] or '—')[:10]} → {(r[3] or '—')[:10]}"
              f"  hors limites : bact {r[4]}, physico-chimie {r[5]}")
        print(f"      maître d'ouvrage : {r[6] or '—'} · exploitant : {r[7] or '—'}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Hub'Eau — l'eau du robinet")
    ap.add_argument("--since", default=None,
                    help="date plancher AAAA-MM-JJ (défaut : incrémental)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        show_stats()
    else:
        run(args.since)
