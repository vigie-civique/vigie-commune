"""L'aperçu du site ne s'ouvre qu'avec une session de l'atelier.

Audit du 15/11/2026, B3 : l'hôte d'aperçu n'était gardé que par le mur
`auth_basic`, un identifiant commun. Un bénévole parti gardait l'aperçu tant
que le mot de passe de tous n'avait pas changé, et le compte montré venait d'un
cookie que le visiteur écrit.

Deux moitiés, essayées ici :
- l'API (api_auth.py, « L'aperçu du site, derrière les comptes ») : passage
  depuis l'atelier, code à usage unique, témoin de l'hôte d'aperçu, contrôle ;
- le gabarit nginx (deploy/nginx-atelier.conf) qui branche ce contrôle
  (`auth_request`) et transmet le compte au serveur d'aperçu. Il est lu, pas
  joué : `nginx -t` reste à faire sur le serveur.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MOT_DE_PASSE = "mot-de-passe-de-test"
ATELIER, APERCU = "https://atelier.test", "https://apercu.test"


@pytest.fixture
def comptes(tmp_path, monkeypatch, schema_sql):
    chemin_db = tmp_path / "instance.db"
    conn = sqlite3.connect(chemin_db)
    conn.executescript(schema_sql)
    import api
    import api_auth
    ids = {}
    for email, role in (("admin@exemple.fr", "admin"), ("val@exemple.fr", "validator"),
                        ("autre@exemple.fr", "validator"), ("contrib@exemple.fr", "contributor")):
        ids[email] = conn.execute("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
                                  (email, api_auth._hash_pw(MOT_DE_PASSE), role)).lastrowid
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", chemin_db)
    monkeypatch.setattr(api_auth, "DB_PATH", chemin_db)
    monkeypatch.setenv("VIGIE_APERCU_URL", APERCU)
    monkeypatch.setenv("ATELIER_URL", ATELIER)
    return {"ids": ids, "db": chemin_db, "app": api.app}


def _atelier(comptes, email=None):
    """Un navigateur sur l'atelier ; connecté si `email`."""
    c = TestClient(comptes["app"], base_url=ATELIER)
    if email:
        r = c.post("/api/auth/login", json={"email": email, "password": MOT_DE_PASSE})
        assert r.status_code == 200, r.text
        c.acces = r.json()["access_token"]
    return c


def _apercu(comptes, provenance="same-site"):
    """Un navigateur sur l'hôte d'aperçu, arrivé depuis l'atelier (même site)."""
    entetes = {"Sec-Fetch-Site": provenance} if provenance else {}
    return TestClient(comptes["app"], base_url=APERCU, headers=entetes)


def _ouvrir(atelier, chemin="/"):
    return atelier.get(f"/api/auth/apercu/ouvrir{chemin}", follow_redirects=False)


def _entree(apercu, lieu):
    """Ce que nginx fait de `/__vigie/entree` sur l'hôte d'aperçu."""
    morceaux = urlsplit(lieu)
    assert f"{morceaux.scheme}://{morceaux.netloc}" == APERCU
    assert morceaux.path == "/__vigie/entree"
    return apercu.get(f"/api/auth/apercu/entree?{morceaux.query}", follow_redirects=False)


def _passer(comptes, email, chemin="/"):
    atelier, apercu = _atelier(comptes, email), _apercu(comptes)
    r = _ouvrir(atelier, chemin)
    assert r.status_code == 303, r.text
    r = _entree(apercu, r.headers["location"])
    assert r.status_code == 303, r.text
    return atelier, apercu, r


def _controle(apercu):
    return apercu.get("/api/auth/apercu/controle")


# ─── Le passage ────────────────────────────────────────────────────────────────

