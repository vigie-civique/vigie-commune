"""ocrmypdf écrit son dossier temporaire dans l'instance, balayé au passage suivant.

Le 23/10/2026, une OCR lancée à la main sous le compte de l'atelier, puis
interrompue, a laissé 4,0 Gio de `ocrmypdf.io.*` dans /tmp du VPS : ocrmypdf ne
retire pas son dossier quand on le tue, et `PrivateTmp` ne vaut que pour les
unités systemd.
"""
from __future__ import annotations

import ast
import os
import pathlib
import time

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
MODULES = ("collectors/cm_ocr.py", "collectors/conseils.py")


def test_chaque_appel_a_ocrmypdf_passe_par_l_environnement_commun():
    for module in MODULES:
        arbre = ast.parse((RACINE / module).read_text())
        appels = [
            noeud for noeud in ast.walk(arbre)
            if isinstance(noeud, ast.Call) and noeud.args
            and isinstance(noeud.args[0], ast.List) and noeud.args[0].elts
            and isinstance(noeud.args[0].elts[0], ast.Constant)
            and noeud.args[0].elts[0].value == "ocrmypdf"
        ]
        assert appels, f"{module} : aucun appel à ocrmypdf trouvé — essai périmé"
        for appel in appels:
            env = [k.value for k in appel.keywords if k.arg == "env"]
            assert env and isinstance(env[0], ast.Call) \
                and getattr(env[0].func, "id", "") == "environnement_ocr", (
                    f"{module} : ocrmypdf lancé sans `env=environnement_ocr()` — "
                    f"son dossier temporaire retomberait dans /tmp")


def test_les_residus_anciens_partent_les_recents_restent(tmp_path, monkeypatch):
    """Job « tests-deps » : importer `cm_ocr` exige pdfplumber."""
    pytest.importorskip("pdfplumber",
                        reason="job « tests-deps » : pip install -r requirements.txt")
    from collectors import cm_ocr

    monkeypatch.setattr(cm_ocr, "TMP_OCR", tmp_path / "tmp-ocr")
    ancien = tmp_path / "tmp-ocr" / "ocrmypdf.io.ancien"
    recent = tmp_path / "tmp-ocr" / "ocrmypdf.io.en-cours"
    for dossier in (ancien, recent):
        dossier.mkdir(parents=True)
        (dossier / "page.png").write_bytes(b"x")
    vieux = time.time() - (cm_ocr.RESIDU_OCR_HEURES + 1) * 3600
    os.utime(ancien, (vieux, vieux))

    env = cm_ocr.environnement_ocr()

    assert env["TMPDIR"] == str(tmp_path / "tmp-ocr")
    assert not ancien.exists(), "le résidu d'une OCR interrompue est resté"
    assert recent.is_dir(), "une OCR peut-être en cours a perdu son dossier"
