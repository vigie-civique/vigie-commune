"""Entreprises individuelles non publiées : ni nom ni identifiant dans les flux,
les marchés et `/finances`, et l'argent public qu'elles touchent reste compté.

Brief `ei-flux-marches` (24/11/2026). Leçon de #317 : la retenue
(`ids_retenus_par_un_fait`) et la sortie (`etape_flux`, `etape_finances`)
divergent dès qu'elles décident séparément. Ces essais passent donc par les
ÉTAPES réelles, sur une base au schéma réel, jusqu'aux paquets `entite/<id>.json`
et au contrôleur (`check_ei_sans_fait`, `check_noms_d_ei_ecartees`).
Les pièges du snapshot complet sont dans `tests/test_pieges.py`.
"""
from __future__ import annotations

import importlib.util
import os
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def vs():
    spec = importlib.util.spec_from_file_location(
        "verify_snapshot", ROOT / "scripts" / "verify_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chaine(conn, out: Path, monkeypatch, revue=None) -> dict:
    """Les étapes du snapshot dans l'ordre d'`etapes.py`, de `personnes_publiques`
    à `controle_ei`, et les fichiers que le contrôleur lit."""
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes, etape_liens_actes
    from scripts.snapshot.argent import etape_finances, etape_flux
    from scripts.snapshot.personnes import etape_personnes_publiques
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    monkeypatch.setenv("VIGIE_AUDITS", str(out.parent / "audits"))
    revue = revue or {}
    exclusions = defaultdict(Counter)
    pers = etape_personnes_publiques(conn, "[]")
    actes = etape_actes(conn, revue, pers["noms_publics"], pers["redige"], exclusions)
    f = fiches.etape_fiches(conn, revue, pers["public_person_ids"],
                            pers["ids_conseil_communautaire"], exclusions,
                            actes["public_events"], actes["actes_de_rubrique"],
                            redige=pers["redige"])
    liens = etape_liens_actes(conn, f["public_ids"], f["public_entities"],
                              actes["public_events"], exclusions, f["ei_ids"])
    flux = etape_flux(conn, revue, f["entity_rows"], pers["public_person_ids"],
                      f["public_ids"], f["ecartees_du_perimetre"], pers["redige"],
                      exclusions, actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    marches = etape_finances(conn, revue, f["public_ids"], f["public_entities"],
                             liens["perimetre_par_entite"], pers["noms_publics"],
                             exclusions, ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["marches_data"]
    out.mkdir(parents=True, exist_ok=True)
    fiches.write_entity_bundles(out, f["public_entities"], [], actes["public_events"],
                                liens["public_links"], flux, marches)
    stats = {"generated_at": "2026-11-24T12:00:00"}
    for nom, cle, valeur in (("entities.json", "entities", f["public_entities"]),
                             ("flows.json", "flows", flux), ("marches.json", "marches", marches),
                             ("events.json", "events", actes["public_events"])):
        (out / nom).write_text(json.dumps({cle: valeur}, ensure_ascii=False, default=str))
    (out / "stats.json").write_text(json.dumps(stats))
    # `actualite.json` aussi : il recopie titres et acteurs des flux et des marchés.
    from scripts.snapshot.actualite import etape_actualite
    etape_actualite(out, stats, marches, actes["public_events"], flux,
                    liens["perimetre_par_entite"], exclusions)
    fiches.etape_controle_ei(conn, out, revue, stats, f["entity_rows"], f["ei_ids"],
                             f["public_ids"],
                             f["ei_nommees"])
    return {"fiches": f, "flux": flux, "marches": marches}


def _controle(vs, out: Path) -> dict:
    rep = vs.Report()
    controle = vs.lire_controle_ei(out, rep)
    assert controle is not None, rep.warnings
    vs.check_ei_sans_fait(out, rep, controle)
    vs.check_noms_d_ei_ecartees(out, rep, controle)
    return rep.errors


# ── Sur la base fictive (`scripts/donnees_synthetiques.py`) ─────────────────

@pytest.fixture
def synthetique(tmp_path):
    chemin = tmp_path / "ei-synth.db"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "donnees_synthetiques.py"),
                        str(chemin)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    from scripts.donnees_synthetiques import EI_FICTIVES  # noqa: F401 — la liste existe
    ei = {}
    for (eid, nom, forme, perimetre) in conn.execute(
            "SELECT e.id, e.name, b.legal_form_code, e.perimetre FROM entities e"
            " JOIN businesses b ON b.entity_id = e.id"
            " WHERE e.name IN (SELECT name FROM entities WHERE type = 'person')"
            "    OR b.legal_form_code = '1000'"):
        ei[eid] = {"nom": nom, "forme": forme, "perimetre": perimetre}
    yield conn, ei
    conn.close()


def test_la_base_fictive_porte_les_cinq_ei(synthetique):
    conn, ei = synthetique
    assert len(ei) == 5
    assert sum(1 for e in ei.values() if e["forme"] is None) == 1
    assert sum(1 for e in ei.values() if e["perimetre"] == "C2") == 1


def test_de_bout_en_bout_sur_la_base_fictive(synthetique, tmp_path, monkeypatch, vs):
    """Deux EI retenues (marché, subvention de la mairie) ; trois non publiées
    (sans fait, C2, sans forme juridique) dont aucun nom ne sort ; leurs flux avec
    la mairie restent comptés, celui de l'association est écarté ; le contrôleur
    ne trouve rien."""
    from scripts.snapshot.fiches import ei_presumees
    conn, ei = synthetique
    sortie = _chaine(conn, tmp_path / "snap", monkeypatch)
    f = sortie["fiches"]
    publiees = {eid for eid in ei if eid in f["public_ids"]}
    assert len(publiees) == 2
    assert ei_presumees(conn, set()) == {eid for eid, e in ei.items() if e["forme"] is None}
    ecartees = set(ei) - publiees
    noms_ecartes = {ei[eid]["nom"] for eid in ecartees}
    texte = (json.dumps(sortie["flux"], ensure_ascii=False, default=str)
             + json.dumps(sortie["marches"], ensure_ascii=False, default=str))
    assert not [n for n in noms_ecartes if n in texte]
    montants = Counter(fl["amount"] for fl in sortie["flux"] if fl["year"] == 2025)
    # Loyers (600, 450) et subvention à l'EI C2 (900) : gardés, déliés.
    assert montants[600] and montants[450] and montants[900]
    assert not montants[300], "aide d'une association à une EI écartée : publiée"
    marche_c2 = next(m for m in sortie["marches"] if m["montant"] == 8_000)
    assert marche_c2["titulaire_id"] is None and marche_c2["titulaire_nom"] is None
    assert marche_c2["objet"] and marche_c2["acheteur_nom"]
    assert _controle(vs, tmp_path / "snap") == {}


# ── Sur la base d'essai (`conftest.base`) : un cas par règle ────────────────

def _ei(base, entite, nom, perimetre="C1", forme="1000", siren=None):
    eid = entite(nom, perimetre=perimetre)
    base.execute("INSERT INTO businesses (entity_id, siren, legal_form_code, status)"
                 " VALUES (?, ?, ?, 'A')", (eid, siren, forme))
    return eid


def _syndicat(base, entite):
    eid = entite("Syndicat des eaux", "service", perimetre="C1")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '7355')", (eid,))
    return eid


def _flux(base, de, vers, montant, type_="subvention"):
    base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                 " description) VALUES (?, 2025, ?, ?, ?, 'verified', 'Versement')",
                 (type_, montant, de, vers))


def _marche(base, titulaire, nom, montant, siren=None):
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " titulaire_siren, titulaire_nom, objet, montant, source, confidence)"
                 " VALUES ('1', 'Syndicat des eaux', ?, ?, ?, 'Travaux', ?, 'DECP', 'verified')",
                 (titulaire, siren, nom, montant))