def test_sans_session_l_apercu_refuse_et_renvoie_vers_l_atelier(comptes):
    assert _controle(_apercu(comptes)).status_code == 403
    r = _ouvrir(_atelier(comptes), "/finances")
    assert r.status_code == 403
    assert 'href="/"' in r.text and "session de l'atelier" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_une_session_ouvre_son_apercu_et_le_controle_dit_le_compte(comptes):
    _, apercu, r = _passer(comptes, "val@exemple.fr", "/finances?apercu=7")
    assert r.headers["location"] == "/finances?apercu=7&__vigie_passage=1"
    temoin = r.headers["set-cookie"]
    assert temoin.startswith("vigie_apercu_session=")
    for attribut in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert attribut.lower() in temoin.lower()
    # Sans `Domain` : le témoin ne part ni vers l'atelier ni vers le site public.
    assert "domain=" not in temoin.lower()

    c = _controle(apercu)
    assert c.status_code == 204
    assert c.headers["x-vigie-compte"] == str(comptes["ids"]["val@exemple.fr"])


def test_un_compte_ne_voit_que_son_apercu(comptes):
    """Le compte transmis est celui de la session, pas un numéro choisi."""
    _, apercu_val, _ = _passer(comptes, "val@exemple.fr", "/?apercu=3")
    _, apercu_autre, _ = _passer(comptes, "autre@exemple.fr")
    assert _controle(apercu_val).headers["x-vigie-compte"] == str(comptes["ids"]["val@exemple.fr"])
    assert _controle(apercu_autre).headers["x-vigie-compte"] == str(comptes["ids"]["autre@exemple.fr"])


def test_le_code_ne_sert_qu_une_fois(comptes):
    atelier, apercu = _atelier(comptes, "val@exemple.fr"), _apercu(comptes)
    lieu = _ouvrir(atelier).headers["location"]
    assert _entree(apercu, lieu).status_code == 303
    rejoue = _entree(_apercu(comptes), lieu)
    assert rejoue.status_code == 403
    assert "set-cookie" not in rejoue.headers
    assert f'href="{ATELIER}/"' in rejoue.text


def test_un_code_perime_ou_retouche_est_refuse(comptes):
    import api_auth
    uid = comptes["ids"]["val@exemple.fr"]
    corps = f"{uid}.0.{int(time.time()) - 1}.abc"
    perime = f"{corps}.{api_auth._signer_apercu(b'passage', corps)}"
    apercu = _apercu(comptes)
    assert apercu.get("/api/auth/apercu/entree", params={"code": perime},
                      follow_redirects=False).status_code == 403

    atelier = _atelier(comptes, "val@exemple.fr")
    code = parse_qs(urlsplit(_ouvrir(atelier).headers["location"]).query)["code"][0]
    autre = str(comptes["ids"]["autre@exemple.fr"])
    retouche = autre + code[code.index("."):]
    assert apercu.get("/api/auth/apercu/entree", params={"code": retouche},
                      follow_redirects=False).status_code == 403


# ─── Les refus après coup : désactivation, sessions closes, rôle ───────────────

def test_desactiver_le_compte_ferme_l_apercu_a_la_requete_suivante(comptes):
    atelier_val, apercu, _ = _passer(comptes, "val@exemple.fr")
    assert _controle(apercu).status_code == 204

    admin = _atelier(comptes, "admin@exemple.fr")
    r = admin.patch(f"/api/admin/comptes/{comptes['ids']['val@exemple.fr']}",
                    json={"actif": False}, headers={"X-Atelier-Session": admin.acces})
    assert r.status_code == 200, r.text

    assert _controle(apercu).status_code == 403
    # Et le passage ne se rouvre pas avec le cookie de l'atelier resté au navigateur.
    assert _ouvrir(atelier_val).status_code == 403


def test_des_sessions_closes_ferment_l_apercu(comptes):
    import api_auth
    _, apercu, _ = _passer(comptes, "val@exemple.fr")
    conn = sqlite3.connect(comptes["db"])
    api_auth._clore_sessions(conn, comptes["ids"]["val@exemple.fr"])
    conn.commit()
    conn.close()
    assert _controle(apercu).status_code == 403


