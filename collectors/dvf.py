"""
Collecteur DVF — Demandes de Valeurs Foncières
Source : API DVF+ Cerema (api-dvf.cerema.fr) ou fichier bulk data.gouv.fr
Périmètre : les communes de config/instance.json, via COMMUNE_INSEE.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from . import mesure
from .archive import archive_fetch, fetch_json
from .config import (DVF_API, COMMUNE_INSEE, DEPARTEMENT, HEADERS,
                     NATIONAL_STORE, REQUEST_DELAY)
from .db import transaction
from .national_store import ecrire_atomiquement, est_frais, magasin_ecrivable, perime_lisible


DVF_CACHE = NATIONAL_STORE / "dvf"
DVF_CEREMA_CACHE_JOURS = 90
DVF_BULK_COURANT_CACHE_JOURS = 30


def _cache_cerema(insee: str):
    return DVF_CACHE / "cerema" / f"{insee}.json"


def _cache_departement(year: str, departement: str = DEPARTEMENT):
    return DVF_CACHE / "departements" / year / f"{departement}.csv.gz"


def _resultats_cerema(data) -> list[dict]:
    if isinstance(data, dict):
        return data.get("results", [])
    return data if isinstance(data, list) else []


def fetch_dvf_cerema(insee: str = COMMUNE_INSEE, limit: int = 1000) -> list[dict]:
    """
    Fetch via API DVF+ Cerema.
    Endpoint : /api/v1/mutations/?code_departement=30&code_commune_bien=<insee>
    """
    results = []
    params = urllib.parse.urlencode({
        "code_departement": DEPARTEMENT,
        "code_commune_bien": insee,
        "limit": limit,
        "offset": 0
    })
    url = f"{DVF_API}?{params}"
    cache = _cache_cerema(insee)
    try:
        if est_frais(cache, DVF_CEREMA_CACHE_JOURS):
            data = json.loads(cache.read_bytes())
            origine = "magasin partagé"
        else:
            # Compté plus bas, cache compris, sous un nom à lui : ici, une liste
            # au sommet l'aurait été une seconde fois.
            data = fetch_json(url, source="dvf-cerema", timeout=20, mesurer=False)
            ecrire_atomiquement(
                cache, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
            )
            origine = "API Cerema"
        results = _resultats_cerema(data)
        # Plafonné à `limit` sans pagination : un plafond atteint reste stable.
        mesure.lu("cerema-mutations", len(results),
                  sorted(results[0]) if results and isinstance(results[0], dict) else None,
                  complete=True)
        print(f"  [dvf] {insee} {origine} → {len(results)} mutations")
    except Exception as e:
        if cache.is_file() and cache.stat().st_size > 0:
            try:
                data = json.loads(cache.read_bytes())
                results = _resultats_cerema(data)
                print(f"  [dvf] {insee} API indisponible, cache périmé repris "
                      f"→ {len(results)} mutations")
                return results
            except Exception:
                pass
        print(f"  [dvf] {insee} API Cerema indisponible: {e}")

    return results


def _annees() -> list[str]:
    # Millésimes déduits de l'année courante plutôt que codés en dur : la liste
    # figée à 2024 a laissé passer le millésime 2025 (données arrêtées au
    # 20/12/2024 pendant 577 jours). Le millésime de l'année en cours n'est
    # publié qu'en cours d'année — un 404 est normal et simplement ignoré.
    from datetime import date
    return [str(a) for a in range(2020, date.today().year + 1)]


def csv_departement(year: str, departement: str = DEPARTEMENT, insee: str = "") -> bytes:
    """Le CSV DVF géolocalisé d'un département pour une année, compressé :
    celui du magasin s'il est frais, sinon téléchargé (et déposé si le magasin
    l'accepte). Un 404 (millésime pas encore publié) lève `HTTPError`."""
    from datetime import date

    url = (f"https://files.data.gouv.fr/geo-dvf/latest/csv"
           f"/{year}/departements/{departement}.csv.gz")
    cache = _cache_departement(year, departement)
    # Les millésimes clos sont immuables. Seul celui de l'année en
    # cours expire, car data.gouv le complète au fil des publications.
    frais = None if int(year) < date.today().year else DVF_BULK_COURANT_CACHE_JOURS
    if est_frais(cache, frais):
        print(f"  [dvf] {insee} magasin partagé {year}/{departement}.csv.gz")
        return cache.read_bytes()
    # Comme `deposer` : un lecteur qui ne peut pas écrire lit la copie périmée
    # bornée avant d'appeler la source ; l'écrivain, jamais.
    if not magasin_ecrivable(cache.parent, cache.name) and perime_lisible(cache, frais or 1):
        return cache.read_bytes()
    print(f"  [dvf] {insee} téléchargement {year}/{departement}.csv.gz...")
    req = urllib.request.Request(url, headers=HEADERS)

    # Sur une connexion instable, les téléchargements peuvent être
    # coupés en cours de route. Le fichier partagé n'est publié
    # qu'après réception complète.
    derniere_erreur = None
    for tentative in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                compressed = resp.read()
            ecrire_atomiquement(cache, compressed)
            return compressed
        except urllib.error.HTTPError:
            raise       # 404 : millésime non publié, inutile d'insister
        except Exception as e:
            derniere_erreur = e
            if tentative < 2:
                attente = 3 * (tentative + 1)
                print(f"  [dvf] {insee} {year} coupé ({type(e).__name__}), "
                      f"nouvelle tentative dans {attente}s...")
                time.sleep(attente)
    if cache.is_file() and cache.stat().st_size > 0 and perime_lisible(cache, frais or 1):
        print(f"  [dvf] {insee} cache périmé repris pour {year}")
        return cache.read_bytes()
    raise derniere_erreur