@pytest.mark.parametrize("cas", ["C2", "rejetee", "sans_fait_siren"])
def test_le_marche_d_une_ei_non_publiee_garde_son_montant_et_perd_son_nom(
        base, entite, tmp_path, monkeypatch, vs, cas):
    """Le titulaire est une EI non publiée (C2, rejetée à l'atelier, ou désignée
    par le seul SIREN d'un marché que rien ne rattache) : `titulaire_nom` tombe
    avec le lien ; l'objet, le montant et l'acheteur restent."""
    _syndicat(base, entite)
    revue = {}
    if cas == "C2":
        ei = _ei(base, entite, "Jeanne VOISINE", perimetre="C2")
        _marche(base, ei, "Jeanne VOISINE", 5000)
    elif cas == "rejetee":
        ei = _ei(base, entite, "Jeanne REJETEE")
        _marche(base, ei, "JEANNE REJETEE", 5000)
        revue = {"entity": {ei: {"statut": "ecarte"}}}
    else:
        _ei(base, entite, "Jeanne SANSFAIT", siren="999000123")
        _marche(base, None, "SANSFAIT Jeanne", 5000, siren="999 000 123")
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch, revue)
    m = next(m for m in sortie["marches"] if m["montant"] == 5000)
    assert m["titulaire_id"] is None and m["titulaire_nom"] is None
    assert m["objet"] == "Travaux" and m["acheteur_nom"]
    assert "titulaire_siren" not in m
    assert _controle(vs, tmp_path / "snap") == {}


def test_le_marche_d_une_ei_publiee_garde_son_nom(base, entite, tmp_path, monkeypatch, vs):
    _syndicat(base, entite)
    ei = _ei(base, entite, "Jeanne TITULAIRE")
    _marche(base, ei, "Jeanne TITULAIRE", 5000)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    assert ei in sortie["fiches"]["public_ids"]
    m = next(m for m in sortie["marches"] if m["montant"] == 5000)
    assert m["titulaire_id"] == ei and m["titulaire_nom"] == "Jeanne TITULAIRE"
    assert _controle(vs, tmp_path / "snap") == {}


def test_une_ei_sans_fiche_ne_sort_qu_avec_une_partie_publique(base, entite, tmp_path,
                                                               monkeypatch, vs):
    """EI sans fait public : son loyer au syndicat sort délié (montant gardé) ;
    l'aide d'une association, elle, est écartée (elle sortait déliée avant)."""
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    ei = _ei(base, entite, "Jeanne LOCATAIRE")
    _flux(base, ei, syndicat, 640, "bail")
    _flux(base, association, ei, 320)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    assert ei not in sortie["fiches"]["public_ids"]
    assert [fl["amount"] for fl in sortie["flux"]] == [640]
    fl = sortie["flux"][0]
    assert fl["from_id"] is None and fl["from_name"] is None and fl["description"] is None
    assert fl["to_id"] == syndicat
    assert _controle(vs, tmp_path / "snap") == {}


def test_une_ei_sans_forme_juridique_suit_la_regle_des_ei(base, entite, tmp_path,
                                                          monkeypatch, vs):
    """Forme inconnue, exploitant au même nom : présumée EI. Sans fait, ni fiche,
    ni nom (son flux d'une partie publique sort délié). Avec un marché : publiée,
    `nature = individuelle`, domicile masqué. Une société au nom de son dirigeant
    (forme 5499) reste une société."""
    syndicat = _syndicat(base, entite)
    personnes = {}
    for nom in ("Jeanne PRESUMEE", "Jeanne RETENUE", "Jeanne SOCIETE"):
        personnes[nom] = entite(nom, "person", perimetre="C1")
    presumee = _ei(base, entite, "JEANNE PRÉSUMÉE", forme=None)
    retenue = _ei(base, entite, "Jeanne Retenue", forme="")
    societe = _ei(base, entite, "Jeanne SOCIETE", forme="5499")
    base.execute("UPDATE entities SET lat = 44.0, lng = 3.9 WHERE id = ?", (retenue,))
    for nom, eid in (("Jeanne PRESUMEE", presumee), ("Jeanne RETENUE", retenue),
                     ("Jeanne SOCIETE", societe)):
        base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                     " VALUES (?, ?, 'dirigeant', 'verified')", (personnes[nom], eid))
    _flux(base, syndicat, presumee, 910)
    base.execute("UPDATE financial_flows SET description = 'Aide à un particulier'"
                 " WHERE amount = 910")
    _marche(base, retenue, "Jeanne Retenue", 7000)
    base.commit()
    from scripts.snapshot.fiches import ei_presumees
    assert ei_presumees(base, set()) == {presumee, retenue}
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    f = sortie["fiches"]
    assert presumee not in f["public_ids"] and {retenue, societe} <= f["public_ids"]
    motifs = {x["id"]: x["reasons"] for x in f["entity_exclusions"]}
    assert motifs[presumee] == ["ei_sans_fait_public"]
    fiche = next(e for e in f["public_entities"] if e["id"] == retenue)
    assert fiche["nature"] == "individuelle" and fiche["lat"] is None
    assert next(e for e in f["public_entities"] if e["id"] == societe)["nature"] != "individuelle"
    fl = next(fl for fl in sortie["flux"] if fl["amount"] == 910)
    assert fl["to_id"] is None and fl["to_name"] is None
    assert _controle(vs, tmp_path / "snap") == {}


def test_l_exploitant_d_une_ei_presumee_ne_devient_pas_publiable(base, entite):
    """La règle de pertinence (dirige une structure payée) ne vaut pas pour une
    EI, forme 1000 ou présumée : son exploitant resterait nommé par sa fiche."""
    from scripts.snapshot.personnes import etape_personnes_publiques
    syndicat = _syndicat(base, entite)
    personne = entite("Jeanne PRESUMEE", "person", perimetre="C1")
    presumee = _ei(base, entite, "Jeanne PRESUMEE", forme=None)
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'verified')", (personne, presumee))
    _flux(base, syndicat, presumee, 910)
    base.commit()
    assert personne not in etape_personnes_publiques(base, "[]")["public_person_ids"]


# ── Le contrôleur, seul ─────────────────────────────────────────────────────

def _snapshot_a_la_main(tmp_path, monkeypatch, flows=(), marches=(), entities=(),
                        controle=None, generated_at="2026-11-24T12:00:00"):
    out = tmp_path / "snap"
    (out / "entite").mkdir(parents=True)
    for nom, cle, valeur in (("entities.json", "entities", list(entities)),
                             ("flows.json", "flows", list(flows)),
                             ("marches.json", "marches", list(marches))):
        (out / nom).write_text(json.dumps({cle: valeur}))
    (out / "stats.json").write_text(json.dumps({"generated_at": generated_at}))
    audits = tmp_path / "audits"
    audits.mkdir()
    if controle is not None:
        # Le relevé de CE snapshot (son empreinte) si `generated_at` concorde,
        # sinon celui d'un autre.
        from scripts.snapshot.fiches import empreinte_snapshot
        cle = (empreinte_snapshot(out) if controle.pop("generated_at") == generated_at
               else "0" * 64)
        (audits / "controle_ei.json").write_text(json.dumps({"releves": {cle: controle}}))
    monkeypatch.setenv("VIGIE_AUDITS", str(audits))
    for e in entities:
        (out / "entite" / f"{e['id']}.json").write_text(json.dumps(
            {"entity": e, "flows": [fl for fl in flows if e["id"] in (fl.get("from_id"),
                                                                     fl.get("to_id"))],
             "marches": [], "liens": []}))
    return out


def test_un_flux_venu_d_une_association_ne_retient_pas_l_ei(vs, tmp_path, monkeypatch):
    out = _snapshot_a_la_main(
        tmp_path, monkeypatch,
        entities=[{"id": 1, "type": "business", "nature": "individuelle"},
                  {"id": 2, "type": "association"}, {"id": 3, "type": "service"}],
        flows=[{"id": 1, "from_id": 2, "to_id": 1, "etat": "paye"}],
        controle={"generated_at": "2026-11-24T12:00:00", "parties_publiques": [3],
                  "ei_ecartees": []})
    rep = vs.Report()
    vs.check_ei_sans_fait(out, rep, vs.lire_controle_ei(out, rep))
    assert "entreprise individuelle publiée sans marché, flux ni acte qui la désigne" in rep.errors


def test_un_flux_venu_d_une_partie_publique_retient_l_ei(vs, tmp_path, monkeypatch):
    out = _snapshot_a_la_main(
        tmp_path, monkeypatch,
        entities=[{"id": 1, "type": "business", "nature": "individuelle"},
                  {"id": 3, "type": "service"}],
        flows=[{"id": 1, "from_id": 3, "to_id": 1, "etat": "paye"}],
        controle={"generated_at": "2026-11-24T12:00:00", "parties_publiques": [3],
                  "ei_ecartees": []})
    rep = vs.Report()
    vs.check_ei_sans_fait(out, rep, vs.lire_controle_ei(out, rep))
    assert rep.errors == {} and rep.warnings == {}


