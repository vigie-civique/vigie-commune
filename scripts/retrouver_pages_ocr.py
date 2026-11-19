#!/usr/bin/env python3
"""
retrouver_pages_ocr.py — Rend ses débuts de page au manifeste d'un texte océrisé dont le PDF océrisé n'est plus là (25/10/2026).

Le découpage à la page (`pv_parsers.actes_par_page`) lit les débuts de page dans
le manifeste `x.ocr.json`. Sur le VPS, 78 des 83 textes de Lasalle sont venus
SANS leur `x.ocr.pdf` : le manifeste rétrospectif n'a pas pu les retrouver, et
le découpage à la page ne voyait que 3 liasses. Les PDF océrisés sont restés
sur le Mac.

La preuve n'est pas la date ni le nom : c'est l'EMPREINTE. Les pages relues sur
le Mac ne sont retenues que si leur jointure redonne, octet pour octet, le texte
que tient la base (`texte_sha256` du manifeste). Le texte n'est ni réocérisé ni
modifié (décision du 24/10) ; seul le champ `pages` du manifeste est rempli.

Trois temps, dans cet ordre :
  1. sur le VPS — les textes sans pages, avec leur empreinte :
       venv/bin/python scripts/retrouver_pages_ocr.py lister > sans_pages.txt
  2. sur le Mac — relire les PDF océrisés, garder ce qui redonne l'empreinte :
       python3 scripts/retrouver_pages_ocr.py exporter <dossier des .ocr.pdf> sans_pages.txt > pages.json
  3. sur le VPS — écrire les pages dans les manifestes (à blanc par défaut) :
       venv/bin/python scripts/retrouver_pages_ocr.py appliquer pages.json [--appliquer]

N'imprime que des noms de fichiers, des empreintes et des comptes.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import cm_ocr, conseils  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOSSIER = ROOT / "data" / "pv"


def lister() -> int:
    for manifeste in sorted(DOSSIER.glob("*.ocr.json")):
        m = cm_ocr.lire_manifeste(manifeste.with_suffix(".pdf")) or {}
        if m.get("role") == "reference" and not m.get("pages") and m.get("texte_sha256"):
            print(manifeste.name.removesuffix(".ocr.json"), m["texte_sha256"])
    return 0


def exporter(dossier: Path, liste: Path) -> int:
    """Deux lectures, dans l'ordre : celle de la collecte d'aujourd'hui (couche
    native en tête de page), puis celle d'avant, qui lisait le PDF océrisé seul."""
    trouves, bilan = {}, Counter()
    for ligne in liste.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        nom, attendu = ligne.split()
        cible = dossier / f"{nom}.ocr.pdf"
        if not cible.exists():
            bilan["ocr_pdf_absent"] += 1
            continue
        for lecture in (lambda: conseils._pages_ocerisees(dossier / f"{nom}.pdf", cible),
                        lambda: conseils.pages_pdf(cible)):
            pages = lecture()
            if cm_ocr.empreinte("\n".join(pages).encode("utf-8")) == attendu:
                trouves[nom] = {"texte_sha256": attendu, "pages": cm_ocr.positions_pages(pages)}
                bilan["retrouves"] += 1
                break
        else:
            bilan["empreinte_differente"] += 1
    json.dump(trouves, sys.stdout, ensure_ascii=False)
    print(f"\n{dict(bilan)}", file=sys.stderr)
    return 0


def appliquer(fichier: Path, ecrire: bool) -> int:
    bilan = Counter()
    for nom, entree in json.loads(fichier.read_text(encoding="utf-8")).items():
        if Path(nom).name != nom:
            bilan["nom_refuse"] += 1
            continue
        cible = DOSSIER / f"{nom}.ocr.pdf"
        m = cm_ocr.lire_manifeste(cible)
        garde = cible.with_suffix(".txt")
        if not m or not garde.exists():
            bilan["sans_manifeste"] += 1
            continue
        if m.get("pages"):
            bilan["deja"] += 1
            continue
        texte = garde.read_text(encoding="utf-8")
        pages = entree.get("pages") or []
        # Le texte d'ici doit être CELUI dont les pages ont été relues, et les
        # positions doivent tenir dedans, croissantes, la première à zéro.
        if (cm_ocr.empreinte(texte.encode("utf-8")) != entree.get("texte_sha256")
                or m.get("texte_sha256") != entree.get("texte_sha256")):
            bilan["empreinte_differente"] += 1
            continue
        if (not pages or pages[0] != 0 or pages[-1] > len(texte)
                or any(b <= a for a, b in zip(pages, pages[1:]))
                or any(texte[p - 1] != "\n" for p in pages[1:])):
            bilan["positions_refusees"] += 1
            continue
        if ecrire:
            m["pages"] = pages
            m["pages_retrouvees"] = "relecture du x.ocr.pdf sur une autre machine ; jointure = texte_sha256"
            provisoire = cible.with_suffix(".json.partiel")
            provisoire.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            provisoire.replace(cible.with_suffix(".json"))
        bilan["completes"] += 1
    verbe = "complétés" if ecrire else "à compléter"
    print(f"Manifestes {verbe} : {bilan['completes']}")
    for cle in ("deja", "empreinte_differente", "positions_refusees", "sans_manifeste", "nom_refuse"):
        if bilan[cle]:
            print(f"  {cle} : {bilan[cle]}")
    if not ecrire:
        print("\n(à blanc — relancer avec --appliquer)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sous = ap.add_subparsers(dest="geste", required=True)
    sous.add_parser("lister")
    e = sous.add_parser("exporter")
    e.add_argument("dossier", type=Path)
    e.add_argument("liste", type=Path)
    a = sous.add_parser("appliquer")
    a.add_argument("fichier", type=Path)
    a.add_argument("--appliquer", action="store_true")
    args = ap.parse_args()
    if args.geste == "lister":
        return lister()
    if args.geste == "exporter":
        return exporter(args.dossier.expanduser(), args.liste)
    return appliquer(args.fichier, args.appliquer)


if __name__ == "__main__":
    sys.exit(main())
