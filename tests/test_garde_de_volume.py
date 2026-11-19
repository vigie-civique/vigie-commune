"""Garde de volume de la passe automatique (revue d'architecture du 07/11/2026, C2).

La passe de nuit publie sans regard humain : une source tronquée qui vide sa
table ne doit pas partir en ligne parce qu'une autre source a ajouté une
ligne. Seuils décidés par Julien : plus de la moitié perdue, ou zéro, sur un
fichier qui comptait au moins 20 objets dans la version servie.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def publication():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "publication_garde_sous_test", ROOT / "scripts" / "publication.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def emplacements(publication, tmp_path, monkeypatch):
    brouillon, publie = tmp_path / "brouillon", tmp_path / "publie"
    site = tmp_path / "site" / "public" / "static" / "data"
    for d in (brouillon, publie, site):
        d.mkdir(parents=True)
    monkeypatch.setattr(publication, "BROUILLON", brouillon)
    monkeypatch.setattr(publication, "PUBLIE", publie)
    monkeypatch.setattr(publication, "SITE", site)
    monkeypatch.setattr(publication, "ETAT", tmp_path / "etat.json")
    monkeypatch.setattr(publication, "VERSIONS", tmp_path / "versions")
    # Un brouillon dont la génération est allée au bout (`_generation_aboutie`).
    publication.ecrire_etat({"brouillon": {"complet": True,
                                           "repertoire": str(brouillon.resolve())}})
    return {"brouillon": brouillon, "publie": publie, "site": site}


def snapshot(dossier: Path, volumes: dict[str, int]) -> None:
    """Un snapshot dont le manifeste porte le volume voulu par fichier."""
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "stats.json").write_text(json.dumps({"entities_public": 1}), encoding="utf-8")
    fichiers = []
    for nom, n in volumes.items():
        (dossier / nom).write_text(json.dumps([{"id": i} for i in range(n)]), encoding="utf-8")
        fichiers.append({"chemin": nom, "objets": n})
    (dossier / "manifeste.json").write_text(json.dumps(
        {"format": "vigie-manifeste/1", "fichiers": fichiers}), encoding="utf-8")


def ok(_cible):
    return {"ok": True, "rapport": "OK"}


def servi_puis_neuf(emplacements, servi, neuf):
    snapshot(emplacements["publie"], servi)
    snapshot(emplacements["brouillon"], neuf)


def test_chute_de_60_pourcent_refusee_a_la_passe_auto(publication, emplacements):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 40})
    with pytest.raises(publication.PublicationRefusee) as e:
        publication.publier(auteur="passe", role="admin", controleur=ok, passe_auto=True)
    assert e.value.detail["chutes_de_volume"] == [
        {"chemin": "flows.json", "servi": 100, "neuf": 40}]
    # Rien n'a été mis en service.
    assert len(json.loads((emplacements["publie"] / "flows.json").read_text())) == 100


def test_chute_a_zero_refusee_meme_fichier_disparu(publication, emplacements):
    servi_puis_neuf(emplacements, {"eau.json": 30, "flows.json": 50}, {"flows.json": 50})
    with pytest.raises(publication.PublicationRefusee) as e:
        publication.publier(auteur="passe", role="admin", controleur=ok, passe_auto=True)
    assert e.value.detail["chutes_de_volume"] == [
        {"chemin": "eau.json", "servi": 30, "neuf": 0}]


def test_baisse_de_moitie_exacte_passe(publication, emplacements):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 50})
    publie = publication.publier(auteur="passe", role="admin", controleur=ok, passe_auto=True)
    assert publie["chutes_de_volume"] == []


def test_petit_fichier_sous_le_plancher_ne_joue_pas(publication, emplacements):
    servi_puis_neuf(emplacements, {"elus.json": 19}, {"elus.json": 0})
    publie = publication.publier(auteur="passe", role="admin", controleur=ok, passe_auto=True)
    assert publie["chutes_de_volume"] == []


def test_premiere_publication_sans_manifeste_servi(publication, emplacements):
    snapshot(emplacements["brouillon"], {"flows.json": 5})
    publie = publication.publier(auteur="passe", role="admin", controleur=ok, passe_auto=True)
    assert publie["chutes_de_volume"] == []


def test_publication_manuelle_non_bloquee(publication, emplacements):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 10})
    publie = publication.publier(auteur="julien", role="admin", controleur=ok)
    assert publie["chutes_de_volume"] == [{"chemin": "flows.json", "servi": 100, "neuf": 10}]
    assert len(json.loads((emplacements["publie"] / "flows.json").read_text())) == 10


def test_passer_outre_avec_motif_consigne_dans_audit_log(publication, emplacements, base):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 10})
    consigner = lambda chutes, motif: publication.consigner_passe_outre(
        base, "julien", motif, chutes)
    publie = publication.publier(auteur="julien", role="admin", controleur=ok,
                                 passe_auto=True, passer_outre="purge voulue du bassin",
                                 consigner=consigner)
    ligne = base.execute("SELECT id, table_name, action, new_value FROM audit_log").fetchone()
    assert publie["garde_passee_outre"] == ligne[0]
    assert (ligne[1], ligne[2]) == ("publication", "garde-de-volume-passee-outre")
    trace = json.loads(ligne[3])
    assert trace["motif"] == "purge voulue du bassin"
    assert trace["chutes"] == [{"chemin": "flows.json", "servi": 100, "neuf": 10}]
    # Le passage outre ne relance pas, à lui seul, une republication de nuit.
    assert "publication" in publication.GESTES_SANS_EFFET_SUR_LE_SITE


@pytest.mark.parametrize("motif", ["", "   "])
def test_passer_outre_sans_motif_refuse(publication, emplacements, motif):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 10})
    with pytest.raises(publication.PublicationRefusee, match="motif"):
        publication.publier(auteur="julien", role="admin", controleur=ok,
                            passe_auto=True, passer_outre=motif, consigner=lambda *a: 1)


def test_passer_outre_sans_lieu_ou_consigner_refuse(publication, emplacements):
    servi_puis_neuf(emplacements, {"flows.json": 100}, {"flows.json": 10})
    with pytest.raises(publication.PublicationRefusee, match="nulle part"):
        publication.publier(auteur="julien", role="admin", controleur=ok,
                            passe_auto=True, passer_outre="motif")


def test_la_passe_de_nuit_passe_par_la_garde():
    """Le chemin réel : `collecte.sh` → `publier-site.sh --passe-auto` →
    `publication.py publier --passe-auto`."""
    collecte = (ROOT / "deploy" / "collecte.sh").read_text(encoding="utf-8")
    publier_site = (ROOT / "deploy" / "publier-site.sh").read_text(encoding="utf-8")
    assert "publier-site.sh --deployer --passe-auto" in collecte
    assert "--passe-auto) OPTIONS_PUBLIER+=(--passe-auto)" in publier_site
    assert '"$PUBLICATION" publier ${OPTIONS_PUBLIER[@]+"${OPTIONS_PUBLIER[@]}"}' in publier_site


def test_la_ligne_de_commande_garde_toujours(publication, monkeypatch):
    """Avis vigie-securite du 07/11 : `publier` sans `--passe-auto` ne doit pas
    publier une chute sans motif."""
    vus = {}

    def faux_publier(**kw):
        vus.update(kw)
        return {"empreinte": "x", "chutes_de_volume": []}
    monkeypatch.setattr(publication, "publier", faux_publier)
    try:
        publication.main(["publier"])
    except Exception:
        pass
    assert vus.get("passe_auto") is True