def test_un_snapshot_sans_son_releve_est_refuse(vs, tmp_path, monkeypatch):
    """Le relevé ne porte que d'autres snapshots : ERREUR, le filet ne se coupe
    pas sans bruit (avis vigie-securite du 25/11/2026)."""
    out = _snapshot_a_la_main(
        tmp_path, monkeypatch, entities=[{"id": 3, "type": "service"}],
        controle={"generated_at": "2026-01-01T00:00:00", "parties_publiques": [],
                  "ei_ecartees": []})
    rep = vs.Report()
    assert vs.lire_controle_ei(out, rep) is None
    assert list(rep.errors) == ["relevé des entreprises individuelles absent pour ce snapshot"]
    # La cause est dite (relecture de code du 25/11/2026).
    cas = str(rep.errors)
    assert "aucun relevé pour l'empreinte" in cas and "purgé" in cas


def test_deux_constructions_dans_la_meme_seconde_gardent_chacune_leur_releve(
        base, tmp_path, monkeypatch, vs):
    """Même `generated_at`, contenus différents : deux relevés, chacun retrouvé
    par le contrôleur sur son snapshot (clé = empreinte du contenu)."""
    from scripts.snapshot.fiches import etape_controle_ei
    monkeypatch.setenv("VIGIE_AUDITS", str(tmp_path / "audits"))
    dossiers = []
    for i in range(2):
        out = tmp_path / f"snap{i}"
        out.mkdir()
        (out / "entities.json").write_text(json.dumps({"entities": [{"id": i}]}))
        (out / "stats.json").write_text(json.dumps({"generated_at": "2026-11-24T10:00:00"}))
        etape_controle_ei(base, out, {}, {"generated_at": "2026-11-24T10:00:00"}, [], set(),
                          set(), set())
        dossiers.append(out)
    releves = json.loads((tmp_path / "audits" / "controle_ei.json").read_text())["releves"]
    assert len(releves) == 2
    for out in dossiers:
        rep = vs.Report()
        assert vs.lire_controle_ei(out, rep) is not None and not rep.errors


@pytest.mark.parametrize("age_jours,gardes", [(1, 26), (60, 20)])
def test_la_retention_des_releves(base, tmp_path, monkeypatch, age_jours, gardes):
    """Moins de 30 jours : tous gardés. Plus vieux : les 20 plus récents."""
    from datetime import datetime, timedelta
    from scripts.snapshot.fiches import etape_controle_ei
    audits = tmp_path / "audits"
    audits.mkdir()
    monkeypatch.setenv("VIGIE_AUDITS", str(audits))
    vieux = (datetime.now() - timedelta(days=age_jours)).isoformat(timespec="seconds")
    (audits / "controle_ei.json").write_text(json.dumps({"releves": {
        f"{i:064x}": {"construit_le": vieux, "parties_publiques": [], "ei_ecartees": []}
        for i in range(25)}}))
    out = tmp_path / "snap"
    out.mkdir()
    etape_controle_ei(base, out, {}, {"generated_at": "x"}, [], set(), set(), set())
    assert len(json.loads((audits / "controle_ei.json").read_text())["releves"]) == gardes


def test_un_nom_d_ei_ecartee_lie_a_une_fiche_publiee_passe(vs, tmp_path, monkeypatch):
    """Homonymie : le nom d'une EI écartée porté par une fiche PUBLIÉE (son
    identifiant est là) désigne cette fiche, pas l'EI."""
    from scripts.snapshot.fiches import cle_de_nom
    controle = {"generated_at": "2026-11-24T12:00:00", "parties_publiques": [],
                "ei_ecartees": [cle_de_nom("Jeanne HOMONYME")]}
    out = _snapshot_a_la_main(
        tmp_path, monkeypatch, entities=[{"id": 4, "type": "person"}],
        flows=[{"id": 1, "from_id": None, "from_name": "Syndicat", "to_id": 4,
                "to_name": "Jeanne HOMONYME"}],
        marches=[{"id": 2, "titulaire_id": None, "titulaire_nom": "jeanne  homonyme"}],
        controle=controle)
    rep = vs.Report()
    vs.check_noms_d_ei_ecartees(out, rep, vs.lire_controle_ei(out, rep))
    assert list(rep.errors) == ["nom d'une entreprise individuelle non publiée"]
    assert len(rep.errors["nom d'une entreprise individuelle non publiée"]) == 1


def test_l_empreinte_du_controleur_est_celle_du_builder(vs, tmp_path):
    from scripts.snapshot import fiches
    (tmp_path / "entities.json").write_text('{"entities": []}')
    assert vs.empreinte_snapshot(tmp_path) == fiches.empreinte_snapshot(tmp_path)
    assert vs.FICHIERS_EMPREINTE == fiches.FICHIERS_EMPREINTE
    assert (vs.RETENTION_RELEVES_JOURS, vs.RELEVES_EI_MINIMUM) == (
        fiches.RETENTION_RELEVES_JOURS, fiches.RELEVES_EI_MINIMUM)


def test_la_cle_de_nom_du_controleur_est_celle_du_builder(vs):
    from scripts.snapshot.fiches import cle_de_nom
    for nom in ("Jeanne EXEMPLE", "  jeanne   exemple ", "JEANNE-ÉLODIE Exémple", "", None):
        assert vs.cle_de_nom(nom) == cle_de_nom(nom)
    assert cle_de_nom("Jeanne-Élodie EXEMPLE") == cle_de_nom("jeanne elodie exemple")


# ── Audit du filtre du 24/11/2026, P4 : la forme 1000 quel que soit le type ──

@pytest.mark.parametrize("type_", ["service", "place"])
def test_une_ei_typee_service_ou_lieu_perd_sa_position(monkeypatch, type_):
    """La bbox de la CI est nulle (toute position y est masquée) : on en pose une
    vraie, sinon l'essai ne prouverait rien. Témoin : la même fiche sans forme
    1000 garde sa position."""
    from scripts.snapshot import fiches
    regles = {**fiches.RULES, "locations": {
        **fiches.RULES["locations"],
        "bbox": {"lat_min": 44, "lat_max": 45, "lng_min": 3, "lng_max": 4},
        "center_fallback_box": {"lat_min": 0, "lat_max": 0, "lng_min": 0, "lng_max": 0}}}
    monkeypatch.setattr(fiches, "RULES", regles)
    ligne = {"id": 5, "type": type_, "name": "Cabinet Exemple", "confidence": "verified",
             "perimetre": "C1", "lat": 44.5, "lng": 3.5}
    temoin, _ = fiches.public_entity(dict(ligne), [], set())
    assert temoin["lat"] == 44.5 and "nature" not in temoin
    assert fiches.public_entity({**ligne, "legal_form_code": "1000"}, [], set()) == (
        None, ["ei_sans_fait_public"])
    ei, _ = fiches.public_entity({**ligne, "legal_form_code": "1000"}, [], set(),
                                 ids_retenus={5})
    assert ei["lat"] is None and ei["lng"] is None and not ei["has_public_location"]
    assert ei["location_quality"] == "hidden_ei_domicile" and ei["nature"] == "individuelle"


def test_une_note_de_revue_un_particulier_ne_retient_pas(base, entite, tmp_path,
                                                         monkeypatch, vs):
    """Audit P1 : le seul flux d'une EI, rectifié à l'atelier avec la note
    « versée à un particulier ». La note sort, délie le flux : l'EI n'est pas
    retenue, le flux sort délié avec son montant, le contrôle passe."""
    syndicat = _syndicat(base, entite)
    ei = _ei(base, entite, "Jeanne NOTEE")
    _flux(base, syndicat, ei, 555)
    flux_id = base.execute("SELECT id FROM financial_flows WHERE amount = 555").fetchone()[0]
    base.commit()
    revue = {"flow": {flux_id: {"statut": "retenu", "corrections": {"amount": 556},
                                "confidence": None, "note": "versée à un particulier"}}}
    sortie = _chaine(base, tmp_path / "snap", monkeypatch, revue)
    assert ei not in sortie["fiches"]["public_ids"]
    fl = next(fl for fl in sortie["flux"] if fl["amount"] == 556)
    assert fl["to_id"] is None and fl["to_name"] is None
    assert _controle(vs, tmp_path / "snap") == {}


