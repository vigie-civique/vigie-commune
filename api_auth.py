"""
Atelier — Auth JWT (access 1h, refresh 7j) + bcrypt + lockout
Routes exportées : /api/auth/*, /api/admin/comptes/*
Deps exportées  : require_auth, require_role, require_au_moins, au_moins, exiger,
                  utilisateur_du_jeton

On n'entre dans l'atelier que sur invitation (arbitré par Julien le 17/09/2026) :
le premier compte admin est créé à l'installation (installateur ou
`scripts/create_user.py`) ; ensuite, un admin invite une adresse en choisissant
son rôle, et seul le porteur du lien d'invitation crée le compte.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode, urlsplit

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

try:
    import jwt
except ImportError:
    raise RuntimeError("PyJWT manquant — pip install PyJWT")


def _hash_pw(password: str) -> str:
    pre = hashlib.sha256(password.encode()).hexdigest().encode()
    return bcrypt.hashpw(pre, bcrypt.gensalt()).decode()


def _verify_pw(password: str, hashed: str) -> bool:
    pre = hashlib.sha256(password.encode()).hexdigest().encode()
    return bcrypt.checkpw(pre, hashed.encode())


_leurre: list[str] = []


def _hash_leurre() -> str:
    """Un hash sans compte, pour qu'une adresse inconnue coûte un bcrypt comme
    les autres : sans lui, la réponse rapide disait quelles adresses ont un
    compte (audit du 17/10/2026). Calculé au premier besoin, pas à l'import."""
    if not _leurre:
        _leurre.append(_hash_pw(secrets.token_hex(16)))
    return _leurre[0]

BASE_DIR         = Path(__file__).parent
from collectors.config import DB_PATH
_SECRET          = os.environ.get("JWT_SECRET", "")
_ALGO            = "HS256"

# Sans secret, l'API démarrait sans rien dire et n'échouait qu'à la PREMIÈRE
# tentative de connexion, par une trace dans le terminal du serveur — pendant que
# le navigateur affichait une erreur générique. Le lien entre les deux n'était
# pas déductible, et un `.env` recopié depuis l'exemple (donc au secret vide)
# ramène exactement cette situation.
#
# Un service qui ne peut rien faire d'utile doit refuser de démarrer, à voix
# haute, au moment où on le lance. C'est le seul instant où l'exploitant regarde.
if not _SECRET:
    print(
        "\n  ✖ JWT_SECRET absent : l'atelier ne peut authentifier personne.\n"
        "\n    cp deploy/env.exemple .env && chmod 600 .env\n"
        "    puis renseigner JWT_SECRET (openssl rand -hex 32)\n"
        "\n    Attention : recopier l'exemple par-dessus un .env existant\n"
        "    REMET le secret à vide.\n",
        file=sys.stderr,
    )
    raise SystemExit(1)
_ACCESS_MINUTES  = 60
_REFRESH_MINUTES = 60 * 24 * 7
# Chaque rotation repartait pour sept jours, sans fin : une session volée se
# renouvelait indéfiniment. Elle a maintenant un âge, compté depuis la connexion
# (`auth_time` dans le jeton) et que la rotation ne remet pas à zéro.
SESSION_MAX_JOURS = 30
# Deux onglets qui rafraîchissent au même instant présentent le même jeton : le
# second arrive juste après la révocation. Refusé, mais sans y voir un vol.
REUTILISATION_TOLERANCE_S = 10

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Le jeton de session voyage dans SON en-tête, et non plus seulement dans
# `Authorization: Bearer`. Un atelier en ligne se place derrière un mur HTTP
# (`auth_basic` de nginx), et ce mur parle par `Authorization` lui aussi : un
# navigateur n'en envoie qu'un. Avec le jeton dans `Authorization`, nginx
# refusait chaque appel de l'interface, mot de passe du mur pourtant donné.
# `Authorization: Bearer` reste lu, pour les scripts et les ateliers sans mur.
EN_TETE_SESSION = "x-atelier-session"


def jeton_de(request: Request) -> Optional[str]:
    jeton = (request.headers.get(EN_TETE_SESSION) or "").strip()
    if jeton:
        return jeton
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


#: Bases dont les tables de comptes ont déjà été mises à niveau.
_COMPTES_PRETS: set[str] = set()

#: Rattrapé ici ET déclaré dans `db/schema.sql` : l'API ne passe pas par
#: `init_db()`, et une base d'avant le 17/09/2026 n'a ni l'une ni l'autre.
_INVITATIONS_SQL = """
CREATE TABLE IF NOT EXISTS invitations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL CHECK(role IN ('admin','validator','contributor')),
    nature       TEXT NOT NULL DEFAULT 'compte' CHECK(nature IN ('compte','mot_de_passe')),
    jeton_sha256 TEXT NOT NULL UNIQUE,
    invite_par   INTEGER REFERENCES users(id),
    cree_le      TEXT DEFAULT (datetime('now')),
    expire_le    TEXT NOT NULL,
    utilisee_le  TEXT,
    annulee_le   TEXT
)"""


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    cle = str(DB_PATH)
    if cle not in _COMPTES_PRETS:
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if colonnes:
            # `desactive_le` : un compte ne se supprime pas — `audit_log` le
            # référence, et effacer l'auteur effacerait la trace.
            # `sessions_version` : un mot de passe changé met fin aux sessions
            # ouvertes avant, au lieu de les laisser courir sept jours.
            for colonne, definition in (("desactive_le", "TEXT"),
                                        ("sessions_version", "INTEGER DEFAULT 0")):
                if colonne not in colonnes:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {colonne} {definition}")
            conn.execute(_INVITATIONS_SQL)
            conn.commit()
            _COMPTES_PRETS.add(cle)
    return conn


def _get(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def _make_token(sub: str, kind: str, minutes: int, version: int = 0,
                origine: Optional[int] = None) -> str:
    if not _SECRET:
        raise RuntimeError("JWT_SECRET absent : le renseigner dans .env "
                           "(cp deploy/env.exemple .env, puis openssl rand -hex 32)")
    maintenant = datetime.now(timezone.utc)
    exp = maintenant + timedelta(minutes=minutes)
    # `sv` : la génération de sessions du compte. Un changement de mot de passe
    # l'incrémente, et tout jeton d'une génération antérieure est refusé. Une
    # date (`iat`, à la seconde) laissait passer les jetons de la même seconde.
    # `jti` : deux jetons de la même seconde seraient sinon identiques, et
    # révoquer l'ancien à la rotation (`/refresh`) révoquerait aussi le neuf.
    # `auth_time` : l'instant de la CONNEXION, recopié de jeton en jeton par la
    # rotation. `iat` date le jeton, pas la session.
    iat = int(maintenant.timestamp())
    return jwt.encode({"sub": sub, "kind": kind, "exp": exp, "iat": iat,
                       "auth_time": iat if origine is None else origine,
                       "sv": version, "jti": secrets.token_hex(8)}, _SECRET, algorithm=_ALGO)


def _decode(token: str) -> dict:
    if not _SECRET:
        raise HTTPException(503, "JWT non configuré — JWT_SECRET manquant dans .env")
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, str(exc))


