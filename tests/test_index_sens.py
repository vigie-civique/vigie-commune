"""La recherche par sens sur un atelier qui n'a que le modèle d'embeddings.

Chantier du 07/11/2026 : ouvrir `/api/rag/search` sur l'atelier de Lasalle
(VPS, Ollama local, nomic-embed-text seul) sans ouvrir `/api/rag/ask`, et
refuser une recherche dont l'index vient d'un autre modèle.

Ollama est remplacé par un faux serveur : ce qui est vérifié ici, c'est ce que
le moteur fait de ses réponses. La base, elle, est au schéma réel.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

import pytest

# Avant tout import qui tire un paquet de requirements.txt : le job `tests` de la CI
# n'installe que pytest et saute ce module.
pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")
np = pytest.importorskip("numpy", reason="job « tests-deps » : pip install -r requirements.txt")
requests = pytest.importorskip("requests", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import index_sens  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MDP = "mot-de-passe-de-test"
EMPREINTE = "0a109f422b47"
AUTRE = "ffffeeee0000"


class FauxOllama:
    """`/api/tags` et `/api/embeddings`, sans réseau. Le vecteur d'un texte se
    déduit de son contenu : deux textes égaux ont le même vecteur."""

    RequestException = requests.RequestException

    def __init__(self, empreinte=EMPREINTE, nom="nomic-embed-text:latest"):
        self.empreinte, self.nom = empreinte, nom
        self.embeddings = 0

    class _R:
        def __init__(self, donnees):
            self._d = donnees

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    def get(self, url, timeout=None):
        assert url.endswith("/api/tags")
        modeles = [{"name": self.nom, "model": self.nom,
                    "digest": self.empreinte + "0" * 52}] if self.empreinte else []
        return self._R({"models": modeles})

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/api/embeddings")
        self.embeddings += 1
        graine = sum(map(ord, json["prompt"])) % 997
        vec = np.random.default_rng(graine).random(8).tolist()
        return self._R({"embedding": vec})


def _vecteur(texte):
    return np.array(FauxOllama().post("/api/embeddings", {"prompt": texte}).json()["embedding"],
                    dtype=np.float32).tobytes()


# ─── L'API ────────────────────────────────────────────────────────────────────

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
    monkeypatch.setattr(api, "ADMIN_KEY", "")
    monkeypatch.setattr(api, "RAG_ENABLED", True)
    monkeypatch.setattr(api, "RAG_ASK_ENABLED", False)
    ollama = FauxOllama()
    monkeypatch.setattr(api, "_requests", ollama)
    client = TestClient(api.app)

    def sql(requete, params=()):
        c = sqlite3.connect(chemin_db)
        try:
            c.execute(requete, params)
            c.commit()
        finally:
            c.close()

    sql("INSERT INTO users(email, password_hash, role) VALUES(?,?,?)",
        ("valid@exemple.fr", api_auth._hash_pw(MDP), "validator"))
    j = client.post("/api/auth/login",
                    json={"email": "valid@exemple.fr", "password": MDP}).json()
    h = {"Authorization": f"Bearer {j['access_token']}"}

    def extrait(sid, texte, modele="nomic-embed-text:latest", emp=EMPREINTE):
        sql("INSERT INTO embeddings(source_table, source_id, chunk_text, vector, modele,"
            " modele_empreinte) VALUES('events', ?, ?, ?, ?, ?)",
            (sid, texte, _vecteur(texte), modele, emp))

    return {"api": api, "client": client, "h": h, "sql": sql, "extrait": extrait,
            "ollama": ollama}


class TestGenerationSeparee:
    def test_la_recherche_ouverte_n_ouvre_pas_la_generation(self, atelier):
        c, h = atelier["client"], atelier["h"]
        config = c.get("/api/rag/config", headers=h).json()
        assert (config["enabled"], config["ask_enabled"]) == (True, False)
        r = c.post("/api/rag/ask", headers=h, json={"question": "budget"})
        assert r.status_code == 503 and "RAG_ASK_ENABLED" in r.json()["detail"]
        atelier["extrait"](1, "délibération sur la cantine")
        assert c.get("/api/rag/search", headers=h, params={"q": "cantine"}).status_code == 200

    def test_la_generation_demande_aussi_la_recherche(self, atelier, monkeypatch):
        api, c, h = atelier["api"], atelier["client"], atelier["h"]
        monkeypatch.setattr(api, "RAG_ASK_ENABLED", True)
        assert c.get("/api/rag/config", headers=h).json()["ask_enabled"] is True
        monkeypatch.setattr(api, "RAG_ENABLED", False)
        assert c.get("/api/rag/config", headers=h).json()["ask_enabled"] is False
        assert c.post("/api/rag/ask", headers=h, json={"question": "x"}).status_code == 503

    def test_l_interface_ne_montre_faire_resumer_que_si_la_generation_est_ouverte(self):
        from pathlib import Path
        page = (Path(__file__).resolve().parent.parent / "dashboard" / "src" / "routes"
                / "atelier" / "ia" / "+page.svelte").read_text(encoding="utf-8")
        bloc = re.search(r"\{#if config\?\.ask_enabled\}(.*?)\{/if\}", page, re.S)
        assert bloc and "Faire résumer" in bloc.group(1)
        assert page.count(">Faire résumer<") == 1     # le seul bouton, dans le bloc


class TestModeleDeLIndex:
    def test_meme_empreinte_la_recherche_repond(self, atelier):
        atelier["extrait"](1, "récusation d'un conseiller")
        atelier["extrait"](2, "budget de l'eau", modele="nomic-embed-text")  # autre nom, même modèle
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "conflit"})
        assert r.status_code == 200 and len(r.json()) == 2

    def test_un_autre_modele_est_refuse_avec_quoi_relancer(self, atelier):
        atelier["extrait"](1, "récusation d'un conseiller", emp=AUTRE)
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "conflit"})
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "build_rag_index.py --reset" in detail and AUTRE in detail and EMPREINTE in detail
        assert atelier["ollama"].embeddings == 0      # la question n'est même pas calculée

    def test_un_index_sans_trace_de_modele_est_refuse(self, atelier):
        atelier["extrait"](1, "récusation", modele=None, emp=None)
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "x"})
        assert r.status_code == 409 and "modèle inconnu" in r.json()["detail"]

    def test_un_index_melange_est_refuse(self, atelier):
        atelier["extrait"](1, "a")
        atelier["extrait"](2, "b", emp=AUTRE)
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "x"})
        assert r.status_code == 409

    def test_modele_absent_ou_ollama_muet(self, atelier, monkeypatch):
        atelier["extrait"](1, "a")
        atelier["ollama"].empreinte = None
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "x"})
        assert r.status_code == 409 and "ollama pull" in r.json()["detail"]

        def muet(*a, **k):
            raise requests.ConnectionError("http://127.0.0.1:11434 texte du serveur")
        monkeypatch.setattr(atelier["ollama"], "get", muet)
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"], params={"q": "x"})
        assert r.status_code == 503 and "11434" not in r.json()["detail"]

    @pytest.mark.parametrize("limite, statut", [(0, 422), (-1, 422), (51, 422), (10_000, 422),
                                                (1, 200), (50, 200)])
    def test_la_limite_est_bornee(self, atelier, limite, statut):
        """`limit` n'avait pas de borne haute : une recherche pouvait rendre
        l'index entier (brief du 14/11/2026). La page IA demande 10."""
        atelier["extrait"](1, "récusation d'un conseiller")
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"],
                                  params={"q": "conflit", "limit": limite})
        assert r.status_code == statut, r.text

    def test_un_vecteur_pousse_porte_son_modele(self, atelier):
        c, h = atelier["client"], atelier["h"]
        item = {"source_table": "events", "source_id": 7, "chunk_text": "t", "vector": [0.1, 0.2]}
        assert c.post("/api/atelier/ia/embeddings", headers=h, json=[item]).status_code == 422
        item |= {"modele": "nomic-embed-text:latest", "modele_empreinte": EMPREINTE,
                 "vector": [0.1] * 768}
        assert c.post("/api/atelier/ia/embeddings", headers=h, json=[item]).status_code == 200

    def test_un_vecteur_pousse_ne_casse_pas_la_recherche(self, atelier):
        """Revue vigie-securite du 07/11 (E) : une autre dimension, ou un NaN,
        faisaient tomber toutes les recherches."""
        c, h = atelier["client"], atelier["h"]
        base = {"source_table": "events", "chunk_text": "t",
                "modele": "nomic-embed-text:latest", "modele_empreinte": EMPREINTE}
        assert c.post("/api/atelier/ia/embeddings", headers=h,
                      json=[base | {"source_id": 1, "vector": [0.1] * 768}]).status_code == 200
        r = c.post("/api/atelier/ia/embeddings", headers=h,
                   json=[base | {"source_id": 2, "vector": [0.1] * 384}])
        assert r.status_code == 400
        nan = ('[{"source_table":"events","source_id":3,"chunk_text":"t",'
               '"modele":"nomic-embed-text:latest","modele_empreinte":"' + EMPREINTE
               + '","vector":[NaN' + ',0.1' * 767 + ']}]')
        # Refusé, jamais rangé. Le code est 500 et non 422 : la réponse de
        # validation de FastAPI recopie l'entrée et ne sait pas écrire un NaN
        # (défaut de toutes les routes, hors de ce chantier).
        brut = TestClient(atelier["api"].app, raise_server_exceptions=False)
        r = brut.post("/api/atelier/ia/embeddings", content=nan,
                      headers=h | {"Content-Type": "application/json"})
        assert r.status_code in (422, 500)
        conn = sqlite3.connect(atelier["api"].DB_PATH)
        assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE source_id=3").fetchone()[0] == 0
        conn.close()
        trop = [base | {"source_id": i, "vector": [0.1] * 768} for i in range(501)]
        assert c.post("/api/atelier/ia/embeddings", headers=h, json=trop).status_code == 422

    def test_une_question_trop_longue_est_refusee(self, atelier):
        r = atelier["client"].get("/api/rag/search", headers=atelier["h"],
                                  params={"q": "x" * 2001})
        assert r.status_code == 422


