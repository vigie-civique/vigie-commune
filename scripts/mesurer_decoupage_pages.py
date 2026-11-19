#!/usr/bin/env python3
"""
mesurer_decoupage_pages.py — Compare le découpage à la page au découpage actuel des liasses (25/10/2026).

Lot 2 du chantier « lire les montants des PV ». `pv_parsers.actes_par_page`
coupe une liasse au début de la page où un acte commence ; le régime en service,
`_actes_teletransmis`, coupe sur la ligne de son premier cachet. Avant de
brancher l'un à la place de l'autre, ce script mesure ce qui changerait, texte
par texte, sur les textes océrisés dont le manifeste garde les débuts de page.

LECTURE SEULE : ni la base ni un fichier ne sont touchés. N'imprime que des
noms de fichiers et des comptes, jamais un extrait de texte. À jouer sur la
machine qui tient les textes (le VPS pour Lasalle).

Usage :
  venv/bin/python scripts/mesurer_decoupage_pages.py
  venv/bin/python scripts/mesurer_decoupage_pages.py --detail    # numéros concernés, par texte
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import pv_parsers  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOSSIERS = (ROOT / "data" / "pv", ROOT / "data" / "cm_records_pdf")


def pages_du_texte(texte: str, debuts: list[int]) -> list[str]:
    """Les pages, recoupées aux positions du manifeste (le texte est leur jointure)."""
    fins = [d - 1 for d in debuts[1:]] + [len(texte)]
    return [texte[d:f] for d, f in zip(debuts, fins)]


def page_de(position: int, debuts: list[int]) -> int:
    return sum(1 for d in debuts if d <= position)


def comparer(texte: str, debuts: list[int]) -> dict | None:
    pages = pages_du_texte(texte, debuts)
    if "\n".join(pages) != texte:
        return {"incoherent": True}
    actuels = pv_parsers.deliberations(texte)
    actuels = actuels if actuels and actuels[0]["regime"] == "actes_teletransmis" else []
    nouveau = pv_parsers.actes_par_page(pages)
    par_page = pv_parsers._recoller(nouveau["actes"])
    if not actuels and not par_page:
        return None
    debut_actuel = {}
    for a in actuels:
        i = texte.find(a["texte"][:200])
        debut_actuel[a["numero_acte"]] = page_de(i, debuts) if i >= 0 else None
    debut_page = {a["numero_acte"]: a["pages"][0] for a in par_page}
    communs = debut_actuel.keys() & debut_page.keys()
    c = nouveau["controle"]
    return {
        "actuels": len(actuels), "par_page": len(par_page),
        "seulement_actuel": sorted(debut_actuel.keys() - debut_page.keys(), key=int),
        "seulement_page": sorted(debut_page.keys() - debut_actuel.keys(), key=int),
        "debut_change": sorted((n for n in communs if debut_actuel[n] != debut_page[n]), key=int),
        "manquants": c.get("manquants", []),
        "suite_illisible": not c.get("suite_lisible", True),
        "hors_suite": len(c.get("hors_suite", [])),
        "ouvertures_deplacees": len(c.get("ouvertures_deplacees", [])),
        "pages_sans_cachet": c.get("pages_sans_cachet", 0), "pages": len(pages),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--detail", action="store_true", help="les numéros concernés, par texte")
    args = ap.parse_args()

    total = {"textes": 0, "sans_pages": 0, "incoherents": 0, "liasses": 0, "actuels": 0,
             "par_page": 0, "debut_change": 0, "seulement_actuel": 0, "seulement_page": 0,
             "manquants": 0, "suites_illisibles": 0, "hors_suite": 0, "ouvertures_deplacees": 0}
    for dossier in DOSSIERS:
        for garde in sorted(dossier.glob("*.ocr.txt")):
            total["textes"] += 1
            try:
                manifeste = json.loads(garde.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifeste = {}
            debuts = manifeste.get("pages")
            if not debuts:
                total["sans_pages"] += 1
                continue
            r = comparer(garde.read_text(encoding="utf-8"), debuts)
            if r is None:
                continue
            if r.get("incoherent"):
                total["incoherents"] += 1
                continue
            total["liasses"] += 1
            total["suites_illisibles"] += r["suite_illisible"]
            for cle in ("actuels", "par_page", "hors_suite", "ouvertures_deplacees"):
                total[cle] += r[cle]
            for cle in ("debut_change", "seulement_actuel", "seulement_page", "manquants"):
                total[cle] += len(r[cle])
            print(f"{garde.name[:60]:60} {r['pages']:4d} p. ; actes {r['actuels']:3d} → {r['par_page']:3d}"
                  f" ; début changé {len(r['debut_change']):3d} ; manquants {len(r['manquants']):2d}"
                  f" ; ouvertures déplacées {r['ouvertures_deplacees']:2d}")
            if args.detail:
                for cle in ("debut_change", "seulement_actuel", "seulement_page", "manquants"):
                    if r[cle]:
                        print(f"    {cle} : {', '.join(map(str, r[cle]))}")

    print(f"\nTextes : {total['textes']} (sans débuts de page : {total['sans_pages']},"
          f" pages incohérentes avec le manifeste : {total['incoherents']})")
    print(f"Liasses comparées : {total['liasses']}")
    print(f"Actes : {total['actuels']} aujourd'hui → {total['par_page']} à la page")
    print(f"  même numéro, page de début changée : {total['debut_change']}")
    print(f"  numéros que seul le découpage actuel trouve : {total['seulement_actuel']}")
    print(f"  numéros que seul le découpage à la page trouve : {total['seulement_page']}")
    print(f"Contrôle de la suite : {total['manquants']} numéro(s) manquant(s),"
          f" {total['suites_illisibles']} liasse(s) à la suite illisible,"
          f" {total['hors_suite']} cachet(s) hors suite,"
          f" {total['ouvertures_deplacees']} ouverture(s) ramenée(s) à la page d'avant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
