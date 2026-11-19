"""Les polices admises dans l'archive (09/11/2026).

Les vigies servent les polices du portail (public/static/fonts). Comme les
icônes PNG, elles n'échappent pas au contrôle du kit : chacune est nommée, et
admise pour ses octets exacts (empreinte SHA-256). Rien d'autre ne passe sous
son nom : ni un autre WOFF2, ni un fichier qui en porte la signature, ni des
octets collés derrière.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_kit", ROOT / "scripts" / "build_kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

NOM = "public/static/fonts/essai.woff2"


def _woff2(corps: bytes = b"\x00" * 64, longueur: int | None = None) -> bytes:
    """Un en-tête WOFF2 minimal : signature, saveur, longueur totale, reste."""
    total = 12 + len(corps)
    declaree = total if longueur is None else longueur
    return b"wOF2" + b"\x00\x01\x00\x00" + declaree.to_bytes(4, "big") + corps


@pytest.fixture
def depot(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t.invalid")
    git("config", "user.name", "test")
    (tmp_path / "LISEZ-MOI.txt").write_text("dépôt d'essai\n")
    git("add", "-A")
    git("commit", "-q", "-m", "état initial")
    return tmp_path


def _verifier(depot, monkeypatch, octets: bytes, empreinte: str | None = None):
    f = depot / NOM
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(octets)
    subprocess.run(["git", "-C", str(depot), "add", "-f", NOM], check=True, capture_output=True)
    admises = {} if empreinte is False else {
        NOM: empreinte or hashlib.sha256(octets).hexdigest()}
    monkeypatch.setattr(kit, "FONTES_ADMISES", admises)
    return kit.verifier(depot, kit.fichiers(depot))


def test_une_police_nommee_et_conforme_est_admise(depot, monkeypatch):
    assert _verifier(depot, monkeypatch, _woff2()) == []


def test_une_police_non_nommee_reste_refusee(depot, monkeypatch):
    problemes = _verifier(depot, monkeypatch, _woff2(), empreinte=False)
    assert any("non contrôlable" in p for p in problemes), problemes


def test_une_autre_empreinte_est_refusee(depot, monkeypatch):
    problemes = _verifier(depot, monkeypatch, _woff2(), empreinte="0" * 64)
    assert any("empreinte" in p for p in problemes), problemes


def test_un_nom_admis_ne_couvre_pas_autre_chose_quun_woff2(depot, monkeypatch):
    problemes = _verifier(depot, monkeypatch, b"SQLite format 3\x00base")
    assert any("pas un WOFF2" in p for p in problemes), problemes


def test_des_octets_colles_derriere_sont_refuses(depot, monkeypatch):
    octets = _woff2() + b"SQLite format 3"
    problemes = _verifier(depot, monkeypatch, octets)
    assert any("longueur déclarée" in p for p in problemes), problemes


def test_une_police_trop_lourde_est_refusee(depot, monkeypatch):
    problemes = _verifier(depot, monkeypatch, _woff2(b"\x00" * (kit.FONTE_POIDS_MAX + 1)))
    assert any("plus que les" in p for p in problemes), problemes


def test_les_polices_admises_existent_et_passent():
    """La liste ne nomme que des fichiers présents, et chacun passe son contrôle."""
    assert len(kit.FONTES_ADMISES) == 5
    for rel, empreinte in sorted(kit.FONTES_ADMISES.items()):
        f = ROOT / rel
        assert f.is_file(), f"{rel} est admise mais absente du dépôt"
        assert kit.refus_fonte(f.read_bytes(), empreinte) is None, rel


def test_chaque_police_servie_est_admise():
    """Une police ajoutée dans static/fonts sans ligne dans FONTES_ADMISES
    ferait échouer le kit : autant le dire ici, plus tôt."""
    servies = {p.relative_to(ROOT).as_posix()
               for p in (ROOT / "public" / "static" / "fonts").glob("*.woff2")}
    assert servies == set(kit.FONTES_ADMISES)