# ─── L'indexeur ───────────────────────────────────────────────────────────────

@pytest.fixture
def indexeur(base, monkeypatch):
    from scripts import build_rag_index as b
    ollama = FauxOllama()
    monkeypatch.setattr(b, "requests", ollama)

    def faux_embed(texte):
        ollama.embeddings += 1
        return np.frombuffer(_vecteur(texte), dtype=np.float32)
    monkeypatch.setattr(b, "embed", faux_embed)

    def acte(titre, contenu="contenu suffisamment long pour être indexé tel quel ici"):
        cur = base.execute("INSERT INTO events(type, date, title, content) VALUES"
                           " ('deliberation', '2026-10-01', ?, ?)", (titre, contenu))
        base.commit()
        return cur.lastrowid

    return {"b": b, "ollama": ollama, "acte": acte}


def _index(base):
    return {r["source_id"]: (r["chunk_text"], r["modele"], r["modele_empreinte"])
            for r in base.execute("SELECT * FROM embeddings")}


class TestIndexeur:
    def test_chaque_vecteur_porte_le_modele(self, base, indexeur):
        a = indexeur["acte"]("Vote du budget primitif")
        m = indexeur["b"].construire(base, reset=False, source=None)
        assert _index(base)[a][1:] == ("nomic-embed-text:latest", EMPREINTE)
        assert (m["ajoutes"], m["modele"], m["empreinte"]) == (1, "nomic-embed-text:latest", EMPREINTE)
        assert m["octets_index"] > 0 and m["octets_base"] >= m["octets_index"]

    def test_incremental_recalcule_ce_qui_change_et_retire_ce_qui_disparait(self, base, indexeur):
        b = indexeur["b"]
        a1, a2, a3 = (indexeur["acte"](t) for t in ("Cantine", "Voirie", "Eau potable"))
        b.construire(base, reset=False, source=None)
        appels = indexeur["ollama"].embeddings
        base.execute("UPDATE events SET title='Voirie communale' WHERE id=?", (a2,))
        base.execute("DELETE FROM events WHERE id=?", (a3,))
        base.commit()
        m = b.construire(base, reset=False, source=None)
        assert (m["inchanges"], m["mis_a_jour"], m["retires"], m["ajoutes"]) == (1, 1, 1, 0)
        assert indexeur["ollama"].embeddings == appels + 1
        assert set(_index(base)) == {a1, a2} and "Voirie communale" in _index(base)[a2][0]

    def test_un_texte_qui_ne_se_recalcule_pas_ne_reste_pas_en_base(self, base, indexeur,
                                                                     monkeypatch):
        b = indexeur["b"]
        a = indexeur["acte"]("Subvention à Untel")
        b.construire(base, reset=False, source=None)
        base.execute("UPDATE events SET title='Subvention' WHERE id=?", (a,))
        base.commit()

        def en_panne(texte):
            raise TimeoutError
        monkeypatch.setattr(b, "embed", en_panne)
        with pytest.raises(b.Echec, match="ExtraitsEnEchec"):
            b.construire(base, reset=False, source=None)
        assert a not in _index(base)

    def test_un_index_d_un_autre_modele_ne_se_complete_pas(self, base, indexeur):
        b = indexeur["b"]
        indexeur["acte"]("Cantine")
        b.construire(base, reset=False, source=None)
        indexeur["ollama"].empreinte = AUTRE        # un `ollama pull` a changé le modèle
        indexeur["acte"]("Voirie")
        with pytest.raises(b.Echec, match="ModeleDifferent"):
            b.construire(base, reset=False, source=None)
        assert {e for _, _, e in _index(base).values()} == {EMPREINTE}
        m = b.construire(base, reset=True, source=None)
        assert m["ajoutes"] == 2 and {e for _, _, e in _index(base).values()} == {AUTRE}

    def test_un_texte_trop_long_est_tronque_pas_perdu(self, monkeypatch):
        """Ollama refuse au-delà de sa fenêtre : l'indexeur recalcule sur le début."""
        from scripts import build_rag_index as b
        recus = []

        class Reponse:
            def __init__(self, code, texte="", donnees=None):
                self.status_code, self.text, self._d = code, texte, donnees

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(str(self.status_code))

            def json(self):
                return self._d

        class Etroit:                       # une fenêtre de 1 200 caractères
            def post(self, url, json=None, timeout=None):
                recus.append(len(json["prompt"]))
                if len(json["prompt"]) > 1200:
                    return Reponse(500, '{"error":"input (3122 tokens) is too large to process"}')
                return Reponse(200, donnees={"embedding": [0.1] * 8})

        monkeypatch.setattr(b, "requests", Etroit())
        assert b.embed("x" * 10_000).shape == (8,)
        assert recus == [4000, 2000, 1000]
        recus.clear()
        assert b.embed("court").shape == (8,) and recus == [5]

        class Panne(Etroit):                # une autre erreur ne se tronque pas
            def post(self, url, json=None, timeout=None):
                recus.append(len(json["prompt"]))
                return Reponse(500, '{"error":"model runner crashed"}')
        monkeypatch.setattr(b, "requests", Panne())
        recus.clear()
        with pytest.raises(requests.HTTPError):
            b.embed("x" * 10_000)
        assert recus == [4000]

    def test_le_calcul_ne_tient_pas_le_verrou_d_ecriture(self, base, indexeur, monkeypatch,
                                                          tmp_path):
        """Pendant qu'Ollama calcule, un autre écrivain (l'atelier, une reprise)
        passe sans attendre : aucune transaction ne reste ouverte d'un extrait
        à l'autre."""
        b = indexeur["b"]
        base.execute("PRAGMA journal_mode=WAL")
        for t in ("Cantine", "Voirie", "Eau potable"):
            indexeur["acte"](t)
        bloques = []

        def embed_pendant_qu_un_autre_ecrit(texte):
            autre = sqlite3.connect(tmp_path / "test.db", timeout=0)
            try:
                autre.execute("INSERT INTO events(type, date, title) VALUES"
                              " ('note', '2026-11-08', 'saisie pendant le calcul')")
                autre.commit()
            except sqlite3.OperationalError:
                bloques.append(texte)
            finally:
                autre.close()
            return np.frombuffer(_vecteur(texte), dtype=np.float32)
        monkeypatch.setattr(b, "embed", embed_pendant_qu_un_autre_ecrit)
        b.construire(base, reset=False, source="events")
        assert bloques == []

    def test_ollama_muet_ou_modele_absent(self, base, indexeur, monkeypatch):
        b = indexeur["b"]
        indexeur["ollama"].empreinte = None
        with pytest.raises(b.Echec, match="ModeleAbsent"):
            b.construire(base, reset=False, source=None)

        def muet(*a, **k):
            raise requests.ConnectionError("texte du serveur")
        monkeypatch.setattr(indexeur["ollama"], "get", muet)
        with pytest.raises(b.Echec, match="connexion"):
            b.construire(base, reset=False, source=None)


