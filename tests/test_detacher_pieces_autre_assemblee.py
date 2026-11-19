"""La reprise défait les séances qui servent une pièce de l'autre assemblée.

Trois formes relevées sur les sites servis le 07/11/2026, rejouées ici sur le
schéma réel avec des données fictives : une séance communale qui n'a pas eu
lieu (seule pièce : le PV de la CC), une vraie séance communale dont le PV de
la CC est devenu la pièce principale, et une séance fantôme sans `pieces` (forme
d'avant le 15/09, Brassac).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber",
                    reason="job « tests-deps » : pip install -r requirements.txt")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import detacher_pieces_autre_assemblee as reprise  # noqa: E402

CC = "https://www.exemple-epci.fr/pv/"
CM = "https://www.exemple-commune.fr/cr/"


def _ev(base, type_, date, src, pieces=None, titre="t"):
    meta = {} if pieces is None else {"pieces": [{"nature": n, "url": u} for n, u in pieces]}
    return base.execute(
        "INSERT INTO events (type, date, title, source, source_url, metadata)"
        " VALUES (?,?,?,?,?,?)", (type_, date, titre, "x", src, json.dumps(meta))).lastrowid


@pytest.fixture
def touchee(base, monkeypatch):
    monkeypatch.setattr(reprise.config, "COMMUNE_URL", "https://www.exemple-commune.fr")
    monkeypatch.setattr(reprise.config, "EPCI_URL", "https://www.exemple-epci.fr")
    ids = {}
    # 1. séance communale fantôme
    _ev(base, "conseil_communautaire", "2021-10-20", CC + "a.pdf", [("proces_verbal", CC + "a.pdf")])
    ids["fantome"] = _ev(base, "conseil_municipal", "2021-10-20", CC + "a.pdf",
                         [("proces_verbal", CC + "a.pdf")])
    # 2. vraie séance communale, PV de la CC en tête ; un acte CC cite le PV
    _ev(base, "conseil_communautaire", "2021-12-08", CC + "b.pdf", [("proces_verbal", CC + "b.pdf")])
    _ev(base, "deliberation_cc", "2021-12-08", CC + "b.pdf")
    ids["mixte"] = _ev(base, "conseil_municipal", "2021-12-08", CC + "b.pdf",
                       [("proces_verbal", CC + "b.pdf"), ("compte_rendu", CM + "8-12-2021")])
    _ev(base, "deliberation", "2021-12-08", CM + "8-12-2021")
    # présences lues dans le PV de la CC, fusionnées dans la séance communale
    base.execute("UPDATE events SET metadata=json_set(metadata, '$.presents', json('[\"DÉLÉGUÉ FICTIF\"]'))"
                 " WHERE id=?", (ids["mixte"],))
    ent = base.execute("INSERT INTO entities (type, name) VALUES ('person', 'Délégué Fictif')").lastrowid
    base.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?,?,'présent')",
                 (ids["mixte"], ent))
    # 3. fantôme sans pièces (forme d'avant le 15/09)
    _ev(base, "conseil_communautaire", "2018-03-19", CC + "c.pdf", [("piece", CC + "c.pdf")])
    ids["ancien"] = _ev(base, "conseil_municipal", "2018-03-19", CC + "c.pdf")
    # témoin : deux séances le même jour, chacune ses pièces — rien à faire
    _ev(base, "conseil_communautaire", "2022-01-12", CC + "d.pdf", [("proces_verbal", CC + "d.pdf")])
    ids["temoin"] = _ev(base, "conseil_municipal", "2022-01-12", CM + "d",
                        [("compte_rendu", CM + "d")])
    base.commit()
    return base, ids


def test_le_plan(touchee):
    base, ids = touchee
    actions = {l["id"]: l["action"] for l in reprise.plan(base)}
    assert actions == {ids["fantome"]: "retirer", ids["mixte"]: "detacher",
                       ids["ancien"]: "retirer"}


def test_appliquer_puis_rien_ne_reste(touchee):
    base, ids = touchee
    reprise.appliquer(base, reprise.plan(base))
    restants = {r[0] for r in base.execute("SELECT id FROM events WHERE type='conseil_municipal'")}
    assert restants == {ids["mixte"], ids["temoin"]}
    src, meta = base.execute("SELECT source_url, metadata FROM events WHERE id=?",
                             (ids["mixte"],)).fetchone()
    assert src == CM + "8-12-2021"
    assert [p["url"] for p in json.loads(meta)["pieces"]] == [CM + "8-12-2021"]
    assert "presents" not in json.loads(meta), "présences lues dans le PV de la CC gardées"
    assert base.execute("SELECT COUNT(*) FROM event_entities WHERE event_id=?",
                        (ids["mixte"],)).fetchone()[0] == 0
    assert reprise.plan(base) == []
    assert base.execute("SELECT COUNT(*) FROM events WHERE type='conseil_communautaire'"
                        ).fetchone()[0] == 4


# ── Orphelins et provenance (08/11/2026) ─────────────────────────────────────
# Saillans : actes de la CC lus en portée commune le 29/08, sur le portail
# d'actes de la CC, sans séance communautaire qui partage leur URL.
# Brassac : séance communale déjà détachée, `source` restée au nom de la CC.
ACTES = "https://actes-epci.exemple-portail.eu/repository/2026/07/"


@pytest.fixture
def orphelins(base, monkeypatch):
    monkeypatch.setattr(reprise.config, "COMMUNE_URL", "https://www.exemple-commune.fr")
    monkeypatch.setattr(reprise.config, "EPCI_URL", "https://www.exemple-epci.fr")
    monkeypatch.setattr(reprise.config, "PAGES", {
        "epci": {"portail": "https://actes-epci.exemple-portail.eu/public/14"},
        "commune": {"conseil": "cr"}})
    # Chaque PDF du plan vérifié à la main (SIREN @ctes de la CC) : `--portail-verifie`.
    monkeypatch.setattr(reprise, "PORTAILS_VERIFIES", {"actes-epci.exemple-portail.eu"})
    ids = {}
    # la séance communautaire du jour ne sert que la page du portail
    _ev(base, "conseil_communautaire", "2026-06-25", "https://actes-epci.exemple-portail.eu/public/14",
        [("piece", "https://actes-epci.exemple-portail.eu/public/14")])
    ids["seance"] = _ev(base, "conseil_municipal", "2026-06-25", ACTES + "P1.pdf",
                        [("piece", ACTES + "P1.pdf"), ("piece", ACTES + "P2.pdf")])
    ids["actes"] = [_ev(base, "deliberation", "2026-06-25", ACTES + f"P{i}.pdf") for i in (1, 2)]
    # témoins : un acte et une séance de la commune, et un acte de la CC
    ids["acte_commune"] = _ev(base, "deliberation", "2026-05-12", CM + "12-05-2026")
    ids["seance_commune"] = _ev(base, "conseil_municipal", "2026-05-12", CM + "12-05-2026",
                                [("compte_rendu", CM + "12-05-2026")])
    _ev(base, "deliberation_cc", "2026-09-24", ACTES + "P9.pdf")
    # Brassac : le PV est celui de la commune, la source nomme encore la CC
    ids["provenance"] = _ev(base, "conseil_municipal", "2026-03-09", CM + "PV_09-MARS_2026.pdf",
                            [("proces_verbal", CM + "PV_09-MARS_2026.pdf")])
    base.execute("UPDATE events SET source='exemple-epci.fr' WHERE id=?", (ids["provenance"],))
    base.execute("UPDATE events SET source='exemple-commune.fr' WHERE id IN (?, ?)",
                 (ids["acte_commune"], ids["seance_commune"]))
    base.commit()
    return base, ids


def test_orphelins_au_plan(orphelins):
    base, ids = orphelins
    plan = reprise.plan(base)
    actions = sorted((l["action"], l["id"]) for l in plan)
    assert actions == sorted([("retirer_acte", ids["actes"][0]), ("retirer_acte", ids["actes"][1]),
                              ("retirer", ids["seance"]), ("retirer", ids["seance"]),
                              ("provenance", ids["provenance"])])


def test_orphelins_appliques_puis_rien_ne_reste(orphelins):
    base, ids = orphelins
    reprise.appliquer(base, reprise.plan(base))
    restants = {r[0] for r in base.execute("SELECT id FROM events")}
    assert ids["seance"] not in restants and not set(ids["actes"]) & restants
    assert {ids["acte_commune"], ids["seance_commune"], ids["provenance"]} <= restants
    assert base.execute("SELECT COUNT(*) FROM events WHERE type='deliberation_cc'").fetchone()[0] == 1
    assert base.execute("SELECT source FROM events WHERE id=?",
                        (ids["provenance"],)).fetchone()[0] == "exemple-commune.fr"
    assert reprise.plan(base) == []


def test_acte_cite_ailleurs_reste_a_trancher(orphelins):
    base, ids = orphelins
    base.execute("INSERT INTO annotations (object_type, object_id) VALUES ('deliberation', ?)",
                 (ids["actes"][0],))
    lignes = {l["id"]: l["action"] for l in reprise.plan(base) if l["type"] == "deliberation"}
    assert lignes[ids["actes"][0]] == "?" and lignes[ids["actes"][1]] == "retirer_acte"


def test_detacher_recalcule_la_source(touchee):
    """Le cas de #224 : la pièce de la CC sort, la source suit la pièce restante."""
    base, ids = touchee
    base.execute("UPDATE events SET source='exemple-epci.fr' WHERE id=?", (ids["mixte"],))
    reprise.appliquer(base, reprise.plan(base))
    assert base.execute("SELECT source FROM events WHERE id=?",
                        (ids["mixte"],)).fetchone()[0] == "exemple-commune.fr"


