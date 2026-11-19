#!/usr/bin/env python3
"""Purge unique des archives brutes BODACC et JO des associations hors périmètre.

Jusqu'au 02/11/2026, `fetch_json` archivait la page d'API ENTIÈRE avant le
filtre par commune : interrogée par code postal, elle porte les annonces des
communes voisines, personnes physiques comprises (1 023 sur 1 680 au relevé de
Vallouise-Pelvoux, davantage sous le 81100 de Brassac). Elles n'entraient ni en
base ni en publication, mais restaient sur disque sans durée ni finalité
(avis vigie-dpo du 02/11/2026). Les collecteurs n'archivent plus que les
annonces retenues ; ce script met l'existant au même régime.

Pour chaque pièce `bodacc` et `rna-jo` de `raw_documents` (et chaque fichier
orphelin de `data/raw/bodacc|rna-jo`) :
  - aucune annonce du périmètre → fichier et ligne supprimés ;
  - quelques-unes → fichier réécrit réduit à elles (forme de l'API gardée),
    empreinte et chemin mis à jour ; un doublon d'une pièce déjà réduite est
    supprimé.
Le filtre est celui des collecteurs (`_match_commune`), lu sur l'instance
courante. Rien n'est affiché du contenu : des comptes seulement.

    cd <instance> && python3 scripts/purger_archive_hors_perimetre.py             # à blanc
    cd <instance> && python3 scripts/purger_archive_hors_perimetre.py --appliquer

Le contrôle final relit chaque fichier restant par un second chemin et compte
les annonces hors périmètre : il doit rendre 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import bodacc, rna  # noqa: E402
from collectors.archive import RAW_DIR, page_reduite  # noqa: E402
from collectors.config import ROOT  # noqa: E402
from collectors.db import get_conn  # noqa: E402


def _bodacc_retenue(rec: dict) -> bool:
    # L'archive garde l'annonce telle que la source l'écrit, mojibake compris ;
    # la collecte la réparait AVANT de filtrer : le même ordre ici.
    return bool(bodacc._match_commune(
        bodacc._reparer_encodage_partout(rec.get("ville") or "")))


def _rna_retenue(rec: dict) -> bool:
    return bool(rna._match_commune(rec.get("commune_actuelle") or ""))


SOURCES = {"bodacc": _bodacc_retenue, "rna-jo": _rna_retenue}


def _lire(chemin: Path) -> dict | None:
    try:
        page = json.loads(chemin.read_bytes())
    except (OSError, ValueError):
        return None
    return page if isinstance(page, dict) and isinstance(page.get("results"), list) else None


def _reduire(page: dict, retenue) -> tuple[bytes | None, int, int]:
    """(contenu réduit ou None si rien n'est retenu, retenues, écartées)."""
    retenues = [r for r in page["results"] if isinstance(r, dict) and retenue(r)]
    ecartees = len(page["results"]) - len(retenues)
    if not retenues:
        return None, 0, ecartees
    brut = json.dumps(page_reduite(page, retenues), ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    return brut, len(retenues), ecartees


def purger(conn, appliquer: bool) -> dict:
    c = {"pieces": 0, "illisibles": 0, "intactes": 0, "reduites": 0, "supprimees": 0,
         "doublons": 0, "orphelins": 0, "annonces_ecartees": 0, "annonces_gardees": 0}
    connus = set()
    for source, retenue in SOURCES.items():
        lignes = conn.execute(
            "SELECT id, sha256, local_path FROM raw_documents WHERE source = ?",
            (source,)).fetchall()
        for rid, sha, local in lignes:
            c["pieces"] += 1
            chemin = ROOT / local
            connus.add(chemin.resolve())
            page = _lire(chemin)
            if page is None:
                c["illisibles"] += 1
                continue
            brut, gardees, ecartees = _reduire(page, retenue)
            c["annonces_ecartees"] += ecartees
            c["annonces_gardees"] += gardees
            if not ecartees:
                c["intactes"] += 1
                continue
            if brut is None:
                c["supprimees"] += 1
                if appliquer:
                    conn.execute("DELETE FROM raw_documents WHERE id = ?", (rid,))
                    chemin.unlink(missing_ok=True)
                continue
            neuf = hashlib.sha256(brut).hexdigest()
            deja = conn.execute("SELECT id FROM raw_documents WHERE sha256 = ? AND id <> ?",
                                (neuf, rid)).fetchone()
            if deja:
                c["doublons"] += 1
                if appliquer:
                    conn.execute("DELETE FROM raw_documents WHERE id = ?", (rid,))
                    chemin.unlink(missing_ok=True)
                continue
            c["reduites"] += 1
            if appliquer:
                dest = chemin.with_name(f"{neuf[:16]}.json")
                dest.write_bytes(brut)
                if dest != chemin:
                    chemin.unlink(missing_ok=True)
                conn.execute(
                    "UPDATE raw_documents SET sha256 = ?, byte_size = ?, local_path = ? WHERE id = ?",
                    (neuf, len(brut), str(dest.relative_to(ROOT)), rid))
                connus.add(dest.resolve())
        # Les fichiers que rien n'indexe : même régime, réduits sur place.
        for chemin in sorted((RAW_DIR / source).glob("*.json")):
            if chemin.resolve() in connus:
                continue
            c["orphelins"] += 1
            page = _lire(chemin)
            if page is None:
                c["illisibles"] += 1
                continue
            brut, gardees, ecartees = _reduire(page, retenue)
            c["annonces_ecartees"] += ecartees
            c["annonces_gardees"] += gardees
            if appliquer and ecartees:
                if brut is None:
                    chemin.unlink(missing_ok=True)
                else:
                    chemin.write_bytes(brut)
    if appliquer:
        conn.commit()
    return c


def controler() -> tuple[int, int]:
    """Second chemin : les fichiers sur disque, relus sans passer par la base.
    Rend (fichiers relus, annonces hors périmètre restantes)."""
    fichiers = restantes = 0
    for source, retenue in SOURCES.items():
        for chemin in (RAW_DIR / source).glob("*.json"):
            page = _lire(chemin)
            if page is None:
                continue
            fichiers += 1
            restantes += sum(1 for r in page["results"]
                             if isinstance(r, dict) and not retenue(r))
    return fichiers, restantes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--appliquer", action="store_true",
                        help="réécrire et supprimer (sans lui : à blanc, rien ne change)")
    args = parser.parse_args()
    conn = get_conn()
    try:
        c = purger(conn, args.appliquer)
    finally:
        conn.close()
    print(("APPLIQUÉ" if args.appliquer else "À BLANC") + " — archives bodacc et rna-jo")
    for k, v in c.items():
        print(f"  {k:20s} {v}")
    fichiers, restantes = controler()
    print(f"  contrôle : {fichiers} fichier(s) relus, {restantes} annonce(s) hors périmètre restantes")
    if args.appliquer and restantes:
        print("✖ des annonces hors périmètre restent sur disque")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