def test_un_mot_de_passe_change_ferme_l_apercu_ouvert_avant(comptes):
    atelier, apercu, _ = _passer(comptes, "val@exemple.fr")
    r = atelier.post("/api/auth/mot-de-passe",
                     json={"actuel": MOT_DE_PASSE, "nouveau": "un-autre-mot-de-passe"},
                     headers={"X-Atelier-Session": atelier.acces})
    assert r.status_code == 200, r.text
    assert _controle(apercu).status_code == 403
    # La session qui a changé le mot de passe, elle, rouvre un passage neuf.
    assert _ouvrir(atelier).status_code == 303


def test_un_contributeur_n_a_pas_d_apercu_et_un_retrograde_le_perd(comptes):
    assert _ouvrir(_atelier(comptes, "contrib@exemple.fr")).status_code == 403

    _, apercu, _ = _passer(comptes, "val@exemple.fr")
    conn = sqlite3.connect(comptes["db"])
    conn.execute("UPDATE users SET role='contributor' WHERE id=?",
                 (comptes["ids"]["val@exemple.fr"],))
    conn.commit()
    conn.close()
    assert _controle(apercu).status_code == 403


def test_une_deconnexion_ferme_le_passage(comptes):
    atelier = _atelier(comptes, "val@exemple.fr")
    atelier.post("/api/auth/logout", headers={"X-Atelier-Session": atelier.acces})
    assert _ouvrir(atelier).status_code == 403


# ─── Ce qu'un témoin ou un passage ne doit pas permettre ───────────────────────

def test_un_temoin_forge_ou_un_code_ne_vaut_pas_temoin(comptes):
    import api_auth
    _, apercu, r = _passer(comptes, "val@exemple.fr")
    valeur = apercu.cookies.get(api_auth.COOKIE_APERCU)
    autre = str(comptes["ids"]["autre@exemple.fr"])

    forge = _apercu(comptes)
    forge.cookies.set(api_auth.COOKIE_APERCU, autre + valeur[valeur.index("."):])
    assert _controle(forge).status_code == 403

    atelier = _atelier(comptes, "val@exemple.fr")
    code = parse_qs(urlsplit(_ouvrir(atelier).headers["location"]).query)["code"][0]
    uid, sv, exp, _, sig = code.split(".")
    comme_temoin = _apercu(comptes)
    comme_temoin.cookies.set(api_auth.COOKIE_APERCU, f"{uid}.{sv}.{exp}.{sig}")
    assert _controle(comme_temoin).status_code == 403

    for n_importe_quoi in ("", "1.0.9999999999.x", "x.0.1.y", "-1.0.1.y", "a" * 600):
        c = _apercu(comptes)
        c.cookies.set(api_auth.COOKIE_APERCU, n_importe_quoi)
        assert _controle(c).status_code == 403


def test_l_entree_ne_sert_que_sur_l_hote_d_apercu(comptes):
    atelier = _atelier(comptes, "val@exemple.fr")
    lieu = urlsplit(_ouvrir(atelier).headers["location"])
    r = atelier.get(f"/api/auth/apercu/entree?{lieu.query}", follow_redirects=False)
    assert r.status_code == 404
    assert "vigie_apercu_session" not in r.headers.get("set-cookie", "")


@pytest.mark.parametrize("chemin", ["//ailleurs.example/x", "/%5Cailleurs.example"])
def test_le_passage_ne_mene_que_sur_l_hote_d_apercu(comptes, chemin):
    _, _, r = _passer(comptes, "val@exemple.fr", chemin)
    lieu = r.headers["location"]
    # Un chemin du même hôte : ni `//` (un autre site), ni barre inverse.
    assert lieu.startswith("/") and not lieu.startswith("//") and "\\" not in lieu


@pytest.mark.parametrize("suite", ["https://ailleurs.example/", "//ailleurs.example",
                                   "/\\ailleurs.example", "/x\r\nSet-Cookie: a=b", "x"])