def remplir_magasin(departements: list[str]) -> list[str]:
    """Pour l'écrivain du magasin : les CSV départementaux de chaque année,
    sans commune ni base. Rend les « département/année » en échec ; un
    millésime pas encore publié (404) n'en est pas un."""
    echecs = []
    for departement in departements:
        for year in _annees():
            try:
                csv_departement(year, departement)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    echecs.append(f"{departement}/{year}")
                    print(f"  ✗ [dvf] {departement} {year} : HTTP {e.code}")
            except Exception as e:
                echecs.append(f"{departement}/{year}")
                print(f"  ✗ [dvf] {departement} {year} : {type(e).__name__}: {e}")
    return echecs


def fetch_dvf_csv_bulk(insee: str = COMMUNE_INSEE) -> list[dict]:
    """
    Télécharge le CSV DVF géolocalisé par département (data.gouv.fr),
    le décompresse et filtre sur le code commune. Cumule toutes les années.
    """
    import gzip
    import io
    import csv

    all_rows: list[dict] = []
    for year in _annees():
        url = (f"https://files.data.gouv.fr/geo-dvf/latest/csv"
               f"/{year}/departements/{DEPARTEMENT}.csv.gz")
        try:
            compressed = csv_departement(year, DEPARTEMENT, insee)
            raw = gzip.decompress(compressed).decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(raw))
            rows = [
                row for row in reader
                if row.get("code_commune") == insee
                or row.get("commune_code") == insee
            ]
            print(f"  [dvf] {insee} {year}: {len(rows)} mutations")
            # Archive le sous-ensemble commune (le CSV départemental complet est
            # re-téléchargeable sur data.gouv ; seules ces lignes sont exploitées)
            if rows:
                archive_fetch("dvf-geodvf", url,
                              json.dumps(rows, ensure_ascii=False).encode("utf-8"),
                              doc_type="json",
                              metadata={"insee": insee, "year": year})
            all_rows.extend(rows)
        except Exception as e:
            print(f"  [dvf] {insee} CSV {year} indisponible: {e}")

    return all_rows


def _first(item: dict, *keys):
    """Première valeur non vide parmi plusieurs clés possibles (multi-schéma)."""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", "null", []):
            return v
    return None


def _cadastre(item: dict) -> tuple[str, str, str]:
    """
    Retourne (cadastre_ref, section, numero) depuis id_parcelle / l_idpar.
    id_parcelle = INSEE(5) + préfixe(3) + section(2) + numéro(4) ; on n'en
    garde que section + numéro, soit la forme 'AB0001'.
    Fallback sur les champs explicites section/numero_plan si présents.
    """
    idpar = _first(item, "id_parcelle", "idpar", "l_idpar", "id_parcelle_1")
    if isinstance(idpar, list):
        idpar = idpar[0] if idpar else None
    if isinstance(idpar, str):
        idpar = idpar.strip("{}[]'\" ").split(",")[0].strip()
        if len(idpar) >= 14:
            section, numero = idpar[8:10], idpar[10:14]
            return f"{section}{numero}", section, numero
    section = _first(item, "section", "Section", "l_section") or ""
    if isinstance(section, list):
        section = section[0] if section else ""
    numero = _first(item, "numero_plan", "No plan", "numero") or ""
    return f"{section}{numero}", str(section), str(numero)


def _lieu_dit(item: dict) -> str:
    """Localisation : n° + voie (geo-dvf), sinon lieu-dit normalisé Cerema."""
    voie = _first(item, "adresse_nom_voie", "lieu_dit_normalise", "lieu_dit",
                  "Voie", "voie", "l_libvoie")
    if isinstance(voie, list):
        voie = voie[0] if voie else ""
    num = _first(item, "adresse_numero", "No voie", "novoie", "l_dnvoiri")
    if isinstance(num, list):
        num = num[0] if num else ""
    parts = [str(num).strip() for num in ([num] if num else [])] + ([str(voie).strip()] if voie else [])
    return " ".join(p for p in parts if p)


