"""
Vigie Civique — API de l'atelier (FastAPI)
Port 8765, proxié par Vite (:5173) en dev.
"""
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

# Charge .env si présent (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from fastapi import (Body, Depends, FastAPI, File, Form, Header, HTTPException,
                     Path as FPath, Query, Request, UploadFile)
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─── Config ────────────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
SYNTHESES_DIR = BASE_DIR / "dashboard" / "static" / "api" / "syntheses"

# L'atelier et la publication lisent la MÊME base, décrite par la même
# configuration : un atelier qui pointerait ailleurs travaillerait sur des
# données que le site ne publie pas, et l'écart ne se verrait jamais.
from collectors.config import (BBOX, COMMUNE_NAME, COMMUNE_INSEE, DB_PATH,
                               DEPARTEMENT, EPCI_NOM)
from collectors import dossiers as _dossiers
from collectors import propositions as _propositions
from collectors import marches_extraits as _marches_extraits
from collectors import flux_extraits as _flux_extraits

#: Les lignes LUES dans les actes par un outil d'extraction, par nature de
#: proposition : un marché, une subvention. Même chemin, mêmes gestes.
_EXTRAITS = {_marches_extraits.NATURE: _marches_extraits,
             _flux_extraits.NATURE: _flux_extraits}
from collectors import extraction as _extraction
from collectors.origine import (ATELIER, INSTITUTIONNEL, ORIGINES, VERBATIM,
                                modifiable, origine_de)
from collectors.verdict import (GESTES, JAMAIS_RELU, OBJETS, OBJETS_A_RETENIR, RETENU,
                                TYPES_REVUS, VERDICTS,
                                empreinte, geste_de, modifie_depuis_relecture, note_du_geste,
                                verdict_de)
from collectors.files import (GEO_A_FAIRE, GEO_DEPUIS, GEO_ETAT,
                              RESERVATION_MINUTES, a_trancher, geo_params,
                              premier_jour, relever)

# Boîte englobante de la commune, telle que déclarée par l'instance.
COMMUNE_BBOX = {"lat_min": BBOX[0], "lng_min": BBOX[1],
                "lat_max": BBOX[2], "lng_max": BBOX[3]} if len(BBOX) == 4 else {
    "lat_min": -90.0, "lat_max": 90.0, "lng_min": -180.0, "lng_max": 180.0}

# Assistance par modèle de langage : DÉSACTIVÉE par défaut.
#
# Elle suppose un Ollama joignable en local — vrai sur une machine de collecte
# équipée, faux partout ailleurs. Le défaut inverse faisait qu'une instance
# neuve annonçait la fonction dans son interface et échouait à la première
# question, sur une erreur de connexion que rien n'expliquait. Une fonction
# absente se comprend ; une fonction qui plante ressemble à une panne.
#
# RAG_ENABLED=1 dans .env sur la machine qui a Ollama.
RAG_ENABLED = os.environ.get("RAG_ENABLED", "0").strip().lower() not in ("0", "false", "no")
# La génération (`/api/rag/ask`) a son propre réglage, fermé par défaut, et ne
# s'ouvre qu'avec la recherche. Un serveur peut porter le modèle d'embeddings
# sans modèle de conversation (l'atelier de Lasalle, sur un VPS de 3,7 Go) :
# ouvrir la recherche ne doit pas y faire apparaître un bouton qui échoue.
RAG_ASK_ENABLED = os.environ.get("RAG_ASK_ENABLED", "0").strip().lower() \
    not in ("0", "false", "no", "")

# `/docs`, `/redoc` et `/openapi.json` ne commencent pas par `/api/` : le verrou
# global ne les voit pas, et FastAPI les sert à qui les demande. Éteints sauf
# demande explicite (`VIGIE_API_DOCS=1`, en développement).
_DOCS = os.environ.get("VIGIE_API_DOCS", "0").strip().lower() not in ("0", "false", "no", "")

# L'origine à part d'où s'ouvrent les documents archivés (api_auth.py, « Les
# documents »), sans « / » final : `https://docs.atelier.example.org`. Vide, les
# documents s'ouvrent comme avant, en `blob:` — un atelier local n'a pas de
# second hôte à donner.
DOCS_URL = os.environ.get("DOCS_URL", "").strip().rstrip("/")
from urllib.parse import urlencode, urlsplit  # noqa: E402
HOTE_DOCS = urlsplit(DOCS_URL).hostname if DOCS_URL else None

app = FastAPI(title=f"Vigie Civique — atelier {COMMUNE_NAME}",
              docs_url="/docs" if _DOCS else None,
              redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None)

# ─── Verrou global API (audit Phase A — session 22) ──────────────────────────
# Toute requête /api/* exige un JWT access valide OU l'en-tête X-Admin-Key.
# Seul /api/auth/* est exempté (login/refresh/logout/me gèrent leur propre auth).
# Enregistré AVANT CORS pour que CORSMiddleware reste le plus externe (préflights
# OPTIONS + en-têtes CORS sur les réponses 401). Défense en profondeur : les
# require_auth / _check_admin des routes restent en place — NE PAS retirer.
import secrets as _secrets
from starlette.responses import JSONResponse as _JSONResponse

def _boucle_locale(request) -> bool:
    """La requête vient-elle de la machine elle-même, sans proxy devant ?

    Deux conditions, parce que l'adresse seule ne suffit pas : derrière nginx et
    sans `--proxy-headers`, uvicorn voit 127.0.0.1 pour tout le monde. Un proxy
    signe son passage par un en-tête de transfert, qu'un script local n'envoie pas.
    """
    if any(request.headers.get(h) for h in ("x-forwarded-for", "x-real-ip", "forwarded")):
        return False
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1")


@app.middleware("http")
async def _api_auth_guard(request, call_next):
    path = request.url.path
    # L'hôte des documents ne sert QUE `/d/` : ni l'API, ni l'interface. nginx
    # le borne déjà ; l'application ne compte pas dessus.
    if HOTE_DOCS and request.url.hostname == HOTE_DOCS and not path.startswith("/d/"):
        return _JSONResponse({"detail": "introuvable"}, status_code=404)
    if (request.method == "OPTIONS"
            or not path.startswith("/api/")
            or path.startswith("/api/auth/")):
        return await call_next(request)

    # 1) Clé admin — fallback scripts CLI (sync_ia, generate_syntheses --push, deploy)
    # `ADMIN_KEY` du module, lue au démarrage : la même valeur que les gardes de
    # route. Le verrou relisait l'environnement, une seconde source.
    admin_key = ADMIN_KEY
    provided  = request.headers.get("x-admin-key")
    # La clé passe ce verrou ET vaut le rôle admin dans les routes de
    # publication, sans compte ni nom. Rien dans l'application ne l'empêchait
    # d'arriver d'internet : seule une ligne de nginx, recopiée à la main, la
    # retirait. Une requête qui la porte sans venir de la machine elle-même est
    # refusée ici, que la clé soit juste ou fausse — la réponse n'en dit rien.
    if provided and not _boucle_locale(request):
        return _JSONResponse(
            {"detail": "La clé d'administration ne sert que depuis la machine de l'atelier."},
            status_code=403)
    # En octets : sur des chaînes, `compare_digest` lève une TypeError au premier
    # caractère non ASCII, et un en-tête forgé rendait un 500 au lieu d'un 401.
    if admin_key and provided and _secrets.compare_digest(provided.encode(),
                                                          admin_key.encode()):
        return await call_next(request)

    # 2) JWT access valide — jugé par `api_auth.utilisateur_du_jeton`, le même
    #    contrôle que les routes : révocation, compte désactivé (effet à la
    #    requête suivante, pas à l'expiration du jeton), sessions closes par un
    #    changement de mot de passe.
    from api_auth import jeton_de, utilisateur_du_jeton
    jeton = jeton_de(request)
    if jeton:
        # Seul le JUGEMENT du jeton est dans le `try` : `call_next` en sort. Avec
        # la route dedans, toute exception qu'elle levait (un fichier absent, un
        # disque en lecture seule) était avalée et ressortait en 401
        # « Authentification requise », sans trace au journal — trois pannes
        # différentes, un seul message, le 04/10/2026.
        try:
            utilisateur_du_jeton(jeton, "access")
        except HTTPException as refus:
            # 401 : jeton refusé. Un autre statut (503 « JWT_SECRET manquant »)
            # est une panne de configuration, pas un refus : il garde son code.
            if refus.status_code != 401:
                return _JSONResponse({"detail": refus.detail},
                                     status_code=refus.status_code)
        else:
            return await call_next(request)
        # Toute autre exception (base illisible, disque plein) n'est PAS rattrapée :
        # 500, pile au journal du service — jamais un refus d'accès.

    return _JSONResponse({"detail": "Authentification requise"}, status_code=401)


@app.exception_handler(Exception)
async def _panne_non_prevue(request, exc):
    """500 en JSON, que l'atelier sait lire (`echec()` du client), au lieu du
    texte « Internal Server Error ». La pile part au journal du service : le
    gestionnaire d'`Exception` de Starlette relève l'exception après la réponse.
    Seul le NOM de l'erreur sort — son message peut citer un chemin du serveur."""
    return _JSONResponse(
        {"detail": f"Erreur interne du serveur ({type(exc).__name__}) : "
                   "la cause est au journal du service."},
        status_code=500)

# CORS — restreint aux origines autorisées (env ALLOWED_ORIGINS ou localhost en dev)
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:4173")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request as StarletteRequest

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Auth JWT — atelier
from api_auth import (router as auth_router, comptes as comptes_router, au_moins,  # noqa: E402
                      exiger, jeton_de, require_au_moins, require_auth, require_role,
                      utilisateur_du_jeton)
app.include_router(auth_router)
app.include_router(comptes_router)

# Clé admin des scripts (env ADMIN_KEY), admise depuis la machine seule ; absente, elle n'ouvre rien
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


def require_compte_ou_cle(role_min: str = "contributor", geste: str = "Cette action"):
    """Garde de route : un jeton d'au moins `role_min`, ou la clé admin locale.

    `require_auth` n'admet que le jeton. Cette garde ne sert plus qu'aux routes
    de publication et de décision, où la clé VAUT admin (`_role_effectif`) ;
    les lectures, ouvertes à la clé sans identité ni journal, lui sont fermées
    depuis le 14/11/2026. La boucle locale est revérifiée ici, pour ne pas
    tenir du seul verrou ce que la clé vaut."""
    def _dep(request: Request, x_admin_key: Optional[str] = Header(default=None),
             jeton: Optional[str] = Depends(jeton_de)):
        if _cle_admin_valide(x_admin_key) and _boucle_locale(request):
            return None
        if not jeton:
            raise HTTPException(401, "Token manquant")
        user = utilisateur_du_jeton(jeton, "access")
        exiger(user, role_min, geste)
        return user
    return _dep

# ─── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def get_db_rw():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")   # anti-SQLITE_BUSY multi-utilisateurs (WAL)
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]