def test_l_entree_refuse_une_suite_hors_du_site(comptes, suite):
    atelier, apercu = _atelier(comptes, "val@exemple.fr"), _apercu(comptes)
    code = parse_qs(urlsplit(_ouvrir(atelier).headers["location"]).query)["code"][0]
    r = apercu.get("/api/auth/apercu/entree", params={"code": code, "suite": suite},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?__vigie_passage=1"


def test_un_fetch_ne_frappe_pas_de_passage(comptes):
    """Une page du même site (l'hôte des documents) pourrait l'appeler."""
    atelier = _atelier(comptes, "val@exemple.fr")
    r = atelier.get("/api/auth/apercu/ouvrir/", headers={"Sec-Fetch-Mode": "cors"},
                    follow_redirects=False)
    assert r.status_code == 403
    r = atelier.get("/api/auth/apercu/ouvrir/", headers={"Origin": "https://docs.atelier.test"},
                    follow_redirects=False)
    assert r.status_code == 403


def test_sans_hote_d_apercu_en_ligne_pas_de_passage(comptes, monkeypatch):
    monkeypatch.setenv("VIGIE_APERCU_URL", "http://localhost:5180")
    assert _ouvrir(_atelier(comptes, "val@exemple.fr")).status_code == 404


def test_un_lien_d_entree_venu_d_ailleurs_ne_pose_rien(comptes):
    """Un validateur frappe son passage et envoie le lien d'entrée à un
    collègue : celui-ci verrait le brouillon de l'autre en croyant voir le sien."""
    atelier, apercu = _atelier(comptes, "val@exemple.fr"), _apercu(comptes)
    lieu = urlsplit(_ouvrir(atelier).headers["location"])
    r = apercu.get(f"/api/auth/apercu/entree?{lieu.query}", follow_redirects=False,
                   headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and "set-cookie" not in r.headers


@pytest.mark.parametrize("provenance", ["none", "cross-site", None])
def test_l_entree_n_accepte_que_le_meme_site(comptes, provenance):
    """Arbitré par Julien le 15/11/2026 : lien collé (`none`), autre site,
    en-tête absent — refusés, sans `Set-Cookie`, et le code n'est PAS consommé."""
    atelier = _atelier(comptes, "val@exemple.fr")
    lieu = _ouvrir(atelier).headers["location"]
    r = _entree(_apercu(comptes, provenance), lieu)
    assert r.status_code == 403 and "set-cookie" not in r.headers
    assert f'href="{ATELIER}/"' in r.text
    # Le même code sert encore depuis le même site.
    assert _entree(_apercu(comptes, "same-site"), lieu).status_code == 303


@pytest.mark.parametrize("provenance", ["same-site", "same-origin"])
def test_l_entree_accepte_le_meme_site(comptes, provenance):
    atelier = _atelier(comptes, "val@exemple.fr")
    r = _entree(_apercu(comptes, provenance), _ouvrir(atelier).headers["location"])
    assert r.status_code == 303 and r.headers["set-cookie"].startswith("vigie_apercu_session=")


def test_un_temoin_refuse_par_le_navigateur_ne_fait_pas_tourner_le_passage(comptes):
    """Retour à `ouvrir` avec la marque de l'entrée : le témoin n'est pas revenu.
    Pas de nouveau code, une page qui dit pourquoi."""
    atelier = _atelier(comptes, "val@exemple.fr")
    r = _ouvrir(atelier, "/finances?__vigie_passage=1")
    assert r.status_code == 403
    assert "témoin" in r.text and "location" not in r.headers


def test_le_temoin_dure_un_quart_d_heure(comptes):
    _, _, r = _passer(comptes, "val@exemple.fr")
    assert "max-age=900" in r.headers["set-cookie"].lower()


def _temoin_signe(uid: int, fin: int) -> str:
    import api_auth
    corps = f"{uid}.0.{fin}"
    return f"{corps}.{api_auth._signer_apercu(b'session', corps)}"


def test_un_temoin_signe_mais_echu_est_refuse(comptes):
    import api_auth
    c = _apercu(comptes)
    c.cookies.set(api_auth.COOKIE_APERCU,
                  _temoin_signe(comptes["ids"]["val@exemple.fr"], int(time.time()) - 1))
    assert _controle(c).status_code == 403


def test_un_temoin_qui_vit_plus_que_la_borne_du_serveur_est_refuse(comptes):
    """Une échéance plus lointaine que la durée d'un témoin ne vient pas de l'API."""
    import api_auth
    uid = comptes["ids"]["val@exemple.fr"]
    c = _apercu(comptes)
    c.cookies.set(api_auth.COOKIE_APERCU,
                  _temoin_signe(uid, int(time.time()) + api_auth.APERCU_SESSION_S + 3600))
    assert _controle(c).status_code == 403
    # Témoin de contrôle : le même, dans la borne, passe.
    c = _apercu(comptes)
    c.cookies.set(api_auth.COOKIE_APERCU,
                  _temoin_signe(uid, int(time.time()) + api_auth.APERCU_SESSION_S))
    assert _controle(c).status_code == 204


# ─── Le gabarit nginx ──────────────────────────────────────────────────────────
# Un lecteur minimal de la syntaxe nginx : directives et blocs, commentaires
# retirés. Assez pour dire ce que porte chaque bloc, pas pour valider nginx.

GABARIT = ROOT / "deploy" / "nginx-atelier.conf"


def _lire_nginx(texte: str) -> list:
    jetons = re.findall(r'"(?:[^"\\]|\\.)*"|[{};]|[^\s{};"]+',
                        "\n".join(l.split("#", 1)[0] if not l.lstrip().startswith("#") else ""
                                  for l in texte.splitlines()))
    pile, courant, mots = [], [], []
    for j in jetons:
        if j == ";":
            courant.append((mots, None))
            mots = []
        elif j == "{":
            bloc = []
            courant.append((mots, bloc))
            pile.append(courant)
            courant, mots = bloc, []
        elif j == "}":
            assert not mots, f"directive sans « ; » avant « }} » : {mots}"
            courant = pile.pop()
        else:
            mots.append(j.strip('"'))
    assert not pile and not mots, "accolades déséquilibrées"
    return courant


def _directives(bloc, nom):
    return [m[1:] for m, sous in bloc if m and m[0] == nom and sous is None]


def _blocs(bloc, *tete):
    return [sous for m, sous in bloc if sous is not None and tuple(m[:len(tete)]) == tete]


def _serveur(hote: str, port: str = "443"):
    serveurs = [s for s in _blocs(_lire_nginx(GABARIT.read_text(encoding="utf-8")), "server")
                if [hote] in _directives(s, "server_name")
                and any(d[0] == port for d in _directives(s, "listen"))]
    assert len(serveurs) == 1, f"un bloc {hote}:{port} attendu, {len(serveurs)} trouvés"
    return serveurs[0]


def _location(serveur, *tete):
    trouves = _blocs(serveur, "location", *tete)
    assert len(trouves) == 1, f"location {tete} : {len(trouves)}"
    return trouves[0]


def test_le_gabarit_exige_une_session_sur_tout_l_hote_d_apercu(comptes):
    s = _serveur("apercu.example.org")
    assert _directives(s, "auth_request") == [["/__vigie/controle"]]
    assert ["$vigie_compte", "$upstream_http_x_vigie_compte"] in _directives(s, "auth_request_set")
    # Le mur reste, le même fichier que l'atelier.
    atelier = _serveur("atelier.example.org")
    assert _directives(s, "auth_basic_user_file") == _directives(atelier, "auth_basic_user_file")
    # Aucun `auth_request off` ailleurs que là où il le faut.
    sans = {tuple(m[1:]) for m, sous in s if sous is not None and m[0] == "location"
            and ["off"] in _directives(sous, "auth_request")}
    # Un brouillon ne s'encadre pas ; et aucun `add_header` dans un `location`,
    # qui ferait tomber ceux du serveur.
    assert ["X-Frame-Options", "DENY", "always"] in _directives(s, "add_header")
    for m, sous in s:
        if sous is not None and m[0] == "location":
            assert not _directives(sous, "add_header"), m
    assert sans == {("=", "/__vigie/entree"), ("=", "/__vigie-401.html"), ("@vers_atelier",)}


def test_le_refus_de_session_renvoie_vers_l_atelier_et_le_mur_garde_son_401(comptes):
    import api_auth
    s = _serveur("apercu.example.org")
    assert ["401", "/__vigie-401.html"] in _directives(s, "error_page")
    assert ["403", "=", "@vers_atelier"] in _directives(s, "error_page")
    retour = _directives(_location(s, "@vers_atelier"), "return")
    assert retour == [["302", "https://atelier.example.org/api/auth/apercu/ouvrir$request_uri"]]
    assert "/api/auth/apercu/ouvrir{suite:path}" in {getattr(x, "path", "")
                                                     for x in api_auth.router.routes}
    # Le juge ne répond jamais 401 : ce serait le mur, et sa demande de mot de passe.
    assert _controle(_apercu(comptes)).status_code == 403
    # Et l'atelier, au bout du renvoi, sert la page de refus en français.
    r = _atelier(comptes).get("/api/auth/apercu/ouvrir/finances?apercu=7",
                              follow_redirects=False)
    assert r.status_code == 403 and "Ouvrir l'atelier" in r.text


def test_le_gabarit_branche_les_routes_de_l_api(comptes):
    import api_auth
    routes = {getattr(x, "path", "") for x in api_auth.router.routes}
    s = _serveur("apercu.example.org")

    controle = _location(s, "=", "/__vigie/controle")
    assert _directives(controle, "internal") == [[]]
    assert _directives(controle, "proxy_pass") == [["http://127.0.0.1:8765/api/auth/apercu/controle"]]
    assert _directives(controle, "proxy_pass_request_body") == [["off"]]
    assert "/api/auth/apercu/controle" in routes

    entree = _location(s, "=", "/__vigie/entree")
    assert _directives(entree, "proxy_pass") == [["http://127.0.0.1:8765/api/auth/apercu/entree"]]
    # Le code est dans l'adresse.
    assert _directives(entree, "access_log") == [["off"]]
    # Sa propre zone : un passage en boucle ne prive pas la salle de connexion.
    assert _directives(entree, "limit_req")[0][0] == "zone=apercu_entree"
    # Les pages : leur zone, pas celle de l'API de l'atelier et des documents.
    assert _directives(_location(s, "/"), "limit_req")[0][0] == "zone=apercu"
    zones = [d for d in _directives(_lire_nginx(GABARIT.read_text(encoding="utf-8")),
                                    "limit_req_zone")]
    assert any("zone=apercu:10m" in d for d in zones)
    assert "/api/auth/apercu/entree" in routes

    for bloc in (controle, entree):
        assert ["Authorization", ""] in _directives(bloc, "proxy_set_header")
        assert ["X-Admin-Key", ""] in _directives(bloc, "proxy_set_header")


def test_le_serveur_d_apercu_recoit_le_compte_de_la_session_et_pas_les_cookies():
    import importlib.util

    import api_auth
    spec = importlib.util.spec_from_file_location("servir_apercu_gabarit",
                                                  ROOT / "scripts" / "servir_apercu.py")
    servir = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(servir)

    racine = _location(_serveur("apercu.example.org"), "/")
    assert _directives(racine, "proxy_pass") == [["http://127.0.0.1:5180"]]
    entetes = _directives(racine, "proxy_set_header")
    assert [servir.EN_TETE_COMPTE, "$vigie_compte"] in entetes
    assert servir.EN_TETE_COMPTE == api_auth.EN_TETE_COMPTE_APERCU
    assert ["Cookie", ""] in entetes and ["Authorization", ""] in entetes


def test_le_gabarit_dit_une_ligne_htpasswd_par_personne():
    texte = GABARIT.read_text(encoding="utf-8")
    assert "UNE LIGNE PAR PERSONNE" in texte
    assert re.search(r"htpasswd -D /etc/nginx/\.htpasswd-vigie-atelier", texte)
    assert re.search(r"htpasswd /etc/nginx/\.htpasswd-vigie-atelier <", texte)
    # Plus d'identifiant commun suggéré à l'installation.
    assert "htpasswd-vigie-atelier equipe" not in texte