# ── Avis vigie-securite et vigie-dpo du 25/11/2026 ──────────────────────────

def test_un_marche_au_seul_nom_d_une_ei_ecartee_perd_ce_nom(base, entite, tmp_path,
                                                            monkeypatch, vs):
    """Ni identifiant ni SIREN, le nom seul (graphie différente) : effacé comme
    le contrôleur l'aurait refusé ; le marché reste attribué (`titulaire_masque`)."""
    _syndicat(base, entite)
    _ei(base, entite, "Jeanne Sanslien")
    _marche(base, None, "JEANNE  SANSLIEN", 5000)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    m = next(m for m in sortie["marches"] if m["montant"] == 5000)
    assert m["titulaire_nom"] is None and m["titulaire_masque"] is True
    assert _controle(vs, tmp_path / "snap") == {}


def test_le_nom_d_une_ei_ecartee_sort_du_texte_libre(base, entite, tmp_path, monkeypatch, vs):
    """Objet et note du marché, description d'un autre flux : le nom devient
    « [entreprise individuelle] » ; la note d'un marché masqué tombe."""
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    ei = _ei(base, entite, "Jeanne Objet", perimetre="C2")
    _marche(base, ei, "Jeanne Objet", 5000)
    base.execute("UPDATE marches_publics SET objet = 'Plomberie confiée à JEANNE-OBJET'")
    base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                 " description) VALUES ('subvention', 2025, 70, ?, ?, 'verified',"
                 " 'Aide au chantier de Jeanne Objet')", (syndicat, association))
    marche_id = base.execute("SELECT id FROM marches_publics").fetchone()[0]
    base.commit()
    revue = {"marche": {marche_id: {"statut": "retenu", "corrections": {"montant": 5001},
                                    "confidence": None, "note": "vu avec Jeanne Objet"}}}
    sortie = _chaine(base, tmp_path / "snap", monkeypatch, revue)
    m = next(m for m in sortie["marches"] if m["montant"] == 5001)
    assert m["objet"] == "Plomberie confiée à [entreprise individuelle]"
    assert "note_revue" not in m
    fl = next(fl for fl in sortie["flux"] if fl["amount"] == 70)
    assert fl["description"] == "Aide au chantier de [entreprise individuelle]"
    assert "Objet" not in json.dumps(sortie["marches"] + sortie["flux"], default=str)


@pytest.mark.parametrize("forme,ei", [("1000", True), ("1300", True), ("1900", True),
                                      ("5499", False), ("7210", False), ("10", False)])
def test_les_formes_de_personne_physique_sont_des_ei(forme, ei):
    """Les catégories INSEE 1100 à 1900, supprimées au 01/07/2018, désignent
    encore des personnes physiques dans les unités anciennes (avis vigie-dpo)."""
    from scripts.snapshot.fiches import est_ei
    assert est_ei({"legal_form_code": forme}) is ei


def test_une_personne_avec_une_forme_1000_reste_publiee(monkeypatch):
    """Une ligne `businesses` égarée sur la fiche d'un élu ne l'efface pas."""
    from scripts.snapshot.fiches import public_entity
    elu = {"id": 9, "type": "person", "name": "Jeanne ÉLUE", "confidence": "verified",
           "perimetre": "C1", "legal_form_code": "1000"}
    publiee, _ = public_entity(elu, [], {9})
    assert publiee is not None and "nature" not in publiee


@pytest.mark.parametrize("personne,entreprise,presumee", [
    ("Jean DUPONT", "DUPONT JEAN", True),
    ("Jeanne Marie DUPONT", "JEANNE DUPONT", True),
    ("Jean DUPONT", "Boulangerie DUPONT", False),
    ("Jean DUPONT", "DUPONT", False)])
def test_la_presomption_ignore_l_ordre_et_les_prenoms_en_plus(base, entite, personne,
                                                              entreprise, presumee):
    from scripts.snapshot.fiches import ei_presumees
    p = entite(personne, "person")
    b = _ei(base, entite, entreprise, forme=None)
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'verified')", (p, b))
    assert (b in ei_presumees(base, set())) is presumee


# ── Décisions de Julien du 25/11/2026 et relecture de code ──────────────────

def _commune_c1(base):
    from collectors.config import COMMUNE_NAME
    from collectors.nom_normalise import normaliser
    nom = f"Commune de {COMMUNE_NAME}"
    return base.execute(
        "INSERT INTO entities (type, name, name_norm, commune, perimetre, confidence)"
        " VALUES ('service', ?, ?, ?, 'C1', 'verified')",
        (nom, normaliser(nom), COMMUNE_NAME)).lastrowid


def test_une_ei_c2_designee_par_la_commune_garde_son_nom(base, entite, tmp_path,
                                                         monkeypatch, vs):
    """Q2/B3 : marché dont la commune est l'acheteur, versement de la commune :
    le nom de l'EI C2 reste, sans lien. Sur le marché du syndicat, il tombe.
    Une EI C2 rejetée à l'atelier, même désignée par la commune : effacée."""
    commune = _commune_c1(base)
    syndicat = _syndicat(base, entite)
    nommee = _ei(base, entite, "Jeanne VOISINE", perimetre="C2")
    rejetee = _ei(base, entite, "Jeanne REJETEE", perimetre="C2")
    for ei, nom in ((nommee, "Jeanne VOISINE"), (rejetee, "Jeanne REJETEE")):
        base.execute("INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom,"
                     " titulaire_id, titulaire_nom, objet, montant, source, confidence)"
                     " VALUES (?, '1', 'Commune', ?, ?, 'Travaux', ?, 'DECP', 'verified')",
                     (commune, ei, nom, 5000 if ei == nommee else 5100))
    _marche(base, nommee, "Jeanne VOISINE", 6000)          # acheteur : le syndicat
    _flux(base, commune, nommee, 800)
    _flux(base, syndicat, nommee, 810)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch,
                     {"entity": {rejetee: {"statut": "ecarte"}}})
    f = sortie["fiches"]
    assert f["ei_nommees"] == {nommee}
    assert nommee not in f["public_ids"]
    par_montant = {m["montant"]: m for m in sortie["marches"]}
    assert par_montant[5000]["titulaire_nom"] == "Jeanne VOISINE"
    assert par_montant[5000]["titulaire_id"] is None
    assert par_montant[6000]["titulaire_nom"] is None
    assert par_montant[5100]["titulaire_nom"] is None
    flux = {fl["amount"]: fl for fl in sortie["flux"]}
    assert flux[800]["to_name"] == "Jeanne VOISINE" and flux[800]["to_id"] is None
    assert flux[810]["to_name"] is None
    assert _controle(vs, tmp_path / "snap") == {}


@pytest.mark.parametrize("statuts", [("demande",), ("annule",), ("vote", "demande")])
def test_une_ei_c2_que_la_commune_designe_par_une_demande_perd_son_nom(
        base, entite, tmp_path, monkeypatch, vs, statuts):
    """É0, H3 (relecture de #323) : changement de sortie VOULU. L'EI C2 que la
    commune ne désigne que par un flux demandé ou annulé, sans marché de la
    commune, n'est plus dans `ei_nommees` : le flux sort délié, sans nom ni
    description. Sur `main`, il gardait le nom (essai précédent). Même effet
    pour un flux voté qui a un jumeau demandé (`jumeaux_masques`)."""
    commune = _commune_c1(base)
    nommee = _ei(base, entite, "Jeanne VOISINE", perimetre="C2")
    for statut in statuts:
        _flux(base, commune, nommee, 800)
        base.execute("UPDATE financial_flows SET statut = ? WHERE id = last_insert_rowid()",
                     (statut,))
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    assert sortie["fiches"]["ei_nommees"] == set()
    flux = [fl for fl in sortie["flux"] if fl["amount"] == 800]
    assert len(flux) == 1
    assert flux[0]["to_name"] is None and flux[0]["to_id"] is None
    assert flux[0]["description"] is None
    assert "VOISINE" not in json.dumps(sortie["flux"] + sortie["marches"], default=str)
    assert _controle(vs, tmp_path / "snap") == {}