def row(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None

def parse_json_field(value, default=None):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default

# Revue d'architecture du 07/11/2026, C12 (avis vigie-dpo et vigie-securite) :
# `web_scraper` range dans `events.metadata` les courriels et téléphones relevés
# sur une page, et `/api/events` les rendait à tout compte, contributeur compris.
# L'atelier n'en lit que le nombre ; les valeurs ne sortent plus de l'API.
CLES_DE_CONTACT = ("emails", "phones")


def metadata_sans_contacts(value) -> dict:
    meta = parse_json_field(value, {})
    if not isinstance(meta, dict):
        return {}
    for cle in CLES_DE_CONTACT:
        if cle in meta:
            valeurs = meta.pop(cle)
            meta[f"n_{cle}"] = len(valeurs) if isinstance(valeurs, list) else 1
    return meta


def fts_query(value: str) -> str:
    tokens = re.findall(r"[\wÀ-ÿ]{2,}", value or "", flags=re.UNICODE)
    if not tokens:
        return '""'
    return " AND ".join(f"{token}*" for token in tokens[:8])

# ─── /api/stats ────────────────────────────────────────────────────────────────

@app.get("/api/stats", dependencies=[Depends(require_auth)])
def stats():
    conn = get_db()
    try:
        s = row(conn, "SELECT * FROM v_stats") or {}
        s["entities"] = (s.get("persons", 0) + s.get("businesses", 0)
                         + s.get("associations", 0) + s.get("services", 0)
                         + s.get("places", 0))
        return s
    finally:
        conn.close()

@app.get("/api/pivots", dependencies=[Depends(require_auth)])
def pivots():
    """Identifiants des entités structurantes : commune, EPCI, État, préfecture.

    L'atelier les portait en dur — `const CC_CAC_ID = 1645` — c'est-à-dire un
    numéro de ligne de la base d'origine. Sur une autre base, la page de
    l'intercommunalité affichait une entité prise au hasard. `pivot_ids` les
    résout par le NOM, à l'exécution ; cette route ne fait que l'exposer.

    Lecture seule : la base est ouverte en `mode=ro` et un pivot absent rend
    None. La route ouvrait la base en écriture et CRÉAIT l'entité manquante,
    comme le font les collecteurs (14/11/2026).
    """
    from collectors.db import pivot_ids
    conn = get_db()
    try:
        return pivot_ids(conn, creer=False)
    finally:
        conn.close()


# ─── /api/entities ─────────────────────────────────────────────────────────────

# Périmètre — cf. collectors/config.py et memory-bank/decisions.md.
# C1 = la commune, C2 = l'intercommunalité et ses communes membres,
# C3 = autorité supra-communale, lien = rattaché à un acteur suivi.
PERIMETRES = ("C1", "C2", "C3", "lien")


@app.get("/api/entities", dependencies=[Depends(require_auth)])
def entities(
    type: Optional[str] = None,
    confidence: Optional[str] = None,
    perimetre: Optional[str] = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
):
    # Filtre publication : whitelist stricte (Phase 3 — dépend du backfill Phase 2)
    filters = ["e.confidence IN ('verified','confirmed')"]
    params: list = []
    if type:
        filters.append("e.type = ?")
        params.append(type)
    if confidence and confidence in ("verified", "confirmed"):
        filters.append("e.confidence = ?")
        params.append(confidence)
    if perimetre:
        # Plusieurs valeurs acceptées : `?perimetre=C1,lien`. Sans filtre, la
        # réponse mêle la commune et les autres communes de l'EPCI — l'appelant
        # doit pouvoir demander explicitement ce qu'il affiche.
        demandes = [p.strip() for p in perimetre.split(",") if p.strip() in PERIMETRES]
        if not demandes:
            raise HTTPException(400, f"perimetre invalide — valeurs: {', '.join(PERIMETRES)}")
        filters.append(f"e.perimetre IN ({','.join('?' for _ in demandes)})")
        params += demandes
    where = "WHERE " + " AND ".join(filters)
    params += [limit, offset]

    conn = get_db()
    try:
        sql = f"""
            SELECT
                e.id, e.type, e.name, e.short_name,
                e.lat, e.lng, e.address, e.confidence, e.created_at,
                e.commune, e.perimetre,
                p.firstname, p.lastname, p.birth_year,
                b.siren, b.naf_code, b.naf_label, b.status AS biz_status,
                b.creation_date, b.legal_form,
                a.rna_id, a.object AS asso_object,
                a.creation_date AS asso_creation_date,
                pl.osm_category, pl.osm_value, pl.tags,
                s.category AS service_category
            FROM entities e
            LEFT JOIN persons      p  ON p.entity_id  = e.id
            LEFT JOIN businesses   b  ON b.entity_id  = e.id
            LEFT JOIN associations a  ON a.entity_id  = e.id
            LEFT JOIN places       pl ON pl.entity_id = e.id
            LEFT JOIN services     s  ON s.entity_id  = e.id
            {where}
            ORDER BY e.name
            LIMIT ? OFFSET ?
        """
        return rows(conn, sql, params)
    finally:
        conn.close()

# ─── /api/entities/{id} ────────────────────────────────────────────────────────

@app.get("/api/entities/{entity_id}", dependencies=[Depends(require_auth)])
def entity_detail(entity_id: int = FPath(..., ge=1)):
    conn = get_db()
    try:
        e = row(conn, """
            SELECT
                e.id, e.type, e.name, e.short_name,
                e.lat, e.lng, e.address, e.confidence, e.created_at,
                p.firstname, p.lastname, p.birth_year, p.gender,
                b.siren, b.siret_siege, b.naf_code, b.naf_label,
                b.legal_form, b.status AS biz_status, b.capital,
                b.employees_range, b.creation_date, b.closing_date,
                a.rna_id, a.waldec_id, a.object AS asso_object,
                a.status AS asso_status, a.creation_date AS asso_creation_date,
                pl.osm_id, pl.osm_category, pl.osm_value, pl.tags,
                s.category AS service_category, s.operator, s.opening_hours
            FROM entities e
            LEFT JOIN persons      p  ON p.entity_id  = e.id
            LEFT JOIN businesses   b  ON b.entity_id  = e.id
            LEFT JOIN associations a  ON a.entity_id  = e.id
            LEFT JOIN places       pl ON pl.entity_id = e.id
            LEFT JOIN services     s  ON s.entity_id  = e.id
            WHERE e.id = ?
        """, (entity_id,))
        if not e:
            raise HTTPException(404, "Entité introuvable")

        # Filtre publication : relations verified/confirmed uniquement (Phase 3 — whitelist)
        e["relations"] = rows(conn, """
            SELECT r.id, r.relation_type, r.since, r.until, r.source, r.confidence, r.metadata,
                   f.id AS from_id, f.name AS from_name, f.type AS from_type,
                   t.id AS to_id,   t.name AS to_name,   t.type AS to_type
            FROM relations r
            JOIN entities f ON f.id = r.from_id
            JOIN entities t ON t.id = r.to_id
            WHERE (r.from_id = ? OR r.to_id = ?)
              AND r.confidence IN ('verified','confirmed')
            ORDER BY r.relation_type
        """, (entity_id, entity_id))

        e["flows"] = rows(conn, """
            SELECT ff.id, ff.type, ff.year, ff.amount, ff.description, ff.source,
                   ef.id AS from_id, ef.name AS from_name,
                   et.id AS to_id,   et.name AS to_name
            FROM financial_flows ff
            LEFT JOIN entities ef ON ef.id = ff.from_id
            LEFT JOIN entities et ON et.id = ff.to_id
            WHERE ff.from_id = ? OR ff.to_id = ?
            ORDER BY ff.year DESC
        """, (entity_id, entity_id))

        e["events"] = rows(conn, """
            SELECT ev.id, ev.type, ev.date, ev.title, ev.source, ev.source_url,
                   ee.role
            FROM events ev
            JOIN event_entities ee ON ee.event_id = ev.id
            WHERE ee.entity_id = ?
            ORDER BY ev.date DESC
            LIMIT 50
        """, (entity_id,))

        return e
    finally:
        conn.close()

# ─── /api/search ───────────────────────────────────────────────────────────────

@app.get("/api/search", dependencies=[Depends(require_auth)])
def search(q: str = Query(..., min_length=1), limit: int = Query(20, le=100)):
    conn = get_db()
    try:
        # Une fiche écartée reste en base, donc dans la recherche : elle sort
        # marquée, sans quoi on la choisit (doublon, lien) sans savoir qu'elle
        # a quitté le site.
        lignes = rows(conn, """
            SELECT e.id, e.type, e.name, e.short_name, e.address,
                   e.lat, e.lng, e.confidence, a.review_status
            FROM entities_fts
            JOIN entities e ON e.id = entities_fts.rowid
            LEFT JOIN annotations a ON a.object_type = 'entity' AND a.object_id = e.id
            WHERE entities_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query(q), limit))
        for ligne in lignes:
            ligne["verdict"] = verdict_de(ligne.pop("review_status")) or JAMAIS_RELU
            ligne["ecartee"] = ligne["verdict"] == "ecarte"
        return lignes
    finally:
        conn.close()

# ─── /api/layers/{type} ────────────────────────────────────────────────────────

LAYER_TYPES = {"businesses", "associations", "persons", "services", "places", "dvf", "flows"}

@app.get("/api/layers/{layer_type}", dependencies=[Depends(require_auth)])
def layer(
    layer_type: str,
    status: Optional[str] = None,
    in_commune: bool = True,
):
    if layer_type not in LAYER_TYPES:
        raise HTTPException(400, f"Type inconnu. Valeurs: {', '.join(LAYER_TYPES)}")

    type_map = {
        "businesses": "business", "associations": "association",
        "persons": "person", "services": "service", "places": "place",
    }

    if layer_type == "dvf":
        conn = get_db()
        try:
            filters = []
            params: list = []
            if in_commune:
                filters.append("lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?")
                params += [COMMUNE_BBOX["lat_min"], COMMUNE_BBOX["lat_max"],
                           COMMUNE_BBOX["lng_min"], COMMUNE_BBOX["lng_max"]]
            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            return rows(conn, f"SELECT * FROM dvf_transactions {where} ORDER BY date DESC LIMIT 1000", params)
        finally:
            conn.close()

    if layer_type == "flows":
        conn = get_db()
        try:
            return rows(conn, "SELECT * FROM v_flows_summary LIMIT 500")
        finally:
            conn.close()

    entity_type = type_map[layer_type]
    # Filtre publication : whitelist stricte confidence (Phase 3)
    filters = ["e.type = ?", "e.confidence IN ('verified','confirmed')"]
    params: list = [entity_type]

    if in_commune:
        filters.append("e.lat BETWEEN ? AND ? AND e.lng BETWEEN ? AND ?")
        params += [COMMUNE_BBOX["lat_min"], COMMUNE_BBOX["lat_max"],
                   COMMUNE_BBOX["lng_min"], COMMUNE_BBOX["lng_max"]]

    if status and layer_type == "businesses":
        filters.append("b.status = ?")
        params.append(status)

    where = "WHERE " + " AND ".join(filters)

    conn = get_db()
    try:
        sql = f"""
            SELECT
                e.id, e.type, e.name, e.short_name,
                e.lat, e.lng, e.address, e.confidence,
                p.firstname, p.lastname,
                b.siren, b.naf_code, b.naf_label, b.status AS biz_status,
                b.creation_date,
                a.rna_id, a.object AS asso_object,
                a.creation_date AS asso_creation_date,
                pl.osm_category, pl.osm_value, pl.tags,
                s.category AS service_category
            FROM entities e
            LEFT JOIN persons      p  ON p.entity_id  = e.id
            LEFT JOIN businesses   b  ON b.entity_id  = e.id
            LEFT JOIN associations a  ON a.entity_id  = e.id
            LEFT JOIN places       pl ON pl.entity_id = e.id
            LEFT JOIN services     s  ON s.entity_id  = e.id
            {where}
            ORDER BY e.name
        """
        return rows(conn, sql, params)
    finally:
        conn.close()

# ─── /api/graph ────────────────────────────────────────────────────────────────

@app.get("/api/graph", dependencies=[Depends(require_auth)])
@limiter.limit("30/minute;200/hour")
def graph(
    request: StarletteRequest,
    entity_id: Optional[int] = None,
    depth: int = Query(2, ge=1, le=4),
    min_relations: int = Query(1, ge=1),
    limit: int = Query(180, ge=20, le=500),
):
    # En vue focalisée, cap pour éviter la saturation
    MAX_NEIGHBORS = 50   # voisins directs max au 1er niveau
    MAX_NODES     = 120  # total nœuds max dans le sous-graphe

    conn = get_db()
    try:
        if entity_id:
            # Sous-graphe centré sur une entité
            all_entity_ids = {entity_id}
            frontier = {entity_id}
            for step in range(depth):
                if not frontier:
                    break
                placeholders = ",".join("?" * len(frontier))
                neighbors = rows(conn, f"""
                    SELECT DISTINCT
                        CASE WHEN from_id IN ({placeholders}) THEN to_id ELSE from_id END AS nid
                    FROM relations
                    WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
                """, list(frontier) * 3)
                new_ids = {r["nid"] for r in neighbors} - all_entity_ids

                # Au premier niveau, limiter aux MAX_NEIGHBORS les plus connectés
                if step == 0 and len(new_ids) > MAX_NEIGHBORS:
                    nid_list = list(new_ids)
                    ph2 = ",".join("?" * len(nid_list))
                    ranked = rows(conn, f"""
                        SELECT e.id, COUNT(*) AS deg
                        FROM entities e
                        JOIN relations r ON r.from_id = e.id OR r.to_id = e.id
                        WHERE e.id IN ({ph2})
                        GROUP BY e.id
                        ORDER BY deg DESC
                        LIMIT {MAX_NEIGHBORS}
                    """, nid_list)
                    new_ids = {r["id"] for r in ranked}

                # Cap total nœuds : garder les plus connectés si dépassement
                if len(all_entity_ids) + len(new_ids) > MAX_NODES:
                    remaining = MAX_NODES - len(all_entity_ids)
                    if remaining <= 0:
                        break
                    nid_list2 = list(new_ids)
                    ph3 = ",".join("?" * len(nid_list2))
                    ranked2 = rows(conn, f"""
                        SELECT e.id, COUNT(*) AS deg
                        FROM entities e
                        JOIN relations r ON r.from_id = e.id OR r.to_id = e.id
                        WHERE e.id IN ({ph3})
                        GROUP BY e.id
                        ORDER BY deg DESC
                        LIMIT {remaining}
                    """, nid_list2)
                    new_ids = {r["id"] for r in ranked2}

                frontier = new_ids
                all_entity_ids |= new_ids

            id_list = list(all_entity_ids)
            ph = ",".join("?" * len(id_list))
            # Filtre publication : nodes verified/confirmed uniquement (Phase 3)
            nodes = rows(conn, f"""
                SELECT e.id, e.type, e.name, e.short_name, e.lat, e.lng, e.confidence,
                       p.firstname, p.lastname,
                       b.siren, b.naf_code, b.naf_label, b.status AS biz_status,
                       a.rna_id
                FROM entities e
                LEFT JOIN persons p ON p.entity_id = e.id
                LEFT JOIN businesses b ON b.entity_id = e.id
                LEFT JOIN associations a ON a.entity_id = e.id
                WHERE e.id IN ({ph})
                  AND e.confidence IN ('verified','confirmed')
            """, id_list)
            # Links : whitelist stricte relations (Phase 3)
            links = rows(conn, f"""
                SELECT id, from_id, to_id, relation_type, confidence, source
                FROM relations
                WHERE from_id IN ({ph}) AND to_id IN ({ph})
                  AND confidence IN ('verified','confirmed')
            """, id_list + id_list)

        else:
            # Vue globale filtrée par min_relations — filtre publication Phase 3
            nodes = rows(conn, """
                SELECT e.id, e.type, e.name, e.short_name, e.lat, e.lng, e.confidence,
                       p.firstname, p.lastname,
                       b.siren, b.naf_code, b.naf_label, b.status AS biz_status,
                       a.rna_id,
                       COUNT(DISTINCT r.id) AS degree
                FROM entities e
                LEFT JOIN persons p ON p.entity_id = e.id
                LEFT JOIN businesses b ON b.entity_id = e.id
                LEFT JOIN associations a ON a.entity_id = e.id
                LEFT JOIN relations r ON (r.from_id = e.id OR r.to_id = e.id)
                    AND r.confidence IN ('verified','confirmed')
                WHERE e.confidence IN ('verified','confirmed')
                GROUP BY e.id
                HAVING degree >= ?
                ORDER BY degree DESC
                LIMIT ?
            """, (min_relations, limit))

            id_list = [n["id"] for n in nodes]
            if id_list:
                ph = ",".join("?" * len(id_list))
                links = rows(conn, f"""
                    SELECT id, from_id, to_id, relation_type, confidence, source
                    FROM relations
                    WHERE from_id IN ({ph}) AND to_id IN ({ph})
                      AND confidence IN ('verified','confirmed')
                """, id_list + id_list)
            else:
                links = []

        return {"nodes": nodes, "links": links}
    finally:
        conn.close()

# ─── /api/events ───────────────────────────────────────────────────────────────

@app.get("/api/events", dependencies=[Depends(require_auth)])
def events(
    type: Optional[str] = None,
    limit: int = Query(50, le=500),
):
    filters = []
    params: list = []
    if type:
        filters.append("e.type = ?")
        params.append(type)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(limit)

    conn = get_db()
    try:
        event_rows = rows(conn, f"""
            SELECT e.id, e.type, e.date, e.title,
                   substr(e.content, 1, 1200) AS content,
                   length(e.content) AS content_length,
                   e.source, e.source_url, e.metadata
            FROM events e
            {where}
            ORDER BY e.date DESC
            LIMIT ?
        """, params)
        for event in event_rows:
            event["metadata"] = metadata_sans_contacts(event.get("metadata"))
        return event_rows
    finally:
        conn.close()

@app.get("/api/events/search", dependencies=[Depends(require_auth)])
def event_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(50, ge=1, le=200),
    civic: bool = False,
):
    # La recherche ne porte pas sur `metadata` (C12) : sans quoi chercher une
    # adresse dirait si elle est en base, même filtrée de la réponse.
    filters = ["events_fts MATCH ?"]
    params: list = ["{title content source} : (" + fts_query(q) + ")"]
    if civic:
        civic_types = (
            "conseil_municipal",
            "deliberation",
            "délibérations_cc",
            "pv_cc",
            "marché_public",
            "election",
        )
        filters.append("e.type IN ({})".format(",".join("?" for _ in civic_types)))
        params.extend(civic_types)
    params.append(limit)

    conn = get_db()
    try:
        event_rows = rows(conn, f"""
            SELECT e.id, e.type, e.date, e.title,
                   substr(e.content, 1, 1200) AS content,
                   e.source, e.source_url, e.metadata,
                   snippet(events_fts, 1, char(2), char(3), '…', 16) AS snippet
            FROM events_fts
            JOIN events e ON e.id = events_fts.rowid
            WHERE {" AND ".join(filters)}
            ORDER BY rank
            LIMIT ?
        """, params)
        for event in event_rows:
            event["metadata"] = metadata_sans_contacts(event.get("metadata"))
            # L'extrait est affiché comme du HTML (le surlignage), et son texte
            # vient d'un document COLLECTÉ : une balise écrite dans un PV ou une
            # page de mairie s'exécutait dans l'atelier. Le surlignage voyage
            # donc en caractères de contrôle, et les balises ne sont posées
            # qu'APRÈS l'échappement du texte.
            event["snippet"] = (html.escape(event.get("snippet") or "", quote=False)
                                .replace("\x02", "<mark>").replace("\x03", "</mark>"))
        return event_rows
    finally:
        conn.close()

# ─── /api/dvf ──────────────────────────────────────────────────────────────────

@app.get("/api/dvf", dependencies=[Depends(require_auth)])
def dvf(
    year: Optional[int] = None,
    limit: int = Query(500, le=5000),
):
    filters = []
    params: list = []
    if year:
        filters.append("year = ?")
        params.append(year)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(limit)

    conn = get_db()
    try:
        return rows(conn, f"""
            SELECT * FROM dvf_transactions
            {where}
            ORDER BY date DESC
            LIMIT ?
        """, params)
    finally:
        conn.close()

# ─── /api/flows ────────────────────────────────────────────────────────────────

@app.get("/api/flows", dependencies=[Depends(require_auth)])
def flows(
    year: Optional[int] = None,
    type: Optional[str] = None,
):
    filters = []
    params: list = []
    if year:
        filters.append("ff.year = ?")
        params.append(year)
    if type:
        filters.append("ff.type = ?")
        params.append(type)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    conn = get_db()
    try:
        return rows(conn, f"""
            SELECT ff.id, ff.type, ff.year, ff.amount, ff.description, ff.source,
                   ef.id AS from_id, ef.name AS from_name,
                   et.id AS to_id,   et.name AS to_name
            FROM financial_flows ff
            LEFT JOIN entities ef ON ef.id = ff.from_id
            LEFT JOIN entities et ON et.id = ff.to_id
            {where}
            ORDER BY ff.year DESC, ff.amount DESC
        """, params)
    finally:
        conn.close()

# ─── /api/marches ─────────────────────────────────────────────────────────────

@app.get("/api/marches", dependencies=[Depends(require_auth)])
def marches(
    acheteur: Optional[str] = None,
    titulaire: Optional[str] = None,
    #: Un mot de l'objet du marché (« voirie », « cantine »), sans casse.
    objet: Optional[str] = Query(None, max_length=200),
    year: Optional[int] = None,
    # Les pages en demandent 200 (com-com, urbanisme) : plafond à 500.
    limit: int = Query(200, ge=1, le=500),
):
    filters = []
    params: list = []
    if acheteur:
        filters.append("(mp.acheteur_siren=? OR mp.acheteur_nom LIKE ?)")
        params += [acheteur, f"%{acheteur}%"]
    if titulaire:
        filters.append("(mp.titulaire_siren=? OR mp.titulaire_nom LIKE ?)")
        params += [titulaire, f"%{titulaire}%"]
    if objet and objet.strip():
        # `%` et `_` saisis sont des caractères, pas des jokers.
        mot = objet.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append("mp.objet LIKE ? ESCAPE '\\'")
        params.append(f"%{mot}%")
    if year:
        filters.append("substr(mp.date_notif,1,4)=?")
        params.append(str(year))
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(limit)
    conn = get_db()
    try:
        return rows(conn, f"""
            SELECT mp.id, mp.acheteur_siren, mp.acheteur_nom,
                   mp.titulaire_siren, mp.titulaire_nom,
                   mp.objet, mp.nature, mp.procedure, mp.montant,
                   mp.cpv, mp.cpv_label, mp.date_notif, mp.date_pub,
                   mp.duree_mois, mp.lieu_exec, mp.source, mp.source_url,
                   mp.raw_id, mp.event_id,
                   ea.id AS acheteur_entity_id,
                   et.id AS titulaire_entity_id
            FROM marches_publics mp
            LEFT JOIN entities ea ON ea.id = mp.acheteur_id
            LEFT JOIN entities et ON et.id = mp.titulaire_id
            {where}
            ORDER BY mp.date_notif DESC
            LIMIT ?
        """, params)
    finally:
        conn.close()

# ─── /api/budget ───────────────────────────────────────────────────────────────

@app.get("/api/budget", dependencies=[Depends(require_auth)])
def budget(year: Optional[int] = None):
    conn = get_db()
    try:
        if year:
            return rows(conn, "SELECT * FROM budget_annuel WHERE year=? ORDER BY categorie, compte", (year,))
        return rows(conn, "SELECT * FROM budget_annuel ORDER BY year, categorie, compte")
    except Exception:
        return []
    finally:
        conn.close()

# ─── /api/ofgl ─────────────────────────────────────────────────────────────────

@app.get("/api/ofgl", dependencies=[Depends(require_auth)])
def ofgl(year: Optional[int] = None, agregat: Optional[str] = None):
    conn = get_db()
    try:
        filters, params = [], []
        if year:
            filters.append("year = ?"); params.append(year)
        if agregat:
            filters.append("agregat = ?"); params.append(agregat)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        return rows(conn, f"SELECT * FROM ofgl_agregats {where} ORDER BY year, agregat", params)
    except Exception:
        return []
    finally:
        conn.close()

# ─── /api/syntheses ────────────────────────────────────────────────────────────

@app.get("/api/syntheses", dependencies=[Depends(require_auth)])
def syntheses_list():
    if not SYNTHESES_DIR.exists():
        return []
    result = []
    for f in SYNTHESES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            result.append({
                "entity_id": data.get("_entity_id"),
                "entity_name": data.get("_entity_name"),
                "entity_type": data.get("_entity_type"),
                "generated": data.get("_generated"),
                "niveau_interet": data.get("niveau_interet"),
            })
        except Exception:
            pass
    return result

@app.get("/api/syntheses/{entity_id}", dependencies=[Depends(require_auth)])
def synthesis(entity_id: int = FPath(..., ge=1)):
    from collectors.chemins import sous
    path = sous(SYNTHESES_DIR, f"{entity_id}.json")
    if not path.exists():
        raise HTTPException(404, "Synthèse non disponible")
    return json.loads(path.read_text())

# ─── /api/synthesize — synthèse par modèle de langage ─────────────────────────
#
# Le fournisseur est un RÉGLAGE, pas un choix du dispositif. Deux fournisseurs
# étaient nommés en dur dans le code — clés, modèles et ordre de repli — ce qui
# imposait à qui reprend le kit d'ouvrir un compte chez l'un des deux, ou de
# modifier le moteur. Tout passe désormais par quatre variables, et rien n'est
# installé : l'appel se fait en HTTP direct, sans bibliothèque de fournisseur.
#
#   IA_URL        racine de l'API (sans /chat/completions ni /messages)
#   IA_MODELE     identifiant du modèle, tel que le fournisseur l'attend
#   IA_CLE        jeton d'authentification — VIDE pour un modèle local
#   IA_PROTOCOLE  « openai » (défaut) ou « anthropic »
#   IA_DELAI      délai d'un appel en secondes (défaut 1800)
#
# Le protocole « openai » n'engage pas OpenAI : c'est le format de requête que
# parlent aussi Ollama, llama.cpp, vLLM, LM Studio, Groq, Mistral, DeepSeek et
# les passerelles type OpenRouter. Un modèle qui tourne sur la machine de
# collecte se règle comme un service distant, et ne fait sortir aucune donnée.
#
# Rien n'est configuré par défaut : la fonction est absente tant qu'on ne la
# demande pas, et le reste de l'atelier fonctionne sans elle.

_IA_URL       = os.environ.get("IA_URL", "").strip().rstrip("/")
_IA_MODELE    = os.environ.get("IA_MODELE", "").strip()
_IA_CLE       = os.environ.get("IA_CLE", "").strip()
_IA_PROTOCOLE = os.environ.get("IA_PROTOCOLE", "openai").strip().lower()
# Délai d'un appel, en secondes. 600 ne laissaient pas de marge : au banc du
# 25/09/2026, qwen3.8:27b raisonnait jusqu'à ~5 min sur une seule tranche de PV.
# Un modèle local lent n'est pas en panne — l'abandonner perd la tranche.
_IA_DELAI     = int(os.environ.get("IA_DELAI") or 1800)

_IA_PROTOCOLES = ("openai", "anthropic")


def _ia_locale() -> bool:
    """Le modèle tourne-t-il sur CETTE machine ? Jugé sur le nom d'hôte de
    l'adresse, pas sur une sous-chaîne."""
    from urllib.parse import urlsplit
    return (urlsplit(_IA_URL).hostname or "") in ("localhost", "127.0.0.1", "::1")


# Ces routes envoient au modèle des extraits de la base de travail — des noms
# de personnes, des pistes non établies. Vers un modèle local, rien ne sort.
# Vers un service distant, c'est une sortie de données non filtrées : elle se
# DÉCIDE (`IA_HORS_MACHINE=1`), elle ne s'obtient pas en renseignant une adresse.
_IA_HORS_MACHINE = os.environ.get("IA_HORS_MACHINE", "0").strip().lower() \
    not in ("0", "false", "no", "")


def _ia_configuree() -> bool:
    """Une URL et un modèle suffisent. La clé ne fait pas partie du minimum :
    un modèle local n'en demande pas, et l'exiger fermait la porte au seul
    fournisseur qui ne coûte rien et ne publie rien."""
    return bool(_IA_URL and _IA_MODELE) and (_ia_locale() or _IA_HORS_MACHINE)


def _empreinte_modele() -> str | None:
    """La version EXACTE du modèle local : l'empreinte que sert Ollama (ses 12
    premiers caractères, l'« ID » de `ollama list`). Le nom ne suffit pas : un
    `ollama pull` met une autre version sous le même nom (charte IA, engagement 4).

    `None` pour un service distant, ou si le serveur ne la donne pas : la trace
    garde alors le nom seul, et ne prétend pas davantage.
    """
    if not (_IA_URL and _IA_MODELE and _ia_locale()):
        return None
    from urllib.parse import urlsplit
    import requests as _rq
    u = urlsplit(_IA_URL)
    try:
        r = _rq.get(f"{u.scheme}://{u.netloc}/api/tags", timeout=5)
        r.raise_for_status()
        noms = {_IA_MODELE, f"{_IA_MODELE}:latest"}
        for m in r.json().get("models") or []:
            if m.get("name") in noms or m.get("model") in noms:
                return (m.get("digest") or "")[:12] or None
    except Exception:
        return None
    return None


class SynthesizeRequest(BaseModel):
    topic: str
    context: str = ""

SYSTEM_PROMPT = (
    "Tu es un assistant de veille citoyenne pour la commune de "
    f"{COMMUNE_NAME} ({COMMUNE_INSEE}, département {DEPARTEMENT}). "
    "Tu analyses des données OSINT locales (SIRENE, RNA, DVF, délibérations CM, subventions). "
    "Tu réponds en français, de manière factuelle, structurée et concise. "
    "Tu ne spécules pas au-delà des données fournies."
)

def _db_context_for_topic(topic: str, conn) -> str:
    """Charge des données réelles de la DB selon le topic demandé."""
    topic_l = topic.lower()
    parts = []

    if any(k in topic_l for k in ("flux", "subvention", "financ", "budget")):
        data = rows(conn, """
            SELECT ff.type, ff.year, ff.amount, ef.name AS from_name, et.name AS to_name, ff.description
            FROM financial_flows ff
            LEFT JOIN entities ef ON ef.id = ff.from_id
            LEFT JOIN entities et ON et.id = ff.to_id
            ORDER BY ff.year DESC, ff.amount DESC LIMIT 50
        """)
        if data:
            lines = [f"- {r['year']} | {r['type']} | {r['amount']} € | {r['to_name']} ({r['description'] or ''})" for r in data]
            parts.append("FLUX FINANCIERS (50 derniers) :\n" + "\n".join(lines))

    if any(k in topic_l for k in ("immobilier", "dvf", "terrain", "foncier")):
        data = rows(conn, """
            SELECT date, nature_mutation, nature_bien, price, surface_terrain, lieu_dit, cadastre_ref
            FROM dvf_transactions ORDER BY date DESC LIMIT 40
        """)
        if data:
            lines = [f"- {r['date']} | {r['nature_mutation']} | {r['nature_bien']} | {r['price']} € | {r['lieu_dit']} ({r['cadastre_ref']})" for r in data]
            parts.append("TRANSACTIONS DVF (40 dernières) :\n" + "\n".join(lines))

    if any(k in topic_l for k in ("associat", "tissu asso")):
        data = rows(conn, """
            SELECT e.name, a.object, a.creation_date, a.status
            FROM associations a JOIN entities e ON e.id = a.entity_id
            ORDER BY e.name LIMIT 80
        """)
        if data:
            lines = [f"- {r['name']} | {r['object'] or '?'} | créée {r['creation_date'] or '?'} | {r['status'] or '?'}" for r in data]
            parts.append("ASSOCIATIONS :\n" + "\n".join(lines))

    if any(k in topic_l for k in ("économi", "entreprise", "tissu éco")):
        data = rows(conn, """
            SELECT e.name, b.naf_label, b.creation_date, b.status, b.employees_range
            FROM businesses b JOIN entities e ON e.id = b.entity_id
            WHERE b.status = 'A' AND e.type = 'business'
            ORDER BY e.name LIMIT 80
        """)
        if data:
            lines = [f"- {r['name']} | {r['naf_label'] or '?'} | créée {r['creation_date'] or '?'} | {r['employees_range'] or '?'} sal." for r in data]
            parts.append("ENTREPRISES ACTIVES :\n" + "\n".join(lines))

    if any(k in topic_l for k in ("réseau", "influence", "person", "élu", "clé")):
        data = rows(conn, """
            SELECT e.name, p.firstname, p.lastname,
                   COUNT(DISTINCT r.id) AS nb_relations
            FROM persons p
            JOIN entities e ON e.id = p.entity_id
            LEFT JOIN relations r ON r.from_id = e.id OR r.to_id = e.id
            GROUP BY e.id ORDER BY nb_relations DESC LIMIT 40
        """)
        if data:
            lines = [f"- {r['firstname'] or ''} {r['lastname'] or r['name']} | {r['nb_relations']} relations" for r in data]
            parts.append("PERSONNES LES PLUS CONNECTÉES :\n" + "\n".join(lines))

    if any(k in topic_l for k in ("délibér", "cm ", "conseil municipal", "générale", "générale")):
        data = rows(conn, """
            SELECT date, title, source_url FROM events
            WHERE type = 'deliberation'
            ORDER BY date DESC LIMIT 30
        """)
        if data:
            lines = [f"- {r['date']} | {r['title']}" for r in data]
            parts.append("DÉLIBÉRATIONS CM (30 dernières) :\n" + "\n".join(lines))

    return "\n\n".join(parts)

def _build_user_msg(req: SynthesizeRequest, db_ctx: str = "") -> str:
    parts = []
    if db_ctx:
        parts.append(f"DONNÉES DE LA BASE :\n{db_ctx}")
    if req.context:
        parts.append(f"CONTEXTE UTILISATEUR :\n{req.context}")
    parts.append(f"DEMANDE : {req.topic}")
    return "\n\n".join(parts)

def _appel_openai(user_msg: str, system: str = SYSTEM_PROMPT,
                  max_tokens: int = 1024, json_strict: bool = False) -> str:
    """Format « chat completions ». Parlé par Ollama, llama.cpp, vLLM, LM Studio,
    Groq, Mistral, DeepSeek, OpenAI et la plupart des passerelles."""
    import requests as _rq
    entetes = {"Content-Type": "application/json"}
    if _IA_CLE:
        entetes["Authorization"] = f"Bearer {_IA_CLE}"
    charge = {
        "model": _IA_MODELE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
    }
    if json_strict:
        # Demandé, pas exigé : tous les fournisseurs ne connaissent pas ce
        # paramètre. Celui qui l'ignore renvoie du texte, et l'appelant sait
        # déjà y retrouver un objet JSON — mieux vaut une réponse à nettoyer
        # qu'un 400 sur un réglage optionnel.
        charge["response_format"] = {"type": "json_object"}
        # Une extraction n'a pas à être créative : elle recopie ce qu'elle lit.
        charge["temperature"] = 0
    r = _rq.post(f"{_IA_URL}/chat/completions", headers=entetes, json=charge,
                 timeout=_IA_DELAI)
    r.raise_for_status()
    # Pas `["content"]` directement : un modèle à raisonnement rend un contenu
    # VIDE avec un HTTP 200 quand son budget de tokens est épuisé. Ce lecteur
    # lève une erreur qui dit quoi faire, au lieu de propager une chaîne vide.
    return _extraction.contenu_openai(r.json())


def _appel_anthropic(user_msg: str, system: str = SYSTEM_PROMPT,
                     max_tokens: int = 1024, json_strict: bool = False) -> str:
    """Format « messages » d'Anthropic — le seul répandu qui ne soit pas
    compatible avec le précédent, d'où cette seconde branche."""
    import requests as _rq
    entetes = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if _IA_CLE:
        entetes["x-api-key"] = _IA_CLE
    charge = {
        "model": _IA_MODELE,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    }
    if json_strict:
        charge["temperature"] = 0
    r = _rq.post(f"{_IA_URL}/messages", headers=entetes, json=charge,
                 timeout=_IA_DELAI)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def _appel_modele(user_msg: str, **kw) -> str:
    """Le fournisseur réglé, quel qu'il soit. Un seul point d'appel."""
    if _IA_URL and _IA_MODELE and not _ia_configuree():
        raise HTTPException(503, "Le modèle réglé est un service DISTANT : lui envoyer des "
                                 "extraits de la base de travail demande IA_HORS_MACHINE=1 "
                                 "(cf. deploy/env.exemple).")
    if not _ia_configuree():
        raise HTTPException(503, "Aucun modèle configuré : régler IA_URL et IA_MODELE "
                                 "(cf. deploy/env.exemple).")
    if _IA_PROTOCOLE not in _IA_PROTOCOLES:
        raise HTTPException(503, f"IA_PROTOCOLE={_IA_PROTOCOLE} inconnu — "
                                 f"valeurs acceptées : {', '.join(_IA_PROTOCOLES)}.")
    appel = _appel_anthropic if _IA_PROTOCOLE == "anthropic" else _appel_openai
    try:
        return appel(user_msg, **kw)
    except HTTPException:
        raise
    except _extraction.ReponseSansContenu:
        # Remonte telle quelle : `extraire` la rattrape par tranche pour en
        # faire un motif d'échec lisible, plutôt qu'un « rien trouvé » muet.
        raise
    except Exception as e:
        # Le texte d'une exception `requests` porte l'URL appelée (adresse
        # interne, clé en paramètre d'URL) et parfois un bout de réponse :
        # il ne va ni au navigateur ni au journal. Catégories fermées seules.
        import requests as _rq
        code = None
        if isinstance(e, _rq.Timeout):
            motif = "délai dépassé"
        elif isinstance(e, _rq.ConnectionError):
            motif = "connexion au modèle impossible"
        elif isinstance(e, _rq.HTTPError):
            code = getattr(getattr(e, "response", None), "status_code", None)
            motif = f"statut {code}" if isinstance(code, int) else "erreur HTTP"
        elif isinstance(e, (_rq.exceptions.MissingSchema, _rq.exceptions.InvalidSchema,
                            _rq.exceptions.InvalidURL)):
            motif = "réglage du modèle invalide"   # MissingSchema, InvalidURL…
        elif isinstance(e, (KeyError, IndexError, ValueError, TypeError)):
            motif = "réponse illisible"
        else:
            motif = "erreur inattendue"
        print(f"[ia] échec du modèle : {motif} ({type(e).__name__}"
              f"{f', HTTP {code}' if isinstance(code, int) else ''})", file=sys.stderr)
        # 502 et non 500 : la panne est chez le fournisseur, pas dans l'atelier.
        raise HTTPException(502, f"Le modèle n'a pas répondu : {motif}") from None


@app.get("/api/ia/config")
def ia_config(user=Depends(require_auth)):
    """Ce que l'atelier doit savoir pour se décrire honnêtement : le modèle
    réellement réglé, et s'il sort de la machine. L'interface annonçait un
    fournisseur en dur, qui restait affiché après en avoir changé."""
    return {
        "configuree": _ia_configuree(),
        "modele":     _IA_MODELE or None,
        "protocole":  _IA_PROTOCOLE,
        "locale":     bool(_IA_URL) and _ia_locale(),
    }


@app.post("/api/synthesize")
@limiter.limit("10/minute;50/hour")
def synthesize(request: StarletteRequest, req: SynthesizeRequest,
               user=Depends(require_auth)):
    """Interroge le modèle réglé dans l'environnement. Aucun fournisseur par
    défaut : sans réglage, la route dit qu'elle n'est pas configurée."""
    conn = get_db()
    try:
        db_ctx = _db_context_for_topic(req.topic, conn)
    finally:
        conn.close()
    return {"synthesis": _appel_modele(_build_user_msg(req, db_ctx)),
            "provider": _IA_MODELE}


# ─── Extraction assistée ──────────────────────────────────────────────────────
#
# Le prompt, le découpage et la vérification des citations vivent dans
# `collectors/extraction.py` — pas ici. C'est ce qui permet au banc d'essai
# (`scripts/banc_essai_ia.py`) de comparer deux modèles avec EXACTEMENT ce que
# l'atelier leur demande : une seconde copie du prompt divergerait, et l'essai
# ne dirait plus rien de ce qui tourne vraiment.
#
# Le modèle ne saisit rien. Il propose ; l'atelier affiche ; un humain décide.

class ExtraireRequest(BaseModel):
    objet: str                      # flux | acte | marche | budget_vote
    document_id: Optional[int] = None
    event_id: Optional[int] = None


@app.post("/api/atelier/ia/extraire")
@limiter.limit("20/hour")
def atelier_ia_extraire(request: StarletteRequest, req: ExtraireRequest,
                        user=Depends(require_auth)):
    """Propose des lignes structurées d'après un document — sans rien écrire."""
    if req.objet not in _extraction.GABARITS:
        raise HTTPException(400, f"objet inconnu : {req.objet} — valeurs admises : "
                                 f"{', '.join(_extraction.GABARITS)}")
    if not (req.document_id or req.event_id):
        raise HTTPException(400, "préciser document_id ou event_id.")

    conn = get_db()
    try:
        if req.event_id:
            ev = row(conn, "SELECT id, title, content, date, source_url, "
                           "raw_document_id FROM events WHERE id=?", (req.event_id,))
            if not ev:
                raise HTTPException(404, "acte introuvable.")
            texte = ev["content"] or ""
            doc_id = ev["raw_document_id"]
            titre = ev["title"]
            if not doc_id and ev["source_url"]:
                lien = row(conn, "SELECT id FROM raw_documents WHERE url=?",
                           (ev["source_url"],))
                doc_id = lien["id"] if lien else None
        else:
            doc = row(conn, "SELECT id, title, local_path, doc_type FROM raw_documents "
                            "WHERE id=?", (req.document_id,))
            if not doc:
                raise HTTPException(404, "document introuvable.")
            doc_id, titre = doc["id"], doc["title"]
            # Le texte des actes est déjà extrait en base ; le relire d'un PDF
            # ici demanderait pdfplumber dans l'API, donc de tenir un second
            # chemin d'extraction qui divergerait du premier.
            actes = rows(conn, "SELECT content FROM events WHERE raw_document_id=? "
                               "OR source_url=(SELECT url FROM raw_documents WHERE id=?)",
                         (doc_id, doc_id))
            texte = "\n\n".join(a["content"] or "" for a in actes)
    finally:
        conn.close()

    if not texte.strip():
        raise HTTPException(422, "Aucun texte exploitable pour cette source : "
                                 "le document n'a pas encore été lu par un parser.")

    resultat = _extraction.extraire(texte, req.objet, _appel_modele)
    resultat["source"] = {"raw_document_id": doc_id, "titre": titre}
    resultat["modele"] = _IA_MODELE
    # Rendue à l'atelier, qui la renvoie avec la saisie (`source.ia`) : c'est
    # elle qui dira, dans un an, quelle version a lu la ligne.
    resultat["empreinte"] = _empreinte_modele()
    # Le dire explicitement : quand on manipule des données de personnes, savoir
    # si le texte est sorti de la machine n'est pas un détail d'affichage.
    resultat["locale"] = bool(_IA_URL) and _ia_locale()
    return resultat

# ─── /api/candidates ───────────────────────────────────────────────────────────

@app.get("/api/candidates")
def candidates(
    status: str = "pending",
    signal: Optional[str] = None,
    # Un lien PRÉSUMÉ entre deux noms est une hypothèse sur des personnes : il se
    # lit par qui peut le trancher, pas par tout compte (registre des
    # traitements, point 2 — minimisation, 08/10/2026).
    user=Depends(require_au_moins("validator", "Lire les liens présumés")),
):
    filters = ["rc.review_status = ?"]
    params: list = [status]
    if signal:
        filters.append("rc.signal = ?")
        params.append(signal)
    where = "WHERE " + " AND ".join(filters)

    conn = get_db()
    try:
        lignes = rows(conn, f"""
            SELECT rc.id, rc.relation_type, rc.confidence, rc.signal,
                   rc.signal_detail, rc.score, rc.review_status, rc.created_at,
                   rc.locked_by, rc.locked_at,
                   f.id AS from_id, f.name AS from_name, f.type AS from_type,
                   t.id AS to_id,   t.name AS to_name,   t.type AS to_type
            FROM relation_candidates rc
            JOIN entities f ON f.id = rc.from_id
            JOIN entities t ON t.id = rc.to_id
            {where}
            ORDER BY rc.score DESC
        """, params)
        # La réservation était lisible sur la file des sites et pas ici, alors
        # que les deux tables la portent et que le même endpoint la pose : un
        # éditeur ne voyait jamais qu'un lien était déjà pris (23/09/2026).
        for ligne in lignes:
            ligne["reservation"] = _reservation_active(ligne)
            del ligne["locked_by"], ligne["locked_at"]
            # Ce que « Oui » changera, lu dans les RÈGLES de l'instance et non
            # deviné par l'écran. Depuis que `detect_links` est câblé, la file
            # mélange des sièges de commission (qui se publient) et des
            # doublons ou des lieux-dits partagés (qui ne sortent jamais) :
            # une seule phrase pour les deux aurait menti aux trois quarts.
            ligne["sort_si_accepte"] = _sort_du_type(ligne["relation_type"])
        return lignes
    finally:
        conn.close()

class ReviewRequest(BaseModel):
    action: str   # "accept" | "reject" | "ignore"
    note: str = ""


def optional_user(jeton: Optional[str] = Depends(jeton_de)):
    """Utilisateur JWT si présent et valide, sinon None (pas d'erreur)."""
    if not jeton:
        return None
    try:
        return require_auth(jeton)
    except HTTPException:
        return None

def _check_admin(x_admin_key: Optional[str], user: Optional[dict] = None,
                 role_min: str = "admin"):
    """Autorise : JWT d'au moins `role_min`, ou clé ADMIN_KEY (scripts/CLI).

    `allow_validator=True` laissait passer `validator` ET `contributor` : le nom
    promettait un validateur, le code admettait tout compte.

    Le refus dit ce que le rôle ne permet pas (`exiger`). Il disait « configurer
    ADMIN_KEY en production » à un contributeur qui n'y pouvait rien."""
    if _cle_admin_valide(x_admin_key):
        return
    exiger(user, role_min, "Cette action")

def _read_json_file(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _public_snapshot_status():
    """Le contexte que la page de publication affiche : projet, règles, chemins."""
    from scripts.build_public_snapshot import DEFAULT_OUT, RULES_PATH

    rules = _read_json_file(RULES_PATH, {})
    # Le rapport QA est écrit HORS du snapshot depuis que `verify_snapshot.py`
    # interdit `review_report*.json` dans un répertoire publié — à raison, il
    # contient les exclusions nominatives. Le chercher dans le snapshot rendait
    # « Règles actives » vide en permanence, sans que rien ne le signale ; seule
    # la clé `rules` est reprise, jamais les exclusions.
    report = _read_json_file(BASE_DIR / "audits" / "public_snapshot_review.json", {})
    report = {"rules": report.get("rules", {})}
    return {
        "output_dir": str(DEFAULT_OUT),
        "rules_path": str(RULES_PATH),
        "project": rules.get("project", {}),
        "rules": report.get("rules", {}),
    }

# Les règles de publication et le sort d'un lien accepté viennent du builder.
# `POST /api/admin/public-snapshot/generate`, seconde voie de publication sans
# verrou ni bascule et sans appelant, est retirée (Julien, 14/11/2026), comme
# `GET /api/admin/public-snapshot`, lecture admise à la clé et sans appelant.
from scripts.build_public_snapshot import (  # noqa: E402
    RULES as _REGLES_PUBLICATION,
    sort_du_type as _sort_du_type)

#: Confiances qui partent sur le site, lues dans les règles de l'instance. Poser
#: l'une d'elles, c'est trancher (réservé au validateur) : un contributeur propose
#: en `probable` ou `hypothesis`.
PUBLIABLES = frozenset(_REGLES_PUBLICATION["confidence"]["public"])

# Le flux de publication (aperçu → contrôle → publication) vit dans
# `scripts/publication.py` : il n'a besoin ni de FastAPI ni d'une base, et les
# tests le rejouent sur des répertoires jetables. api.py y ajoute ce qu'il est
# seul à savoir — qui a cliqué.
from scripts import publication as pub   # noqa: E402

# ─── Décisions : exporter / importer ──────────────────────────────────────────
#
# La base est reconstructible — n'importe qui la refait avec le code et un code
# INSEE. Le jugement humain, non : les sièges tranchés, les sites validés, les
# corrections, et depuis aujourd'hui les saisies. C'est petit, c'est cher, et
# c'est la seule chose qui mérite de circuler.
#
# ⚠ L'export PEUT NOMMER DES PERSONNES PHYSIQUES : l'atelier travaille sur la
# base non filtrée. Le répertoire produit va dans un dépôt PRIVÉ, ou nulle part.
# `sans_personnes` retire ce qui porte sur une personne, au prix de devoir le
# ressaisir en face.

class ExportDecisionsRequest(BaseModel):
    sans_personnes: bool = False


@app.post("/api/atelier/decisions/exporter")
def atelier_exporter_decisions(req: ExportDecisionsRequest,
                               user=Depends(require_role("admin"))):
    from scripts.exporter_decisions import exporter

    dest = RACINE / "decisions"
    conn = get_db()
    try:
        compte = exporter(conn, dest, req.sans_personnes)
    finally:
        conn.close()
    return {"ok": True, "vers": str(dest), "compte": compte,
            "avertissement": None if req.sans_personnes else
            "Cet export peut nommer des personnes physiques : dépôt privé obligatoire."}


@app.get("/api/atelier/decisions")
def atelier_decisions_etat(user=Depends(require_auth)):
    """Ce qu'il y a dans le répertoire de décisions, sans rien y toucher."""
    dest = RACINE / "decisions"
    manifeste = dest / "manifeste.json"
    if not manifeste.is_file():
        return {"present": False}
    try:
        contenu = json.loads(manifeste.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"present": True, "lisible": False}
    return {"present": True, "lisible": True, **contenu}


class ImportDecisionsRequest(BaseModel):
    # Par défaut on REGARDE sans écrire. Importer des décisions d'un autre
    # atelier est irréversible et peut contredire les siennes : le rapport à
    # blanc d'abord, l'écriture ensuite, jamais l'inverse.
    appliquer: bool = False
    forcer: bool = False


@app.post("/api/atelier/decisions/importer")
def atelier_importer_decisions(req: ImportDecisionsRequest,
                               user=Depends(require_role("admin"))):
    from scripts.importer_decisions import importer

    src = RACINE / "decisions"
    if not (src / "manifeste.json").is_file():
        raise HTTPException(404, f"Aucune décision à importer dans {src}.")
    manifeste = json.loads((src / "manifeste.json").read_text(encoding="utf-8"))
    # Refus net : les clés naturelles d'une commune ne désignent rien dans une
    # autre, et un import croisé mélangerait deux territoires en silence.
    if manifeste.get("insee") and manifeste["insee"] != COMMUNE_INSEE:
        raise HTTPException(
            409, f"Ces décisions viennent de {manifeste.get('commune')} "
                 f"({manifeste['insee']}), cet atelier est {COMMUNE_NAME} "
                 f"({COMMUNE_INSEE}).")

    conn = get_db_rw()
    try:
        rap = importer(conn, src, req.appliquer, req.forcer)
        if req.appliquer:
            conn.commit()
    finally:
        conn.close()
    return {
        "ok": True, "applique_reellement": req.appliquer,
        "appliquees": rap.applique, "deja_a_jour": rap.a_jour,
        "desaccords": rap.desaccords, "non_rattachees": rap.non_rattachees,
        "sans_objet": rap.sans_objet,
    }



# ─── Publication en deux temps : aperçu, contrôle, publication ───────────────
#
# L'endpoint ci-dessus construit par-dessus le répertoire servi puis synchronise
# dans la foulée : un seul geste, irréversible, dont le résultat ne se regarde
# qu'une fois en ligne. Il reste — `deploy/publier-site.sh` et les scripts CLI
# s'en servent — mais l'atelier ne l'offre plus. Ce qu'on publie se regarde
# d'abord, et ce qui est rouge ne se publie pas.


#: Délai minimal, en secondes, entre deux aperçus d'un même compte.
APERCU_DELAI_S = 120


class ApercuServeurRequest(BaseModel):
    action: Literal["demarrer", "arreter"] = "demarrer"


def _cle_admin_valide(x_admin_key: Optional[str]) -> bool:
    return bool(ADMIN_KEY) and _secrets.compare_digest((x_admin_key or "").encode(),
                                                       ADMIN_KEY.encode())


def _role_effectif(x_admin_key: Optional[str], user: Optional[dict]) -> Optional[str]:
    """La clé admin VAUT admin : c'est le contrat des scripts depuis l'origine.

    Sans cette ligne, un validateur qui possède aussi la clé se verrait refuser
    ce qu'un script anonyme muni de la même clé obtient — l'identification
    dégraderait le droit, ce qui est le meilleur moyen de la faire contourner.
    """
    if _cle_admin_valide(x_admin_key):
        return "admin"
    return (user or {}).get("role")


def _auteur(user: Optional[dict]) -> str:
    """Un nom lisible dans « publié par ». Sans JWT, l'auteur est la clé."""
    if user:
        return user.get("email") or f"utilisateur #{user.get('id')}"
    return "clé admin (script)"


def _journal_publication(user: Optional[dict], action: str, detail: dict) -> None:
    """Qui a généré un aperçu, qui a publié, quand, et avec quel verdict.

    Dans `audit_log`, la table qui porte déjà toutes les écritures de l'atelier :
    une publication est une écriture comme une autre, en plus lourde de
    conséquences. Lui donner un journal à part, c'est se garantir qu'on lira
    l'un des deux.

    Le journal ne doit jamais faire échouer ce qu'il journalise : une base
    verrouillée ne transforme pas une publication réussie en erreur.
    """
    try:
        conn = get_db_rw()
        try:
            conn.execute(
                "INSERT INTO audit_log(user_id, table_name, action, new_value) "
                "VALUES(?,?,?,?)",
                ((user or {}).get("id"), "publication", action,
                 json.dumps(detail, ensure_ascii=False)[:4000]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                      # noqa: BLE001
        print(f"  [publication] journal non écrit : {e}", file=sys.stderr)


def _etat_complet(x_admin_key: Optional[str], user: Optional[dict]) -> dict:
    """L'état du flux plus le contexte que la page affiche en permanence.

    Les trois routes rendent la MÊME forme. Une action qui ne renverrait que
    l'état laisserait la page sans son nom de projet ni ses règles actives, et
    la moitié de l'écran se viderait au moment où on vient d'agir.
    """
    role = _role_effectif(x_admin_key, user)
    contexte = _public_snapshot_status()
    # Connecté, on voit SON aperçu ; par la clé de service, le brouillon de la
    # ligne de commande.
    return pub.etat_publication((user or {}).get("id")) | {
        "role": role,
        "peut_agir": pub.peut_publier(role),
        # Générer et regarder un aperçu : validateur et au-dessus (Julien,
        # 07/10/2026 — tout compte depuis le 17/09, la page ne s'ouvre plus
        # au contributeur).
        "peut_apercevoir": au_moins(user, "validator") or _cle_admin_valide(x_admin_key),
        "project": contexte["project"],
        "rules": contexte["rules"],
        "rules_path": contexte["rules_path"],
        "output_dir": contexte["output_dir"],
    }


@app.get("/api/admin/publication",
         dependencies=[Depends(require_compte_ou_cle("validator", "Voir l'état de la publication"))])
def publication_etat(x_admin_key: Optional[str] = Header(default=None),
                     user=Depends(optional_user)):
    """L'état du flux, en LECTURE SEULE — validateur et au-dessus.

    Un validateur ne publie pas, mais il doit pouvoir savoir si la correction
    qu'il vient de faire est en ligne, et si le dernier contrôle est rouge.
    Le contributeur propose : ce qui part en ligne ne se décide pas à son poste
    (Julien, 07/10/2026). Jusque-là la route ne contrôlait rien du tout.
    """
    _check_admin(x_admin_key, user, role_min="validator")
    return _etat_complet(x_admin_key, user)


@app.post("/api/admin/publication/apercu",
         dependencies=[Depends(require_compte_ou_cle("validator", "Générer un aperçu"))])
def publication_apercu(x_admin_key: Optional[str] = Header(default=None),
                       user=Depends(optional_user)):
    """Construit l'aperçu et le contrôle. Rien de servi ne bouge.

    Ouvert au validateur et au-dessus (tout compte du 17/09 au 07/10/2026) :
    chacun génère le sien, daté, dans `audits/apercus/<compte>/`, qui remplace
    son précédent. La clé de service, sans compte, génère le brouillon de la
    ligne de commande.

    Un contrôle rouge n'est PAS une erreur de cette route : l'aperçu a bien été
    produit, c'est son verdict qui est rouge — et c'est exactement ce qu'on
    voulait pouvoir regarder. Répondre 500 ici ferait disparaître le rapport
    dans une bannière d'erreur au lieu de l'afficher.
    """
    _check_admin(x_admin_key, user, role_min="validator")
    from scripts.build_public_snapshot import PerimetreNonClasse
    # Un aperçu reconstruit le snapshot ENTIER, sous le verrou de publication :
    # relancé en boucle par un seul compte, il immobilise le serveur et la
    # publication de tout le monde. Un par compte toutes les deux minutes.
    if user:
        dernier = pub.apercu_du_compte(user["id"]).get("genere_le")
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(dernier)).total_seconds()
        except (TypeError, ValueError):
            age = None
        if age is not None and age < APERCU_DELAI_S:
            raise HTTPException(429, f"Votre aperçu vient d'être généré : attendre "
                                     f"{int(APERCU_DELAI_S - age) + 1} s avant d'en "
                                     f"demander un autre.")
    try:
        resume = (pub.generer_apercu_du_compte(user["id"], auteur=_auteur(user)) if user
                  else pub.generer_apercu(auteur=_auteur(user)))
    except pub.PublicationRefusee as e:
        raise HTTPException(400, e.message)
    except PerimetreNonClasse as e:
        raise HTTPException(400, f"Snapshot refusé — {e}")
    controle = resume.get("controle") or {}
    _journal_publication(user, "apercu", {
        "repertoire": resume.get("repertoire"),
        "controle_ok": controle.get("ok"),
        "violations": controle.get("compte_erreurs"),
        "avertissements": controle.get("compte_avertissements"),
        "entites_publiques": (resume.get("stats") or {}).get("entities_public"),
    })
    return _etat_complet(x_admin_key, user)


@app.post("/api/admin/publication/publier",
         dependencies=[Depends(require_compte_ou_cle("admin", "Publier"))])
def publication_publier(x_admin_key: Optional[str] = Header(default=None),
                        user=Depends(optional_user)):
    """Porte l'aperçu contrôlé vers les deux emplacements servis. Admin seul, et
    à partir de SON aperçu : on publie ce qu'on a regardé."""
    _check_admin(x_admin_key, user)
    role = _role_effectif(x_admin_key, user)
    try:
        publie = pub.publier(auteur=_auteur(user), role=role,
                             apercu=pub.apercu_du_compte(user["id"]) if user else None)
    except pub.PublicationRefusee as e:
        _journal_publication(user, "publication-refusee",
                             {"motif": e.message, "role": role})
        raise HTTPException(409, {"message": e.message, **e.detail})
    _journal_publication(user, "publication", {
        "repertoire": publie.get("repertoire"),
        "apercu_genere_le": publie.get("apercu_genere_le"),
        "fichiers_publies": (publie.get("copie") or {}).get("count"),
        "fichiers_site": (publie.get("synchro") or {}).get("count"),
        "differences": publie.get("differences"),
        "entites_publiques": (publie.get("stats") or {}).get("entities_public"),
        # La garde de volume ne bloque que la passe de nuit : une publication
        # faite ici la lève, et la baisse qu'elle publie reste consignée.
        "chutes_de_volume": publie.get("chutes_de_volume"),
    })
    return _etat_complet(x_admin_key, user)


@app.post("/api/admin/publication/verifier-en-ligne",
         dependencies=[Depends(require_compte_ou_cle("validator", "Vérifier ce qui est en ligne"))])
def publication_verifier_en_ligne(x_admin_key: Optional[str] = Header(default=None),
                                  user=Depends(optional_user)):
    """Constate ce que le site public sert vraiment.

    C'est le seul état que l'atelier ne peut pas déduire de ses propres
    écritures : entre la promotion locale et la mise en ligne il y a un build et
    un déploiement qu'il ne fait pas. Tant que personne n'a interrogé le site,
    « déployé » reste une supposition — et la page l'affichait comme un fait.

    Ouvert au validateur : constater n'écrit rien dans les données.
    """
    _check_admin(x_admin_key, user, role_min="validator")
    verdict = pub.verifier_en_ligne()
    _journal_publication(user, "verification-en-ligne", {
        "url": verdict.get("url_interrogee"),
        "ok": verdict.get("ok"),
        "empreinte": verdict.get("empreinte"),
        "empreinte_attendue": verdict.get("empreinte_attendue"),
    })
    return _etat_complet(x_admin_key, user)


@app.post("/api/admin/publication/mettre-en-ligne",
         dependencies=[Depends(require_compte_ou_cle("admin", "Mettre le site en ligne"))])
def publication_mettre_en_ligne(x_admin_key: Optional[str] = Header(default=None),
                                user=Depends(optional_user)):
    """Construit le site depuis la version promue et le téléverse. Admin seul.

    Le seul geste de tout l'atelier qui sorte de la machine. Il rend la main
    tout de suite : construire 1 449 pages et les téléverser prend plusieurs
    minutes, et une route qui attendrait la fin ferait expirer le navigateur
    avant le déploiement. L'avancement se lit dans l'état.
    """
    _check_admin(x_admin_key, user)
    role = _role_effectif(x_admin_key, user)
    try:
        lance = pub.mettre_en_ligne(auteur=_auteur(user), role=role)
    except pub.PublicationRefusee as e:
        _journal_publication(user, "mise-en-ligne-refusee",
                             {"motif": e.message, "role": role})
        raise HTTPException(409, {"message": e.message, **e.detail})
    _journal_publication(user, "mise-en-ligne", {
        "destination": lance.get("destination"),
        "empreinte_visee": lance.get("empreinte_visee"),
    })
    return _etat_complet(x_admin_key, user)


@app.post("/api/admin/publication/revenir",
         dependencies=[Depends(require_compte_ou_cle("admin", "Revenir à la version précédente"))])
def publication_revenir(x_admin_key: Optional[str] = Header(default=None),
                        user=Depends(optional_user)):
    """Remet en service la version d'avant, sur les deux emplacements servis.

    Un contrôle vert ne dit pas qu'une version est BONNE : il dit qu'elle est
    étanche. Un chiffre faux, un découpage raté, une page vide passent le
    contrôle — et jusqu'ici, revenir en arrière demandait de reconstruire tout
    un snapshot, donc de disposer de la base d'avant.
    """
    _check_admin(x_admin_key, user)
    try:
        with pub.verrou_de_publication():
            repris = [pub.revenir_a_la_version_precedente(d)
                      for d in (pub.SITE, pub.PUBLIE)]
    except pub.PublicationRefusee as e:
        raise HTTPException(409, {"message": e.message, **e.detail})
    _journal_publication(user, "retour-arriere",
                         {"emplacements": [r["dest"] for r in repris]})
    return _etat_complet(x_admin_key, user)


@app.get("/api/admin/publication/modifications",
         dependencies=[Depends(require_compte_ou_cle("validator", "Voir ce qui partira à la publication"))])
def publication_modifications(x_admin_key: Optional[str] = Header(default=None),
                              user=Depends(optional_user)):
    """Ce qui partira avec la prochaine publication, et ce qui attend encore.

    C'est la question que se pose vraiment celui qui s'apprête à publier :
    « qu'est-ce qui a changé, par qui, et est-ce que je le laisse partir ? »
    Trois listes, chacune avec son quoi / qui / quand / pourquoi :

    - `propositions` : ce qu'un contributeur a voulu écrire sur un objet publié,
      et qui attend. Les marchés lus dans un procès-verbal n'y sont que comptés :
      ils se relisent l'acte sous les yeux, dans leur file.
    - `contributions` : les fiches touchées depuis la dernière publication.
    - `nouvelles` : les fiches entrées en base depuis.

    `dans_apercu` dit si la fiche existe dans le brouillon : une entité écartée
    par le filtre de publication n'a pas de page, et proposer le lien enverrait
    sur un 404 en laissant croire à une panne.
    """
    _check_admin(x_admin_key, user, role_min="validator")
    etat = pub.etat_publication((user or {}).get("id"))
    depuis = (etat.get("publie") or {}).get("publie_le")
    borne = pub.horodatage_base(depuis)
    verdict = ("LEFT JOIN annotations d ON d.object_type = 'entity' "
               "AND d.object_id = e.id")

    conn = get_db_rw()
    try:
        _propositions.assurer_schema(conn)
        conn.commit()
        attendent = _propositions.lister(conn, "en_attente")
        propositions = [
            {c: p[c] for c in ("id", "nature", "object_type", "object_id", "entity_id",
                               "fiche", "charge", "avant", "propose_par", "propose_le")}
            for p in attendent if p["nature"] not in _EXTRAITS]
        contributions = rows(conn, f"""
            SELECT e.id, e.name, e.type, e.confidence,
                   COUNT(*) AS modifications,
                   MAX(al.at) AS derniere,
                   GROUP_CONCAT(DISTINCT al.field) AS champs,
                   GROUP_CONCAT(DISTINCT u.email) AS par,
                   COALESCE(d.review_status, 'jamais_relu') AS verdict, d.note
            FROM audit_log al
            JOIN entities e ON e.id = al.entity_id
            LEFT JOIN users u ON u.id = al.user_id
            {verdict}
            WHERE al.entity_id IS NOT NULL
              AND COALESCE(al.table_name, '') <> 'propositions'
              AND (? IS NULL OR (al.at > ? AND e.created_at <= ?))
            GROUP BY e.id
            ORDER BY derniere DESC
            LIMIT 50
        """, (borne, borne, borne))
        # Sans publication antérieure, tout serait « nouveau » : la liste ne
        # dirait rien. Elle commence à la première publication.
        nouvelles = rows(conn, f"""
            SELECT e.id, e.name, e.type, e.confidence, e.origine,
                   e.created_at AS derniere,
                   (SELECT u.email FROM audit_log al JOIN users u ON u.id = al.user_id
                     WHERE al.entity_id = e.id ORDER BY al.id LIMIT 1) AS par,
                   COALESCE(d.review_status, 'jamais_relu') AS verdict, d.note
            FROM entities e {verdict}
            WHERE e.created_at > ?
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT 50
        """, (borne,)) if borne else []
        total_nouvelles = row(conn, "SELECT COUNT(*) AS n FROM entities WHERE created_at > ?",
                              (borne,))["n"] if borne else 0
    finally:
        conn.close()

    brouillon = Path((etat.get("brouillon") or {}).get("repertoire") or pub.BROUILLON)
    for ligne in contributions + nouvelles:
        ligne["verdict"] = verdict_de(ligne["verdict"]) or JAMAIS_RELU
        ligne["dans_apercu"] = (brouillon / "entite" / f"{ligne['id']}.json").is_file()
    return {"depuis": depuis, "propositions": propositions,
            "marches_a_relire": len(attendent) - len(propositions),
            "contributions": contributions,
            "nouvelles": nouvelles, "total_nouvelles": total_nouvelles,
            "peut_trancher": au_moins(user, "validator")}


@app.post("/api/admin/publication/apercu/serveur",
         dependencies=[Depends(require_compte_ou_cle("validator", "Démarrer le serveur d'aperçu"))])
def publication_apercu_serveur(req: ApercuServeurRequest = ApercuServeurRequest(),
                               x_admin_key: Optional[str] = Header(default=None),
                               user=Depends(optional_user)):
    """Démarre (ou arrête) le SITE PUBLIC lui-même, branché sur le brouillon.

    Pas une maquette de l'aperçu : le site, ses composants, sa feuille de style,
    sur son propre port, avec `VIGIE_DATA_DIR` pointé sur le brouillon. C'est la
    seule façon qu'un aperçu ressemble à ce qui sera publié — et il est servi à
    la RACINE de ce port parce que les liens du site sont absolus.

    Chaque validateur montre SON aperçu ; tous passent par le même port, le serveur
    choisissant l'aperçu d'après le lien. L'arrêter coupe celui de tout le
    monde : réservé à l'admin.
    """
    _check_admin(x_admin_key, user,
                 role_min="admin" if req.action == "arreter" else "validator")
    try:
        if req.action == "arreter":
            return pub.arreter_serveur_apercu()
        if user:
            return pub.demarrer_serveur_apercu_du_compte(user["id"])
        return pub.demarrer_serveur_apercu()
    except pub.PublicationRefusee as e:
        raise HTTPException(400, e.message)

@app.post("/api/candidates/{candidate_id}/review",
         dependencies=[Depends(require_compte_ou_cle("validator", "Trancher une relation candidate"))])
def review_candidate(
    candidate_id: int = FPath(..., ge=1),
    req: ReviewRequest = ...,
    x_admin_key: Optional[str] = Header(default=None),
    user=Depends(optional_user),
):
    if user and not au_moins(user, "validator"):
        exiger(user, "validator", "Trancher une relation candidate")
    _check_admin(x_admin_key, user, role_min="validator")
    if req.action not in ("accept", "reject", "ignore"):
        raise HTTPException(400, "action doit être: accept, reject ou ignore")

    status_map = {"accept": "accepted", "reject": "rejected", "ignore": "ignored"}
    new_status = status_map[req.action]

    conn = get_db_rw()
    try:
        candidate = row(conn, "SELECT * FROM relation_candidates WHERE id = ?", (candidate_id,))
        if not candidate:
            raise HTTPException(404, "Candidat introuvable")
        # Même garde que pour les sites : la réservation n'était tenue que par
        # l'interface (audit du 15/11/2026).
        active = _reservation_active(candidate)
        if user and active and active["par"] != user["email"]:
            raise _conflit(f"Pris par {active['par']}, qui l'examine.", active["par"],
                           active["le"], expire_dans_min=active["expire_dans_min"])

        # Un lien qu'un validateur a refusé ne revient pas par un candidat : le
        # déclencheur avalerait l'INSERT et le candidat passerait « accepté »
        # sans lien (second avis de vigie-securite).
        if req.action == "accept":
            refus = refus_de_relation(conn, candidate["from_id"], candidate["to_id"],
                                      candidate["relation_type"])
            if refus:
                raise HTTPException(409, {
                    "message": "Ce lien entre ces deux fiches a été refusé par un "
                               "validateur : le refus doit être levé d'abord.",
                    "refus_id": refus["id"]})

        conn.execute("""
            UPDATE relation_candidates
            SET review_status = ?, reviewed_at = datetime('now'), review_note = ?
            WHERE id = ?
        """, (new_status, req.note, candidate_id))

        if req.action == "accept":
            conn.execute("""
                INSERT OR IGNORE INTO relations
                    (from_id, to_id, relation_type, confidence, source, metadata)
                VALUES (?, ?, ?, ?, 'candidate', ?)
            """, (
                candidate["from_id"],
                candidate["to_id"],
                candidate["relation_type"],
                candidate["confidence"],
                json.dumps({"signal": candidate["signal"], "signal_detail": candidate["signal_detail"]}),
            ))

        conn.commit()
        return {"ok": True, "status": new_status}
    finally:
        conn.close()

# ─── /api/atelier/stats ────────────────────────────────────────────────────────

@app.get("/api/atelier/stats")
def atelier_stats(user=Depends(require_auth)):
    conn = get_db()
    try:
        total = row(conn, "SELECT COUNT(*) AS n FROM entities")["n"]
        # Le verdict d'une fiche est une décision (`annotations`), plus la
        # colonne `validation_status`, que la publication n'a jamais lue.
        by_verdict = rows(conn, """
            SELECT COALESCE(d.review_status, 'jamais_relu') AS verdict, COUNT(*) AS n
            FROM entities e
            LEFT JOIN annotations d ON d.object_type = 'entity' AND d.object_id = e.id
            GROUP BY 1
        """)
        result = {"total": total}
        for r in by_verdict:
            result[r["verdict"]] = r["n"]
        # Le total seul ne dit plus rien depuis l'élargissement à l'EPCI :
        # l'atelier doit savoir combien de fiches relèvent de la commune.
        result["perimetre"] = {
            (r["perimetre"] or "non_classe"): r["n"]
            for r in rows(conn, "SELECT perimetre, COUNT(*) AS n FROM entities "
                                "GROUP BY perimetre")
        }
        return result
    finally:
        conn.close()

# ─── /api/atelier/decision/{type}/{id} — l'écran d'une décision ──────────────
# Lot C du chantier, 23/09/2026. Trois zones, et rien d'autre : la QUESTION en
# français, les PREUVES avec leur origine et leur date, le GESTE et sa
# conséquence. L'atelier ne montrait jusqu'ici qu'un tableau de lignes et des
# ✓ ✗ sans libellé : on tranchait sans jamais voir sur quoi.

#: Ce qu'il faut savoir d'un objet pour en décider : sa revendication en une
#: phrase, et l'acte qui la porte. Une clé par type d'`OBJETS` servi ici.
_REVENDICATION = {
    "flow": lambda o: (
        f"{_euros(o['amount'])} — {o['description'] or o['type'] or 'flux'}"
        + (f", de {o['from_name']}" if o.get("from_name") else "")
        + (f" à {o['to_name']}" if o.get("to_name") else "")
        + (f", en {o['year']}" if o.get("year") else "")),
    "marche": lambda o: (
        f"{_euros(o['montant'])} — {o['objet'] or 'marché'}"
        + (f", attribué à {o['titulaire_nom']}" if o.get("titulaire_nom") else "")
        + (f" par {o['acheteur_nom']}" if o.get("acheteur_nom") else "")
        + (f", le {o['date_notif']}" if o.get("date_notif") else "")),
    "deliberation": lambda o: (o["title"] or "acte sans titre")
        + (f", séance du {o['date']}" if o.get("date") else ""),
}


def _euros(montant) -> str:
    """Un montant en français, ou ce qu'il faut lire quand il n'y en a pas.

    ⭐ **Un montant absent ne s'affiche pas « 0 € »**, et ZÉRO non plus. Relevé
    le 23/09/2026 sur Lasalle : 10 flux ont `amount` NULL, 2 valent 0, et
    l'écran de décision présentait le premier comme « 0 € — Demande subvention
    DETR ». Personne ne vote une subvention de zéro euro : ce zéro dit que la
    lecture de l'acte n'a pas su retenir le montant. Le présenter comme un fait
    ferait écarter une ligne juste pour un défaut de collecte — et c'est
    exactement la famille de défauts qui a le plus coûté à ce projet.
    """
    if montant is None or int(montant) == 0:
        return "Aucun montant retenu"
    return f"{int(montant):,}".replace(",", " ") + " €"


def _acte_de(conn, object_type: str, object_id: int) -> Optional[dict]:
    """L'acte qui porte la ligne : son titre, sa date, et SON TEXTE.

    C'est la pièce. Sans elle, « ce montant est-il bien celui que l'acte a
    voté ? » est une question à laquelle on ne peut pas répondre, et le geste
    se réduit à faire confiance au collecteur — ce qu'on voulait justement
    cesser de faire.

    ⚠️ Le lien se relit DANS LA TABLE SOURCE, pas dans la ligne servie par la
    liste : `_donnees_query` ne sélectionne pas `event_id`, et s'appuyer sur
    ses colonnes rendait « Aucun acte rattaché » sur tous les flux. Un test l'a
    attrapé ; à l'écran, la zone de preuves serait restée vide sans un mot.
    """
    if object_type == "deliberation":
        eid = object_id
    else:
        table = TABLE_DE_TYPE[object_type]
        lien = row(conn, f"SELECT event_id FROM {table} WHERE id=?", (object_id,))
        eid = lien["event_id"] if lien else None
    if not eid:
        return None
    acte = row(conn, "SELECT id, title, date, content, source, source_url, "
                     "raw_document_id FROM events WHERE id=?", (eid,))
    return dict(acte) if acte else None


#: Les colonnes de traçabilité que l'écran affiche et que la liste n'a pas
#: besoin de porter. Lues dans la table source, pour la même raison.
def _tracabilite(conn, object_type: str, object_id: int) -> dict:
    table = TABLE_DE_TYPE[object_type]
    colonnes = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    voulues = [c for c in ("saisi_par", "saisi_le", "source_url", "origine")
               if c in colonnes]
    if not voulues:
        return {}
    r = row(conn, f"SELECT {', '.join(voulues)} FROM {table} WHERE id=?",
            (object_id,))
    return dict(r) if r else {}


@app.get("/api/atelier/decision/{object_type}/{object_id}")
def atelier_decision(
    object_type: str,
    object_id: int = FPath(..., ge=1),
    user=Depends(require_auth),
):
    """Un objet, ses preuves, les gestes ouverts, et le suivant de la file."""
    if object_type not in TABLE_DE_TYPE:
        raise HTTPException(400, f"type invalide — valeurs: "
                                 f"{', '.join(TABLE_DE_TYPE)}")
    sql, params = _donnees_query(object_type, 1000)
    conn = get_db()
    try:
        items = {o["id"]: o for o in rows(conn, sql, params)}
        objet = items.get(object_id)
        if not objet:
            raise HTTPException(404, "Ligne introuvable")

        a = row(conn, "SELECT review_status, note, reviewed_by, reviewed_at "
                      "FROM annotations WHERE object_type=? AND object_id=?",
                (object_type, object_id))
        verdict = verdict_de(a["review_status"]) if a else JAMAIS_RELU

        # Le suivant, et le reste : la MÊME définition du « à trancher » que la
        # carte d'« Aujourd'hui », et elle TRAVERSE LES TYPES. L'écran comptait
        # d'abord toutes les lignes de la table — 544 flux à Lasalle — puis les
        # 76 flux probables quand la carte en annonçait 79, les trois marchés
        # restant invisibles. Deux définitions du même travail, et c'est celle
        # de l'écran qui décide le bénévole à s'arrêter.
        file = a_trancher(conn)
        suivant = next((x for x in file
                        if not (x["objet"] == object_type and x["id"] == object_id)),
                       None)

        return {
            "type": object_type,
            "id": object_id,
            "question": _QUESTION_DE_TYPE.get(object_type, "Cette ligne est-elle juste ?"),
            "revendication": _REVENDICATION[object_type](objet),
            "objet": {**objet, **_tracabilite(conn, object_type, object_id)},
            "acte": _acte_de(conn, object_type, object_id),
            "decision": {"verdict": verdict,
                         "note": (a["note"] if a else "") or "",
                         "par": a["reviewed_by"] if a else None,
                         "le": a["reviewed_at"] if a else None},
            "gestes": [
                {"cle": g.cle, "libelle": g.libelle, "verdict": g.verdict,
                 "effet": g.effet, "demande_un_mot": g.demande_un_mot}
                for g in GESTES
                # « Remettre à relire » n'a de sens que sur une ligne tranchée :
                # l'offrir toujours, c'est proposer d'annuler ce qui n'existe pas.
                if not (g.verdict == JAMAIS_RELU and verdict == JAMAIS_RELU)
            ],
            "suivant": suivant,
            "reste": len(file),
        }
    finally:
        conn.close()


class GesteRequest(BaseModel):
    geste: str
    #: Ce que la personne ajoute. Obligatoire pour les gestes qui le demandent :
    #: écarter sans dire ce qui est faux laisse le suivant au même point.
    precision: str = ""


@app.post("/api/atelier/decision/{object_type}/{object_id}")
def atelier_poser_geste(
    object_type: str,
    object_id: int = FPath(..., ge=1),
    req: GesteRequest = ...,
    user=Depends(require_au_moins("validator", "Trancher une ligne")),
):
    """Poser un GESTE. Il devient un verdict et un motif, par `_decider`.

    Un geste n'ouvre pas un second chemin d'écriture : il se traduit en
    verdict, et repart par l'écrivain unique des décisions. Deux chemins pour
    écrire la même chose, c'est deux vérités.
    """
    if object_type not in TABLE_DE_TYPE:
        raise HTTPException(400, f"type invalide — valeurs: {', '.join(TABLE_DE_TYPE)}")
    geste = geste_de(req.geste)
    if geste is None:
        raise HTTPException(400, f"geste inconnu : « {req.geste} » — valeurs : "
                                 f"{', '.join(g.cle for g in GESTES)}")
    if geste.demande_un_mot and not req.precision.strip():
        raise HTTPException(400, {
            "message": f"« {geste.libelle} » demande d'expliquer en un mot ce "
                       f"qui ne va pas : sans cela, la personne suivante "
                       f"reprendra au même point.",
            "geste": geste.cle})

    res = _decider(object_type, object_id,
                   AnnotationUpdate(review_status=geste.verdict,
                                    note=note_du_geste(geste, req.precision)),
                   user)
    return {"ok": True, "id": object_id, "geste": geste.cle,
            "verdict": res["review_status"], "effet": geste.effet,
            "reviewed_by": res["reviewed_by"], "reviewed_at": res["reviewed_at"]}


#: La question posée, par type. Reprise du registre des files pour que l'écran
#: et la carte d'« Aujourd'hui » ne se contredisent pas.
_QUESTION_DE_TYPE = {
    "flow":         "Ce montant est-il bien celui que l'acte a voté ?",
    "marche":       "Ce marché est-il bien celui que l'acte a attribué ?",
    "deliberation": "Cet acte est-il bien ce que son titre annonce ?",
}


# ─── /api/atelier/files ───────────────────────────────────────────────────────

@app.get("/api/atelier/files")
def atelier_files(user=Depends(require_auth)):
    """« Aujourd'hui » : les files de travail, leur reste, et ce qui est en cours.

    Le registre vit dans `collectors/files.py` — question en français, geste,
    effet sur le site, et les collecteurs qui alimentent chaque file. Cet
    endpoint ne fait que le relever.

    Toutes les files sont renvoyées, y compris celles qu'un contributeur ne peut
    pas trancher : ce qu'on ne peut pas faire doit se VOIR, pas s'apprendre par
    un 403 (lot D). `role_min` accompagne donc chaque ligne, et l'écran la
    présente sans son bouton plutôt que de la cacher.
    """
    from collectors.config import CODE_POSTAL, COMMUNE_NAME
    # Le relevé n'écrit rien ; une base d'avant le 02/10/2026 n'a pas la table
    # des propositions, et sa carte s'afficherait « indisponible ».
    conn = get_db_rw()
    try:
        _propositions.assurer_schema(conn)
        # La file « lacunes » compte des tâches : elles se mettent d'accord
        # avec les dossiers AVANT d'être comptées, sinon la carte annoncerait
        # une question qu'un éditeur vient de retirer.
        _synchroniser_taches(conn)
        _dossiers.assurer_schema(conn)
        conn.commit()
    finally:
        conn.close()
    conn = get_db()
    try:
        files = relever(conn, COMMUNE_NAME, CODE_POSTAL)
        files.insert(1, _file_dossiers_modifies(conn))
        files.insert(2, _file_refus_contredits(conn))
        return {"files": files,
                "reservation_minutes": RESERVATION_MINUTES,
                # Lot D : ce qu'un bénévole peut faire EN ARRIVANT, selon son
                # rôle. Avant le 23/09/2026, un contributeur qui se connectait
                # n'avait aucun geste qui compte — le seul qui lui était ouvert
                # écrivait dans une colonne que la publication ne lisait pas.
                "premier_jour": premier_jour(user.get("role", ""))}
    finally:
        conn.close()


def _file_dossiers_modifies(conn) -> dict:
    """La file des dossiers retenus puis modifiés (audit du 15/11/2026, A4).

    Hors du registre `collectors/files.py`, dont chaque file est une requête :
    « modifié » se lit en comparant le fichier sur le disque à l'empreinte
    retenue, ce que SQL ne sait pas faire. Même forme que les autres lignes,
    pour que l'écran la montre sans rien savoir de sa nature."""
    try:
        slugs = _dossiers_modifies(conn)
        mesure = {"reste": len(slugs), "indisponible": None}
    except Exception as e:                       # base ancienne, disque illisible
        print(f"[files] dossiers modifiés : {e!r}", file=sys.stderr)
        slugs, mesure = [], {"reste": None, "indisponible": "relevé des dossiers impossible"}
    ligne = {
        "cle": "dossiers-modifies",
        "titre": "Dossiers modifiés depuis leur verdict",
        "question": "Ce dossier retenu a changé depuis sa relecture : le nouveau "
                    "texte peut-il sortir ?",
        "geste": "Relire le texte et le retenir de nouveau, ou le laisser hors publication",
        "effet": "Un dossier retenu qui change sort du site au prochain passage, et "
                 "n'y revient que retenu de nouveau.",
        "route": "/atelier/dossiers", "role_min": "validator", "experte": False,
        "vide": "Aucun dossier retenu n'a changé depuis son verdict.",
        "fait": None, "en_cours": [], "premier": None, "derniere_passe": None,
        "sans_collecte": True, "dossiers": slugs, **mesure,
    }
    if ligne["indisponible"] is None:
        del ligne["indisponible"]
    return ligne


def _file_refus_contredits(conn) -> dict:
    """Les liens refusés qu'une source NOUVELLE redit depuis leur dernière revue
    (avis de vigie-securite) : un refus bloque toutes les sources sans bruit,
    un second validateur doit pouvoir le revoir. « Garder le refus » tient pour
    revues les sources déjà vues ; la fusion et les modifications à l'atelier
    ne comptent pas (cf. `relations_refus_respecte_maj`)."""
    existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                          " AND name='relations_refus_sources'").fetchone()
    reste = fait = sans_motif = 0
    if existe:
        reste = conn.execute("""
            SELECT COUNT(*) FROM relations_refusees x
             WHERE EXISTS (SELECT 1 FROM relations_refus_sources s
                            WHERE s.refus_id = x.id AND s.premiere > COALESCE(x.revu_le, ''))
        """).fetchone()[0]
        fait, sans_motif = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(motif IS NULL OR trim(motif) = ''), 0)"
            " FROM relations_refusees").fetchone()
    return {
        "cle": "refus-contredits",
        "titre": "Liens refusés qu'une source redit",
        "question": "Ce lien supprimé à l'atelier, une source le redit : le refus "
                    "tient-il toujours ?",
        "geste": "Garder le refus, ou le lever pour que la collecte rétablisse le lien",
        "effet": "Levé, le lien revient à la collecte suivante ; gardé, il reste hors du site.",
        "route": "/atelier/relations", "role_min": "validator", "experte": False,
        "vide": "Aucune source nouvelle n'a redit un lien refusé.",
        "reste": reste, "fait": fait, "sans_motif": sans_motif, "en_cours": [],
        "premier": None, "derniere_passe": None, "sans_collecte": True,
    }


# ─── /api/atelier/workqueue ────────────────────────────────────────────────────

# `entities.validation_status` est GELÉE depuis le 21/09/2026 : plus rien ne
# l'écrit, et la publication ne l'a jamais lue. Ses six anciens états restent
# compris en entrée par `verdict_de` (cf. `collectors/verdict.py`).


def _verdict_requis(valeur: Optional[str]) -> str:
    """Le verdict qu'exprime `valeur`, ou un 400 qui dit les valeurs admises.

    Les anciens mots (`rejected`, `verified`…) restent compris : un script ou
    un export d'avant le 21/09 n'a pas à casser. Un mot inconnu est refusé —
    on ne devine pas un verdict à la place de quelqu'un.
    """
    v = verdict_de(valeur)
    if v is None:
        raise HTTPException(400, f"verdict inconnu : « {valeur} » — valeurs : "
                                 f"{', '.join(VERDICTS)}")
    return v

# Ce qu'un contributeur a écrit sur une fiche depuis le dernier VERDICT posé
# par un validateur (arbitrage du 15/11/2026 : le validateur le voit avant de
# retenir). La borne est l'identifiant de journal de la dernière décision qui a
# CHANGÉ le verdict : une simple note ne la déplace pas. « Contributeur » =
# rôle actuel du compte, pas celui qu'il avait en écrivant.
_ECRITS_DE_CONTRIBUTEUR = (
    "u.role = 'contributor' AND al.action IN ('create', 'update', 'delete')"
    " AND al.table_name IN ('entities', 'contacts', 'relations', 'entity_notes',"
    " 'entity_websites')")
_DERNIER_VERDICT = """(SELECT MAX(v.id) FROM audit_log v JOIN users vu ON vu.id = v.user_id
    WHERE v.table_name = 'annotations' AND v.field = 'entity/' || {eid}
      AND vu.role IN ('validator', 'admin')
      AND COALESCE(json_extract(v.new_value, '$.statut'), 'jamais_relu')
          != COALESCE(json_extract(v.old_value, '$.statut'), 'jamais_relu'))"""


@app.get("/api/atelier/workqueue")
def atelier_workqueue(
    status: str = Query(JAMAIS_RELU),
    type: Optional[str] = None,
    perimetre: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    user=Depends(require_auth),
):
    verdict = _verdict_requis(status)
    # Les décisions sur une fiche n'existent que depuis le 21/09 : elles sont
    # toutes écrites dans le vocabulaire neuf, l'égalité suffit.
    filters = ["COALESCE(d.review_status, 'jamais_relu') = ?"]
    params: list = [verdict]
    if type:
        filters.append("e.type = ?")
        params.append(type)
    # Sans ce filtre, la file de travail noie les entités de la commune sous
    # les 6 500 de l'intercommunalité : on ne valide pas une fiche C2 avec le
    # même soin ni dans le même but qu'une fiche C1.
    if perimetre:
        demandes = [p.strip() for p in perimetre.split(",") if p.strip() in PERIMETRES]
        if not demandes:
            raise HTTPException(400, f"perimetre invalide — valeurs: {', '.join(PERIMETRES)}")
        filters.append(f"e.perimetre IN ({','.join('?' for _ in demandes)})")
        params += demandes
    where = "WHERE " + " AND ".join(filters)

    conn = get_db()
    try:
        jointure = ("LEFT JOIN annotations d ON d.object_type = 'entity' "
                    "AND d.object_id = e.id")
        total = row(conn, f"SELECT COUNT(*) AS n FROM entities e {jointure} {where}",
                    params)["n"]
        items = rows(conn, f"""
            SELECT
                e.id, e.type, e.name, e.short_name, e.address,
                e.confidence, e.responsible, e.created_at,
                e.commune, e.perimetre,
                COALESCE(d.review_status, 'jamais_relu') AS verdict,
                d.reviewed_by AS verdict_par, d.reviewed_at AS verdict_le,
                p.firstname, p.lastname,
                b.siren, b.naf_label, b.status AS biz_status,
                a.rna_id, a.object AS asso_object,
                (SELECT COUNT(*) FROM relations r WHERE (r.from_id=e.id OR r.to_id=e.id)
                  {"" if au_moins(user, "validator") else _SANS_PRESUMES}) AS rel_count,
                (SELECT COUNT(*) FROM audit_log WHERE entity_id=e.id) AS edit_count,
                (SELECT COUNT(*) FROM audit_log al JOIN users u ON u.id = al.user_id
                  WHERE al.entity_id = e.id AND {_ECRITS_DE_CONTRIBUTEUR}
                    AND al.id > COALESCE({_DERNIER_VERDICT.format(eid="e.id")}, 0)
                ) AS ecrit_par_contributeurs,
                (SELECT COUNT(*) FROM contacts WHERE entity_id=e.id) AS contacts_count,
                (SELECT value FROM contacts WHERE entity_id=e.id AND type='website' LIMIT 1) AS website
            FROM entities e
            LEFT JOIN persons      p ON p.entity_id  = e.id
            LEFT JOIN businesses   b ON b.entity_id  = e.id
            LEFT JOIN associations a ON a.entity_id  = e.id
            {jointure}
            {where}
            ORDER BY e.type, e.name
            LIMIT ? OFFSET ?
        """, params + [limit, offset])
        # Contacts d'une personne physique réservés aux validateurs : la liste
        # ne porte ni le site ni le nombre de contacts d'une personne.
        for it in items:
            if _notes_reservees(it, user):
                it["website"] = None
                it["contacts_count"] = None
        return {"total": total, "items": items, "offset": offset, "limit": limit}
    finally:
        conn.close()

# ─── Journal ─────────────────────────────────────────────────────────────────
# Qui a fait quoi, toutes fiches confondues. L'historique n'existait que fiche
# par fiche : pour savoir ce qu'un éditeur avait touché dans la journée, il
# fallait ouvrir les fiches une à une — donc savoir d'avance lesquelles.

#: Ce que désigne chaque table du journal, pour qui ne lit pas le schéma.
LIBELLE_TABLE_JOURNAL = {
    "entities": "fiche", "annotations": "décision", "saisies": "saisie",
    "relations": "relation", "entity_websites": "site", "relation_candidates":
    "relation candidate", "users": "compte", "publication": "publication",
    "propositions": "proposition", "reglages": "réglage",
}


@app.get("/api/atelier/journal")
def atelier_journal(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    qui: Optional[str] = Query(None, description="adresse de l'auteur"),
    quoi: Optional[str] = Query(None, description="table : entities, annotations…"),
    # Le journal nomme qui a écrit quoi : validateur et au-dessus (Julien,
    # 07/10/2026). L'historique d'UNE fiche reste lisible de qui l'édite.
    user=Depends(require_au_moins("validator", "Lire le journal")),
):
    filtres, params = [], []
    # Qui a été invité, quel rôle a changé, quel compte est désactivé : cela ne
    # sert à aucun geste d'édition, et nomme des personnes. Admin seul (Julien,
    # 02/10/2026) — la page Comptes, elle, l'a toujours été.
    comptes_visibles = au_moins(user, "admin")
    if not comptes_visibles:
        filtres.append("a.table_name <> 'users'")
    if qui:
        filtres.append("u.email = ?"); params.append(qui.strip().lower())
    if quoi:
        filtres.append("a.table_name = ?"); params.append(quoi)
    where = ("WHERE " + " AND ".join(filtres)) if filtres else ""
    conn = get_db()
    try:
        total = row(conn, f"SELECT COUNT(*) AS n FROM audit_log a "
                          f"LEFT JOIN users u ON u.id = a.user_id {where}", params)["n"]
        lignes = rows(conn, f"""
            SELECT a.id, a.at, u.email AS par, u.role, a.table_name AS quoi, a.action,
                   a.entity_id, e.name AS fiche, a.field AS champ,
                   substr(a.old_value, 1, 300) AS avant, substr(a.new_value, 1, 300) AS apres
            FROM audit_log a
            LEFT JOIN users u    ON u.id = a.user_id
            LEFT JOIN entities e ON e.id = a.entity_id
            {where}
            ORDER BY a.id DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])
        auteurs = [r["email"] for r in rows(conn, """
            SELECT DISTINCT u.email FROM audit_log a JOIN users u ON u.id = a.user_id
            ORDER BY u.email""")]
    finally:
        conn.close()
    for ligne in lignes:
        ligne["quoi_libelle"] = LIBELLE_TABLE_JOURNAL.get(ligne["quoi"], ligne["quoi"])
    return {"total": total, "lignes": lignes, "auteurs": auteurs,
            "tables": {t: nom for t, nom in LIBELLE_TABLE_JOURNAL.items()
                       if comptes_visibles or t != "users"}}


# ─── Conflits d'édition ───────────────────────────────────────────────────────
# Deux éditeurs sur la même ligne : le second à écrire doit l'apprendre, et
# apprendre de qui il s'agit. Mesuré le 17/09/2026 sur une copie de l'atelier :
# le statut d'une fiche et l'annotation d'une délibération passaient au dernier
# arrivé sans un mot, la note du premier comprise.

def _conflit(message: str, par: Optional[str], le: Optional[str],
             **detail) -> HTTPException:
    """Un 409 qui dit QUI a écrit, QUAND, et ce qu'il y a maintenant.

    Le 409 des fiches disait « updated_at attendu='…', actuel='…' » : exact pour
    un script, illisible pour qui édite, et sans ce qu'il faut pour décider.
    `le` est un horodatage SQLite (UTC, sans fuseau) : l'interface le rend en
    heure locale.
    """
    return HTTPException(409, {"message": message, "par": par, "le": le, **detail})


def _dernier_auteur(conn, entity_id: int,
                    champ: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """(email, horodatage) de la dernière écriture journalisée sur une fiche."""
    sql = ("SELECT u.email, a.at FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
           "WHERE a.entity_id = ?" + (" AND a.field = ?" if champ else "")
           + " ORDER BY a.id DESC LIMIT 1")
    r = conn.execute(sql, (entity_id, champ) if champ else (entity_id,)).fetchone()
    return (r[0], r[1]) if r else (None, None)


# ─── PATCH /api/atelier/entities/{id}/status ──────────────────────────────────
# Le ✓ ✗ de la file de travail. Jusqu'au 21/09/2026 il écrivait dans
# `entities.validation_status`, que la publication ne lisait pas : trancher une
# fiche ne changeait rien au site. Il pose désormais une DÉCISION, par le même
# chemin que toutes les autres (`_decider`) — pas un second écrivain.

class ValidationStatusUpdate(BaseModel):
    verdict: Optional[str] = None
    # L'ancien nom du champ, lu par `verdict_de` : un script d'avant le 21/09
    # continue de fonctionner, et `verified` y veut dire `retenu`.
    validation_status: Optional[str] = None
    note: Optional[str] = None
    # Le verdict que l'éditeur avait sous les yeux. Fourni, il devient une
    # condition : si la fiche a été tranchée entre-temps → 409. Absent, on
    # écrit sans condition — les scripts n'ont rien sous les yeux.
    statut_lu: Optional[str] = None

@app.patch("/api/atelier/entities/{entity_id}/status")
def atelier_update_status(
    entity_id: int = FPath(..., ge=1),
    req: ValidationStatusUpdate = ...,
    user=Depends(require_au_moins("validator", "Trancher le statut d'une fiche")),
):
    demande = req.verdict if req.verdict is not None else req.validation_status
    if demande is None:
        raise HTTPException(400, "verdict manquant — valeurs : " + ", ".join(VERDICTS))
    fournis = req.model_fields_set if hasattr(req, "model_fields_set") else req.__fields_set__
    decision = AnnotationUpdate(review_status=_verdict_requis(demande),
                                **({"note": req.note} if "note" in fournis else {}))
    res = _decider("entity", entity_id, decision, user, statut_lu=req.statut_lu)
    return {"ok": True, "id": entity_id, "verdict": res["review_status"],
            "reviewed_by": res["reviewed_by"], "reviewed_at": res["reviewed_at"]}

# ─── Données importées (délibs / flux / marchés) — revue & annotation ─────────
# Les collecteurs sont INSERT-only ; l'annotation vit dans une table à part
# (annotations) pour ne jamais écraser les données sources. cf. CARTE_PRODUIT §4.

DONNEES_TYPES = ("deliberation", "flow", "marche")

#: Les objets qui portent un verdict sans passer par `/atelier/donnees` : la
#: fiche et la relation. L'annotation n'a pas de clé étrangère — on vérifie ici
#: qu'on ne décide pas d'un objet qui n'existe pas.
TABLE_DE_DECISION = {"entity": "entities", "relation": "relations",
                     # La séance dont une feuille « en clair » rend compte.
                     "en_clair": "events",
                     # Un dossier thématique (`collectors/dossiers.py`).
                     "dossier": "dossiers"}

#: Table portant chaque type d'objet annotable. Sert à lire son `origine` avant
#: d'autoriser une rectification — cf. `_verifier_origine_modifiable`.
TABLE_DE_TYPE = {
    "deliberation": "events",
    "flow":         "financial_flows",
    "marche":       "marches_publics",
}


def _verifier_origine_modifiable(conn, object_type: str, object_id: int) -> str | None:
    """Refuse de rectifier une VALEUR sur une ligne institutionnelle.

    Règle posée par Julien le 20/08/2026 : « ne pas corrompre tout ce qui est
    établi comme venant de collecteurs institutionnels ; cela ne concerne que ce
    qui a été extrait de verbatims ou la collecte de sites ».

    La nuance qui compte : ÉCARTER une ligne de la publication reste permis quelle
    que soit son origine — refuser de publier un chiffre qu'on juge faux n'exige
    pas d'en connaître un meilleur. Ce qui est interdit, c'est de substituer sa
    lecture à celle d'une administration sur une donnée qu'elle a elle-même
    structurée. Cette fonction ne garde donc que les corrections de valeurs.

    Une ligne encore non classée est traitée comme institutionnelle : devant
    l'inconnu, ne rien casser. Le message dit quoi faire pour en sortir.
    """
    table = TABLE_DE_TYPE.get(object_type)
    if not table:
        return None
    ligne = row(conn, f"SELECT origine FROM {table} WHERE id=?", (object_id,))
    if not ligne:
        raise HTTPException(404, f"{object_type} {object_id} introuvable.")
    origine = ligne["origine"]
    if modifiable(origine):
        return origine
    if origine == INSTITUTIONNEL:
        raise HTTPException(
            409, "Cette ligne vient d'un collecteur institutionnel : sa valeur "
                 "est celle que publie l'administration source, et l'atelier ne "
                 "la réécrit pas. Vous pouvez en revanche l'écarter de la "
                 "publication (statut « rejeté »), ou la commenter.")
    raise HTTPException(
        409, "Origine de cette ligne inconnue : par précaution, sa valeur n'est "
             "pas modifiable. Lancer `scripts/classer_origine.py`, qui indiquera "
             "la source non reconnue à déclarer dans collectors/origine.py.")

# ─── Corrections : rectifier sans écraser ────────────────────────────────────
# Rejeter une donnée fausse la fait disparaître ; le plus souvent ce qu'il faut
# c'est la rectifier — un statut de cession, un montant OCR aberrant, une date.
# La correction est stockée à part (annotations.corrections) et n'est appliquée
# qu'à la publication : la ligne du collecteur reste le reflet de la source.
#
# Liste blanche par type : on ne corrige QUE ce que la page publique affiche.
# Ouvrir tous les champs, ce serait rouvrir la porte à l'écrasement par l'API.
CHAMPS_CORRIGEABLES = {
    "deliberation": {"date": "date", "title": "texte", "source_url": "url",
                     "montant": "montant"},
    "flow": {"year": "annee", "amount": "montant", "description": "texte",
             "statut": "statut", "type_norm": "texte_court"},
    "marche": {"date_notif": "date", "montant": "montant", "objet": "texte",
               "titulaire_nom": "texte_court", "acheteur_nom": "texte_court"},
}
STATUTS_FLUX = ("demande", "engage", "realise")


def _valide_correction(object_type: str, champ: str, valeur):
    """Une correction mal typée en base est pire que la donnée fausse d'origine.

    Renvoie la valeur normalisée, ou lève une HTTPException. `None` est admis
    partout : il signifie « annuler cette correction ».
    """
    genre = CHAMPS_CORRIGEABLES.get(object_type, {}).get(champ)
    if genre is None:
        raise HTTPException(400, f"champ non corrigeable pour {object_type} : {champ}")
    return _normaliser(genre, champ, valeur)


def _normaliser(genre: str, champ: str, valeur):
    """Normalise une valeur d'après son genre, ou refuse.

    Partagé par les corrections et par la saisie manuelle : les deux écrivent
    dans les mêmes colonnes, et deux validateurs pour un même champ finiraient
    par admettre des choses différentes.
    """
    if valeur is None or valeur == "":
        return None
    if genre.startswith("choix:"):
        admis = genre.split(":", 1)[1].split(",")
        s = str(valeur).strip()
        if s not in admis:
            raise HTTPException(400, f"{champ} : valeurs admises — {', '.join(admis)}")
        return s
    if genre == "entite":
        # Soit une entité déjà choisie dans la recherche, soit un nom et un type
        # à créer. Jamais un nom seul : deviner le type d'après l'orthographe a
        # produit des doublons dans cette base, une même structure existant à la
        # fois en entreprise et en association.
        if not isinstance(valeur, dict):
            raise HTTPException(400, f"{champ} : entité attendue (id, ou nom + type)")
        if valeur.get("id"):
            return {"id": int(valeur["id"])}
        if not str(valeur.get("nom") or "").strip() or not valeur.get("type"):
            raise HTTPException(400, f"{champ} : choisir une entité existante, "
                                     "ou donner un nom ET un type")
        return {"nom": " ".join(str(valeur["nom"]).split())[:255],
                "type": str(valeur["type"]).strip(),
                "commune": (valeur.get("commune") or "").strip() or None}
    if genre == "date":
        s = str(valeur).strip()
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"{champ} : date attendue au format AAAA-MM-JJ")
        return s
    if genre == "annee":
        try:
            n = int(valeur)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{champ} : année attendue")
        if not 1900 <= n <= 2100:
            raise HTTPException(400, f"{champ} : année hors plage (1900-2100)")
        return n
    if genre == "montant":
        try:
            v = float(str(valeur).replace(",", ".").replace(" ", "").replace(" ", ""))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{champ} : montant attendu")
        if v < 0:
            raise HTTPException(400, f"{champ} : montant négatif")
        return round(v, 2)
    if genre == "url":
        s = str(valeur).strip()
        if not s.startswith(("http://", "https://")):
            raise HTTPException(400, f"{champ} : URL http(s) attendue")
        return s[:1000]
    if genre == "statut":
        s = str(valeur).strip()
        if s not in STATUTS_FLUX:
            raise HTTPException(400, f"{champ} : valeurs admises — {', '.join(STATUTS_FLUX)}")
        return s
    if genre == "texte_court":
        return " ".join(str(valeur).split())[:255]
    return " ".join(str(valeur).split())[:4000]


def _filtre_origine(alias: str, origine: Optional[str], params: list) -> str:
    """Clause SQL de filtrage par origine, et son paramètre.

    « non-classé » est une valeur de filtre à part entière : c'est la liste que
    quelqu'un doit regarder pour déclarer les sources manquantes, et sans elle
    ces lignes restent invisibles — donc jamais corrigées, puisqu'une origine
    inconnue interdit la rectification.
    """
    if not origine:
        return ""
    if origine == "non-classe":
        return f" AND {alias}.origine IS NULL"
    params.append(origine)
    return f" AND {alias}.origine = ?"


def _donnees_query(object_type: str, limit: int, origine: Optional[str] = None,
                   offset: int = 0):
    params: list = []
    if object_type == "deliberation":
        return ("""
            SELECT e.id, e.type, e.date, e.title,
                   substr(e.content, 1, 400) AS excerpt,
                   e.source, e.source_url, e.origine, e.saisi_par,
                   -- Le document local qui porte cette ligne, s'il existe : le
                   -- lien direct d'abord, la comparaison d'URL en repli pour les
                   -- 4 839 actes archivés avant que la colonne n'existe.
                   COALESCE(e.raw_document_id,
                            (SELECT r.id FROM raw_documents r
                              WHERE r.url = e.source_url LIMIT 1)) AS document_id,
                   -- Montant tel que la page publique le calcule : le plus élevé
                   -- des montants cités. C'est celui qu'on corrige.
                   -- Les parsers successifs ont écrit `montant` ou `value` :
                   -- les deux clés sont acceptées, comme à la publication.
                   (SELECT MAX(CAST(COALESCE(json_extract(m.value, '$.montant'),
                                             json_extract(m.value, '$.value')) AS REAL))
                      FROM json_each(json_extract(e.metadata, '$.montants')) m
                     WHERE json_valid(e.metadata)) AS montant
            FROM events e
            WHERE e.type IN ({types})
            {filtre}
            ORDER BY e.date DESC
            LIMIT ? OFFSET ?
        """.replace("{types}", ",".join("?" * len(TYPES_REVUS["deliberation"])))
           .replace("{filtre}", _filtre_origine("e", origine, params)),
                [*TYPES_REVUS["deliberation"], *params, limit, offset])
    if object_type == "flow":
        return ("""
            SELECT ff.id, ff.type, ff.year, ff.amount, ff.description,
                   ff.source, ff.confidence, ff.origine, ff.saisi_par,
                   ff.raw_document_id AS document_id,
                   ef.name AS from_name, et.name AS to_name
            FROM financial_flows ff
            LEFT JOIN entities ef ON ef.id = ff.from_id
            LEFT JOIN entities et ON et.id = ff.to_id
            WHERE 1=1 {filtre}
            ORDER BY ff.year DESC, ff.amount DESC
            LIMIT ? OFFSET ?
        """.replace("{filtre}", _filtre_origine("ff", origine, params)), params + [limit, offset])
    # marche
    return ("""
        SELECT mp.id, mp.acheteur_nom, mp.titulaire_nom, mp.objet,
               mp.montant, mp.procedure, mp.date_notif, mp.source, mp.source_url,
               mp.origine, mp.saisi_par, mp.raw_document_id AS document_id
        FROM marches_publics mp
        WHERE 1=1 {filtre}
        ORDER BY mp.date_notif DESC
        LIMIT ? OFFSET ?
    """.replace("{filtre}", _filtre_origine("mp", origine, params)), params + [limit, offset])


@app.get("/api/atelier/donnees")
def atelier_donnees(
    type: str = Query(..., description="deliberation | flow | marche"),
    status: Optional[str] = None,
    origine: Optional[str] = Query(None, description="institutionnel | verbatim | "
                                                     "atelier | non-classe"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    response: Response = None,
    user=Depends(require_auth),
):
    """Une page de lignes ; le total du filtre est dans l'en-tête `X-Total-Count`
    (le corps reste une liste, d'autres lecteurs s'y fient)."""
    if type not in DONNEES_TYPES:
        raise HTTPException(400, f"type invalide — valeurs: {', '.join(DONNEES_TYPES)}")
    filtre_verdict = _verdict_requis(status) if status else None
    if origine and origine not in ORIGINES + ("non-classe",):
        raise HTTPException(400, f"origine invalide — valeurs: "
                                 f"{', '.join(ORIGINES + ('non-classe',))}")

    # Sans filtre de verdict, la page se découpe en SQL. Avec, le verdict vit
    # dans une autre table : on lit tout, on filtre, puis on découpe.
    sql, params = _donnees_query(type, -1 if filtre_verdict else limit, origine,
                                 0 if filtre_verdict else offset)
    conn = get_db()
    try:
        items = rows(conn, sql, params)
        if not filtre_verdict:
            sans_page, p_total = _donnees_query(type, -1, origine, 0)
            response.headers["X-Total-Count"] = str(conn.execute(
                f"SELECT COUNT(*) FROM ({sans_page})", p_total).fetchone()[0])
        ann = {
            a["object_id"]: a for a in rows(conn,
                "SELECT object_id, review_status, confidence, note, corrections, "
                "reviewed_by, reviewed_at FROM annotations WHERE object_type=?", (type,))
        }
        out = []
        for it in items:
            a = ann.get(it["id"])
            if a:
                a = dict(a)
                a["corrections"] = parse_json_field(a.get("corrections"), {}) or {}
                # Une base non migrée garde `rejected`, `validated` : l'écran ne
                # reçoit que le vocabulaire neuf.
                a["review_status"] = verdict_de(a["review_status"]) or JAMAIS_RELU
            it["annotation"] = a or {"review_status": JAMAIS_RELU, "confidence": None,
                                     "note": "", "corrections": {},
                                     "reviewed_by": None, "reviewed_at": None}
            if filtre_verdict and it["annotation"]["review_status"] != filtre_verdict:
                continue
            out.append(it)
        if filtre_verdict:
            response.headers["X-Total-Count"] = str(len(out))
            out = out[offset:offset + limit]
        return out
    finally:
        conn.close()


@app.get("/api/atelier/champs-corrigeables")
def atelier_champs_corrigeables(user=Depends(require_auth)):
    """Contrat des corrections : l'atelier construit ses champs là-dessus.

    Le front ne devine pas ce qui est éditable — sinon il propose un champ que
    l'API refusera, ou il en oublie un qu'elle accepterait.
    """
    return {"champs": CHAMPS_CORRIGEABLES, "statuts_flux": list(STATUTS_FLUX)}


class AnnotationUpdate(BaseModel):
    review_status: Optional[str] = None
    confidence: Optional[str] = None
    note: Optional[str] = None
    # {champ: valeur} — valeur nulle ou vide = correction annulée.
    corrections: Optional[dict] = None
    # `reviewed_at` tel que l'éditeur l'a lu (null : aucune annotation encore).
    # FOURNI, il devient une condition — quelqu'un a annoté entre-temps → 409.
    lu_le: Optional[str] = None
    # Objets à retenir : l'empreinte du texte que le validateur a sous les yeux.
    # FOURNIE, elle devient une condition — le texte a changé entre-temps → 409 :
    # on ne retient pas une version qu'on n'a pas lue.
    empreinte_vue: Optional[str] = None


@app.patch("/api/atelier/annotations/{object_type}/{object_id}")
def atelier_annotate(
    object_type: str = FPath(...),
    object_id: int = FPath(..., ge=1),
    req: AnnotationUpdate = ...,
    user=Depends(require_auth),
):
    return _decider(object_type, object_id, req, user)


def _exiger_releve_conforme(seance: int) -> None:
    """Retenir une feuille dont le vérificateur trouve une faute : refusé."""
    from collectors.en_clair.seances import releves, seance_id
    from collectors.en_clair.verifier import verifier
    conn = get_db()
    try:
        trouves = [(p, r) for p, r in releves(RACINE) if seance_id(conn, r) == seance]
    finally:
        conn.close()
    if not trouves:
        raise HTTPException(404, f"Aucun relevé ne décrit la séance {seance}.")
    for p, _ in trouves:
        if fautes := verifier(p):
            raise HTTPException(409, "Relevé non conforme, il ne peut pas être retenu : "
                                     + " ; ".join(fautes[:3]))


def _empreinte_objet(object_type: str, object_id: int) -> Optional[str]:
    """L'empreinte du texte qu'un verdict sur un objet à retenir porte : le ou
    les relevés d'une séance, ou le markdown d'un dossier. None si introuvable."""
    from collectors.verdict import empreinte
    conn = get_db()
    try:
        if object_type == "en_clair":
            from collectors.en_clair.seances import releves, seance_id
            chemins = sorted(p for p, r in releves(RACINE) if seance_id(conn, r) == object_id)
            return empreinte(b"".join(c.read_bytes() for c in chemins)) if chemins else None
        if object_type == "dossier":
            slug = _dossiers.slug_de(conn, object_id)
            return _dossiers.empreinte_de(_dossiers.chemin(RACINE, slug)) if slug else None
    finally:
        conn.close()
    return None


def _note_de_decision_reservee(conn, object_type: str, object_id: int, user: dict) -> bool:
    """Vraie si la note d'une décision porte sur une personne et que le compte
    n'est pas validateur : même règle que les notes (`_notes_reservees`)."""
    if object_type != "entity":
        return False
    return _notes_reservees(row(conn, "SELECT type FROM entities WHERE id=?",
                                (object_id,)), user)


def _decider(object_type: str, object_id: int, req: AnnotationUpdate, user,
             statut_lu: Optional[str] = None) -> dict:
    """Le SEUL écrivain des décisions de l'atelier — cf. `collectors/verdict.py`.

    Cinq types d'objets : les trois lignes importées (délibération, flux,
    marché) et, depuis le 21/09/2026, la fiche et la relation. Sur ces deux-là
    une décision porte un verdict et une note, rien d'autre : la fiabilité et les
    valeurs d'une fiche se modifient sur la fiche, avec son propre verrou et son
    propre historique. Deux chemins pour écrire la même chose, c'est deux
    vérités.
    """
    if object_type not in OBJETS + OBJETS_A_RETENIR:
        raise HTTPException(400, "object_type invalide — valeurs: "
                                 f"{', '.join(OBJETS + OBJETS_A_RETENIR)}")
    if object_type == "en_clair" and req.review_status and \
            _verdict_requis(req.review_status) == RETENU:
        _exiger_releve_conforme(object_id)
    a_retenir = object_type in OBJETS_A_RETENIR
    if a_retenir:
        conn = get_db_rw()
        try:
            _dossiers.assurer_schema(conn)
            conn.commit()
        finally:
            conn.close()
    actuelle = _empreinte_objet(object_type, object_id) if a_retenir else None
    if a_retenir and req.empreinte_vue and req.empreinte_vue != actuelle:
        raise HTTPException(409, "Le texte a changé depuis que vous l'avez ouvert : "
                                 "rechargez-le et relisez-le avant de décider.")
    demande = _verdict_requis(req.review_status) if req.review_status else None
    fournis = req.model_fields_set if hasattr(req, "model_fields_set") else req.__fields_set__
    table_fiche = TABLE_DE_DECISION.get(object_type)
    if table_fiche and ((req.confidence not in (None, "") and "confidence" in fournis)
                        or req.corrections):
        raise HTTPException(
            400, "Une décision sur une fiche ou une relation porte un verdict et "
                 "une note. La fiabilité et les valeurs se modifient sur la fiche.")

    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if table_fiche and not row(conn, f"SELECT 1 AS ok FROM {table_fiche} WHERE id=?",
                                   (object_id,)):
            raise HTTPException(404, f"{object_type} {object_id} introuvable.")
        existing = row(conn,
            "SELECT review_status, confidence, note, corrections, reviewed_by, "
            "reviewed_at FROM annotations "
            "WHERE object_type=? AND object_id=?", (object_type, object_id))
        # Lu dans le vocabulaire neuf, quelle que soit la façon dont il a été
        # écrit : une base non migrée garde `pending` ou `rejected`.
        statut_avant = ((verdict_de(existing["review_status"]) if existing else None)
                        or JAMAIS_RELU)
        if statut_lu is not None and _verdict_requis(statut_lu) != statut_avant:
            raise _conflit("Le verdict de cette fiche a changé pendant que vous la "
                           "regardiez.",
                           existing["reviewed_by"] if existing else None,
                           existing["reviewed_at"] if existing else None,
                           actuel=statut_avant)
        if "lu_le" in fournis:
            actuel_le = existing["reviewed_at"] if existing else None
            if (req.lu_le or None) != actuel_le:
                raise _conflit(
                    "Quelqu'un a annoté cette ligne pendant que vous la regardiez.",
                    existing["reviewed_by"] if existing else None, actuel_le,
                    actuel={"review_status": statut_avant,
                            "confidence": existing["confidence"],
                            # Sur une personne, la note de décision est un texte
                            # libre réservé aux validateurs (avis vigie-dpo).
                            "note": None if _note_de_decision_reservee(
                                conn, object_type, object_id, user) else existing["note"],
                            "corrections": ({} if _note_de_decision_reservee(
                                conn, object_type, object_id, user)
                                else parse_json_field(existing["corrections"], {})),
                            "reviewed_at": actuel_le} if existing else None)
        new_status = demande or statut_avant

        # Sémantique PATCH : un champ ABSENT du corps n'est pas touché. Sans
        # ça, corriger un montant effaçait la note qui l'explique — et la note
        # est justement ce qui rend la correction défendable.
        confidence = (req.confidence if "confidence" in fournis
                      else (existing["confidence"] if existing else None))
        note = (req.note if "note" in fournis
                else (existing["note"] if existing else None))
        # Sur une personne, la note d'une décision est un texte libre réservé
        # aux validateurs, ici et non dans les routes : tout appelant en hérite.
        # La note existante ne lui est pas rendue (None) : un corps renvoyé sans
        # note, ou avec une note vide, ne la change pas et ne l'efface pas.
        if _note_de_decision_reservee(conn, object_type, object_id, user):
            ancienne = (existing["note"] if existing else None) or None
            if (note or None) is None or note == ancienne:
                note = ancienne
            else:
                exiger(user, "validator", "Écrire la note d'une décision sur une personne physique")

        # Retenir, écarter (ce qui retire l'objet du site) ou poser une fiabilité
        # tranche ; noter et corriger proposent. Comparé à l'EXISTANT : un
        # contributeur qui renvoie le verdict affiché sans le changer ne tranche rien.
        conf_avant = existing["confidence"] if existing else None
        if new_status != statut_avant:
            exiger(user, "validator", "Retenir ou écarter")
        # La note d'une décision est le MOTIF d'une rectification, et part sur
        # le site avec elle (`scripts/snapshot/revue.py`, `note_revue`). Un
        # contributeur ne la réécrit pas sous la décision d'un autre compte : il
        # remplaçait le motif et devenait l'auteur du verdict (audit du 15/11).
        decision_d_autrui = bool(
            existing and existing["reviewed_by"] and not au_moins(user, "validator")
            and existing["reviewed_by"] != user["email"])
        if decision_d_autrui and (note or None) != (existing["note"] or None):
            raise HTTPException(403, "Cette ligne porte la décision d'un autre compte, "
                                     "et sa note en est le motif publié : un contributeur "
                                     "ne la réécrit pas. Proposez une correction de la "
                                     "valeur, ou demandez à un validateur.")
        if (confidence or None) != (conf_avant or None):
            exiger(user, "validator", "Poser la fiabilité d'une ligne")

        # Les corrections sont FUSIONNÉES avec l'existant, pas remplacées : la
        # page d'atelier peut n'envoyer que le champ qu'elle vient d'éditer.
        # Une valeur vide retire la correction et rend la donnée d'origine.
        anciennes = parse_json_field(existing["corrections"], {}) if existing else {}
        corrections = dict(anciennes) if isinstance(anciennes, dict) else {}
        proposees: dict = {}
        if req.corrections and not au_moins(user, "validator"):
            # Une correction est appliquée à la publication : venant d'un
            # contributeur, elle changeait le site sans que personne la relise.
            # Elle devient une proposition ; la note, elle, s'écrit toujours.
            if any(v not in (None, "") for v in req.corrections.values()):
                _verifier_origine_modifiable(conn, object_type, object_id)
            proposees = {champ: _valide_correction(object_type, champ, valeur)
                         for champ, valeur in req.corrections.items()}
            proposees = {c: v for c, v in proposees.items() if corrections.get(c) != v}
            if proposees:
                _propositions.assurer_schema(conn)
                proposition = _propositions.proposer(
                    conn, "correction", object_type, object_id, None, proposees,
                    {c: corrections.get(c) for c in proposees}, user)
            if not (fournis & {"note", "review_status", "confidence"}):
                conn.commit()
                return {"ok": True, "object_type": object_type, "object_id": object_id,
                        "review_status": statut_avant, "corrections": corrections,
                        "propose": bool(proposees),
                        "proposition": proposition if proposees else None,
                        "reviewed_by": existing["reviewed_by"] if existing else None,
                        "reviewed_at": existing["reviewed_at"] if existing else None}
        elif req.corrections is not None:
            # Une correction de VALEUR exige une ligne dont l'origine l'autorise.
            # Le test porte sur les corrections non vides seulement : annuler une
            # correction (valeur vide) doit rester possible même si la ligne a
            # changé d'origine entre-temps, sinon une correction posée par erreur
            # sur de l'institutionnel deviendrait indélogeable.
            if any(v not in (None, "") for v in req.corrections.values()):
                _verifier_origine_modifiable(conn, object_type, object_id)
            for champ, valeur in req.corrections.items():
                propre = _valide_correction(object_type, champ, valeur)
                if propre is None:
                    corrections.pop(champ, None)
                else:
                    corrections[champ] = propre

        # Décision d'un autre compte, contributeur : rien d'autre à écrire que
        # des propositions (déjà déposées). Réécrire la ligne ferait du
        # contributeur l'auteur du verdict (audit du 15/11/2026).
        if decision_d_autrui:
            conn.commit()
            return {"ok": True, "object_type": object_type, "object_id": object_id,
                    "review_status": statut_avant, "corrections": corrections,
                    "propose": bool(proposees),
                    "proposition": proposition if proposees else None,
                    "reviewed_by": existing["reviewed_by"],
                    "reviewed_at": existing["reviewed_at"]}
        conn.execute("""
            INSERT INTO annotations(object_type, object_id, review_status, confidence,
                                    note, corrections, reviewed_by, reviewed_at, updated_at)
            VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(object_type, object_id) DO UPDATE SET
                review_status = excluded.review_status,
                confidence    = excluded.confidence,
                note          = excluded.note,
                corrections   = excluded.corrections,
                reviewed_by   = excluded.reviewed_by,
                reviewed_at   = datetime('now'),
                updated_at    = datetime('now')
        """, (object_type, object_id, new_status, confidence, note,
              json.dumps(corrections, ensure_ascii=False) if corrections else None,
              user["email"]))
        # Retenir signe un texte précis : son empreinte. Tout autre verdict
        # l'efface ; une simple note la laisse — elle ne relit rien.
        if a_retenir and demande:
            if demande == RETENU and actuelle is None:
                raise HTTPException(404, "Le texte de cet objet est introuvable.")
            conn.execute("UPDATE annotations SET empreinte=? WHERE object_type=? AND object_id=?",
                         (actuelle if demande == RETENU else None, object_type, object_id))
            if object_type == "dossier":
                _sceller_dossier(conn, object_id, actuelle if demande == RETENU else None)
        conn.execute("""
            INSERT INTO audit_log(user_id, entity_id, table_name, action, field, old_value, new_value)
            VALUES(?,?,?,?,?,?,?)
        """, (user["id"],
              # Une décision sur une fiche entre dans l'historique DE la fiche.
              object_id if object_type == "entity" else None,
              "annotations", "annotate", f"{object_type}/{object_id}",
              # La note et la fiabilité y entrent : une note écrasée par un
              # autre éditeur ne laissait jusqu'ici aucune trace de ce qu'elle
              # disait.
              json.dumps({"statut": statut_avant if existing else None,
                          "confidence": existing["confidence"] if existing else None,
                          "note": existing["note"] if existing else None,
                          "corrections": anciennes}, ensure_ascii=False),
              json.dumps({"statut": new_status, "confidence": confidence,
                          "note": note, "corrections": corrections},
                         ensure_ascii=False)))
        conn.commit()
        apres = row(conn, "SELECT reviewed_at FROM annotations "
                          "WHERE object_type=? AND object_id=?", (object_type, object_id))
        return {"ok": True, "object_type": object_type, "object_id": object_id,
                "review_status": new_status, "corrections": corrections,
                "propose": bool(proposees),
                "reviewed_by": user["email"],
                "reviewed_at": apres["reviewed_at"] if apres else None}
    finally:
        conn.close()

# ─── Le conseil en clair ─────────────────────────────────────────────────────
#
# Les feuilles « avant / après / documents » d'une séance, relevées hors de la
# chaîne automatique (`collectors/en_clair`). L'atelier les montre, un
# validateur les RETIENT ou les écarte (`_decider`, type `en_clair`, identifiant
# = la séance) ; la publication ne sort que les retenues.

def _en_clair_ligne(conn, chemin, releve) -> dict:
    from collectors.en_clair.verifier import verifier
    from collectors.en_clair.rendu import erreurs
    from collectors.en_clair.seances import seance_id

    sid = seance_id(conn, releve)
    fautes = verifier(chemin)
    emp = "empreinte" if _dossiers.a_la_colonne_empreinte(conn) else "NULL AS empreinte"
    a = row(conn, f"SELECT review_status, note, reviewed_by, reviewed_at, {emp} FROM annotations "
                  "WHERE object_type='en_clair' AND object_id=?", (sid,)) if sid else None
    actuelle = empreinte(chemin.read_bytes())
    apres = releve["en_clair"]["apres"]
    return {
        "releve": chemin.parent.name,
        "seance_id": sid,
        "date": releve["seance"]["date"],
        "assemblee": releve["seance"]["assemblee_court"],
        "code": releve.get("code"),
        "titre": apres.get("titre"),
        "actes": len(releve.get("actes", [])),
        "erreurs_documents": len(erreurs(releve)),
        # Ce que le vérificateur reproche au relevé : une seule faute et la
        # feuille ne peut pas être retenue — l'API le refuse, pas seulement l'écran.
        "fautes": fautes,
        "verdict": (verdict_de(a["review_status"]) if a else None) or JAMAIS_RELU,
        "note": (a["note"] or "") if a else "",
        "reviewed_by": a["reviewed_by"] if a else None,
        "reviewed_at": a["reviewed_at"] if a else None,
        "empreinte": actuelle,
        # Retenu, puis le relevé a changé : il ne sortira plus tant qu'il n'est
        # pas retenu de nouveau.
        "modifie": modifie_depuis_relecture(a["review_status"] if a else None,
                                            a["empreinte"] if a else None, actuelle),
    }


@app.get("/api/atelier/en-clair")
def atelier_en_clair(user=Depends(require_auth)):
    """La file des séances relevées, avec leur vérification et leur verdict."""
    from collectors.en_clair.seances import releves
    conn = get_db()
    try:
        return [_en_clair_ligne(conn, p, r) for p, r in releves(RACINE)]
    finally:
        conn.close()


@app.get("/api/atelier/en-clair/{nom}/apercu")
def atelier_en_clair_apercu(nom: str = FPath(..., pattern=r"^\d{4}-\d{2}-\d{2}-(cm|cc)$"),
                            user=Depends(require_auth)):
    """Les trois feuilles telles qu'elles seraient publiées, tampon compris."""
    from fastapi.responses import HTMLResponse
    from collectors.en_clair.rendu import document, feuilles, page_erreurs
    from collectors.en_clair.seances import dossier
    from collectors.chemins import sous
    chemin = sous(dossier(RACINE), nom, "releve.json")
    if not chemin.exists():
        raise HTTPException(404, f"Aucun relevé « {nom} ».")
    releve = json.loads(chemin.read_text())
    return HTMLResponse(document(f"Aperçu · {nom}", feuilles(releve) + page_erreurs(releve)))


# ─── Les dossiers thématiques ────────────────────────────────────────────────
#
# 01/10/2026. Un dossier était un markdown édité sur le disque et publié par une
# ligne de son en-tête : ni relecture, ni historique, ni moyen pour un éditeur
# non technicien d'y toucher. Ici on les lit, on les écrit (contributeur et
# au-dessus : écrire PROPOSE, ça ne publie rien), et on les retient par le même
# `_decider` que tout le reste (validateur). Ce qui sort est décidé par le
# snapshot : retenu ET inchangé depuis la relecture.

_SLUG_DOSSIER = r"^[a-z0-9][a-z0-9-]{0,60}$"


def _index_atelier(conn):
    """Les actes que la publication laisserait sortir, selon les règles de
    l'instance — l'atelier n'a pas de snapshot sous la main quand un validateur
    retient. Même prédicat que le snapshot (`citations.publiable_selon_regles`) :
    on ne scelle pas une citation vers un acte que le site ne montrera pas."""
    from collectors.citations import index_selon_regles
    from collectors.config import RULES_PATH as _regles
    return index_selon_regles(conn, _read_json_file(_regles, {}))


def _sceller_dossier(conn, dossier_id: int, empreinte_retenue: Optional[str]) -> None:
    """Le sceau des citations d'un dossier qu'on retient (cf.
    `collectors/dossiers.py::sceller`) ; tout autre verdict le lève. Dans la
    transaction du verdict : un verdict sans son sceau ne doit pas exister."""
    from collectors.citations import relier
    if empreinte_retenue is None:
        _dossiers.lever_le_sceau(conn, dossier_id)
        return
    slug = _dossiers.slug_de(conn, dossier_id)
    p = _dossiers.chemin(RACINE, slug)
    octets = p.read_bytes()
    if empreinte(octets) != empreinte_retenue:
        raise HTTPException(409, "Le texte a changé pendant que vous le reteniez : "
                                 "rechargez-le et relisez-le avant de décider.")
    relu_le = conn.execute("SELECT reviewed_at FROM annotations WHERE object_type='dossier' "
                           "AND object_id=?", (dossier_id,)).fetchone()[0]
    _dossiers.sceller(conn, dossier_id, empreinte_retenue,
                      relier(octets.decode("utf-8"), _index_atelier(conn)), relu_le)


class DossierEcriture(BaseModel):
    texte: str
    # L'empreinte du fichier quand l'éditeur l'a ouvert ; null pour CRÉER.
    empreinte_lue: Optional[str] = None


class DossierCreation(BaseModel):
    slug: str = Field(..., pattern=_SLUG_DOSSIER)
    titre: str


#: État d'un dossier retenu dont le texte a changé depuis : hors publication
#: tant qu'un validateur ne l'a pas retenu de nouveau. Distinct de tout verdict.
DOSSIER_MODIFIE = "modifie_hors_publication"


def _dossiers_modifies(conn) -> list[str]:
    """Les dossiers retenus dont le texte a changé depuis : la publication les
    a retirés en silence. Même prédicat que `_dossier_ligne`."""
    sortie = []
    for slug, p in _dossiers.lister(RACINE):
        v = _dossiers.verdict(conn, _dossiers.identifiant(conn, slug))
        if v and modifie_depuis_relecture(v["review_status"], v["empreinte"],
                                          _dossiers.empreinte_de(p)):
            sortie.append(slug)
    return sortie


def _dossier_ligne(conn, slug: str, p: Path, index=None) -> dict:
    meta, corps = _dossiers.entete(p.read_text(encoding="utf-8"))
    # Un dossier posé sur le disque (à la main, par un script) reçoit ici son
    # identifiant : sans lui, aucun verdict ne pourrait s'y attacher.
    did = _dossiers.identifiant(conn, slug, creer=True)
    v = _dossiers.verdict(conn, did)
    actuelle = _dossiers.empreinte_de(p)
    statut = v["review_status"] if v else None
    # « À revoir » parce qu'un acte cité a changé depuis la relecture : un état
    # DÉDUIT, pas le verdict `a_revoir` — qui, pour un dossier, le retirerait
    # du site (cf. collectors/dossiers.py, « Ce que la relecture a vu »).
    perime = (_dossiers.peremption(conn, did, actuelle, index)
              if index is not None and verdict_de(statut) == RETENU else None)
    verdict = (verdict_de(statut) if v else None) or JAMAIS_RELU
    modifie = modifie_depuis_relecture(statut, v and v["empreinte"], actuelle)
    return {
        "perime": perime if perime and perime["elements"] else None,
        "slug": slug, "dossier_id": did,
        "titre": meta.get("titre") or slug, "chapeau": meta.get("chapeau", ""),
        "maj": meta.get("maj", ""), "a_developper": meta.get("statut") == "a_developper",
        "mots": len(corps.split()), "empreinte": actuelle,
        # Le verdict POSÉ, tel qu'en base : « retenu » reste vrai d'un texte
        # qui n'est plus celui-ci.
        "verdict": verdict,
        "modifie": modifie,
        # L'état à AFFICHER (audit du 15/11/2026, A4) : un dossier retenu puis
        # modifié ne sort plus, et son verdict seul disait encore « retenu ».
        "etat": DOSSIER_MODIFIE if modifie else verdict,
        "note": (v or {}).get("note") or "",
        "reviewed_by": (v or {}).get("reviewed_by"),
        "reviewed_at": (v or {}).get("reviewed_at"),
    }


@app.get("/api/atelier/dossiers")
def atelier_dossiers(user=Depends(require_auth)):
    """Les dossiers de l'instance, avec leur verdict et leur état de relecture."""
    conn = get_db_rw()
    try:
        _dossiers.assurer_schema(conn)
        index = _index_atelier(conn)
        lignes = [_dossier_ligne(conn, slug, p, index) for slug, p in _dossiers.lister(RACINE)]
        conn.commit()   # les identifiants donnés aux dossiers posés sur le disque
        return lignes
    finally:
        conn.close()


@app.get("/api/atelier/dossiers/{slug}")
def atelier_dossier(slug: str = FPath(..., pattern=_SLUG_DOSSIER), user=Depends(require_auth)):
    p = _dossiers.chemin(RACINE, slug)
    if not p.exists():
        raise HTTPException(404, f"Aucun dossier « {slug} ».")
    conn = get_db_rw()
    try:
        _dossiers.assurer_schema(conn)
        from collectors.citations import relier
        index = _index_atelier(conn)
        texte = p.read_text(encoding="utf-8")
        ligne = {**_dossier_ligne(conn, slug, p, index), "texte": texte,
                 # Ce que le résolveur fait de chaque citation, contre les actes
                 # publiables : l'éditeur voit ce qui sera relié, et ce qui ne
                 # le sera pas — avant de demander une relecture.
                 "citations": [c.releve() for c in relier(texte, index).citations]}
        conn.commit()
        return ligne
    finally:
        conn.close()


def _identifier_dossier(slug: str) -> int:
    """Donne au dossier son identifiant en base — celui que porte son verdict."""
    conn = get_db_rw()
    try:
        _dossiers.assurer_schema(conn)
        did = _dossiers.identifiant(conn, slug, creer=True)
        conn.commit()
        return did
    finally:
        conn.close()


@app.put("/api/atelier/dossiers/{slug}")
def atelier_dossier_ecrire(req: DossierEcriture, slug: str = FPath(..., pattern=_SLUG_DOSSIER),
                           user=Depends(require_auth)):
    """Enregistre le texte. Un dossier retenu qui change ne sort plus tant
    qu'un validateur ne l'a pas relu et retenu de nouveau."""
    exiger(user, "contributor", "Modifier un dossier")
    if not _dossiers.chemin(RACINE, slug).exists():
        raise HTTPException(404, f"Aucun dossier « {slug} ».")
    did = _identifier_dossier(slug)
    conn = get_db()
    try:
        v = _dossiers.verdict(conn, did)
    finally:
        conn.close()
    statut, retenue = (v["review_status"], v["empreinte"]) if v else (None, None)
    try:
        nouvelle = _dossiers.ecrire(RACINE, slug, req.texte, req.empreinte_lue)
    except _dossiers.Conflit as e:
        raise HTTPException(409, str(e))
    _journaliser_dossier(user, did, "edit")
    modifie = modifie_depuis_relecture(statut, retenue, nouvelle)
    return {"ok": True, "slug": slug, "empreinte": nouvelle,
            "etat": DOSSIER_MODIFIE if modifie else (verdict_de(statut) or JAMAIS_RELU),
            # Ce geste vient de retirer du site un dossier qui y était : le dire
            # à qui l'a fait, au lieu de répondre « enregistré » et rien d'autre.
            "retire_de_la_publication": modifie and retenue == req.empreinte_lue}


@app.post("/api/atelier/dossiers")
def atelier_dossier_creer(req: DossierCreation, user=Depends(require_auth)):
    exiger(user, "contributor", "Créer un dossier")
    try:
        p = _dossiers.chemin(RACINE, req.slug)
        if p.exists():
            raise HTTPException(409, f"Le dossier « {req.slug} » existe déjà.")
        nouvelle = _dossiers.ecrire(RACINE, req.slug, _dossiers.modele(req.titre.strip()), None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except _dossiers.Conflit as e:
        raise HTTPException(409, str(e))
    did = _identifier_dossier(req.slug)
    _journaliser_dossier(user, did, "create")
    return {"ok": True, "slug": req.slug, "dossier_id": did, "empreinte": nouvelle}


# ─── Les tâches nées des lacunes ─────────────────────────────────────────────
#
# 03/10/2026. Chaque lacune d'un dossier — question ouverte, citation sans acte
# publié — est une tâche de la file « lacunes » (`collectors/taches.py`). Les
# tâches se mettent d'accord avec les dossiers à chaque relevé : une lacune
# retirée ferme sa tâche, avec la raison. Répondre PROPOSE (contributeur) ;
# valider tranche (validateur). Rien n'écrit jamais dans un dossier.

from collectors import taches as _taches  # noqa: E402


class TacheReponse(BaseModel):
    contenu: dict


class TacheDecision(BaseModel):
    accepter: bool
    motif: str = ""


class TacheEnvoi(BaseModel):
    envoye_le: str


def _synchroniser_taches(conn, index=None) -> dict:
    """Le relevé : les lacunes de tous les dossiers, contre les actes
    publiables maintenant, puis les tâches mises d'accord avec elles."""
    _dossiers.assurer_schema(conn)
    _taches.assurer_schema(conn)
    lacunes, titres = _taches.lacunes_de_l_instance(RACINE, index or _index_atelier(conn))
    return _taches.synchroniser(conn, lacunes, titres)


def _candidats_de(index, t: dict) -> list[dict]:
    """Les actes publiés proposés pour relier une citation, tels qu'un
    bénévole les lit : date, assemblée, numéro, titre, et où les vérifier."""
    from collectors.citations import candidats
    c = (t.get("lacune") or {}).get("citation") or {}
    return [{"cle": a.cle, "date": a.date, "assemblee": a.assemblee, "numero": a.numero,
             "titre": a.titre, "url": a.url, "source_url": a.source_url}
            for a in candidats(index, c.get("texte", ""), c.get("candidats"))]


def _tache_vue(index, t: dict, titres: dict) -> dict:
    t = dict(t)
    t["dossier_titre"] = titres.get(t["dossier"], t["dossier"])
    if t["nature"] == "relier" and t["etat"] in ("ouverte", "proposee"):
        t["candidats"] = _candidats_de(index, t)
    # Ce que l'éditeur reportera dans le texte, une fois la réponse validée.
    if t["nature"] == "relier" and t["etat"] == "validee" and (t.get("reponse") or {}).get("cle"):
        texte = ((t.get("lacune") or {}).get("citation") or {}).get("texte", "")
        t["a_reporter"] = f"[{texte}](acte:{t['reponse']['cle']})"
    return t


@app.get("/api/atelier/taches")
def atelier_taches(etat: str = "a_faire", user=Depends(require_auth)):
    """La file des tâches. `a_faire` : ouvertes, proposées, et validées en
    attente de l'éditeur ; `fermee` : l'historique de ce que les dossiers ont
    refermé."""
    etats = {"a_faire": ("ouverte", "proposee", "validee"), "fermee": ("fermee",)}.get(etat)
    if etats is None:
        raise HTTPException(400, "etat invalide — valeurs : a_faire, fermee")
    conn = get_db_rw()
    try:
        index = _index_atelier(conn)
        bilan = _synchroniser_taches(conn, index)
        conn.commit()
        _, titres = _taches.lacunes_de_l_instance(RACINE, index)
        return {"taches": [_tache_vue(index, t, titres) for t in _taches.lister(conn, etats)],
                "releve": bilan, "dossiers": len(titres),
                "relecture_croisee": _taches.relecture_croisee(),
                "peut_valider": au_moins(user, "validator"), "moi": user.get("id")}
    finally:
        conn.close()


@app.get("/api/atelier/taches/{tache_id}")
def atelier_tache(tache_id: int = FPath(..., ge=1), user=Depends(require_auth)):
    conn = get_db_rw()
    try:
        _taches.assurer_schema(conn)
        t = _taches.lire(conn, tache_id)
        if not t:
            raise HTTPException(404, "Tâche introuvable.")
        index = _index_atelier(conn)
        _, titres = _taches.lacunes_de_l_instance(RACINE, index)
        return _tache_vue(index, t, titres)
    finally:
        conn.close()


def _geste_tache(fonction, tache_id: int, user, *args) -> dict:
    conn = get_db_rw()
    try:
        _taches.assurer_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            t = fonction(conn, tache_id, user, *args)
        except _taches.Refus as e:
            conn.rollback()
            raise HTTPException(e.code, str(e))
        conn.commit()
        return t
    finally:
        conn.close()


@app.post("/api/atelier/taches/{tache_id}/reponse")
def atelier_tache_repondre(req: TacheReponse, tache_id: int = FPath(..., ge=1),
                           user=Depends(require_au_moins("contributor", "Répondre à une tâche"))):
    """Apporter une réponse : elle est PROPOSÉE, un validateur la tranchera.
    Pour relier une citation, seuls les actes publiés proposés sont admis."""
    cles = None
    conn = get_db()
    try:
        t = _taches.lire(conn, tache_id) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='taches'").fetchone() else None
        if t and t["nature"] == "relier":
            cles = {c["cle"] for c in _candidats_de(_index_atelier(conn), t)}
    finally:
        conn.close()
    return _geste_tache(_taches.repondre, tache_id, user, req.contenu, cles)


@app.post("/api/atelier/taches/{tache_id}/decision")
def atelier_tache_valider(req: TacheDecision, tache_id: int = FPath(..., ge=1),
                          user=Depends(require_au_moins("validator", "Valider une réponse"))):
    return _geste_tache(_taches.valider, tache_id, user, req.accepter, req.motif)


@app.post("/api/atelier/taches/{tache_id}/envoi")
def atelier_tache_envoyee(req: TacheEnvoi, tache_id: int = FPath(..., ge=1),
                          user=Depends(require_au_moins("contributor", "Noter l'envoi d'une demande"))):
    """La demande validée est partie : sa date est notée, et la même tâche
    attend désormais la réponse."""
    return _geste_tache(_taches.marquer_envoyee, tache_id, user, req.envoye_le)


def _journaliser_dossier(user, dossier_id: int, action: str) -> None:
    conn = get_db_rw()
    try:
        conn.execute("INSERT INTO audit_log(user_id, entity_id, table_name, action, field) "
                     "VALUES(?,?,?,?,?)", (user.get("id"), None, "dossiers", action,
                                           f"dossier/{dossier_id}"))
        conn.commit()
    finally:
        conn.close()


# ─── Saisie manuelle ──────────────────────────────────────────────────────────
#
# Ce que l'atelier savait faire jusqu'ici : JUGER une ligne qu'un collecteur a
# écrite — l'écarter, la rectifier. Ce qu'il ne savait pas faire : en CRÉER une.
# Or 49 % des flux financiers de la production sont saisis à la main, dans onze
# scripts Python jetables, et les budgets votés n'existent nulle part ailleurs
# que dans les comptes rendus du conseil.
#
# La saisie n'écrit PAS directement en base. Elle va dans `saisies/saisies.json`,
# que le collecteur `saisies` rejoue à chaque collecte : sans quoi elle serait
# perdue au premier `init_instance`, exactement comme le travail de la v1. L'API
# écrit le fichier puis rejoue l'import — un seul chemin d'écriture, donc pas de
# divergence possible entre « saisir » et « rejouer ».
#
# Elle ne touche jamais une ligne existante : elle en ajoute, marquées
# `origine='atelier'`. La règle « ne pas corrompre ce qui vient d'un collecteur
# institutionnel » tient donc par construction.

from collectors import saisies as _saisies  # noqa: E402
from collectors.db import assurer_refus, refus_de_relation  # noqa: E402

RACINE = Path(__file__).resolve().parent


@app.get("/api/atelier/saisies/champs")
def atelier_saisies_champs(user=Depends(require_auth)):
    """Le contrat de saisie : le formulaire le demande au lieu de le deviner."""
    return {"objets": _saisies.CHAMPS_SAISIE, "confiances": list(_saisies.CONFIANCES)}


@app.get("/api/atelier/saisies")
def atelier_saisies_liste(user=Depends(require_auth)):
    data = _saisies.charger()
    return {"version": data.get("version"),
            "saisies": [s for s in data.get("saisies", []) if not s.get("retire")]}


@app.get("/api/atelier/saisies/entite/existante")
def atelier_saisie_entite_existante(
    type: str = Query(...), name: str = Query(..., min_length=1, max_length=300),
    commune: Optional[str] = Query(None, max_length=200),
    siren: Optional[str] = Query(None, max_length=40),
    user=Depends(require_auth),
):
    """La fiche qu'une saisie d'entité rejoindrait, AVANT de l'enregistrer.

    Même recherche que le rejeu (`entite_connue` : SIREN, puis nom exact, puis
    variante d'écriture dans la même commune). Une variante rejoignait la
    fiche existante sans que rien le dise : on croyait en créer une."""
    if type not in _saisies.TABLE_DU_SIREN and type not in ("person", "service", "place"):
        raise HTTPException(400, "type invalide")
    nom = " ".join(name.split())
    siren_ok = _saisies.siren_valide(siren) if type in _saisies.TABLE_DU_SIREN else ""
    conn = get_db()
    try:
        eid = _saisies.entite_connue(conn, type=type, name=nom,
                                     commune=(commune or "").strip() or None,
                                     siren=siren_ok or None)
        if eid is None:
            return {"fiche": None}
        fiche = row(conn, "SELECT id, name, type, commune FROM entities WHERE id=?", (eid,))
        fiche["verdict"] = _verdict_de_fiche(conn, eid)
        return {"fiche": fiche, "meme_nom": fiche["name"] == nom}
    finally:
        conn.close()


class SaisieCreate(BaseModel):
    objet: str
    valeurs: dict
    source: dict = {}
    confidence: str = "confirmed"


def _valide_saisie(objet: str, valeurs: dict) -> dict:
    """Applique le contrat de `collectors/saisies.py` : champs obligatoires
    présents, valeurs normalisées, rien d'autre accepté.

    Refuser un champ inconnu n'est pas du zèle : c'est ce qui empêche un
    formulaire d'écrire dans une colonne que la publication ne relit jamais,
    donc de faire croire à une saisie qui n'aura aucun effet visible.
    """
    contrat = _saisies.CHAMPS_SAISIE.get(objet)
    if not contrat:
        raise HTTPException(400, f"objet inconnu : {objet} — valeurs admises : "
                                 f"{', '.join(_saisies.CHAMPS_SAISIE)}")
    propres: dict = {}
    for cle, spec in contrat.items():
        if cle.startswith("_"):
            continue
        champ = cle.rstrip("*")
        genre = spec[0]
        valeur = valeurs.get(champ)
        normalisee = _normaliser(genre, champ, valeur)
        # Une chaîne d'espaces normalisée vaut "" : elle passait le contrôle
        # d'obligation (fiche sans nom, audit du 15/11/2026). Ici seulement :
        # pour une correction, None veut dire « annuler », pas « vide ».
        if normalisee is None or normalisee == "":
            if cle.endswith("*"):
                raise HTTPException(400, f"{champ} : champ obligatoire ({spec[1]})")
            continue
        propres[champ] = normalisee

    inconnus = set(valeurs) - {c.rstrip("*") for c in contrat if not c.startswith("_")}
    if inconnus:
        raise HTTPException(400, f"champs non reconnus pour {objet} : "
                                 f"{', '.join(sorted(inconnus))}")
    if objet == "entite" and propres.get("siren"):
        propres["siren"] = _siren_de_saisie(propres["siren"], propres.get("type"))
    return propres


def _siren_de_saisie(valeur: str, type_: Optional[str]) -> str:
    """Le SIREN d'une fiche saisie, gardé s'il est valide, refusé sinon : il
    était accepté puis jeté sans un mot (audit du 15/11/2026)."""
    if type_ not in _saisies.TABLE_DU_SIREN:
        raise HTTPException(400, "siren : seule une entreprise ou une association porte "
                                 "un SIREN. Laissez ce champ vide pour cette nature de fiche.")
    siren = _saisies.siren_valide(valeur)
    if not siren:
        raise HTTPException(400, f"siren : « {valeur} » n'est pas un SIREN valide. Il "
                                 "compte neuf chiffres (ou les quatorze d'un SIRET) et "
                                 "se vérifie sur annuaire-entreprises.data.gouv.fr. "
                                 "Laissez le champ vide si vous ne l'avez pas.")
    return siren


def _valide_source(source: dict, conn) -> dict:
    """Une saisie sans source ne s'enregistre pas.

    Les procès-verbaux 2017-2019 de la commune ont disparu du site de la mairie :
    une URL seule ne prouve plus rien. On exige donc un document ARCHIVÉ —
    choisi parmi ceux que la collecte a déjà déposés, ou déposé à la main.

    La soupape `sans_document_motif` existe pour les cas où c'est impossible
    (document remis sur papier, consultation sur place). Elle n'est pas un
    contournement : le motif est enregistré, republié avec la ligne, et se voit.
    """
    doc_id = source.get("raw_document_id")
    motif = (source.get("sans_document_motif") or "").strip()
    if not doc_id and not motif:
        raise HTTPException(
            400, "Source manquante : choisir un document archivé, en déposer un, "
                 "ou expliquer pourquoi il n'y en a pas (sans_document_motif).")
    propre = {"citation": " ".join(str(source.get("citation") or "").split())[:2000]}
    if doc_id:
        doc = row(conn, "SELECT id, sha256, url, local_path FROM raw_documents "
                        "WHERE id=?", (int(doc_id),))
        if not doc:
            raise HTTPException(400, f"document {doc_id} introuvable.")
        # Le sha256 accompagne l'identifiant : sur une autre machine, les `id`
        # ne veulent rien dire, et c'est l'empreinte qui retrouve le document.
        propre.update({"raw_document_id": doc["id"], "sha256": doc["sha256"],
                       "url": doc["url"]})
    else:
        propre["sans_document_motif"] = motif[:500]
        if source.get("url"):
            propre["url"] = _normaliser("url", "url", source["url"])
    # Une ligne reprise d'une proposition du modèle garde QUEL modèle l'a lue
    # (charte IA, engagement 4) — déclarée par l'atelier, vérifiée dans sa forme.
    try:
        ia = _extraction.trace_ia(source.get("ia"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if ia:
        propre["ia"] = ia
    return propre


@app.post("/api/atelier/saisies", status_code=201)
def atelier_saisie_creer(req: SaisieCreate, user=Depends(require_auth)):
    if req.confidence not in _saisies.CONFIANCES:
        raise HTTPException(400, f"confidence : valeurs admises — "
                                 f"{', '.join(_saisies.CONFIANCES)}")
    # `confirmed` est publiable : saisir « confirmé », c'est trancher. Le
    # contributeur saisit en `probable`, un validateur confirme.
    if req.confidence in PUBLIABLES:
        exiger(user, "validator", "Saisir une donnée confirmée")
    conn = get_db()
    try:
        valeurs = _valide_saisie(req.objet, req.valeurs)
        source = _valide_source(req.source or {}, conn)
    finally:
        conn.close()

    saisie = {
        "id": _secrets.token_hex(8),
        "objet": req.objet,
        "valeurs": valeurs,
        "source": source,
        "confidence": req.confidence,
        "saisi_par": user["email"],
        "saisi_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Le SIREN d'une fiche n'est écrit en base que saisi par un validateur :
    # c'est par lui que la collecte range les données d'un registre.
    if req.objet == "entite" and valeurs.get("siren") and au_moins(user, "validator"):
        saisie["siren_confirme_par"] = user["email"]
    with _saisies.modifier() as data:
        data.setdefault("saisies", []).append(saisie)

    # Rejouer TOUT le fichier plutôt qu'insérer cette ligne seule : c'est le même
    # code qui écrira en base au prochain `run_all`, donc le résultat visible
    # dans l'atelier est exactement celui qu'une recollecte reproduira.
    try:
        resultat = _saisies.import_saisies()
    except Exception as e:
        # La saisie est déjà dans le fichier — donc elle n'est pas perdue, et
        # elle sera reprise au prochain passage. Le dire, plutôt que de laisser
        # croire à un échec total : le travail de lecture est ce qui coûte cher.
        raise HTTPException(
            500, f"Saisie enregistrée dans saisies/saisies.json, mais son "
                 f"insertion en base a échoué : {e}. Elle sera reprise au "
                 f"prochain passage du collecteur.")

    conn = get_db_rw()
    try:
        entite = refus = None
        if req.objet == "entite":
            entite = _completer_fiche_rejointe(conn, saisie["id"], valeurs, user)
        elif req.objet == "flux":
            refus = _saisies.refus_du_lien_de_flux(conn, saisie)
        # Avec la fiche créée ou rejointe : sans elle, la création n'entrait pas
        # dans l'historique de la fiche (audit du 15/11/2026).
        conn.execute(
            "INSERT INTO audit_log(user_id, entity_id, table_name, action, field,"
            " old_value, new_value) VALUES(?,?,?,?,?,?,?)",
            (user["id"], entite["id"] if entite else None, "saisies", "create",
             req.objet, None, json.dumps(saisie, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    reponse = {"ok": True, "saisie": saisie, "import": resultat}
    if entite is not None:
        reponse["entite"] = entite
        reponse["entity_id"] = entite["id"]
    if refus is not None:
        reponse["lien"] = {
            "ecrit": False, "refus_id": refus["id"],
            "message": "Flux enregistré, mais son lien sur la fiche n'a pas été écrit : "
                       "un validateur a refusé ce lien. Le lever le rétablira."}
    return reponse


#: Ce qu'une saisie d'entité apporte et qu'une édition directe sait écrire.
#: `commune` et `siren` ne s'éditent pas sur une fiche (absents de
#: `_CHAMPS_FICHE`) : sur une fiche rejointe, ils sont rendus « non repris ».
_SAISIE_COMPLETE = ("address",)


def _completer_fiche_rejointe(conn, saisie_id: str, valeurs: dict, user: dict) -> Optional[dict]:
    """La fiche que la saisie a créée ou rejointe, et ce qu'elle y a apporté.

    Rejointe, ses champs VIDES que la saisie remplirait passent par le même
    chemin qu'une édition directe (`_changements_fiche`) : un contributeur sur
    une fiche publiée propose, sinon on écrit et on journalise. Le rejeu des
    saisies, lui, ne touche jamais une fiche existante (cf.
    `collectors/saisies.py::_inserer_entite`) : sans ceci, l'adresse saisie
    disparaissait sans que rien le dise. Un champ déjà rempli n'est pas
    remplacé : corriger une valeur, c'est éditer la fiche."""
    _saisies.assurer_rattachement(conn)
    trouvee = _saisies.fiche_de_la_saisie(conn, saisie_id)
    if trouvee is None:
        return None
    eid, cree = trouvee
    sortie = {"id": eid, "cree": cree, "modifies": [], "proposes": [],
              "proposition": None, "non_repris": [], "siren_a_confirmer": False}
    siren = valeurs.get("siren")
    if cree:
        # Contributeur : le SIREN reste dans la saisie, non repris, jusqu'à ce
        # qu'un validateur le confirme (avis de vigie-securite, cf. saisies.py).
        if siren and not au_moins(user, "validator"):
            sortie["non_repris"] = ["siren"]
            sortie["siren_a_confirmer"] = True
        return sortie
    current = row(conn, _FICHE_COURANTE, (eid,))
    vides = {c: valeurs[c] for c in _SAISIE_COMPLETE
             if valeurs.get(c) and not str(current.get(c) or "").strip()}
    # Un validateur qui saisit de nouveau la fiche avec son SIREN le confirme :
    # écrit si la fiche n'en porte aucun et qu'aucune autre ne le porte. C'est
    # ainsi qu'un validateur reprend le SIREN laissé à confirmer par un
    # contributeur. Jamais remplacé : corriger un SIREN reste hors de l'atelier.
    table_siren = _saisies.TABLE_DU_SIREN.get(current.get("type"))
    if (siren and table_siren and au_moins(user, "validator")
            and not row(conn, f"SELECT 1 AS ok FROM {table_siren} WHERE entity_id=? "
                              "AND COALESCE(siren, '') <> ''", (eid,))
            and _saisies.entite_par_siren(conn, siren) is None):
        if row(conn, f"SELECT 1 AS ok FROM {table_siren} WHERE entity_id=?", (eid,)):
            conn.execute(f"UPDATE {table_siren} SET siren=? WHERE entity_id=?", (siren, eid))
        else:
            conn.execute(f"INSERT INTO {table_siren}(entity_id, siren) VALUES(?,?)", (eid, siren))
        _journal(conn, user, eid, table_siren, "update", "siren", apres=siren)
        sortie["modifies"].append("siren")
    sortie["non_repris"] = sorted(c for c in ("address", "commune", "siren")
                                  if valeurs.get(c) and c not in vides
                                  and c not in sortie["modifies"])
    changements = _changements_fiche(current, vides, user)
    if not changements:
        return sortie
    if _doit_proposer(user, current.get("confidence")):
        _propositions.assurer_schema(conn)
        sortie["proposition"] = _propositions.proposer(
            conn, "fiche", "entity", eid, eid, {c: n for c, _, n in changements},
            {c: a for c, a, _ in changements}, user)
        sortie["proposes"] = sorted(c for c, _, _ in changements)
    else:
        sortie["modifies"] += _ecrire_fiche(conn, eid, changements, user)
    return sortie


def _saisie_validee(saisie: dict) -> bool:
    """Vraie si la saisie est publiable telle quelle, ou si un validateur a
    retenu la ligne qu'elle a produite (`source = atelier:<id>`)."""
    if saisie.get("confidence") in PUBLIABLES:
        return True
    table = (_saisies.CHAMPS_SAISIE.get(saisie.get("objet")) or {}).get("_table")
    objet = next((t for t, nom in TABLE_DE_TYPE.items() if nom == table), None)
    if not objet:
        return False
    conn = get_db()
    try:
        a = row(conn, f"SELECT a.review_status FROM annotations a JOIN {table} t "
                      "ON t.id = a.object_id WHERE a.object_type = ? AND t.source = ?",
                (objet, f"atelier:{saisie.get('id')}"))
    finally:
        conn.close()
    return bool(a) and verdict_de(a["review_status"]) == RETENU


@app.delete("/api/atelier/saisies/{saisie_id}")
def atelier_saisie_retirer(saisie_id: str, user=Depends(require_auth)):
    """Retire une saisie — marquée retirée, pas effacée du fichier.

    Garder la trace du retrait est ce qui permet de le propager : sur une autre
    machine qui a déjà importé cette saisie, une ligne disparue du fichier serait
    indistinguable d'une ligne jamais reçue, et l'import la laisserait en place.
    """
    deja_retiree = False
    with _saisies.modifier() as data:
        cible = next((s for s in data.get("saisies", [])
                      if s.get("id") == saisie_id), None)
        if cible is None:
            raise HTTPException(404, f"saisie {saisie_id} introuvable.")
        if cible.get("saisi_par") != user["email"]:
            exiger(user, "validator", "Retirer la saisie de quelqu'un d'autre")
        # Retirer une saisie EFFACE sa ligne : validée, elle est sur le site, et
        # son auteur l'en sortait seul. Ce qu'un validateur a retenu ne se
        # défait que par un validateur.
        if _saisie_validee(cible):
            exiger(user, "validator", "Retirer une saisie déjà validée")
        if cible.get("retire"):
            deja_retiree = True
        else:
            cible["retire"] = True
            cible["retire_par"] = user["email"]
            cible["retire_le"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if deja_retiree:
        return {"ok": True, "deja_retiree": True}
    resultat = _saisies.import_saisies()

    # La création laissait une trace dans `audit_log`, le retrait non : on
    # savait qui avait saisi une ligne, jamais qui l'avait fait disparaître du
    # site — alors que c'est le geste qui retire une information publiée.
    conn = get_db_rw()
    try:
        conn.execute(
            "INSERT INTO audit_log(user_id, entity_id, table_name, action, field,"
            " old_value, new_value) VALUES(?,?,?,?,?,?,?)",
            (user["id"], None, "saisies", "retrait", cible.get("objet"),
             json.dumps(cible, ensure_ascii=False), None))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "import": resultat}


# ─── Documents sources ────────────────────────────────────────────────────────

@app.get("/api/atelier/documents")
def atelier_documents(
    q: Optional[str] = Query(None, description="filtre sur le titre ou l'URL"),
    limit: int = Query(50, le=500),
    user=Depends(require_auth),
):
    """Les sources déjà archivées localement, pour en choisir une à la saisie."""
    conn = get_db()
    try:
        if q:
            motif = f"%{q.strip()}%"
            items = rows(conn, """
                SELECT id, source, doc_type, title, url, byte_size, fetched_at
                FROM raw_documents
                WHERE title LIKE ? OR url LIKE ? OR local_path LIKE ?
                ORDER BY fetched_at DESC LIMIT ?""", (motif, motif, motif, limit))
        else:
            items = rows(conn, """
                SELECT id, source, doc_type, title, url, byte_size, fetched_at
                FROM raw_documents ORDER BY fetched_at DESC LIMIT ?""", (limit,))
        return items
    finally:
        conn.close()


@app.post("/api/atelier/documents", status_code=201)
async def atelier_document_deposer(
    fichier: UploadFile = File(...),
    titre: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    user=Depends(require_auth),
):
    """Dépose un document que la collecte n'a pas atteint.

    Rangé comme les collecteurs le font — `data/raw/atelier/<empreinte>.<ext>` —
    et inscrit dans `raw_documents`. L'empreinte sert de nom : deux dépôts du
    même fichier ne font qu'une entrée, et l'empreinte est ce qui permettra de
    retrouver ce document sur une autre machine.
    """
    contenu = await fichier.read()
    if not contenu:
        raise HTTPException(400, "fichier vide.")
    if len(contenu) > 50 * 1024 * 1024:
        raise HTTPException(413, "fichier trop volumineux (50 Mo maximum).")

    import hashlib
    empreinte = hashlib.sha256(contenu).hexdigest()
    # L'extension vient du nom déposé, mais n'est jamais reprise telle quelle :
    # elle sert à nommer un fichier sur le disque, et un nom fourni par un
    # formulaire n'a pas à décider d'un chemin. On ne garde que des lettres.
    ext = "".join(c for c in Path(fichier.filename or "").suffix.lower()
                  if c.isalnum() or c == ".")[:8] or ".bin"
    dossier = RACINE / "data" / "raw" / "atelier"
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{empreinte[:16]}{ext}"
    if not chemin.exists():
        chemin.write_bytes(contenu)

    relatif = str(chemin.relative_to(RACINE))
    conn = get_db_rw()
    try:
        existant = row(conn, "SELECT id FROM raw_documents WHERE sha256=?", (empreinte,))
        if existant:
            return {"ok": True, "id": existant["id"], "deja_present": True,
                    "sha256": empreinte}
        cur = conn.execute("""
            INSERT INTO raw_documents(source, url, doc_type, sha256, byte_size,
                                      local_path, http_status, title, parse_status)
            VALUES('atelier', ?, ?, ?, ?, ?, NULL, ?, 'manuel')""",
            (url, ext.lstrip(".") or "bin", empreinte, len(contenu), relatif,
             titre or fichier.filename))
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "sha256": empreinte,
                "byte_size": len(contenu), "local_path": relatif}
    finally:
        conn.close()


def _document_archive(doc_id: int) -> tuple[Path, str]:
    """Le fichier d'un document archivé et son type, ou 404."""
    conn = get_db()
    try:
        doc = row(conn, "SELECT local_path, doc_type FROM raw_documents WHERE id=?", (doc_id,))
    finally:
        conn.close()
    if not doc:
        raise HTTPException(404, "document introuvable.")
    # `local_path` vient de la base, mais la base est modifiable par d'autres
    # chemins que celui-ci : on résout et on vérifie l'appartenance à l'arbre du
    # projet plutôt que de faire confiance à une chaîne stockée.
    # L'arbre du projet ne suffit pas : il contient aussi `.env` et la base. Un
    # document archivé vit sous `data/`, nulle part ailleurs.
    chemin = (RACINE / doc["local_path"]).resolve()
    if not chemin.is_file() or (RACINE / "data") not in chemin.parents:
        raise HTTPException(404, "fichier absent du disque.")
    return chemin, doc["doc_type"]


@app.get("/api/atelier/documents/{doc_id}/fichier")
def atelier_document_fichier(doc_id: int = FPath(..., ge=1), user=Depends(require_auth)):
    """Sert le document archivé, pour relire la source sans quitter l'atelier.
    Chemin de repli, quand `DOCS_URL` n'est pas réglé (atelier local)."""
    chemin, doc_type = _document_archive(doc_id)
    # Un document vient d'un site tiers ou d'un dépôt : son contenu n'est PAS de
    # confiance. Servi en `text/html`, il s'exécutait à l'adresse de l'atelier,
    # où le navigateur garde le jeton de session de celui qui l'ouvre. Seul le
    # PDF s'affiche ; tout le reste se télécharge, sans type deviné.
    if doc_type == "pdf":
        return FileResponse(chemin, media_type="application/pdf",
                            headers={"X-Content-Type-Options": "nosniff"})
    return FileResponse(
        chemin, media_type="application/octet-stream", filename=chemin.name,
        headers={"X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.post("/api/atelier/documents/{doc_id}/lien")
def atelier_document_lien(doc_id: int = FPath(..., ge=1), user=Depends(require_auth)):
    """Un lien signé vers le document, sur l'hôte `DOCS_URL`, pour ce compte et
    cinq minutes. `url` vaut `null` sans `DOCS_URL` : l'interface se replie
    alors sur `/fichier`."""
    from api_auth import DUREE_LIEN_DOCUMENT, signer_lien_document
    if not DOCS_URL:
        return {"url": None}
    _, doc_type = _document_archive(doc_id)
    return {"url": f"{DOCS_URL}/d/{doc_id}?{urlencode(signer_lien_document(doc_id, user))}",
            "affichage": "pdf" if doc_type == "pdf" else "telechargement",
            "expire_dans": DUREE_LIEN_DOCUMENT}


# Hors de `/api/`, donc hors du verrou global — et c'est voulu : l'hôte des
# documents ne voit passer aucune session. La signature tient lieu de verrou.
# Les en-têtes de TOUTE réponse de cet hôte, refus compris : un 403 ou un 404 qui
# part sans `nosniff` ni `no-referrer` laisse le lien signé dans le Referer.
_ENTETES_DOCS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",          # le lien signé ne fuit pas
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "private, no-store",
    "X-Robots-Tag": "noindex, nofollow",
}


@app.get("/d/{doc_id}", include_in_schema=False)
def document_signe(request: Request, doc_id: int = FPath(..., ge=1),
                   exp: int | None = Query(None), u: int | None = Query(None),
                   sig: str | None = Query(None, max_length=64)):
    from api_auth import lien_document_valide
    if not HOTE_DOCS or request.url.hostname != HOTE_DOCS:
        raise HTTPException(404, "introuvable")
    # Paramètres facultatifs pour la validation, obligatoires pour la signature :
    # un 422 nommait les paramètres attendus à qui n'avait pas de lien.
    if exp is None or u is None or sig is None or not lien_document_valide(doc_id, exp, u, sig):
        raise HTTPException(403, "Lien invalide ou expiré : rouvrir le document depuis l'atelier.",
                            headers=_ENTETES_DOCS)
    chemin, doc_type = _document_archive(doc_id)
    entetes = _ENTETES_DOCS
    # Le PDF s'affiche SANS `sandbox` : la CSP `sandbox` empêche le lecteur PDF
    # de Chrome de s'ouvrir, et l'isolement tient ici à l'origine elle-même.
    if doc_type == "pdf":
        return FileResponse(chemin, media_type="application/pdf", headers=entetes)
    return FileResponse(chemin, media_type="application/octet-stream", filename=chemin.name,
                        headers={**entetes,
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


# ─── GET /api/atelier/entities/{id} — détail complet pour l'éditeur ───────────

def _colonne_existe(conn, table: str, colonne: str) -> bool:
    return any(r["name"] == colonne for r in rows(conn, f"PRAGMA table_info({table})"))


@app.get("/api/atelier/entities/{entity_id}")
def atelier_entity_detail(entity_id: int = FPath(..., ge=1), user=Depends(require_auth)):
    conn = get_db()
    try:
        e = row(conn, """
            SELECT e.id, e.type, e.name, e.short_name, e.address, e.commune,
                   e.perimetre,
                   e.lat, e.lng, e.confidence, e.responsible,
                   e.created_at, e.updated_at,
                   COALESCE(d.review_status, 'jamais_relu') AS verdict,
                   d.note AS verdict_note, d.reviewed_by AS verdict_par,
                   d.reviewed_at AS verdict_le,
                   p.firstname, p.lastname, p.birth_year, p.birth_month, p.gender,
                   b.siren, b.siret_siege, b.naf_code, b.naf_label,
                   b.legal_form_code, b.legal_form, b.status AS biz_status,
                   b.capital, b.employees_range, b.creation_date AS biz_creation,
                   b.closing_date,
                   a.rna_id, a.waldec_id, a.object AS asso_object,
                   a.status AS asso_status, a.creation_date AS asso_creation,
                   a.dissolution_date,
                   pl.osm_id, pl.osm_category, pl.osm_value, pl.tags AS osm_tags,
                   s.category AS svc_category, s.operator, s.opening_hours
            FROM entities e
            LEFT JOIN persons      p  ON p.entity_id  = e.id
            LEFT JOIN businesses   b  ON b.entity_id  = e.id
            LEFT JOIN associations a  ON a.entity_id  = e.id
            LEFT JOIN places       pl ON pl.entity_id = e.id
            LEFT JOIN services     s  ON s.entity_id  = e.id
            LEFT JOIN annotations  d  ON d.object_type = 'entity' AND d.object_id = e.id
            WHERE e.id = ?
        """, (entity_id,))
        if not e:
            raise HTTPException(404, "Entité introuvable")
        # `origine` arrive avec la collecte (`init_db` la rattrape, `classer_origine`
        # la remplit) : une instance pas encore recollectée ne l'a pas. La page
        # ne doit pas tomber pour autant.
        e["origine"] = (row(conn, "SELECT origine FROM entities WHERE id=?",
                            (entity_id,))["origine"]
                        if _colonne_existe(conn, "entities", "origine") else None)

        # Contacts d'une personne physique : réservés aux validateurs, tous types
        # (filtre ici, côté serveur ; même règle que les notes).
        e["contacts_reserves"] = _notes_reservees(e, user)
        e["contacts"] = [] if e["contacts_reserves"] else rows(conn,
            "SELECT id, type, value, label FROM contacts WHERE entity_id=? ORDER BY type, id",
            (entity_id,))

        e["relations"] = rows(conn, """
            SELECT r.id, r.relation_type, r.since, r.until, r.source, r.confidence,
                   f.id AS from_id, f.name AS from_name, f.type AS from_type,
                   t.id AS to_id,   t.name AS to_name,   t.type AS to_type
            FROM relations r
            JOIN entities f ON f.id = r.from_id
            JOIN entities t ON t.id = r.to_id
            WHERE (r.from_id = ? OR r.to_id = ?){reserve}
            ORDER BY r.relation_type, r.since DESC
        """.format(reserve="" if au_moins(user, "validator") else _SANS_PRESUMES),
            (entity_id, entity_id))

        e["audit"] = rows(conn, """
            SELECT al.at, al.action, al.field, al.old_value, al.new_value,
                   u.email AS user_email
            FROM audit_log al
            LEFT JOIN users u ON u.id = al.user_id
            WHERE al.entity_id = ?{reserve}
            ORDER BY al.at DESC
            LIMIT 30
        """.format(reserve="" if au_moins(user, "validator") else
                   # Un lien présumé changé ou retiré y laissait son type.
                   " AND NOT (al.table_name = 'relations' AND (al.old_value IN ({p})"
                   " OR al.new_value IN ({p})))".format(
                       p=", ".join("'" + t + "'" for t in PRESUMES))),
            (entity_id,))
        # Fiche réservée (personne, compte non validateur) : refus par défaut.
        # Toute ligne perd son contenu, quelle que soit la table (note, décision
        # annotée, contact…) ; « qui, quand, quel champ » reste. Une liste noire
        # par table a déjà laissé passer ce cas (c1b4fb6).
        if _notes_reservees(e, user):
            for ligne in e["audit"]:
                ligne.pop("old_value", None)
                ligne.pop("new_value", None)
                ligne["contenu_reserve"] = True

        # Les notes libres sur une PERSONNE PHYSIQUE sont réservées aux
        # validateurs : c'est du texte sans forme, sur quelqu'un, que rien ne
        # filtre (registre des traitements, point 2 — 08/10/2026).
        e["notes_reservees"] = _notes_reservees(e, user)
        if e["notes_reservees"]:
            e["verdict_note"] = None  # la note de décision est du texte libre aussi
        e["notes"] = [] if e["notes_reservees"] else rows(conn,
            "SELECT id, date, note, source, confidence FROM entity_notes"
            " WHERE entity_id=? ORDER BY date DESC, id DESC",
            (entity_id,))

        e["websites"] = rows(conn,
            "SELECT id, url, status, score, found_by, last_check, http_status, last_scraped"
            " FROM entity_websites WHERE entity_id=? ORDER BY status, score DESC",
            (entity_id,))

        # Avant de RETENIR, le validateur voit ce que des contributeurs ont
        # écrit directement sur la fiche depuis le dernier verdict (arbitrage
        # de Julien, 15/11/2026 : l'écriture directe reste, elle se montre).
        if au_moins(user, "validator"):
            e["ecrit_par_contributeurs"] = rows(conn, f"""
                SELECT al.table_name AS objet, al.action, al.field AS champ,
                       al.old_value AS avant, al.new_value AS apres,
                       u.email AS par, al.at AS le
                FROM audit_log al JOIN users u ON u.id = al.user_id
                WHERE al.entity_id = ? AND {_ECRITS_DE_CONTRIBUTEUR}
                  AND al.id > COALESCE({_DERNIER_VERDICT.format(eid="?")}, 0)
                ORDER BY al.id
            """, (entity_id, entity_id))

        return e
    finally:
        conn.close()

# ─── PUT /api/atelier/entities/{id} — retirée le 15/11/2026 ──────────────────
# Elle réécrivait une fiche sans verrou optimiste, sans valider `confidence` ni
# `type` (500 sur une valeur hors liste), acceptait un nom vide, et ne passait
# ni par les propositions ni par l'origine. Ni l'atelier ni les essais ne
# l'appelaient : l'écran enregistre par PATCH, ci-dessous (audit du 15/11).


def _audit(conn, user_id: int, entity_id: int, field: str, old, new):
    # `is None` et non `or` : un capital à 0 effacé est une modification.
    if str("" if old is None else old) != str("" if new is None else new):
        conn.execute("""
            INSERT INTO audit_log(user_id, entity_id, table_name, action, field, old_value, new_value)
            VALUES(?,?,'entities','update',?,?,?)
        """, (user_id, entity_id, field, str(old) if old is not None else None,
              str(new) if new is not None else None))


# ─── PATCH /api/atelier/entities/{id} — verrou optimiste ──────────────────────
# Édition partielle d'une entité avec protection contre les conflits concurrents.
# Le client envoie updated_at (valeur lue à l'ouverture de la fiche).
# Si la valeur a changé entre-temps → 409 Conflict.

class EntityPatch(BaseModel):
    updated_at:        str             # verrou optimiste obligatoire
    #: Pourquoi on change l'identité d'une fiche institutionnelle (nom, SIREN,
    #: année de naissance) : exigé dans ce seul cas, journalisé avec le changement.
    motif:             Optional[str] = Field(None, max_length=1000)
    # Champs éditables
    name:              Optional[str] = None
    short_name:        Optional[str] = None
    address:           Optional[str] = None
    confidence:        Optional[str] = None
    validation_status: Optional[str] = None
    responsible:       Optional[str] = None
    # person
    firstname:   Optional[str] = None
    lastname:    Optional[str] = None
    birth_year:  Optional[int] = None
    birth_month: Optional[int] = None
    gender:      Optional[str] = None
    # business
    naf_code:        Optional[str] = None
    naf_label:       Optional[str] = None
    legal_form:      Optional[str] = None
    biz_status:      Optional[str] = None
    capital:         Optional[int] = None
    employees_range: Optional[str] = None
    biz_creation:    Optional[str] = None
    closing_date:    Optional[str] = None
    # association
    rna_id:           Optional[str] = None
    asso_object:      Optional[str] = None
    asso_status:      Optional[str] = None
    asso_creation:    Optional[str] = None
    dissolution_date: Optional[str] = None
    # place
    osm_category: Optional[str] = None
    osm_value:    Optional[str] = None
    # service
    svc_category:  Optional[str] = None
    operator:      Optional[str] = None
    opening_hours: Optional[str] = None


#: Champ de la requête → (table, colonne, types d'entité concernés ou None).
_CHAMPS_FICHE = {
    "name": ("entities", "name", None),
    "short_name": ("entities", "short_name", None),
    "address": ("entities", "address", None),
    "confidence": ("entities", "confidence", None),
    "responsible": ("entities", "responsible", None),
    "firstname": ("persons", "firstname", {"person"}),
    "lastname": ("persons", "lastname", {"person"}),
    "birth_year": ("persons", "birth_year", {"person"}),
    "birth_month": ("persons", "birth_month", {"person"}),
    "gender": ("persons", "gender", {"person"}),
    "naf_code": ("businesses", "naf_code", {"business"}),
    "naf_label": ("businesses", "naf_label", {"business"}),
    "legal_form": ("businesses", "legal_form", {"business"}),
    "biz_status": ("businesses", "status", {"business"}),
    "capital": ("businesses", "capital", {"business"}),
    "employees_range": ("businesses", "employees_range", {"business"}),
    "biz_creation": ("businesses", "creation_date", {"business"}),
    "closing_date": ("businesses", "closing_date", {"business"}),
    "rna_id": ("associations", "rna_id", {"association"}),
    "asso_object": ("associations", "object", {"association"}),
    "asso_status": ("associations", "status", {"association"}),
    "asso_creation": ("associations", "creation_date", {"association"}),
    "dissolution_date": ("associations", "dissolution_date", {"association"}),
    "osm_category": ("places", "osm_category", {"place"}),
    "osm_value": ("places", "osm_value", {"place"}),
    "svc_category": ("services", "category", {"service"}),
    "operator": ("services", "operator", {"service"}),
    "opening_hours": ("services", "opening_hours", {"service"}),
}
_CONFIANCES_FICHE = ("verified", "confirmed", "probable", "hypothesis")

_FICHE_COURANTE = """
    SELECT e.*, p.firstname, p.lastname, p.birth_year, p.birth_month, p.gender,
           b.naf_code, b.naf_label, b.legal_form, b.status AS biz_status,
           b.capital, b.employees_range, b.creation_date AS biz_creation, b.closing_date,
           a.rna_id, a.object AS asso_object, a.status AS asso_status,
           a.creation_date AS asso_creation, a.dissolution_date,
           pl.osm_category, pl.osm_value,
           s.category AS svc_category, s.operator, s.opening_hours
    FROM entities e
    LEFT JOIN persons      p  ON p.entity_id  = e.id
    LEFT JOIN businesses   b  ON b.entity_id  = e.id
    LEFT JOIN associations a  ON a.entity_id  = e.id
    LEFT JOIN places       pl ON pl.entity_id = e.id
    LEFT JOIN services     s  ON s.entity_id  = e.id
    WHERE e.id = ?
"""


def _doit_proposer(user: dict, confidence: Optional[str]) -> bool:
    """Un contributeur qui écrit sur un objet PUBLIÉ propose ; il n'écrit pas.

    Arbitré par Julien le 02/10/2026 (cf. `collectors/propositions.py`). Sur un
    objet que la publication ne sort pas, il écrit directement : rien n'en part
    en ligne tant qu'un validateur ne l'a pas rendu publiable — et c'est ce
    jour-là qu'il le relit.
    """
    return not au_moins(user, "validator") and confidence in PUBLIABLES


def _propose(conn, nature: str, object_type: str, object_id: int,
             entity_id: Optional[int], charge: dict, avant: dict, user: dict,
             **reponse) -> _JSONResponse:
    """Enregistre la proposition et répond 202 : reçue, pas appliquée."""
    _propositions.assurer_schema(conn)
    pid, reprises = _propositions.proposer(conn, nature, object_type, object_id, entity_id,
                                           charge, avant, user, rendre_reprises=True)
    conn.commit()
    message = ("Proposition enregistrée : elle sera appliquée quand un "
               "validateur l'aura acceptée.")
    if reprises:
        message += (" Elle reprend aussi ce que vous aviez déjà proposé sur cette "
                    "fiche et qui attendait encore.")
    return _JSONResponse(status_code=202, content={
        "ok": True, "propose": True, "proposition": pid, "reprend": reprises,
        "message": message, **reponse})


#: Ce qui IDENTIFIE une fiche (avis de vigie-securite : prénom, nom et mois de
#: naissance se réécrivent aussi bien que `name`, et `rna_id` est le numéro du
#: registre lui-même). Sur une fiche d'origine explicitement
#: institutionnelle, le changer revient à dire que le registre se trompe : le
#: rôle validateur et un motif journalisé (décision de Julien, 15/11/2026).
#: `siren` n'est pas éditable aujourd'hui (absent de `_CHAMPS_FICHE`) : il est
#: nommé ici pour que l'ouvrir un jour n'ouvre pas aussi cette porte.
_CHAMPS_IDENTITAIRES = ("name", "siren", "firstname", "lastname", "birth_year",
                        "birth_month", "rna_id")


def _changements_fiche(current: dict, valeurs: dict, user: dict,
                       motif: Optional[str] = None) -> list[tuple]:
    """[(champ, ancien, nouveau)] : ce que `valeurs` change VRAIMENT à la fiche.

    Une origine NULL n'est PAS protégée : 3 025 fiches non classées à Lasalle,
    une garde pleine en bloquerait la moitié. Seule `institutionnel`, posé
    explicitement (`collectors/origine.py`), ferme le nom, le SIREN et l'année
    de naissance.
    """
    changements = []
    for champ, nouveau in valeurs.items():
        if champ not in _CHAMPS_FICHE:
            continue
        _, _, types = _CHAMPS_FICHE[champ]
        if types is not None and current["type"] not in types:
            continue
        if isinstance(nouveau, str):
            nouveau = nouveau.strip() or None
        if nouveau is None and champ in ("name", "confidence"):
            raise HTTPException(400, f"« {champ} » ne peut pas être vidé.")
        if champ == "confidence" and nouveau not in _CONFIANCES_FICHE:
            raise HTTPException(400, f"confidence : valeurs admises — "
                                     f"{', '.join(_CONFIANCES_FICHE)}")
        ancien = current.get(champ)
        if str("" if ancien is None else ancien) == str("" if nouveau is None else nouveau):
            continue
        if champ == "confidence":
            exiger(user, "validator", "Trancher la fiabilité d'une fiche")
        if champ in _CHAMPS_IDENTITAIRES and current.get("origine") == INSTITUTIONNEL:
            # Le rôle d'abord : un contributeur reçoit le 403 qui le lui dit,
            # même s'il a oublié le motif (et même sur un objet publié, où il
            # proposerait d'ordinaire : une proposition n'efface pas le registre).
            exiger(user, "validator", "Modifier le nom, le SIREN ou l'année de naissance "
                                      "d'une fiche institutionnelle")
            if not (motif or "").strip():
                raise HTTPException(400, "Modifier le nom, le SIREN ou l'année de naissance "
                                         "d'une fiche institutionnelle demande un motif "
                                         "(champ « motif ») : il sera journalisé.")
        changements.append((champ, ancien, nouveau))
    return changements


def _ecrire_fiche(conn, entity_id: int, changements: list[tuple], user: dict,
                  motif: Optional[str] = None) -> list[str]:
    """Écrit et journalise. Le SEUL écrivain des champs d'une fiche : l'édition
    d'un validateur et l'acceptation d'une proposition passent toutes deux ici.

    Un motif donné pour un changement d'identité (`_CHAMPS_IDENTITAIRES`) se
    journalise en une ligne `action='motif'`, à la suite des changements qu'il
    explique (le champ nomme ceux-ci)."""
    par_table: dict[str, dict] = {}
    for champ, ancien, nouveau in changements:
        table, colonne, _ = _CHAMPS_FICHE[champ]
        _audit(conn, user["id"], entity_id, champ, ancien, nouveau)
        par_table.setdefault(table, {})[colonne] = nouveau
    identitaires = [c for c, _, _ in changements if c in _CHAMPS_IDENTITAIRES]
    if identitaires and (motif or "").strip():
        _journal(conn, user, entity_id, "entities", "motif",
                 ",".join(identitaires), apres=motif.strip())
    for table, valeurs in par_table.items():
        sets = ", ".join(f"{c}=?" for c in valeurs)
        cle = "id" if table == "entities" else "entity_id"
        if table != "entities":
            # 160 fiches de Lasalle (17/09/2026) n'ont pas de ligne de détail :
            # l'UPDATE n'atteignait aucune ligne, et l'objet corrigé d'une
            # association disparaissait derrière « Sauvegardé ✓ ».
            conn.execute(f"INSERT OR IGNORE INTO {table}(entity_id) VALUES(?)",
                         (entity_id,))
        conn.execute(f"UPDATE {table} SET {sets} WHERE {cle}=?",
                     list(valeurs.values()) + [entity_id])
    if par_table:
        conn.execute("UPDATE entities SET updated_at=datetime('now') WHERE id=?",
                     (entity_id,))
    return sorted(c for champs in par_table.values() for c in champs)


@app.patch("/api/atelier/entities/{entity_id}")
def atelier_patch_entity(
    entity_id: int = FPath(..., ge=1),
    req: EntityPatch = ...,
    user=Depends(require_auth),
):
    """
    Mise à jour partielle avec verrou optimiste.

    - Seuls les champs PRÉSENTS dans le corps sont touchés ; `null` ou `""`
      EFFACE la valeur. Avant le 17/09/2026, `null` valait « non fourni » : un
      champ vidé dans l'atelier ne s'effaçait jamais, et la page affichait
      « Sauvegardé ✓ » quand même.
    - Un champ fourni mais inchangé n'écrit rien et ne déplace pas `updated_at`.
    - 409 si `updated_at` reçu ≠ valeur en base, avec qui, quand, et quels champs.
    """
    fournis = (req.model_fields_set if hasattr(req, "model_fields_set")
               else req.__fields_set__) - {"updated_at", "motif"}
    conn = get_db_rw()
    try:
        # Réserver l'écriture avant de lire : la condition et l'écriture forment
        # un seul geste, qu'aucune autre requête ne peut couper en deux.
        conn.execute("BEGIN IMMEDIATE")
        current = row(conn, _FICHE_COURANTE, (entity_id,))
        if not current:
            raise HTTPException(404, "Entité introuvable")

        # ── Verrou optimiste ──────────────────────────────────────────────────
        db_updated_at = current.get("updated_at") or ""
        if req.updated_at != db_updated_at:
            champs = rows(conn, """
                SELECT a.field AS champ, u.email AS par, a.at AS le,
                       a.old_value AS avant, a.new_value AS apres
                FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
                WHERE a.entity_id = ? AND a.table_name = 'entities'
                  AND a.action = 'update' AND a.at > ?
                ORDER BY a.id
            """, (entity_id, req.updated_at or ""))
            # Le journal est au millième comme `updated_at` (déclencheur
            # `audit_log_at_milliemes`) : `>` strict écarte les lignes de
            # l'écriture qui a produit la valeur lue, écrites au plus tard à
            # son horodatage. Avec l'ancienne coupe à la seconde (`>=`), ces
            # lignes revenaient et l'ancienne valeur de l'encadré était fausse.
            # `action = 'update'` : une ligne de motif n'est pas un champ.
            # Un champ, une ligne : de sa valeur lue à sa valeur actuelle. Deux
            # écritures du même champ ne font pas deux lignes à comparer.
            par_champ: dict[str, dict] = {}
            for c in champs:
                if c["champ"] in par_champ:
                    par_champ[c["champ"]].update(par=c["par"], le=c["le"], apres=c["apres"])
                else:
                    par_champ[c["champ"]] = dict(c)
            champs = list(par_champ.values())
            # Le déclencheur `entities_updated_at` déplace `updated_at` à TOUTE
            # écriture, collecte comprise, et une collecte ne journalise rien.
            # Sans champ journalisé depuis la lecture, l'auteur n'est donc pas
            # le dernier humain connu — ce serait accuser quelqu'un à tort.
            if champs:
                # Le journal est à la seconde : à seconde égale, `max` rendait la
                # première ligne lue, parfois l'écriture du demandeur lui-même.
                # Les lignes sont dans l'ordre d'écriture (ORDER BY a.id).
                dernier = champs[-1]
                raise _conflit("Cette fiche a été modifiée pendant que vous l'éditiez.",
                               dernier["par"], dernier["le"], champs=champs,
                               updated_at=db_updated_at)
            raise _conflit("Cette fiche a été mise à jour par la collecte pendant que "
                           "vous l'éditiez.", None, db_updated_at, champs=[],
                           updated_at=db_updated_at)

        if "validation_status" in fournis:
            raise HTTPException(400, "Le verdict d'une fiche ne s'écrit plus dans le formulaire : c'est "
                                 "une décision, posée par les boutons Retenir / Écarter.")
        changements = _changements_fiche(current, {c: getattr(req, c) for c in fournis}, user,
                                         motif=req.motif)
        if changements and _doit_proposer(user, current.get("confidence")):
            return _propose(conn, "fiche", "entity", entity_id, entity_id,
                            {c: n for c, _, n in changements},
                            {c: a for c, a, _ in changements}, user,
                            id=entity_id, modifies=[],
                            proposes=sorted(c for c, _, _ in changements),
                            updated_at=db_updated_at)
        modifies = _ecrire_fiche(conn, entity_id, changements, user, motif=req.motif)
        conn.commit()
        updated = row(conn, "SELECT updated_at FROM entities WHERE id=?", (entity_id,))
        return {"ok": True, "id": entity_id, "modifies": modifies,
                "updated_at": updated["updated_at"] if updated else None}
    finally:
        conn.close()


# ─── Contacts ─────────────────────────────────────────────────────────────────

CONTACT_TYPES = {"website", "phone", "email", "other"}

class ContactCreate(BaseModel):
    type:  str
    value: str
    label: Optional[str] = None

@app.post("/api/atelier/entities/{entity_id}/contacts")
def add_contact(
    entity_id: int = FPath(..., ge=1),
    req: ContactCreate = ...,
    user=Depends(require_auth),
):
    if req.type not in CONTACT_TYPES:
        raise HTTPException(400, f"type invalide — valeurs: {', '.join(CONTACT_TYPES)}")
    if req.type == "website":
        req.value = _url_http(req.value, "site")
    conn = get_db_rw()
    try:
        fiche = row(conn, "SELECT type FROM entities WHERE id=?", (entity_id,))
        if not fiche:
            raise HTTPException(404, "Entité introuvable")
        # Un téléphone ou un courriel d'une PERSONNE physique : même règle que les
        # notes (registre des traitements, point 2), et une trace de qui l'a écrit.
        if _notes_reservees(fiche, user):
            exiger(user, "validator", "Ajouter un contact à une personne physique")
        r = conn.execute(
            "INSERT INTO contacts(entity_id, type, value, label) VALUES(?,?,?,?)",
            (entity_id, req.type, req.value.strip(), req.label),
        )
        _journal(conn, user, entity_id, "contacts", "create", f"contact/{r.lastrowid}",
                 apres=f"{req.type}")
        conn.commit()
        return {"ok": True, "id": r.lastrowid, "entity_id": entity_id,
                "type": req.type, "value": req.value.strip(), "label": req.label}
    finally:
        conn.close()

@app.delete("/api/atelier/contacts/{contact_id}")
def delete_contact(contact_id: int = FPath(..., ge=1), user=Depends(require_au_moins("validator", "Supprimer un contact"))):
    conn = get_db_rw()
    try:
        if not row(conn, "SELECT 1 FROM contacts WHERE id=?", (contact_id,)):
            raise HTTPException(404, "Contact introuvable")
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

# ─── Relations atelier ────────────────────────────────────────────────────────

RELATION_TYPES = [
    "dirigeant", "gérant", "associé", "président", "trésorier", "secrétaire", "membre",
    "élu_cm", "élu_cc", "candidat", "agent_communal", "membre_commission",
    "locataire_commune", "bailleur_commune", "subventionné", "prestataire",
    "famille_présumé", "époux_présumé", "enfant_présumé", "proche_présumé",
    "même_adresse", "même_lieu_dit",
]

# Un lien PRÉSUMÉ entre deux noms se lit et s'écrit par qui peut le trancher
# (`/api/candidates`, registre des traitements point 2). La fiche les rendait
# pourtant à tout compte (audit du 15/11/2026).
PRESUMES = tuple(t for t in RELATION_TYPES if t.endswith("_présumé"))
_SANS_PRESUMES = (" AND r.relation_type NOT IN ("
                  + ", ".join("'" + t + "'" for t in PRESUMES) + ")")


def _relation_d_autrui(conn, rel_id: int, user: dict) -> bool:
    """Vrai si la relation n'a PAS été créée à l'atelier par ce compte : posée
    par un autre, ou venue d'un collecteur (aucune création au journal). Un
    contributeur ne modifie que les siennes, comme ses notes."""
    auteur = conn.execute(
        "SELECT user_id FROM audit_log WHERE table_name='relations' AND action='create' "
        "AND field='relation_id' AND new_value=? ORDER BY id LIMIT 1",
        (str(rel_id),)).fetchone()
    return not auteur or auteur[0] != user["id"]


class RelationCreate(BaseModel):
    direction:       str            # "from" = entity → cible, "to" = cible → entity
    other_entity_id: int
    relation_type:   str
    since:      Optional[str] = None
    until:      Optional[str] = None
    source:     str = "manual"
    confidence: str = "verified"

class RelationUpdate(BaseModel):
    relation_type: Optional[str] = None
    since:         Optional[str] = None
    until:         Optional[str] = None
    source:        Optional[str] = None
    confidence:    Optional[str] = None

def _relation_enriched(conn, rel_id: int):
    return row(conn, """
        SELECT r.id, r.relation_type, r.since, r.until, r.source, r.confidence,
               f.id AS from_id, f.name AS from_name, f.type AS from_type,
               t.id AS to_id,   t.name AS to_name,   t.type AS to_type
        FROM relations r
        JOIN entities f ON f.id = r.from_id
        JOIN entities t ON t.id = r.to_id
        WHERE r.id = ?
    """, (rel_id,))

_FORME_DATE_LIEN = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def _date_de_lien(valeur: Optional[str], nom: str) -> Optional[str]:
    """Une date de lien : AAAA, AAAA-MM ou AAAA-MM-JJ, réelle, entre 1800 et 2100.

    Vide → None. Le champ était libre : `since="n'importe quoi"` s'écrivait, et
    `until` avant `since` aussi (audit du 15/11/2026).
    """
    v = (valeur or "").strip()
    if not v:
        return None
    m = _FORME_DATE_LIEN.match(v)
    ok = bool(m) and 1800 <= int(m.group(1)) <= 2100
    if ok and m.group(2):
        try:
            datetime.strptime(v, "%Y-%m-%d" if m.group(3) else "%Y-%m")
        except ValueError:
            ok = False
    if not ok:
        raise HTTPException(400, f"« {nom} » : date attendue AAAA, AAAA-MM ou AAAA-MM-JJ "
                                 f"(entre 1800 et 2100), reçu « {v[:40]}{'…' if len(v) > 40 else ''} ».")
    return v


def _dates_de_lien_coherentes(since: Optional[str], until: Optional[str]) -> None:
    """`until` ne précède pas `since`. Comparés sur leur partie commune : « 2020 »
    et « 2020-06 » ne se contredisent pas (juin est dans 2020)."""
    if since and until:
        n = min(len(since), len(until))
        if until[:n] < since[:n]:
            raise HTTPException(400, f"« until » ({until}) précède « since » ({since}).")


@app.post("/api/atelier/entities/{entity_id}/relations")
def add_relation(
    entity_id: int = FPath(..., ge=1),
    req: RelationCreate = ...,
    user=Depends(require_auth),
):
    if req.relation_type not in RELATION_TYPES:
        raise HTTPException(400, "relation_type invalide")
    if req.relation_type in PRESUMES:
        exiger(user, "validator", "Poser un lien présumé entre deux personnes")
    if req.confidence in PUBLIABLES:
        exiger(user, "validator", "Ajouter une relation publiable (verified)")
    if req.confidence not in ("verified", "probable", "hypothesis"):
        raise HTTPException(400, "confidence invalide")
    if req.direction not in ("from", "to"):
        raise HTTPException(400, "direction doit être 'from' ou 'to'")
    if req.other_entity_id == entity_id:
        raise HTTPException(400, "Un lien relie deux fiches différentes : on ne relie "
                                 "pas une fiche à elle-même.")
    if not au_moins(user, "validator") and origine_de(req.source) in (INSTITUTIONNEL, VERBATIM):
        # Une source dit d'où vient le lien : « sirene » ou « cm » fait passer
        # pour attesté par un registre ou un procès-verbal ce qu'un contributeur
        # affirme. Le défaut « manual » et une source libre restent permis.
        exiger(user, "validator", "Citer comme source un registre ou un procès-verbal")
    since = _date_de_lien(req.since, "since")
    until = _date_de_lien(req.until, "until")
    _dates_de_lien_coherentes(since, until)

    from_id = entity_id          if req.direction == "from" else req.other_entity_id
    to_id   = req.other_entity_id if req.direction == "from" else entity_id

    conn = get_db_rw()
    try:
        # Les DEUX bouts : seul l'autre était vérifié, et une fiche d'origine
        # inexistante tombait dans le `except` ci-dessous, rendue en « existe déjà ».
        if not row(conn, "SELECT 1 FROM entities WHERE id=?", (entity_id,)):
            raise HTTPException(404, "Entité introuvable")
        if not row(conn, "SELECT 1 FROM entities WHERE id=?", (req.other_entity_id,)):
            raise HTTPException(404, "Entité cible introuvable")
        # Même couple, même type, mêmes dates : un second lien qui ne diffère que
        # par `source` double l'information sans rien dire de plus. NULL et ""
        # se valent (le formulaire envoie "").
        deja = row(conn, """
            SELECT id FROM relations
            WHERE from_id=? AND to_id=? AND relation_type=?
              AND COALESCE(since,'')=? AND COALESCE(until,'')=?
        """, (from_id, to_id, req.relation_type, since or "", until or ""))
        if deja:
            raise HTTPException(409, {
                "message": "Ce lien existe déjà (même couple, même type, mêmes dates) : "
                           "la source n'en fait pas un autre lien.",
                "relation_id": deja["id"]})
        # Le déclencheur écarterait l'écriture sans rien dire, et l'on
        # journaliserait une création qui n'a pas eu lieu.
        refus = refus_de_relation(conn, from_id, to_id, req.relation_type)
        if refus:
            raise HTTPException(409, {
                "message": "Ce lien a été supprimé et refusé par un validateur : "
                           "le refus doit être levé avant de le reposer.",
                "refus_id": refus["id"]})
        try:
            r = conn.execute("""
                INSERT INTO relations(from_id, to_id, relation_type, since, until, source, confidence)
                VALUES(?,?,?,?,?,?,?)
            """, (from_id, to_id, req.relation_type, since, until, req.source, req.confidence))
            if r.rowcount != 1:      # refus posé entre la vérification et l'écriture
                raise HTTPException(409, "Ce lien vient d'être refusé par un validateur.")
            rel_id = r.lastrowid
        except sqlite3.IntegrityError as e:
            # Seule l'unicité (couple + type + source) se dit « existe déjà » ;
            # toute autre contrainte est un défaut, pas un doublon.
            if "UNIQUE" not in str(e):
                raise
            raise HTTPException(409, "Cette relation existe déjà (même type + source)")
        conn.execute("""
            INSERT INTO audit_log(user_id, entity_id, table_name, action, field, new_value)
            VALUES(?,?,'relations','create','relation_id',?)
        """, (user["id"], entity_id, str(rel_id)))
        conn.commit()
        return _relation_enriched(conn, rel_id)
    finally:
        conn.close()

@app.put("/api/atelier/relations/{rel_id}")
def update_relation(
    rel_id: int = FPath(..., ge=1),
    req: RelationUpdate = ...,
    user=Depends(require_auth),
):
    conn = get_db_rw()
    try:
        rel = row(conn, "SELECT * FROM relations WHERE id=?", (rel_id,))
        if not rel:
            raise HTTPException(404, "Relation introuvable")
        # La création refusait un type ou une fiabilité inconnus ; la
        # modification, non — on créait `membre`, puis on écrivait n'importe quoi.
        if req.relation_type is not None and req.relation_type not in RELATION_TYPES:
            raise HTTPException(400, "relation_type invalide")
        if req.confidence is not None and req.confidence not in ("verified", "probable",
                                                                 "hypothesis"):
            raise HTTPException(400, "confidence invalide")
        if req.confidence is not None and req.confidence != rel.get("confidence"):
            exiger(user, "validator", "Changer la fiabilité d'une relation")
        if rel.get("relation_type") in PRESUMES or req.relation_type in PRESUMES:
            exiger(user, "validator", "Modifier un lien présumé entre deux personnes")
        if req.source is not None and str(rel.get("source") or "") != req.source:
            # La source dit d'où vient le lien : la réécrire fait passer pour
            # attesté ce qui ne l'est pas. Refusé avant toute proposition.
            exiger(user, "validator", "Réécrire la source d'une relation")
        valeurs = {f: getattr(req, f) for f in _CHAMPS_RELATION if getattr(req, f) is not None}
        for f in ("since", "until"):
            if f in valeurs:
                valeurs[f] = _date_de_lien(valeurs[f], f) or ""
        _dates_de_lien_coherentes(valeurs.get("since", rel.get("since")),
                                  valeurs.get("until", rel.get("until")))
        changes = {f: v for f, v in valeurs.items() if str(rel.get(f) or "") != str(v)}
        if "relation_type" in changes and refus_de_relation(
                conn, rel["from_id"], rel["to_id"], changes["relation_type"]):
            raise HTTPException(409, "Ce type de lien entre ces deux fiches a été refusé "
                                     "par un validateur : le refus doit être levé d'abord.")
        if changes and _doit_proposer(user, rel.get("confidence")):
            return _propose(conn, "relation", "relation", rel_id, rel["from_id"], changes,
                            {f: rel.get(f) for f in changes}, user,
                            relation=_relation_enriched(conn, rel_id))
        # Écrire directement (relation non publiée) : un contributeur ne touche
        # que celles qu'il a posées. Proposer, lui, reste ouvert (ci-dessus).
        if changes and not au_moins(user, "validator") and _relation_d_autrui(conn, rel_id, user):
            exiger(user, "validator", "Modifier une relation que vous n'avez pas posée")
        _ecrire_relation(conn, rel, valeurs, user)
        conn.commit()
        return _relation_enriched(conn, rel_id)
    finally:
        conn.close()


_CHAMPS_RELATION = ("relation_type", "since", "until", "source", "confidence")


def _ecrire_relation(conn, rel: dict, valeurs: dict, user: dict) -> None:
    """N'écrit que ce qui change. Réécrire `relation_type` inchangé déclenchait
    `relations_refus_respecte_maj` sur le frère d'un lien refusé : l'UPDATE
    entier était avalé, le journal disait la fiabilité changée et elle ne
    l'était pas (second avis de vigie-securite). Ligne non écrite → 409, et
    l'appelant ne valide rien : ni changement ni journal."""
    changes = {f: v for f, v in valeurs.items() if str(rel.get(f) or "") != str(v)}
    if not changes:
        return
    cur = conn.execute(f"UPDATE relations SET {', '.join(f'{f}=?' for f in changes)} WHERE id=?",
                       list(changes.values()) + [rel["id"]])
    if cur.rowcount != 1:
        conn.rollback()
        raise HTTPException(409, "La relation n'a pas pu être modifiée : un refus "
                                 "mémorisé ou une écriture concurrente l'en empêche.")
    for field, val in changes.items():
        old = rel.get(field)
        conn.execute("""
            INSERT INTO audit_log(user_id, entity_id, table_name, action, field, old_value, new_value)
            VALUES(?,?,'relations','update',?,?,?)
        """, (user["id"], rel["from_id"], field, str(old) if old else None, str(val)))

@app.delete("/api/atelier/relations/{rel_id}")
def delete_relation(rel_id: int = FPath(..., ge=1),
                    motif: Optional[str] = Query(None, max_length=500),
                    user=Depends(require_au_moins("validator", "Supprimer une relation"))):
    """Supprime le lien, le journalise entier, et — s'il vient d'un collecteur —
    le refuse : la collecte suivante ne le recrée plus (cf. `relations_refusees`
    dans le schéma). Un lien posé à l'atelier n'est pas refusé : aucune collecte
    ne le réécrit, et le reposer reste un geste humain ordinaire."""
    conn = get_db_rw()
    try:
        # Avant toute écriture : `executescript` valide la transaction en cours.
        assurer_refus(conn)
        rel = row(conn, "SELECT * FROM relations WHERE id=?", (rel_id,))
        if not rel:
            raise HTTPException(404, "Relation introuvable")
        motif = (motif or "").strip() or None
        # Venu d'un collecteur = aucune création à l'atelier au journal (même
        # critère que `_relation_d_autrui`). Un lien présumé a son circuit.
        cree_a_l_atelier = row(conn, "SELECT 1 FROM audit_log WHERE table_name='relations' "
                                     "AND action='create' AND field='relation_id' "
                                     "AND new_value=?", (str(rel_id),))
        a_refuser = not cree_a_l_atelier and rel["relation_type"] not in PRESUMES
        # Le refus qui suit dure : la collecte ne réécrira plus ce lien. Sans
        # motif, le validateur qui le relira (file « refus contredits ») ne
        # saura pas pourquoi une source qui le redit a tort (décision de
        # Julien, 15/11/2026, une fois l'écran capable de le demander).
        if a_refuser and not motif:
            raise HTTPException(400, "Ce lien vient d'un collecteur : le supprimer le "
                                     "refuse pour les collectes suivantes. Dites pourquoi "
                                     "(motif).")
        # Le lien ENTIER, pas son seul identifiant : de quoi le rétablir à la main
        # (les deux bouts, le type, les dates, la source, la fiabilité).
        lien = json.dumps(
            {k: rel.get(k) for k in ("id", "from_id", "to_id", "relation_type", "since",
                                     "until", "source", "confidence", "metadata")},
            ensure_ascii=False)
        conn.execute("""
            INSERT INTO audit_log(user_id, entity_id, table_name, action, field, old_value,
                                  new_value)
            VALUES(?,?,'relations','delete','relation_id',?,?)
        """, (user["id"], rel["from_id"], lien, motif))
        refus_id = None
        if a_refuser:
            deja = refus_de_relation(conn, rel["from_id"], rel["to_id"], rel["relation_type"])
            if deja:
                refus_id = deja["id"]
            else:
                refus_id = conn.execute("""
                    INSERT INTO relations_refusees(from_id, to_id, relation_type, relation,
                                                   motif, refuse_par)
                    VALUES(?,?,?,?,?,?)
                """, (rel["from_id"], rel["to_id"], rel["relation_type"], lien, motif,
                      user["id"])).lastrowid
                _journal(conn, user, rel["from_id"], "relations_refusees", "create",
                         f"refus/{refus_id}", apres=json.dumps(
                             {"relation": json.loads(lien), "motif": motif},
                             ensure_ascii=False))
        conn.execute("DELETE FROM relations WHERE id=?", (rel_id,))
        # Le même lien dit par une autre source reste en ligne : le rendre, pour
        # que le validateur ne croie pas le lien retiré.
        freres = rows(conn, "SELECT id, source, confidence FROM relations WHERE from_id=? "
                            "AND to_id=? AND relation_type=? ORDER BY id",
                      (rel["from_id"], rel["to_id"], rel["relation_type"]))
        conn.commit()
        return {"ok": True, "refus_id": refus_id,
                "encore_presents": [r["id"] for r in freres],
                # Par quelle source : l'écran le dit, sans quoi on croit le
                # lien retiré alors qu'il reste en ligne.
                "sources_restantes": freres}
    finally:
        conn.close()


@app.get("/api/atelier/relations/refus")
def lister_refus(entity_id: Optional[int] = Query(None, ge=1),
                 user=Depends(require_au_moins("validator", "Lire les relations refusées"))):
    """Les liens supprimés que la collecte ne recrée plus : qui, quand, pourquoi,
    et combien de fois une source a tenté de les réécrire depuis."""
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                            " AND name='relations_refusees'").fetchone():
            return {"refus": []}
        filtre = "WHERE x.from_id = ? OR x.to_id = ?" if entity_id else ""
        lignes = rows(conn, f"""
            SELECT x.id, x.from_id, f.name AS from_name, x.to_id, t.name AS to_name,
                   x.relation_type, x.relation, x.motif, u.email AS refuse_par,
                   x.refuse_le, x.tentatives, x.derniere_tentative, x.derniere_source,
                   r.email AS revu_par, x.revu_le
            FROM relations_refusees x
            JOIN entities f ON f.id = x.from_id
            JOIN entities t ON t.id = x.to_id
            LEFT JOIN users u ON u.id = x.refuse_par
            LEFT JOIN users r ON r.id = x.revu_par
            {filtre}
            ORDER BY x.refuse_le DESC, x.id DESC
        """, (entity_id, entity_id) if entity_id else ())
        for ligne in lignes:
            try:
                ligne["relation"] = json.loads(ligne["relation"])
            except (TypeError, ValueError):
                pass
            ligne["sources"] = rows(conn, """
                SELECT source, premiere, derniere, tentatives,
                       premiere > COALESCE(?, '') AS nouvelle
                FROM relations_refus_sources WHERE refus_id = ? ORDER BY premiere
            """, (ligne["revu_le"], ligne["id"]))
        return {"refus": lignes}
    finally:
        conn.close()


@app.post("/api/atelier/relations/refus/{refus_id}/garder")
def garder_refus(refus_id: int = FPath(..., ge=1),
                 motif: Optional[str] = Query(None, max_length=500),
                 user=Depends(require_au_moins("validator", "Garder le refus d'une relation"))):
    """Le refus est revu et tient : les sources qui le redisent à ce jour sont
    tenues pour vues, la file « refus-contredits » ne le montre plus tant
    qu'aucune source nouvelle ne le redit. Un motif donné ici remplit celui
    d'un refus qui n'en avait pas ; il ne remplace pas un motif existant."""
    conn = get_db_rw()
    try:
        assurer_refus(conn)
        refus = row(conn, "SELECT * FROM relations_refusees WHERE id=?", (refus_id,))
        if not refus:
            raise HTTPException(404, "Refus introuvable")
        motif = (motif or "").strip() or None
        nouveau_motif = refus["motif"] if (refus["motif"] or "").strip() else motif
        conn.execute("UPDATE relations_refusees SET revu_par=?, motif=?,"
                     " revu_le=strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id=?",
                     (user["id"], nouveau_motif, refus_id))
        apres = row(conn, "SELECT * FROM relations_refusees WHERE id=?", (refus_id,))
        _journal(conn, user, refus["from_id"], "relations_refusees", "update",
                 f"refus/{refus_id}", avant=json.dumps(refus, ensure_ascii=False),
                 apres=json.dumps({"revu_le": apres["revu_le"], "motif": nouveau_motif,
                                   "motif_de_la_revue": motif}, ensure_ascii=False))
        conn.commit()
        return {"ok": True, "revu_le": apres["revu_le"], "motif": nouveau_motif}
    finally:
        conn.close()


@app.delete("/api/atelier/relations/refus/{refus_id}")
def lever_refus(refus_id: int = FPath(..., ge=1),
                motif: Optional[str] = Query(None, max_length=500),
                user=Depends(require_au_moins("validator", "Lever le refus d'une relation"))):
    """Lève le refus : la collecte suivante peut réécrire le lien. Rien n'est
    recréé ici — le lien revient par sa source, ou ne revient pas."""
    # Tenu ici, pas seulement à l'écran (avis de vigie-securite) : le refus
    # avait un motif, le lever en demande un.
    if not (motif or "").strip():
        raise HTTPException(400, "Lever un refus demande de dire pourquoi (motif).")
    conn = get_db_rw()
    try:
        assurer_refus(conn)
        refus = row(conn, "SELECT * FROM relations_refusees WHERE id=?", (refus_id,))
        if not refus:
            raise HTTPException(404, "Refus introuvable")
        _journal(conn, user, refus["from_id"], "relations_refusees", "delete",
                 f"refus/{refus_id}", avant=json.dumps(refus, ensure_ascii=False),
                 apres=(motif or "").strip() or None)
        conn.execute("DELETE FROM relations_refusees WHERE id=?", (refus_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ─── Propositions ─────────────────────────────────────────────────────────────
# Ce qu'un contributeur a voulu écrire sur un objet publié (cf.
# `collectors/propositions.py`). Accepter REJOUE la proposition par l'écrivain
# ordinaire de l'objet, au nom du validateur qui accepte — il n'y a pas de
# second chemin d'écriture, donc pas de seconde règle à tenir.

class DecisionProposition(BaseModel):
    accepter: bool
    motif: str = ""
    #: Une ligne extraite (`_EXTRAITS`) peut être CORRIGÉE en l'acceptant :
    #: le validateur lit l'acte, et c'est lui qui a le dernier mot sur ce qu'il
    #: dit. Refusé pour toute autre nature : celles-là portent déjà ce que le
    #: contributeur voulait écrire, et le corriger serait écrire à sa place.
    corrections: dict = {}
    #: Ce que devient un SIGNALEMENT accepté (`ISSUES_SIGNALEMENT`) : écarter
    #: la fiche, la marquer à revoir — les verdicts que le validateur pose
    #: déjà —, ou, pour un doublon, « garder, fusion à faire ». Refusé ailleurs.
    issue: Optional[str] = None


def _valeurs_actuelles(conn, prop: dict) -> Optional[dict]:
    """Ce que valent AUJOURD'HUI les champs visés : entre la proposition et sa
    relecture, la collecte ou un autre éditeur ont pu passer. None : l'objet
    n'existe plus."""
    champs = list(prop["charge"])
    if prop["nature"] in _EXTRAITS:
        # Une ligne extraite ne remplace aucune valeur en place : ce qui peut
        # avoir disparu, c'est l'acte d'où elle a été lue.
        # Retrouvé par sa clé datée s'il a été renuméroté depuis le dépôt.
        return {} if _marches_extraits.acte_de_proposition(conn, prop) else None
    if prop["nature"] == "signalement":
        # Un signalement ne remplace aucune valeur : ce qui compte, c'est que
        # la fiche existe encore, et le verdict qu'elle porte aujourd'hui.
        if not row(conn, "SELECT 1 AS ok FROM entities WHERE id=?", (prop["object_id"],)):
            return None
        return {"verdict": _verdict_de_fiche(conn, prop["object_id"])}
    if prop["nature"] == "fiche":
        ligne = row(conn, _FICHE_COURANTE, (prop["object_id"],))
    elif prop["nature"] == "coords":
        ligne = row(conn, "SELECT lat, lng FROM entities WHERE id=?", (prop["object_id"],))
    elif prop["nature"] == "relation":
        ligne = row(conn, "SELECT * FROM relations WHERE id=?", (prop["object_id"],))
    else:
        a = row(conn, "SELECT corrections FROM annotations WHERE object_type=? AND object_id=?",
                (prop["object_type"], prop["object_id"]))
        ligne = parse_json_field(a["corrections"], {}) if a else {}
    if ligne is None:
        return None
    return {c: ligne.get(c) for c in champs}


def _acte_sous_les_yeux(conn, prop: dict) -> Optional[dict]:
    """L'acte d'où une ligne extraite a été lue, et ce qu'il faut pour la juger :
    sa pièce, et le passage où la citation s'y lit, avec son contexte. Relire
    une extraction sans l'acte, c'est faire confiance à l'outil — ce que la
    relecture devait justement cesser de faire.

    Le passage, pas le texte entier : la file pesait environ 2 Mo pour les
    161 lignes du rapport de Lasalle (relecture du 05/10/2026)."""
    acte = _marches_extraits.acte_de_proposition(conn, prop)
    if not acte:
        return None
    sortie = {k: acte.get(k) for k in ("id", "type", "date", "title", "source_url",
                                         "raw_document_id", "cle_acte")}
    sortie["extrait"] = _marches_extraits.extrait_de(
        conn, acte, (prop.get("charge") or {}).get("citation") or "")
    return sortie


@app.get("/api/atelier/propositions")
def atelier_propositions(etat: str = "en_attente", nature: Optional[str] = None,
                         issue: Optional[str] = None,
                         limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                         user=Depends(require_auth)):
    """Une page de la file des propositions (`limit` ≤ 100, `total` dans la
    réponse). Un contributeur n'y voit que les siennes.

    `?etat=acceptee&issue=fusion_a_faire` : les fusions demandées, que
    l'atelier ne fait pas lui-même (décision de Julien, 16/11/2026)."""
    if issue is not None and issue not in _propositions.ISSUES_SIGNALEMENT:
        raise HTTPException(400, "issue invalide — valeurs : "
                                 + ", ".join(_propositions.ISSUES_SIGNALEMENT))
    if etat not in _propositions.ETATS:
        raise HTTPException(400, f"etat invalide — valeurs : {', '.join(_propositions.ETATS)}")
    if nature is not None and nature not in _propositions.NATURES:
        raise HTTPException(400, f"nature invalide — valeurs : {', '.join(_propositions.NATURES)}")
    conn = get_db_rw()
    try:
        _propositions.assurer_schema(conn)
        conn.commit()
        auteur = None if au_moins(user, "validator") else user["id"]
        total = _propositions.compter(conn, etat, auteur, nature=nature, issue=issue)
        lignes = _propositions.lister(
            conn, etat, auteur, limit=limit, offset=offset, nature=nature, issue=issue,
            natures_lues=tuple(_EXTRAITS))
        for p in lignes:
            p["actuel"] = _valeurs_actuelles(conn, p) if etat == "en_attente" else None
            if p["nature"] == "signalement" and (p["charge"] or {}).get("doublon_de"):
                p["doublon"] = row(conn, "SELECT id, name, type FROM entities WHERE id=?",
                                   (p["charge"]["doublon_de"],))
            if p["nature"] in _EXTRAITS:
                p["acte"] = _acte_sous_les_yeux(conn, p)
                # Calculés à la lecture, pas au dépôt : un marché accepté hier
                # doit apparaître comme doublon possible de la ligne d'aujourd'hui.
                if etat == "en_attente":
                    p["doublons"] = _EXTRAITS[p["nature"]].doublons(conn, p["charge"])
        return {"propositions": lignes, "total": total, "offset": offset, "limit": limit,
                "peut_trancher": au_moins(user, "validator"),
                "corrigeables": {n: list(m.CORRIGEABLES) for n, m in _EXTRAITS.items()}}
    finally:
        conn.close()


def _exiger_a_jour(conn, prop: dict, actuel: dict) -> None:
    """409 si un champ visé a changé depuis que la proposition a été faite.

    Une proposition porte ce que son auteur avait sous les yeux (`avant`).
    L'accepter sans comparer écrasait une correction posée entre-temps par un
    validateur, sans qu'aucun des deux le sache (audit du 15/11/2026). Le
    validateur refuse alors la proposition en le disant, et l'auteur repropose
    sur la fiche à jour.
    """
    def meme(a, b):
        # Même règle que `_audit` et `_changements_fiche` : NULL et "" se
        # valent, et 1000 vaut "1000" — sans quoi une valeur inchangée
        # bloquerait l'acceptation.
        return str("" if a is None else a) == str("" if b is None else b)

    avant = prop.get("avant") or {}
    champs = [{"champ": c, "avant": avant[c], "apres": actuel.get(c)}
              for c in prop.get("charge") or {}
              if c in avant and not meme(actuel.get(c), avant[c])]
    if champs:
        # L'auteur du CHAMP qui a changé, pas de la dernière écriture sur la
        # fiche. Une relation n'a pas de journal par champ : on ne nomme
        # personne plutôt que d'accuser à tort.
        if prop["nature"] == "fiche":
            par, le = _dernier_auteur(conn, prop["entity_id"], champs[0]["champ"])
        elif prop["nature"] == "coords":
            par, le = _dernier_auteur(conn, prop["entity_id"], "coords")
        else:
            par, le = None, None
        raise _conflit("Ce que la proposition corrige a changé depuis qu'elle a été "
                       "faite : elle ne peut plus être acceptée telle quelle. "
                       "Refusez-la en le disant ; son auteur repartira de la "
                       "fiche à jour.", par, le, champs=champs)


@app.post("/api/atelier/propositions/{proposition_id}/decision")
def atelier_trancher_proposition(
    proposition_id: int = FPath(..., ge=1),
    req: DecisionProposition = ...,
    user=Depends(require_au_moins("validator", "Accepter ou refuser une proposition")),
):
    if not req.accepter and not req.motif.strip():
        raise HTTPException(400, "Refuser demande d'expliquer pourquoi : sans motif, la "
                                 "personne qui a proposé ne saura pas quoi corriger.")
    conn = get_db_rw()
    try:
        _propositions.assurer_schema(conn)
        conn.commit()
        prop = _propositions.lire(conn, proposition_id)
        if not prop:
            raise HTTPException(404, "Proposition introuvable.")
        if prop["etat"] != "en_attente":
            raise HTTPException(409, f"Cette proposition n'attend plus : {prop['etat']}"
                                     + (f" par {prop['tranche_par']}." if prop["tranche_par"] else "."))
        if req.corrections and not (req.accepter and prop["nature"] in _EXTRAITS):
            raise HTTPException(400, "Seule une ligne extraite se corrige en l'acceptant.")
        if req.issue is not None and not (req.accepter and prop["nature"] == "signalement"):
            raise HTTPException(400, "Seul un signalement accepté porte une issue.")
        motif = req.motif
        issue = None
        if req.accepter and prop["nature"] == "signalement":
            issue = req.issue
            if issue not in _propositions.ISSUES_SIGNALEMENT:
                raise HTTPException(400, "Accepter un signalement, c'est écarter la fiche "
                                         "(« ecarte »), la marquer à revoir (« a_revoir »), "
                                         "ou, pour un doublon, la garder en demandant la "
                                         "fusion (« fusion_a_faire ») : dire lequel.")
            if not row(conn, "SELECT 1 AS ok FROM entities WHERE id=?", (prop["object_id"],)):
                raise HTTPException(404, "La fiche visée n'existe plus.")
            doublon_de = (prop["charge"] or {}).get("doublon_de")
            if issue == _propositions.FUSION_A_FAIRE:
                if prop["charge"].get("motif") != "doublon" or not doublon_de:
                    raise HTTPException(400, "« Garder, fusion à faire » ne répond qu'à un "
                                             "signalement de doublon.")
                if not row(conn, "SELECT 1 AS ok FROM entities WHERE id=?", (doublon_de,)):
                    raise HTTPException(409, "La fiche désignée comme doublon n'existe plus : "
                                             "il n'y a rien à fusionner.")
            # Même ordre que pour une correction : `_decider` tient sa propre
            # transaction ; le verdict est posé d'abord, la proposition close
            # ensuite. Le verdict déjà en place ne s'écrit pas deux fois. La
            # fusion à faire, elle, ne touche pas la fiche.
            elif _verdict_de_fiche(conn, prop["object_id"]) != issue:
                _decider("entity", prop["object_id"],
                         AnnotationUpdate(review_status=issue), user)
            motif = ({"ecarte": "fiche écartée", "a_revoir": "fiche à revoir",
                      "fusion_a_faire": f"fiche gardée, fusion à faire avec la fiche n° {doublon_de}"}[issue]
                     + (f" — {req.motif.strip()}" if req.motif.strip() else ""))
        if req.accepter and prop["nature"] in _EXTRAITS:
            extraits = _EXTRAITS[prop["nature"]]
            if _valeurs_actuelles(conn, prop) is None:
                raise HTTPException(409, "L'acte d'où la ligne a été lue n'existe plus "
                                         "dans cette base : elle ne peut qu'être écartée.")
            try:
                saisie = extraits.saisie_de(conn, prop, req.corrections, user)
            except _marches_extraits.LigneRefusee as e:
                raise HTTPException(400, str(e))
            # Même ordre que pour une correction : la saisie est écrite et
            # rejouée d'abord, la proposition close ensuite. `import_saisies`
            # ouvre sa propre transaction ; la tenir ici l'aurait bloqué.
            _saisies.ajouter(saisie)
            _saisies.import_saisies()
            if req.corrections:
                motif = (f"corrigée : {', '.join(sorted(req.corrections))}"
                         + (f" — {req.motif.strip()}" if req.motif.strip() else ""))
        if req.accepter and prop["nature"] == "correction":
            # `_decider` ouvre sa propre transaction : la correction est posée
            # d'abord, la proposition close ensuite. Rejouer une correction déjà
            # posée n'écrit rien de plus — l'ordre inverse perdrait la correction.
            _decider(prop["object_type"], prop["object_id"],
                     AnnotationUpdate(corrections=prop["charge"]), user)
        conn.execute("BEGIN IMMEDIATE")
        if req.accepter and prop["nature"] not in ("correction", "signalement") \
                and prop["nature"] not in _EXTRAITS:
            oid, charge = prop["object_id"], prop["charge"]
            if prop["nature"] == "fiche":
                current = row(conn, _FICHE_COURANTE, (oid,))
                if not current:
                    raise HTTPException(404, "La fiche visée n'existe plus.")
                _exiger_a_jour(conn, prop, current)
                # Le motif du validateur qui accepte vaut motif du changement :
                # une fiche reclassée institutionnelle depuis la proposition
                # l'exige, et le refus du motif (400) laisse la proposition.
                _ecrire_fiche(conn, oid,
                              _changements_fiche(current, charge, user, motif=req.motif),
                              user, motif=req.motif)
            elif prop["nature"] == "coords":
                old = row(conn, "SELECT lat, lng FROM entities WHERE id=?", (oid,))
                if not old:
                    raise HTTPException(404, "La fiche visée n'existe plus.")
                _exiger_a_jour(conn, prop, old)
                _ecrire_coords(conn, oid, charge["lat"], charge["lng"], old, user)
            else:
                rel = row(conn, "SELECT * FROM relations WHERE id=?", (oid,))
                if not rel:
                    raise HTTPException(404, "La relation visée n'existe plus.")
                _exiger_a_jour(conn, prop, rel)
                # Refusé depuis la proposition : le dire, plutôt que de
                # journaliser un changement que le déclencheur avalerait.
                if charge.get("relation_type") and refus_de_relation(
                        conn, rel["from_id"], rel["to_id"], charge["relation_type"]):
                    raise HTTPException(409, "Ce type de lien entre ces deux fiches a été "
                                             "refusé par un validateur depuis la proposition.")
                _ecrire_relation(conn, rel, charge, user)
        _propositions.clore(conn, proposition_id, "acceptee" if req.accepter else "refusee",
                            user, motif)
        if issue:
            conn.execute("UPDATE propositions SET issue=? WHERE id=?", (issue, proposition_id))
        if issue == _propositions.FUSION_A_FAIRE:
            # Au journal des DEUX fiches : la fusion demandée se lit dans
            # l'historique de l'une comme de l'autre.
            demande = json.dumps({"fiche": prop["object_id"], "doublon_de": doublon_de,
                                  "proposition": proposition_id}, ensure_ascii=False)
            for eid in (prop["object_id"], doublon_de):
                _journal(conn, user, eid, "propositions", _propositions.FUSION_A_FAIRE,
                         f"proposition/{proposition_id}", apres=demande)
        conn.commit()
        return _propositions.lire(conn, proposition_id)
    finally:
        conn.close()


# ─── Marchés lus dans les procès-verbaux ──────────────────────────────────────
# Un rapport d'extraction (cf. `collectors/marches_extraits.py`,
# `docs/format-marches-extraits.md`) entre par ici : chaque ligne attestée
# devient une proposition, rien n'est écrit dans `marches_publics` avant qu'un
# validateur l'ait acceptée. Le rapport reçu est archivé tel quel, sous son
# empreinte : une ligne proposée dit de quel rapport elle vient.

RAPPORTS_EXTRAITS = _marches_extraits.RAPPORTS


@app.post("/api/atelier/marches-extraits", status_code=201)
def atelier_deposer_marches_extraits(
    rapport: dict | list = Body(...),
    user=Depends(require_au_moins("validator", "Déposer un rapport de marchés extraits")),
):
    """Dépose un rapport : une proposition par ligne nouvelle dont la citation
    se lit dans l'acte. Rejouer le même rapport ne propose rien de plus.

    Réservé au validateur : un dépôt fait entrer d'un coup des dizaines de
    lignes dans la file que les validateurs relisent, et c'est à eux d'en
    décider."""
    octets = json.dumps(rapport, ensure_ascii=False, sort_keys=True).encode()
    if len(octets) > 20 * 1024 * 1024:
        raise HTTPException(413, "rapport trop volumineux (20 Mo maximum).")
    empreinte = _marches_extraits.empreinte(octets)
    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            bilan = _marches_extraits.deposer(conn, rapport, user, empreinte[:16])
        except _marches_extraits.RapportRefuse as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        _marches_extraits.journaliser(conn, user, empreinte[:16], bilan)
        conn.commit()
    finally:
        conn.close()
    # Archivé APRÈS le dépôt : un rapport refusé n'a rien proposé, il n'a pas
    # à occuper le disque. Le nom est l'empreinte, jamais une valeur reçue.
    _marches_extraits.archiver(octets, RAPPORTS_EXTRAITS)
    return {"ok": True, "rapport": empreinte[:16], **bilan}


RAPPORTS_FLUX_EXTRAITS = _flux_extraits.RAPPORTS


@app.post("/api/atelier/flux-extraits", status_code=201)
def atelier_deposer_flux_extraits(
    rapport: dict = Body(...),
    user=Depends(require_au_moins("validator", "Déposer un rapport de subventions extraites")),
):
    """Le dépôt des marchés lus, pour les subventions (cf. `collectors/flux_extraits.py`) :
    une proposition par ligne nouvelle dont la citation se lit dans l'acte."""
    octets = json.dumps(rapport, ensure_ascii=False, sort_keys=True).encode()
    if len(octets) > 20 * 1024 * 1024:
        raise HTTPException(413, "rapport trop volumineux (20 Mo maximum).")
    empreinte = _flux_extraits.empreinte(octets)
    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            bilan = _flux_extraits.deposer(conn, rapport, user, empreinte[:16])
        except _flux_extraits.RapportRefuse as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        _flux_extraits.journaliser(conn, user, empreinte[:16], bilan)
        conn.commit()
    finally:
        conn.close()
    _flux_extraits.archiver(octets, RAPPORTS_FLUX_EXTRAITS)
    return {"ok": True, "rapport": empreinte[:16], **bilan}


@app.delete("/api/atelier/propositions/{proposition_id}")
def atelier_retirer_proposition(proposition_id: int = FPath(..., ge=1),
                                user=Depends(require_auth)):
    """Son auteur retire une proposition qui attend encore."""
    conn = get_db_rw()
    try:
        _propositions.assurer_schema(conn)
        prop = _propositions.lire(conn, proposition_id)
        if not prop or prop["propose_par_id"] != user["id"]:
            raise HTTPException(404, "Proposition introuvable.")
        if prop["etat"] != "en_attente":
            raise HTTPException(409, f"Cette proposition n'attend plus : {prop['etat']}.")
        _propositions.clore(conn, proposition_id, "retiree", user)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _verdict_de_fiche(conn, entity_id: int) -> str:
    a = row(conn, "SELECT review_status FROM annotations WHERE object_type='entity' "
                  "AND object_id=?", (entity_id,))
    return (verdict_de(a["review_status"]) if a else None) or JAMAIS_RELU


class SignalementFiche(BaseModel):
    motif: str
    doublon_de: Optional[int] = None
    texte: str = Field("", max_length=500)


@app.post("/api/atelier/entities/{entity_id}/signalement", status_code=202)
def atelier_signaler_fiche(entity_id: int = FPath(..., ge=1),
                           req: SignalementFiche = ...,
                           user=Depends(require_auth)):
    """Demande de retirer une fiche, de la fusionner avec un doublon, ou de la
    revoir. Ouvert à tout compte, et toujours une PROPOSITION : la fiche ne
    bouge pas tant qu'un validateur n'a pas tranché (décision de Julien,
    15/11/2026, cf. `collectors/propositions.py`).

    Deux onglets, un même auteur, une même fiche : le second signalement
    reprend le premier (règle de #278). Chaque signalement porte toutes ses
    clés, `doublon_de` comprise : la reprise vaut donc remplacement, et un
    « doublon » corrigé en « à retirer » ne garde pas l'ancienne fiche visée."""
    if req.motif not in _propositions.MOTIFS_SIGNALEMENT:
        raise HTTPException(400, "motif invalide — valeurs : "
                                 + ", ".join(_propositions.MOTIFS_SIGNALEMENT))
    texte = " ".join((req.texte or "").split())
    doublon_de = req.doublon_de if req.motif == "doublon" else None
    if req.motif == "doublon":
        if not doublon_de:
            raise HTTPException(400, "Un doublon se signale avec l'autre fiche : la "
                                     "choisir dans la recherche.")
        if doublon_de == entity_id:
            raise HTTPException(400, "Une fiche n'est pas le doublon d'elle-même.")
    conn = get_db_rw()
    try:
        if not row(conn, "SELECT 1 AS ok FROM entities WHERE id=?", (entity_id,)):
            raise HTTPException(404, "Entité introuvable")
        if doublon_de and not row(conn, "SELECT 1 AS ok FROM entities WHERE id=?",
                                  (doublon_de,)):
            raise HTTPException(404, "La fiche désignée comme doublon est introuvable.")
        _propositions.assurer_schema(conn)
        conn.commit()
        # Un plafond par auteur (avis de vigie-securite) : la file se lit par
        # pages de 200, et un seul compte pouvait en pousser dehors les
        # propositions des autres. Remplacer son signalement sur la même fiche
        # ne compte pas.
        en_attente = row(conn, "SELECT COUNT(*) AS n FROM propositions WHERE "
                               "etat='en_attente' AND nature='signalement' AND "
                               "propose_par=? AND object_id<>?", (user["id"], entity_id))["n"]
        if en_attente >= _propositions.PLAFOND_SIGNALEMENTS:
            raise HTTPException(409, f"Vous avez déjà {en_attente} signalements qui attendent "
                                     "un validateur. Attendez qu'ils soient relus avant "
                                     "d'en envoyer d'autres.")
        pid, reprises = _propositions.proposer(
            conn, "signalement", "entity", entity_id, entity_id,
            {"motif": req.motif, "doublon_de": doublon_de, "texte": texte},
            {"verdict": _verdict_de_fiche(conn, entity_id)}, user, rendre_reprises=True)
        conn.commit()
    finally:
        conn.close()
    message = ("Signalement envoyé. Un validateur le lira dans la file des propositions "
               "et décidera ; la fiche ne change pas d'ici là.")
    if reprises:
        message += " Il remplace votre signalement précédent sur cette fiche."
    return {"ok": True, "proposition": pid, "reprend": reprises, "message": message}


# ─── /api/budget-annexe (public) ──────────────────────────────────────────────

@app.get("/api/budget-annexe", dependencies=[Depends(require_auth)])
def budget_annexe_list(entity_id: Optional[int] = None, year: Optional[int] = None):
    conn = get_db()
    try:
        filters, params = [], []
        if entity_id:
            filters.append("ba.entity_id = ?"); params.append(entity_id)
        if year:
            filters.append("ba.year = ?"); params.append(year)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        return rows(conn, f"""
            SELECT ba.*, e.name AS entity_name, e.type AS entity_type
            FROM budget_annexe ba
            JOIN entities e ON e.id = ba.entity_id
            {where}
            ORDER BY ba.entity_id, ba.year DESC, ba.section, ba.sens, ba.id
        """, params)
    except Exception:
        return []
    finally:
        conn.close()


# ─── /api/atelier/budget-annexe (auth) ────────────────────────────────────────

class BudgetAnnexeCreate(BaseModel):
    entity_id:       int
    year:            int
    section:         str
    sens:            str
    compte:          Optional[str] = None
    libelle:         str
    montant:         float
    source:          str
    source_event_id: Optional[int] = None
    confidence:      str = "verified"

class BudgetAnnexeUpdate(BaseModel):
    section:    Optional[str]   = None
    sens:       Optional[str]   = None
    compte:     Optional[str]   = None
    libelle:    Optional[str]   = None
    montant:    Optional[float] = None
    source:     Optional[str]   = None
    confidence: Optional[str]   = None

@app.post("/api/atelier/budget-annexe")
def create_budget_annexe(req: BudgetAnnexeCreate, user=Depends(require_au_moins("validator", "Ajouter au budget annexe"))):
    if req.section not in ("fonctionnement", "investissement", "dette"):
        raise HTTPException(400, "section invalide")
    if req.sens not in ("depense", "recette", "solde"):
        raise HTTPException(400, "sens invalide")
    conn = get_db_rw()
    try:
        if not row(conn, "SELECT 1 FROM entities WHERE id=?", (req.entity_id,)):
            raise HTTPException(404, "Entité introuvable")
        r = conn.execute(
            """INSERT INTO budget_annexe (entity_id, year, section, sens, compte, libelle, montant, source, source_event_id, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (req.entity_id, req.year, req.section, req.sens, req.compte,
             req.libelle, req.montant, req.source, req.source_event_id, req.confidence)
        )
        conn.commit()
        return row(conn, "SELECT * FROM budget_annexe WHERE id=?", (r.lastrowid,))
    finally:
        conn.close()

@app.put("/api/atelier/budget-annexe/{ba_id}")
def update_budget_annexe(ba_id: int = FPath(..., ge=1), req: BudgetAnnexeUpdate = ..., user=Depends(require_au_moins("validator", "Modifier le budget annexe"))):
    conn = get_db_rw()
    try:
        existing = row(conn, "SELECT * FROM budget_annexe WHERE id=?", (ba_id,))
        if not existing:
            raise HTTPException(404, "Ligne de budget introuvable")
        updates = {k: v for k, v in req.dict().items() if v is not None}
        if not updates:
            return existing
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE budget_annexe SET {set_clause} WHERE id=?",
                     list(updates.values()) + [ba_id])
        conn.commit()
        return row(conn, "SELECT * FROM budget_annexe WHERE id=?", (ba_id,))
    finally:
        conn.close()

@app.delete("/api/atelier/budget-annexe/{ba_id}")
def delete_budget_annexe(ba_id: int = FPath(..., ge=1), user=Depends(require_au_moins("validator", "Supprimer du budget annexe"))):
    conn = get_db_rw()
    try:
        if not row(conn, "SELECT 1 FROM budget_annexe WHERE id=?", (ba_id,)):
            raise HTTPException(404, "Ligne de budget introuvable")
        conn.execute("DELETE FROM budget_annexe WHERE id=?", (ba_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ─── /api/atelier/entities/{id}/coords ────────────────────────────────────────

class CoordsUpdate(BaseModel):
    lat: float
    lng: float

@app.patch("/api/atelier/entities/{entity_id}/coords")
def update_coords(
    entity_id: int = FPath(..., ge=1),
    req: CoordsUpdate = ...,
    user=Depends(require_auth),
):
    if not (-90 <= req.lat <= 90 and -180 <= req.lng <= 180):
        raise HTTPException(400, "coordonnées hors du globe")
    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        old = row(conn, "SELECT lat, lng, confidence FROM entities WHERE id=?", (entity_id,))
        if not old:
            raise HTTPException(404, "Entité introuvable")
        if _doit_proposer(user, old["confidence"]):
            return _propose(conn, "coords", "entity", entity_id, entity_id,
                            {"lat": req.lat, "lng": req.lng},
                            {"lat": old["lat"], "lng": old["lng"]}, user,
                            lat=old["lat"], lng=old["lng"])
        x_l93, y_l93 = _ecrire_coords(conn, entity_id, req.lat, req.lng, old, user)
        conn.commit()
        return {"ok": True, "lat": req.lat, "lng": req.lng, "x_l93": x_l93, "y_l93": y_l93}
    finally:
        conn.close()


def _ecrire_coords(conn, entity_id: int, lat: float, lng: float, old: dict, user: dict):
    from collectors.geocoder import wgs84_to_l93
    x_l93, y_l93 = wgs84_to_l93(lat, lng)
    conn.execute("""
        UPDATE entities
        SET lat=?, lng=?, x_l93=?, y_l93=?,
            geocode_source='manual', geocode_score=1.0,
            updated_at=datetime('now')
        WHERE id=?
    """, (lat, lng, x_l93, y_l93, entity_id))
    conn.execute("""
        INSERT INTO audit_log(user_id, entity_id, table_name, action, field, old_value, new_value)
        VALUES(?,?,'entities','update','coords',?,?)
    """, (user["id"], entity_id, f"{old['lat']},{old['lng']}",
          f"{lat},{lng} (L93: {x_l93},{y_l93})"))
    return x_l93, y_l93


# ─── Notes ────────────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    note:       str
    source:     str = "manual"
    # Sans valeur : `verified` pour un validateur, `probable` pour un
    # contributeur, qui ne pose pas de fiabilité publiable (audit du 15/11/2026).
    confidence: Optional[str] = None
    date:       Optional[str] = None

class NoteUpdate(BaseModel):
    note:       Optional[str] = None
    source:     Optional[str] = None
    confidence: Optional[str] = None

def _notes_reservees(fiche: Optional[dict], user: dict) -> bool:
    return bool(fiche) and fiche.get("type") == "person" and not au_moins(user, "validator")


_NOTE_SUR_PERSONNE = "Écrire une note sur une personne physique"


@app.post("/api/atelier/entities/{entity_id}/notes")
def create_note(entity_id: int = FPath(..., ge=1), req: NoteCreate = ..., user=Depends(require_auth)):
    conn = get_db_rw()
    try:
        fiche = row(conn, "SELECT type FROM entities WHERE id=?", (entity_id,))
        if not fiche:
            raise HTTPException(404, "Entité introuvable")
        if _notes_reservees(fiche, user):
            exiger(user, "validator", _NOTE_SUR_PERSONNE)
        tranche = au_moins(user, "validator")
        confiance = req.confidence or ("verified" if tranche else "probable")
        if confiance in ("verified", "confirmed") and not tranche:
            exiger(user, "validator", "Poser la fiabilité d'une note")
        date = req.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur = conn.execute(
            "INSERT INTO entity_notes (entity_id, date, note, source, confidence)"
            " VALUES (?,?,?,?,?)",
            (entity_id, date, req.note.strip(), req.source, confiance)
        )
        # L'auteur d'une note n'avait pas de trace : il en faut une pour savoir
        # à qui elle est (cf. `update_note`).
        _journal(conn, user, entity_id, "entity_notes", "create", f"note/{cur.lastrowid}")
        conn.commit()
        return {"id": cur.lastrowid, "entity_id": entity_id, "date": date,
                "note": req.note.strip(), "source": req.source, "confidence": confiance}
    finally:
        conn.close()

@app.put("/api/atelier/notes/{note_id}")
def update_note(note_id: int = FPath(..., ge=1), req: NoteUpdate = ..., user=Depends(require_auth)):
    conn = get_db_rw()
    try:
        existing = row(conn, "SELECT * FROM entity_notes WHERE id=?", (note_id,))
        if not existing:
            raise HTTPException(404, "Note introuvable")
        if _notes_reservees(row(conn, "SELECT type FROM entities WHERE id=?",
                                (existing["entity_id"],)), user):
            exiger(user, "validator", _NOTE_SUR_PERSONNE)
        if not au_moins(user, "validator"):
            # Un contributeur réécrivait la note d'un autre, et la passait en
            # `verified` (audit du 15/11/2026).
            auteur = conn.execute(
                "SELECT user_id FROM audit_log WHERE table_name='entity_notes' "
                "AND action='create' AND field=? ORDER BY id LIMIT 1",
                (f"note/{note_id}",)).fetchone()
            if not auteur or auteur[0] != user["id"]:
                exiger(user, "validator", "Modifier la note d'un autre compte")
            if req.confidence in ("verified", "confirmed") \
                    and req.confidence != existing.get("confidence"):
                exiger(user, "validator", "Poser la fiabilité d'une note")
        updates, vals = [], []
        if req.note is not None:
            updates.append("note=?"); vals.append(req.note.strip())
        if req.source is not None:
            updates.append("source=?"); vals.append(req.source)
        if req.confidence is not None:
            updates.append("confidence=?"); vals.append(req.confidence)
        if updates:
            vals.append(note_id)
            conn.execute(f"UPDATE entity_notes SET {','.join(updates)} WHERE id=?", vals)
            _journal(conn, user, existing["entity_id"], "entity_notes", "update",
                     f"note/{note_id}", avant=existing["note"],
                     apres=req.note.strip() if req.note is not None else None)
            conn.commit()
        return row(conn, "SELECT id, date, note, source, confidence FROM entity_notes WHERE id=?", (note_id,))
    finally:
        conn.close()

@app.delete("/api/atelier/notes/{note_id}", status_code=204)
def delete_note(note_id: int = FPath(..., ge=1), user=Depends(require_au_moins("validator", "Supprimer une note"))):
    conn = get_db_rw()
    try:
        conn.execute("DELETE FROM entity_notes WHERE id=?", (note_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Websites ─────────────────────────────────────────────────────────────────

class WebsiteAdd(BaseModel):
    url: str
    found_by: str = "manual"
    score: Optional[float] = 1.0


def _url_http(valeur: str, champ: str = "url") -> str:
    """Une adresse saisie devient un lien cliquable dans l'atelier ET sur le
    site : `javascript:…` y serait un script, pas une adresse."""
    s = (valeur or "").strip()
    if not s.lower().startswith(("http://", "https://")) or any(c.isspace() for c in s):
        raise HTTPException(400, f"{champ} : adresse http(s) attendue")
    return s[:1000]

@app.post("/api/atelier/entities/{entity_id}/websites")
def add_website(entity_id: int = FPath(..., ge=1), req: WebsiteAdd = ..., user=Depends(require_auth)):
    req.url = _url_http(req.url)
    conn = get_db_rw()
    try:
        if not row(conn, "SELECT 1 FROM entities WHERE id=?", (entity_id,)):
            raise HTTPException(404, "Entité introuvable")
        # Un site ajouté à la main était validé d'office, donc publié. Celui d'un
        # contributeur entre dans la file des candidats, où un validateur tranche.
        statut = "validated" if au_moins(user, "validator") else "candidate"
        conn.execute(
            "INSERT OR IGNORE INTO entity_websites (entity_id, url, status, score, found_by)"
            " VALUES (?,?,?,?,?)",
            (entity_id, req.url.strip(), statut, req.score, req.found_by)
        )
        _journal(conn, user, entity_id, "entity_websites", "create",
                 f"site {req.url.strip()}", apres=statut)
        conn.commit()
        return row(conn, "SELECT * FROM entity_websites WHERE entity_id=? AND url=?",
                   (entity_id, req.url.strip()))
    finally:
        conn.close()

@app.patch("/api/atelier/websites/{website_id}")
def patch_website(website_id: int = FPath(..., ge=1),
                  body: dict = Body(...), user=Depends(require_au_moins("validator", "Trancher un site"))):
    status = body.get("status")
    if status not in ("validated", "rejected", "candidate", "broken"):
        raise HTTPException(422, "status invalide")
    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        site = row(conn, "SELECT * FROM entity_websites WHERE id=?", (website_id,))
        if not site:
            raise HTTPException(404)
        # La réservation n'était tenue que par l'interface, qui grisait les
        # boutons : l'API, elle, laissait trancher un site pris par un autre.
        active = _reservation_active(site)
        if active and active["par"] != user["email"]:
            raise _conflit(f"Pris par {active['par']}, qui l'examine.", active["par"],
                           active["le"], expire_dans_min=active["expire_dans_min"])
        conn.execute("UPDATE entity_websites SET status=?, last_check=datetime('now') WHERE id=?",
                     (status, website_id))
        # Valider un site le publie : ce geste n'était journalisé nulle part.
        if site["status"] != status:
            _journal(conn, user, site["entity_id"], "entity_websites", "update",
                     f"site {site['url']}", avant=site["status"], apres=status)
        conn.commit()
        return row(conn, "SELECT * FROM entity_websites WHERE id=?", (website_id,))
    finally:
        conn.close()

@app.delete("/api/atelier/websites/{website_id}", status_code=204)
def delete_website(website_id: int = FPath(..., ge=1), user=Depends(require_au_moins("validator", "Supprimer un site"))):
    conn = get_db_rw()
    try:
        site = row(conn, "SELECT entity_id, url, status FROM entity_websites WHERE id=?",
                   (website_id,))
        conn.execute("DELETE FROM entity_websites WHERE id=?", (website_id,))
        if site:
            _journal(conn, user, site["entity_id"], "entity_websites", "delete",
                     f"site {site['url']}", avant=site["status"])
        conn.commit()
    finally:
        conn.close()


# ─── Queue websites ───────────────────────────────────────────────────────────

@app.get("/api/atelier/queue/websites")
def queue_websites(status: str = "candidate", limit: int = 100, user=Depends(require_auth)):
    conn = get_db()
    try:
        lignes = rows(conn, """
            SELECT ew.id, ew.url, ew.status, ew.score, ew.found_by,
                   ew.locked_by, ew.locked_at,
                   e.id AS entity_id, e.name AS entity_name, e.type AS entity_type
            FROM entity_websites ew
            JOIN entities e ON e.id = ew.entity_id
            WHERE ew.status = ?
            ORDER BY ew.score DESC, e.name
            LIMIT ?
        """, (status, limit))
        # La réservation n'était pas renvoyée : un autre éditeur ne voyait
        # jamais qu'un site était pris. Une réservation expirée n'en est plus une.
        for ligne in lignes:
            ligne["reservation"] = _reservation_active(ligne)
            del ligne["locked_by"], ligne["locked_at"]
        return lignes
    finally:
        conn.close()


# ─── Analyses / cross-references (Sprint F) ───────────────────────────────────

@app.get("/api/analyses/mandats-croises")
def analyse_mandats_croises(min_roles: int = 2, user=Depends(require_au_moins("validator", "Voir les analyses"))):
    conn = get_db()
    try:
        return rows(conn,
            "SELECT * FROM v_mandats_croises WHERE nb_roles >= ? ORDER BY nb_roles DESC",
            (min_roles,))
    finally:
        conn.close()


@app.get("/api/analyses/conflits")
def analyse_conflits(
    chronologie: Optional[str] = None,  # contemporain|lien_sans_flux|dates_manquantes|tous
    user=Depends(require_au_moins("validator", "Voir les analyses"))
):
    conn = get_db()
    try:
        if chronologie and chronologie != "tous":
            q = "SELECT * FROM v_conflits_potentiels WHERE chronologie=?"
            params = (chronologie,)
        else:
            q = "SELECT * FROM v_conflits_potentiels"
            params = ()
        return rows(conn, q, params)
    finally:
        conn.close()


@app.get("/api/analyses/subventions")
def analyse_subventions(entity_id: Optional[int] = None, user=Depends(require_au_moins("validator", "Voir les analyses"))):
    conn = get_db()
    try:
        if entity_id:
            return rows(conn,
                "SELECT * FROM v_subventions_beneficiaires WHERE entity_id=?",
                (entity_id,))
        return rows(conn, "SELECT * FROM v_subventions_beneficiaires", ())
    finally:
        conn.close()


@app.get("/api/analyses/marches")
def analyse_marches(entity_id: Optional[int] = None, user=Depends(require_au_moins("validator", "Voir les analyses"))):
    conn = get_db()
    try:
        if entity_id:
            return rows(conn,
                "SELECT * FROM v_marches_attributaires WHERE entity_id=?",
                (entity_id,))
        return rows(conn,
            "SELECT * FROM v_marches_attributaires WHERE titulaire_nom IS NOT NULL",
            ())
    finally:
        conn.close()


@app.get("/api/analyses/adresses-partagees")
def analyse_adresses(user=Depends(require_au_moins("validator", "Voir les analyses"))):
    conn = get_db()
    try:
        return rows(conn, "SELECT * FROM v_adresses_partagees", ())
    finally:
        conn.close()


@app.get("/api/analyses/familles")
def analyse_familles(min_personnes: int = 2, user=Depends(require_au_moins("validator", "Voir les analyses"))):
    conn = get_db()
    try:
        return rows(conn,
            "SELECT * FROM v_familles_potentielles WHERE nb_personnes >= ? ORDER BY nb_personnes DESC",
            (min_personnes,))
    finally:
        conn.close()


# ─── RAG — recherche sémantique (Sprint G) ────────────────────────────────────

import numpy as np
import requests as _requests

# Adresse et modèles sont des réglages, pas des constantes du dispositif :
# l'interface les affichait en dur — « nomic-embed-text + Gemma 3 » — et
# continuait de les afficher après un changement de modèle.
# `127.0.0.1` et non `localhost` : Ollama n'écoute qu'en IPv4, et sur macOS
# `localhost` résout d'abord en IPv6 — la requête part vers `::1` où rien
# n'écoute. Même piège que le proxy de l'atelier (cf. dashboard/vite.config.js).
from collectors import index_sens as _index_sens
_OLLAMA_URL   = _index_sens.OLLAMA_URL
_OLLAMA_EMBED = f"{_OLLAMA_URL}/api/embeddings"
_OLLAMA_CHAT  = f"{_OLLAMA_URL}/api/chat"
_EMBED_MODEL  = _index_sens.MODELE
_CHAT_MODEL   = os.environ.get("OLLAMA_CHAT_MODEL", "gemma3:4b")


@app.get("/api/rag/config")
def rag_config(user=Depends(require_auth)):
    """Ce que l'atelier doit savoir pour décrire honnêtement sa recherche.

    Sert aussi à distinguer les trois états que l'interface confondait : la
    fonction désactivée, l'index jamais construit, et l'index prêt.
    """
    conn = get_db()
    try:
        chunks = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    except sqlite3.Error:
        chunks = 0        # table absente : l'index n'a jamais été construit
    finally:
        conn.close()
    return {
        "enabled": RAG_ENABLED,
        # « Faire résumer » ne s'affiche que si la génération est ouverte.
        "ask_enabled": _ask_ouvert(),
        "embed_model": _EMBED_MODEL,
        "chat_model": _CHAT_MODEL,
        "chunks": chunks,
    }


def _ask_ouvert() -> bool:
    return RAG_ENABLED and RAG_ASK_ENABLED


# ─── Brancher une IA depuis l'atelier (admin) ────────────────────────────────
# Jusqu'au 07/10/2026 un modèle ne se branchait que par `.env`, donc par qui a
# la main sur le serveur. Un administrateur d'atelier peut maintenant brancher
# un modèle LOCAL — et seulement local : il choisit parmi ceux que l'Ollama de
# cette machine annonce. Il ne saisit ni adresse ni clé, ce qui ferme d'avance
# les deux portes qu'un réglage libre ouvrirait à une session admin volée :
# faire requêter le serveur vers une adresse de son choix, et lui faire envoyer
# des extraits de la base de travail chez un tiers. Un service DISTANT reste une
# décision de serveur (`IA_URL` + `IA_HORS_MACHINE=1`), que cette page constate
# sans pouvoir la prendre.
#
# Le réglage vit dans `audits/`, pas en base : il décrit CETTE machine, et la
# base voyage (sauvegardes, copies tirées).
REGLAGE_IA = BASE_DIR / "audits" / "ia_locale.json"
_IA_DU_SERVEUR = bool(_IA_URL)          # réglé dans .env : l'atelier n'y touche pas
_RAG_DU_SERVEUR = RAG_ENABLED
_CHAT_DU_SERVEUR = _CHAT_MODEL


def _ollama_local() -> bool:
    from urllib.parse import urlsplit
    return (urlsplit(_OLLAMA_URL).hostname or "") in ("localhost", "127.0.0.1", "::1")


def _modeles_ollama() -> Optional[list[str]]:
    """Les modèles que l'Ollama de cette machine annonce ; None s'il ne répond pas."""
    if not _ollama_local():
        return None
    try:
        r = _requests.get(f"{_OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:                               # noqa: BLE001
        return None


def _a_le_modele(modeles: list[str], nom: str) -> bool:
    """`nomic-embed-text` est annoncé `nomic-embed-text:latest`."""
    return any(m == nom or m.split(":")[0] == nom for m in modeles)


def _appliquer_reglage_ia(reglage: dict) -> None:
    global _IA_URL, _IA_MODELE, _IA_CLE, _IA_PROTOCOLE, RAG_ENABLED, _CHAT_MODEL
    modele = (reglage.get("modele") or "").strip()
    if not _IA_DU_SERVEUR:
        _IA_URL = f"{_OLLAMA_URL}/v1" if modele else ""
        _IA_MODELE, _IA_CLE, _IA_PROTOCOLE = modele, "", "openai"
    _CHAT_MODEL = modele or _CHAT_DU_SERVEUR
    RAG_ENABLED = _RAG_DU_SERVEUR or bool(reglage.get("recherche"))


if _ollama_local():
    _appliquer_reglage_ia(_read_json_file(REGLAGE_IA, {}) or {})


class ReglageIA(BaseModel):
    modele: Optional[str] = None      # None ou vide : aucun modèle branché
    recherche: bool = False           # la recherche par sens (`/api/rag/*`)


def _etat_ia() -> dict:
    modeles = _modeles_ollama()
    return {
        "ollama": {"url": _OLLAMA_URL, "local": _ollama_local(),
                   "joignable": modeles is not None, "modeles": modeles or []},
        "reglage": {"modele": None, "recherche": False}
                   | (_read_json_file(REGLAGE_IA, {}) or {}),
        # Ce que le serveur a décidé dans `.env`, et que l'atelier ne défait pas.
        "serveur": {"ia": _IA_DU_SERVEUR, "recherche": _RAG_DU_SERVEUR},
        "ia": ia_config(None),
        "recherche": rag_config(None)
                     | {"embed_present": bool(modeles) and _a_le_modele(modeles, _EMBED_MODEL)},
    }


_REGLER_IA = "Brancher une IA sur l'atelier"


@app.get("/api/admin/ia")
def admin_ia(user=Depends(require_au_moins("admin", _REGLER_IA))):
    return _etat_ia()


@app.put("/api/admin/ia")
def admin_regler_ia(req: ReglageIA, user=Depends(require_au_moins("admin", _REGLER_IA))):
    modele = (req.modele or "").strip()
    if modele or req.recherche:
        modeles = _modeles_ollama()
        if modeles is None:
            raise HTTPException(409, "Ollama ne répond pas sur cette machine "
                                     f"({_OLLAMA_URL}) : il n'y a pas de modèle à brancher.")
        # Le nom vient de la liste, jamais d'une saisie libre.
        if modele and modele not in modeles:
            raise HTTPException(400, f"« {modele} » n'est pas installé dans Ollama — "
                                     f"modèles présents : {', '.join(modeles) or 'aucun'}.")
        if req.recherche and not _a_le_modele(modeles, _EMBED_MODEL):
            raise HTTPException(400, "La recherche par sens demande le modèle "
                                     f"d'embeddings : `ollama pull {_EMBED_MODEL}`.")
    reglage = {"modele": modele or None, "recherche": req.recherche}
    REGLAGE_IA.parent.mkdir(parents=True, exist_ok=True)
    provisoire = REGLAGE_IA.with_suffix(".tmp")
    provisoire.write_text(json.dumps(reglage, ensure_ascii=False), encoding="utf-8")
    provisoire.replace(REGLAGE_IA)
    _appliquer_reglage_ia(reglage)
    conn = get_db_rw()
    try:
        conn.execute("INSERT INTO audit_log(user_id, table_name, action, new_value) "
                     "VALUES(?, 'reglages', 'ia', ?)",
                     (user["id"], json.dumps(reglage, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return _etat_ia()


def _embed(text: str) -> np.ndarray:
    r = _requests.post(_OLLAMA_EMBED, json={"model": _EMBED_MODEL, "prompt": text}, timeout=30)
    r.raise_for_status()
    return np.array(r.json()["embedding"], dtype=np.float32)


def _modele_incompatible(conn) -> Optional[str]:
    """Pourquoi l'index ne peut pas être interrogé avec le modèle que sert
    Ollama ; `None` s'il le peut. Lève si Ollama ne répond pas."""
    emp = _index_sens.empreinte(_requests, _OLLAMA_URL, _EMBED_MODEL)
    try:
        return _index_sens.incompatibilite(conn, _EMBED_MODEL, emp)
    except sqlite3.OperationalError:
        # Base que ni une passe ni l'indexeur n'ont encore migrée : ses
        # vecteurs ne disent pas de quel modèle ils viennent.
        n = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        if not n:
            return None
        return (f"{n} extraits de l'index ne disent pas quel modèle les a calculés "
                "(index antérieur au 07/11/2026). Reconstruire l'index : "
                "`python3 scripts/build_rag_index.py --reset`.")


@app.get("/api/rag/search")
def rag_search(q: str = Query(..., max_length=2000), limit: int = Query(8, ge=1, le=50),
               user=Depends(require_auth)):
    """Recherche sémantique dans l'index (pas de génération). LOCAL UNIQUEMENT.

    Ce qu'elle apporte, et qu'aucune recherche plein texte ne peut donner :
    retrouver une notion quand on n'en a pas le vocabulaire — « conflit
    d'intérêt » ramène les récusations au conseil, où ce mot ne figure pas.
    Pour tout ce qui s'écrit en toutes lettres, le FTS5 déjà en base est plus
    rapide et plus prévisible.

    L'index entier est chargé et comparé en mémoire à chaque question, sans
    index approximatif. Mesuré le 15/08/2026 sur 18 176 chunks : 0,10 s de
    lecture et 0,03 s de calcul. À l'échelle d'une commune, la simplicité vaut
    mieux qu'une structure d'index à tenir à jour.

    Refusée (409) si l'index a été construit par un autre modèle que celui que
    sert Ollama : la comparaison rendrait des voisins au hasard, avec des
    scores plausibles (`collectors/index_sens.py`).
    """
    if not RAG_ENABLED:
        raise HTTPException(503, "RAG désactivé sur cet atelier (RAG_ENABLED=0). "
                                 "Interroger localement avec Ollama.")
    try:
        limit = min(max(int(limit), 1), 50)
    except (TypeError, ValueError):
        raise HTTPException(400, "limit : un entier de 1 à 50")

    conn = get_db()
    try:
        try:
            raison = _modele_incompatible(conn)
        except Exception:                               # noqa: BLE001
            # Pas le texte de l'exception : il porte l'adresse et la réponse du serveur.
            raise HTTPException(503, "Le modèle d'embeddings ne répond pas sur cette machine.")
        if raison:
            raise HTTPException(409, raison)
        try:
            q_vec = _embed(q)
        except Exception:                               # noqa: BLE001
            raise HTTPException(503, "Le modèle d'embeddings ne répond pas sur cette machine.")
        # Les notes sur une personne physique sont réservées au validateur dans
        # la fiche (`_notes_reservees`) : l'index les porte en clair, il ne
        # doit pas les rendre à qui la fiche les cache (audit du 15/11/2026).
        reserve = "" if au_moins(user, "validator") else (
            " WHERE NOT (source_table = 'entity_notes' AND entity_id IN"
            " (SELECT id FROM entities WHERE type = 'person'))")
        db_rows = conn.execute(
            "SELECT source_table, source_id, entity_id, chunk_text, vector FROM embeddings"
            + reserve
        ).fetchall()
    finally:
        conn.close()

    if not db_rows:
        return []

    # Un seul produit matriciel plutôt qu'une boucle Python : la boucle coûtait
    # une à deux secondes sur 18 000 chunks, la matrice 0,03 s. C'est ce qui
    # rend l'index brut suffisant à l'échelle d'une commune — aucun index
    # approximatif n'est nécessaire ici.
    matrice = np.frombuffer(
        b"".join(r[4] for r in db_rows), dtype=np.float32
    ).reshape(len(db_rows), -1)
    normes = np.linalg.norm(matrice, axis=1) * np.linalg.norm(q_vec)
    sims = np.divide(matrice @ q_vec, normes,
                     out=np.zeros(len(db_rows), dtype=np.float32),
                     where=normes > 1e-8)

    meilleurs = np.argsort(-sims)[:limit]
    return [
        {"score": round(float(sims[i]), 4),
         "source_table": db_rows[i][0], "source_id": db_rows[i][1],
         "entity_id": db_rows[i][2], "chunk_text": db_rows[i][3]}
        for i in meilleurs
    ]


@app.post("/api/rag/ask")
def rag_ask(body: dict = Body(...), user=Depends(require_auth)):
    """Recherche + génération (via Ollama). LOCAL UNIQUEMENT. body: {question, limit?}

    Fermée tant que `RAG_ASK_ENABLED=1` n'est pas posé dans `.env`, même quand
    la recherche est ouverte : un serveur peut n'avoir que le modèle
    d'embeddings. Un administrateur ne l'ouvre pas depuis l'atelier."""
    if not _ask_ouvert():
        raise HTTPException(503, "La génération de réponse est fermée sur cet atelier "
                                 "(RAG_ASK_ENABLED=0) ; la recherche par sens, elle, "
                                 "reste disponible si elle est active.")
    question = body.get("question", "").strip()
    # Borné : chaque extrait part dans le prompt, et `limit` venait tel quel
    # du client (audit du 06/11/2026).
    try:
        limit = min(max(int(body.get("limit", 6)), 1), 20)
    except (TypeError, ValueError):
        raise HTTPException(400, "limit : un entier de 1 à 20")
    if not question:
        raise HTTPException(400, "question vide")

    # Récupérer le contexte
    try:
        results = rag_search(q=question, limit=limit, user=user)
    except HTTPException:
        raise

    context = "\n\n".join(
        f"[{r['source_table']}#{r['source_id']}] {r['chunk_text']}"
        for r in results
    )

    prompt = (
        "Tu es un assistant d'investigation citoyenne sur la commune de "
        f"{COMMUNE_NAME} (département {DEPARTEMENT}, France). "
        "Réponds en français, de manière factuelle et concise, uniquement à partir des extraits fournis. "
        "Si l'information n'est pas dans les extraits, dis-le clairement.\n\n"
        f"### Extraits pertinents\n{context}\n\n"
        f"### Question\n{question}\n\n"
        "### Réponse"
    )

    try:
        r = _requests.post(
            _OLLAMA_CHAT,
            json={"model": _CHAT_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
            timeout=120,
        )
        r.raise_for_status()
        answer = r.json()["message"]["content"]
    except Exception as e:
        answer = None
        # Le message d'une erreur réseau porte l'adresse et le port du modèle :
        # il reste au journal serveur, le client reçoit une phrase fixe.
        print(f"[rag_ask] le modèle n'a pas répondu : {e!r}", file=sys.stderr)
        error  = "le modèle n'a pas répondu"
    else:
        error = None

    return {"question": question, "answer": answer, "error": error, "sources": results}


# ─── POST /api/atelier/queue/{id}/claim ────────────────────────────────────────
# Claim queue : lock mou sur un item de validation pour éviter le double-traitement.
# Expiration : 10 minutes. Utilise locked_by + locked_at sur relation_candidates ou entity_websites.

# La durée vit dans `collectors/files.py` : le relevé des files s'en sert pour
# dire « en cours », et poser la réservation ici avec un autre nombre ferait
# apparaître un chantier que plus personne ne tient (23/09/2026).
_CLAIM_EXPIRES_MIN = RESERVATION_MINUTES


class ClaimRequest(BaseModel):
    table:     str     # "relation_candidates" | "entity_websites"
    # IGNORÉ depuis le 17/09/2026 : le nom venait du navigateur, et l'on pouvait
    # réserver au nom de n'importe qui. C'est le compte connecté qui réserve.
    locked_by: Optional[str] = None


CLAIMABLE_TABLES = {"relation_candidates", "entity_websites"}
# Le nom de table SQL vient de cette table constante, jamais de la valeur reçue.
_TABLE_SQL = {"relation_candidates": "relation_candidates",
              "entity_websites": "entity_websites"}


def _reservation_active(ligne: dict) -> Optional[dict]:
    """{par, le, expire_dans_min} si la ligne est réservée et que ça n'a pas
    expiré ; None sinon. `locked_at` est un horodatage SQLite (UTC)."""
    par, le = ligne.get("locked_by"), ligne.get("locked_at")
    if not par or not le:
        return None
    try:
        pose = datetime.fromisoformat(le).replace(tzinfo=timezone.utc)
    except ValueError:
        return None                       # date illisible : réservation caduque
    age = (datetime.now(timezone.utc) - pose).total_seconds() / 60
    if age >= _CLAIM_EXPIRES_MIN:
        return None
    return {"par": par, "le": le, "expire_dans_min": round(_CLAIM_EXPIRES_MIN - age, 1)}


def _entite_de(table: str, ligne: dict) -> Optional[int]:
    return ligne.get("entity_id") if table == "entity_websites" else ligne.get("from_id")


def _journal(conn, user: dict, entity_id: Optional[int], table: str, action: str,
             champ: Optional[str], avant=None, apres=None) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, entity_id, table_name, action, field, "
        "old_value, new_value) VALUES(?,?,?,?,?,?,?)",
        (user["id"], entity_id, table, action, champ,
         None if avant is None else str(avant), None if apres is None else str(apres)))


@app.post("/api/atelier/queue/{item_id}/claim")
def queue_claim(
    item_id: int = FPath(..., ge=1),
    req: ClaimRequest = ...,
    user=Depends(require_au_moins("validator", "Réserver un élément à trancher")),
):
    """
    Réserve un élément de file au nom du compte connecté, pour 10 minutes.
    - Déjà réservé par quelqu'un d'autre, et pas expiré → 409 (qui, depuis quand).
    - La réservation est journalisée : on sait qui travaillait sur quoi.
    """
    if req.table not in CLAIMABLE_TABLES:
        raise HTTPException(400, f"table invalide — valeurs: {', '.join(CLAIMABLE_TABLES)}")

    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = row(conn, f"SELECT * FROM {_TABLE_SQL[req.table]} WHERE id=?", (item_id,))
        if not existing:
            raise HTTPException(404, "Item introuvable")

        active = _reservation_active(existing)
        if active and active["par"] != user["email"]:
            raise _conflit(f"Déjà pris par {active['par']}.", active["par"], active["le"],
                           expire_dans_min=active["expire_dans_min"])

        conn.execute(
            f"UPDATE {_TABLE_SQL[req.table]} SET locked_by=?, locked_at=datetime('now') WHERE id=?",
            (user["email"], item_id),
        )
        _journal(conn, user, _entite_de(req.table, existing), req.table, "reservation",
                 str(item_id))
        conn.commit()
        return {
            "ok": True,
            "table": req.table,
            "item_id": item_id,
            "locked_by": user["email"],
            "expires_in_min": _CLAIM_EXPIRES_MIN,
        }
    finally:
        conn.close()


@app.delete("/api/atelier/queue/{item_id}/claim")
def queue_unclaim(
    item_id: int = FPath(..., ge=1),
    table: str = Query(...),
    user=Depends(require_au_moins("validator", "Libérer une réservation")),
):
    """Libère une réservation : la sienne, une réservation expirée, ou — admin —
    celle de n'importe qui. N'importe quel compte pouvait libérer celle d'autrui."""
    if table not in CLAIMABLE_TABLES:
        raise HTTPException(400, f"table invalide — valeurs: {', '.join(CLAIMABLE_TABLES)}")
    conn = get_db_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = row(conn, f"SELECT * FROM {_TABLE_SQL[table]} WHERE id=?", (item_id,))
        if not existing:
            raise HTTPException(404, "Item introuvable")
        active = _reservation_active(existing)
        if active and active["par"] != user["email"] and user.get("role") != "admin":
            raise HTTPException(403, {
                "message": f"Réservé par {active['par']} : seul ce compte ou un "
                           f"administrateur peut le libérer avant l'expiration.",
                "par": active["par"], "le": active["le"],
                "expire_dans_min": active["expire_dans_min"]})
        conn.execute(
            f"UPDATE {_TABLE_SQL[table]} SET locked_by=NULL, locked_at=NULL WHERE id=?",
            (item_id,),
        )
        if existing.get("locked_by"):
            _journal(conn, user, _entite_de(table, existing), table, "liberation",
                     str(item_id), avant=existing["locked_by"])
        conn.commit()
        return {"ok": True, "table": table, "item_id": item_id}
    finally:
        conn.close()


# ─── /api/atelier/geo-review — file de correction géo priorisée ──────────────

@app.get("/api/atelier/geo-review")
def geo_review(limit: int = Query(150, ge=1, le=500), user=Depends(require_auth)):
    """
    File de correction géolocalisation : entités PHYSIQUES, dans la commune,
    exposées (flux financiers, marchés, relations), triées « à corriger d'abord »
    puis par exposition. Objectif : atteindre le <5 m sur les entités qui comptent.
    Exclut personnes (coords masquées), institutions hors commune, commissions abstraites.
    """
    from collectors.config import CODE_POSTAL, COMMUNE_NAME
    conn = get_db()
    try:
        # Le prédicat et l'état viennent de `collectors/files.py` : le compte
        # affiché sur « Aujourd'hui » et cette liste sont la MÊME définition.
        # Avant le 23/09/2026, `geo_status` se calculait ici en Python, et rien
        # n'aurait empêché un compte écrit ailleurs de dire autre chose.
        items = rows(conn, f"""
            SELECT e.id, e.type, e.name, e.address, e.lat, e.lng,
                   e.geocode_source, e.geocode_score,
                   ({GEO_ETAT}) AS geo_etat,
                   CASE WHEN {GEO_A_FAIRE} THEN 1 ELSE 0 END AS a_faire,
                   ( (SELECT COUNT(*) FROM financial_flows f WHERE f.from_id=e.id OR f.to_id=e.id)*3
                   + (SELECT COUNT(*) FROM marches_publics m WHERE m.titulaire_id=e.id OR m.acheteur_id=e.id)*3
                   + MIN((SELECT COUNT(*) FROM relations r WHERE (r.from_id=e.id OR r.to_id=e.id)
                          {"" if au_moins(user, "validator") else _SANS_PRESUMES}),12)
                   + CASE WHEN e.type='service' THEN 2 ELSE 0 END ) AS expo
            {GEO_DEPUIS}
            ORDER BY a_faire DESC, expo DESC
            LIMIT ?
        """, (*geo_params(COMMUNE_NAME, CODE_POSTAL), limit))
        # `geo_status` reste servi sous son ancien nom et ses anciennes valeurs :
        # la page `/atelier/geo` les lit, et deux vocabulaires d'un coup, c'est
        # un écran cassé pour une amélioration de nommage.
        ANCIENS = {"absent": "missing", "pose_a_la_main": "ok_manual",
                   "approximatif": "imprecise", "sur": "ok"}
        for r in items:
            r["geo_status"] = ANCIENS[r["geo_etat"]]
        return items
    finally:
        conn.close()


# ─── /api/atelier/ia — sync IA local → atelier hébergé ───────────────────────
# Le batch local (Mac) génère synthèses + embeddings, puis les pousse via ces endpoints.
# La recherche (/api/rag/search) s'ouvre sur un serveur qui porte son propre
# Ollama, sur la boucle locale ; la génération (/api/rag/ask) reste fermée
# tant que RAG_ASK_ENABLED=1 n'est pas posé.

@app.get("/api/atelier/ia/pending")
def ia_pending(
    limit: int = Query(50, le=500),
    user=Depends(require_auth),
):
    """
    Retourne les entités sans synthèse IA (à traiter par le batch local).
    Consommé par generate_syntheses.py en mode remote.
    """
    conn = get_db()
    try:
        # Entités verified/confirmed sans synthèse déjà générée
        pending = rows(conn, """
            SELECT e.id, e.type, e.name, e.short_name, e.address,
                   e.confidence,
                   b.siren, b.naf_label, b.creation_date AS biz_creation,
                   a.rna_id, a.object AS asso_object,
                   p.firstname, p.lastname,
                   s.category AS service_category,
                   (SELECT COUNT(*) FROM relations r
                    WHERE (r.from_id = e.id OR r.to_id = e.id){reserve}) AS nb_relations,
                   (SELECT COUNT(*) FROM entity_notes n WHERE n.entity_id = e.id) AS nb_notes
            FROM entities e
            LEFT JOIN businesses   b ON b.entity_id = e.id
            LEFT JOIN associations a ON a.entity_id = e.id
            LEFT JOIN persons      p ON p.entity_id = e.id
            LEFT JOIN services     s ON s.entity_id = e.id
            WHERE e.confidence IN ('verified','confirmed')
            ORDER BY nb_relations DESC, nb_notes DESC
            LIMIT ?
        """.format(reserve="" if au_moins(user, "validator") else _SANS_PRESUMES), (limit,))

        # Filtrer ceux qui n'ont pas encore de synthèse sur disque
        existing_ids = set()
        for f in SYNTHESES_DIR.glob("*.json"):
            try:
                existing_ids.add(int(f.stem))
            except ValueError:
                pass

        result = [p for p in pending if p["id"] not in existing_ids]
        return {"pending": result, "total": len(result)}
    finally:
        conn.close()


class SynthesisItem(BaseModel):
    entity_id:   int
    entity_name: str
    entity_type: str
    content:     dict    # contenu JSON de la synthèse (clés libres + _entity_id, _generated)


@app.post("/api/atelier/ia/syntheses")
def push_syntheses(
    items: list[SynthesisItem],
    user=Depends(require_au_moins("validator", "Déposer des synthèses")),
):
    """
    Push des synthèses générées en local vers l'atelier.
    Écritures petites, atomiques. Ne modifie pas la DB, écrit sur disque (SYNTHESES_DIR).
    """
    SYNTHESES_DIR.mkdir(parents=True, exist_ok=True)
    saved, skipped = 0, 0
    for item in items:
        if item.entity_id <= 0:
            skipped += 1
            continue
        content = dict(item.content)
        content["_entity_id"]   = item.entity_id
        content["_entity_name"] = item.entity_name
        content["_entity_type"] = item.entity_type
        if "_generated" not in content:
            content["_generated"] = datetime.now(timezone.utc).isoformat()
        path = SYNTHESES_DIR / f"{item.entity_id}.json"
        path.write_text(
            json.dumps(content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved += 1
    return {"ok": True, "saved": saved, "skipped": skipped}


class EmbeddingItem(BaseModel):
    source_table: str
    source_id:    int
    entity_id:    Optional[int] = None
    chunk_text:   str = Field(max_length=20_000)
    # Vecteur float32 (768 dimensions pour nomic-embed-text), fini : un NaN ou
    # un infini rendrait tous les scores illisibles.
    vector:       list[float] = Field(min_length=64, max_length=4096)
    model_config = {"allow_inf_nan": False}
    # Le modèle qui a calculé le vecteur, et son empreinte Ollama : sans eux,
    # la recherche refuse l'index (collectors/index_sens.py).
    modele:           str = Field(pattern=r"^[\w.:/-]{1,80}$")
    modele_empreinte: str = Field(pattern=r"^[0-9a-f]{12}$")


@app.post("/api/atelier/ia/embeddings")
def push_embeddings(
    items: list[EmbeddingItem] = Body(..., max_length=500),
    user=Depends(require_au_moins("validator", "Déposer l'index de recherche")),
):
    """
    Push des vecteurs RAG générés en local vers la DB atelier.
    Upsert par (source_table, source_id) — écritures courtes, hors heures de validation.
    """
    import struct
    conn = get_db_rw()
    saved, skipped = 0, 0
    try:
        # Une seule dimension par index : un vecteur d'une autre taille ferait
        # tomber toutes les recherches (revue vigie-securite du 07/11, E).
        en_place = conn.execute("SELECT LENGTH(vector) FROM embeddings LIMIT 1").fetchone()
        tailles = {len(i.vector) * 4 for i in items} | ({en_place[0]} if en_place else set())
        if len(tailles) > 1:
            raise HTTPException(400, "Vecteurs de dimensions différentes : "
                                     f"{sorted(t // 4 for t in tailles)}.")
        for item in items:
            if not item.vector or not item.chunk_text:
                skipped += 1
                continue
            # Sérialiser le vecteur float32 en bytes (compatible numpy.frombuffer)
            vec_bytes = struct.pack(f"{len(item.vector)}f", *item.vector)
            conn.execute("""
                INSERT INTO embeddings (source_table, source_id, entity_id, chunk_text, vector,
                                        modele, modele_empreinte)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_table, source_id)
                DO UPDATE SET chunk_text=excluded.chunk_text,
                              vector=excluded.vector,
                              modele=excluded.modele,
                              modele_empreinte=excluded.modele_empreinte,
                              created_at=datetime('now')
            """, (item.source_table, item.source_id, item.entity_id,
                  item.chunk_text, vec_bytes, item.modele, item.modele_empreinte))
            saved += 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "saved": saved, "skipped": skipped}

# ─── Interrogation en langage naturel : RETIRÉE du moteur le 15/08/2026 ───────
#
# Deux routes (`/api/ask`, `/api/ask/schema`) traduisaient une question en SQL
# et l'exécutaient sur une base publique dérivée du snapshot. Le dispositif
# était bien construit — isolation physique, EXPLAIN avant exécution, requête
# affichée — mais ni `scripts/ask.py` ni `scripts/build_public_db.py` n'ont été
# portés, et la recherche du site public est en texte clair, sans modèle.
# Deux routes qui répondaient 503 et 500 valaient moins que leur absence.
# L'assistance par modèle reste côté atelier : voir /api/rag/*.


# ─── L'atelier, servi par l'API elle-même ────────────────────────────────────
#
# Quand `dashboard/dist` existe — c'est-à-dire quand l'atelier a été CONSTRUIT,
# ce que fait l'installateur sans ligne de commande — l'API le sert directement.
# Une seule adresse, un seul port, aucun proxy à traverser.
#
# Ce n'est pas un confort : c'est la panne la plus fréquente du dispositif qui
# disparaît. « Serveur inaccessible » affiché par une interface qui tourne très
# bien, parce que le proxy de Vite vise `localhost` là où uvicorn n'écoute qu'en
# IPv4 — le diagnostic le plus coûteux qui soit, puisque tout a l'air en ordre.
#
# Monté EN DERNIER : un `mount("/")` capte tout ce que les routes déclarées
# avant lui n'ont pas pris.
#
# Il faut le DEMANDER (`VIGIE_ATELIER_STATIQUE=1`, ce que fait le lanceur), et
# la présence de `dist/` ne suffit pas. La raison est concrète : une instance
# qui a construit l'atelier une fois, il y a des semaines, garde son `dist/` sur
# le disque. Sans cet interrupteur, elle se mettrait à servir cette version
# périmée à la racine au premier redémarrage de son API — sans que personne
# n'ait rien demandé, et pendant que `pnpm run dev` sert la version à jour sur un
# autre port. Deux ateliers différents à deux adresses, c'est le genre d'écart
# qu'on met une soirée à comprendre.
_ATELIER_STATIQUE = os.environ.get("VIGIE_ATELIER_STATIQUE", "0").strip().lower() \
    not in ("0", "false", "no", "")
_ATELIER_CONSTRUIT = BASE_DIR / "dashboard" / "dist"
if _ATELIER_STATIQUE and _ATELIER_CONSTRUIT.is_dir():
    from starlette.exceptions import HTTPException as _HTTPStarlette
    from starlette.staticfiles import StaticFiles as _StaticFiles

    class _AtelierSPA(_StaticFiles):
        """Une page de l'atelier est une route du NAVIGATEUR, pas un fichier.

        `/entites/1234` n'existe nulle part sur le disque : adapter-static
        laisse `index.html` la résoudre côté client. Sans ce repli, l'atelier
        fonctionnait tant qu'on n'actualisait pas la page — et rendait 404 dès
        qu'on le faisait, ou qu'on ouvrait un lien dans un nouvel onglet.

        Le repli ne vaut PAS pour `/api/…` : une route d'API inconnue doit
        répondre 404, pas rendre une page HTML avec un code 200 qu'un client
        prendrait pour une réponse.
        """

        async def get_response(self, path, scope):
            # StaticFiles ne REND pas un 404 : il le LÈVE. Tester le code de la
            # réponse ne servait donc à rien — le repli n'était jamais atteint
            # et l'atelier rendait 404 sur ses propres pages. Les deux formes
            # sont traitées : l'exception d'aujourd'hui, et le code de retour
            # au cas où une version ultérieure change d'avis.
            try:
                reponse = await super().get_response(path, scope)
            except _HTTPStarlette as e:
                if e.status_code != 404 or path.startswith("api"):
                    raise
                return await super().get_response("index.html", scope)
            if reponse.status_code == 404 and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            return reponse

    app.mount("/", _AtelierSPA(directory=str(_ATELIER_CONSTRUIT), html=True),
              name="atelier")
