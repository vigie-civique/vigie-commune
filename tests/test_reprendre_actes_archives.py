"""Reprise d'actes qu'un portail ne liste plus, depuis ses réponses d'API archivées.

Saillans, 08/11/2026 : 42 actes de la CC relus par `conseils --url` ont été découpés
comme des procès-verbaux (95 fragments). Rejoué ici sur le schéma réel, données
fictives : l'acte est rebâti depuis l'archive avec son numéro et son objet
déclarés, les fragments partent, et une lecture qui échoue ne retire rien.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber",
                    reason="job « tests-deps » : pip install -r requirements.txt")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import reprendre_actes_archives as reprise  # noqa: E402

BASE = "https://actes-epci.exemple-portail.eu"
PDF = "/repository/2026/07/P1_x.pdf"
PDF2 = "/repository/2026/07/P2_x.pdf"


def _document(chemin, numero, objet, type_="Délibération du conseil communautaire", corbeille=14):
    val = lambda v: {"displayValue": v}  # noqa: E731
    return {"bifferPath": chemin, "name": chemin.rsplit("/", 1)[-1],
            "parentDocType": {"id": corbeille, "caption": "ACTES"},
            "values": {"NUMEROACTE": val(numero), "OBJET": val(objet),
                       "TYPE_DACTE_ACTE": val(type_), "DATEACTE": val("2026-07-02")}}


@pytest.fixture
def instance(base, tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    import collectors.db
    monkeypatch.setattr(collectors.db, "DB_PATH", db)
    monkeypatch.setattr(reprise.config, "DB_PATH", db)
    monkeypatch.setattr(reprise.config, "PAGES", {"epci": {"portail": BASE + "/public/14"}})
    raw = tmp_path / "raw"
    (raw / "actes-epci.exemple-portail.eu").mkdir(parents=True)
    (raw / "actes-epci.exemple-portail.eu" / "a.json").write_text(json.dumps({
        "documents": [_document(PDF, "DE2026090", "Approbation du compte de gestion"),
                      _document(PDF2, "DE2026091", "Tarifs de la piscine"),
                      _document("/repository/x/arrete.pdf", "AR1", "Arrêté",
                                type_="Arrêté du Président")],
        "nextDocsIds": []}))
    monkeypatch.setattr(reprise, "RAW_DIR", raw)
    ids = []
    for url in (BASE + PDF, BASE + PDF, BASE + PDF2):
        ids.append(base.execute(
            "INSERT INTO events (type, date, title, source, source_url) VALUES "
            "('deliberation_cc', '2026-06-25', 'D''autoriser le Président', 'x', ?)",
            (url,)).lastrowid)
    base.commit()
    liste = tmp_path / "liste.txt"
    liste.write_text(f"2026-06-25|{BASE}{PDF}\n2026-06-25|{BASE}{PDF2}\n")
    return base, ids, liste


def test_archives_rebatissent_l_acte_declare(instance):
    archives = reprise.actes_archives(BASE + "/public/14")
    assert set(archives) == {BASE + PDF, BASE + PDF2}, "un arrêté n'est pas une délibération"
    acte = archives[BASE + PDF]["doc"].acte
    assert acte["numero"] == "DE2026090" and acte["objet"] == "Approbation du compte de gestion"


def test_plan(instance):
    base, ids, liste = instance
    lignes = reprise.plan(base, reprise._urls(liste), "epci",
                          reprise.actes_archives(BASE + "/public/14"), {14})
    assert [(l["action"], l["fragments"], l["numero"]) for l in lignes] == [
        ("relire", 2, "DE2026090"), ("relire", 1, "DE2026091")]


def test_appliquer_par_traiter_acte_et_annuler_un_echec(instance, monkeypatch):
    base, ids, liste = instance
    archives = reprise.actes_archives(BASE + "/public/14")
    vus = []

    def traiter(conn, doc, portee, verbose=True, avec_ocr=False, budget=None):
        vus.append(doc.acte["numero"])
        if doc.url.endswith(PDF2):
            return {"statut": "sans_couche_texte", "delibs": 0}
        conn.execute("INSERT INTO events (type, date, title, source, source_url) VALUES "
                     "('deliberation_cc', '2026-06-25', ?, 'x', ?)", (doc.acte["objet"], doc.url))
        return {"statut": "ok", "delibs": 1}

    monkeypatch.setattr(reprise.conseils, "traiter", traiter)
    monkeypatch.setattr(reprise.conseils, "lire_document", lambda doc, avec_ocr=False: ("t", "pdf", False))
    lignes = reprise.plan(base, reprise._urls(liste), "epci", archives, {14})
    bilan = reprise.appliquer(lignes, "epci", archives)
    assert vus == ["DE2026090", "DE2026091"]
    assert bilan == {"ok": 1, "annulé (sans_couche_texte)": 1}
    rangs = base.execute("SELECT source_url, title FROM events WHERE type='deliberation_cc'"
                         " ORDER BY id").fetchall()
    # le fragment de PDF2 reste (lecture en échec), ceux de PDF partent
    assert [(r[0].rsplit("/", 1)[-1], r[1]) for r in rangs] == [
        ("P2_x.pdf", "D'autoriser le Président"), ("P1_x.pdf", "Approbation du compte de gestion")]


def test_absent_des_archives_ou_cite_ailleurs_reste_a_trancher(instance):
    base, ids, liste = instance
    base.execute("INSERT INTO annotations (object_type, object_id) VALUES ('deliberation', ?)",
                 (ids[2],))
    base.commit()
    liste.write_text(liste.read_text() + f"{BASE}/repository/inconnu.pdf\n")
    lignes = reprise.plan(base, reprise._urls(liste), "epci",
                          reprise.actes_archives(BASE + "/public/14"), {14})
    assert [(l["action"], l["motif"]) for l in lignes] == [
        ("relire", ""), ("?", "fragment cité ailleurs"), ("?", "absent des réponses archivées")]


def test_appliquer_exige_le_plan_relu(instance, tmp_path, monkeypatch):
    base, ids, liste = instance
    sim = tmp_path / "sim.tsv"
    monkeypatch.setattr(sys, "argv", ["x", "--portee", "epci", "--liste", str(liste),
                                      "--tsv", str(sim)])
    assert reprise.main() == 0
    monkeypatch.setattr(sys, "argv", ["x", "--portee", "epci", "--liste", str(liste),
                                      "--tsv", str(tmp_path / "b.tsv"), "--appliquer"])
    assert reprise.main() == 2
    base.execute("INSERT INTO events (type, date, title, source, source_url) VALUES "
                 "('deliberation_cc', '2026-06-25', 'neuf', 'x', ?)", (BASE + PDF,))
    base.commit()
    monkeypatch.setattr(sys, "argv", ["x", "--portee", "epci", "--liste", str(liste),
                                      "--tsv", str(tmp_path / "c.tsv"), "--appliquer",
                                      "--plan-relu", str(sim)])
    assert reprise.main() == 1, "un fragment né après la simulation change le plan"


def _plan(base, liste):
    return {l["url"].rsplit("/", 1)[-1]: (l["action"], l["motif"]) for l in reprise.plan(
        base, reprise._urls(liste), "epci", reprise.actes_archives(BASE + "/public/14"), {14})}


def test_autre_corbeille_double_declaration_et_saisie_restent_a_trancher(instance, tmp_path):
    base, ids, liste = instance
    dossier = tmp_path / "raw" / "actes-epci.exemple-portail.eu"
    (dossier / "b.json").write_text(json.dumps({"documents": [
        _document(PDF, "DE2026090", "Autre objet"),                       # double déclaration
        _document("/repository/2026/07/P3_x.pdf", "C1", "Délibération communale", corbeille=15)]}))
    base.execute("UPDATE events SET origine='atelier' WHERE id=?", (ids[2],))
    base.commit()
    liste.write_text(liste.read_text() + f"{BASE}/repository/2026/07/P3_x.pdf\n")
    assert _plan(base, liste) == {
        "P1_x.pdf": ("?", "déclaré de plusieurs façons"),
        "P2_x.pdf": ("?", "saisie de l'atelier"),
        "P3_x.pdf": ("?", "autre corbeille que celle déclarée")}


@pytest.mark.parametrize("chemin", ["@autre.fr/x.pdf", ".autre.fr/x.pdf", "//autre.fr/x.pdf"])
def test_un_chemin_ne_change_pas_d_hote(chemin):
    from collectors.connecteurs.dematdoc import document_publie
    assert document_publie(_document(chemin, "1", "o"), BASE, "s", BASE + "/public/14") is None