def _revoked(token: str, conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM revoked_tokens WHERE jti=? AND expires_at > datetime('now')",
        (token,),
    ).fetchone() is not None


# ─── Rôles ─────────────────────────────────────────────────────────────────────
# EMBOÎTÉS — arbitré par Julien le 17/09/2026. Le contributeur propose sans
# trancher ; le validateur fait tout ce que fait le contributeur, tranche, et
# voit les analyses ; l'admin fait tout ce que fait le validateur, gère les
# comptes et publie. Jusque-là `contributor` et `validator` avaient exactement
# les mêmes droits : un contributeur rendait une fiche publiable.

ROLES = ("contributor", "validator", "admin")
RANG = {r: i for i, r in enumerate(ROLES)}
LIBELLE_ROLE = {"contributor": "contributeur", "validator": "validateur",
                "admin": "administrateur"}


def au_moins(user: Optional[dict], role: str) -> bool:
    return bool(user) and RANG.get(user.get("role"), -1) >= RANG[role]


def exiger(user: Optional[dict], role: str, geste: str) -> None:
    """403 lisible si `user` n'a pas au moins `role` pour `geste`."""
    if not au_moins(user, role):
        actuel = LIBELLE_ROLE.get((user or {}).get("role"), "non identifié")
        raise HTTPException(403, {
            "message": f"{geste} : réservé au rôle {LIBELLE_ROLE[role]}"
                       f"{'' if role == 'admin' else ' et au-dessus'} "
                       f"— vous êtes {actuel}.",
            "role_requis": role})


# ─── Dépendances FastAPI ────────────────────────────────────────────────────────

def utilisateur_du_jeton(jeton: str, kind: str = "access") -> dict:
    """Le compte d'un jeton valide, ou 401. Seul endroit où une session est
    jugée : le verrou global de `api.py`, `require_auth` et `/refresh` l'appellent
    tous — trois copies de ce contrôle auraient divergé au premier ajout."""
    payload = _decode(jeton)
    if payload.get("kind") != kind:
        raise HTTPException(401, "Token d'accès requis" if kind == "access"
                            else "Token de rafraîchissement requis")
    conn = _db()
    try:
        if _revoked(jeton, conn):
            raise HTTPException(401, "Token révoqué")
        return _compte_du_jeton(conn, payload)
    finally:
        conn.close()


def _compte_du_jeton(conn, payload: dict) -> dict:
    """Le compte que désigne un jeton déjà décodé, ou 401 : il existe, il n'est
    pas désactivé, et le jeton est de sa génération de sessions courante."""
    user = _get(conn, "SELECT id, email, role, desactive_le, sessions_version "
                      "FROM users WHERE email=?", (payload.get("sub"),))
    if not user:
        raise HTTPException(401, "Utilisateur introuvable")
    if user.pop("desactive_le"):
        raise HTTPException(401, "Compte désactivé")
    if payload.get("sv", 0) != (user.pop("sessions_version") or 0):
        raise HTTPException(401, "Session close : le mot de passe a changé depuis")
    return user


def require_auth(jeton: Optional[str] = Depends(jeton_de)):
    if not jeton:
        raise HTTPException(401, "Token manquant")
    return utilisateur_du_jeton(jeton, "access")


def require_role(*roles):
    def _dep(user=Depends(require_auth)):
        if user["role"] not in roles:
            raise HTTPException(403, f"Rôle requis : {', '.join(roles)}")
        return user
    return _dep


def require_au_moins(role: str, geste: str = "Cette action"):
    def _dep(user=Depends(require_auth)):
        exiger(user, role, geste)
        return user
    return _dep


# ─── Les documents : un lien signé, ouvert sur une autre origine ────────────────
# Un document archivé vient d'un site tiers ou d'un dépôt : son contenu n'est
# pas de confiance. L'atelier l'affichait en `blob:`, c'est-à-dire À SON
# ADRESSE, dans un onglet relié à la fenêtre de l'atelier : le lecteur PDF du
# navigateur était le seul rempart (audit du 17/10/2026, S6). Il s'ouvre
# maintenant sur l'hôte `DOCS_URL`, qui ne porte ni session ni cookie de
# l'atelier, par un lien que l'API signe pour un compte et cinq minutes.
#
# La clé est dérivée de JWT_SECRET, jamais JWT_SECRET lui-même : une signature
# de lien ne doit pas pouvoir servir de jeton, ni l'inverse. Le lien porte la
# génération de sessions du compte (`sv`) : un mot de passe changé, ou un compte
# désactivé, éteint aussi les liens déjà donnés.

DUREE_LIEN_DOCUMENT = 300


def _signature_document(doc_id: int, exp: int, uid: int, sv: int) -> str:
    cle = hmac.new(_SECRET.encode(), b"vigie-documents", hashlib.sha256).digest()
    return hmac.new(cle, f"{doc_id}.{exp}.{uid}.{sv}".encode(), hashlib.sha256).hexdigest()


def signer_lien_document(doc_id: int, user: dict) -> dict:
    """Les paramètres du lien (`exp`, `u`, `sig`) pour ce document et ce compte."""
    conn = _db()
    try:
        compte = _get(conn, "SELECT sessions_version FROM users WHERE id=?", (user["id"],))
    finally:
        conn.close()
    exp = int(time.time()) + DUREE_LIEN_DOCUMENT
    sv = (compte or {}).get("sessions_version") or 0
    return {"exp": exp, "u": user["id"], "sig": _signature_document(doc_id, exp, user["id"], sv)}


