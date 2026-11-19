"""
loyers.py — Loyers d'annonce par commune, charges comprises, au m² (« Carte des
loyers » 2025, ministère du Logement, DHUP).

Le ministère estime pour chaque commune un loyer d'annonce au m², pour un
appartement (tous, 1-2 pièces, 3 pièces et plus) et pour une maison, à partir
d'annonces en ligne (note méthodologique jointe au jeu). « Se loger » y lit ce que coûte la
location dans la commune, à côté du parc social (`rpls`) et de la vacance
(`lovac`).

─────────────────────────────────────────────────────────────────────────────
DANS UN VILLAGE, LE CHIFFRE EST CELUI D'UNE MAILLE

Une commune qui a trop peu d'annonces reçoit l'estimation de sa MAILLE, un
regroupement de communes voisines (`TYPPRED = maille`) : à Lasalle, 16
annonces de maisons dans la commune, 1 369 dans la maille. Le chiffre n'est
alors pas « le loyer de la commune » ; la page doit le dire, nombre d'annonces
à l'appui. Les deux nombres et la qualité de l'ajustement (R² ajusté) sont
gardés, avec l'intervalle de prédiction.

UN MILLÉSIME, UN JEU

Chaque année a son propre jeu data.gouv, et la méthode change d'un millésime à
l'autre : ils ne se mettent pas bout à bout sans précaution. Seul 2025 est lu
(décision du 29/10/2026) ; en ajouter un se fait dans `JEUX`.

Fichiers en ISO-8859-1, séparateur « ; », virgule décimale.

Source : data.gouv.fr, jeu « Carte des loyers » (DHUP), Licence Ouverte v2.
Mention imposée : « Estimations ANIL, à partir des données du Groupe SeLoger et
de leboncoin » (`MENTION`).

Usage :
  python3 -m collectors.loyers
  python3 -m collectors.loyers --stats
"""
import argparse
import csv
import io

from . import absences, mesure
from .archive import fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import deposer, retirer_hors_magasin

SOURCE = "loyers"
JEU = "Carte des loyers (ministère du Logement, estimations ANIL)"
# Mention imposée par la fiche du jeu, à reprendre MOT POUR MOT partout où un
# chiffre de ce jeu est affiché.
MENTION = "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin"
# millésime → (identifiant du jeu, slug pour la page)
JEUX = {
    2025: ("693aa2feed1bf4da603faa49",
           "carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025"),
}
CACHE = NATIONAL_STORE / "loyers"

# Un jeu par an, publié en fin d'année ; une republication corrigée doit
# pouvoir remonter.
CACHE_JOURS = 90

# Début du nom de fichier → type de bien rangé en base. L'ordre compte : `app12`
# et `app3` commencent aussi par `app`.
FICHIERS = (
    ("pred-app12", "appartement_1_2_pieces"),
    ("pred-app3", "appartement_3_pieces_et_plus"),
    ("pred-app", "appartement"),
    ("pred-mai", "maison"),
)


def _nombre(brut: str | None) -> float | None:
    brut = (brut or "").strip()
    return float(brut.replace(",", ".")) if brut and brut != "NA" else None


def _type_de_bien(url: str) -> str | None:
    nom = url.rsplit("/", 1)[-1]
    return next((t for prefixe, t in FICHIERS if nom.startswith(prefixe)), None)


