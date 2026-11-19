"""garde_perimetre.py : ce qu'une session exécutante peut écrire et lancer.

Cas tirés de la relecture sécurité de la PR #250 (11/11/2026) : zones fermées
jugées avant les globs, casse, Bash (code en ligne, lecture seule hors du
worktree, heredoc et $( ) tolérés hors des zones protégées), brief non
réécrivable, forme unique du push, échec fermé.

Sans base ni réseau : un faux dossier personnel (mémoire et briefs) et un faux
worktree, rangés SOUS le dépôt et non sous /tmp, que la garde tient pour un
dossier temporaire où tout est permis.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "garde_perimetre.py"
if not HOOK.is_file():   # le miroir public ne porte pas .claude/
    pytest.skip("garde_perimetre.py absent (miroir public)", allow_module_level=True)

SLUG = "zz-essai-garde"
MEMOIRE_REL = ".claude-vigie/projects/-Users-juliencolin-Claude-vigie-civique/memory"
M = "~/" + MEMOIRE_REL
T = "/private/tmp/claude-501/x"
BRIEF_NORMAL = f"""---
slug: {SLUG}
modele: haiku
branche: {SLUG}
perimetre:
  - public/src/routes/zz/**
  - '*.py'
  - '*.md'
  - .github/workflows/zz.yml
critere_fin: e
---
essai
"""
BRIEF_AUTRE = """---
slug: zz-essai-autre
modele: haiku
branche: zz-essai-autre
perimetre:
  - public/src/lib/occupe.js
critere_fin: e
---
essai
"""


@pytest.fixture(scope="module")
def terrain():
    base = Path(tempfile.mkdtemp(prefix=".garde-perimetre-", dir=ROOT)).resolve()
    if str(base).startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")):
        shutil.rmtree(base)
        pytest.skip("dépôt sous un dossier temporaire : la garde y permet tout")
    home = base / "home"
    briefs = home / MEMOIRE_REL / "briefs"
    briefs.mkdir(parents=True)
    wt = home / "Claude" / f"vigie-wt-{SLUG}"
    wt.mkdir(parents=True)
    (briefs / "zz-essai-autre.md").write_text(BRIEF_AUTRE)
    yield {"home": str(home), "wt": str(wt), "briefs": briefs}
    shutil.rmtree(base)


def poser_brief(terrain, texte=BRIEF_NORMAL):
    (terrain["briefs"] / f"{SLUG}.md").write_text(texte)


def env_propre(**pose):
    """Environnement hérité de la session, sans ce que la garde lit : VIGIE_*,
    CLAUDE_PROJECT_DIR et TMPDIR. Un essai pose lui-même ce qu'il attend ;
    PATH et le reste (ce dont python3 a besoin) passent."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("VIGIE_") and k not in ("CLAUDE_PROJECT_DIR", "TMPDIR")}
    env.update(pose)
    return env


def lancer(terrain, outil=None, valeur=None, brut=None, sans_projet=False, **plus):
    env = env_propre(HOME=terrain["home"], VIGIE_ROLE="executant", VIGIE_CHANTIER=SLUG,
                     CLAUDE_PROJECT_DIR=terrain["wt"], TMPDIR=T, **plus)
    if sans_projet:
        env.pop("CLAUDE_PROJECT_DIR")
    if brut is None:
        entree = {"command": valeur} if outil == "Bash" else {"file_path": valeur}
        brut = json.dumps({"tool_name": outil, "tool_input": entree})
    r = subprocess.run([sys.executable, str(HOOK)], input=brut, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return "non" if '"deny"' in r.stdout else "ok"


def remplir(terrain, gabarit: str) -> str:
    brief = str(terrain["briefs"] / f"{SLUG}.md")
    return (gabarit.replace("{WT}", terrain["wt"]).replace("{H}", terrain["home"])
            .replace("{M}", M).replace("{T}", T).replace("{BRIEF}", brief))


# (point de la relecture, attendu, outil, commande ou chemin)
# {WT} worktree, {H} dossier personnel, {M} mémoire (~), {T} temporaire, {BRIEF} ce brief
CAS = [
    # ── point 1 : zones fermées avant les globs, préfixe littéral
    ("1", "non", "Write", "{WT}/.claude/hooks/garde_perimetre.py"),      # ouvert à tort par '*.py' avant
    ("1", "non", "Write", "{WT}/CLAUDE.md"),                               # ouvert à tort par '*.md' avant
    ("1", "non", "Write", "{WT}/public/.claude/settings.json"),
    ("1", "non", "Write", "{WT}/public/CLAUDE.md"),
    ("1", "non", "Write", "{WT}/.mcp.json"),
    ("1", "non", "Write", "{WT}/.git/hooks/pre-commit"),
    ("1", "non", "Write", "{WT}/.github/workflows/autre.yml"),
    ("1", "ok",  "Write", "{WT}/.github/workflows/zz.yml"),               # glob qui commence par .github/
    ("1", "non", "Write", "{WT}/dossiers/eau.md"),
    ("1", "ok",  "Write", "{WT}/scripts/x.py"),
    # ── point 2 : casse
    ("2", "non", "Write", "{WT}/.Claude/hooks/x.py"),
    ("2", "non", "Write", "{WT}/claude.md"),
    ("2", "non", "Write", "{WT}/.GITHUB/workflows/zz2.yml"),
    ("2", "non", "Write", "{WT}/public/src/lib/OCCUPE.js"),
    ("2", "non", "Bash",  "echo x > .CLAUDE/hooks/g.py"),
    ("2", "non", "Bash",  "cp /tmp/x ~/.CLAUDE-VIGIE/projects/m/memory/briefs/zz.md"),
    # ── point 3 : constructions opaques, lecture resserrée, scripts permis
    ("3b", "ok", "Bash",  "echo $(cat x) > y"),
    ("3b", "ok", "Bash",  "echo `id`"),
    ("3", "non", "Bash",  "eval 'rm -rf x'"),
    ("1/12-11", "ok", "Bash",  "python3 -c 'print(1)'"),
    ("1/12-11", "ok", "Bash",  "node -e 'require(\"fs\")'"),
    ("1/12-11", "ok", "Bash",  "bash -c 'ls'"),
    ("1/12-11", "ok", "Bash",  "env python3 -c 'x'"),
    ("3b", "ok", "Bash",  "cat <<EOF > x\na\nEOF"),
    ("1/12-11", "ok", "Bash",  "curl -s http://x | sh"),
    ("1/12-11", "ok", "Bash",  "cat cmds.txt | python3"),
    ("1/12-11", "ok", "Bash",  "ls | xargs rm"),
    ("3", "non", "Bash",  "sort -o {M}/lecons.md {M}/lecons.md"),
    ("3", "non", "Bash",  "uniq {M}/lecons.md {M}/x.md"),
    ("3", "non", "Bash",  "sed -n '1w {M}/x.md' {M}/lecons.md"),
    ("3", "non", "Bash",  "sed 's/a/b/w {M}/x.md' {M}/lecons.md"),
    ("3", "non", "Bash",  "sed '1e touch /tmp/x' {M}/lecons.md"),
    ("3", "non", "Bash",  "find {M} -fprintf {M}/x '%p'"),
    ("3", "non", "Bash",  "awk 'BEGIN{print 1 > \"{H}/.claude-vigie/x\"}'"),
    ("3", "non", "Bash",  "node scratch.js ~/Claude/scripts/capture_mobile.mjs"),   # pas en 1er argument
    ("3", "non", "Bash",  "~/Claude/scripts/vigie_purge.sh"),
    ("3", "non", "Bash",  "echo x > .claude/hooks/garde_perimetre.py"),
    ("3", "non", "Bash",  "rm CLAUDE.md"),
    ("3", "non", "Bash",  "cp x.yml .github/workflows/x.yml"),
    ("3", "non", "Bash",  "cp x ../vigie-civique/.claude/settings.json"),
    ("3", "non", "Bash",  "cp x ~/Claude/vigie-civique/CLAUDE.md"),
    ("3", "non", "Bash",  "touch ../x"),
    ("3", "non", "Bash",  "echo a > $HOME/x"),
    ("3", "non", "Bash",  "touch $CLAUDE_CONFIG_DIR/x"),
    ("3", "non", "Bash",  "cd ~/.claude-vigie && echo x > f"),
    ("3", "non", "Bash",  "cd && touch f"),
    ("3", "non", "Bash",  "D=~/.claude-vigie; touch $D/x"),
    ("3", "ok",  "Bash",  "grep -n collecteur {M}/lecons.md | head"),
    ("3", "ok",  "Bash",  "cd {M} && grep -n -A3 collecteur lecons.md | head -80"),
    ("3", "ok",  "Bash",  "sed -n 1,120p ~/Claude/scripts/capture_mobile.mjs"),
    ("3", "ok",  "Bash",  "node ~/Claude/scripts/capture_mobile.mjs http://localhost:5173/dossiers/eau {T}/a.png"),
    ("3", "ok",  "Bash",  "git status -sb && ~/Claude/scripts/sessions_actives.py 2>/dev/null | head -30"),
    ("3", "ok",  "Bash",  "grep -rnE '#[0-9a-f]{3}\\b' app/*.tsx | grep -v 'href=\"#\\|ancre' | awk '{print $1}' | head"),
    ("3", "ok",  "Bash",  "~/venvs/vigie/bin/python -m pytest tests/test_x.py -q 2>&1 | tail -5"),
    ("3", "ok",  "Bash",  "cd public && npm run build 2>&1 | tail -3"),
    ("3", "ok",  "Bash",  "git add public/src/routes/zz/a.svelte && git commit -q -F {T}/msg.txt"),
    ("3", "ok",  "Bash",  "gh pr create --draft --title 'Accueil : tuiles' --body-file {T}/pr.md"),
    ("3", "ok",  "Bash",  "git -C ~/Claude/vigie-civique log --oneline -3"),
    ("3", "ok",  "Bash",  "python3 scripts/build.py --out /tmp/claude-501/x/out.json"),
    ("3", "ok",  "Bash",  "ls .claude/hooks && cat CLAUDE.md | head"),
    ("3", "ok",  "Bash",  "grep -rn '#[0-9a-f]{3}' app | grep -v 'href={`#' | head"),
    ("3", "ok",  "Bash",  "~/venvs/vigie/bin/python -m pytest tests -q -p no:warnings 2>&1 | tail -3"),
    ("3", "ok",  "Bash",  "source ~/venvs/vigie/bin/activate && python -m pytest -q"),
    ("3", "non", "Bash",  "source ~/.claude-vigie/x.sh"),
    ("3", "non", "Bash",  ". /tmp/x.sh"),
    ("3", "ok",  "Bash",  "grep -rn source src | head; find . -name '*.py' | head"),
    ("3", "ok",  "Bash",  "cd public && npm ci 2>&1 | tail -3; cd ../public && npm test; cd .. && git status --short"),
    ("3", "non", "Bash",  "cd public && cd ../.. && touch x"),
    ("3", "ok",  "Bash",  "sed -i '' -e 's/^  .tuile { display: block;/  .marque .tuile { display: block;/' public/src/routes/zz/a.svelte"),
    ("3", "ok",  "Bash",  "cat ~/Claude/vigie-civique/CLAUDE.md | head; npm run build"),
    ("3b", "ok", "Bash",  "echo \"$(id)\""),
    ("1/12-11", "ok", "Bash",  "python3 - < /tmp/x.py | python3"),
    ("3", "ok",  "Bash",  "curl -s https://x/api | jq '.results[0]'"),
    # ── 3b : heredoc et $( ) tolérés hors des zones protégées (choix du 11/11)
    ("3b", "ok",  "Bash", "git commit -q -m \"$(cat <<'EOF'\nAccueil : tuiles (1) ; voir .claude/ et ~/Claude/scripts\n\nCo-Authored-By: x\nEOF\n)\""),
    ("3b", "ok",  "Bash", "S=/private/tmp/claude-501/x; cat > $S/pr.md <<'EOF'\n## Ce qui change\n- a | b ; c\nEOF\ngh pr create --draft --body-file $S/pr.md"),
    ("3b", "ok",  "Bash", "N=$(git rev-list --count origin/main..HEAD); echo $N"),
    ("1/12-11", "ok",  "Bash", "python3 - <<'EOF'\nprint(1)\nEOF"),
    ("1/12-11", "ok",  "Bash", "cd public && node <<'EOF'\nconsole.log(1)\nEOF"),
    ("1/12-11", "ok",  "Bash", "python3 <<< 'print(1)'"),
    ("1/12-11", "ok",  "Bash", "echo $(python3 -c 'print(1)')"),
    ("1/12-11", "ok",  "Bash", "x=$(curl -s u | python3)"),
    ("3b", "non", "Bash", "echo $(cat ~/.claude-vigie/projects/m/memory/lecons.md) > notes.txt"),
    ("3b", "non", "Bash", "cat > ~/.claude-vigie/projects/m/memory/x.md <<'EOF'\na\nEOF"),
    ("3b", "non", "Bash", "cat > .claude/hooks/x.py <<'EOF'\na\nEOF"),
    ("3b", "non", "Bash", "cat ~/Claude/scripts/a.py; cat <<'EOF' > x\na\nEOF"),
    ("3b", "non", "Bash", "echo `cat ../x`"),
    ("3b", "non", "Bash", "diff <(cat ~/.claude-vigie/x) y > /tmp/claude-1/d"),
    ("3b", "non", "Bash", "echo $(git push origin main)"),
    ("3b", "non", "Bash", "D=$(echo ~/.claude-vigie); touch $D/x"),
    ("3b", "non", "Bash", "D=$(dirname x); touch $D/x"),
    # ── renvoi du 12/11 : 1. code en ligne jugé comme toute commande
    ("1/12-11", "non", "Bash", "python3 -c \"open('{H}/.claude-vigie/x','w').write('a')\""),
    ("1/12-11", "non", "Bash", "node -e \"require('fs').writeFileSync('../x','a')\""),
    # même une lecture : la garde ne sait pas ce que fait le code, python3 n'est pas `cat`
    ("1/12-11", "non", "Bash", "python3 -c \"print(open('{H}/.claude-vigie/x').read())\" | head"),
    # ── 2. git add n'est pas une lecture
    ("2/12-11", "non", "Bash", "git -C ../vigie-civique add ."),
    ("2/12-11", "non", "Bash", "git -C ~/Claude/vigie-civique add -A"),
    ("2/12-11", "non", "Bash", "git add .claude/hooks/x.py"),
    ("2/12-11", "ok",  "Bash", "git add public/src/routes/zz/a.svelte && git status --short"),
    ("2/12-11", "ok",  "Bash", "git -C ~/Claude/vigie-civique status --short"),
    # ── 3. déploiement : sur le nom de ce qui est lancé
    ("3/12-11", "ok",  "Bash", "grep -rn deployer scripts/ | head"),
    ("3/12-11", "ok",  "Bash", "rg -n 'mettre_en_ligne|vigie_maj_instance' ."),
    ("3/12-11", "non", "Bash", "./scripts/vigie_atelier_deployer.sh lasalle"),
    ("3/12-11", "non", "Bash", "bash scripts/vigie_atelier_deployer.sh"),
    ("3/12-11", "non", "Bash", "python3 scripts/mettre_en_ligne.py"),
    ("3/12-11", "non", "Bash", "vigie_maj_instance.sh lasalle"),
    ("3/12-11", "non", "Bash", "echo $(./deployer.sh)"),
    ("3b", "ok",  "Bash", "git add -A && git commit -qm 'CI : kit sans .claude/ ni CLAUDE.md'"),
    ("3b", "ok",  "Bash", "gh pr create --draft --title 'Garde : .claude/ fermé' --body-file /tmp/claude-1/pr.md"),
    ("3b", "non", "Bash", "git -C ~/.claude-vigie commit -m x"),
    ("3b", "ok",  "Bash", "S=/private/tmp/claude-1; for n in a b; do for f in x y; do cp $S/pub-$n/$f public/src/routes/zz/; done; done"),
    ("3", "ok",  "Bash",  "S=/private/tmp/claude-501/x; for f in com dep; do sips -s format png $S/$f.webp --out $S/$f.png; done"),
    ("3", "non", "Bash",  "for d in ../vigie-civique ../Saillans; do touch $d/x; done"),
    ("3", "ok",  "Bash",  "for d in ../vigie-civique ../Saillans; do ls $d; done"),
    ("3", "ok",  "Bash",  "B=https://data.gouv.fr/api; for d in a b; do curl -s $B/$d -o /tmp/claude-1/$d.json; done"),
    ("3", "ok",  "Bash",  "gh pr view 244 --json body -q .body > \"$TMPDIR/body.md\" && sed -n 50,80p \"$TMPDIR/body.md\""),
    ("3", "non", "Bash",  "cp ~/Claude/vigie-civique/config/instance.json config/instance.json"),
    # ── point 4 : le brief n'est pas réécrivable par Bash
    ("4", "non", "Bash",  "echo 'perimetre_strict: non' >> {BRIEF}"),
    ("4", "non", "Bash",  "sed -n '1w {BRIEF}' /tmp/claude-501/x/faux.md"),
    ("4", "non", "Bash",  "sort -o {BRIEF} {T}/faux.md"),
    ("4", "non", "Bash",  "cp {T}/faux.md {BRIEF}"),
    ("4", "non", "Bash",  "cat {T}/faux.md | tee {BRIEF}"),
    ("4", "non", "Write", "{BRIEF}"),
    # ── point 5 : push
    ("5", "ok",  "Bash",  "git push --force-with-lease origin zz-essai-garde 2>&1 | tail -3"),
    ("5", "ok",  "Bash",  "git push -u origin zz-essai-garde"),
    ("5", "non", "Bash",  "git push --force-with-lease origin zz-essai-garde main"),
    ("5", "non", "Bash",  "git push origin zz-essai-garde:main"),
    ("5", "non", "Bash",  "git push origin HEAD:refs/heads/main"),
    ("5", "non", "Bash",  "git push origin +zz-essai-garde"),
    ("5", "non", "Bash",  "git push --delete origin zz-essai-garde"),
    ("5", "non", "Bash",  "git push origin :zz-essai-autre"),
    ("5", "non", "Bash",  "git push origin zz-essai-autre"),
    ("5", "non", "Bash",  "git push --force origin zz-essai-garde"),
    ("5", "non", "Bash",  "git push --force-with-lease=main:abc origin zz-essai-garde"),
    ("5", "non", "Bash",  "git -C ../vigie-civique push origin zz-essai-garde"),
    ("5", "non", "Bash",  "git push"),
    ("5", "non", "Bash",  "git push origin MAIN"),
    # ── autres refus conservés
    ("-", "non", "Bash",  "ssh vigie ls"),
    ("-", "non", "Bash",  "gh pr merge 3"),
    ("-", "non", "Bash",  "gh api -X DELETE repos/o/r/git/refs/heads/x"),
    ("-", "non", "Bash",  "git -c core.hooksPath=/tmp status"),
    ("-", "non", "Bash",  "git checkout main"),
    ("-", "non", "Write", "{H}/Claude/vigie-civique/x.py"),
    ("-", "ok",  "Write", "/private/tmp/claude-501/x/y"),
    ("-", "non", "Write", "{WT}/public/src/lib/occupe.js"),
    ("-", "ok",  "Write", "{WT}/public/src/routes/zz/a.svelte"),
    ("-", "ok",  "Write", "{WT}/tests/test_zz.py"),
]


@pytest.mark.parametrize("point,attendu,outil,gabarit", CAS,
                         ids=[f"{c[0]}-{c[1]}-{i}" for i, c in enumerate(CAS)])
def test_cas(terrain, point, attendu, outil, gabarit):
    poser_brief(terrain)
    assert lancer(terrain, outil, remplir(terrain, gabarit)) == attendu


def test_push_refuse_si_le_brief_n_a_pas_de_branche(terrain):
    poser_brief(terrain, f"---\nslug: {SLUG}\nmodele: haiku\nperimetre:\n  - x/**\ncritere_fin: e\n---\n")
    assert lancer(terrain, "Bash", f"git push origin {SLUG}") == "non"


def test_perimetre_strict_refuse_hors_des_globs(terrain):
    poser_brief(terrain, f"---\nslug: {SLUG}\nmodele: haiku\nbranche: {SLUG}\n"
                         "perimetre_strict: oui\nperimetre:\n  - x/**\ncritere_fin: e\n---\n")
    assert lancer(terrain, "Write", terrain["wt"] + "/tests/test_zz.py") == "non"
    assert lancer(terrain, "Write", terrain["wt"] + "/x/a.py") == "ok"


@pytest.mark.parametrize("brut", [
    "pas du json",
    json.dumps({"tool_name": "Bash", "tool_input": {"command": ["liste", "au lieu de texte"]}}),
])
def test_une_garde_qui_plante_refuse(terrain, brut):
    poser_brief(terrain)
    assert lancer(terrain, brut=brut) == "non"


def test_sans_worktree_connu_bash_refuse(terrain):
    poser_brief(terrain)
    assert lancer(terrain, "Bash", "ls", sans_projet=True) == "non"


def test_hors_du_role_executant_rien_n_est_controle(terrain):
    poser_brief(terrain)
    env = env_propre(HOME=terrain["home"], VIGIE_ROLE="")
    r = subprocess.run([sys.executable, str(HOOK)], env=env, capture_output=True, text=True,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}}))
    assert r.returncode == 0 and r.stdout.strip() == ""


# ── 4. Empreinte du brief (renvoi du 12/11/2026) ─────────────────────────────

def empreinte(terrain) -> str:
    return hashlib.sha256((terrain["briefs"] / f"{SLUG}.md").read_bytes()).hexdigest()


def test_brief_inchange_rien_ne_change(terrain):
    poser_brief(terrain)
    sha = empreinte(terrain)
    assert lancer(terrain, "Write", terrain["wt"] + "/public/src/routes/zz/a.svelte",
                  VIGIE_BRIEF_SHA256=sha) == "ok"
    assert lancer(terrain, "Bash", "ls", VIGIE_BRIEF_SHA256=sha) == "ok"


@pytest.mark.parametrize("outil,valeur", [
    ("Write", "{WT}/public/src/routes/zz/a.svelte"),
    ("Bash", "ls"),
    ("Edit", "{WT}/tests/test_zz.py"),
])
def test_brief_reecrit_tout_est_refuse(terrain, outil, valeur):
    poser_brief(terrain)
    sha = empreinte(terrain)
    # un script de l'exécutante élargit son propre périmètre
    poser_brief(terrain, BRIEF_NORMAL.replace("perimetre:\n", "perimetre:\n  - '**'\n"))
    assert lancer(terrain, outil, remplir(terrain, valeur), VIGIE_BRIEF_SHA256=sha) == "non"


def test_brief_supprime_tout_est_refuse(terrain):
    poser_brief(terrain)
    sha = empreinte(terrain)
    (terrain["briefs"] / f"{SLUG}.md").unlink()
    assert lancer(terrain, "Bash", "ls", VIGIE_BRIEF_SHA256=sha) == "non"


# ── 5. Environnement parasite : le résultat ne dépend pas de la session ──────

@pytest.fixture
def session_parasite(monkeypatch):
    """Ce que porte une session exécutante : empreinte d'un autre brief, TMPDIR
    ailleurs, autre chantier et autre worktree."""
    monkeypatch.setenv("VIGIE_BRIEF_SHA256", "0" * 64)
    monkeypatch.setenv("TMPDIR", "/private/tmp/claude-501/parasite/")
    monkeypatch.setenv("VIGIE_CHANTIER", "zz-autre-session")
    monkeypatch.setenv("VIGIE_ROLE", "executant")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/nulle/part")


@pytest.mark.parametrize("attendu,outil,gabarit", [
    ("ok", "Write", "{WT}/public/src/routes/zz/a.svelte"),
    ("ok", "Bash", "gh pr view 244 --json body -q .body > \"$TMPDIR/body.md\""),
    ("non", "Write", "{WT}/public/src/lib/occupe.js"),
    ("non", "Bash", "touch ../x"),
])
def test_environnement_parasite_ne_change_rien(terrain, session_parasite, attendu, outil, gabarit):
    poser_brief(terrain)
    assert lancer(terrain, outil, remplir(terrain, gabarit)) == attendu


def test_environnement_parasite_hors_role(terrain, session_parasite):
    poser_brief(terrain)
    env = env_propre(HOME=terrain["home"], VIGIE_ROLE="")
    assert "VIGIE_BRIEF_SHA256" not in env and "VIGIE_CHANTIER" not in env
    r = subprocess.run([sys.executable, str(HOOK)], env=env, capture_output=True, text=True,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}}))
    assert r.returncode == 0 and r.stdout.strip() == ""
