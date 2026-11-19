"""L'écrivain unique du magasin national (`scripts/rafraichir_magasin.py`)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from scripts import rafraichir_magasin as ecrivain

COLLECTEURS = Path(__file__).resolve().parent.parent / "collectors"


def test_tout_collecteur_du_magasin_a_son_step():
    """Un collecteur qui dépose au magasin sans place dans l'écrivain ne serait
    plus jamais rafraîchi : les lecteurs n'y écrivent plus."""
    deposants = {
        chemin.stem for chemin in COLLECTEURS.glob("*.py")
        if re.search(r"^from \.national_store import", chemin.read_text(encoding="utf-8"), re.M)
    }
    assert deposants == (set(ecrivain.STEPS_NATIONAUX.values()) | set(ecrivain.REMPLISSEURS.values())
                         | set(ecrivain.LECTEURS_SEULS))


def test_chaque_step_existe_dans_run_all():
    pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")  # run_all tire collectors.conseils
    from collectors.run_all import STEPS

    assert set(ecrivain.STEPS_NATIONAUX) <= set(STEPS)


def test_chaque_remplisseur_a_sa_fonction():
    pytest.importorskip("certifi", reason="job « tests-deps » : pip install -r requirements.txt")  # collectors.incendie
    pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")
    import importlib

    for module in ecrivain.REMPLISSEURS.values():
        assert callable(importlib.import_module(f"collectors.{module}").remplir_magasin)


def test_chaque_step_ne_se_joue_qu_une_fois_par_passe(monkeypatch, tmp_path):
    """Le défaut d'avant : quinze steps rejoués par département."""
    commandes = []

    def lancer(commande, cwd, env, delai=None):
        commandes.append(commande)
        return True

    monkeypatch.setattr(ecrivain, "lancer", lancer)
    monkeypatch.setattr(ecrivain, "preparer_espace", lambda d: _faux_moteur(d))
    steps = ["marches", "dvf", "incendie"]
    assert ecrivain.rafraichir(["30140", "26289", "97411"], tmp_path, steps) == []
    init, *joues = commandes
    assert init[-2:] == ["30140", "--force"]
    assert joues[0][-2:] == ["--step", "marches"]
    assert joues[1][-5:] == ["--remplir", "dvf", "30", "26", "974"]
    assert joues[2][-5:] == ["--remplir", "incendie", "30", "26", "974"]
    assert len(joues) == 3


def test_une_commune_qui_ne_s_initialise_pas_cede_la_place(monkeypatch, tmp_path):
    inits = []

    def lancer(commande, cwd, env, delai=None):
        if "init_instance.py" in commande[1]:
            inits.append(commande[2])
            return commande[2] != "30140"
        return True

    monkeypatch.setattr(ecrivain, "lancer", lancer)
    monkeypatch.setattr(ecrivain, "preparer_espace", lambda d: _faux_moteur(d))
    assert ecrivain.rafraichir(["30140", "26289"], tmp_path, ["marches"]) == ["30140:init"]
    assert inits == ["30140", "26289"]


def test_le_fils_ne_remplit_qu_un_remplisseur_connu(monkeypatch, capsys):
    monkeypatch.setenv("VIGIE_NATIONAL_STORE", "/inexistant")
    assert ecrivain.main(["--remplir", "os", "30"]) == 2


def test_le_fichier_des_communes_est_une_entree_non_fiable():
    lignes = ["30140", "30141", "../../etc/passwd", "2A004", "2b033", "97411",
              "97412", "", "  26289  ", "30140; rm -rf /", "1234", "123456"]
    assert ecrivain.communes_par_departement(lignes) == ["30140", "2A004", "2B033", "97411", "26289"]


def test_le_nombre_de_departements_est_borne(monkeypatch):
    monkeypatch.setattr(ecrivain, "DEPARTEMENTS_MAX", 2)
    assert ecrivain.communes_par_departement(["30140", "26289", "81037"]) == ["30140", "26289"]


def test_seuls_les_part_abandonnes_sont_retires(tmp_path):
    vieux, recent = tmp_path / "decp" / ".decp-2025.json.1.ab.part", tmp_path / ".ban.2.cd.part"
    dossier = tmp_path / "sispea" / "archives" / ".AEP-2024.x1.part"
    garde = tmp_path / "decp" / "decp-2025.json"
    for chemin in (vieux, recent, garde):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(b"x")
    dossier.mkdir(parents=True)
    (dossier / "classeur.xlsx").write_bytes(b"x")
    il_y_a_7h = time.time() - 7 * 3600
    for chemin in (vieux, dossier):
        os.utime(chemin, (il_y_a_7h, il_y_a_7h))
    retires = ecrivain.purger_parts(tmp_path)
    assert set(retires) == {vieux, dossier}
    assert recent.exists() and garde.exists()


def test_le_manifeste_ignore_les_fichiers_caches(tmp_path):
    (tmp_path / "decp").mkdir()
    (tmp_path / "decp" / "decp-2025.json").write_bytes(b"{}")
    (tmp_path / "decp" / ".decp-2025.json.1.part").write_bytes(b"{")
    (tmp_path / "MANIFESTE.json").write_text("{}")
    fichiers = ecrivain.manifeste(tmp_path)
    assert list(fichiers) == ["decp/decp-2025.json"]
    assert fichiers["decp/decp-2025.json"]["sha256"] == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")


