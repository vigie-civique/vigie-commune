"""Le garde-fou données refuse les copies de bases rangées hors d'une instance."""
import importlib.util
import json
import os
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "garde_donnees.py"
if not HOOK.is_file():   # le miroir public ne porte pas .claude/
    pytest.skip("garde_donnees.py absent (miroir public)", allow_module_level=True)
HOME = Path.home()


def verdict(outil, entree, cwd="/tmp"):
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                       input=json.dumps({"tool_name": outil, "tool_input": entree, "cwd": cwd}))
    return "deny" if '"deny"' in r.stdout else "ok"


@pytest.mark.parametrize("chemin", [
    HOME / "Claude/.backups/atelier/lasalle/x-20261009.db",
    HOME / "Claude/.backups/vigie-collecte-20261025/81037.db",
    HOME / "Claude/Lasalle-v3/db/26000.db",
])
def test_read_d_une_copie_protegee_refuse(chemin):
    assert verdict("Read", {"file_path": str(chemin)}) == "deny"


def test_read_d_une_base_fictive_protegee_passe():
    assert verdict("Read", {"file_path": str(HOME / "Claude/.backups/synth.db")}) == "ok"


def test_read_hors_racine_protegee_passe(tmp_path):
    assert verdict("Read", {"file_path": str(tmp_path / "notes.txt")}) == "ok"


@pytest.mark.parametrize("prefixe", ["", "cd /tmp && "])
def test_bash_sur_une_copie_protegee_refuse(prefixe, monkeypatch, capsys):
    # La racine protégée est remplacée par un dossier du test : la base doit
    # exister pour que le hook la tienne pour nommée, ce que la CI n'a pas sous ~.
    spec = importlib.util.spec_from_file_location("garde_donnees", HOOK)
    garde = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOK.parent))
    spec.loader.exec_module(garde)
    # Hors de tmp_path : « test » dans le chemin ferait passer la base pour fictive.
    racine = Path(tempfile.mkdtemp(prefix="sauv-", dir="/tmp")).resolve()
    base = racine / "lasalle-20261009.db"
    base.write_bytes(b"SQLite format 3\0")
    monkeypatch.setattr(garde, "PROTEGES", (str(racine) + os.sep,))
    with pytest.raises(SystemExit):
        garde.controler_bash(f"{prefixe}sqlite3 {base} 'select 1'", "/tmp")
    sortie = capsys.readouterr().out
    shutil.rmtree(racine)
    assert '"deny"' in sortie
