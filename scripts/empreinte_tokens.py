#!/usr/bin/env python3
"""Tokens des journaux de session Claude Code, par dossier de travail (cwd),
puis énergie estimée par la méthode de S. Couch (2026-01-20,
simonpcouch.com/blog/2026-01-20-cc-impact/), bâtie sur Epoch AI (2025-02).
Lecture seule de ~/.claude/projects. Dédoublonne par (message.id, requestId).
Tokens de sortie en streaming parfois sous-comptés : lire comme un plancher.
Facteurs en Wh par million de tokens : 'haut' = contexte maximal (Couch),
'bas' = contexte moyen (Epoch : 2,5 Wh pour 10 k entrée + 530 sortie, ratio 5:1)."""
import json, glob, os, collections
HOME = os.path.expanduser('~')
base = HOME + '/.claude/projects'
import sys
# Sous-chaînes de dossiers de travail imputées au dispositif, en arguments :
#   empreinte_tokens.py Claude/vigie Claude/<instance> ...
VIGIE = tuple(sys.argv[1:]) or ('vigie',)
F = {'haut': dict(sortie=1950, entree=390, cache_ecr=490, cache_lec=39)}
F['bas'] = {k: v * 0.5 for k, v in F['haut'].items()}   # ≈ moitié (0,988 / 0,198 mWh)
seen = set(); P = collections.defaultdict(collections.Counter)
for f in glob.glob(base + '/**/*.jsonl', recursive=True):
    cwd = None
    for line in open(f, errors='ignore'):
        try: d = json.loads(line)
        except ValueError: continue
        cwd = d.get('cwd') or cwd
        m = d.get('message')
        if not isinstance(m, dict) or m.get('role') != 'assistant' or not m.get('usage'): continue
        k = (m.get('id'), d.get('requestId'))
        if k[0] and k in seen: continue
        seen.add(k); u = m['usage']
        p = (cwd or '?').replace(HOME, '~')
        g = 'vigie' if any(v in p for v in VIGIE) else 'autre'
        for key, src in (('sortie','output_tokens'),('entree','input_tokens'),
                         ('cache_ecr','cache_creation_input_tokens'),('cache_lec','cache_read_input_tokens')):
            P[g][key] += u.get(src) or 0
        P[g]['msgs'] += 1
tot = sum(P.values(), collections.Counter())
for name, c in (('vigie (par cwd)', P['vigie']), ('autre', P['autre']), ('TOTAL', tot)):
    kwh = {s: sum(c[k] * F[s][k] for k in F['haut']) / 1e6 / 1000 for s in F}
    print(f"{name:16} sortie {c['sortie']/1e6:6.2f} M | écr. cache {c['cache_ecr']/1e6:6.1f} M | "
          f"lec. cache {c['cache_lec']/1e6:7.0f} M | {kwh['bas']:5.0f}–{kwh['haut']:.0f} kWh")
