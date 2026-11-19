"""Le débit par hôte — cf. collectors/debit.py.

Les quotas comptent par adresse IP : le portail, les ateliers et le magasin
partagent la même. Aucun de ces tests ne sort sur le réseau ; l'horloge est
factice, sauf dans l'essai à plusieurs processus, qui ne fait jamais attendre.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from collectors import debit, reseau


class Horloge:
    def __init__(self):
        self.maintenant = 1_000_000.0
        self.attentes: list[float] = []

    def time(self):
        return self.maintenant

    # L'horloge du mur ne bouge qu'à la main ; celle des délais avance avec
    # chaque attente, pour qu'un verrou gardé finisse par lâcher.
    def monotonic(self):
        return sum(self.attentes)

    def sleep(self, secondes):
        self.attentes.append(secondes)


@pytest.fixture
def horloge(monkeypatch):
    faux = Horloge()
    monkeypatch.setattr(debit, "time", faux)
    monkeypatch.setattr(debit, "_etat_local", {})
    monkeypatch.setattr(debit, "_repli_signale", False)
    monkeypatch.setattr(debit, "_attente_commune", {})
    monkeypatch.setitem(debit.QUOTAS, "essai.example", (2.0, 3))
    monkeypatch.delenv("VIGIE_DEBIT_DIR", raising=False)
    return faux


def test_un_hote_sans_quota_n_attend_jamais(horloge):
    for _ in range(50):
        assert debit.attendre("example.org") == 0.0
    assert horloge.attentes == []


def test_la_rafale_passe_puis_le_rythme_publie(horloge):
    attentes = [debit.attendre("essai.example") for _ in range(6)]
    assert attentes == [0.0, 0.0, 0.0, 0.5, 1.0, 1.5]


def test_le_seau_se_remplit_avec_le_temps(horloge):
    for _ in range(4):
        debit.attendre("essai.example")
    horloge.maintenant += 10
    assert debit.attendre("essai.example") == 0.0


def test_le_nom_d_hote_se_lit_sans_casse_ni_point_final(horloge):
    for _ in range(3):
        debit.attendre("ESSAI.example.")
    assert debit.attendre("essai.example") == 0.5


def test_l_etat_se_partage_par_le_dossier(horloge, tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    for _ in range(3):
        debit.attendre("essai.example")
    # Un autre processus : rien en mémoire, tout dans le fichier.
    monkeypatch.setattr(debit, "_etat_local", {})
    assert debit.attendre("essai.example") == 0.5
    assert oct((tmp_path / "essai.example.tat").stat().st_mode & 0o777) == "0o660"


def test_une_date_absurde_ne_bloque_pas_les_autres(horloge, tmp_path, monkeypatch):
    """Fichier abîmé ou écrit par un autre compte : remis à zéro, pas obéi."""
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    (tmp_path / "essai.example.tat").write_text(repr(horloge.maintenant + 86_400))
    assert debit.attendre("essai.example") == 0.0
    (tmp_path / "essai.example.tat").write_text("n'importe quoi")
    horloge.maintenant += 10
    assert debit.attendre("essai.example") == 0.0


def test_un_dossier_inutilisable_retombe_sur_le_processus_en_le_disant(horloge, tmp_path,
                                                                       monkeypatch, capsys):
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path / "absent"))
    attentes = [debit.attendre("essai.example") for _ in range(4)]
    assert attentes[-1] == 0.5
    erreurs = capsys.readouterr().err
    assert erreurs.count("[debit]") == 1
    assert "inutilisable" in erreurs


def test_l_attente_s_ecrit_au_journal_de_la_demande(horloge, capsys):
    """Le même événement que l'ancien verrou du portail : vigie_bilan_generation le lit."""
    for _ in range(5):
        debit.attendre("essai.example")
    lignes = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert [l["event"] for l in lignes] == ["hote_en_attente"]   # 0,5 s ne vaut pas une ligne
    assert lignes[0]["hote"] == "essai.example" and lignes[0]["attenteS"] == 1.0
    assert horloge.attentes == [0.5, 1.0]


def test_la_resolution_de_nom_passe_par_le_debit(monkeypatch):
    vus = []
    monkeypatch.setattr(debit, "attendre", lambda hote: vus.append(hote) or 0.0)
    reseau.garder()
    try:
        with pytest.raises(reseau.AdresseInterdite):
            reseau.socket.getaddrinfo("127.0.0.1", 80)
    finally:
        reseau.relacher()
    assert vus == ["127.0.0.1"]


