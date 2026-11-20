"""L'atelier en ligne : ce qu'un compte, ou un document collecté, ne doit pas pouvoir faire.

Revue du 02/10/2026, avant la mise sur serveur. Tant que l'atelier tournait sur
le poste de celui qui tient l'instance, ces défauts n'avaient pas de victime :
il n'y avait qu'un compte. À plusieurs, le texte d'un PV, un fichier déposé ou
une adresse saisie par un contributeur s'affichent dans le navigateur d'un
administrateur — qui y garde son jeton de session, et qui publie.

Comme `test_api_roles`, ces tests passent par de VRAIS jetons.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MDP = "mot-de-passe-de-test"
DOCS = "https://docs.atelier.exemple.fr"

#: Routes hors de `/api/`, chacune avec son propre verrou et sa classe de tests.
HORS_DU_VERROU = {"/d/{doc_id}": "lien signé — TestDocumentsSurUnAutreHote"}

#: Routes de `/api/` (hors `/api/auth/`) sans garde de route, chacune avec sa
#: raison. Vide : aucune ne tient sa protection du seul verrou global.
SANS_GARDE: dict[str, str] = {}

#: Les dépendances qui jugent un appelant. `optional_user` n'en est pas une :
#: il rend None sans refuser.
GARDES = {
    ("api_auth", "require_auth"),
    ("api_auth", "require_role.<locals>._dep"),
    ("api_auth", "require_au_moins.<locals>._dep"),
    ("api", "require_compte_ou_cle.<locals>._dep"),
}


def _gardee(dependant) -> bool:
    return any((getattr(d.call, "__module__", None), getattr(d.call, "__qualname__", None)) in GARDES
               or _gardee(d) for d in dependant.dependencies)


@pytest.fixture
def atelier(tmp_path, monkeypatch, schema_sql):
    chemin_db = tmp_path / "instance.db"
    conn = sqlite3.connect(chemin_db)
    conn.executescript(schema_sql)
    conn.close()

    import api
    import api_auth
    from collectors import db as db_mod
    for module in (api, api_auth, db_mod):
        monkeypatch.setattr(module, "DB_PATH", chemin_db)
    monkeypatch.setattr(api, "RACINE", tmp_path)
    monkeypatch.setattr(api_auth, "_echecs", {})
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.setattr(api, "ADMIN_KEY", "")
    client = TestClient(api.app)

    def sql(requete, params=()):
        c = sqlite3.connect(chemin_db)
        try:
            cur = c.execute(requete, params)
            c.commit()
            return cur.lastrowid
        finally:
            c.close()

    def compte(email, role):
        sql("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
            (email, api_auth._hash_pw(MDP), role))
        j = client.post("/api/auth/login", json={"email": email, "password": MDP}).json()
        return {"Authorization": f"Bearer {j['access_token']}"}

    return {"client": client, "sql": sql, "compte": compte, "racine": tmp_path, "app": api.app}


class TestSansJeton:
    def test_aucune_route_de_l_api_ne_repond_sans_session(self, atelier):
        """Parcourt les routes DÉCLARÉES : une route ajoutée demain est couverte
        sans que personne pense à l'inscrire ici."""
        c = atelier["client"]
        ouvertes = []
        for route in atelier["app"].routes:
            if not isinstance(route, APIRoute) or route.path.startswith("/api/auth/"):
                continue
            if route.path in HORS_DU_VERROU:
                continue
            assert route.path.startswith("/api/"), (
                f"{route.path} : hors de /api/, donc hors du verrou global")
            chemin = route.path
            for nom in route.param_convertors:
                chemin = chemin.replace("{" + nom + "}", "1")
            for methode in route.methods - {"HEAD", "OPTIONS"}:
                if c.request(methode, chemin).status_code != 401:
                    ouvertes.append(f"{methode} {route.path}")
        assert ouvertes == []

    def test_chaque_route_de_l_api_porte_sa_propre_garde(self, atelier):
        """Le verrou global ne connaît pas les rôles : un contributeur le passe.
        Chaque route déclare donc qui peut l'appeler (brief du 14/11/2026 ; 28
        routes n'avaient que le verrou, ou un contrôle dans leur corps)."""
        sans = sorted(f"{'/'.join(sorted(r.methods))} {r.path}" for r in atelier["app"].routes
                      if isinstance(r, APIRoute) and r.path.startswith("/api/")
                      and not r.path.startswith("/api/auth/")
                      and r.path not in SANS_GARDE and not _gardee(r.dependant))
        assert sans == []
        # Hors `APIRoute` (route Starlette, websocket, montage), la garde ne
        # se lit pas : rien de tel ne doit vivre sous /api/.
        assert [r.path for r in atelier["app"].routes if not isinstance(r, APIRoute)
                and getattr(r, "path", "").startswith("/api/")] == []

    def test_optional_user_seul_n_est_pas_une_garde(self):
        import api
        from fastapi import Depends, FastAPI
        essai = FastAPI()

        @essai.get("/api/ouverte")
        def ouverte(user=Depends(api.optional_user)):
            return user

        @essai.get("/api/fermee")
        def fermee(user=Depends(api.require_auth)):
            return user

        routes = {r.path: r for r in essai.routes if isinstance(r, APIRoute)}
        assert not _gardee(routes["/api/ouverte"].dependant)
        assert _gardee(routes["/api/fermee"].dependant)

    def test_la_documentation_de_l_api_est_eteinte(self, atelier):
        for chemin in ("/docs", "/redoc", "/openapi.json"):
            assert atelier["client"].get(chemin).status_code == 404, chemin


