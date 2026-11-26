"""Le garde-fou compte exige le monde `cv` ET une connexion à l'organisation Team."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "garde_compte.py"
if not HOOK.is_file():   # le miroir public ne porte pas .claude/
    pytest.skip("garde_compte.py absent (miroir public)", allow_module_level=True)
TEAM = "1d0041bd-1889-4afb-a194-e8423fe222e3"


def verdict(tmp_path, organisation=None, vigie="team", remote=None, contenu=None):
    config = tmp_path / ".claude.json"
    if contenu is not None:
        config.write_text(contenu)
    elif organisation is not None:
        config.write_text(json.dumps({"oauthAccount": {"organizationUuid": organisation}}))
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIGIE_COMPTE", "CLAUDE_CODE_REMOTE")}
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path)
    if vigie:
        env["VIGIE_COMPTE"] = vigie
    if remote:
        env["CLAUDE_CODE_REMOTE"] = remote
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                       input="{}", env=env)
    return "deny" if '"deny"' in r.stdout else "ok"


def test_monde_cv_et_organisation_team_passe(tmp_path):
    assert verdict(tmp_path, TEAM) == "ok"


def test_login_pro_dans_une_session_cv_refuse(tmp_path):
    assert verdict(tmp_path, "00000000-0000-0000-0000-000000000000") == "deny"


def test_sans_alias_cv_refuse_meme_connecte_team(tmp_path):
    assert verdict(tmp_path, TEAM, vigie=None) == "deny"


@pytest.mark.parametrize("contenu", ["", "pas du json", "[]", "{}", '{"oauthAccount": null}'])
def test_connexion_illisible_refuse(tmp_path, contenu):
    assert verdict(tmp_path, contenu=contenu) == "deny"


def test_config_absente_refuse(tmp_path):
    assert verdict(tmp_path) == "deny"


def test_session_cloud_passe(tmp_path):
    assert verdict(tmp_path, vigie=None, remote="true") == "ok"
