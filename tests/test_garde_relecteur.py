"""garde_relecteur.py : le relecteur de code lit, il n'écrit pas.

Le hook est lancé en sous-processus avec une entrée JSON, comme le fait Claude
Code. Une garde qui ne se déclenche jamais passe tous les essais « permis » :
chaque refus est donc un essai à part, et le cas « ce n'est pas le relecteur »
prouve que la même commande passe pour tout autre appelant.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "garde_relecteur.py"
if not HOOK.is_file():   # le miroir public ne porte pas .claude/
    pytest.skip("garde_relecteur.py absent (miroir public)", allow_module_level=True)

RELECTEUR = "vigie-relecteur-code"


def lancer(commande, agent_type=RELECTEUR, outil="Bash", brut=None):
    entree = {"hook_event_name": "PreToolUse", "tool_name": outil,
              "tool_input": {"command": commande}}
    if agent_type is not None:
        entree["agent_type"] = agent_type
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                       input=brut if brut is not None else json.dumps(entree), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def refuse(sortie):
    assert sortie is not None, "aucun refus"
    h = sortie["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "garde_relecteur" in h["permissionDecisionReason"]
    assert "lecture seule" in h["permissionDecisionReason"]


REFUSEES = [
    "gh pr merge 12 --merge",
    "gh pr comment 12 --body ok",
    "gh pr review 12 --approve",
    "gh pr close 12",
    "gh pr reopen 12",
    "gh pr edit 12 --title x",
    "gh pr ready 12",
    "gh pr merge 12 -R vigie-civique/moteur",
    "gh -R vigie-civique/moteur pr merge 12",
    "gh issue create --title x",
    "gh issue comment 3 --body x",
    "gh issue close 3",
    "gh api repos/o/r/issues/1/comments -f body=x",
    "gh api repos/o/r/issues/1/comments -F body=x",
    "gh api repos/o/r/pulls/1/merge -X PUT",
    "gh api repos/o/r/pulls/1 --method PATCH",
    "gh api repos/o/r/pulls/1 --method=DELETE",
    "gh api graphql --input q.json",
    "git push",
    "git push origin main",
    "git push --dry-run origin fusion-condition-6-et-garde-relecteur",
    "git commit -m x",
    "git merge origin/main",
    "git rebase origin/main",
    "git reset --hard HEAD~1",
    "git checkout main",
    "git switch main",
    "git stash",
    "git stash pop",
    "git tag v1",
    "git tag -d v1",
    "git branch nouvelle",
    "git branch -D vieille",
    "git -C /tmp/x push",
    "git -c core.editor=true commit",
    "git worktree add /tmp/w",
    "git cherry-pick abc123",
    # relevés par le poste sécurité (revue de la PR)
    "gh pr checkout 12",
    "gh pr update-branch 12",
    "gh pr create --fill",
    "gh pr revert 12",
    "gh repo edit --visibility public",
    "gh gist create diff.txt",
    "gh workflow run x.yml",
    "gh run rerun 5",
    "gh release create v1",
    "gh secret set X",
    "gh api x -fbody=y",
    "gh api x -XPOST",
    "gh api 'x?a=1&b=2' -X POST",
    "gh api x --jq '.a|.b' -X POST",
    "git -P push",
    "git --git-dir .git push",
    "git --work-tree . commit -m x",
    "git config core.hooksPath /tmp",
    "git bisect start",
    "git update-ref -d refs/heads/x",
    "git symbolic-ref HEAD refs/heads/x",
    "git remote set-url origin x",
    "git notes add -m x",
    "git clone x y",
    "git gc",
    "git branch -a -D x",
    "git branch -v -m a b",
    "git tag -a v1 -m x",
    "git branch -a nouvelle",
    "xargs git push",
    "git fetch origin +main:x",
    "git fetch --upload-pack=cmd origin",
    "git branch --set-upstream-to=origin/main",
    "git diff --output=/tmp/x",
    "git log --output=/tmp/x",
    "git diff --ext-diff",
    "git grep -O vi motif",
    "gh api x -iXPOST",
    "gh api x -if body=y",
    "git -c core.pager=cmd log",
    "git -c core.sshCommand=x log",
    "GIT_EXTERNAL_DIFF=cmd git diff",
    "env GIT_PAGER=cmd git log",
    "env GIT_X=1 git push",
    "/usr/bin/git push",
]

PERMISES = [
    "gh pr diff 12",
    "gh pr diff 12 -R vigie-civique/moteur --name-only",
    "gh pr view 12 --json body",
    "gh pr checks 12",
    "gh pr list",
    "gh issue view 3",
    "gh api repos/o/r/pulls/12/files",
    "gh api repos/o/r/pulls/12 -X GET",
    "gh api repos/o/r/pulls/12 --method GET",
    "git diff origin/main...HEAD",
    "git log --oneline -5",
    "git show abc123:fichier.py",
    "git status",
    "git grep -n garde",
    "git branch",
    "git branch --show-current",
    "git branch -a",
    "git tag --list",
    "git -P log -3",
    "git --no-pager diff",
    "git -C /srv/wt log",
    "git worktree list",
    "git remote -v",
    "GIT_PAGER=cat git log -1",
    "PAGER=cat git diff",
    "gh api x --method 'GET'",
    "gh api --paginate repos/o/r/pulls/12/files --jq '.[]|.filename,.previous_filename'",
    "git log -O ordre.txt",
    "gh auth status",
    "env -i git log -1",
    "git branch --contains abc123",
    "git branch -a --list 'fusion*'",
    "gh api 'repos/o/r/pulls/12/files?per_page=100' --jq '.[]|.filename,.previous_filename'",
    "gh search issues garde",
    "gh run view 5",
    "gh pr diff 12 | grep \"git push\"",
    "grep -n 'git commit' fichier.md",
    "timeout 60 git log -1",
    "if git diff --quiet; then echo propre; fi",
    "for f in a b; do git show HEAD:$f; done",
    "xargs -n 1 git show",
    "git diff --name-only | xargs grep -l garde",
    "pytest tests/test_x.py -k cas",
    "ls -la && cat README.md | head -5",
]

CACHEES = [
    "gh api x '-XPOST'",
    'gh api x "-X" POST',
    "gh api x '-fbody=y'",
    'gh api x "--method" PATCH',
    "gh api x '--input' q.json",
    "git grep --open=echo -e AGENT",
    "git grep --op=echo -e AGENT",
    "git grep --open-files=echo -e AGENT",
    "gh auth status --show-token",
    "gh auth status -t",
    "noglob git push",
    "nocorrect gh pr merge 12",
    "ssh vigie git pull",
    "ssh vigie 'ls'",
    "caffeinate -i git push",
    "caffeinate -d git commit -am x",
    "caffeinate -s gh pr merge 12",
    "env -i git push",
    "sudo -E git push",
    "sudo -i git push",
    "xargs -t git push",
    "xargs -o git push",
    "command -p git push",
    "exec -a x git push",
    "timeout --signal KILL 60 git push",
    "timeout --kill-after 5 60 git push",
    "xargs --max-args 1 git push",
    "sudo --user x git push",
    "env --unset X git push",
    "time -p git push",
    "GIT push",
    "Git push",
    "GH pr merge 12",
    "git grep -nOecho AGENT",
    "git grep -iO motif",
    "if git push; then :; fi",
    "! git push",
    "{ git push; }",
    "for i in 1; do git push; done",
    "while true; do gh pr merge 12; done",
    "timeout 60 git push",
    "xargs -n 1 git push",
    "env -u X git push",
    "sudo -u x git push",
    "time git commit -m x",
    "nohup nice -n 5 git push",
    "git status && git push",
    "git diff; git commit -am x",
    "git log | head -3 && gh pr merge 12",
    "gh pr view 12 | tee /dev/null; gh pr comment 12 --body x",
    "echo ok | gh pr review 12 --approve",
    "(git push)",
    "ls $(git push)",
    "git status\ngit push",
    "true || git reset --hard",
    "git diff `git checkout main`",
]


@pytest.mark.parametrize("commande", REFUSEES)
def test_commande_refusee(commande):
    refuse(lancer(commande))


@pytest.mark.parametrize("commande", PERMISES)
def test_commande_permise(commande):
    assert lancer(commande) is None


@pytest.mark.parametrize("commande", CACHEES)
def test_commande_refusee_cachee(commande):
    refuse(lancer(commande))


@pytest.mark.parametrize("agent", [None, "", "vigie-securite", "general-purpose",
                                   "vigie-relecteur-code-bis", "Vigie-Relecteur-Code"])
@pytest.mark.parametrize("commande", ["git push", "gh pr merge 12 --merge",
                                      "gh api x -X POST && git commit -m x"])
def test_autre_que_le_relecteur_passe(commande, agent):
    """Session principale, orchestratrice, exécutante, autre poste : jamais bloqués."""
    assert lancer(commande, agent_type=agent) is None


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="pas de python système")
def test_python_systeme():
    """La copie des gardes tourne parfois sous /usr/bin/python3 (3.9) : refus, pas traceback."""
    entree = json.dumps({"agent_type": RELECTEUR, "tool_name": "Bash",
                         "tool_input": {"command": "git push"}})
    r = subprocess.run(["/usr/bin/python3", str(HOOK)], capture_output=True, text=True,
                       input=entree, timeout=30)
    assert r.returncode == 0, r.stderr
    refuse(json.loads(r.stdout))


def test_hors_bash_passe():
    assert lancer("git push", outil="Read") is None


@pytest.mark.parametrize("brut", ["", "pas du json", "[]", "null",
                                  json.dumps({"agent_type": RELECTEUR, "tool_name": "Bash"}),
                                  json.dumps({"agent_type": RELECTEUR, "tool_name": "Bash",
                                              "tool_input": {"command": 5}})])
def test_entree_illisible_ne_bloque_personne(brut):
    assert lancer("x", brut=brut) is None