def test_portail_non_verifie_reste_a_trancher(orphelins, monkeypatch):
    """Un portail peut être mutualisé avec les communes : le domaine seul ne suffit pas."""
    base, ids = orphelins
    monkeypatch.setattr(reprise, "PORTAILS_VERIFIES", set())
    actions = {(l["action"], l["id"]) for l in reprise.plan(base)}
    assert ("?", ids["actes"][0]) in actions and ("?", ids["seance"]) in actions
    assert not {a for a, _ in actions} & {"retirer_acte", "retirer", "detacher"}


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["detacher_pieces_autre_assemblee.py", *map(str, argv)])
    return reprise.main()


def test_appliquer_exige_le_plan_relu_et_lui_seul(orphelins, tmp_path, monkeypatch):
    base, ids = orphelins
    db, sim = tmp_path / "test.db", tmp_path / "sim.tsv"
    portail = ("--portail-verifie", "actes-epci.exemple-portail.eu")
    assert _main(monkeypatch, "--db", db, "--tsv", sim, *portail) == 0
    # sans plan relu : refus
    assert _main(monkeypatch, "--db", db, "--tsv", tmp_path / "a.tsv", "--appliquer", *portail) == 2
    # un acte né après la simulation : le plan diffère, rien n'est écrit
    _ev(base, "deliberation", "2026-06-25", ACTES + "P3.pdf")
    base.commit()
    avant = base.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert _main(monkeypatch, "--db", db, "--tsv", tmp_path / "b.tsv", "--appliquer",
                 "--plan-relu", sim, *portail) == 1
    assert base.execute("SELECT COUNT(*) FROM events").fetchone()[0] == avant
    # resimulé et relu : appliqué
    assert _main(monkeypatch, "--db", db, "--tsv", sim, *portail) == 0
    assert _main(monkeypatch, "--db", db, "--tsv", tmp_path / "c.tsv", "--appliquer",
                 "--plan-relu", sim, *portail) == 0
    assert base.execute("SELECT COUNT(*) FROM events WHERE id=?", (ids["seance"],)).fetchone()[0] == 0
