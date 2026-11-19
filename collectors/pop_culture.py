"""
pop_culture.py — Patrimoine protégé (base Mérimée) pour le périmètre.

  - Immeubles protégés MH (base Mérimée) → entités 'place' + note (historique,
    datation, protection, lien notice POP) + couche places (patrimoine)

Source : le jeu « Immeubles protégés au titre des Monuments Historiques » du
ministère de la Culture sur data.gouv.fr, interrogé commune par commune par
l'API tabulaire de data.gouv (le CSV entier pèse 100 Mo).

L'ancienne API (data.culture.gouv.fr, Opendatasoft) redirige depuis octobre
2026 vers la page d'accueil HTML de culture.data.gouv.fr : le collecteur
annonçait « OK — 0 éléments » sur une page qui n'était pas une réponse.

Objets mobiliers (base Palissy) : NON collectés. Le jeu n'existe plus que sous
la forme d'un CSV de 365 Mo (ministere-culture.s3…/POP/palissy.csv), sans API,
et la fiche data.gouv pointe encore vers l'ancien portail. Le télécharger à
chaque passe pour un périmètre où il ne rendait rien ne se justifie pas.

Le patrimoine non protégé (inventaire général, filatures, temples…) passe par
le canal documentaire de l'atelier. Réponses archivées (source
culture-mh) et rejouables depuis raw_documents.

Usage :
  python3 -m collectors.pop_culture
  python3 -m collectors.pop_culture --insee 30236
  python3 -m collectors.pop_culture --stats
"""
import argparse
import json
import time
import urllib.parse

from . import mesure
from .archive import fetch_json
from .config import COMMUNES, COMMUNES_ADRESSE, REQUEST_DELAY, communes_du_step
from .db import get_conn, upsert_entity

# Ressource CSV du jeu data.gouv 5af120e5b595087cfabcde81 (mise à jour par le
# ministère, même identifiant d'une version à l'autre).
API = "https://tabular-api.data.gouv.fr/api/resources/3a52af4a-f9da-4dcc-8110-b07774dfb3bc/data/"
POP_NOTICE = "https://www.pop.culture.gouv.fr/notice/merimee/{ref}"


def _fetch(insee: str) -> list[dict]:
    """Les notices Mérimée d'une commune, toutes pages suivies."""
    url = f"{API}?COG_Insee_lors_de_la_protection__exact={urllib.parse.quote(insee)}&page_size=50"
    lignes = []
    while url:
        data = fetch_json(url, source="culture-mh", timeout=30)
        if not isinstance(data.get("data"), list):
            raise ValueError(f"réponse sans `data` : {str(data)[:200]}")
        lignes += data["data"]
        url = (data.get("links") or {}).get("next")
        if url:
            time.sleep(REQUEST_DELAY)
    # Les communes du périmètre s'additionnent : un zéro communal est ordinaire,
    # un zéro du périmètre entier ne l'est pas.
    mesure.lu("culture-mh", len(lignes), mesure.forme_de(lignes),
              complete=True)
    return lignes


def _coords(v) -> tuple[float | None, float | None]:
    """« 43.8375,4.3623 » → (lat, lon) ; tout autre format → (None, None)."""
    try:
        lat, lon = (float(x) for x in str(v).split(","))
        return lat, lon
    except (TypeError, ValueError):
        return None, None


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _commune_de(insee: str) -> str | None:
    """Nom de la commune ACTUELLE, y compris pour le code d'une ancienne commune."""
    fiche = COMMUNES_ADRESSE.get(insee, {})
    return COMMUNES.get(fiche.get("commune") or insee, {}).get("nom")


def _poser_protection(conn, eid: int, ref: str, protection: str | None) -> None:
    """La protection MH vit dans les tags de `places`, sous `monuments_historiques`.

    `upsert_entity` fusionne un monument avec une fiche OSM du même nom, et
    l'`INSERT OR IGNORE` garde alors la catégorie OSM : la Chapelle
    Saint-Sébastien de Vallouise (PA00080648) sortait « lieu de culte », sans
    protection, et la notice ne vivait que dans `entity_notes`, que rien ne
    publie (02/11/2026). La protection s'ajoute donc aux tags existants, quels
    qu'ils soient, une entrée par référence Mérimée.
    """
    ligne = conn.execute("SELECT tags, osm_value FROM places WHERE entity_id=?",
                         (eid,)).fetchone()
    try:
        tags = json.loads((ligne[0] if ligne else None) or "{}")
    except ValueError:
        tags = {}
    if not isinstance(tags, dict):
        tags = {}
    if ligne and ligne[1] == "monument_historique":
        # Les lignes MH d'avant portaient `reference` et `protection` à la racine.
        tags.pop("reference", None)
        tags.pop("protection", None)
    entrees = [m for m in tags.get("monuments_historiques") or []
               if isinstance(m, dict) and m.get("reference") != ref]
    entrees.append({"reference": ref, "protection": protection})
    tags["monuments_historiques"] = sorted(entrees, key=lambda m: m.get("reference") or "")
    conn.execute("UPDATE places SET tags=? WHERE entity_id=?",
                 (json.dumps(tags, ensure_ascii=False), eid))


