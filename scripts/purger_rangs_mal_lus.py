#!/usr/bin/env python3
"""Retire les délibérations nées d'un rang mal lu au bord de la suite (PR #196).

    python3 scripts/purger_rangs_mal_lus.py              # simulation
    python3 scripts/purger_rangs_mal_lus.py --appliquer

`pv_parsers._suite_croissante` laissait un rang abîmé prendre la tête ou la
queue de la suite des actes d'une liasse océrisée. Deux fiches en sont nées,
relevées le 02/11/2026 en rejouant le découpage sur les 193 textes océrisés des
trois instances ; le découpage corrigé ne les crée plus, mais une relecture ne
retire pas une fiche que le document ne rend plus. Ce script retire CES deux-là,
et rien d'autre : la liste est fermée, chaque fiche désignée par sa séance, sa
pièce et son numéro.

À lancer APRÈS le déploiement de la correction : avant, la passe suivante la
recréerait. Sur chaque instance ; une cible absente de la base est ignorée.

Il refuse de toucher une fiche :
  - dont l'acte véritable (même séance, numéro lu correctement) n'est pas en
    base : la fiche n'est alors plus un doublon, c'est à un humain de trancher ;
  - créée dans l'atelier, annotée, ou à laquelle s'accroche un flux, un marché,
    une approbation ou un budget annexe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.conseils import PORTEES, _REFERENCES_EVENEMENT, _a_la_colonne  # noqa: E402
from collectors.db import get_conn  # noqa: E402

# (portée, date de séance, nom de la pièce, numéro mal lu, numéro véritable)
CIBLES = (
    # Lasalle : « D141_2025 » océrisé « Di41_2025 », page 44.
    ("epci", "2025-10-29", "Deliberations-du-29.10.2025.pdf", "41", "141"),
    # Saillans : « DELIB3CMO807 », un cachet de l'acte 3.
    ("commune", "2021-07-08", "CR-CM-08-juillet-2021-part-I.pdf", "380721", "3"),
)


def _accroches(conn, eid: int) -> int:
    n = conn.execute("SELECT COUNT(*) FROM annotations"
                     " WHERE object_type='deliberation' AND object_id=?", (eid,)).fetchone()[0]
    for table, colonne in _REFERENCES_EVENEMENT:
        if _a_la_colonne(conn, table, colonne):
            n += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {colonne}=?",
                              (eid,)).fetchone()[0]
    return n


def reperer(conn) -> tuple[list[tuple], list[tuple]]:
    """(à retirer, retenues avec leur raison)."""
    a_retirer, retenues = [], []
    for portee, date, piece, faux, vrai in CIBLES:
        type_ = PORTEES[portee]["delib"]
        lignes = conn.execute(
            "SELECT id, title, COALESCE(origine, '') FROM events"
            " WHERE type=? AND date=? AND json_extract(metadata, '$.numero_acte')=?"
            " AND source_url LIKE ?",
            (type_, date, faux, f"%/{piece}")).fetchall()
        for eid, titre, origine in lignes:
            cible = (eid, date, faux, titre or "")
            vraie = conn.execute(
                "SELECT COUNT(*) FROM events WHERE type=? AND date=?"
                " AND json_extract(metadata, '$.numero_acte')=?",
                (type_, date, vrai)).fetchone()[0]
            if not vraie:
                retenues.append((*cible, f"l'acte n° {vrai} du {date} est absent"))
            elif origine == "atelier":
                retenues.append((*cible, "créée dans l'atelier"))
            elif (n := _accroches(conn, eid)):
                retenues.append((*cible, f"{n} rattachement(s)"))
            else:
                a_retirer.append(cible)
    return a_retirer, retenues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--appliquer", action="store_true", help="écrit en base")
    args = ap.parse_args()

    conn = get_conn()
    a_retirer, retenues = reperer(conn)

    print(f"{len(a_retirer)} fiche(s) à retirer — rang mal lu au bord de la suite")
    for eid, date, faux, titre in a_retirer:
        print(f"   {eid:>7}  {date}  n° {faux:<7} {titre[:56]}")
    for eid, date, faux, titre, raison in retenues:
        print(f"   conservée {eid}  {date}  n° {faux} : {raison}")

    if not args.appliquer:
        print("\nSimulation. Relancer avec --appliquer pour écrire.")
        return 0
    if not a_retirer:
        return 0

    with conn:
        for eid, *_ in a_retirer:
            conn.execute("DELETE FROM event_entities WHERE event_id=?", (eid,))
            conn.execute("DELETE FROM events WHERE id=?", (eid,))
    print(f"\n✓ {len(a_retirer)} fiche(s) retirée(s). "
          f"Régénérer le snapshot pour que le site en tienne compte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