def test_une_fiche_c3_sans_forme_est_une_partie_publique(base, entite, tmp_path,
                                                         monkeypatch, vs):
    """Décisions de Julien du 25/11/2026 : un C3 est partie publique s'il est de
    type `service` et de forme 7xxx, 4xxx ou sans forme. Le département (service
    sans forme) compte ; une personne, une association, un `business` sans forme
    ou une société classés C3 non."""
    from scripts.snapshot.fiches import parties_publiques
    departement = entite("Conseil départemental", "service", perimetre="C3")
    prefecture = entite("Préfecture", "service", perimetre="C3")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '7172')",
                 (prefecture,))
    personne = entite("Jeanne PREFETE", "person", perimetre="C3")
    association = entite("Fédération départementale de pêche", "association", perimetre="C3")
    sans_forme = entite("Département Bois", perimetre="C3")
    societe = entite("Hôtel du Département", perimetre="C3")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '5499')",
                 (societe,))
    publiques = parties_publiques(base)
    assert {departement, prefecture} <= publiques
    assert not {personne, association, sans_forme, societe} & publiques


def test_la_presomption_lit_tout_lien_sauf_un_lien_rejete(base, entite):
    """Tout lien fait présumer l'EI (`gérant`, `président`, `responsable` des
    collecteurs web : avis vigie-securite du 25/11/2026), sauf un lien que
    l'atelier a rejeté."""
    from scripts.snapshot.fiches import ei_presumees
    ids = {}
    for cas, type_ in (("dirigeant", "dirigeant"), ("gerant", "gérant"),
                       ("responsable", "responsable"), ("rejete", "dirigeant")):
        p = entite(f"Jeanne {cas.upper()}", "person")
        b = _ei(base, entite, f"JEANNE {cas.upper()}", forme=None)
        ids[cas] = (b, base.execute(
            "INSERT INTO relations (from_id, to_id, relation_type, confidence)"
            " VALUES (?, ?, ?, 'probable')", (p, b, type_)).lastrowid)
    assert ei_presumees(base, {ids["rejete"][1]}) == {
        ids[c][0] for c in ("dirigeant", "gerant", "responsable")}


def test_le_nom_d_une_fiche_publiee_n_est_pas_masque(base, entite, tmp_path, monkeypatch, vs):
    """Une EI écartée porte le nom d'une société publiée : le texte libre qui
    nomme la société n'est pas masqué (relecture de code du 25/11/2026)."""
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    _ei(base, entite, "Garage Central")                     # EI sans fait
    societe = _ei(base, entite, "GARAGE CENTRAL", perimetre="C1", forme="5499")
    _marche(base, societe, "GARAGE CENTRAL", 3000)
    base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                 " description) VALUES ('subvention', 2025, 71, ?, ?, 'verified',"
                 " 'Réparation au Garage Central')", (syndicat, association))
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    assert next(fl for fl in sortie["flux"] if fl["amount"] == 71)["description"] == \
        "Réparation au Garage Central"
    assert next(m for m in sortie["marches"] if m["montant"] == 3000)["titulaire_nom"] == \
        "GARAGE CENTRAL"
    assert _controle(vs, tmp_path / "snap") == {}


# ── Second avis vigie-securite du 25/11/2026 : A1, A2, A3 ───────────────────

def test_une_ei_nommee_par_la_commune_perd_son_nom_ailleurs(base, entite, tmp_path,
                                                            monkeypatch, vs):
    """A1 : le nom d'une EI C2 que la commune désigne ne reste QUE sur le marché
    communal qui la désigne par identifiant. Marché du syndicat ou de la commune
    rattaché par le seul SIREN, marché au seul nom : effacé. A2 : son nom sort
    aussi du texte libre ailleurs. Le contrôleur, réarmé, refuse le nom remis."""
    commune = _commune_c1(base)
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    ei = _ei(base, entite, "Jeanne VOISINE", perimetre="C2", siren="999000321")
    base.execute("INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom,"
                 " titulaire_id, titulaire_nom, objet, montant, source, confidence)"
                 " VALUES (?, '1', 'Commune', ?, 'Jeanne VOISINE', 'Travaux', 5000, 'DECP',"
                 " 'verified')", (commune, ei))
    base.execute("INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom,"
                 " titulaire_id, titulaire_siren, titulaire_nom, objet, montant, source,"
                 " confidence) VALUES (?, '1', 'Commune', NULL, '999000321', 'VOISINE JEANNE',"
                 " 'Travaux', 5001, 'DECP', 'verified')", (commune,))
    _marche(base, None, "VOISINE JEANNE", 5002, siren="999000321")      # syndicat, SIREN
    _marche(base, None, "Jeanne Voisine", 5003)                          # syndicat, nom
    base.execute("UPDATE marches_publics SET objet = 'Lot 2 sous-traité à Jeanne Voisine'"
                 " WHERE montant = 5003")
    base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                 " description) VALUES ('subvention', 2025, 72, ?, ?, 'verified',"
                 " 'Aide pour Jeanne VOISINE')", (syndicat, association))
    base.commit()
    out = tmp_path / "snap"
    sortie = _chaine(base, out, monkeypatch)
    assert sortie["fiches"]["ei_nommees"] == {ei}
    noms = {m["montant"]: m["titulaire_nom"] for m in sortie["marches"]}
    assert noms == {5000: "Jeanne VOISINE", 5001: None, 5002: None, 5003: None}
    objet = next(m for m in sortie["marches"] if m["montant"] == 5003)["objet"]
    assert objet == "Lot 2 sous-traité à [entreprise individuelle]"
    assert next(fl for fl in sortie["flux"] if fl["amount"] == 72)["description"] == \
        "Aide pour [entreprise individuelle]"
    assert _controle(vs, out) == {}
    # Le contrôleur, seul : le nom remis sur le marché du syndicat est une fuite.
    from scripts.snapshot.fiches import empreinte_snapshot
    audits = out.parent / "audits" / "controle_ei.json"
    (releve,) = json.loads(audits.read_text())["releves"].values()
    marches = json.loads((out / "marches.json").read_text())
    next(m for m in marches["marches"] if m["montant"] == 5002)["titulaire_nom"] = "Jeanne VOISINE"
    next(m for m in marches["marches"] if m["montant"] == 5003)["objet"] = "Lot 2 à VOISINE Jeanne"
    (out / "marches.json").write_text(json.dumps(marches, ensure_ascii=False))
    audits.write_text(json.dumps({"releves": {empreinte_snapshot(out): releve}}))
    erreurs = _controle(vs, out)
    assert "nom d'une entreprise individuelle non publiée" in erreurs
    assert "nom d'une entreprise individuelle non publiée dans un texte libre" in erreurs


@pytest.mark.parametrize("nom,texte,attendu", [
    ("DUPONT JEAN", "Travaux de Jean DUPONT.", "Travaux de [entreprise individuelle]."),
    ("Hélène MARTIN", "chez Helene Martin", "chez [entreprise individuelle]"),
    ("Jeanne Marie DUPONT", "pour Jeanne DUPONT", "pour [entreprise individuelle]"),
    ("Jean DUPONT", "lot de DUPONT Jean", "lot de [entreprise individuelle]"),
    ("Jean DUPONT", "lot de J. DUPONT", "lot de [entreprise individuelle]"),
    ("Jean D'ARC", "atelier Jean D’ARC", "atelier [entreprise individuelle]"),
    # « M. » se lit comme l'initiale de « Marc » : la civilité part avec le nom.
    ("Jean-Marc DUPONT", "M. jean marc dupont", "[entreprise individuelle]"),
    ("Jean DUPONT", "Jean et Paul DUPONT", "Jean et Paul DUPONT"),   # pas une suite
    ("Jean DUPONT", "rue Dupont", "rue Dupont"),                     # un seul mot
    ("Jean de LA FONTAINE", "pour Jean de la Fontaine", "pour [entreprise individuelle]"),
    ("LA FERME DES TROIS CHENES", "la ferme des voisins", "la ferme des voisins"),
])
def test_le_masque_du_texte_libre(nom, texte, attendu):
    """A3 : accents, casse, apostrophe typographique, ordre des mots, prénom omis,
    initiale. Un mot seul, ou des mots séparés par autre chose qu'un blanc, non."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    assert masquer_noms(texte, motif_des_noms(({nom}, set()))) == attendu


def test_une_variante_decp_ne_se_masque_qu_en_suite_exacte():
    """Avis vigie-securite (b) : l'enseigne d'un titulaire DECP ne fait pas
    masquer ses mots de métier ailleurs."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    motif = motif_des_noms((set(), {"DUPONT ESPACES VERTS"}))
    assert masquer_noms("Entretien des espaces verts communaux", motif) == \
        "Entretien des espaces verts communaux"
    assert masquer_noms("Lot confié à Dupont Espaces-Verts", motif) == \
        "Lot confié à [entreprise individuelle]"