# ─── L'état, et ce qu'en lit `vigie_etat.sh` ─────────────────────────────────

# Copie des formes que `vigie_etat.sh` (dépôt d'exploitation) reconnaît : une
# alerte d'une autre forme y devient « alerte de forme inconnue ».
_MOTIF = (r"(?:SystemExit \d{1,3}|HTTP \d{3}|certificat|DNS|délai|connexion|curl \d{1,3}"
          r"|[A-Za-z_][A-Za-z0-9_]{0,39})")
_FORMES = [re.compile(f"[\\w.-]{{1,40}} : {f}", re.ASCII) for f in (
    rf"\d+ échecs de suite — {_MOTIF}",
    r"\d+ j sans passage abouti \(TTL \d+ j\)",
)]


class TestEtat:
    def test_un_echec_se_compte_et_une_reussite_le_remet_a_zero(self, tmp_path):
        index_sens.ecrire_etat(tmp_path, reussi=True, mesures={"extraits": 3, "duree_s": 1.5})
        index_sens.ecrire_etat(tmp_path, reussi=False, motif="connexion")
        etat = index_sens.ecrire_etat(tmp_path, reussi=False, motif="ModeleDifferent")
        assert etat["echecs_consecutifs"] == 2 and etat["dernier_succes"]
        alertes = index_sens.alertes(etat)
        assert alertes == ["index_sens : 2 échecs de suite — ModeleDifferent"]
        assert all(any(f.fullmatch(a) for f in _FORMES) for a in alertes)
        etat = index_sens.ecrire_etat(tmp_path, reussi=True)
        assert etat["echecs_consecutifs"] == 0 and index_sens.alertes(etat) == []

    def test_l_etat_ne_porte_que_des_formes_fermees(self, tmp_path):
        etat = index_sens.ecrire_etat(
            tmp_path, reussi=False, motif="Erreur: http://hote/?jeton=abc texte libre",
            mesures={"modele": "nom\nlibre <b>", "empreinte": "pas-hex", "extraits": 4,
                     "texte": "jamais"})
        assert etat["derniere_erreur"] == "autre"
        assert "modele" not in etat and "empreinte" not in etat and "texte" not in etat

    def test_l_etat_est_refiltre_a_la_relecture(self, tmp_path):
        import json
        (tmp_path / index_sens.ETAT).write_text(json.dumps({
            "dernier_succes": "hier <script>", "echecs_consecutifs": 2,
            "derniere_erreur": "texte libre du serveur", "extraits": 9,
            "inconnu": "jamais", "modele": "a b"}))
        etat = index_sens.lire_etat(tmp_path)
        assert etat["dernier_succes"] is None and etat["derniere_erreur"] == "autre"
        assert etat["extraits"] == 9 and "inconnu" not in etat and "modele" not in etat

    def test_un_index_qui_n_avance_plus_se_voit(self, tmp_path):
        etat = index_sens.ecrire_etat(tmp_path, reussi=True)
        plus_tard = datetime.now() + timedelta(days=3)
        alertes = index_sens.alertes(etat, plus_tard)
        assert alertes == ["index_sens : 3 j sans passage abouti (TTL 1 j)"]
        assert all(any(f.fullmatch(a) for f in _FORMES) for a in alertes)

    def test_sans_index_entretenu_rien_a_dire(self, tmp_path):
        assert index_sens.lire_etat(tmp_path) is None
        assert index_sens.alertes(None) == []

    def test_sante_json_porte_l_index(self, tmp_path, monkeypatch, schema_sql):
        from scripts import collect_loop
        chemin = tmp_path / "instance.db"
        conn = sqlite3.connect(chemin)
        conn.executescript(schema_sql)
        conn.row_factory = sqlite3.Row
        monkeypatch.setattr(collect_loop, "DB_PATH", chemin)
        index_sens.ecrire_etat(tmp_path, reussi=False, motif="connexion")
        etat = collect_loop.sante(conn, suspendus={})
        conn.close()
        assert etat["index_sens"]["echecs_consecutifs"] == 1
        assert "index_sens : 1 échecs de suite — connexion" in etat["alertes"]
