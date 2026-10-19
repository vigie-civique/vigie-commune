"""« Sources et limites » : chaque bloc de donnée des pages carrefour a la sienne.

Aucun réseau, aucune base : on lit la déclaration (`public/src/lib/
sources-limites.json`) et le texte des pages.

Ce que ces essais protègent :
  1. un bloc ajouté à une page carrefour sans sa provenance rougit la CI — un
     `<h2>` qui n'est pas une donnée le dit par `data-sans-donnees` ;
  2. une carte ou une tuile chiffrée porte une clé déclarée ;
  3. la déclaration ne garde pas d'entrée morte, ni d'entrée bavarde : une
     limite tient en une phrase courte, sans quoi le repli alourdirait la page
     qu'il doit éclairer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "src"
DECLARATION = json.loads((SRC / "lib" / "sources-limites.json").read_text(encoding="utf-8"))
BLOCS = DECLARATION["blocs"]

PAGES = {
    "territoire": SRC / "routes" / "territoire" / "+page.svelte",
    "argent": SRC / "routes" / "argent" / "+page.svelte",
    "qui-decide": SRC / "routes" / "qui-decide" / "+page.svelte",
    "accueil": SRC / "routes" / "+page.svelte",
}
TABLEAU = SRC / "lib" / "tableau.server.js"

# Une limite qui ne tient pas dans cette longueur n'est plus une limite, c'est
# un encadré : il a sa place dans la page, visible, pas dans le repli.
LIMITE_MAX = 240


def _texte(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8")


def _cles_des_tuiles() -> list[str]:
    return re.findall(r"cle: '([^']+)'", _texte(TABLEAU))


def _cles_citees() -> set[str]:
    cites = set()
    for f in SRC.rglob("*.svelte"):
        t = _texte(f)
        cites |= set(re.findall(r'<SourcesLimites[^>]*\bbloc="([^"{]+)"', t))
        cites |= set(re.findall(r"\bbloc: '([^']+)'", t))
    return cites | {f"accueil.{c}" for c in _cles_des_tuiles()}


class TestDeclaration:
    @pytest.mark.parametrize("cle", sorted(BLOCS))
    def test_chaque_entree_dit_sa_source_et_sa_limite(self, cle):
        e = BLOCS[cle]
        assert e.get("source", "").strip(), f"{cle} : source vide"
        assert e.get("limite", "").strip(), f"{cle} : limite vide"
        assert len(e["limite"]) <= LIMITE_MAX, (
            f"{cle} : limite de {len(e['limite'])} caractères — une phrase, "
            "ou un encadré visible dans la page")
        assert e.get("sondage") in (None, "principale", "complementaire"), cle
        if e.get("lien"):
            assert e["lien"].startswith("https://"), cle

    def test_aucune_entree_morte(self):
        mortes = sorted(set(BLOCS) - _cles_citees())
        assert mortes == [], f"entrées que plus aucun bloc n'emploie : {mortes}"

    def test_aucune_cle_citee_sans_entree(self):
        absentes = sorted(_cles_citees() - set(BLOCS))
        assert absentes == [], (
            f"blocs sans « Sources et limites » déclarées : {absentes} — "
            "ajouter l'entrée dans public/src/lib/sources-limites.json")


class TestPages:
    @pytest.mark.parametrize("page", sorted(PAGES))
    def test_chaque_section_de_donnees_a_son_repli(self, page):
        texte = _texte(PAGES[page])
        balisage = texte.split("</script>", 1)[-1].split("<style", 1)[0]
        sections = re.split(r"(?=<h2\b)", balisage)[1:]
        nues = []
        for s in sections:
            ouverture = s[:s.index(">") + 1]
            if "data-sans-donnees" in ouverture:
                continue
            if "<SourcesLimites" not in s:
                titre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\{[^}]*\}", "", s[:s.find("</h2>")]))
                nues.append(titre.strip()[:60])
        assert nues == [], (
            f"/{page} : sections sans « Sources et limites » ({nues}). Poser "
            "<SourcesLimites bloc=…>, ou `data-sans-donnees` sur le <h2> s'il "
            "ne porte aucun chiffre.")

    @pytest.mark.parametrize("page", ["argent", "qui-decide"])
    def test_chaque_carte_chiffree_porte_sa_cle(self, page):
        texte = _texte(PAGES[page])
        cartes = re.findall(r"\{ icone: .*?(?=\n    \{ icone: |\n  \])", texte, re.S)
        assert cartes, f"/{page} : cartes introuvables — le motif a vieilli"
        sans = [re.search(r"titre: [`'\"]([^`'\"]+)", c).group(1) for c in cartes
                if "bloc:" not in c and "valeur: null" not in c]
        assert sans == [], f"/{page} : cartes chiffrées sans clé de provenance : {sans}"

    def test_l_accueil_cite_toutes_ses_tuiles(self):
        assert "blocs={tableau.map((t) => `accueil.${t.cle}`)}" in _texte(PAGES["accueil"])
