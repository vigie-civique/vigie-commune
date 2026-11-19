"""La déconnexion met fin à la session — toute la session.

Constaté le 17/09/2026 : « Déconnexion » révoquait le jeton d'accès (une heure)
mais pas celui de rafraîchissement (sept jours, gardé dans le navigateur), qui
en refabriquait un neuf. Quiconque rouvrait le navigateur était connecté.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch, schema_sql):
    chemin_db = tmp_path / "instance.db"
    conn = sqlite3.connect(chemin_db)
    conn.executescript(schema_sql)
    import api
    import api_auth
    conn.execute("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
                 ("une@exemple.fr", api_auth._hash_pw("mot-de-passe-de-test"), "validator"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", chemin_db)
    monkeypatch.setattr(api_auth, "DB_PATH", chemin_db)
    return TestClient(api.app)


def _connecter(client):
    return client.post("/api/auth/login", json={"email": "une@exemple.fr",
                                                "password": "mot-de-passe-de-test"})


def test_apres_deconnexion_le_rafraichissement_est_refuse(client):
    j = _connecter(client).json()
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    courant = client.cookies.get("atelier_refresh")

    r = client.post("/api/auth/logout",
                    headers={"Authorization": f"Bearer {j['access_token']}"})
    assert r.status_code == 200

    assert client.post("/api/auth/refresh",
                       json={"refresh_token": courant}).status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {j['access_token']}"}).status_code == 401


def test_le_jeton_de_rafraichissement_ne_sort_pas_dans_le_corps(client):
    """Lisible dans le JSON, il l'était par un script injecté : le cookie
    HttpOnly ne protégeait plus rien (audit du 17/10/2026)."""
    r = _connecter(client)
    assert "refresh_token" not in r.json()
    assert r.cookies.get("atelier_refresh")
    assert "refresh_token" not in client.post("/api/auth/refresh").json()


def test_le_rafraichissement_fait_tourner_le_jeton(client):
    """Un jeton présenté une fois ne sert plus : volé, il ne sert qu'une fois."""
    ancien = _connecter(client).cookies.get("atelier_refresh")
    r = client.post("/api/auth/refresh", json={"refresh_token": ancien})
    assert r.status_code == 200
    assert r.cookies.get("atelier_refresh") not in (None, ancien)
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": ancien}).status_code == 401
    assert client.post("/api/auth/refresh").status_code == 200   # le neuf, par le cookie


def test_une_adresse_inconnue_coute_un_bcrypt_comme_les_autres(client, monkeypatch):
    """Sans bcrypt, la réponse rapide disait quelles adresses ont un compte."""
    import api_auth
    appels = []
    vrai = api_auth._verify_pw
    monkeypatch.setattr(api_auth, "_verify_pw", lambda m, h: appels.append(h) or vrai(m, h))
    r = client.post("/api/auth/login", json={"email": "personne@exemple.fr", "password": "x"})
    assert r.status_code == 401
    assert len(appels) == 1 and appels[0].startswith("$2")


def test_une_deconnexion_sans_corps_reste_acceptee(client):
    j = client.post("/api/auth/login", json={"email": "une@exemple.fr",
                                             "password": "mot-de-passe-de-test"}).json()
    r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {j['access_token']}"})
    assert r.status_code == 200


def test_un_jeton_presente_deux_fois_clot_toutes_les_sessions(client, monkeypatch):
    """Si le voleur rafraîchit le premier, la personne légitime revient avec un
    jeton déjà révoqué : c'est le signal. Jusque-là elle était seule déconnectée,
    et le voleur gardait une session renouvelable sans fin (revue du 13/11/2026)."""
    import api_auth
    monkeypatch.setattr(api_auth, "REUTILISATION_TOLERANCE_S", -1)
    acces = _connecter(client).json()["access_token"]
    vole = client.cookies.get("atelier_refresh")

    assert client.post("/api/auth/refresh", json={"refresh_token": vole}).status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": vole}).status_code == 401

    # La session issue du premier usage tombe aussi, et le jeton d'accès avec.
    assert client.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {acces}"}).status_code == 401
    conn = sqlite3.connect(api_auth.DB_PATH)
    assert conn.execute("SELECT field FROM audit_log WHERE action='sessions_closes'"
                        ).fetchall() == [("une@exemple.fr",)]
    conn.close()
    assert _connecter(client).status_code == 200


def test_deux_onglets_au_meme_instant_ne_closent_rien(client):
    """Le second onglet arrive juste après la révocation : refusé, sans plus."""
    ancien = _connecter(client).cookies.get("atelier_refresh")
    assert client.post("/api/auth/refresh", json={"refresh_token": ancien}).status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": ancien}).status_code == 401
    assert client.post("/api/auth/refresh").status_code == 200


def test_la_rotation_ne_rajeunit_pas_la_session(client):
    """Chaque rotation repartait pour sept jours, sans fin."""
    import api_auth
    import jwt
    _connecter(client)
    lire = lambda j: jwt.decode(j, api_auth._SECRET, algorithms=["HS256"])  # noqa: E731
    connexion = lire(client.cookies.get("atelier_refresh"))["auth_time"]
    r = client.post("/api/auth/refresh")
    assert lire(client.cookies.get("atelier_refresh"))["auth_time"] == connexion
    assert lire(r.json()["access_token"])["auth_time"] == connexion


def test_une_session_trop_ancienne_ne_se_renouvelle_plus(client):
    import time

    import api_auth
    vieux = int(time.time()) - (api_auth.SESSION_MAX_JOURS + 1) * 86400
    jeton = api_auth._make_token("une@exemple.fr", "refresh", 60, 0, vieux)
    r = client.post("/api/auth/refresh", json={"refresh_token": jeton})
    assert r.status_code == 401
    assert "se reconnecter" in r.json()["detail"]


def test_un_jeton_d_acces_ne_rafraichit_pas(client):
    acces = _connecter(client).json()["access_token"]
    assert client.post("/api/auth/refresh", json={"refresh_token": acces}).status_code == 401
