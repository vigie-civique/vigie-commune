#!/usr/bin/env python3
"""
marquer_actes_ocerises.py — Pose `ocr` sur les actes déjà lus dans une pièce océrisée (24/10/2026).

La collecte pose le drapeau sur l'acte depuis le 24/10/2026
(`conseils._marquer_ocr`). Avant, il n'était que sur la séance, et
`cm_finances.lu_par_ocr` ne le retrouvait plus dès que la séance avait deux
pièces ou venait d'un portail d'actes : une ligne de tableau océrisée entrait
`verified` (D5). Ce script pose le drapeau sur les actes collectés avant.

Un acte est océrisé quand la pièce de son `source_url` l'a été : son
`x.ocr.pdf` ou son `x.ocr.txt` est dans `data/pv/`, à côté du cache — la même
règle que `conseils.ocr`. Rejouable : un acte déjà marqué n'est pas réécrit.

Ce qu'il ne fait PAS : toucher aux flux. Les flux déjà écrits `verified`
depuis ces actes restent tels quels — les rétrograder les retire du site, et
c'est une décision à part. Le script les COMPTE, pour qu'elle se prenne.

Il n'imprime que des comptes. À blanc par défaut.

Usage :
  venv/bin/python scripts/marquer_actes_ocerises.py              # à blanc
  venv/bin/python scripts/marquer_actes_ocerises.py --appliquer
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.cle_acte import TYPES_ACTES  # noqa: E402
from collectors.conseils import _cible_cache, _marquer_ocr  # noqa: E402


def piece_ocerisee(url: str | None) -> bool:
    if not url:
        return False
    cible = _cible_cache(url).with_suffix(".ocr.pdf")
    garde = cible.with_suffix(".txt")
    return any(f.exists() and f.stat().st_size > 0 for f in (cible, garde))


def calculer(conn) -> dict:
    """{a_marquer: [(id, url)], par_type, deja, flux_verified}. Aucune écriture."""
    marques = ",".join("?" for _ in TYPES_ACTES)
    a_marquer, par_type, deja = [], Counter(), 0
    for eid, type_, url, meta in conn.execute(
            f"SELECT id, type, source_url, metadata FROM events "
            f"WHERE type IN ({marques}) ORDER BY id", TYPES_ACTES):
        if not piece_ocerisee(url):
            continue
        try:
            ocr = json.loads(meta or "{}").get("ocr")
        except json.JSONDecodeError:
            ocr = None
        if ocr is True:
            deja += 1
            continue
        a_marquer.append((eid, url))
        par_type[type_] += 1
    ids = [eid for eid, _ in a_marquer]
    flux = conn.execute(
        f"SELECT COUNT(*) FROM financial_flows WHERE confidence='verified'"
        f" AND event_id IN ({','.join('?' for _ in ids)})", ids).fetchone()[0] if ids else 0
    return {"a_marquer": a_marquer, "par_type": par_type, "deja": deja,
            "flux_verified": flux}


def appliquer(conn, bilan: dict) -> None:
    with conn:
        for eid, url in bilan["a_marquer"]:
            _marquer_ocr(conn, eid, url)


def rapport(bilan: dict, applique: bool) -> None:
    verbe = "marqués" if applique else "à marquer"
    print(f"Actes lus dans une pièce océrisée, {verbe} : {len(bilan['a_marquer'])}")
    for type_, n in sorted(bilan["par_type"].items()):
        print(f"  {type_} : {n}")
    print(f"Déjà marqués : {bilan['deja']}")
    print(f"Flux `verified` écrits depuis ces actes (non touchés) : {bilan['flux_verified']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--appliquer", action="store_true",
                    help="poser le drapeau (sinon : à blanc)")
    args = ap.parse_args()
    from collectors.db import get_conn
    conn = get_conn()
    try:
        bilan = calculer(conn)
        if args.appliquer:
            appliquer(conn, bilan)
        rapport(bilan, args.appliquer)
        if not args.appliquer:
            print("\n(à blanc — rien n'est écrit ; --appliquer pour poser le drapeau)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
