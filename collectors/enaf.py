"""
enaf.py — Consommation d'espaces naturels, agricoles et forestiers, par commune
et par année (Cerema, fichiers fonciers).

C'est la mesure de la loi Climat et résilience : l'objectif « zéro
artificialisation nette » se compte d'abord en hectares d'ENAF consommés depuis
2011, réduits de moitié sur 2021-2031 par rapport à 2011-2021. Le Cerema la
publie par commune, année par année, et ventile chaque année par destination :
habitat, activité, mixte, route, fer, inconnue. Aucune parcelle ni aucun
propriétaire n'en sort : le fichier des communes est un agrégat.

─────────────────────────────────────────────────────────────────────────────
UNE ANNÉE VA DU 1er JANVIER AU 1er JANVIER

`naf14art15` = ENAF devenus artificialisés entre le 1er janvier 2014 et le
1er janvier 2015. La base range l'année de DÉBUT (2014). Le fichier porte
aussi des cumuls (`naf11art25`, `naf11art21`, `naf21art25`) : ils ne sont pas
lus, ils se recalculent et deux chiffres pour une même chose finissent par
diverger.

L'UNITÉ EST LE MÈTRE CARRÉ

Le fichier compte en m². La page convertira ; la base garde ce que dit la
source.

⚠️ LES NOMS DE COLONNES PORTENT LES ANNÉES : le prochain dépôt ajoutera
`naf25art26`. Les colonnes sont reconnues par motif, jamais nommées une à une,
et le millésime du fichier est la dernière année de fin lue.

Source : data.gouv.fr, jeu du Cerema, Licence Ouverte v2. Fichier national
`conso-com.csv` (35 000 communes, ~33 Mo), déposé au magasin partagé.
Dictionnaire : https://doc-datafoncier.cerema.fr/doc/conso-enaf/

Usage :
  python3 -m collectors.enaf
  python3 -m collectors.enaf --insee 30140
  python3 -m collectors.enaf --stats
"""
import argparse
import csv
import io
import re
from pathlib import Path

from . import absences, mesure
from .archive import fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import deposer, retirer_hors_magasin

SOURCE = "enaf"
JEU = "Consommation d'espaces naturels, agricoles et forestiers (Cerema)"
# L'identifiant, pas le slug : le slug porte « 2011 au 1er janvier 2025 ».
DATASET = "6a01b9280dd2d45907e5fc61"
PAGE_JEU = ("https://www.data.gouv.fr/datasets/"
            "consommation-despaces-naturels-agricoles-et-forestiers-du-1er-janvier-2011-au-1er-janvier-2025")
CACHE = NATIONAL_STORE / "enaf"
FICHIER = CACHE / "conso-com.csv"

# Un dépôt par an.
CACHE_JOURS = 180

# Code de la colonne → destination rangée en base. `naf…art…` est le total.
DESTINATIONS = {
    "art": "total",
    "hab": "habitat",
    "act": "activite",
    "mix": "mixte",
    "rou": "route",
    "fer": "fer",
    "inc": "inconnue",
}
_COLONNE = re.compile(r"^(?P<de>naf|art)(?P<a1>\d{2})(?P<vers>[a-z]{3})(?P<a2>\d{2})$")


def _telecharger() -> tuple[str, Path]:
    """L'URL du fichier des communes, lue dans la fiche du jeu, et le fichier à lire."""
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{DATASET}/",
                       source=SOURCE, timeout=60)
    url = None
    for res in fiche.get("resources", []):
        if ((res.get("format") or "").lower() == "csv"
                and (res.get("title") or "").lower().startswith("conso_com")):
            url = res.get("url")
            break
    if not url:
        raise RuntimeError("ENAF : pas de fichier des communes au catalogue")
    return url, deposer(url, FICHIER, SOURCE, CACHE_JOURS)


def _colonne(nom: str) -> tuple[str, int] | None:
    """`art14hab15` → ("habitat", 2014) ; None pour un cumul ou une colonne hors série."""
    m = _COLONNE.match(nom.strip())
    if not m or int(m["a2"]) != int(m["a1"]) + 1:
        return None
    if (m["de"] == "naf") != (m["vers"] == "art"):
        return None
    destination = DESTINATIONS.get(m["vers"])
    return (destination, 2000 + int(m["a1"])) if destination else None


def lire(texte: str, codes: set[str]) -> tuple[list[tuple[str, int, str, float]], int]:
    """(insee, année, destination, m²) des communes demandées, et le millésime.

    Le millésime est l'année du 1er janvier qui clôt la série (2025 pour le
    fichier « 2011 au 1er janvier 2025 »).
    """
    lecteur = csv.DictReader(io.StringIO(texte))
    entetes = lecteur.fieldnames or []
    if "idcom" not in entetes:
        raise RuntimeError(f"ENAF : colonne idcom introuvable ({entetes[:3]})")
    series = {c: _colonne(c) for c in entetes}
    if not any(series.values()):
        raise RuntimeError("ENAF : aucune colonne annuelle reconnue")
    millesime = max(cle[1] for cle in series.values() if cle) + 1
    lignes, lues = [], 0
    for rang in lecteur:
        lues += 1
        insee = (rang.get("idcom") or "").strip().zfill(5)
        if insee not in codes:
            continue
        for colonne, cle in series.items():
            brut = (rang.get(colonne) or "").strip()
            if cle and brut:
                lignes.append((insee, cle[1], cle[0], float(brut)))
    # Les communes de France, avant le filtre ; un millésime neuf ajoute des
    # colonnes, pas des lignes.
    mesure.lu(SOURCE, lues, entetes, complete=True)
    return lignes, millesime


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enaf_consommation (
            insee        TEXT NOT NULL,
            annee        INTEGER NOT NULL,  -- du 1er janvier annee au 1er janvier annee+1
            destination  TEXT NOT NULL,     -- total | habitat | activite | mixte | route | fer | inconnue
            m2           REAL NOT NULL,
            millesime    INTEGER NOT NULL,  -- 1er janvier qui clôt le fichier lu
            source_url   TEXT,
            releve_le    TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, annee, destination)
        )
    """)
    conn.commit()


def run(insee: str | None = None) -> int:
    url, chemin = _telecharger()
    codes = {insee} if insee else set(communes_du_step("enaf"))
    try:
        lignes, millesime = lire(chemin.read_text(encoding="utf-8"), codes)
    finally:
        retirer_hors_magasin(chemin, FICHIER)
    trouvees = {l[0] for l in lignes}
    with transaction() as conn:
        ensure_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO enaf_consommation"
            " (insee, annee, destination, m2, millesime, source_url) VALUES (?,?,?,?,?,?)",
            [(*l, millesime, url) for l in lignes])
        if not insee:
            absences.noter(conn, SOURCE, codes, trouvees, jeu=JEU, page_jeu=PAGE_JEU,
                           source_url=url, millesime=millesime)
    print(f"  [enaf] {len(lignes)} valeur(s) sur {len(trouvees)}/{len(codes)} commune(s),"
          f" fichier au 1er janvier {millesime}")
    return len(lignes)


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, MIN(annee) de, MAX(annee) a, ROUND(SUM(m2)/10000, 2) ha
          FROM enaf_consommation WHERE destination='total' GROUP BY insee ORDER BY insee
    """):
        print(f"  {r['insee']}  {r['de']}-{r['a']}  {r['ha']} ha consommés")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insee", help="une seule commune")
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run(a.insee)
