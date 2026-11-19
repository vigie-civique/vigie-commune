#!/usr/bin/env python3
"""
build_rag_index.py — Construit l'index de recherche par sens de l'atelier.

Un vecteur par note, acte, flux financier et entité, calculé par un modèle
d'embeddings local (Ollama). L'atelier s'en sert pour retrouver une notion
quand on n'en a pas le vocabulaire : « conflit d'intérêt » ramène les
récusations au conseil, où ce mot ne figure nulle part. Pour un mot qui
s'écrit tel quel, la recherche plein texte déjà en base (FTS5) est plus rapide
et plus prévisible — les deux ne se remplacent pas.

L'index porte sur la base de TRAVAIL, non filtrée : il ne sort pas de
l'atelier, et le site public n'en a aucune trace. Sa recherche à lui est en
texte clair, sur le snapshot déjà filtré.

Chaque vecteur porte le nom et l'empreinte du modèle qui l'a calculé
(`collectors/index_sens.py`). Un index d'un autre modèle ne se complète pas :
le script refuse, et dit de le reconstruire (`--reset`).

Incrémental : un extrait déjà indexé n'est recalculé que si son texte a changé,
et l'extrait d'une ligne disparue de la base est retiré (`events.id` change à
chaque rejeu d'un collecteur).

Modèle et adresse se règlent par l'environnement, comme dans api.py :
OLLAMA_URL, OLLAMA_EMBED_MODEL.

Usage :
    python3 scripts/build_rag_index.py            # incrémental
    python3 scripts/build_rag_index.py --reset
    python3 scripts/build_rag_index.py --source events
    python3 scripts/build_rag_index.py --etat     # + consigne db/index_sens.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import sqlite3

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collectors import index_sens            # noqa: E402
from collectors.config import DB_PATH        # noqa: E402
from collectors.db import init_db            # noqa: E402

EMBED_URL   = f"{index_sens.OLLAMA_URL}/api/embeddings"
EMBED_MODEL = index_sens.MODELE


# nomic-embed-text lit au plus 2 048 jetons, et `/api/embeddings` REFUSE un
# texte plus long au lieu de le couper (« input (3122 tokens) is too large to
# process », 168 extraits de Lasalle le 07/11/2026). On ne calcule donc que le
# début du texte : 4 000 caractères tiennent dans 2 048 jetons pour une prose
# ordinaire ; un texte plus dense (tableaux, chiffres) est réduit de moitié tant
# qu'Ollama le juge trop long. `chunk_text` reste entier en base : c'est lui qu'on
# affiche, et lui qui dit si l'extrait a changé.
TRONCATURE = 4000
TRONCATURE_MIN = 500
# Un texte proche des 2 048 jetons prend plus de 30 s sur un seul cœur du VPS
# (`CPUQuota=100%` de vigie-ollama) : 28 extraits de Lasalle en « ReadTimeout »
# le 08/11/2026. Le verrou de la base n'est pas tenu pendant l'attente (#228).
DELAI_S = 180


def embed(text: str) -> np.ndarray:
    longueur = TRONCATURE
    while True:
        r = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text[:longueur]},
                          timeout=DELAI_S)
        trop_long = r.status_code >= 400 and "too large" in (r.text or "")
        if trop_long and longueur > TRONCATURE_MIN and len(text) > TRONCATURE_MIN:
            longueur = min(longueur, len(text)) // 2
            continue
        r.raise_for_status()
        return np.array(r.json()["embedding"], dtype=np.float32)


# ─── chunk builders ───────────────────────────────────────────────────────────

def chunks_entity_notes(conn):
    for row in conn.execute("""
        SELECT n.id, n.entity_id, e.name, n.note, n.source, n.confidence
        FROM entity_notes n
        JOIN entities e ON e.id = n.entity_id
        WHERE n.note IS NOT NULL AND LENGTH(n.note) > 10
    """):
        nid, eid, ename, content, source, conf = row
        text = f"Note ({conf}) sur {ename}: {content}"
        if source:
            text += f" [source: {source}]"
        yield "entity_notes", nid, eid, text


# Bruit connu, exclu de l'index : fragments de tableaux OCR mis en quarantaine
# (fragments de grilles tarifaires découpées par l'OCR d'un PV).
EVENT_TYPES_EXCLUS = ("fragment_ocr",)

# Longueur de contenu retenue par événement. nomic-embed-text accepte 8192
# tokens ; 2000 caractères couvrent une délibération entière dans la quasi-
# totalité des cas, sans diluer le vecteur.
EVENT_CONTENT_MAX = 2000


def chunks_events(conn):
    """Un embedding par événement, enrichi des acteurs liés.

    ⚠️ Cette fonction interrogeait `events.entity_id` et `events.description`
    — deux colonnes qui n'existent pas : le lien acteur↔acte passe par
    `event_entities` et le texte est dans `content`. La requête levait donc
    `OperationalError` à la première itération et faisait tomber tout le script.
    Résultat : 0 des 2 800 événements n'a jamais été indexé, alors que les
    délibérations et PV sont l'essentiel du texte substantiel de la base. Une
    question du type « qu'a voté le conseil sur la Cure ? » ne pouvait rien
    trouver. Ne pas réintroduire ces noms de colonnes.
    """
    placeholders = ",".join("?" for _ in EVENT_TYPES_EXCLUS)
    for row in conn.execute(f"""
        SELECT ev.id, ev.type, ev.date, ev.title, ev.content, ev.source,
               ev.metadata,
               (SELECT ee.entity_id FROM event_entities ee
                 WHERE ee.event_id = ev.id
                 ORDER BY (ee.role <> 'sujet'), ee.entity_id LIMIT 1) AS main_entity_id,
               (SELECT GROUP_CONCAT(e2.name, ', ') FROM event_entities ee2
                  JOIN entities e2 ON e2.id = ee2.entity_id
                 WHERE ee2.event_id = ev.id) AS acteurs
        FROM events ev
        WHERE ev.type NOT IN ({placeholders})
          AND (LENGTH(COALESCE(ev.title, '')) > 5
               OR LENGTH(COALESCE(ev.content, '')) > 40)
    """, EVENT_TYPES_EXCLUS):
        (ev_id, ev_type, date, title, content, source, metadata,
         main_entity_id, acteurs) = row

        parts = [f"{ev_type} {date or 'sans date'} : {title or '(sans titre)'}"]
        if acteurs:
            parts.append(f"Acteurs concernés : {acteurs}.")

        # Vote et montants sont dans metadata et portent l'essentiel du sens
        # d'une délibération — les sortir en clair les rend interrogeables.
        if metadata:
            try:
                meta = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if meta.get("vote"):
                parts.append(f"Vote : {meta['vote']}.")
            montants = meta.get("montants")
            if montants:
                parts.append(f"Montants cités : {montants}.")
            if meta.get("categorie"):
                parts.append(f"Catégorie : {meta['categorie']}.")

        if content:
            parts.append(content[:EVENT_CONTENT_MAX])
        if source:
            parts.append(f"[source: {source}]")
        yield "events", ev_id, main_entity_id, " ".join(parts)


def chunks_financial_flows(conn):
    for row in conn.execute("""
        SELECT ff.id, ff.to_id, e_to.name, ff.from_id, e_from.name,
               ff.type, ff.amount, ff.year, ff.description
        FROM financial_flows ff
        LEFT JOIN entities e_to   ON e_to.id   = ff.to_id
        LEFT JOIN entities e_from ON e_from.id = ff.from_id
        WHERE ff.amount IS NOT NULL OR ff.description IS NOT NULL
    """):
        fid, to_id, to_name, from_id, from_name, ftype, amount, year, desc = row
        dest = to_name or "?"
        src  = from_name or "?"
        parts = [f"Flux {ftype} {year or ''}:"]
        parts.append(f"{src} → {dest}")
        if amount:
            parts.append(f"{amount:,.0f} €")
        if desc:
            parts.append(f"— {desc[:200]}")
        entity_id = to_id or from_id
        yield "financial_flows", fid, entity_id, " ".join(parts)


def chunks_entities(conn):
    for row in conn.execute("""
        SELECT e.id, e.name, e.type,
               b.siren
        FROM entities e
        LEFT JOIN businesses b ON b.entity_id = e.id
        WHERE e.name IS NOT NULL
    """):
        eid, name, etype, siren = row
        # Sans l'adresse : l'index recopie des textes, pas des fiches.
        parts = [f"Entité {etype}: {name}"]
        if siren:
            parts.append(f"SIREN {siren}")
        yield "entities", eid, eid, " ".join(parts)


# Ce que l'index doit contenir : du TEXTE ÉCRIT — notes de l'atelier,
# délibérations, libellés de flux. Pas des fiches d'annuaire.
#
# `chunks_entities` produisait « Entité association: NOM. Adresse: … » pour chaque
# acteur. Mesuré le 15/08/2026 sur une base de 18 176 chunks : ces 8 753 fiches
# représentaient 48 % de l'index et sortaient EN TÊTE de la question « conflit
# d'intérêt élu association subventionnée » — le mot « association » figure dans
# chacune d'elles. Les délibérations qui traitent réellement du sujet
# n'apparaissaient qu'une fois ces fiches écartées.
#
# Un nom d'acteur se cherche par son nom : c'est le métier du FTS5 déjà en base,
# qui le fait mieux et sans modèle. La recherche par sens sert à retrouver ce
# qu'on ne sait pas nommer.
#
# `--source entities` reste possible pour qui veut mesurer par lui-même.
ALL_BUILDERS = [
    chunks_entity_notes,
    chunks_events,
    chunks_financial_flows,
]

BUILDERS_OPTIONNELS = [chunks_entities]


# ─── taille ───────────────────────────────────────────────────────────────────

def octets(conn) -> tuple[int, int]:
    """(octets de l'index, octets de la base). `dbstat` compte les pages
    réellement occupées, index SQL compris ; sans lui, la somme des longueurs
    sous-estime un peu."""
    base = conn.execute("PRAGMA page_count").fetchone()[0] * \
        conn.execute("PRAGMA page_size").fetchone()[0]
    try:
        index = conn.execute(
            "SELECT COALESCE(SUM(pgsize), 0) FROM dbstat"
            " WHERE name IN ('embeddings', 'idx_embeddings_entity', 'idx_embeddings_source')"
            " OR name LIKE 'sqlite_autoindex_embeddings%'").fetchone()[0]
    except Exception:                                   # noqa: BLE001
        index = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(vector) + LENGTH(chunk_text)), 0)"
            " FROM embeddings").fetchone()[0]
    return int(index), int(base)


# ─── main ─────────────────────────────────────────────────────────────────────

class Echec(Exception):
    """Un échec dont le motif est déjà dans la liste fermée."""


def construire(conn, *, reset: bool, source: str | None) -> dict:
    """Construit ou complète l'index. Rend les mesures ; lève `Echec` ou
    l'exception d'origine si l'index n'est pas tenu."""
    try:
        emp = index_sens.empreinte(requests)
    except requests.RequestException as e:
        print("  ✗ Ollama ne répond pas", file=sys.stderr)
        raise Echec("connexion") from e
    if emp is None:
        print(f"  ✗ modèle {EMBED_MODEL} absent d'Ollama : ollama pull {EMBED_MODEL}",
              file=sys.stderr)
        raise Echec("ModeleAbsent")
    modele = index_sens.nom_complet(EMBED_MODEL)
    print(f"Modèle : {modele} ({emp})")

    if reset:
        if source:
            conn.execute("DELETE FROM embeddings WHERE source_table=?", (source,))
        else:
            conn.execute("DELETE FROM embeddings")
        conn.commit()
        print("Index réinitialisé.")

    raison = index_sens.incompatibilite(conn, EMBED_MODEL, emp)
    if raison:
        # Compléter un index d'un autre modèle ferait cohabiter deux espaces de
        # vecteurs : la recherche le refuserait de toute façon.
        print(f"  ✗ {raison}", file=sys.stderr)
        raise Echec("ModeleDifferent")

    builders = ([b for b in ALL_BUILDERS + BUILDERS_OPTIONNELS
                 if b.__name__ == f"chunks_{source}"]
                if source else list(ALL_BUILDERS))

    debut = time.monotonic()
    total = ajoutes = mis_a_jour = inchanges = retires = extraits_en_echec = 0
    # Un builder qui casse ne doit pas emporter les autres : c'est ce qui a
    # masqué l'absence totale des events dans l'index (OperationalError sur une
    # colonne inexistante → le script mourait, les autres sources passaient
    # quand on les demandait une par une, et le trou restait invisible).
    builders_en_echec: list[str] = []

    for builder in builders:
        try:
            produced = list(builder(conn))
        except Exception as e:                          # noqa: BLE001
            builders_en_echec.append(builder.__name__)
            print(f"  ✗ builder {builder.__name__} en échec — {type(e).__name__}",
                  file=sys.stderr)
            continue
        table = builder.__name__.removeprefix("chunks_")
        deja = {sid: texte for sid, texte in conn.execute(
            "SELECT source_id, chunk_text FROM embeddings WHERE source_table=?", (table,))}
        vus = set()

        for source_table, source_id, entity_id, chunk_text in produced:
            total += 1
            vus.add(source_id)
            if deja.get(source_id) == chunk_text:
                inchanges += 1
                continue
            try:
                vec = embed(chunk_text)
            except Exception as e:                      # noqa: BLE001
                extraits_en_echec += 1
                print(f"  ✗ {source_table}:{source_id} — {type(e).__name__}",
                      file=sys.stderr)
                # L'ancien texte ne reste pas en base : il peut porter ce que
                # la ligne ne dit plus (un nom retiré, avis DPO du 07/11/2026).
                conn.execute("DELETE FROM embeddings WHERE source_table=? AND source_id=?",
                             (source_table, source_id))
                conn.commit()
                continue
            conn.execute(
                "INSERT INTO embeddings (source_table, source_id, entity_id, chunk_text,"
                " vector, created_at, modele, modele_empreinte) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(source_table, source_id) DO UPDATE SET"
                " entity_id=excluded.entity_id, chunk_text=excluded.chunk_text,"
                " vector=excluded.vector, created_at=excluded.created_at,"
                " modele=excluded.modele, modele_empreinte=excluded.modele_empreinte",
                (source_table, source_id, entity_id, chunk_text, vec.tobytes(),
                 datetime.now(timezone.utc).isoformat(), modele, emp))
            # Validé AUSSITÔT : le calcul d'un extrait prend jusqu'à 15 s sur le
            # VPS, et une transaction ouverte d'un extrait à l'autre gardait le
            # verrou d'écriture de la base plusieurs minutes. L'atelier et les
            # scripts de reprise échouaient alors en « database is locked »
            # (Lasalle, 08/11/2026, lots de 50).
            conn.commit()
            if source_id in deja:
                mis_a_jour += 1
            else:
                ajoutes += 1
            if (ajoutes + mis_a_jour) % 50 == 0:
                print(f"  {ajoutes + mis_a_jour} extraits calculés…")
        conn.commit()

        # La ligne a disparu de la base : son extrait ne doit plus remonter.
        orphelins = set(deja) - vus
        if orphelins:
            conn.executemany("DELETE FROM embeddings WHERE source_table=? AND source_id=?",
                             [(table, sid) for sid in orphelins])
            conn.commit()
            retires += len(orphelins)

    index, base = octets(conn)
    mesures = {
        "modele": modele, "empreinte": emp,
        "extraits": conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
        "ajoutes": ajoutes, "mis_a_jour": mis_a_jour, "inchanges": inchanges,
        "retires": retires, "extraits_en_echec": extraits_en_echec,
        "duree_s": round(time.monotonic() - debut, 1),
        "octets_index": index, "octets_base": base,
    }
    print(f"\nTerminé en {mesures['duree_s']} s — {ajoutes} ajoutés, {mis_a_jour} mis à jour,"
          f" {inchanges} inchangés, {retires} retirés. Total en base : {mesures['extraits']}")
    for src, n in conn.execute(
            "SELECT source_table, COUNT(*) FROM embeddings GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {src:16} {n:>6}")
    if base:
        print(f"Taille : index {index / 1e6:.1f} Mo sur une base de {base / 1e6:.1f} Mo"
              f" ({index / base:.0%})")
    if builders_en_echec:
        print(f"\n⚠ builders en échec (index incomplet) : {', '.join(builders_en_echec)}",
              file=sys.stderr)
        raise Echec("BuilderEnEchec")
    if extraits_en_echec:
        print(f"\n⚠ {extraits_en_echec} extrait(s) non calculé(s)", file=sys.stderr)
        raise Echec("ExtraitsEnEchec")
    return mesures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Vider et reconstruire l'index")
    parser.add_argument("--source", choices=["entity_notes","events","financial_flows","entities"],
                        help="Limiter à une source")
    parser.add_argument("--etat", action="store_true",
                        help=f"consigner l'essai dans {index_sens.ETAT}, à côté de la base"
                             " (passe de collecte)")
    args = parser.parse_args()

    # Les colonnes du modèle arrivent par le rattrapage du schéma : une base
    # qu'aucune passe n'a encore migrée les reçoit ici.
    init_db()
    # Sans `foreign_keys` : une entité fusionnée entre la lecture et l'écriture
    # ne doit pas faire tomber l'indexation entière.
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        mesures = construire(conn, reset=args.reset, source=args.source)
    except Exception as e:                              # noqa: BLE001
        if args.etat:
            if isinstance(e, Echec):
                motif = str(e)
            else:
                from scripts.collect_loop import forme_erreur
                motif = forme_erreur(f"{type(e).__name__}: {e}")
            index_sens.ecrire_etat(Path(DB_PATH).parent, reussi=False, motif=motif)
        if not isinstance(e, Echec):
            print(f"  ✗ {type(e).__name__}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.etat:
        index_sens.ecrire_etat(Path(DB_PATH).parent, reussi=True, mesures=mesures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
