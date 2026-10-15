#!/usr/bin/env python3
"""Rend explicites les espaces que Svelte 4 gardait au bord intérieur d'un bloc
({#if}, {:else}, {:then}, {:catch}, {#key}, {#await}) et que Svelte 5
supprime. Pas `{#each}` : Svelte 4 rognait déjà les espaces à ses bords. Une espace n'est rendue explicite (`{' '}`) que si, de l'autre côté,
il y a du texte, une expression ou une balise EN LIGNE : entre deux blocs de
mise en page, elle ne se voyait pas, et ne se verra pas davantage.

Né de la montée en Svelte 5 (15/10/2026) : « Procès-verbal : » était devenu
« Procès-verbal: », « {#if i}, {/if} » collait les citations, sur une
vingtaine d'endroits du site public. `tests/test_espaces_svelte.py` le joue
sur les deux paquets : une espace de bord de bloc s'écrit `{' '}`.

    python3 scripts/espaces_svelte5.py public/src [--appliquer]
"""
import re
import sys
from pathlib import Path

OUVRANT = re.compile(r"\{[#:](?:if|else|then|catch|key|await)\b[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
FERMANT = re.compile(r"\{(?:/(?:if|key|await)|:(?:else|then|catch)\b[^{}]*)\}")
EN_LIGNE = {"a", "span", "strong", "em", "b", "i", "abbr", "time", "code", "small", "sup",
            "sub", "mark", "q", "cite", "data", "kbd", "var", "s", "u", "bdi", "bdo", "wbr",
            "label", "output", "button", "img", "input", "select", "svg"}
ESPACE = "{' '}"


def nature(fragment: str) -> str:
    """Ce qui touche la frontière : 'texte', 'en_ligne', ou 'bloc'."""
    if not fragment:
        return "bloc"
    if fragment[0] == "<":
        m = re.match(r"</?([A-Za-z][\w.:-]*)", fragment)
        if not m:
            return "bloc"            # commentaire, doctype
        nom = m.group(1)
        if nom[0].isupper():          # composant : en ligne s'il est petit (Icon…)
            return "en_ligne" if nom in {"Icon"} else "bloc"
        return "en_ligne" if nom.lower() in EN_LIGNE else "bloc"
    if fragment[0] == "{":
        return "bloc" if fragment[1:2] in ("#", "/", ":", "@") else "texte"
    return "texte"


def balisage(source: str):
    """Les plages hors <script> et <style>."""
    caches = [(m.start(), m.end()) for m in
              re.finditer(r"<(script|style)\b.*?</\1>", source, re.S)]
    debut = 0
    for a, b in caches:
        yield debut, a
        debut = b
    yield debut, len(source)


def corriger(source: str) -> tuple[str, list[str]]:
    insertions = []                   # (position, ligne pour le rapport)
    for a, b in balisage(source):
        zone = source[a:b]
        for m in OUVRANT.finditer(zone):
            fin = m.end()
            ws = re.match(r"[ \t\r\n]+", zone[fin:])
            if not ws:
                continue
            suite = zone[fin + ws.end():]
            if suite.startswith(ESPACE) or nature(suite) == "bloc":
                continue
            insertions.append((a + fin, f"après {m.group(0)[:40]}"))
        for m in FERMANT.finditer(zone):
            avant = zone[:m.start()]
            ws = re.search(r"[ \t\r\n]+$", avant)
            if not ws:
                continue
            prec = avant[:ws.start()]
            if prec.endswith(ESPACE) or not prec:
                continue
            dernier = prec[-1]
            if dernier == ">":
                ouvre = prec.rfind("<")
                tag = prec[ouvre:]
                if nature(tag.replace("</", "<", 1)) == "bloc":
                    continue
            elif dernier == "}":
                ouvre = prec.rfind("{")
                if prec[ouvre + 1:ouvre + 2] in ("#", "/", ":", "@"):
                    continue
            insertions.append((a + m.start(), f"avant {m.group(0)[:40]}"))
    lignes = []
    for pos, quoi in sorted(insertions, reverse=True):
        source = source[:pos] + ESPACE + source[pos:]
        lignes.append(f"{source.count(chr(10), 0, pos) + 1}: {quoi}")
    return source, sorted(lignes, key=lambda l: int(l.split(":")[0]))


def main():
    racine = Path(sys.argv[1])
    appliquer = "--appliquer" in sys.argv
    total = 0
    for f in sorted(racine.rglob("*.svelte")):
        if "node_modules" in f.parts:
            continue
        texte = f.read_text(encoding="utf-8")
        neuf, lignes = corriger(texte)
        if lignes:
            total += len(lignes)
            print(f"{f.relative_to(racine)} ({len(lignes)})")
            for l in lignes:
                print("   ", l)
            if appliquer:
                f.write_text(neuf, encoding="utf-8")
    print(f"\n{total} espace(s) {'rendue(s) explicite(s)' if appliquer else 'à rendre explicite(s)'}")


if __name__ == "__main__":
    main()
