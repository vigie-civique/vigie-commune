"""
cantines.py — Cantines de la commune et leur part de produits bio et durables
(registre national et télédéclarations EGalim, ministère de l'Agriculture).

La loi EGalim impose à la restauration collective 50 % de produits durables et
de qualité, dont 20 % de bio, en valeur d'achat. Chaque cantine le déclare sur
« ma cantine » ; le ministère publie le registre des cantines et, campagne par
campagne, les taux télédéclarés. Le dossier « Grandir » dit si l'école de la
commune a déclaré, et à combien elle est du seuil.

─────────────────────────────────────────────────────────────────────────────
PAS DE NOM QUI POURRAIT ÊTRE CELUI D'UNE PERSONNE

Une cantine privée peut être tenue par un entrepreneur individuel : sa raison
sociale est alors un nom de personne. Le nom et le SIRET ne sont gardés que
pour les cantines PUBLIQUES (`economic_model = public`) ; une cantine privée
est comptée, son secteur et ses repas aussi, sans son nom. L'identifiant du
déclarant (`applicant_id`), qui désigne un compte utilisateur, n'est jamais lu.

UNE DÉCLARATION DE CUISINE CENTRALE VAUT POUR SES SATELLITES

`teledeclaration_mode = CENTRAL_ALL` : la cuisine centrale déclare pour les
cantines qu'elle livre, et la ligne porte la commune de la CUISINE. L'école
d'une commune livrée par la cuisine d'à côté n'a donc pas de ligne à elle : le
registre le dit par `central_producer_siret`, gardé pour les cantines publiques.

« N'A PAS DÉCLARÉ » N'EST PAS UNE LACUNE DE LA SOURCE

Une commune absente du REGISTRE est une lacune (rien n'est connu). Une cantine
du registre sans télédéclaration est un fait : elle n'a pas déclaré.

Source : data.gouv.fr, jeux « Registre national des cantines » et « Résultats
de campagnes de télédéclaration des cantines » (MASA, ma-cantine), Licence
Ouverte v2.

Usage :
  python3 -m collectors.cantines
  python3 -m collectors.cantines --stats
"""
import argparse
import csv
import io
import re

from . import absences, mesure
from .archive import fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import deposer, retirer_hors_magasin

SOURCE = "cantines"
JEU = "Registre national des cantines (MASA)"
DATASET_REGISTRE = "registre-national-des-cantines"
DATASET_TD = "resultats-de-campagnes-de-teledeclaration-des-cantines"
PAGE_REGISTRE = f"https://www.data.gouv.fr/datasets/{DATASET_REGISTRE}"
PAGE_TD = f"https://www.data.gouv.fr/datasets/{DATASET_TD}"
CACHE = NATIONAL_STORE / "cantines"

# Le registre est republié chaque nuit ; une campagne se clôt une fois par an.
REGISTRE_JOURS = 7
TD_JOURS = 30

PUBLIC = "public"
_DECLARATION = re.compile(r"^declaration_donnees_(\d{4})$")
_ANNEE = re.compile(r"\((?:données )?(\d{4})\)")


def _csv(dataset: str) -> list[tuple[str, str, str]]:
    """(URL, titre, date de mise à jour) des ressources CSV d'un jeu.

    La date est celle que le catalogue donne au FICHIER : c'est le millésime
    d'une absence. Le tirer des lignes trouvées le laissait vide précisément
    quand la commune n'a aucune cantine, et `/couverture` écrivait « millésime ».
    """
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{dataset}/",
                       source=SOURCE, timeout=60)
    return [(r["url"], r.get("title") or "",
             (r.get("last_modified") or fiche.get("last_modified") or "")[:10])
            for r in fiche.get("resources", [])
            if (r.get("format") or "").lower() == "csv"]


def _verifier_entetes(lecteur: csv.DictReader, attendues: tuple[str, ...], quoi: str):
    """Un format changé lève : lu de travers, il ferait de chaque commune une absente."""
    manquantes = [c for c in attendues if c not in (lecteur.fieldnames or [])]
    if manquantes:
        raise RuntimeError(f"Cantines : colonne(s) {manquantes} absente(s) du {quoi}")


