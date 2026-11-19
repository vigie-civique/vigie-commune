"""Les libellés publics ne promettent que ce qu'une vigie publie.

Une vigie publie les délibérations des conseils, leurs séances et, pour la
communauté de communes, les décisions par délégation (titre seul). Pas les
arrêtés du maire, pas les recueils de la préfecture. « Compte rendu » n'existe
plus en droit depuis le 01/07/2022. Les chaînes écartées ne reviennent pas.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "src"

ECARTEES = [
    r"actes officiels",
    r"Délibérations\s*(?:&amp;|&|et)\s*(?:actes|décisions)",
    r"et actes issus",
    r"séances \(comptes rendus\)",
    r"actes des assemblées",
]

# « Compte rendu » n'existe plus en droit : écarté des pages ci-dessous
# (il reste ailleurs, chantier de suite). Sauf /deliberations, qui le nomme
# une fois pour dire quelles pièces les communes publient.
SANS_COMPTE_RENDU = [
    "routes/deliberations/[annee]/+page.svelte",
    "routes/qui-decide/+page.svelte",
    "routes/llms.txt/+server.js",
    "routes/methode/+page.svelte",
]


def _fichiers():
    for f in sorted(SRC.rglob("*")):
        if f.suffix in {".svelte", ".js", ".json"} and f.is_file():
            yield f


def test_chaines_ecartees_absentes_de_public_src():
    fautes = []
    for f in _fichiers():
        s = f.read_text(encoding="utf-8")
        for motif in ECARTEES:
            for m in re.finditer(motif, s, flags=re.IGNORECASE):
                ligne = s.count("\n", 0, m.start()) + 1
                fautes.append(f"{f.relative_to(ROOT)}:{ligne} « {m.group(0)} »")
    assert not fautes, "libellé écarté revenu :\n" + "\n".join(fautes)


def test_les_pages_des_actes_portent_le_libelle_retenu():
    libelle = "Délibérations des conseils"
    for rel in (
        "routes/deliberations/+page.svelte",
        "routes/deliberations/[annee]/+page.svelte",
        "routes/qui-decide/+page.svelte",
        "routes/llms.txt/+server.js",
    ):
        assert libelle in (SRC / rel).read_text(encoding="utf-8"), rel


def test_description_de_l_accueil_compte_ce_que_la_page_compte():
    """Le total annoncé est `total` (hors décisions), pas le nombre d'actes bruts."""
    s = (SRC / "routes/deliberations/+page.svelte").read_text(encoding="utf-8")
    assert re.search(r'content="Les \{total\} délibérations et séances', s)


def test_compte_rendu_ecarte_des_pages_traitees():
    for rel in SANS_COMPTE_RENDU:
        t = (SRC / rel).read_text(encoding="utf-8")
        assert not re.search(r"comptes? rendus?", t, flags=re.IGNORECASE), rel


def test_les_seances_ne_sont_pas_toutes_des_proces_verbaux():
    """Lasalle, Brassac, Saillans publient aussi comptes rendus, listes et recueils."""
    s = (SRC / "routes/deliberations/+page.svelte").read_text(encoding="utf-8")
    assert "séances (procès-verbaux)" not in s
    assert "son procès-verbal" not in s


def test_source_des_seances_ne_cite_que_des_pieces_lues():
    """Vérifié sur les seances.json servis : aucun « recueil » de séance."""
    s = (SRC / "lib/sources-limites.json").read_text(encoding="utf-8")
    bloc = s.split('"accueil.seances"')[1].split('"limite"')[0]
    assert "recueil" not in bloc.lower()
    assert "Procès-verbaux" in bloc and "délibérations" in bloc