class TestDerriereUnMurHttp:
    """nginx `auth_basic` occupe `Authorization` : la session passe à côté."""

    def test_la_session_tient_quand_authorization_porte_le_mur(self, atelier):
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        jeton = h["Authorization"].removeprefix("Bearer ")
        r = atelier["client"].get("/api/atelier/stats", headers={
            "Authorization": "Basic YXRlbGllcjptdXI=", "X-Atelier-Session": jeton})
        assert r.status_code == 200, r.text

    def test_le_mur_seul_n_ouvre_rien(self, atelier):
        r = atelier["client"].get("/api/atelier/stats",
                                  headers={"Authorization": "Basic YXRlbGllcjptdXI="})
        assert r.status_code == 401

    def test_la_deconnexion_revoque_le_jeton_de_l_en_tete(self, atelier):
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        session = {"X-Atelier-Session": h["Authorization"].removeprefix("Bearer ")}
        c = atelier["client"]
        assert c.post("/api/auth/logout", headers=session).status_code == 200
        assert c.get("/api/atelier/stats", headers=session).status_code == 401


class TestContenuCollecte:
    def _page_scrapee(self, atelier):
        import json
        atelier["sql"](
            "INSERT INTO events(type, title, content, source, metadata) "
            "VALUES('scrape', 'Boulangerie du Four', 'Pain et viennoiseries', "
            "'web_scraper', ?)",
            (json.dumps({"emails": ["jean.dupont@exemple.fr"],
                         "phones": ["01 99 00 11 00", "06 39 98 12 00"],
                         "page_title": "Accueil"}),))

    def test_les_courriels_et_telephones_ne_sortent_pas_des_evenements(self, atelier):
        """C12 : l'atelier ne lit que le nombre ; aucun rôle ne lit les valeurs."""
        self._page_scrapee(atelier)
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        for route, params in (("/api/events", {}),
                              ("/api/events/search", {"q": "viennoiseries"})):
            r = atelier["client"].get(route, params=params, headers=h)
            assert r.status_code == 200, r.text
            assert "jean.dupont" not in r.text and "04 66" not in r.text
            meta = r.json()[0]["metadata"]
            assert meta["n_emails"] == 1 and meta["n_phones"] == 2
            assert meta["page_title"] == "Accueil"

    def test_la_recherche_ne_dit_pas_si_une_adresse_est_en_base(self, atelier):
        self._page_scrapee(atelier)
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].get("/api/events/search", params={"q": "dupont"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_une_balise_dans_un_acte_ne_sort_pas_de_l_extrait(self, atelier):
        atelier["sql"](
            "INSERT INTO events(type, title, content) VALUES('deliberation', 'Budget', ?)",
            ("Vote du budget primitif <img src=x onerror=alert(1)> à l'unanimité",))
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].get("/api/events/search", params={"q": "primitif"}, headers=h)
        assert r.status_code == 200, r.text
        extrait = r.json()[0]["snippet"]
        assert "<mark>primitif</mark>" in extrait
        assert "<img" not in extrait and "&lt;img" in extrait

    def _deposer(self, atelier, h, nom, contenu):
        r = atelier["client"].post("/api/atelier/documents", headers=h,
                                   files={"fichier": (nom, contenu, "text/html")})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_une_page_html_deposee_se_telecharge_et_ne_s_affiche_pas(self, atelier):
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        doc = self._deposer(atelier, h, "piege.html", b"<script>alert(1)</script>")
        r = atelier["client"].get(f"/api/atelier/documents/{doc}/fichier", headers=h)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment")
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_un_pdf_s_affiche(self, atelier):
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        doc = self._deposer(atelier, h, "pv.pdf", b"%PDF-1.4 faux pv")
        r = atelier["client"].get(f"/api/atelier/documents/{doc}/fichier", headers=h)
        assert r.headers["content-type"] == "application/pdf"
        assert "attachment" not in r.headers.get("content-disposition", "")

    def test_un_chemin_hors_de_data_n_est_pas_servi(self, atelier):
        """`local_path` vit en base, que d'autres chemins que l'API écrivent :
        il ne doit jamais mener au `.env` ni à la base, qui sont dans le même arbre."""
        (atelier["racine"] / ".env").write_text("JWT_SECRET=secret")
        doc = atelier["sql"](
            "INSERT INTO raw_documents(source, url, doc_type, sha256, byte_size, local_path, "
            "title, parse_status) VALUES('atelier', NULL, 'bin', 'abc', 17, '.env', 'x', 'manuel')")
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].get(f"/api/atelier/documents/{doc}/fichier", headers=h)
        assert r.status_code == 404