def _entier(brut: str | None) -> int | None:
    brut = (brut or "").strip()
    return int(float(brut)) if brut else None


def _taux(brut: str | None) -> float | None:
    brut = (brut or "").strip()
    return float(brut) if brut else None


def lire_registre(texte: str, codes: set[str]) -> list[dict]:
    """Les cantines des communes demandées ; nom et SIRET des seules publiques."""
    sortie = []
    lecteur = csv.DictReader(io.StringIO(texte))
    _verifier_entetes(lecteur, ("id", "city_insee_code", "economic_model"), "registre")
    lues = 0
    for rang in lecteur:
        lues += 1
        insee = (rang.get("city_insee_code") or "").strip().zfill(5)
        if insee not in codes:
            continue
        publique = (rang.get("economic_model") or "").strip() == PUBLIC
        declarees = sorted(int(m[1]) for k, v in rang.items()
                           if (m := _DECLARATION.match(k or "")) and v == "True")
        sortie.append({
            "id": int(rang["id"]),
            "insee": insee,
            "nom": rang.get("name") if publique else None,
            "siret": rang.get("siret") if publique else None,
            "cuisine_centrale_siret": (rang.get("central_producer_siret") or None)
                                      if publique else None,
            "modele": rang.get("economic_model") or None,
            "gestion": rang.get("management_type") or None,
            "production": rang.get("production_type") or None,
            "secteurs": rang.get("sector_list") or None,
            "repas_jour": _entier(rang.get("daily_meal_count")),
            "repas_an": _entier(rang.get("yearly_meal_count")),
            "annees_declarees": ",".join(map(str, declarees)) or None,
            "modifie_le": rang.get("modification_date") or None,
        })
    # Le registre national, lu au magasin ou neuf, avant le filtre communal.
    mesure.lu("cantines-registre", lues, lecteur.fieldnames, complete=True)
    return sortie


def lire_teledeclarations(texte: str, codes: set[str]) -> list[dict]:
    """Les télédéclarations des cantines situées dans les communes demandées."""
    sortie = []
    lecteur = csv.DictReader(io.StringIO(texte))
    _verifier_entetes(lecteur, ("id", "year", "canteen_city_insee_code",
                                "canteen_economic_model"), "fichier de télédéclarations")
    lues = 0
    for rang in lecteur:
        lues += 1
        insee = (rang.get("canteen_city_insee_code") or "").strip().zfill(5)
        if insee not in codes:
            continue
        publique = (rang.get("canteen_economic_model") or "").strip() == PUBLIC
        sortie.append({
            "id": int(rang["id"]),
            "annee": int(rang["year"]),
            "cantine_id": _entier(rang.get("canteen_id")),
            "insee": insee,
            "nom": rang.get("canteen_name") if publique else None,
            "siret": rang.get("canteen_siret") if publique else None,
            "modele": rang.get("canteen_economic_model") or None,
            "mode": rang.get("teledeclaration_mode") or None,
            "satellites": _entier(rang.get("canteen_satellite_canteens_count")),
            "secteurs": rang.get("canteen_sector_list") or None,
            "part_bio": _taux(rang.get("teledeclaration_ratio_bio")),
            "part_egalim_hors_bio": _taux(rang.get("teledeclaration_ratio_egalim_hors_bio")),
        })
    # Un fichier par campagne, sommés : une campagne neuve fait monter le compte.
    mesure.lu("cantines-teledeclarations", lues, lecteur.fieldnames, complete=True)
    return sortie


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cantines_registre (
            id                      INTEGER PRIMARY KEY,  -- identifiant ma-cantine
            insee                   TEXT NOT NULL,
            nom                     TEXT,     -- cantines publiques seulement
            siret                   TEXT,     -- idem
            cuisine_centrale_siret  TEXT,     -- idem
            modele                  TEXT,     -- public | private
            gestion                 TEXT,     -- direct | conceded
            production              TEXT,
            secteurs                TEXT,
            repas_jour              INTEGER,
            repas_an                INTEGER,
            annees_declarees        TEXT,     -- « 2023,2024 » d'après le registre
            modifie_le              TEXT,
            source_url              TEXT,
            releve_le               TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cantines_teledeclarations (
            id                    INTEGER PRIMARY KEY,
            annee                 INTEGER NOT NULL,   -- année des achats déclarés
            cantine_id            INTEGER,
            insee                 TEXT NOT NULL,      -- commune de la cantine déclarante
            nom                   TEXT,               -- cantines publiques seulement
            siret                 TEXT,
            modele                TEXT,
            mode                  TEXT,               -- CENTRAL_ALL : vaut pour les satellites
            satellites            INTEGER,
            secteurs              TEXT,
            part_bio              REAL,               -- 0 à 1, en valeur d'achat
            part_egalim_hors_bio  REAL,
            source_url            TEXT,
            releve_le             TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def _inserer(conn, table: str, lignes: list[dict], url: str):
    if not lignes:
        return
    colonnes = [*lignes[0], "source_url"]
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(colonnes)})"
        f" VALUES ({', '.join('?' * len(colonnes))})",
        [(*l.values(), url) for l in lignes])


