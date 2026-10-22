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

from .archive import fetch_json
from .config import COMMUNES, REQUEST_DELAY, communes_du_step
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


def import_mh(conn, insee: str, results: list[dict]) -> int:
    commune = COMMUNES.get(insee, {}).get("nom")
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
            " VALUES (?, 'patrimoine', 'monument_historique', ?)",
            (eid, json.dumps({"reference": ref,
                              "protection": _first(r.get("Typologie_de_la_protection"))},
                             ensure_ascii=False)))
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
    for insee in (insee_list or communes_du_step("patrimoine")):
        commune = COMMUNES.get(insee, {}).get("nom", insee)
        try:
            results = _fetch(insee)
        except Exception as e:
            print(f"[patrimoine] {commune} : erreur — {e}")
            echecs.append(commune)
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
        raise RuntimeError(f"Mérimée non relevée pour {len(echecs)} commune(s) : "
                           f"{', '.join(echecs)} ({total} élément(s) pour les autres)")
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