class TestAdresseSaisie:
    @pytest.mark.parametrize("adresse", ["javascript:alert(1)", "data:text/html,x",
                                         " JavaScript:alert(1)", "exemple.fr"])
    def test_un_site_qui_n_est_pas_une_adresse_http_est_refuse(self, atelier, adresse):
        eid = atelier["sql"]("INSERT INTO entities(type, name) VALUES('association', 'Le Four')")
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        c = atelier["client"]
        r = c.post(f"/api/atelier/entities/{eid}/websites", headers=h, json={"url": adresse})
        assert r.status_code == 400, r.text
        r = c.post(f"/api/atelier/entities/{eid}/contacts", headers=h,
                   json={"type": "website", "value": adresse})
        assert r.status_code == 400, r.text

    def test_une_relation_ne_prend_pas_un_type_inconnu_a_la_modification(self, atelier):
        a = atelier["sql"]("INSERT INTO entities(type, name) VALUES('person', 'A')")
        b = atelier["sql"]("INSERT INTO entities(type, name) VALUES('association', 'B')")
        rel = atelier["sql"]("INSERT INTO relations(from_id, to_id, relation_type, source, "
                             "confidence) VALUES(?,?,'membre','manual','probable')", (a, b))
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].put(f"/api/atelier/relations/{rel}", headers=h,
                                  json={"relation_type": "<b>n'importe quoi</b>"})
        assert r.status_code == 400, r.text

    def test_une_adresse_http_passe(self, atelier):
        eid = atelier["sql"]("INSERT INTO entities(type, name) VALUES('association', 'Le Four')")
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].post(f"/api/atelier/entities/{eid}/websites", headers=h,
                                   json={"url": "https://exemple.fr/four"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "candidate"


class TestSessionHorsDePorteeDesScripts:
    """Le jeton de sept jours vit dans un cookie que la page ne peut pas lire."""

    def _connecter(self, atelier):
        atelier["compte"]("une@exemple.fr", "validator")
        return atelier["client"].post("/api/auth/login",
                                      json={"email": "une@exemple.fr", "password": MDP})

    def test_la_connexion_pose_un_cookie_que_les_scripts_ne_lisent_pas(self, atelier):
        cookie = self._connecter(atelier).headers["set-cookie"].lower()
        assert cookie.startswith("atelier_refresh=")
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert "path=/api/auth" in cookie

    def test_le_cookie_suffit_a_rafraichir_et_la_deconnexion_le_revoque(self, atelier):
        c = atelier["client"]
        self._connecter(atelier)
        r = c.post("/api/auth/refresh")                 # ni corps, ni en-tête
        assert r.status_code == 200, r.text
        acces = {"X-Atelier-Session": r.json()["access_token"]}
        assert c.get("/api/atelier/stats", headers=acces).status_code == 200

        ancien = c.cookies.get("atelier_refresh")
        assert c.post("/api/auth/logout", headers=acces).status_code == 200
        assert c.post("/api/auth/refresh").status_code == 401
        # Le jeton lui-même est révoqué, pas seulement retiré du navigateur.
        assert c.post("/api/auth/refresh", json={"refresh_token": ancien}).status_code == 401

    def test_sans_cookie_ni_jeton_rien_ne_se_rafraichit(self, atelier):
        assert atelier["client"].post("/api/auth/refresh").status_code == 401


class TestEssaisRates:
    """Celui qui se trompe est arrêté — pas celui qu'il vise."""

    def test_cinq_essais_rates_arretent_cette_origine_pas_le_compte(self, atelier):
        from fastapi.testclient import TestClient as Client
        atelier["compte"]("admin@exemple.fr", "admin")
        c = atelier["client"]
        faux = {"email": "admin@exemple.fr", "password": "pas-le-bon-mot-de-passe"}
        juste = {"email": "admin@exemple.fr", "password": MDP}
        for _ in range(5):
            assert c.post("/api/auth/login", json=faux).status_code == 401
        # La même origine est arrêtée, même avec le bon mot de passe…
        assert c.post("/api/auth/login", json=juste).status_code == 429
        # …l'administrateur, lui, entre de chez lui.
        ailleurs = Client(atelier["app"], client=("203.0.113.7", 50000))
        assert ailleurs.post("/api/auth/login", json=juste).status_code == 200

    def test_un_compte_inexistant_repond_comme_un_mauvais_mot_de_passe(self, atelier):
        c = atelier["client"]
        inconnu = {"email": "personne@exemple.fr", "password": "x" * 12}
        for _ in range(5):
            assert c.post("/api/auth/login", json=inconnu).status_code == 401
        assert c.post("/api/auth/login", json=inconnu).status_code == 429


class TestModeleDistant:
    """Envoyer la base de travail hors de la machine se décide, ça ne s'obtient
    pas en renseignant une adresse."""

    @pytest.mark.parametrize("adresse, accepte, attendu", [
        ("http://localhost:11434/v1", False, True),
        ("http://127.0.0.1:11434/v1", False, True),
        ("https://api.exemple.com/v1", False, False),
        ("https://api.exemple.com/v1", True, True),
        ("https://localhost.exemple.com/v1", False, False),     # pas une sous-chaîne
    ])
    def test_un_modele_distant_demande_une_decision(self, monkeypatch, adresse, accepte, attendu):
        import api
        monkeypatch.setattr(api, "_IA_URL", adresse)
        monkeypatch.setattr(api, "_IA_MODELE", "un-modele")
        monkeypatch.setattr(api, "_IA_HORS_MACHINE", accepte)
        assert api._ia_configuree() is attendu


class TestErreurModeleSansFuite:
    """Un échec du modèle affiche un motif fixe : ni URL, ni clé, ni réponse."""

    @staticmethod
    def _http401():
        import requests
        rep = requests.Response()
        rep.status_code = 401
        return requests.HTTPError("401 https://interne.test/v1?key=SECRET", response=rep)

    @pytest.mark.parametrize("fabrique, libelle", [
        (lambda: __import__("requests").ConnectionError("x https://interne.test/v1?key=SECRET"),
         "connexion au modèle impossible"),
        (lambda: __import__("requests").Timeout("x https://interne.test/v1?key=SECRET"),
         "délai dépassé"),
        (lambda: __import__("requests").ConnectTimeout("x https://interne.test/v1?key=SECRET"),
         "délai dépassé"),
        (lambda: TestErreurModeleSansFuite._http401(), "statut 401"),
        (lambda: KeyError("SECRET"), "réponse illisible"),
        (lambda: __import__("requests").exceptions.MissingSchema(
            "x interne.test?key=SECRET"), "réglage du modèle invalide"),
        (lambda: __import__("requests").JSONDecodeError("SECRET", "doc", 0),
         "réponse illisible"),
        (lambda: __import__("requests").exceptions.ChunkedEncodingError("SECRET"),
         "erreur inattendue"),
        (lambda: RuntimeError("SECRET interne.test"), "erreur inattendue"),
    ])
    def test_le_detail_est_un_libelle_fixe(self, monkeypatch, capsys, fabrique, libelle):
        import api
        from fastapi import HTTPException
        monkeypatch.setattr(api, "_IA_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(api, "_IA_MODELE", "un-modele")
        monkeypatch.setattr(api, "_IA_PROTOCOLE", "openai")

        def leve(*a, **kw):
            raise fabrique()
        monkeypatch.setattr(api, "_appel_openai", leve)
        with pytest.raises(HTTPException) as exc:
            api._appel_modele("question")
        assert exc.value.status_code == 502
        assert "SECRET" not in exc.value.detail
        assert "interne.test" not in exc.value.detail
        assert libelle in exc.value.detail
        assert exc.value.__cause__ is None
        journal = capsys.readouterr().err
        assert "SECRET" not in journal and "interne.test" not in journal


class TestDocumentsSurUnAutreHote:
    """Audit du 17/10/2026 (S6) : un document archivé n'est pas de confiance, et
    il s'ouvrait à l'adresse de l'atelier. Il s'ouvre sur `DOCS_URL`, par un lien
    signé pour un compte et cinq minutes."""

    @pytest.fixture
    def docs(self, atelier, monkeypatch):
        import api
        monkeypatch.setattr(api, "DOCS_URL", DOCS)
        monkeypatch.setattr(api, "HOTE_DOCS", "docs.atelier.exemple.fr")
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        dossier = atelier["racine"] / "data" / "raw" / "atelier"
        dossier.mkdir(parents=True)
        (dossier / "pv.pdf").write_bytes(b"%PDF-1.4 faux pv")
        (dossier / "piege.html").write_bytes(b"<script>alert(1)</script>")
        ins = ("INSERT INTO raw_documents(source, doc_type, sha256, byte_size, local_path, "
               "title, parse_status) VALUES('atelier', ?, ?, 1, ?, 'x', 'manuel')")
        pdf = atelier["sql"](ins, ("pdf", "a", "data/raw/atelier/pv.pdf"))
        html = atelier["sql"](ins, ("html", "b", "data/raw/atelier/piege.html"))
        return {"h": h, "pdf": pdf, "html": html, "c": atelier["client"], "sql": atelier["sql"]}

    def _lien(self, docs, doc):
        r = docs["c"].post(f"/api/atelier/documents/{doc}/lien", headers=docs["h"])
        assert r.status_code == 200, r.text
        return r.json()

    def test_le_lien_pointe_vers_l_autre_hote_et_y_ouvre_le_pdf(self, docs):
        lien = self._lien(docs, docs["pdf"])
        assert lien["url"].startswith(f"{DOCS}/d/{docs['pdf']}?")
        assert lien["affichage"] == "pdf"
        r = docs["c"].get(lien["url"])                 # sans session
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert "attachment" not in r.headers.get("content-disposition", "")
        # Pas de `sandbox` sur le PDF : il empêche le lecteur de Chrome de s'ouvrir.
        assert "content-security-policy" not in r.headers
        for nom, valeur in (("referrer-policy", "no-referrer"), ("x-frame-options", "DENY"),
                            ("cross-origin-resource-policy", "same-origin"),
                            ("x-content-type-options", "nosniff")):
            assert r.headers[nom] == valeur, nom

    def test_le_reste_se_telecharge_sous_sandbox(self, docs):
        lien = self._lien(docs, docs["html"])
        assert lien["affichage"] == "telechargement"
        r = docs["c"].get(lien["url"])
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment")
        assert "sandbox" in r.headers["content-security-policy"]

    def test_le_lien_ne_sert_que_sur_l_hote_des_documents(self, docs):
        url = self._lien(docs, docs["pdf"])["url"]
        assert docs["c"].get(url.replace(DOCS, "")).status_code == 404

    def test_l_hote_des_documents_ne_sert_ni_l_api_ni_l_interface(self, docs):
        for chemin in ("/api/atelier/stats", "/api/auth/login", "/"):
            assert docs["c"].get(f"{DOCS}{chemin}", headers=docs["h"]).status_code == 404, chemin

    def test_une_signature_alteree_ou_un_autre_document_sont_refuses(self, docs):
        url = self._lien(docs, docs["pdf"])["url"]
        assert docs["c"].get(url[:-1] + ("0" if url[-1] != "0" else "1")).status_code == 403
        autre = url.replace(f"/d/{docs['pdf']}?", f"/d/{docs['html']}?")
        assert docs["c"].get(autre).status_code == 403

    def test_un_lien_expire_est_refuse(self, docs, monkeypatch):
        import api_auth
        url = self._lien(docs, docs["pdf"])["url"]
        vrai = api_auth.time.time
        monkeypatch.setattr(api_auth.time, "time", lambda: vrai() + api_auth.DUREE_LIEN_DOCUMENT + 5)
        assert docs["c"].get(url).status_code == 403

    def test_une_echeance_trop_lointaine_ne_vient_pas_de_l_api(self, docs):
        import time
        import api_auth
        exp = int(time.time()) + 86400
        sig = api_auth._signature_document(docs["pdf"], exp, 1, 0)
        r = docs["c"].get(f"{DOCS}/d/{docs['pdf']}?exp={exp}&u=1&sig={sig}")
        assert r.status_code == 403

    def test_sans_parametres_un_403_et_non_un_422_qui_nomme_les_parametres(self, docs):
        r = docs["c"].get(f"{DOCS}/d/{docs['pdf']}")
        assert r.status_code == 403
        assert "query" not in r.text and "Field required" not in r.text
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"

    def test_un_compte_desactive_eteint_ses_liens(self, docs):
        url = self._lien(docs, docs["pdf"])["url"]
        docs["sql"]("UPDATE users SET desactive_le=datetime('now')")
        assert docs["c"].get(url).status_code == 403

    def test_un_mot_de_passe_change_eteint_ses_liens(self, docs):
        url = self._lien(docs, docs["pdf"])["url"]
        docs["sql"]("UPDATE users SET sessions_version = sessions_version + 1")
        assert docs["c"].get(url).status_code == 403

    def test_sans_docs_url_l_atelier_se_replie_sur_le_blob(self, atelier):
        h = atelier["compte"]("contrib@exemple.fr", "contributor")
        r = atelier["client"].post("/api/atelier/documents/1/lien", headers=h)
        assert r.status_code == 200 and r.json() == {"url": None}

    def test_sans_docs_url_la_route_signee_n_existe_pas(self, atelier):
        assert atelier["client"].get("/d/1?exp=1&u=1&sig=x").status_code == 404


class TestCookieDepuisUneAutreOrigine:
    """`docs.` et l'atelier sont du même SITE : `SameSite=Strict` laisse passer
    une requête de l'un vers l'autre. L'origine annoncée est contrôlée."""

    def test_un_rafraichissement_venu_de_l_hote_des_documents_est_refuse(self, atelier):
        atelier["compte"]("une@exemple.fr", "validator")
        c = atelier["client"]
        r = c.post("/api/auth/refresh", headers={"Origin": DOCS})
        assert r.status_code == 403
        assert c.post("/api/auth/logout", headers={"Origin": DOCS}).status_code == 403
        # La session n'a pas bougé : le cookie rafraîchit encore.
        assert c.post("/api/auth/refresh").status_code == 200

    def test_la_meme_origine_passe(self, atelier):
        atelier["compte"]("une@exemple.fr", "validator")
        r = atelier["client"].post("/api/auth/refresh", headers={"Origin": "http://testserver"})
        assert r.status_code == 200, r.text


class TestRoutesIA:
    """Audit du 06/11/2026 : les routes qui parlent à un modèle."""

    @staticmethod
    def _exige_une_session(dependant) -> bool:
        import api_auth
        return any(d.call is api_auth.require_auth or TestRoutesIA._exige_une_session(d)
                   for d in dependant.dependencies)

    @pytest.mark.parametrize("chemin", [
        "/api/synthesize", "/api/rag/ask", "/api/atelier/ia/extraire"])
    def test_chaque_route_ia_porte_son_propre_verrou(self, atelier, chemin):
        """Le verrou global ne dispense pas du verrou de la route (convention en
        tête d'api.py) : `/api/synthesize` n'avait que le premier."""
        route = next(r for r in atelier["app"].routes
                     if isinstance(r, APIRoute) and r.path == chemin)
        assert self._exige_une_session(route.dependant)

    @pytest.mark.parametrize("demande, servie", [(10_000, 20), (0, 1), (-5, 1), (7, 7)])
    def test_la_recherche_d_une_question_est_bornee(self, atelier, monkeypatch, demande, servie):
        import api
        vues = []
        monkeypatch.setattr(api, "RAG_ENABLED", True)
        monkeypatch.setattr(api, "RAG_ASK_ENABLED", True)
        monkeypatch.setattr(api, "rag_search",
                            lambda q, limit, user: vues.append(limit) or [])

        def hors_ligne(*a, **k):
            raise ConnectionError("pas de modèle dans les essais")
        monkeypatch.setattr(api._requests, "post", hors_ligne)
        h = atelier["compte"]("une@exemple.fr", "validator")
        r = atelier["client"].post("/api/rag/ask", headers=h,
                                   json={"question": "budget", "limit": demande})
        assert r.status_code == 200, r.text
        assert vues == [servie]

    def test_une_borne_illisible_est_refusee(self, atelier, monkeypatch):
        import api
        monkeypatch.setattr(api, "RAG_ENABLED", True)
        monkeypatch.setattr(api, "RAG_ASK_ENABLED", True)
        h = atelier["compte"]("une@exemple.fr", "validator")
        r = atelier["client"].post("/api/rag/ask", headers=h,
                                   json={"question": "budget", "limit": "beaucoup"})
        assert r.status_code == 400
