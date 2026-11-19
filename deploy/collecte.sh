#!/bin/bash
# La passe de collecte d'une instance hébergée sur un serveur, puis sa
# republication si elle a trouvé du neuf — ou si quelqu'un a corrigé la base
# depuis la dernière publication. Lancée par `deploy/collecte.timer`.
#
# Le pendant, sur serveur, de la passe quotidienne qu'un poste de travail lance
# par launchd ou cron — mêmes trois temps, même règle de publication :
#
#   1. les sources PÉRIMÉES seulement (`scripts/collect_loop.py`, qui lit la
#      cadence de chaque collecteur dans `collector_runs`), jusqu'à stabilité ;
#   2. ce que la passe a ajouté, compté dans `collector_runs`, et ce qui a été
#      corrigé à la main depuis la dernière publication, lu dans `audit_log` ;
#   3. s'il y a du neuf OU une correction : aperçu, contrôle d'étanchéité, promotion, build, mise
#      en ligne, constat — `deploy/publier-site.sh --deployer`, le même chemin
#      que le bouton de l'atelier. Une violation du contrôle arrête tout : une
#      passe automatique publie sans relecture, la garde reste devant la porte.
#
#   4. si RAG_INDEX=1 (.env) : l'index de recherche par sens de l'atelier,
#      en incrémental (`scripts/build_rag_index.py --etat`). Après la
#      publication, et sans pouvoir l'arrêter : un échec se lit dans
#      `db/index_sens.json` et dans les alertes de `db/sante.json`.
#
#   VIGIE_SANS_PUBLIER=1 deploy/collecte.sh     # collecter sans publier
#
# Les collecteurs ne joignent que l'internet public (`collectors/reseau.py`,
# posé par `collect_loop.py`) : un serveur porte d'autres services sur sa boucle
# locale.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=${PY:-$ROOT/venv/bin/python3}
[ -x "$PY" ] || PY="$(command -v python3)"

echo "════ $(basename "$ROOT") — $(date '+%Y-%m-%d %H:%M %Z')"

# Repère en heure de la base (UTC, comme `datetime('now')`) : ce qui est ajouté
# APRÈS lui décide de la republication.
depart="$("$PY" -c "import datetime;print(datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S'))")"

echo "1/4 — Sources périmées"
statut=0
# `-u` : sortie non retenue. Le 08/11/2026, la passe de Lasalle tuée par manque de
# mémoire n'a laissé AUCUNE ligne au journal : Python l'écrivait par blocs.
"$PY" -u scripts/collect_loop.py --run --loop || statut=$?
[ "$statut" -eq 0 ] || echo "   ⚠ au moins un collecteur en échec (détail ci-dessus)"

echo "2/4 — Ce que la passe a ajouté"
ajouts="$("$PY" - "$depart" <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
from collectors.db import get_conn
conn = get_conn()
lignes = conn.execute(
    "SELECT collector, items_added FROM collector_runs"
    " WHERE finished_at >= ? AND COALESCE(items_added,0) > 0"
    " ORDER BY items_added DESC", (sys.argv[1],)).fetchall()
for r in lignes:
    print(f"   + {r['items_added']:>5}  {r['collector']}", file=sys.stderr)
print(sum(r["items_added"] for r in lignes))
PY
)"
echo "   total : ${ajouts:-0} élément(s) neufs"

# Une correction faite en base hors collecte (fusion de fiches, verdict, saisie)
# n'ajoute rien à `collector_runs` : la passe l'ignorait, et la correction
# attendait la prochaine arrivée de données pour sortir — des semaines sur une
# source rare. Le critère est le journal d'audit depuis la génération de
# l'aperçu publié (`scripts/publication.py`, qui dit aussi pourquoi pas un
# aperçu comparé). Ce qui part reste l'aperçu CONTRÔLÉ de `publier-site.sh` : le
# journal décide s'il faut publier, jamais de ce qui est publié.
corrections="$("$PY" - <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
from collectors.db import get_conn
from scripts import publication
par_table = publication.corrections_depuis_la_publication(get_conn(read_only=True))
for table, n in par_table.items():
    print(f"   ~ {n:>5}  {table}", file=sys.stderr)
print(sum(par_table.values()))
PY
)"
echo "   corrections depuis la dernière publication : ${corrections:-0} geste(s)"

if [ "${VIGIE_SANS_PUBLIER:-0}" = "1" ]; then
    echo "3/4 — Publication passée (VIGIE_SANS_PUBLIER=1)"
elif [ "${ajouts:-0}" -eq 0 ] && [ "${corrections:-0}" -eq 0 ]; then
    echo "3/4 — Rien de neuf, rien de corrigé : pas de republication."
else
    echo "3/4 — Aperçu, contrôle, promotion, build, mise en ligne, constat"
    # `--passe-auto` : sans regard humain, une baisse de volume forte refuse la
    # mise en service (garde de volume, `scripts/publication.py`).
    PY="$PY" deploy/publier-site.sh --deployer --passe-auto || {
        # Pas « le site sert toujours sa version précédente » : c'est vrai si
        # l'arrêt précède la mise en ligne, on n'en sait rien s'il la suit.
        echo "   ✖ publication interrompue — ce que le site sert n'est pas constaté."
        echo "     Le relire en ligne : $PY scripts/publication.py verifier"
        statut=1
    }
fi

# L'index se tient après la publication, qu'elle ait abouti ou non : il ne la
# retarde pas, et ne la bloque jamais. Son échec ne change pas le code de sortie
# de la passe, il se voit dans `sante.json`, réécrit ici.
if [ "${RAG_INDEX:-0}" = "1" ]; then
    echo "4/4 — Index de recherche par sens"
    "$PY" scripts/build_rag_index.py --etat \
        || echo "   ⚠ index non tenu — motif dans db/index_sens.json, alerte dans db/sante.json"
    "$PY" scripts/collect_loop.py --sante >/dev/null \
        || echo "   ⚠ sante.json non réécrit après l'indexation"
else
    echo "4/4 — Index de recherche par sens : non entretenu ici (RAG_INDEX=0)"
fi
exit "$statut"
