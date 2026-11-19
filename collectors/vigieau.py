"""
vigieau.py — Restrictions d'eau en sécheresse, par commune : combien de jours à
chaque niveau, année par année (VigiEau, ministère de la Transition écologique).

Le préfet découpe le département en zones d'alerte et y fixe, par arrêté, un
niveau de restriction : vigilance, alerte, alerte renforcée, crise. VigiEau
reporte chaque jour le niveau en vigueur sur chaque commune, pour trois sortes
de ressource : l'eau potable distribuée (AEP), les eaux souterraines (SOU), les
eaux superficielles (SUP). Le dossier eau n'a pas à recopier les arrêtés : il
dit combien de jours la commune a passé en crise, et depuis quand ça dure.

─────────────────────────────────────────────────────────────────────────────
DEUX FICHIERS, UNE MÊME FORME

- L'année en cours, `restrictions-communes-<année>.zip` (~8 Mo compressés,
  ~560 Mo de JSON) : lue à chaque passe. Les années passées restent en base,
  la passe n'efface que l'année qu'elle relit.
- L'historique depuis le 1er mai 2010, `historique-communes.zip` (590 Mo
  compressés, 12 Go de JSON) : lu sur demande (`--historique`), une fois.

Les deux sont un tableau `[{"commune": {...}, "restrictions": [{date, AEP,
SOU, SUP}, …]}, …]`. Ni l'un ni l'autre ne tient en mémoire : le flux est
découpé sur `{"commune":` et seuls les objets des communes demandées sont
décodés.

UN JOUR SANS RESTRICTION EST UN JOUR RELEVÉ

`null` veut dire « aucune restriction ce jour-là », pas « pas de donnée ». Il
est compté (niveau `aucune`) : la somme des niveaux d'une année est le nombre
de jours que le fichier couvre, et « 40 jours de crise sur 258 relevés » se lit
sans deviner la fin de la série.

Source : data.gouv.fr, jeu « Donnée Sécheresse - VigiEau » (MTE), Licence
Ouverte v2. Aucune donnée personnelle.

Usage :
  python3 -m collectors.vigieau
  python3 -m collectors.vigieau --historique
  python3 -m collectors.vigieau --stats
"""
import argparse
import json
import re
import zipfile
from collections import Counter

from . import absences, mesure
from .archive import fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import deposer, retirer_hors_magasin

SOURCE = "vigieau"
JEU = "Restrictions d'eau en sécheresse (VigiEau, MTE)"
DATASET = "662a5e2cd71b24df5e9a0827"
PAGE_JEU = "https://www.data.gouv.fr/datasets/donnee-secheresse-vigieau"
CACHE = NATIONAL_STORE / "vigieau"

# Le fichier de l'année se republie chaque jour en saison.
CACHE_JOURS = 1
# L'historique ne bouge qu'à la marge ; il n'est relu que sur demande.
HISTORIQUE_JOURS = 365

ZONES = {
    "AEP": "eau potable distribuée",
    "SOU": "eaux souterraines",
    "SUP": "eaux superficielles",
}
NIVEAUX = ("aucune", "vigilance", "alerte", "alerte_renforcee", "crise")

# Le fichier national compte quelque 35 000 communes. En deçà de ce plancher, la
# lecture a raté (format changé, fichier tronqué) : noter chaque commune
# « absente » serait une affirmation fausse sur le jeu de l'État.
MIN_COMMUNES = 30_000
# Un objet commune pèse quelques centaines de Ko (historique compris). Un tampon
# qui dépasse ce plafond sans marqueur : format changé, et la mémoire du service
# n'y survivrait pas.
TAMPON_MAX = 64 << 20

_MARQUE = b'{"commune":'
_CODE = re.compile(rb'\{"commune":\{"code":"([^"]+)"')
_ANNEE = re.compile(r"(\d{4})")


def _ressources() -> tuple[tuple[str, int] | None, str | None]:
    """((URL, année) du fichier de l'année la plus récente, URL de l'historique)."""
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{DATASET}/",
                       source=SOURCE, timeout=60)
    annuels, historique = [], None
    for res in fiche.get("resources", []):
        titre = (res.get("title") or "").lower()
        if (res.get("format") or "").lower() != "zip":
            continue
        if titre.startswith("communes en restrictions"):
            m = _ANNEE.search(titre)
            if m:
                annuels.append((res["url"], int(m[1])))
        elif titre.startswith("historique communes"):
            historique = res["url"]
    return (max(annuels, key=lambda a: a[1]) if annuels else None), historique


def objets(flux, codes: set[str], taille_bloc: int = 1 << 22, vus: list | None = None):
    """Les objets commune du tableau JSON dont le code est demandé, décodés.

    Le flux est découpé sur `{"commune":` : cette suite d'octets n'apparaît
    qu'en tête d'objet (un nom de commune ne contient pas d'accolade). `vus`,
    s'il est donné, reçoit le nombre d'objets commune rencontrés, demandés ou non.
    """
    tampon = b""
    while True:
        bloc = flux.read(taille_bloc)
        tampon += bloc
        if len(tampon) > TAMPON_MAX:
            raise RuntimeError("VigiEau : objet commune de plus de 64 Mo, format changé ?")
        debut = tampon.find(_MARQUE)
        if debut < 0:
            if not bloc:
                return
            continue
        while (suivant := tampon.find(_MARQUE, debut + 1)) >= 0:
            if vus is not None:
                vus[0] += 1
            yield from _objet(tampon[debut:suivant], codes)
            debut = suivant
        tampon = tampon[debut:]
        if not bloc:
            if vus is not None:
                vus[0] += 1
            yield from _objet(tampon, codes)
            return