def import_mh(conn, insee: str, results: list[dict]) -> int:
    commune = _commune_de(insee)
    n = 0
    for r in results:
        name = (_first(r.get("Titre_editorial_de_la_notice"))
                or _first(r.get("Denomination_de_l_edifice")))
        if not name:
            continue
        ref = _first(r.get("Reference")) or ""
        lat, lng = _coords(r.get("coordonnees_au_format_WGS84"))
        eid = upsert_entity(conn,
            type="place", name=str(name).strip(),
            lat=lat, lng=lng,
            address=_first(r.get("Lieudit")),
            confidence="verified", commune=commune)
        conn.execute(
            "INSERT OR IGNORE INTO places (entity_id, osm_category, osm_value, tags)"
            " VALUES (?, 'patrimoine', 'monument_historique', '{}')", (eid,))
        _poser_protection(conn, eid, ref, _first(r.get("Typologie_de_la_protection")))
        exists = conn.execute(
            "SELECT id FROM entity_notes WHERE entity_id=? AND note LIKE ?",
            (eid, f"%{ref}%")).fetchone() if ref else None
        if not exists:
            parts = [f"Monument historique — {_first(r.get('Typologie_de_la_protection')) or 'protection MH'}"]
            for label, key in (("Datation", "Datation_de_l_edifice"),
                               ("Protection", "Date_et_typologie_de_la_protection"),
                               ("Historique", "Historique")):
                v = _first(r.get(key))
                if v:
                    parts.append(f"{label} : {str(v)[:600]}")
            if ref:
                parts.append(f"Notice POP : {POP_NOTICE.format(ref=ref)} ({ref})")
            conn.execute(
                "INSERT INTO entity_notes (entity_id, date, note, source, confidence)"
                " VALUES (?, date('now'), ?, 'pop-merimee', 'verified')",
                (eid, "\n".join(parts)))
        n += 1
    return n


def run(insee_list: list[str] | None = None):
    conn = get_conn()
    total, echecs = 0, []
    # Le champ interrogé est le code INSEE « lors de la protection » : les
    # monuments d'une commune fusionnée restent sous l'ancien code. Les trois
    # de Vallouise (05175) manquaient au relevé de Vallouise-Pelvoux (05101).
    for insee in (insee_list or communes_du_step("patrimoine", adresse=True)):
        commune = _commune_de(insee) or insee
        try:
            results = _fetch(insee)
        except Exception as e:
            # La ligne `[step] INSEE nom : échec → Type: motif` est celle que le
            # service local du portail classe : `: erreur —` n'y était pas lu,
            # le résumé non plus, et un 503 ou un délai dépassé sur UNE commune
            # devenait un défaut jamais rejoué (Lus-la-Croix-Haute le 26/10,
            # Montselgues le 27/10 ; la même requête répondait le 01/11).
            print(f"  [patrimoine] {insee} {commune} : échec → {type(e).__name__}: {e}")
            echecs.append(f"{insee} {commune}")
            continue
        n = import_mh(conn, insee, results)
        conn.commit()
        if n:
            print(f"[patrimoine] {commune} : {n} monument(s)")
        total += n
        time.sleep(REQUEST_DELAY)
    conn.close()
    # Une commune non relevée n'est pas une commune sans monument : rendre
    # « OK » ferait passer le step pour abouti, et la source pour fraîche,
    # jusqu'au bout de son TTL (même piège que `guichets`, 20/10/2026).
    if echecs:
        # Le résumé au format que le portail relit (`relevés en échec : …`) ;
        # le total des autres communes part sur sa propre ligne.
        print(f"[patrimoine] Mérimée non relevée pour {len(echecs)} commune(s) "
              f"({total} élément(s) pour les autres)")
        raise RuntimeError(f"relevés en échec : {', '.join(echecs)}")
    print(f"[patrimoine] OK — {total} éléments")


def show_stats():
    conn = get_conn(read_only=True)
    for r in conn.execute("""
        SELECT e.commune, e.name, json_extract(p.tags,'$.protection')
        FROM places p JOIN entities e ON e.id = p.entity_id
        WHERE p.osm_category='patrimoine'"""):
        print(f"  {r[0] or '?':30} {r[1]:40} {r[2] or ''}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Patrimoine MH — 15 communes de l'EPCI")
    ap.add_argument("--insee", help="Codes INSEE (virgule)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        show_stats()
    else:
        run(insee_list=args.insee.split(",") if args.insee else None)
