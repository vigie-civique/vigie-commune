"""Accessibilité du site public : les écarts RGAA corrigés le 16/10/2026 ne reviennent pas.

Lecture du code source, pas du site servi : l'audit des 106 critères se fait
sur l'URL en ligne. Ici, seulement ce qu'un nouveau composant oublierait sans
que personne le voie — un tableau sans titre, un lien qui ouvre une fenêtre
sans le dire, un filtre dont l'état ne se lit qu'à la couleur.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "src"


# Une balise Svelte : ses expressions `{…}` peuvent contenir « > » (`() => x`).
BALISE = r"(?:\{[^}]*\}|[^>{])*"


def _sources():
    for f in sorted(SRC.rglob("*.svelte")):
        yield f, f.read_text(encoding="utf-8")


def test_chaque_tableau_a_un_titre_et_des_entetes_portees():
    """RGAA 5.4 (titre associé) et 5.7 (en-têtes reliées aux cellules)."""
    fautes = []
    for f, s in _sources():
        for m in re.finditer(r"<table\b[^>]*>", s):
            suite = s[m.end():m.end() + 300]
            if not re.match(r"\s*<caption\b", suite):
                fautes.append(f"{f.relative_to(ROOT)}:{s.count(chr(10), 0, m.start()) + 1} sans <caption>")
        for m in re.finditer(r"<th\b(?![^>]*\bscope=)", s):
            fautes.append(f"{f.relative_to(ROOT)}:{s.count(chr(10), 0, m.start()) + 1} <th> sans scope")
    assert not fautes, "\n".join(fautes)


def test_un_lien_qui_ouvre_une_fenetre_le_dit():
    """RGAA 13.2 : « ↗ » seul ne se lit pas ; le texte « (nouvelle fenêtre) » si."""
    fautes = []
    fichiers = list(_sources()) + [(SRC / "lib/carte/fond.js",
                                    (SRC / "lib/carte/fond.js").read_text(encoding="utf-8"))]
    for f, s in fichiers:
        for m in re.finditer(rf'<a\b(?=[^>]*target="_blank"){BALISE}>(.*?)</a>', s, re.S):
            if "nouvelle fenêtre" not in m.group(1):
                fautes.append(f"{f.relative_to(ROOT)}:{s.count(chr(10), 0, m.start()) + 1}")
    assert not fautes, "lien target=_blank sans « (nouvelle fenêtre) » :\n" + "\n".join(fautes)


def test_un_bouton_bascule_dit_son_etat():
    """RGAA 7.1 : l'état d'un filtre ne se lit pas qu'à la couleur de `class:on`."""
    fautes = []
    for f, s in _sources():
        for m in re.finditer(rf"<button\b{BALISE}>", s, re.S):
            if re.search(r"\bclass:(on|sel|actif|active)=", m.group(0)) and "aria-pressed" not in m.group(0):
                fautes.append(f"{f.relative_to(ROOT)}:{s.count(chr(10), 0, m.start()) + 1}")
    assert not fautes, "bouton bascule sans aria-pressed :\n" + "\n".join(fautes)


def test_le_gabarit_porte_l_evitement_et_respecte_le_mouvement_reduit():
    """RGAA 12.7 (lien d'évitement) et 13.8 (mouvement)."""
    s = (SRC / "routes" / "+layout.svelte").read_text(encoding="utf-8")
    assert '<a class="evitement" href="#contenu">' in s
    assert '<main id="contenu"' in s
    assert s.index('class="evitement"') < s.index("<header")
    assert "prefers-reduced-motion: reduce" in s
