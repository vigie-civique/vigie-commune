"""Un flux publié remonte à sa pièce (revue d'architecture du 07/11/2026, C1).

`flows.json` ne portait que des étiquettes (« CR CM année ») : la requête du
snapshot ne lisait ni `event_id` ni `raw_document_id`. Construit sur la base de
référence de la CI (schéma réel), pas sur une base simulée.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_reference import ROOT, construire  # noqa: E402


def _semer(db: Path) -> None:
    conn = sqlite3.connect(db)
    un = lambda sql: conn.execute(sql).fetchone()[0]
    mairie = un("SELECT id FROM entities WHERE name = 'Mairie d''épreuve'")
    boulangerie = un("SELECT id FROM entities WHERE name = 'Boulangerie d''épreuve'")
    acte = conn.execute(
        "INSERT INTO events (type, date, title, content, source) VALUES ('deliberation',"
        " '2026-02-01', 'Subvention RENVOI_ACTE', 'Le conseil décide.', 'interieur')").lastrowid
    pdf = conn.execute(
        "INSERT INTO raw_documents (source, url, doc_type, sha256, local_path) VALUES"
        " ('mairie', 'https://mairie.example/pv-2026.pdf', 'pdf', 'renvoi-1', 'x.pdf')").lastrowid
    depose = conn.execute(
        "INSERT INTO raw_documents (source, url, doc_type, sha256, local_path) VALUES"
        " ('depot', NULL, 'pdf', 'renvoi-2', 'y.pdf')").lastrowid
    for montant, desc, event_id, doc in (
            (1101, "RENVOI_PAR_ACTE", acte, None),
            (1102, "RENVOI_PAR_DOCUMENT", None, pdf),
            (1103, "RENVOI_DEPOSE", None, depose)):
        conn.execute(
            "INSERT INTO financial_flows (from_id, to_id, type, year, amount, description,"
            " source, confidence, origine, event_id, raw_document_id)"
            " VALUES (?, ?, 'subvention', 2026, ?, ?, 'CR CM 2026', 'verified', 'verbatim', ?, ?)",
            (mairie, boulangerie, montant, desc, event_id, doc))
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    return construire(ROOT, tmp_path_factory.mktemp("renvoi"), apres_amorcage=_semer)


@pytest.fixture(scope="module")
def vs():
    spec = importlib.util.spec_from_file_location(
        "verify_snapshot_renvoi", ROOT / "scripts" / "verify_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _par_description(out: Path) -> dict[str, dict]:
    flows = json.loads((out / "flows.json").read_text(encoding="utf-8"))["flows"]
    return {f["description"]: f for f in flows}


def test_le_flux_porte_l_acte_publie_ou_l_url_externe(snapshot):
    flux = _par_description(snapshot)
    actes = {e["id"]: e for e in json.loads(
        (snapshot / "events.json").read_text(encoding="utf-8"))["events"]}
    par_acte = flux["RENVOI_PAR_ACTE"]
    assert par_acte["acte_id"] in actes and par_acte["document"] is None
    assert flux["RENVOI_PAR_DOCUMENT"]["document"] == "https://mairie.example/pv-2026.pdf"
    assert flux["RENVOI_DEPOSE"]["acte_id"] is None and flux["RENVOI_DEPOSE"]["document"] is None


def test_les_identifiants_internes_ne_sortent_pas(snapshot):
    for f in _par_description(snapshot).values():
        assert not {"event_id", "raw_document_id", "document_url"} & set(f)


def test_le_controle_signale_le_flux_verbatim_sans_piece(snapshot, vs):
    rep = vs.Report()
    vs.check_renvoi_des_flux(snapshot, rep)
    assert rep.errors == {}
    sans = rep.warnings.get("flux lu dans un compte rendu sans renvoi à sa pièce") or []
    assert len(sans) == 1 and "1 flux verbatim" in sans[0]
    assert "flux qui renvoie à un acte non publié" not in rep.errors


def test_le_manifeste_dit_avec_quoi_il_a_ete_construit(snapshot):
    """C3 : empreinte des règles et de l'instance, dernier geste du journal."""
    entete = json.loads((snapshot / "manifeste.json").read_text(encoding="utf-8"))
    assert len(entete["regles_sha256"]) == 64
    assert len(entete["instance_sha256"]) == 64
    assert "audit_log_id" in entete
