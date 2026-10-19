"""
lovac.py — Logements vacants du parc privé, par commune (LOVAC, open data).

Le recensement compte tout logement inoccupé au 1er janvier, sans dire depuis
quand : un bien entre deux locataires y pèse autant qu'une maison fermée depuis
dix ans. LOVAC sépare les deux — la vacance de plus de deux ans, dite
STRUCTURELLE, est celle que vise le plan national de lutte contre les logements
vacants. Le Cerema la publie par commune, en données ouvertes ; seul le niveau
du logement, qui désigne des biens et leurs propriétaires, reste sous
convention. Ce collecteur ne lit que le fichier des communes : il n'y a aucune
adresse à en tirer.

─────────────────────────────────────────────────────────────────────────────
« s » N'EST PAS ZÉRO

Sous 11 logements, le chiffre est secrétisé et le fichier écrit « s ». Le
stocker à 0 transformerait « entre 1 et 10, on ne sait pas » en « aucun » — un
zéro qui viendrait d'une absence, cf. [[feedback-un-zero-qui-vient-dune-absence]].
La cellule est donc gardée TELLE QUELLE dans `brut`, et `valeur` reste NULL.
Une colonne que le millésime ne publie pas (le parc privé 2026 n'existe pas
encore) ne fait aucune ligne : une absence n'est pas un secret.

LA SÉRIE A DEUX RUPTURES

2023 : la fin de la taxe d'habitation sur les résidences principales fait
reposer la base sur le seul dispositif « Gérer mes biens immobiliers ».
2025 : le fichier fiscal d'origine est pris avant la chaîne de taxation, et ne
compte plus les vacants « par défaut ». Les millésimes de part et d'autre ne
mesurent pas la même chose : `RUPTURES` les nomme, et l'export les porte jusqu'à
la page, qui doit les marquer sur le graphique.

⚠️ LES NOMS DE COLONNES PORTENT L'ANNÉE (`pp_vacant_26`, `CODGEO_26`). Le
prochain dépôt ajoutera un millésime et changera le suffixe du code commune :
les colonnes sont reconnues par motif, jamais nommées une à une.

Source : data.gouv.fr, jeu du ministère de la Transition écologique (Cerema
pour la DGALN), Licence Ouverte v2. Fichier national de 7 Mo en ISO-8859-1,
déposé au magasin partagé.

Usage :
  python3 -m collectors.lovac
  python3 -m collectors.lovac --insee 30140
  python3 -m collectors.lovac --stats
"""
import argparse
import csv
import io
import re
import urllib.request

from .archive import HEADERS, archive_fetch, fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import copier_atomiquement, est_frais

SOURCE = "lovac"
# L'identifiant, pas le slug : le slug porte « 2020-a-2026 » et changera avec
# le prochain millésime.
DATASET = "61816c6e23197bb34835228e"
PAGE_JEU = ("https://www.data.gouv.fr/datasets/"
            "logements-vacants-du-parc-prive-par-commune-departement-region-france-de-2020-a-2026")
CACHE = NATIONAL_STORE / "lovac"
FICHIER = CACHE / "lovac-communes.csv"

# Un dépôt par an, à l'été.
CACHE_JOURS = 90

SECRET = "s"

# Préfixe de colonne → indicateur rangé en base. Le préfixe est comparé ENTIER :
# `pp_vacant` ne doit pas reconnaître `pp_vacant_plus_2ans`.
INDICATEURS = {
    "pp_vacant_plus_2ans": "vacants_plus_2ans",
    "pp_vacant":           "vacants",
    "ff_pp_total":         "parc_prive",
}
_COLONNE = re.compile(r"^(?P<prefixe>[a-z_0-9]+?)_(?P<aa>\d{2})$")
_CODE = re.compile(r"^CODGEO_\d{2}$")

# Premier millésime de chaque nouvelle définition, et pourquoi.
RUPTURES = {
    2023: "fin de la taxe d'habitation sur les résidences principales : "
          "la base repose sur le seul dispositif « Gérer mes biens immobiliers »",
    2025: "fichier fiscal pris avant la chaîne de taxation : seuls restent les "
          "logements déclarés vacants par leur propriétaire",
}