def lien_document_valide(doc_id: int, exp: int, uid: int, sig: str) -> bool:
    maintenant = int(time.time())
    # Une échéance plus lointaine que la durée d'un lien ne vient pas de l'API.
    if not (maintenant <= exp <= maintenant + DUREE_LIEN_DOCUMENT + 60):
        return False
    conn = _db()
    try:
        compte = _get(conn, "SELECT desactive_le, sessions_version FROM users WHERE id=?", (uid,))
    finally:
        conn.close()
    if not compte or compte["desactive_le"]:
        return False
    attendue = _signature_document(doc_id, exp, uid, compte["sessions_version"] or 0)
    return hmac.compare_digest(attendue.encode(), sig.encode())


# ─── Le cookie ne part que de l'atelier lui-même ────────────────────────────────
# `SameSite=Strict` arrête un AUTRE SITE, pas un sous-domaine : `docs.` et
# l'atelier sont du même site, et une page servie sur `docs.` pouvait faire
# partir le cookie vers `/refresh` ou `/logout` (sans lire la réponse, mais en
# fermant la session). Une requête qui annonce une autre origine que l'hôte
# appelé, hors des origines déclarées, est refusée.

def _origine_admise(request: Request) -> None:
    origine = request.headers.get("origin")
    if not origine:
        return                                   # un script, pas un navigateur
    admises = [o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",")]
    if origine in admises or urlsplit(origine).netloc == request.headers.get("host", ""):
        return
    raise HTTPException(403, "Origine refusée.")


# ─── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    # Absent : le jeton est lu dans le cookie posé à la connexion (l'interface).
    # Fourni : c'est un script, qui n'a pas de cookie.
    refresh_token: Optional[str] = None


# ─── Le jeton de rafraîchissement, hors de portée des scripts ───────────────────
# Il vit sept jours. L'interface le rangeait dans `localStorage`, que tout script
# de la page peut lire : une seule injection dans l'atelier, et la session d'un
# administrateur partait pour une semaine. Il voyage maintenant dans un cookie
# `HttpOnly` — le navigateur l'envoie, aucun script ne le lit.
#
# `SameSite=Strict` et un chemin réduit à `/api/auth` : un autre site ne peut pas
# le faire envoyer, et il ne part avec aucune autre requête de l'atelier.
# `Secure` dès que l'atelier est servi en https (un atelier local en http garde
# un cookie utilisable).

COOKIE_SESSION = "atelier_refresh"


def _poser_cookie(response: Response, request: Request, jeton: str) -> None:
    response.set_cookie(COOKIE_SESSION, jeton, max_age=_REFRESH_MINUTES * 60,
                        path="/api/auth", httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")


def _retirer_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_SESSION, path="/api/auth")


# ─── Les essais ratés, comptés par compte ET par origine ────────────────────────
# Cinq mots de passe faux verrouillaient le COMPTE un quart d'heure, d'où que
# viennent les essais : qui connaissait l'adresse de l'administrateur pouvait
# l'empêcher d'entrer indéfiniment. Le verrou porte désormais sur le couple
# (adresse du compte, adresse d'origine) : celui qui se trompe est arrêté, pas
# celui qu'il vise. En mémoire — un redémarrage l'efface, et c'est sans gravité :
# la borne de débit de nginx et fail2ban tiennent la porte en amont.

ECHECS_MAX = 5
ECHECS_FENETRE_S = 15 * 60
_echecs: dict[tuple[str, str], list[float]] = {}


def _origine(request: Request) -> str:
    return request.client.host if request.client else "?"


def _essais_recents(cle: tuple[str, str]) -> list[float]:
    limite = time.monotonic() - ECHECS_FENETRE_S
    recents = [t for t in _echecs.get(cle, []) if t > limite]
    if recents:
        _echecs[cle] = recents
    else:
        _echecs.pop(cle, None)
    return recents


def _noter_echec(cle: tuple[str, str]) -> None:
    if len(_echecs) > 10_000:           # une table qui ne grossit pas sans fin
        for c in list(_echecs):
            _essais_recents(c)
    _echecs.setdefault(cle, []).append(time.monotonic())


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