def _parse_record(item: dict) -> dict | None:
    """Parseur universel : API Cerema, JSON/CSV geo-dvf, CSV DGFiP brut."""
    try:
        date = (str(_first(item, "date_mutation", "datemut", "Date mutation") or ""))[:10]
        surface_t = _f(_first(item, "surface_terrain", "sterr", "Surface terrain"))
        surface_b = _f(_first(item, "surface_reelle_bati", "sbati", "Surface reelle bati"))
        price     = _i(_first(item, "valeur_fonciere", "valeurfonc", "Valeur fonciere"))
        cad, section, numero = _cadastre(item)
        base = surface_t or surface_b
        return {
            "id_mutation":     str(_first(item, "id_mutation", "idmutinvar", "idmutation") or "") or None,
            "date":            date,
            "cadastre_ref":    cad,
            "section":         section,
            "numero":          numero,
            "lieu_dit":        _lieu_dit(item),
            "nature_mutation": str(_first(item, "nature_mutation", "libnatmut", "Nature mutation") or ""),
            "nature_bien":     str(_first(item, "type_local", "nature_culture",
                                           "libtypbien", "Type local") or ""),
            "surface_terrain": surface_t,
            "surface_bati":    surface_b,
            "price":           price,
            "price_per_m2":    round(price / base, 1) if price and base and base > 0 else None,
            "lat":             _f(_first(item, "latitude", "lat")),
            "lng":             _f(_first(item, "longitude", "lng")),
        }
    except Exception:
        return None


# Compat : anciens noms importés ailleurs (run_all, dvf_soudorgues)
_parse_cerema = _parse_record
_parse_csv_row = _parse_record

def _f(v) -> float | None:
    try: return float(v) if v not in (None, "", "null") else None
    except: return None

def _i(v) -> int | None:
    try:
        s = str(v).replace(",", ".").strip()
        return int(float(s)) if s not in ("", "null") else None
    except: return None


def insert_transaction(conn, parsed: dict, insee: str | None = None) -> None:
    """Insert idempotent d'une mutation parsée (index unique ux_dvf_dedup).

    `insee` est OBLIGATOIRE en pratique : les références cadastrales (0A0002,
    AC0394…) se répètent d'une commune à l'autre. Sans le code commune dans la
    clé de dédoublonnage, deux mutations de communes différentes de même date,
    section et prix s'écrasent silencieusement — bug constaté le 11/08/2026 sur
    les 7 communes collectées jusque-là.
    """
    commune = insee or parsed.get("insee")
    conn.execute(
        "INSERT OR IGNORE INTO dvf_transactions"
        " (insee,id_mutation,date,cadastre_ref,section,numero,lieu_dit,"
        "  nature_mutation,nature_bien,surface_terrain,"
        "  surface_bati,price,price_per_m2,lat,lng)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (commune, parsed.get("id_mutation"),
         parsed["date"], parsed["cadastre_ref"],
         parsed["section"], parsed["numero"],
         parsed["lieu_dit"], parsed["nature_mutation"],
         parsed["nature_bien"], parsed["surface_terrain"],
         parsed["surface_bati"], parsed["price"],
         parsed["price_per_m2"], parsed["lat"], parsed["lng"])
    )
    # Une ligne déjà en base d'avant la colonne est IGNORÉE par l'insertion :
    # elle garderait son identifiant vide pour toujours. On le lui reporte, par
    # la clé même de `ux_dvf_dedup`.
    if parsed.get("id_mutation"):
        conn.execute(
            "UPDATE dvf_transactions SET id_mutation=?"
            " WHERE id_mutation IS NULL AND insee IS ? AND date IS ?"
            "   AND cadastre_ref IS ? AND nature_bien IS ?"
            "   AND COALESCE(surface_terrain,-1)=COALESCE(?,-1)"
            "   AND COALESCE(surface_bati,-1)=COALESCE(?,-1)"
            "   AND COALESCE(price,-1)=COALESCE(?,-1)",
            (parsed["id_mutation"], commune, parsed["date"],
             parsed["cadastre_ref"], parsed["nature_bien"],
             parsed["surface_terrain"], parsed["surface_bati"], parsed["price"])
        )


def import_dvf(insee: str = COMMUNE_INSEE):
    print(f"[dvf] Démarrage collecte (INSEE {insee})...")

    items = fetch_dvf_cerema(insee)
    if not items:
        items = fetch_dvf_csv_bulk(insee)

    if not items:
        print(f"[dvf] {insee} — aucune donnée disponible")
        return

    inserted = skipped = 0

    with transaction() as conn:
        for raw in items:
            parsed = _parse_record(raw)

            if not parsed or not parsed.get("date"):
                skipped += 1
                continue

            insert_transaction(conn, parsed, insee)
            inserted += 1

    print(f"[dvf] {insee} OK — {inserted} transactions, {skipped} ignorées")