def test_un_homonyme_publie_n_est_pas_masque():
    """Avis vigie-securite (b) : une suite qui tient dans le nom d'une fiche
    publiée (« Jean-Pierre Dupont », élu) n'est pas masquée."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    from scripts.snapshot.fiches import mots_du_nom
    motif = motif_des_noms(({"DUPONT JEAN PIERRE"}, set()), [mots_du_nom("Jean-Pierre Dupont")])
    assert masquer_noms("Subvention à Jean DUPONT", motif) == "Subvention à Jean DUPONT"


def test_une_initiale_et_un_nom_ne_tiennent_pas_dans_un_nom_publie():
    """Avis vigie-securite (b1) : « DUPONT TP » publiée ne protège pas « J. Dupont »."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    from scripts.snapshot.fiches import mots_du_nom
    motif = motif_des_noms(({"Jean DUPONT"}, set()), [mots_du_nom("DUPONT TP")])
    assert masquer_noms("Travaux J. Dupont", motif) == "Travaux [entreprise individuelle]"


def test_deux_zones_qui_se_chevauchent_sont_fusionnees():
    """Avis vigie-securite (c)."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    motif = motif_des_noms(({"Pierre Jean DUPONT", "DUPONT Marie Claire"}, set()))
    assert masquer_noms("Lot Jean Dupont Marie Claire", motif) == \
        "Lot [entreprise individuelle]"


def test_sur_un_marche_masque_chaque_mot_du_nom_est_retire():
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    from scripts.snapshot.fiches import mots_du_nom
    assert masquer_noms("Plomberie Dupont, dépannage", motif_des_noms(({"Jean DUPONT"}, set())),
                        mots_du_nom("Jean DUPONT")) == "Plomberie [nom retiré], dépannage"
    # Un mot vide du nom (« DES ») n'est jamais retiré.
    assert masquer_noms("Fourniture des repas", None, mots_du_nom("LA FERME DES TROIS CHENES")) \
        == "Fourniture des repas"


def test_les_mots_du_controleur_sont_ceux_du_builder(vs):
    from scripts.snapshot import fiches
    assert vs.MOT_RE == fiches.MOT_RE
    assert vs.MOTS_VIDES == fiches.MOTS_VIDES and vs.SEPARATEUR_RE == fiches.SEPARATEUR_RE
    for texte in ("Jean D’ARC, J. Dupont.", "Hélène-Marie MARTIN_2", "",
                  "Jean de la Fontaine, la ferme"):
        assert vs.mots_normalises(texte) == fiches.mots_normalises(texte)
        assert vs.jetons_lies(texte) == fiches.jetons_lies(texte)
    assert fiches.empreinte_de_suite("Dupont Espaces Verts") in vs.suites_de_mots(
        "lot de DUPONT espaces-verts")
    # Tout groupe de mots d'un nom écrit dans un texte se retrouve au relevé.
    assert fiches.empreintes_de_mots("Jeanne Marie DUPONT") & vs.groupes_de_mots(
        "versé à DUPONT Jeanne")
    assert not fiches.empreintes_de_mots("Jeanne DUPONT") & vs.groupes_de_mots(
        "Jeanne, puis DUPONT")


def test_acteur_nom_d_une_fiche_publiee_n_est_pas_refuse(vs, tmp_path, monkeypatch):
    """Avis vigie-securite (a) : le père, EI publiée « Jean DUPONT », reçoit une
    subvention ; le fils, EI écartée « DUPONT Jean Pierre ». L'entrée d'actualité
    qui nomme le père n'est pas une fuite ; la même, sans fiche, en est une."""
    from scripts.snapshot.fiches import empreintes_de_mots, empreinte_snapshot
    out = _snapshot_a_la_main(tmp_path, monkeypatch,
                              entities=[{"id": 4, "type": "business", "name": "Jean DUPONT"}])
    entree = {"genre": "argent", "acteur_id": 4, "acteur_nom": "Jean DUPONT", "montant": 9}
    (out / "actualite.json").write_text(json.dumps({"actualite": [entree]}))
    releve = {"parties_publiques": [], "ei_ecartees": [], "ei_nommees": [],
              "mots_ei": sorted(empreintes_de_mots("DUPONT Jean Pierre"))}
    audits = tmp_path / "audits" / "controle_ei.json"
    audits.write_text(json.dumps({"releves": {empreinte_snapshot(out): releve}}))
    rep = vs.Report()
    vs.check_noms_d_ei_ecartees(out, rep, vs.lire_controle_ei(out, rep))
    assert rep.errors == {}
    (out / "actualite.json").write_text(json.dumps({"actualite": [
        {**entree, "acteur_id": None, "acteur_nom": "Pierre DUPONT"}]}))
    rep = vs.Report()
    vs.check_noms_d_ei_ecartees(out, rep, vs.lire_controle_ei(out, rep))
    assert "nom d'une entreprise individuelle non publiée dans un texte libre" in rep.errors


# ── Tour 3 de la relecture de code (25/11/2026) ─────────────────────────────

@pytest.mark.parametrize("contenu", [{}, {"releves": None}, {"releves": []}])
def test_un_releve_sans_entrees_est_refuse_avec_sa_cause(vs, tmp_path, monkeypatch, contenu):
    """`releves` absent, nul ou mal formé : une erreur qui dit pourquoi, jamais
    une exception du contrôleur."""
    out = _snapshot_a_la_main(tmp_path, monkeypatch, entities=[{"id": 3, "type": "service"}])
    (tmp_path / "audits" / "controle_ei.json").write_text(json.dumps(contenu))
    rep = vs.Report()
    assert vs.lire_controle_ei(out, rep) is None
    assert list(rep.errors) == ["relevé des entreprises individuelles absent pour ce snapshot"]
    assert "« releves »" in str(rep.errors)


def test_un_elu_dirigeant_d_une_ei_presumee_ne_fait_pas_un_conflit(base, entite, tmp_path,
                                                                   monkeypatch):
    """Élu « Jean Dupont » dirigeant d'une EI sans forme « DUPONT JEAN », retenue
    par un marché et payée par la commune : l'EI est présumée (`ei_ids`), aucun
    conflit publié, même si le lien sortait (essai direct de `export_conflits`)."""
    from scripts.snapshot.conflits import export_conflits
    from scripts.snapshot.relations import etape_relations
    from collections import Counter, defaultdict
    commune = _commune_c1(base)
    elu = entite("Jean Dupont", "person", perimetre="C1")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (elu, commune))
    ei = _ei(base, entite, "DUPONT JEAN", forme=None)
    lien = base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                        " VALUES (?, ?, 'dirigeant', 'verified')", (elu, ei)).lastrowid
    _marche(base, ei, "DUPONT JEAN", 4000)
    _flux(base, commune, ei, 950)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    f = sortie["fiches"]
    assert ei in f["ei_ids"] and ei in f["public_ids"]
    rel = etape_relations(base, {}, f["public_ids"], {elu}, set(), f["ei_ids"], set(),
                          defaultdict(Counter))["public_relations"]
    args = (base, f["public_ids"], rel, sortie["flux"], [], lambda t: t)
    assert export_conflits(*args, ei_ids=f["ei_ids"])["cas"] == []
    # Même si le lien de direction sortait, l'EI présumée ne fait pas un conflit.
    force = rel + [{"id": lien, "from_id": elu, "to_id": ei, "relation_type": "dirigeant"}]
    args = (base, f["public_ids"], force, sortie["flux"], [], lambda t: t)
    assert export_conflits(*args, ei_ids=f["ei_ids"])["cas"] == []
    assert export_conflits(*args, ei_ids=set())["cas"] != []   # témoin : sans la règle