def test_sans_magasin_declare_l_ecrivain_ne_devine_pas(monkeypatch, tmp_path):
    monkeypatch.delenv("VIGIE_NATIONAL_STORE", raising=False)
    assert ecrivain.main(["--communes", str(tmp_path / "absent.txt")]) == 2


def test_une_passe_ecrit_le_manifeste_et_rend_les_echecs(monkeypatch, tmp_path):
    magasin = tmp_path / "magasin"
    magasin.mkdir()
    communes = tmp_path / "communes.txt"
    communes.write_text("30140\n26289\n")
    monkeypatch.setenv("VIGIE_NATIONAL_STORE", str(magasin))
    joues = []

    def rafraichir(codes, store, steps, acceptes=None):
        joues.append((codes, steps))
        (store / "ban").mkdir()
        (store / "ban" / "adresses-30.csv.gz").write_bytes(b"x")
        return ["26289:dvf"]

    monkeypatch.setattr(ecrivain, "rafraichir", rafraichir)
    assert ecrivain.main(["--communes", str(communes), "--step", "dvf"]) == 1
    assert joues == [(["30140", "26289"], ["dvf"])]
    contenu = json.loads((magasin / "MANIFESTE.json").read_text())
    assert contenu["departements"] == ["30", "26"]
    assert contenu["echecs"] == ["26289:dvf"]
    assert "ban/adresses-30.csv.gz" in contenu["fichiers"]


def test_le_fichier_des_communes_n_est_lu_que_s_il_est_ordinaire(tmp_path):
    vrai = tmp_path / "communes.txt"
    vrai.write_text("30140\n" + "x" * (ecrivain.COMMUNES_OCTETS_MAX * 2))
    assert ecrivain.lire_communes(vrai)[0] == "30140"
    assert sum(len(l) for l in ecrivain.lire_communes(vrai)) <= ecrivain.COMMUNES_OCTETS_MAX
    lien = tmp_path / "lien.txt"
    lien.symlink_to(vrai)
    assert ecrivain.lire_communes(lien) == []
    fifo = tmp_path / "fifo.txt"
    os.mkfifo(fifo)
    assert ecrivain.lire_communes(fifo) == [], "un FIFO ne bloque pas la passe"
    assert ecrivain.lire_communes(tmp_path / "absent.txt") == []


def test_les_steps_tournent_en_mode_ecrivain(monkeypatch, tmp_path):
    vus = {}

    def lancer(commande, cwd, env, delai=None):
        vus.update(env)
        return True

    monkeypatch.setattr(ecrivain, "lancer", lancer)
    monkeypatch.setattr(ecrivain, "preparer_espace", lambda d: _faux_moteur(d))
    ecrivain.rafraichir(["30140"], tmp_path, ["dvf"], ["decp/decp-2024-09.json"])
    assert vus["VIGIE_ECRIVAIN_MAGASIN"] == "1"
    assert vus["VIGIE_ACCEPTER_RETRECISSEMENT"] == "decp/decp-2024-09.json"


def test_le_budget_arrete_la_passe_proprement(monkeypatch, tmp_path):
    monkeypatch.setattr(ecrivain, "BUDGET_SECONDES", -1)
    monkeypatch.setattr(ecrivain, "lancer", lambda *a, **k: True)
    monkeypatch.setattr(ecrivain, "preparer_espace", lambda d: _faux_moteur(d))
    assert ecrivain.rafraichir(["30140"], tmp_path, ["dvf"]) == ["dvf:budget"]


def _faux_moteur(dossier: Path) -> Path:
    moteur = dossier / "moteur"
    (moteur / "tests").mkdir(parents=True)
    (moteur / "config").mkdir()
    (moteur / "tests" / "instance_test.json").write_text("{}")
    return moteur


def test_la_ban_a_son_propre_delai(monkeypatch, tmp_path):
    delais = {}

    def lancer(commande, cwd, env, delai=None):
        delais[commande[-4] if "--remplir" in commande else commande[-1]] = delai
        return True

    monkeypatch.setattr(ecrivain, "lancer", lancer)
    monkeypatch.setattr(ecrivain, "preparer_espace", lambda d: _faux_moteur(d))
    ecrivain.rafraichir(["30140", "26289", "07163"], tmp_path, ["marches", "incendie"])
    assert delais["marches"] == ecrivain.STEP_TIMEOUT
    assert delais["incendie"] == ecrivain.DELAI_DU_STEP["incendie"] > ecrivain.STEP_TIMEOUT


def test_un_departement_suivi_d_un_saut_de_ligne_est_refuse(monkeypatch, capsys):
    monkeypatch.setattr(ecrivain, "REMPLISSEURS", {"dvf": "dvf"})
    vus = []
    import collectors.dvf as dvf
    monkeypatch.setattr(dvf, "remplir_magasin", lambda deps: vus.extend(deps) or [])
    assert ecrivain.remplir("dvf", ["30", "26\n", "../x", "974"]) == 0
    assert vus == ["30", "974"]