def test_seul_exclut_les_autres_processus(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    with debit.seul("overpass-api.de"):
        # Une autre ouverture du fichier : ce que ferait un autre processus.
        fd = os.open(tmp_path / "overpass-api.de.seul", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    fd = os.open(tmp_path / "overpass-api.de.seul", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_un_etat_garde_ailleurs_n_arrete_personne(horloge, tmp_path, monkeypatch, capsys):
    """Un processus planté, ou un compte compromis, qui garde le verrou."""
    import fcntl   # POSIX seul : la collecte des essais doit passer sous Windows
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    monkeypatch.setattr(debit, "DELAI_ETAT_S", 0.1)
    fd = os.open(tmp_path / "essai.example.tat", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert debit.attendre("essai.example") == 0.0
    finally:
        os.close(fd)
    assert "gardé ailleurs" in capsys.readouterr().err


def test_seul_ne_s_attend_pas_sans_fin(tmp_path, monkeypatch, capsys):
    import fcntl
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    monkeypatch.setattr(debit, "DELAI_SEUL_S", 0.1)
    fd = os.open(tmp_path / "overpass-api.de.seul", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with debit.seul("overpass-api.de"):
            pass
    finally:
        os.close(fd)
    assert "gardé ailleurs" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="pas de tube nommé")
def test_un_tube_a_la_place_de_l_etat_ne_casse_aucune_requete(horloge, tmp_path,
                                                              monkeypatch, capsys):
    """Revue sécurité P6 : un tube posé par un autre compte faisait échouer
    `getaddrinfo` vers cet hôte, pour tous, tant qu'il restait là."""
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    os.mkfifo(tmp_path / "essai.example.tat")
    attentes = [debit.attendre("essai.example") for _ in range(4)]
    assert attentes[-1] == 0.5   # la borne tient, dans ce processus
    assert "fichier ordinaire" in capsys.readouterr().err


def test_un_lien_dur_n_est_pas_suivi(horloge, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    autre = tmp_path / "fichier-d-un-autre"
    autre.write_text("intact")
    os.link(autre, tmp_path / "essai.example.tat")
    debit.attendre("essai.example")
    assert autre.read_text() == "intact"
    assert "lien unique" in capsys.readouterr().err


def test_au_dela_du_budget_l_etat_commun_n_est_plus_suivi(horloge, tmp_path,
                                                         monkeypatch, capsys):
    """Un compte qui pose sans cesse une date lointaine coûte au plus
    `BUDGET_COMMUN_S` aux autres processus."""
    monkeypatch.setenv("VIGIE_DEBIT_DIR", str(tmp_path))
    monkeypatch.setattr(debit, "BUDGET_COMMUN_S", 100)
    etat = tmp_path / "essai.example.tat"
    # Juste sous le plafond : tenue pour une vraie file, et payée une fois.
    etat.write_text(repr(horloge.maintenant + 1.5 + 299))
    assert debit.attendre("essai.example") > 100
    assert "borne tenue dans ce seul processus" in capsys.readouterr().err
    etat.write_text(repr(horloge.maintenant + 1.5 + 299))
    assert debit.attendre("essai.example") == 0.0


def test_seul_refuse_un_nom_qui_sortirait_du_dossier():
    with pytest.raises(ValueError):
        debit.seul("../etc/passwd")


def test_aucune_reservation_ne_se_perd_entre_processus(tmp_path):
    """Quatre processus, vingt-cinq réservations chacun, sous `flock`.

    Un intervalle de 1 000 s et une rafale immense : personne n'attend, et la
    date finale dit exactement combien de réservations ont été comptées.
    """
    racine = Path(debit.__file__).resolve().parent.parent
    code = ("from collectors import debit\n"
            "debit.QUOTAS['essai.example'] = (0.001, 10**9)\n"
            "for _ in range(25):\n"
            "    debit.attendre('essai.example')\n")
    env = {**os.environ, "VIGIE_DEBIT_DIR": str(tmp_path), "PYTHONPATH": str(racine)}
    debut = __import__("time").time()
    processus = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=racine)
                 for _ in range(4)]
    assert all(p.wait(timeout=60) == 0 for p in processus)
    tat = float((tmp_path / "essai.example.tat").read_text())
    assert 100 * 1000 <= tat - debut <= 100 * 1000 + 60