def test_une_fiche_c3_presumee_ei_n_est_pas_une_partie_publique(base, entite, tmp_path,
                                                                 monkeypatch):
    """Une fiche C3 sans forme, homonyme d'une personne : un `business`, donc ni
    partie publique (décision de Julien, tour 4) ni hors de la règle des EI ;
    `etape_fiches` la présume EI sauf si l'atelier a rejeté le lien."""
    from scripts.snapshot.fiches import ei_presumees, parties_publiques
    personne = entite("Jeanne PREFETE", "person", perimetre="C3")
    c3 = _ei(base, entite, "JEANNE PREFETE", perimetre="C3", forme=None)
    lien = base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                        " VALUES (?, ?, 'dirigeant', 'probable')", (personne, c3)).lastrowid
    base.commit()
    assert c3 not in parties_publiques(base)
    assert c3 in ei_presumees(base, set()) and c3 not in ei_presumees(base, {lien})
    revue = {"relation": {lien: {"statut": "ecarte", "corrections": {}, "confidence": None,
                                 "note": None}}}
    assert c3 not in _chaine(base, tmp_path / "snap", monkeypatch, revue)["fiches"]["ei_ids"]


# ── Avis vigie-securite sur 2825b8a ─────────────────────────────────────────

def test_l_avis_de_marche_perd_le_mot_seul_et_la_note(base, entite):
    """Le titre et la note d'un acte `marché_public` relié au marché d'une EI
    écartée : un nom de deux mots devient « [entreprise individuelle] », un mot
    seul du nom « [nom retiré] ». Un acte `marché_public` ne reçoit pas de
    `note_revue` par la revue d'atelier (`TYPES_REVUS`) : elle est posée ici à la
    main, l'étape la masque quand même."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.argent import etape_avis_de_marche
    _syndicat(base, entite)
    ei = _ei(base, entite, "Jean Seulmot", perimetre="C2")
    avis = base.execute("INSERT INTO events (type, date, title, source) VALUES"
                        " ('marché_public', '2026-03-06', 'Maconnerie lot Seulmot', 'BOAMP')"
                        ).lastrowid
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " titulaire_nom, objet, montant, source, confidence, event_id) VALUES"
                 " ('1', 'x', ?, 'Jean Seulmot', 'o', 10, 'DECP', 'verified', ?)", (ei, avis))
    base.commit()
    f = fiches.etape_fiches(base, {}, set(), set(), defaultdict(Counter), [], redige=lambda t: t)
    evenements = [{"id": avis, "type": "marché_public", "title": "Maconnerie lot Seulmot",
                   "note_revue": "vu avec Jean SEULMOT, lot Seulmot"}]
    etape_avis_de_marche(base, {}, f["entity_rows"], f["ei_ids"], f["ei_nommees"],
                         f["public_ids"], evenements)
    assert evenements[0]["title"] == "Maconnerie lot [nom retiré]"
    assert evenements[0]["note_revue"] == "vu avec [entreprise individuelle], lot [nom retiré]"


@pytest.mark.parametrize("contenu", ["{pas du json", "[1, 2]"])
def test_un_releve_illisible_le_dit(vs, tmp_path, monkeypatch, contenu):
    """Le message dit « illisible », pas « clé absente » (avis vigie-securite)."""
    out = _snapshot_a_la_main(tmp_path, monkeypatch, entities=[{"id": 3, "type": "service"}])
    (tmp_path / "audits" / "controle_ei.json").write_text(contenu)
    rep = vs.Report()
    assert vs.lire_controle_ei(out, rep) is None
    assert "illisible" in str(rep.errors) and "« releves »" not in str(rep.errors)


# ── Tour 5 : erreurs d'accès au relevé, droits du fichier ───────────────────

def test_le_releve_est_ecrit_en_0644(base, tmp_path, monkeypatch):
    import stat
    from scripts.snapshot.fiches import etape_controle_ei
    monkeypatch.setenv("VIGIE_AUDITS", str(tmp_path / "audits"))
    out = tmp_path / "snap"
    out.mkdir()
    etape_controle_ei(base, out, {}, {"generated_at": "x"}, [], set(), set(), set())
    mode = stat.S_IMODE((tmp_path / "audits" / "controle_ei.json").stat().st_mode)
    assert mode == 0o644


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="droits POSIX, et root lit tout")
def test_un_releve_illisible_par_droits_arrete_le_builder_et_se_dit(base, vs, tmp_path,
                                                                    monkeypatch):
    """Un `PermissionError` n'efface pas les relevés en silence (le builder
    s'arrête), et le contrôleur ne le dit pas « introuvable »."""
    from scripts.snapshot.fiches import etape_controle_ei
    out = _snapshot_a_la_main(tmp_path, monkeypatch, entities=[{"id": 3, "type": "service"}],
                              controle={"generated_at": "2026-11-24T12:00:00",
                                        "parties_publiques": [], "ei_ecartees": []})
    chemin = tmp_path / "audits" / "controle_ei.json"
    avant = chemin.read_text()
    chemin.chmod(0)
    try:
        with pytest.raises(PermissionError):
            etape_controle_ei(base, out, {}, {"generated_at": "y"}, [], set(), set(), set())
        rep = vs.Report()
        assert vs.lire_controle_ei(out, rep) is None
        assert "illisible" in str(rep.errors) and "introuvable" not in str(rep.errors)
    finally:
        chemin.chmod(0o644)
    assert chemin.read_text() == avant


# ── Avis vigie-dpo, tour 5 : l'élu exploitant d'une EI écartée ──────────────

def test_l_elu_exploitant_n_epargne_pas_le_nom_de_son_ei_ecartee(base, entite, tmp_path,
                                                                  monkeypatch, vs):
    """Élu « Marc DUPRAT », publié par son mandat ; son EI « Marc DUPRAT », reliée
    à lui, est rejetée à l'atelier. Un marché au seul nom et une description
    qui le citent : masqués. Un homonyme sans lien avec une EI écartée épargne
    toujours son nom. Le contrôleur, avec le même relevé, ne refuse rien."""
    commune = _commune_c1(base)
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    elu = entite("Marc DUPRAT", "person", perimetre="C1")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (elu, commune))
    ei = _ei(base, entite, "MARC DUPRAT")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'verified')", (elu, ei))
    homonyme = entite("Paul VERNET", "person", perimetre="C1")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (homonyme, commune))
    _ei(base, entite, "PAUL VERNET")                           # EI sans fait, SANS lien
    _marche(base, None, "Marc Duprat", 4100)
    for montant, texte in ((73, "Aide versée via Marc Duprat"), (74, "Aide versée via Paul Vernet")):
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id,"
                     " confidence, description) VALUES ('subvention', 2025, ?, ?, ?,"
                     " 'verified', ?)", (montant, syndicat, association, texte))
    base.commit()
    from scripts.snapshot.personnes import etape_personnes_publiques
    assert elu in etape_personnes_publiques(base, "[]")["public_person_ids"]
    sortie = _chaine(base, tmp_path / "snap", monkeypatch,
                     {"entity": {ei: {"statut": "ecarte"}}})
    assert elu in sortie["fiches"]["public_ids"] and ei not in sortie["fiches"]["public_ids"]
    m = next(m for m in sortie["marches"] if m["montant"] == 4100)
    assert m["titulaire_nom"] is None and m["titulaire_masque"]
    flux = {fl["amount"]: fl["description"] for fl in sortie["flux"]}
    assert flux[73] == "Aide versée via [entreprise individuelle]"
    assert flux[74] == "Aide versée via Paul Vernet"
    assert _controle(vs, tmp_path / "snap") == {}


# ── Tour 6 : sur sa propre ligne, l'élu exploitant garde son nom ────────────

def test_l_elu_exploitant_garde_son_nom_sur_sa_propre_ligne(base, entite, tmp_path,
                                                            monkeypatch, vs):
    """« Indemnité de fonction de Marc Duprat », flux commune → l'élu publié :
    son nom reste (il est l'extrémité de la ligne). La ligne de l'EI écartée,
    elle, reste masquée. Le contrôleur ne refuse rien."""
    commune = _commune_c1(base)
    syndicat = _syndicat(base, entite)
    association = entite("Une association", "association", perimetre="C1")
    elu = entite("Marc DUPRAT", "person", perimetre="C1")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (elu, commune))
    ei = _ei(base, entite, "MARC DUPRAT")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'verified')", (elu, ei))
    for montant, de, vers, texte in (
            (75, commune, elu, "Indemnité de fonction de Marc Duprat"),
            (76, syndicat, association, "Aide versée via Marc Duprat")):
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id,"
                     " confidence, description) VALUES ('indemnite', 2025, ?, ?, ?,"
                     " 'verified', ?)", (montant, de, vers, texte))
    _marche(base, None, "Marc Duprat", 4200)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch,
                     {"entity": {ei: {"statut": "ecarte"}}})
    assert elu in sortie["fiches"]["public_ids"]
    flux = {fl["amount"]: fl for fl in sortie["flux"]}
    assert flux[75]["description"] == "Indemnité de fonction de Marc Duprat"
    assert flux[75]["to_id"] == elu
    assert flux[76]["description"] == "Aide versée via [entreprise individuelle]"
    assert next(m for m in sortie["marches"] if m["montant"] == 4200)["titulaire_nom"] is None
    assert _controle(vs, tmp_path / "snap") == {}