def lire(texte: str, codes: set[str]) -> list[dict]:
    """Les estimations des communes demandées ; un format changé lève.

    Lu de travers, le fichier ne contiendrait « aucune » commune, et chacune
    serait notée absente d'un jeu qui la couvre.
    """
    sortie = []
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    manquantes = [c for c in ("INSEE_C", "loypredm2", "TYPPRED")
                  if c not in (lecteur.fieldnames or [])]
    if manquantes:
        raise RuntimeError(f"Loyers : colonne(s) {manquantes} absente(s) du fichier")
    lues = 0
    for rang in lecteur:
        lues += 1
        insee = (rang.get("INSEE_C") or "").strip().zfill(5)
        if insee not in codes:
            continue
        nb_com, nb_maille = _nombre(rang.get("nbobs_com")), _nombre(rang.get("nbobs_mail"))
        sortie.append({
            "insee": insee,
            "loyer_m2": _nombre(rang.get("loypredm2")),
            "bas_m2": _nombre(rang.get("lwr.IPm2")),
            "haut_m2": _nombre(rang.get("upr.IPm2")),
            "echelle": (rang.get("TYPPRED") or "").strip() or None,
            "annonces_commune": int(nb_com) if nb_com is not None else None,
            "annonces_maille": int(nb_maille) if nb_maille is not None else None,
            "r2_ajuste": _nombre(rang.get("R2_adj")),
        })
    # Les quatre fichiers nationaux (un par type de bien) se somment.
    mesure.lu(SOURCE, lues, lecteur.fieldnames, complete=True)
    return sortie


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS loyers_annonces (
            insee             TEXT NOT NULL,
            millesime         INTEGER NOT NULL,
            type_bien         TEXT NOT NULL,  -- appartement | appartement_1_2_pieces | appartement_3_pieces_et_plus | maison
            loyer_m2          REAL,           -- charges comprises
            bas_m2            REAL,           -- intervalle de prédiction (lwr.IPm2, upr.IPm2)
            haut_m2           REAL,
            echelle           TEXT,           -- commune | maille : où l'estimation a été faite
            annonces_commune  INTEGER,
            annonces_maille   INTEGER,
            r2_ajuste         REAL,
            source_url        TEXT,
            releve_le         TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, millesime, type_bien)
        )
    """)
    conn.commit()


def run() -> int:
    codes = set(communes_du_step(SOURCE))
    millesime = max(JEUX)
    dataset, slug = JEUX[millesime]
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{dataset}/",
                       source=SOURCE, timeout=60)
    lectures = []
    for res in fiche.get("resources", []):
        url = res.get("url") or ""
        type_bien = _type_de_bien(url)
        if (res.get("format") or "").lower() != "csv" or not type_bien:
            continue
        fichier = CACHE / f"{millesime}-{url.rsplit('/', 1)[-1]}"
        chemin = deposer(url, fichier, SOURCE, CACHE_JOURS)
        try:
            lectures.append((url, type_bien, lire(chemin.read_text(encoding="latin-1"), codes)))
        finally:
            retirer_hors_magasin(chemin, fichier)
    if len(lectures) != len(FICHIERS):
        raise RuntimeError(f"Loyers {millesime} : {len(lectures)} fichier(s) reconnu(s)"
                           f" sur {len(FICHIERS)} attendus")
    trouvees = set()
    with transaction() as conn:
        ensure_table(conn)
        for url, type_bien, lignes in lectures:
            conn.executemany(
                "INSERT OR REPLACE INTO loyers_annonces (insee, millesime, type_bien, loyer_m2,"
                " bas_m2, haut_m2, echelle, annonces_commune, annonces_maille, r2_ajuste,"
                " source_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(l["insee"], millesime, type_bien, l["loyer_m2"], l["bas_m2"], l["haut_m2"],
                  l["echelle"], l["annonces_commune"], l["annonces_maille"], l["r2_ajuste"], url)
                 for l in lignes])
            trouvees |= {l["insee"] for l in lignes}
        absences.noter(conn, SOURCE, codes, trouvees, jeu=JEU,
                       page_jeu=f"https://www.data.gouv.fr/datasets/{slug}",
                       source_url=lectures[0][0], millesime=millesime)
    n = sum(len(l) for _, _, l in lectures)
    print(f"  [loyers] {n} valeur(s) {millesime} sur {len(trouvees)}/{len(codes)} commune(s)")
    return n


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, millesime, type_bien, loyer_m2, echelle, annonces_commune, annonces_maille
          FROM loyers_annonces ORDER BY insee, millesime, type_bien
    """):
        print(f"  {r['insee']} {r['millesime']}  {r['type_bien']:<30} {r['loyer_m2']:.2f} €/m²"
              f"  ({r['echelle']} : {r['annonces_commune']} annonces dans la commune,"
              f" {r['annonces_maille']} dans la maille)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run()