def run() -> int:
    codes = set(communes_du_step(SOURCE))
    registre = [(u, d) for u, t, d in _csv(DATASET_REGISTRE) if "registre" in t.lower()]
    if not registre:
        raise RuntimeError("Cantines : pas de registre CSV au catalogue")
    url_registre, date_registre = registre[0]
    if not date_registre:
        raise RuntimeError("Cantines : date du registre introuvable au catalogue")
    fichier = CACHE / "registre_cantines.csv"
    chemin = deposer(url_registre, fichier, SOURCE, REGISTRE_JOURS)
    try:
        cantines = lire_registre(chemin.read_text(encoding="utf-8"), codes)
    finally:
        retirer_hors_magasin(chemin, fichier)

    declarations = []
    for url, titre, _ in _csv(DATASET_TD):
        m = _ANNEE.search(titre)
        if not m:
            continue
        fichier = CACHE / f"campagne_td_{m[1]}.csv"
        chemin = deposer(url, fichier, SOURCE, TD_JOURS)
        try:
            declarations.append((url, lire_teledeclarations(
                chemin.read_text(encoding="utf-8"), codes)))
        finally:
            retirer_hors_magasin(chemin, fichier)

    with transaction() as conn:
        ensure_tables(conn)
        # Le registre se relit en entier : une cantine fermée en sort.
        conn.execute(f"DELETE FROM cantines_registre WHERE insee IN"
                     f" ({', '.join('?' * len(codes))})", sorted(codes))
        _inserer(conn, "cantines_registre", cantines, url_registre)
        for url, lignes in declarations:
            _inserer(conn, "cantines_teledeclarations", lignes, url)
        absences.noter(conn, SOURCE, codes, {c["insee"] for c in cantines}, jeu=JEU,
                       page_jeu=PAGE_REGISTRE, source_url=url_registre,
                       millesime=date_registre)
    n_td = sum(len(l) for _, l in declarations)
    print(f"  [cantines] {len(cantines)} cantine(s), {n_td} télédéclaration(s)"
          f" sur {len(codes)} commune(s)")
    return len(cantines) + n_td


def stats():
    conn = get_conn()
    ensure_tables(conn)
    for r in conn.execute("""
        SELECT r.insee, COALESCE(r.nom, '(privée)') nom, r.secteurs, r.repas_an,
               r.annees_declarees
          FROM cantines_registre r ORDER BY r.insee, r.nom
    """):
        print(f"  {r['insee']}  {r['nom'][:40]:<40} {r['repas_an'] or '—':>6} repas/an"
              f"  déclaré : {r['annees_declarees'] or 'jamais'}")
    for r in conn.execute("""
        SELECT insee, annee, COALESCE(nom, '(privée)') nom, mode, part_bio, part_egalim_hors_bio
          FROM cantines_teledeclarations ORDER BY insee, annee
    """):
        bio = r["part_bio"]
        print(f"  {r['insee']} {r['annee']}  {r['nom'][:30]:<30} {r['mode'] or ''}"
              f"  bio {'—' if bio is None else f'{bio:.0%}'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run()