def _telecharger() -> str:
    """Dépose le fichier des communes au magasin, rend l'URL de la ressource.

    L'URL porte la date de dépôt : elle est LUE dans la fiche du jeu.
    """
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{DATASET}/",
                       source=SOURCE, timeout=60)
    url = None
    for res in fiche.get("resources", []):
        if ((res.get("format") or "").lower() == "csv"
                and "commune" in (res.get("title") or "").lower()):
            url = res.get("url")
            break
    if not url:
        raise RuntimeError("LOVAC : pas de fichier des communes au catalogue")
    if est_frais(FICHIER, CACHE_JOURS):
        return url
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=300) as r:
        copier_atomiquement(FICHIER, r)
        archive_fetch(SOURCE, url, b"", content_type="text/csv", http_status=r.status)
    print(f"  [lovac] fichier national déposé : {FICHIER.name}")
    return url


def _colonne(nom: str) -> tuple[str, int] | None:
    """`pp_vacant_26` → ("vacants", 2026) ; None pour une colonne hors série."""
    m = _COLONNE.match(nom.strip())
    indicateur = INDICATEURS.get(m["prefixe"]) if m else None
    return (indicateur, 2000 + int(m["aa"])) if indicateur else None


def lire(texte: str, codes: set[str]) -> list[tuple[str, int, str, str, int | None]]:
    """(insee, millésime, indicateur, brut, valeur) des communes demandées."""
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    entetes = lecteur.fieldnames or []
    code = next((c for c in entetes if _CODE.match(c.strip())), None)
    if not code:
        raise RuntimeError(f"LOVAC : colonne du code commune introuvable ({entetes[:3]})")
    series = {c: _colonne(c) for c in entetes}
    lignes = []
    for rang in lecteur:
        insee = (rang.get(code) or "").strip()
        if insee not in codes:
            continue
        for colonne, cle in series.items():
            if not cle:
                continue
            brut = (rang.get(colonne) or "").strip()
            if not brut:
                continue
            valeur = None if brut == SECRET else int(brut)
            lignes.append((insee, cle[1], cle[0], brut, valeur))
    return lignes


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lovac_vacance (
            insee       TEXT NOT NULL,
            millesime   INTEGER NOT NULL,
            indicateur  TEXT NOT NULL,   -- vacants | vacants_plus_2ans | parc_prive
            brut        TEXT NOT NULL,   -- la cellule du fichier, « s » compris
            valeur      INTEGER,         -- NULL quand brut = « s »
            source_url  TEXT,
            releve_le   TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, millesime, indicateur)
        )
    """)
    conn.commit()


def run(insee: str | None = None) -> int:
    url = _telecharger()
    codes = {insee} if insee else set(communes_du_step("lovac"))
    lignes = lire(FICHIER.read_text(encoding="latin-1"), codes)
    with transaction() as conn:
        ensure_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO lovac_vacance"
            " (insee, millesime, indicateur, brut, valeur, source_url)"
            " VALUES (?,?,?,?,?,?)",
            [(*l, url) for l in lignes])
    trouvees = {l[0] for l in lignes}
    secrets = sum(1 for l in lignes if l[4] is None)
    print(f"  [lovac] {len(lignes)} valeur(s) sur {len(trouvees)}/{len(codes)} commune(s)"
          + (f", dont {secrets} sous secret statistique" if secrets else ""))
    for absente in sorted(codes - trouvees):
        print(f"  [lovac] {absente} absente du fichier")
    return len(lignes)


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, millesime,
               MAX(CASE WHEN indicateur='parc_prive' THEN brut END) parc,
               MAX(CASE WHEN indicateur='vacants' THEN brut END) vac,
               MAX(CASE WHEN indicateur='vacants_plus_2ans' THEN brut END) vac2
          FROM lovac_vacance GROUP BY insee, millesime ORDER BY insee, millesime
    """):
        print(f"  {r['insee']} {r['millesime']}  parc privé {r['parc'] or '—':>6}"
              f"  vacants {r['vac'] or '—':>5}  dont > 2 ans {r['vac2'] or '—':>5}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insee", help="une seule commune")
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run(a.insee)
