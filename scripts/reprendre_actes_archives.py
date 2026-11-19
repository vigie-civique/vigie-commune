#!/usr/bin/env python3
"""Reprend, depuis les réponses ARCHIVÉES d'un portail d'actes, des actes qu'il ne liste plus.

    python3 scripts/reprendre_actes_archives.py --portee epci --liste audits/actes.txt
    python3 scripts/reprendre_actes_archives.py --portee epci --liste audits/actes.txt \\
        --appliquer --plan-relu audits/actes-simulation.tsv

À lancer depuis le dossier de l'instance, COLLECTE ARRÊTÉE. SIMULATION par défaut
(base en lecture seule) : le plan part dans un TSV (URL de documents publiés,
numéros d'actes, identifiants, décomptes ; aucun nom).

Pourquoi
--------
Saillans, 08/11/2026 : 42 PDF d'actes de la CC (juin 2026), classés « commune » par
le redécoupage du 29/08 et retirés par la reprise #230. Le portail d'actes de la CC
ne les LISTE plus, mais sert toujours les fichiers : `cc_epci`, qui part du
catalogue, ne peut pas les reprendre. Relus un par un par `conseils --url`, ils
ont été DÉCOUPÉS comme des procès-verbaux (95 fragments pour 42 actes) :
`conseils.traiter` ne passe par `traiter_acte` que si le document porte `acte`,
que seul le catalogue fournit.

Or les réponses de l'API du portail sont archivées (`collectors/archive.py`,
`data/raw/<domaine du portail>/`). Celles d'avant la disparition portent, pour
chaque acte, le numéro, l'objet et le type DÉCLARÉS par la collectivité. Ce
script rebâtit chaque document exactement comme le catalogue le faisait
(`dematdoc.document_publie`), sans deviner de titre, puis :
  - `relire` : retire les délibérations de cette portée qui citent l'URL (les
               fragments), puis relit l'acte par `conseils.traiter` → `traiter_acte`,
               DANS LA MÊME TRANSACTION : si la lecture ne rend pas `ok`, rien
               n'est retiré ;
  - `?`      : acte absent des réponses archivées, d'une autre corbeille que
               celle déclarée (un portail peut être mutualisé avec les communes),
               déclaré de deux façons, URL tenue par l'autre assemblée, fragment
               cité ailleurs (`accroches`) ou saisi dans l'atelier — à trancher.
`--appliquer` n'agit que si le plan recalculé est celui de `--plan-relu`.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.parse
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collectors import config, conseils  # noqa: E402
from collectors.archive import RAW_DIR  # noqa: E402
from collectors.connecteurs.dematdoc import (DECISIONS_DEFAUT, DELIBERATIONS_DEFAUT,  # noqa: E402
                                             document_publie)
from collectors.db import get_conn, transaction  # noqa: E402
from collectors.sauvegarde import sauvegarder  # noqa: E402
from detacher_pieces_autre_assemblee import accroches  # noqa: E402

AUTRE = {"commune": "epci", "epci": "commune"}
CHAMPS = ["action", "url", "date", "numero", "type", "corbeille", "fragments", "ids_fragments",
          "accroches", "motif"]


class _Annule(Exception):
    """La lecture d'un acte n'a pas rendu `ok` : sa transaction est annulée."""


def actes_archives(portail: str, motif_type: str = DELIBERATIONS_DEFAUT,
                   motif_decisions: str = DECISIONS_DEFAUT) -> dict[str, dict]:
    """{URL du PDF: {"doc", "corbeille", "declarations"}} d'après les réponses archivées.

    Seuls les types retenus par `catalogue_pv` sont gardés (même réglage). La
    corbeille vient du document lui-même (`parentDocType.id`) ; `declarations`
    compte les déclarations DISTINCTES (numéro, objet, type) d'un même fichier :
    plus d'une, l'acte est à trancher, l'ordre des fichiers ne fait pas foi
    (l'archive déduplique par empreinte, la date du fichier est la première vue).
    """
    parties = urllib.parse.urlsplit(portail)
    base = f"{parties.scheme}://{parties.netloc}"
    source = parties.netloc.lower().removeprefix("www.")
    corbeille = portail.rstrip("/")
    motif = re.compile(motif_type, re.I)
    # Exclusion mutuelle, comme `catalogue_pv` : une décision n'est jamais
    # reprise comme délibération, quel que soit le réglage.
    decisions = re.compile(motif_decisions, re.I)
    sorties: dict[str, dict] = {}
    dossier = RAW_DIR / source
    for fichier in sorted(dossier.glob("*.json"), key=lambda f: f.stat().st_mtime):
        try:
            rendu = json.loads(fichier.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(rendu, dict):
            continue
        for document in rendu.get("documents") or []:
            if not isinstance(document, dict):
                continue
            doc = document_publie(document, base, source, corbeille)
            if not (doc and motif.search(doc.acte.get("type") or "")) \
                    or decisions.search(doc.acte.get("type") or ""):
                continue
            parent = document.get("parentDocType")
            e = sorties.setdefault(doc.url, {"doc": doc, "declarations": set(),
                                             "corbeille": None})
            e["declarations"].add((doc.acte.get("numero"), doc.acte.get("objet"),
                                   doc.acte.get("type")))
            e["corbeille"] = parent.get("id") if isinstance(parent, dict) else None
    return sorties


def corbeilles_declarees(portail: str) -> set[int]:
    return {int(n) for n in re.findall(r"/(\d+)/?$", urllib.parse.urlsplit(portail).path)}


def plan(conn, urls: list[str], portee: str, archives: dict,
         corbeilles: set[int]) -> list[dict]:
    delib, autre = conseils.PORTEES[portee]["delib"], conseils.PORTEES[AUTRE[portee]]["delib"]
    lignes = []
    for url in urls:
        e = archives.get(url) or {}
        doc = e.get("doc")
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM events WHERE type=? AND source_url=? ORDER BY id", (delib, url))]
        tenu = conn.execute("SELECT COUNT(*) FROM events WHERE type=? AND source_url=?",
                            (autre, url)).fetchone()[0]
        saisies = conn.execute(
            "SELECT COUNT(*) FROM events WHERE type=? AND source_url=?"
            " AND COALESCE(origine,'') = 'atelier'", (delib, url)).fetchone()[0]
        nacc = sum(accroches(conn, i) for i in ids)
        motif = ("absent des réponses archivées" if doc is None
                 else "autre corbeille que celle déclarée" if e["corbeille"] not in corbeilles
                 else "déclaré de plusieurs façons" if len(e["declarations"]) > 1
                 else "tenu par l'autre assemblée" if tenu
                 else "saisie de l'atelier" if saisies
                 else "fragment cité ailleurs" if nacc else "")
        lignes.append({
            "action": "?" if motif else "relire", "url": url,
            "date": doc.date if doc else "", "numero": doc.acte.get("numero") if doc else "",
            "type": doc.acte.get("type") if doc else "",
            "corbeille": e.get("corbeille") if doc else "", "fragments": len(ids),
            "ids_fragments": " ".join(map(str, ids)), "accroches": nacc, "motif": motif})
    return lignes


def _cles(lignes) -> set[tuple[str, str, str]]:
    return {(l["action"], l["url"], str(l["ids_fragments"])) for l in lignes
            if l["action"] != "?"}


def appliquer(lignes: list[dict], portee: str, archives: dict) -> dict:
    """Une transaction par acte : retrait des fragments ET relecture, ou rien."""
    bilan: dict[str, int] = {}
    for l in lignes:
        if l["action"] != "relire":
            continue
        doc = archives[l["url"]]["doc"]
        # Lecture (et OCR, jusqu'à de longues minutes) HORS du verrou d'écriture :
        # la seconde lecture, dans la transaction, relit le cache.
        if conseils.lire_document(doc, avec_ocr=True) is None:
            bilan["annulé (inaccessible)"] = bilan.get("annulé (inaccessible)", 0) + 1
            continue
        try:
            with transaction() as conn:
                for eid in map(int, str(l["ids_fragments"]).split()):
                    conn.execute("DELETE FROM events WHERE id=?", (eid,))
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                                    " AND name='embeddings'").fetchone():
                        conn.execute("DELETE FROM embeddings WHERE source_table='events'"
                                     " AND source_id=?", (eid,))
                r = conseils.traiter(conn, doc, portee, verbose=False, avec_ocr=True)
                if r.get("statut") != "ok":
                    raise _Annule(r.get("statut"))
            bilan["ok"] = bilan.get("ok", 0) + 1
        except _Annule as e:
            bilan[f"annulé ({e})"] = bilan.get(f"annulé ({e})", 0) + 1
        except Exception:
            # Les actes déjà validés restent ; celui-ci est annulé. Le bilan
            # s'affiche avant que l'erreur remonte.
            bilan["arrêt sur erreur"] = 1
            for statut, n in sorted(bilan.items()):
                print(f"  {statut:<28} {n}")
            raise
    return bilan


def _ecrire_tsv(chemin: Path, lignes: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CHAMPS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)


def _lire_tsv(chemin: Path) -> list[dict]:
    with chemin.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _urls(chemin: Path) -> list[str]:
    """Une URL par ligne, ou `date|URL` (la date est ignorée : l'archive fait foi)."""
    urls = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#"):
            urls.append(ligne.split("|")[-1].strip())
    return urls


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--portee", choices=["commune", "epci"], required=True)
    a.add_argument("--liste", type=Path, required=True, help="URL des PDF, une par ligne")
    a.add_argument("--tsv", type=Path,
                   default=Path(f"audits/actes-archives-{date.today()}.tsv"))
    a.add_argument("--appliquer", action="store_true")
    a.add_argument("--plan-relu", type=Path,
                   help="avec --appliquer, OBLIGATOIRE : le TSV de la simulation relue")
    args = a.parse_args()

    portail = ((config.PAGES or {}).get(args.portee) or {}).get("portail")
    if not portail:
        print(f"pas de portail d'actes déclaré pour la portée {args.portee} (pages.{args.portee}.portail)")
        return 2
    if args.appliquer and not (args.plan_relu and args.plan_relu.is_file()):
        print("--appliquer exige --plan-relu <TSV de la simulation relue>. Rien d'écrit.")
        return 2
    if args.appliquer and args.plan_relu.resolve() == args.tsv.resolve():
        print("--tsv et --plan-relu désignent le même fichier. Rien d'écrit.")
        return 2

    urls = _urls(args.liste)
    reglage = (config.PAGES or {}).get(args.portee) or {}
    archives = actes_archives(portail, reglage.get("portail_deliberations")
                              or DELIBERATIONS_DEFAUT,
                              reglage.get("portail_decisions") or DECISIONS_DEFAUT)
    conn = get_conn(read_only=True)
    try:
        lignes = plan(conn, urls, args.portee, archives, corbeilles_declarees(portail))
    finally:
        conn.close()
    _ecrire_tsv(args.tsv, lignes)
    compte: dict[str, int] = {}
    for l in lignes:
        compte[l["action"]] = compte.get(l["action"], 0) + 1
    print(f"{len(urls)} URL ; {len(archives)} actes dans les réponses archivées ; "
          f"{sum(int(l['fragments']) for l in lignes)} fragment(s) à retirer")
    for action, n in sorted(compte.items()):
        print(f"  {action:<7} {n}")
    print(f"  → {args.tsv}")
    if not args.appliquer:
        print("SIMULATION — rien n'a été écrit en base.")
        return 0

    if _cles(lignes) != _cles(_lire_tsv(args.plan_relu)):
        print("Le plan diffère de la simulation relue. Rien d'écrit : resimuler et relire.")
        return 1
    db = Path(config.DB_PATH)
    garde = db.with_name(f"{db.stem}.avant-actes-archives-{datetime.now():%Y%m%d-%H%M%S}.db")
    sauvegarder(db, garde)
    print(f"base sauvegardée → {garde.name}")
    bilan = appliquer(lignes, args.portee, archives)
    for statut, n in sorted(bilan.items()):
        print(f"  {statut:<28} {n}")
    print("Ensuite : --step perimetre, aperçu, comparaison, republication.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