def _objet(brut: bytes, codes: set[str]):
    m = _CODE.match(brut)
    if m and m[1].decode() in codes:
        yield json.loads(brut.rstrip().rstrip(b",]"))


def compter(objet: dict) -> dict[int, tuple[Counter, str]]:
    """Par année : jours par (zone, niveau), et dernier jour relevé."""
    par_annee: dict[int, tuple[Counter, str]] = {}
    for jour in objet.get("restrictions") or []:
        date = jour.get("date") or ""
        if len(date) < 10:
            continue
        compte, dernier = par_annee.get(int(date[:4]), (Counter(), ""))
        for zone in ZONES:
            niveau = jour.get(zone) or "aucune"
            compte[(zone, niveau)] += 1
        par_annee[int(date[:4])] = (compte, max(dernier, date))
    return par_annee


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vigieau_jours (
            insee         TEXT NOT NULL,
            annee         INTEGER NOT NULL,
            zone          TEXT NOT NULL,   -- AEP | SOU | SUP
            niveau        TEXT NOT NULL,   -- aucune | vigilance | alerte | alerte_renforcee | crise
            jours         INTEGER NOT NULL,
            dernier_jour  TEXT NOT NULL,   -- dernier jour que le fichier couvre cette année-là
            source_url    TEXT,
            releve_le     TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, annee, zone, niveau)
        )
    """)
    conn.commit()


def _ecrire(conn, objet: dict, url: str, annees: set[int] | None) -> int:
    """Remplace les années relues d'une commune ; rend le nombre de lignes."""
    insee = objet["commune"]["code"]
    n = 0
    for annee, (compte, dernier) in compter(objet).items():
        if annees is not None and annee not in annees:
            continue
        conn.execute("DELETE FROM vigieau_jours WHERE insee=? AND annee=?", (insee, annee))
        conn.executemany(
            "INSERT INTO vigieau_jours"
            " (insee, annee, zone, niveau, jours, dernier_jour, source_url)"
            " VALUES (?,?,?,?,?,?,?)",
            [(insee, annee, z, niv, j, dernier, url) for (z, niv), j in compte.items()])
        n += len(compte)
    return n


def _lire_zip(chemin, codes: set[str], vus: list):
    with zipfile.ZipFile(chemin) as z:
        membre = next(n for n in z.namelist() if n.endswith(".json"))
        with z.open(membre) as flux:
            yield from objets(flux, codes, vus=vus)


def run(historique: bool = False) -> int:
    annuel, url_historique = _ressources()
    if historique:
        if not url_historique:
            raise RuntimeError("VigiEau : pas d'historique des communes au catalogue")
        url, fichier, annees = url_historique, CACHE / "historique-communes.zip", None
        jours = HISTORIQUE_JOURS
    else:
        if not annuel:
            raise RuntimeError("VigiEau : pas de fichier annuel des communes au catalogue")
        url, annee = annuel
        fichier, annees, jours = CACHE / f"restrictions-communes-{annee}.zip", {annee}, CACHE_JOURS
    codes = set(communes_du_step(SOURCE))
    # Des restrictions EN VIGUEUR : une veille plus vieille montrerait une
    # restriction levée. Aucun repli sur un fichier périmé.
    chemin = deposer(url, fichier, SOURCE, jours, facteur_perime=1)
    lignes, trouvees = 0, set()
    try:
        with transaction() as conn:
            ensure_table(conn)
            vus = [0]
            for objet in _lire_zip(chemin, codes, vus):
                trouvees.add(objet["commune"]["code"])
                lignes += _ecrire(conn, objet, url, annees)
            # Les communes du fichier national, avant le filtre : notées avant le
            # plancher, pour qu'une passe refusée laisse sa mesure.
            mesure.lu(SOURCE, vus[0], None, complete=not historique)
            # Lever ici annule la transaction : ni ligne ni absence n'est écrite.
            if vus[0] < MIN_COMMUNES:
                raise RuntimeError(f"VigiEau : {vus[0]} commune(s) lue(s) dans {fichier.name},"
                                   f" moins que le plancher de {MIN_COMMUNES}")
            if not historique:
                absences.noter(conn, SOURCE, codes, trouvees, jeu=JEU, page_jeu=PAGE_JEU,
                               source_url=url, millesime=annee)
    finally:
        retirer_hors_magasin(chemin, fichier)
    print(f"  [vigieau] {lignes} ligne(s) sur {len(trouvees)}/{len(codes)} commune(s)"
          f" ({'historique' if historique else f'année {annee}'})")
    return lignes


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, annee, MAX(dernier_jour) au,
               SUM(CASE WHEN niveau='crise' THEN jours END) crise,
               SUM(CASE WHEN niveau!='aucune' THEN jours END) restreints,
               SUM(jours) releves
          FROM vigieau_jours WHERE zone='AEP' GROUP BY insee, annee ORDER BY insee, annee
    """):
        print(f"  {r['insee']} {r['annee']}  eau potable : {r['restreints'] or 0} j restreints"
              f" dont {r['crise'] or 0} en crise, sur {r['releves']} relevés (au {r['au']})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--historique", action="store_true",
                   help="relire l'historique depuis 2010 (590 Mo, une fois)")
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run(a.historique)