# ─── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    email = req.email.lower().strip()
    cle = (email, _origine(request))
    if len(_essais_recents(cle)) >= ECHECS_MAX:
        raise HTTPException(429, f"Trop d'essais ratés depuis cette connexion : attendre "
                                 f"{ECHECS_FENETRE_S // 60} minutes avant de réessayer.")
    conn = _db()
    try:
        user = _get(conn, "SELECT * FROM users WHERE email=?", (email,))

        # Un compte inexistant passe aussi par bcrypt, contre un hash leurre : la
        # réponse ne doit dire quelles adresses ont un compte ni par son contenu,
        # ni par son temps.
        if not _verify_pw(req.password, user["password_hash"] if user else _hash_leurre()) \
                or not user:
            _noter_echec(cle)
            raise HTTPException(401, "Email ou mot de passe incorrect")

        # Même réponse qu'un mot de passe faux : un 403 « désactivé » disait
        # que le mot de passe était juste (audit du 15/11/2026).
        if user.get("desactive_le"):
            _noter_echec(cle)
            raise HTTPException(401, "Email ou mot de passe incorrect")

        conn.execute(
            "UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=datetime('now') WHERE id=?",
            (user["id"],),
        )
        conn.commit()
        _echecs.pop(cle, None)

        return {
            **_sessions(conn, user["id"], request, response),
            "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        }
    finally:
        conn.close()


@router.post("/refresh")
def refresh_token(request: Request, response: Response,
                  req: Optional[RefreshRequest] = None):
    """Rotation : le jeton présenté est révoqué, un neuf part dans le cookie.

    Un jeton ne sert qu'une fois. Le présenter une SECONDE fois, c'est qu'il en
    existe deux copies : toutes les sessions du compte sont alors closes, celle
    du voleur comprise. Jusqu'au 13/11/2026 la seconde présentation n'était que
    refusée : si le voleur passait le premier, la personne légitime était
    déconnectée et lui gardait une session renouvelable sans fin.
    """
    _origine_admise(request)
    jeton = (req.refresh_token if req else None) or request.cookies.get(COOKIE_SESSION)
    if not jeton:
        raise HTTPException(401, "Session absente : se reconnecter.")
    payload = _decode(jeton)
    if payload.get("kind") != "refresh":
        raise HTTPException(401, "Token de rafraîchissement requis")
    conn = _db()
    try:
        # Lire et révoquer dans UNE transaction d'écriture : sur deux connexions,
        # deux requêtes simultanées lisaient toutes deux « pas révoqué » et
        # repartaient chacune avec une session.
        conn.execute("BEGIN IMMEDIATE")
        deja = _get(conn, "SELECT strftime('%s','now') - strftime('%s', revoked_at) AS age "
                          "FROM revoked_tokens WHERE jti=?", (jeton,))
        if deja:
            _reutilisation(conn, payload, deja["age"])
            conn.commit()
            raise HTTPException(401, "Token révoqué")
        conn.execute("INSERT INTO revoked_tokens(jti, expires_at) VALUES(?,?)",
                     (jeton, datetime.fromtimestamp(payload["exp"], tz=timezone.utc).isoformat()))
        conn.commit()
        user = _compte_du_jeton(conn, payload)
    finally:
        conn.close()
    # Un jeton d'avant `auth_time` date sa session de sa propre émission.
    origine = int(payload.get("auth_time") or payload.get("iat") or 0)
    if time.time() - origine > SESSION_MAX_JOURS * 86400:
        raise HTTPException(401, f"Session ouverte depuis plus de {SESSION_MAX_JOURS} "
                                 f"jours : se reconnecter.")
    sv = payload.get("sv", 0)
    _poser_cookie(response, request,
                  _make_token(user["email"], "refresh", _REFRESH_MINUTES, sv, origine))
    return {
        "access_token": _make_token(user["email"], "access", _ACCESS_MINUTES, sv, origine),
        "token_type":   "bearer",
    }


def _reutilisation(conn, payload: dict, age_s) -> None:
    """Un jeton de rafraîchissement déjà révoqué revient : clore les sessions du
    compte, sauf si la révocation date de quelques secondes (deux onglets) ou si
    les sessions de cette génération sont déjà closes."""
    if age_s is not None and age_s <= REUTILISATION_TOLERANCE_S:
        return
    user = _get(conn, "SELECT id, email, COALESCE(sessions_version, 0) AS v "
                      "FROM users WHERE email=?", (payload.get("sub"),))
    if not user or user["v"] != payload.get("sv", 0):
        return
    _clore_sessions(conn, user["id"])
    _journal_compte(conn, None, "sessions_closes", user["email"], None,
                    "jeton de rafraîchissement présenté deux fois")


@router.post("/logout")
def logout(request: Request, response: Response, req: Optional[LogoutRequest] = None,
           jeton_acces: Optional[str] = Depends(jeton_de)):
    """Révoque le jeton d'accès ET, s'il est fourni, celui de rafraîchissement.

    Seul le jeton d'accès (une heure) était révoqué. Celui de rafraîchissement
    (sept jours, gardé dans le navigateur) en refabriquait un neuf : après
    « Déconnexion », la session survivait une semaine — constaté le 17/09/2026.
    """
    _origine_admise(request)
    jetons = [jeton_acces] if jeton_acces else []
    for rafraichissement in ((req.refresh_token if req else None),
                             request.cookies.get(COOKIE_SESSION)):
        if rafraichissement:
            jetons.append(rafraichissement)
    _retirer_cookie(response)
    conn = _db()
    try:
        for jeton in jetons:
            try:
                payload = _decode(jeton)
            except HTTPException:
                continue                     # déjà expiré ou illisible : rien à révoquer
            exp_iso = datetime.fromtimestamp(payload["exp"], tz=timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO revoked_tokens(jti, expires_at) VALUES(?,?)",
                (jeton, exp_iso),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/me")
def me(user=Depends(require_auth)):
    return user


# ─── Son propre mot de passe ────────────────────────────────────────────────────

MOT_DE_PASSE_MIN = 12
INVITATION_JOURS = 7


def _sessions(conn, user_id: int, request: Request, response: Response) -> dict:
    """Jetons neufs, de la génération de sessions COURANTE du compte.

    Le jeton de rafraîchissement ne part que dans le cookie `HttpOnly`. Il
    sortait aussi dans le corps JSON, où un script injecté le lisait : le cookie
    ne protégeait plus rien (audit du 17/10/2026). L'interface ne s'en servait pas.
    """
    u = _get(conn, "SELECT email, COALESCE(sessions_version, 0) AS v FROM users WHERE id=?",
             (user_id,))
    _poser_cookie(response, request,
                  _make_token(u["email"], "refresh", _REFRESH_MINUTES, u["v"]))
    return {
        "access_token":  _make_token(u["email"], "access",  _ACCESS_MINUTES, u["v"]),
        "token_type":    "bearer",
    }


def _verifier_mot_de_passe(motdepasse: str) -> None:
    if len(motdepasse) < MOT_DE_PASSE_MIN:
        raise HTTPException(400, f"Le mot de passe doit faire au moins "
                                 f"{MOT_DE_PASSE_MIN} caractères.")


def _journal_compte(conn, auteur_id: Optional[int], action: str, cible: str,
                    avant: Optional[str] = None, apres: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, entity_id, table_name, action, field, "
        "old_value, new_value) VALUES(?,?,?,?,?,?,?)",
        (auteur_id, None, "users", action, cible, avant, apres))


def _clore_sessions(conn, user_id: int) -> None:
    """Toute session ouverte jusqu'ici est refusée (cf. `sv` dans le jeton)."""
    conn.execute("UPDATE users SET sessions_version = COALESCE(sessions_version, 0) + 1 "
                 "WHERE id=?", (user_id,))


class ChangementMotDePasse(BaseModel):
    actuel: str
    nouveau: str


@router.post("/mot-de-passe")
def changer_mot_de_passe(req: ChangementMotDePasse, request: Request, response: Response,
                         user=Depends(require_auth)):
    """Seule la ligne de commande changeait un mot de passe — donc personne, sauf
    celui qui tient la machine. Les autres sessions du compte sont closes ; celle
    qui change le mot de passe reçoit des jetons neufs."""
    _verifier_mot_de_passe(req.nouveau)
    conn = _db()
    try:
        ligne = _get(conn, "SELECT password_hash FROM users WHERE id=?", (user["id"],))
        if not ligne or not _verify_pw(req.actuel, ligne["password_hash"]):
            raise HTTPException(403, "Le mot de passe actuel est incorrect.")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (_hash_pw(req.nouveau), user["id"]))
        _clore_sessions(conn, user["id"])
        _journal_compte(conn, user["id"], "mot_de_passe", user["email"])
        conn.commit()
        return {"ok": True, **_sessions(conn, user["id"], request, response)}
    finally:
        conn.close()


@router.get("/etat")
def etat_des_comptes():
    """L'atelier a-t-il déjà un compte ? La page de connexion le dit : sans
    compte, personne ne peut en inviter, et le premier se crée à l'installation."""
    conn = _db()
    try:
        return {"comptes": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0}
    finally:
        conn.close()


# ─── Invitations ─────────────────────────────────────────────────────────────────
# Le lien porte un jeton de 32 octets tiré au sort ; la base n'en garde que
# l'empreinte. Usage unique, sept jours. Il circule dans le FRAGMENT de l'adresse
# (`#…`), que le navigateur n'envoie jamais au serveur : il ne finit ni dans un
# journal d'accès, ni dans un en-tête Referer. L'interface le poste ensuite.

def _empreinte(jeton: str) -> str:
    return hashlib.sha256(jeton.encode()).hexdigest()


def _etat_invitation(inv: dict) -> str:
    if inv["utilisee_le"]:
        return "utilisee"
    if inv["annulee_le"]:
        return "annulee"
    if inv["expire_le"] <= datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        return "expiree"
    return "en_attente"


def _creer_invitation(conn, email: str, role: str, nature: str, par: dict) -> tuple[dict, str]:
    """Annule les invitations encore ouvertes pour cette adresse et cette nature :
    un seul lien valide à la fois."""
    conn.execute("UPDATE invitations SET annulee_le=datetime('now') WHERE email=? "
                 "AND nature=? AND utilisee_le IS NULL AND annulee_le IS NULL",
                 (email, nature))
    jeton = secrets.token_urlsafe(32)
    cur = conn.execute(
        "INSERT INTO invitations(email, role, nature, jeton_sha256, invite_par, expire_le) "
        f"VALUES(?,?,?,?,?, datetime('now', '+{INVITATION_JOURS} days'))",
        (email, role, nature, _empreinte(jeton), par["id"]))
    _journal_compte(conn, par["id"], "invitation" if nature == "compte" else "lien_mot_de_passe",
                    email, None, role)
    return _invitation(conn, cur.lastrowid), jeton


def _invitation(conn, inv_id: int) -> dict:
    inv = _get(conn, """
        SELECT i.id, i.email, i.role, i.nature, i.cree_le, i.expire_le, i.utilisee_le,
               i.annulee_le, u.email AS invite_par
        FROM invitations i LEFT JOIN users u ON u.id = i.invite_par WHERE i.id=?""", (inv_id,))
    inv["etat"] = _etat_invitation(inv)
    return inv


def _invitation_du_jeton(conn, jeton: str) -> dict:
    inv = _get(conn, "SELECT id FROM invitations WHERE jeton_sha256=?", (_empreinte(jeton or ""),))
    if not inv:
        raise HTTPException(404, "Ce lien d'invitation n'est pas reconnu. "
                                 "Demandez-en un nouveau à un administrateur.")
    inv = _invitation(conn, inv["id"])
    messages = {
        "utilisee": "Ce lien a déjà servi : il n'est valable qu'une fois. Connectez-vous, "
                    "ou demandez un nouveau lien à un administrateur.",
        "annulee": "Ce lien a été annulé ou remplacé par un plus récent.",
        "expiree": f"Ce lien a expiré (valable {INVITATION_JOURS} jours). "
                   "Demandez-en un nouveau à un administrateur.",
    }
    if inv["etat"] in messages:
        raise HTTPException(410, messages[inv["etat"]])
    return inv


class JetonInvitation(BaseModel):
    jeton: str


class AcceptationInvitation(BaseModel):
    jeton: str
    motdepasse: str


@router.post("/invitation/lire")
def lire_invitation(req: JetonInvitation):
    conn = _db()
    try:
        inv = _invitation_du_jeton(conn, req.jeton)
    finally:
        conn.close()
    return {k: inv[k] for k in ("email", "role", "nature", "expire_le", "invite_par")} | {
        "role_libelle": LIBELLE_ROLE[inv["role"]]}


@router.post("/invitation/accepter")
def accepter_invitation(req: AcceptationInvitation, request: Request, response: Response):
    """Crée le compte invité (ou pose le nouveau mot de passe), et ouvre la session."""
    _verifier_mot_de_passe(req.motdepasse)
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        inv = _invitation_du_jeton(conn, req.jeton)
        if inv["nature"] == "compte":
            if _get(conn, "SELECT 1 FROM users WHERE email=?", (inv["email"],)):
                raise HTTPException(409, "Un compte existe déjà pour cette adresse : "
                                         "connectez-vous.")
            cur = conn.execute("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
                               (inv["email"], _hash_pw(req.motdepasse), inv["role"]))
            user = {"id": cur.lastrowid, "email": inv["email"], "role": inv["role"]}
            _journal_compte(conn, user["id"], "inscription", user["email"], None, user["role"])
        else:
            user = _get(conn, "SELECT id, email, role, desactive_le FROM users WHERE email=?",
                        (inv["email"],))
            if not user or user.pop("desactive_le"):
                raise HTTPException(410, "Ce compte n'existe plus ou a été désactivé.")
            conn.execute("UPDATE users SET password_hash=?, failed_attempts=0, "
                         "locked_until=NULL WHERE id=?", (_hash_pw(req.motdepasse), user["id"]))
            _clore_sessions(conn, user["id"])
            _journal_compte(conn, user["id"], "mot_de_passe", user["email"])
        conn.execute("UPDATE invitations SET utilisee_le=datetime('now') WHERE id=?", (inv["id"],))
        conn.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
        conn.commit()
        return {**_sessions(conn, user["id"], request, response), "user": user}
    finally:
        conn.close()


# ─── Comptes (admin) ────────────────────────────────────────────────────────────
# Inviter, changer de rôle, désactiver, envoyer un lien de mot de passe. Un compte
# ne se supprime pas : il se désactive, et ce qu'il a fait reste attribué.

comptes = APIRouter(prefix="/api/admin/comptes", tags=["comptes"])
_ADMIN = "Gérer les comptes"


class InvitationDemandee(BaseModel):
    email: str
    role: str = "contributor"


class CompteModifie(BaseModel):
    role: Optional[str] = None
    actif: Optional[bool] = None


def _admins_actifs(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' "
                        "AND desactive_le IS NULL").fetchone()[0]


def _adresse(email: str) -> str:
    email = (email or "").strip().lower()
    local, _, domaine = email.partition("@")
    if not local or "." not in domaine or " " in email or len(email) > 254:
        raise HTTPException(400, "Adresse électronique invalide.")
    return email


@comptes.get("")
def lister_comptes(user=Depends(require_au_moins("admin", _ADMIN))):
    conn = _db()
    try:
        comptes_ = [dict(r) for r in conn.execute(
            "SELECT id, email, role, last_login, created_at, desactive_le, locked_until "
            "FROM users ORDER BY desactive_le IS NOT NULL, email")]
        invitations = [_invitation(conn, r[0]) for r in conn.execute(
            "SELECT id FROM invitations ORDER BY id DESC LIMIT 200")]
    finally:
        conn.close()
    return {"comptes": comptes_, "invitations": invitations,
            "roles": [{"cle": r, "libelle": LIBELLE_ROLE[r]} for r in ROLES],
            "invitation_jours": INVITATION_JOURS,
            # Adresse à mettre dans les liens. Vide : l'interface prend celle par
            # laquelle l'admin consulte l'atelier.
            "adresse_atelier": os.environ.get("ATELIER_URL", "").rstrip("/")}


@comptes.post("/invitations", status_code=201)
def inviter(req: InvitationDemandee, user=Depends(require_au_moins("admin", _ADMIN))):
    """Le jeton n'est rendu QU'ICI, une fois : la base n'en garde que l'empreinte."""
    email = _adresse(req.email)
    if req.role not in ROLES:
        raise HTTPException(400, f"Rôle inconnu — valeurs : {', '.join(ROLES)}")
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existant = _get(conn, "SELECT id, desactive_le FROM users WHERE email=?", (email,))
        if existant:
            raise HTTPException(409, "Cette adresse a déjà un compte"
                                + (" (désactivé : le réactiver plutôt que l'inviter)."
                                   if existant["desactive_le"] else "."))
        inv, jeton = _creer_invitation(conn, email, req.role, "compte", user)
        conn.commit()
    finally:
        conn.close()
    return {"invitation": inv, "jeton": jeton}


@comptes.post("/invitations/{inv_id}/renouveler", status_code=201)
def renouveler_invitation(inv_id: int, user=Depends(require_au_moins("admin", _ADMIN))):
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ancienne = _get(conn, "SELECT email, role, nature FROM invitations WHERE id=?", (inv_id,))
        if not ancienne:
            raise HTTPException(404, "Invitation introuvable.")
        if ancienne["nature"] == "compte" and _get(conn, "SELECT 1 FROM users WHERE email=?",
                                                   (ancienne["email"],)):
            raise HTTPException(409, "Cette adresse a déjà créé son compte.")
        inv, jeton = _creer_invitation(conn, ancienne["email"], ancienne["role"],
                                       ancienne["nature"], user)
        conn.commit()
    finally:
        conn.close()
    return {"invitation": inv, "jeton": jeton}


@comptes.delete("/invitations/{inv_id}")
def annuler_invitation(inv_id: int, user=Depends(require_au_moins("admin", _ADMIN))):
    conn = _db()
    try:
        inv = _get(conn, "SELECT email FROM invitations WHERE id=? AND utilisee_le IS NULL "
                         "AND annulee_le IS NULL", (inv_id,))
        if not inv:
            raise HTTPException(404, "Aucune invitation ouverte à ce numéro.")
        conn.execute("UPDATE invitations SET annulee_le=datetime('now') WHERE id=?", (inv_id,))
        _journal_compte(conn, user["id"], "invitation_annulee", inv["email"])
        conn.commit()
        return _invitation(conn, inv_id)
    finally:
        conn.close()


@comptes.patch("/{compte_id}")
def modifier_compte(compte_id: int, req: CompteModifie,
                    user=Depends(require_au_moins("admin", _ADMIN))):
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cible = _get(conn, "SELECT * FROM users WHERE id=?", (compte_id,))
        if not cible:
            raise HTTPException(404, "Compte introuvable.")
        if req.role is not None and req.role not in ROLES:
            raise HTTPException(400, f"Rôle inconnu — valeurs : {', '.join(ROLES)}")
        perd_admin = (cible["role"] == "admin" and cible["desactive_le"] is None
                      and ((req.role is not None and req.role != "admin")
                           or req.actif is False))
        if perd_admin and _admins_actifs(conn) <= 1:
            raise HTTPException(409, "C'est le dernier administrateur actif : "
                                     "l'atelier n'aurait plus personne pour gérer les comptes.")
        if req.actif is False and compte_id == user["id"]:
            raise HTTPException(409, "On ne désactive pas son propre compte : "
                                     "demander à un autre administrateur.")
        if req.role is not None and req.role != cible["role"]:
            conn.execute("UPDATE users SET role=? WHERE id=?", (req.role, compte_id))
            _journal_compte(conn, user["id"], "role", cible["email"], cible["role"], req.role)
        if req.actif is False and cible["desactive_le"] is None:
            conn.execute("UPDATE users SET desactive_le=datetime('now') WHERE id=?", (compte_id,))
            # Réactivé, le compte retrouvait ses anciennes sessions.
            _clore_sessions(conn, compte_id)
            _journal_compte(conn, user["id"], "desactivation", cible["email"])
        elif req.actif is True and cible["desactive_le"] is not None:
            conn.execute("UPDATE users SET desactive_le=NULL WHERE id=?", (compte_id,))
            _journal_compte(conn, user["id"], "reactivation", cible["email"])
        if perd_admin:
            # Les liens envoyés par un admin qui ne l'est plus ne valent plus rien.
            conn.execute("UPDATE invitations SET annulee_le=datetime('now') WHERE invite_par=? "
                         "AND utilisee_le IS NULL AND annulee_le IS NULL", (compte_id,))
        conn.commit()
        return _get(conn, "SELECT id, email, role, last_login, created_at, desactive_le, "
                          "locked_until FROM users WHERE id=?", (compte_id,))
    finally:
        conn.close()


@comptes.post("/{compte_id}/lien-mot-de-passe", status_code=201)
def lien_mot_de_passe(compte_id: int, user=Depends(require_au_moins("admin", _ADMIN))):
    """Mot de passe oublié : un lien à usage unique, comme une invitation.
    L'admin ne voit ni ne choisit jamais le mot de passe de quelqu'un d'autre."""
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cible = _get(conn, "SELECT email, role, desactive_le FROM users WHERE id=?", (compte_id,))
        if not cible:
            raise HTTPException(404, "Compte introuvable.")
        if cible["desactive_le"]:
            raise HTTPException(409, "Compte désactivé : le réactiver d'abord.")
        inv, jeton = _creer_invitation(conn, cible["email"], cible["role"], "mot_de_passe", user)
        conn.commit()
    finally:
        conn.close()
    return {"invitation": inv, "jeton": jeton}


# ─── L'aperçu du site, derrière les comptes ─────────────────────────────────────
# L'hôte d'aperçu (`VIGIE_APERCU_URL`) n'était gardé que par le mur `auth_basic`,
# un identifiant commun : un bénévole parti gardait l'aperçu tant que le mot de
# passe de tous n'avait pas changé (audit du 15/11/2026, B3). Le compte montré
# par le serveur d'aperçu venait en outre d'un cookie que le visiteur écrit.
#
# Le jeton de session de l'atelier voyage dans `X-Atelier-Session`, qu'une
# navigation vers un autre hôte n'envoie pas. Le passage :
#
#   1. l'aperçu, sans témoin valide, renvoie (nginx : `auth_request` → 403 →
#      `error_page`) vers `/api/auth/apercu/ouvrir<chemin>` sur l'ATELIER ;
#   2. là, le cookie de rafraîchissement de l'atelier (HttpOnly, SameSite=Strict,
#      chemin `/api/auth`) dit qui est là ; on frappe un CODE à usage unique,
#      valable une minute, et on renvoie vers l'entrée de l'aperçu ;
#   3. l'entrée (`/__vigie/entree` sur l'hôte d'aperçu) consomme le code et pose
#      un témoin propre à cet hôte (sans `Domain`), d'un quart d'heure ;
#   4. à chaque requête, nginx fait juger ce témoin (`/api/auth/apercu/controle`)
#      et transmet au serveur d'aperçu le compte qu'il désigne (`X-Vigie-Compte`).
#
# Le témoin porte la génération de sessions du compte (`sv`) et il est rejugé à
# chaque requête : `_clore_sessions` (mot de passe changé, jeton volé) et la
# désactivation le ferment à la requête suivante, sans attendre son heure.
# Une clé dérivée de JWT_SECRET par usage : un code ne vaut pas témoin, et ni
# l'un ni l'autre ne vaut jeton de session ou lien de document.

APERCU_PASSAGE_S = 60
#: Court : la déconnexion de l'atelier ne le ferme pas, et le renouveler ne
#: demande rien à la personne (un renvoi vers l'atelier et retour).
APERCU_SESSION_S = 15 * 60
MARQUE_PASSAGE = "__vigie_passage"
COOKIE_APERCU = "vigie_apercu_session"
EN_TETE_COMPTE_APERCU = "X-Vigie-Compte"
#: Le rôle qui génère un aperçu (api.py, « Générer un aperçu »). Rejugé à chaque
#: requête : un compte rétrogradé perd l'aperçu aussitôt.
ROLE_APERCU = "validator"

# Un chemin du site : une barre en tête, pas deux (`//ailleurs` est un autre
# site), ni barre inverse, ni espace, ni caractère de contrôle (en-tête `Location`).
_SUITE_PERMISE = re.compile(r"/(?![/\\])[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*")
_SURS_DANS_UN_CHEMIN = "/%:@!$&'()*+,;=-._~"


def _signer_apercu(usage: bytes, corps: str) -> str:
    cle = hmac.new(_SECRET.encode(), b"vigie-apercu-" + usage, hashlib.sha256).digest()
    return hmac.new(cle, corps.encode(), hashlib.sha256).hexdigest()


def _adresse_apercu() -> Optional[str]:
    """L'hôte d'aperçu en ligne, ou None. Un aperçu en `http://localhost` n'a ni
    nginx ni témoin à poser : le passage n'y a pas de sens."""
    url = os.environ.get("VIGIE_APERCU_URL", "").strip().rstrip("/")
    morceaux = urlsplit(url)
    return url if morceaux.scheme == "https" and morceaux.hostname else None


def _compte_apercu(conn, uid: int, sv: int) -> Optional[dict]:
    """Le compte, s'il peut voir un aperçu MAINTENANT : actif, de la même
    génération de sessions, au rôle voulu."""
    compte = _get(conn, "SELECT id, role, desactive_le, COALESCE(sessions_version, 0) AS sv "
                        "FROM users WHERE id=?", (uid,))
    if not compte or compte["desactive_le"] or compte["sv"] != sv \
            or not au_moins(compte, ROLE_APERCU):
        return None
    return compte


def _champs(valeur: str, n: int) -> Optional[list[str]]:
    """Les `n` champs d'un code ou d'un témoin ; les trois premiers sont des
    entiers (compte, génération, échéance)."""
    morceaux = (valeur or "").split(".")
    if len(valeur or "") > 512 or len(morceaux) != n \
            or not all(m.isascii() and m.isdigit() for m in morceaux[:3]):
        return None
    return morceaux


def compte_du_temoin_apercu(valeur: str) -> Optional[dict]:
    """Le compte que désigne le témoin de l'hôte d'aperçu, ou None."""
    champs = _champs(valeur, 4)
    if not champs or not _SECRET:
        return None
    uid, sv, exp, sig = champs
    attendue = _signer_apercu(b"session", f"{uid}.{sv}.{exp}")
    if not hmac.compare_digest(attendue.encode(), sig.encode()):
        return None
    if not time.time() <= int(exp) <= time.time() + APERCU_SESSION_S + 60:
        return None
    conn = _db()
    try:
        return _compte_apercu(conn, int(uid), int(sv))
    finally:
        conn.close()


_ENTETES_APERCU = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                   "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                              "frame-ancestors 'none'"}

_PAGE_REFUS = """<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aperçu fermé</title>
<p style="font:16px system-ui;margin:2rem;max-width:36rem">{texte}</p>
{lien}</html>"""


def _refus_apercu(texte: str, status: int, lien_atelier: Optional[str]) -> HTMLResponse:
    """La même page quelle que soit la raison : elle ne dit pas si le compte
    existe, s'il est désactivé ou si son mot de passe a changé."""
    lien = (f'<p style="font:16px system-ui;margin:2rem"><a href="{lien_atelier}">'
            "Ouvrir l'atelier</a></p>" if lien_atelier else "")
    return HTMLResponse(_PAGE_REFUS.format(texte=texte, lien=lien), status_code=status,
                        headers=_ENTETES_APERCU)


@router.get("/apercu/ouvrir{suite:path}")
def ouvrir_apercu(request: Request, suite: str = ""):
    """Sur l'ATELIER : la session ouverte ici devient un passage vers l'aperçu.

    Une navigation (lien de l'atelier, ou renvoi de nginx depuis l'aperçu)
    n'emporte pas `X-Atelier-Session` : la personne est reconnue au cookie de
    rafraîchissement, LU sans être consommé — la rotation reste à `/refresh`.
    """
    _origine_admise(request)
    # Une navigation seulement : un `fetch` depuis une page du même site
    # (l'hôte des documents, par exemple) n'a rien à faire ici.
    if request.headers.get("sec-fetch-mode", "navigate") != "navigate":
        raise HTTPException(403, "Navigation attendue.")
    adresse = _adresse_apercu()
    if not adresse:
        raise HTTPException(404, "Pas d'hôte d'aperçu en ligne (VIGIE_APERCU_URL).")
    # Revenu ici avec la marque de l'entrée : le témoin vient d'être posé et le
    # navigateur ne l'a pas renvoyé. Frapper un nouveau code ferait tourner la
    # boucle jusqu'à la limite du navigateur, en consommant la borne de débit.
    if MARQUE_PASSAGE in request.query_params:
        return _refus_apercu("Votre navigateur n'a pas gardé le témoin de l'aperçu "
                             "(cookies bloqués pour cette adresse ?). Autorisez-les, puis "
                             "rouvrez l'aperçu depuis l'atelier.", 403, "/")
    # 403 et non 401 : l'atelier est derrière le mur, et un 401 pourrait
    # toucher aux identifiants que le navigateur garde pour lui.
    refus = _refus_apercu("Pour voir l'aperçu, ouvrez d'abord votre session de l'atelier, "
                          "avec un compte qui a accès aux aperçus.", 403, "/")
    jeton = request.cookies.get(COOKIE_SESSION)
    if not jeton:
        return refus
    try:
        payload = _decode(jeton)
    except HTTPException:
        return refus
    origine = int(payload.get("auth_time") or payload.get("iat") or 0)
    if payload.get("kind") != "refresh" or time.time() - origine > SESSION_MAX_JOURS * 86400:
        return refus
    sv = payload.get("sv", 0)
    conn = _db()
    try:
        if _revoked(jeton, conn):
            return refus
        try:
            user = _compte_du_jeton(conn, payload)
        except HTTPException:
            return refus
        if not _compte_apercu(conn, user["id"], sv):
            return refus
    finally:
        conn.close()

    chemin = quote(suite or "/", safe=_SURS_DANS_UN_CHEMIN)
    if request.url.query:
        chemin += "?" + quote(request.url.query, safe=_SURS_DANS_UN_CHEMIN + "?")
    corps = f"{user['id']}.{sv}.{int(time.time()) + APERCU_PASSAGE_S}.{secrets.token_urlsafe(16)}"
    code = f"{corps}.{_signer_apercu(b'passage', corps)}"
    return RedirectResponse(f"{adresse}/__vigie/entree?" + urlencode({"code": code, "suite": chemin}),
                            status_code=303, headers=_ENTETES_APERCU)


@router.get("/apercu/entree")
def entree_apercu(request: Request, code: str = "", suite: str = "/"):
    """Sur l'hôte d'APERÇU (nginx : `/__vigie/entree`) : consomme le code et pose
    le témoin de cet hôte. Un code ne sert qu'une fois, une minute."""
    adresse = _adresse_apercu()
    if not adresse or request.url.hostname != urlsplit(adresse).hostname:
        raise HTTPException(404, "introuvable")
    atelier = os.environ.get("ATELIER_URL", "").strip().rstrip("/") or None
    refus = _refus_apercu("Ce passage vers l'aperçu n'est plus valable. "
                          "Rouvrez l'aperçu depuis l'atelier.", 403,
                          f"{atelier}/" if atelier else None)
    # Le parcours légitime vient de l'atelier, donc du même site. Un lien
    # d'entrée envoyé d'ailleurs (courriel, messagerie, barre d'adresse) ferait
    # porter à son destinataire le témoin, donc l'aperçu, de celui qui l'a
    # frappé. Arbitré par Julien le 15/11/2026 : seuls `same-site` et
    # `same-origin` passent ; `none` (lien collé), `cross-site` et l'en-tête
    # absent sont refusés AVANT de consommer le code.
    if request.headers.get("sec-fetch-site") not in ("same-site", "same-origin"):
        return refus
    champs = _champs(code, 5)
    if not champs or not _SECRET:
        return refus
    uid, sv, exp, nonce, sig = champs
    corps = f"{uid}.{sv}.{exp}.{nonce}"
    if not hmac.compare_digest(_signer_apercu(b"passage", corps).encode(), sig.encode()):
        return refus
    if not time.time() <= int(exp) <= time.time() + APERCU_PASSAGE_S + 30:
        return refus
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Usage unique : la table des jetons révoqués tient déjà ce registre.
            conn.execute("INSERT INTO revoked_tokens(jti, expires_at) VALUES(?,?)",
                         ("apercu:" + _empreinte(code),
                          datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            conn.rollback()
            return refus
        conn.commit()
        if not _compte_apercu(conn, int(uid), int(sv)):
            return refus
    finally:
        conn.close()

    if not _SUITE_PERMISE.fullmatch(suite or ""):
        suite = "/"
    # La marque : si le témoin ne revient pas, `ouvrir` la verra et s'arrêtera.
    # Le serveur d'aperçu la retire de l'adresse.
    suite += ("&" if "?" in suite else "?") + f"{MARQUE_PASSAGE}=1"
    temoin = f"{uid}.{sv}.{int(time.time()) + APERCU_SESSION_S}"
    reponse = RedirectResponse(suite, status_code=303, headers=_ENTETES_APERCU)
    # Sans `Domain` : le témoin ne part que vers l'hôte d'aperçu — ni vers
    # l'atelier, ni vers le site public, ni vers l'hôte des documents.
    reponse.set_cookie(COOKIE_APERCU, f"{temoin}.{_signer_apercu(b'session', temoin)}",
                       max_age=APERCU_SESSION_S, path="/", httponly=True, samesite="lax",
                       secure=request.url.scheme == "https")
    return reponse


@router.get("/apercu/controle")
def controle_apercu(request: Request):
    """Pour nginx (`auth_request`) : 204 et le compte, ou 403.

    403 et non 401 : sur l'hôte d'aperçu, 401 est la réponse du mur
    `auth_basic`, qui doit garder sa demande de mot de passe ; le 403 renvoie
    vers l'atelier (`error_page 403` du gabarit).
    """
    compte = compte_du_temoin_apercu(request.cookies.get(COOKIE_APERCU, ""))
    if not compte:
        return Response(status_code=403, headers={"Cache-Control": "no-store"})
    return Response(status_code=204, headers={"Cache-Control": "no-store",
                                              EN_TETE_COMPTE_APERCU: str(compte["id"])})
