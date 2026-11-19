"""Archive brute réduite au périmètre (BODACC, JO des associations).

Avis vigie-dpo du 02/11/2026 : `fetch_json` archivait la page d'API entière
avant le filtre par commune ; interrogée par code postal, elle porte les
annonces des communes voisines, personnes physiques comprises. Annonces
FICTIVES : Testonville est la commune de l'instance d'essai, Ailleurs ne l'est
pas.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

from collectors import archive, bodacc, rna

ROOT = Path(__file__).resolve().parent.parent

DEDANS = {"ville": "Testonville", "commercant": "SARL FICTIVE"}
DEHORS = {"ville": "Ailleurs", "commercant": "PERSONNE FICTIVE"}


def test_une_page_n_archive_que_ses_retenues(monkeypatch):
    archives = []
    monkeypatch.setattr(archive, "archive_fetch", lambda *a, **k: archives.append((a, k)))
    page = {"total_count": 2, "results": [DEDANS, DEHORS]}
    archive.archiver_retenues("bodacc", "https://api.invalid/p", page, [DEDANS])
    [(args, _)] = archives
    ecrit = json.loads(args[2])
    assert ecrit["results"] == [DEDANS] and ecrit["total_count"] == 2
    # Une page entièrement hors périmètre ne laisse rien.
    archive.archiver_retenues("bodacc", "https://api.invalid/p", page, [])
    assert len(archives) == 1


def test_fetch_json_peut_ne_pas_archiver(monkeypatch):
    archives = []
    monkeypatch.setattr(archive, "archive_fetch", lambda *a, **k: archives.append(a))

    class Reponse:
        status = 200
        headers = type("H", (), {"get_content_type": staticmethod(lambda: "application/json")})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"results": []}'
        def geturl(self): return "https://api.invalid/r"
    monkeypatch.setattr(archive.urllib.request, "urlopen", lambda *a, **k: Reponse())
    assert archive.fetch_json("https://api.invalid/r", source="t", archiver=False) == {"results": []}
    assert archives == []
    archive.fetch_json("https://api.invalid/r", source="t")
    assert len(archives) == 1


def test_la_collecte_bodacc_archive_apres_son_filtre(monkeypatch):
    archives = []
    monkeypatch.setattr(bodacc, "fetch_page",
                        lambda *a, **k: {"total_count": 2, "results": [DEDANS, DEHORS]})
    monkeypatch.setattr(bodacc, "archiver_retenues",
                        lambda source, url, page, retenues: archives.append((source, retenues)))
    monkeypatch.setattr(bodacc, "import_record", lambda *a, **k: False)
    compteurs = {"lues": 0, "inserees": 0, "hors": 0}
    bodacc._collecter_fenetre(None, "99000", None, None, False, compteurs)
    assert archives == [("bodacc", [DEDANS])]
    assert compteurs["hors"] == 1


def test_la_collecte_jo_archive_apres_son_filtre(monkeypatch):
    archives = []
    monkeypatch.setattr(rna, "fetch_jo_page", lambda *a, **k: {
        "total_count": 2,
        "results": [{"commune_actuelle": "Testonville"}, {"commune_actuelle": "Ailleurs"}]})
    monkeypatch.setattr(rna, "archiver_retenues",
                        lambda source, url, page, retenues: archives.append((source, retenues)))
    assert len(rna.fetch_all_jo("99000")) == 2
    assert archives == [("rna-jo", [{"commune_actuelle": "Testonville"}])]


# ── La purge de l'existant ───────────────────────────────────────────────────

def _purge():
    spec = importlib.util.spec_from_file_location(
        "_purge", ROOT / "scripts" / "purger_archive_hors_perimetre.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _instance(tmp_path, pages: dict[str, dict]):
    """Une base réelle (schéma du dépôt) et ses fichiers bruts, sous tmp_path."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    raw = tmp_path / "data" / "raw" / "bodacc"
    raw.mkdir(parents=True)
    for nom, page in pages.items():
        brut = json.dumps(page).encode()
        (raw / nom).write_bytes(brut)
        conn.execute("INSERT INTO raw_documents (source, url, doc_type, sha256, byte_size, local_path)"
                     " VALUES ('bodacc', ?, 'json', ?, ?, ?)",
                     (f"https://api.invalid/{nom}", nom, len(brut), f"data/raw/bodacc/{nom}"))
    conn.commit()
    return conn


def test_la_purge_reduit_supprime_et_se_controle(tmp_path, monkeypatch):
    purge = _purge()
    monkeypatch.setattr(purge, "ROOT", tmp_path)
    monkeypatch.setattr(purge, "RAW_DIR", tmp_path / "data" / "raw")
    conn = _instance(tmp_path, {
        "mixte.json": {"total_count": 2, "results": [DEDANS, DEHORS]},
        "dehors.json": {"total_count": 1, "results": [DEHORS]},
        "dedans.json": {"total_count": 1, "results": [DEDANS]},
    })
    (tmp_path / "data" / "raw" / "bodacc" / "orphelin.json").write_text(
        json.dumps({"results": [DEHORS]}))

    a_blanc = purge.purger(conn, appliquer=False)
    assert (a_blanc["reduites"], a_blanc["supprimees"], a_blanc["intactes"]) == (1, 1, 1)
    assert purge.controler() == (4, 3)        # à blanc : rien n'a bougé

    c = purge.purger(conn, appliquer=True)
    assert c["annonces_ecartees"] == 3 and c["orphelins"] == 1
    assert purge.controler() == (2, 0)
    restes = conn.execute("SELECT local_path, byte_size FROM raw_documents ORDER BY id").fetchall()
    assert len(restes) == 2                   # « dehors » est parti, ligne et fichier
    for local, taille in restes:
        assert (tmp_path / local).stat().st_size == taille
    # Rejouée, elle ne trouve plus rien à faire.
    assert purge.purger(conn, appliquer=True)["annonces_ecartees"] == 0
