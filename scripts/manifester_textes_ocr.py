#!/usr/bin/env python3
"""
manifester_textes_ocr.py — Pose un manifeste rétrospectif sur les textes océrisés déjà gardés (24/10/2026).

La collecte écrit `x.ocr.json` à côté de chaque `x.ocr.txt` depuis le
24/10/2026 (`cm_ocr.garder_texte`). Les textes d'avant n'en ont pas : ils se
lisent toujours, par la règle de la date, mais un rapport de montants ne peut
pas prouver qu'il les a lus. Ce script leur donne un manifeste.

Décision de Julien (24/10) : provenance « anterieure-au-manifeste », outils
« inconnu », aucune machine de production déclarée — on ne sait pas qui les a
produits, on ne l'invente pas. Les empreintes, elles, sont exactes. Le texte
n'est ni réocérisé ni modifié : c'est celui que tient la base.

Les débuts de page ne se retrouvent que si le PDF océrisé est là et que sa
relecture redonne EXACTEMENT le texte gardé ; sinon `pages` reste vide, et le
compte le dit.

Un texte périmé (plus ancien que son PDF océrisé) n'est PAS manifesté : ce
serait le figer comme référence alors que la collecte le remplacera.

À jouer sur la machine qui tient la base (le VPS pour Lasalle). N'imprime que
des comptes. À blanc par défaut ; rejouable : un texte déjà manifesté est sauté.

Usage :
  venv/bin/python scripts/manifester_textes_ocr.py              # à blanc
  venv/bin/python scripts/manifester_textes_ocr.py --appliquer
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import cm_ocr, conseils  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOSSIERS = (ROOT / "data" / "pv", cm_ocr.PDF_DIR)


def _pages(cible: Path, dossier: Path) -> list[str] | None:
    if not cible.exists():
        return None
    original = cible.with_name(cible.name.removesuffix(".ocr.pdf") + ".pdf")
    if dossier == cm_ocr.PDF_DIR:
        return cm_ocr._extract_pages(cible)
    return conseils._pages_ocerisees(original, cible)


def traiter(appliquer: bool) -> Counter:
    bilan = Counter()
    for dossier in DOSSIERS:
        for garde in sorted(dossier.glob("*.ocr.txt")):
            cible = garde.with_suffix(".pdf")
            if cm_ocr.lire_manifeste(cible) is not None:
                bilan["deja"] += 1
                continue
            if garde.stat().st_size == 0:
                bilan["vides"] += 1
                continue
            if cible.exists() and cible.stat().st_mtime > garde.stat().st_mtime:
                bilan["perimes"] += 1
                continue
            texte = garde.read_text(encoding="utf-8")
            pages = _pages(cible, dossier)
            bilan["avec_pages" if pages is not None and "\n".join(pages) == texte
                  else "sans_pages"] += 1
            if appliquer:
                cm_ocr.ecrire_manifeste(cible, texte, pages, provenance="anterieure-au-manifeste",
                                        langue=None, outils="inconnu")
            bilan["manifestes"] += 1
    return bilan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--appliquer", action="store_true", help="écrire les manifestes")
    args = ap.parse_args()
    b = traiter(args.appliquer)
    verbe = "manifestés" if args.appliquer else "à manifester"
    print(f"Textes {verbe} : {b['manifestes']}")
    print(f"  débuts de page retrouvés : {b['avec_pages']}")
    print(f"  sans débuts de page (PDF océrisé absent ou relu autrement) : {b['sans_pages']}")
    print(f"Déjà manifestés : {b['deja']}")
    print(f"Périmés, laissés à la collecte : {b['perimes']}")
    print(f"Vides, ignorés : {b['vides']}")
    if not args.appliquer:
        print("\n(à blanc — relancer avec --appliquer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