def test_le_don_de_l_elu_exploitant_ne_fait_pas_refuser_l_actualite(base, entite, tmp_path,
                                                                     monkeypatch, vs):
    """Flux élu → association, « Don de Marc Duprat » (sens `tiers`) : épargné
    sur sa ligne par `from_id`. L'entrée d'actualité ne porte que l'association
    (`acteur_id`) ; le contrôleur ne la refuse pas (avis vigie-securite, tour 6)."""
    commune = _commune_c1(base)
    association = entite("Une association", "association", perimetre="C1")
    elu = entite("Marc DUPRAT", "person", perimetre="C1")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (elu, commune))
    ei = _ei(base, entite, "MARC DUPRAT")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'verified')", (elu, ei))
    base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                 " description) VALUES ('don', 2025, 77, ?, ?, 'verified', 'Don de Marc Duprat')",
                 (elu, association))
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch,
                     {"entity": {ei: {"statut": "ecarte"}}})
    assert next(fl for fl in sortie["flux"] if fl["amount"] == 77)["description"] == \
        "Don de Marc Duprat"
    assert "Don de Marc Duprat" in (tmp_path / "snap" / "actualite.json").read_text()
    assert _controle(vs, tmp_path / "snap") == {}



# ── Tour 7 ──────────────────────────────────────────────────────────────────

def test_le_doublon_homonyme_d_une_ei_sans_fiche_perd_son_nom(base, entite, tmp_path,
                                                              monkeypatch, vs):
    """Sonde du relecteur : un `business` « Jeanne Doublon », C2, sans forme ni
    lien, homonyme d'une EI sans fait, reçoit un flux. Il sort délié, sans nom ;
    le contrôleur ne refuse rien."""
    syndicat = _syndicat(base, entite)
    ei = _ei(base, entite, "Jeanne DOUBLON")
    autre = entite("Jeanne Doublon", "business", perimetre="C2")
    _flux(base, syndicat, autre, 777)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    assert ei not in sortie["fiches"]["public_ids"]
    fl = next(f for f in sortie["flux"] if f["amount"] == 777)
    assert fl["to_id"] is None and fl["to_name"] is None and fl["from_id"] == syndicat
    assert _controle(vs, tmp_path / "snap") == {}


def test_les_mots_de_la_commune_ne_sont_pas_retires():
    """« Travaux à Testonville » face à une EI « Jean TESTONVILLE » au titulaire
    effacé : le nom de la commune reste ; le nom entier, lui, est masqué."""
    from scripts.snapshot.argent import masquer_noms, motif_des_noms
    from scripts.snapshot.fiches import mots_du_nom
    isoles = mots_du_nom("Jean TESTONVILLE")
    motif = motif_des_noms(({"Jean TESTONVILLE"}, set()))
    assert masquer_noms("Travaux à Testonville", motif, isoles) == "Travaux à Testonville"
    assert masquer_noms("commune de Testonville", motif, isoles) == "commune de Testonville"
    # Hors d'un contexte de lieu, c'est un nom de famille : retiré (avis
    # vigie-securite, tour 7).
    assert masquer_noms("Toiture, Ets Testonville", motif, isoles) == "Toiture, Ets [nom retiré]"
    assert masquer_noms("M. Testonville, plombier", motif, isoles) == \
        "M. [nom retiré], plombier"
    assert masquer_noms("Travaux de Jean Testonville", motif, isoles) == \
        "Travaux de [entreprise individuelle]"



def test_un_bout_publie_homonyme_d_une_ei_sans_fiche_garde_son_nom(base, entite, tmp_path,
                                                                   monkeypatch, vs):
    """Le doublon homonyme perd son nom s'il n'est pas publié ; publié (une
    association C1 « Jeanne Doublon »), il le garde (avis vigie-securite, tour 7)."""
    syndicat = _syndicat(base, entite)
    _ei(base, entite, "Jeanne DOUBLON")
    asso = entite("Jeanne Doublon", "association", perimetre="C1")
    _flux(base, syndicat, asso, 778)
    base.commit()
    sortie = _chaine(base, tmp_path / "snap", monkeypatch)
    fl = next(f for f in sortie["flux"] if f["amount"] == 778)
    assert fl["to_id"] == asso and fl["to_name"] == "Jeanne Doublon"
    assert _controle(vs, tmp_path / "snap") == {}


# ── Tour 8 : la requête des homonymes, réécrite sur l'égalité ───────────────

ANCIENNE_REQUETE_HOMONYMES = """
    SELECT r.id AS rid, b.id AS bid, b.name AS bnom, p.id AS pid, p.name AS pnom,
           COALESCE(TRIM(bz.legal_form_code), '') AS forme
    FROM relations r
    JOIN entities b ON b.id IN (r.from_id, r.to_id) AND b.type = 'business'
    JOIN entities p ON p.id IN (r.from_id, r.to_id) AND p.type = 'person'
                   AND p.id != b.id
    LEFT JOIN businesses bz ON bz.entity_id = b.id"""


def test_la_requete_des_homonymes_rend_ce_que_rendait_l_ancienne(base, entite):
    """Mêmes lignes que la jointure `IN (from_id, to_id)` d'avant le tour 8, sur
    des liens dans les deux sens, entre deux personnes, entre deux entreprises,
    vers une association, avec et sans ligne `businesses`."""
    import random
    from scripts.snapshot import fiches
    r = random.Random(7)
    ids = []
    for i in range(40):
        type_ = ("business", "person", "association")[i % 3]
        eid = entite(f"Nom {i % 7} Prenom {i % 5} n{i}", type_)
        if type_ == "business" and i % 2:
            base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, ?)",
                         (eid, r.choice(["1000", " ", None, "5499"])))
        ids.append(eid)
    for _ in range(150):
        base.execute("INSERT OR IGNORE INTO relations (from_id, to_id, relation_type) VALUES"
                     " (?, ?, ?)", (r.choice(ids), r.choice(ids), r.choice(["dirigeant", "membre"])))
    # Deux paires homonymes certaines, une par sens de lien.
    for nom_b, nom_p, sens in (("JEAN HOMONYMEUN", "Homonymeun Jean", 0), ("PAUL ROUX", "Paul Roux", 1)):
        b_id, p_id = entite(nom_b), entite(nom_p, "person")
        base.execute("INSERT INTO relations (from_id, to_id, relation_type) VALUES (?, ?, 'gérant')",
                     (p_id, b_id) if sens == 0 else (b_id, p_id))
    lignes = lambda requete: sorted(tuple(x) for x in base.execute(requete).fetchall())
    nouvelle = f"""
        SELECT {fiches.HOMONYMES_COLONNES} FROM relations r
        JOIN entities p ON p.id = r.from_id AND p.type = 'person'
        JOIN entities b ON b.id = r.to_id AND b.type = 'business'
        LEFT JOIN businesses bz ON bz.entity_id = b.id
        UNION ALL
        SELECT {fiches.HOMONYMES_COLONNES} FROM relations r
        JOIN entities b ON b.id = r.from_id AND b.type = 'business'
        JOIN entities p ON p.id = r.to_id AND p.type = 'person'
        LEFT JOIN businesses bz ON bz.entity_id = b.id"""
    attendu = lignes(ANCIENNE_REQUETE_HOMONYMES)
    assert attendu and lignes(nouvelle) == attendu
    # Et la fonction elle-même : mêmes paires qu'en filtrant l'ancienne requête.
    from scripts.snapshot.textes import norm_nom
    paires = set()
    for x in base.execute(ANCIENNE_REQUETE_HOMONYMES):
        a, b = set(norm_nom(x[2]).split()), set(norm_nom(x[4]).split())
        court, long_ = sorted((a, b), key=len)
        if len(court) >= 2 and court <= long_:
            paires.add((x[1], x[3], x[5]))
    assert len(paires) >= 2 and set(fiches.homonymes_relies(base, set())) == paires
