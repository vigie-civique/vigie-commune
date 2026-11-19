#!/usr/bin/env python3
"""Dérive `mots_courants.txt` de Morphalou 3.1 (ATILF), à rejouer si la liste doit changer.

    python3 -I scripts/snapshot/lexique/deriver.py <Morphalou3.1_CSV.csv>

Source : https://www.ortolang.fr/market/lexicons/morphalou (archive
`Morphalou3.1_formatCSV_toutEnUn.zip`, version de 6/2016, licence LGPL-LR).
Morphalou ne contient pas de noms propres : c'est ce qui en fait le filtre de
l'index du texte des actes (`scripts/snapshot/recherche.py`).

Critères, tous dans ce fichier :
  - catégories : nom commun, adjectif qualificatif, adverbe ; d'un verbe,
    l'infinitif et les participes seulement (les autres formes conjuguées
    font les deux tiers du lexique et ne disent pas un sujet) ;
  - forme d'un seul mot, en minuscules, trois lettres au moins ;
  - attestée par deux au moins des lexiques que Morphalou fusionne : un mot
    qu'un seul lexique connaît est rare, et un mot rare se confond plus
    facilement avec un patronyme.

`-I` : le CSV est une donnée téléchargée, le script ne charge rien d'autre.
"""
import csv
import re
import sys
from pathlib import Path

FORME = re.compile(r"^[a-zà-öø-ÿœæ]{3,}$")
CATEGORIES = {"Nom commun", "Adjectif qualificatif", "Adverbe"}
MODES_DU_VERBE = {"infinitive", "participle"}
ORIGINES_MIN = 2


def formes(chemin: Path) -> list[str]:
    gardees: dict[str, int] = {}
    categorie = None
    with chemin.open(encoding="utf-8", newline="") as f:
        for ligne in csv.reader(f, delimiter=";"):
            if len(ligne) < 18 or ligne[1] == "ID" or ligne[0] == "LEMME":
                continue
            if ligne[0]:
                categorie = ligne[2]
            forme, mode, origines = ligne[9], ligne[12], len(ligne[17].split())
            if not FORME.match(forme):
                continue
            if categorie in CATEGORIES or (categorie == "Verbe" and mode in MODES_DU_VERBE):
                gardees[forme] = max(gardees.get(forme, 0), origines)
    return sorted(f for f, n in gardees.items() if n >= ORIGINES_MIN)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    liste = formes(Path(sys.argv[1]))
    sortie = Path(__file__).resolve().parent / "mots_courants.txt"
    entete = ("# Mots courants du français, dérivés de Morphalou 3.1 (ATILF, CNRS / Université de\n"
              "# Lorraine), sous licence LGPL-LR : voir NOTICE et LICENCE-LGPLLR.txt.\n"
              "# Fichier GÉNÉRÉ par deriver.py, ne pas éditer à la main.\n")
    sortie.write_text(entete + "\n".join(liste) + "\n", encoding="utf-8")
    print(f"{len(liste)} formes → {sortie}")
