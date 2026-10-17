"""`requirements.txt` est généré depuis `requirements.in` : les deux disent la même chose.

Le verrou (dépendances transitives et empreintes) se régénère à la main, par la
commande écrite en tête de `requirements.in`. Modifier une version dans l'un
sans régénérer l'autre installerait en silence ce que personne n'a choisi :
deux fichiers qui portent la même cible divergent dès qu'on en oublie un.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPINGLE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\#]+)")


def normaliser(nom: str) -> str:
    return re.sub(r"[-_.]+", "-", nom).lower()


def epingles(fichier: str) -> dict[str, set[str]]:
    vus: dict[str, set[str]] = {}
    for ligne in (ROOT / fichier).read_text(encoding="utf-8").splitlines():
        m = EPINGLE.match(ligne)
        if m:
            vus.setdefault(normaliser(m.group(1)), set()).add(m.group(2))
    return vus


def test_chaque_dependance_directe_est_au_verrou_a_la_meme_version():
    directes, verrou = epingles("requirements.in"), epingles("requirements.txt")
    assert directes, "requirements.in ne fige plus rien ?"
    ecarts = {nom: (v, verrou.get(nom)) for nom, v in directes.items()
              if not v <= verrou.get(nom, set())}
    assert not ecarts, ("requirements.txt n'est pas régénéré (commande en tête de "
                        f"requirements.in) : {ecarts}")


def test_chaque_paquet_du_verrou_porte_son_empreinte():
    texte = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    blocs = re.split(r"\n(?=[A-Za-z0-9])", texte)
    sans = [b.split()[0] for b in blocs if EPINGLE.match(b) and "--hash=sha256:" not in b]
    assert not sans, f"paquets sans empreinte : {sans}"
