"""
jaune_associations.py — Subventions de l'ÉTAT aux associations du territoire.

Source : l'annexe « jaune » au projet de loi de finances, « Effort financier de
l'État en faveur des associations », publiée par les ministères économiques et
financiers sur data.economie.gouv.fr. Elle liste, association par association,
ce que chaque programme budgétaire lui a versé l'année écoulée : FDVA, culture,
cinéma, politique de la ville, sport, bourses scolaires…

C'est le seul registre national de l'argent de l'État aux associations. Les
autres steps n'en voient que ce qui passe par la commune (`subventions`,
`dotations`) ou par une collectivité qui publie (`subv_ouvertes`, `region`).

Le bénéficiaire se reconnaît à son identifiant, jamais à son nom : SIRET quand
le millésime donne le NIC, SIREN sinon, RNA quand il y est — et toujours par
`db.beneficiaires_locaux`, qui ne rattache un SIREN que si le SIÈGE est d'ici.
Chercher « Lasalle » dans ce jeu remonte l'ECAM LaSalle de Lyon et son million
d'euros de recherche universitaire : le nom ne prouve rien.

Les millésimes n'ont ni les mêmes colonnes ni la même profondeur
d'identification — cf. `MILLESIMES`. L'export 2022 (PLF 2024) est publié CASSÉ,
une seule colonne par ligne : il est écarté, et l'écart est écrit ici plutôt
que découvert à nouveau.

Les exports sont les mêmes pour tous les dossiers : ils vivent au magasin
national (`NATIONAL_STORE/jaune/`), déposés la nuit par l'écrivain
(`scripts/rafraichir_magasin.py`, `STEPS_NATIONAUX`). Un dossier les lit ; il ne
les télécharge que s'ils manquent, et une copie périmée se lit en le disant
(`national_store.deposer`). Avant le 31/10/2026, chaque dossier retéléchargeait
les quatre exports : de 19 à 289 s, et 45 Mo de plus dans `raw_documents`.

Usage :
  python3 -m collectors.jaune_associations
  python3 -m collectors.jaune_associations --dry-run
"""

import argparse
import csv
import io
import re
import sqlite3

from . import mesure
from .config import DB_PATH, NATIONAL_STORE
from .db import beneficiaire_local, beneficiaires_locaux, pivot_ids
from .national_store import deposer, ecrivain, retirer_hors_magasin

API_BASE = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"
SOURCE = "jaune-associations"
CACHE = NATIONAL_STORE / "jaune"
# Un millésime publié ne change plus (dernières modifications : 13/12/2018 pour
# le PLF 2014, 23/12/2024 pour le PLF 2025, relevées le 31/10/2026) ; le suivant
# arrive par une ligne de MILLESIMES, donc par un fichier absent du magasin, lu
# tout de suite. Trois mois rattrapent une republication corrigée sans
# retélécharger 60 Mo chaque nuit ; la copie périmée reste lisible six mois.
CACHE_JOURS = 90

# (exercice annoncé, jeu, colonnes). L'exercice est RELU dans la colonne
# `millesime` quand le jeu en porte une : le jeu nommé « plf-2016 » contient
# en réalité le jaune du PLF 2015, subventions 2014.
MILLESIMES = [
    (2023, "plf25-donnees-de-l-annexe-jaune-effort-financier-de-l-etat-en-faveur-des-associations",
     {"programme": "programme", "nom": "denomination", "montant": "montant",
      "objet": "objet_2023", "nic": "nic"}),
    (2016, "projet-de-loi-de-finances-pour-2018-plf-2018-donnees-de-lannexe-jaune-effort-fin",
     {"programme": "programme_2016", "nom": "denomination", "montant": "montant",
      "objet": "objet", "nic": "nic", "rna": "rna"}),
    # Ces deux-là ne donnent que le SIREN : seules les associations dont le
    # siège est d'ici s'y rapprochent — c'est le cas de presque toutes.
    (2014, "plf-2016-jaune-effort-financier-de-letat-en-faveur-des-associations-",
     {"programme": "programme", "nom": "association", "montant": "2014",
      "objet": "objet"}),
    (2012, "plf-2014-jaune-donnees-associations-subventionnees-2012",
     {"programme": "programme", "nom": "association", "montant": "subvention_2012",
      "objet": "objet"}),
]

_MILLESIME_RE = re.compile(r"Subventions\s+(\d{4})", re.I)


def _telecharger(jeu: str, colonnes: dict) -> list[dict]:
    """L'export intégral d'un millésime, filtré ici et non par l'API — même
    raison que `occitanie_region` : le compte de ce qui est écarté doit être
    mesurable. Une quinzaine de mégaoctets par millésime, lus au magasin."""
    url = f"{API_BASE}/{jeu}/exports/csv?delimiter=%3B"
    fichier = CACHE / f"{jeu}.csv"
    chemin = deposer(url, fichier, SOURCE, CACHE_JOURS)
    try:
        texte = chemin.read_bytes().decode("utf-8-sig", errors="replace")
    finally:
        retirer_hors_magasin(chemin, fichier)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    # Le magasin ne lit pas un CSV : un export tronqué ou une page d'erreur y
    # vivrait trois mois en ne rendant que des « hors périmètre ».
    absentes = {colonnes[c] for c in ("programme", "montant")} - set(lecteur.fieldnames or ())
    if absentes:
        raise ValueError(f"{fichier.name} : colonne(s) absente(s) {', '.join(sorted(absentes))}")
    lignes = list(lecteur)
    # Un millésime figé : il ne rend moins que tronqué ou abîmé au magasin.
    mesure.lu(jeu, len(lignes), lecteur.fieldnames, complete=True)
    return lignes


def _propre(valeur) -> str:
    """Le jeu 2023 sépare ses mots par des espaces INSÉCABLES et coupe certains
    libellés d'un saut de ligne au milieu d'un mot."""
    return re.sub(r"\s+", " ", str(valeur or "").replace("\xa0", " ")).strip()


def _montant(valeur) -> int | None:
    try:
        return int(round(float(str(valeur or "").replace(",", ".").replace(" ", ""))))
    except (TypeError, ValueError):
        return None


def identifiant(rec: dict, colonnes: dict) -> str:
    """SIRET si le NIC est là, SIREN sinon, chaîne vide si rien n'est lisible.

    « NR CHORUS » occupe la colonne SIREN quand l'association n'en a pas : ce
    n'est pas un identifiant, et le compléter par le nom est précisément ce que
    ce collecteur refuse."""
    siren = re.sub(r"\D", "", str(rec.get("siren") or ""))
    if len(siren) != 9:
        return ""
    nic = re.sub(r"\D", "", str(rec.get(colonnes.get("nic", ""), "") or ""))
    if nic and len(nic) <= 5:
        return siren + nic.zfill(5)
    return siren


def exercice(rec: dict, annonce: int) -> int:
    m = _MILLESIME_RE.search(str(rec.get("millesime") or ""))
    return int(m.group(1)) if m else annonce


def importer(conn, lignes: list[dict], annonce: int, colonnes: dict,
             etat_id: int, dry_run: bool = False) -> dict:
    index = beneficiaires_locaux(conn)
    stats = {"lignes": len(lignes), "retenues": 0, "inserees": 0, "deja": 0,
             "hors_perimetre": 0, "sans_identifiant": 0, "fdva_deja_accorde": 0}

    for rec in lignes:
        ident = identifiant(rec, colonnes)
        rna = _propre(rec.get(colonnes.get("rna", ""), ""))
        if not ident and not rna:
            stats["sans_identifiant"] += 1
            continue
        to_id = beneficiaire_local(index, ident, rna=rna)
        if to_id is None:
            stats["hors_perimetre"] += 1
            continue
        stats["retenues"] += 1

        annee = exercice(rec, annonce)
        montant = _montant(rec.get(colonnes["montant"]))
        programme = _propre(rec.get(colonnes["programme"]))
        objet = _propre(rec.get(colonnes["objet"]))
        # Le programme ouvre la description : c'est lui qui dit quel ministère
        # a payé, et il sert de clé de dédoublonnage avec l'objet — une même
        # association reçoit souvent deux lignes du même programme la même
        # année (FDVA fonctionnement, puis FDVA formation).
        prog = programme.split(" ")[0] if programme else "?"
        description = f"[P{prog}] {objet}"[:250]

        # Le FDVA ACCORDÉ (`fdva_resultats`, l'année même) et le FDVA VERSÉ (ce
        # jaune, trois ans plus tard) décrivent la même subvention : les deux
        # lignes restent, liées, et ne s'additionnent pas. Tant que ce lien
        # n'est pas codé, la rencontre se signale à chaque passe.
        if prog == "163" and conn.execute(
                "SELECT 1 FROM financial_flows WHERE source='fdva-drajes' AND to_id=? AND year=?"
                " LIMIT 1", (to_id, annee)).fetchone():
            stats["fdva_deja_accorde"] += 1

        if conn.execute(
            "SELECT 1 FROM financial_flows WHERE source=? AND to_id=? AND year=?"
            " AND amount IS ? AND description=? LIMIT 1",
            (SOURCE, to_id, annee, montant, description)
        ).fetchone():
            stats["deja"] += 1
            continue

        if dry_run:
            print(f"  [DRY] {annee} {montant if montant is not None else '?':>9} € "
                  f"→ #{to_id} {_propre(rec.get(colonnes['nom']))[:40]} — {description[:50]}")
            stats["inserees"] += 1
            continue

        # Une subvention figure au jaune parce qu'elle a été VERSÉE : l'annexe
        # liste les associations ayant reçu une subvention l'année écoulée, et
        # les montants sortent de Chorus, l'outil de paiement de l'État (la
        # colonne SIREN le dit elle-même quand elle vaut « NR CHORUS »).
        conn.execute(
            "INSERT INTO financial_flows"
            " (type, year, amount, from_id, to_id, description, source, confidence)"
            " VALUES ('subvention_etat_association', ?, ?, ?, ?, ?, ?, 'verified')",
            (annee, montant, etat_id, to_id, description, SOURCE))
        conn.execute(
            "INSERT OR IGNORE INTO relations"
            " (from_id, to_id, relation_type, source, confidence)"
            " VALUES (?,?,'subventionné',?,'verified')",
            (etat_id, to_id, SOURCE))
        stats["inserees"] += 1

    return stats


def run(dry_run: bool = False):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    etat_id = pivot_ids(conn)["etat"]

    total = 0
    manquants = []
    for annonce, jeu, colonnes in MILLESIMES:
        print(f"\n[jaune] Subventions {annonce} — {jeu}")
        try:
            lignes = _telecharger(jeu, colonnes)
        except (OSError, RuntimeError, ValueError) as exc:
            # Un millésime qui ne répond pas n'empêche pas les autres ; le
            # compteur final dira qu'il manque.
            print(f"  ⚠️ millésime indisponible : {exc}")
            manquants.append(str(annonce))
            continue
        stats = importer(conn, lignes, annonce, colonnes, etat_id, dry_run=dry_run)
        total += stats["inserees"]
        print(f"  lignes du jeu       : {stats['lignes']}")
        print(f"  bénéficiaire local  : {stats['retenues']}")
        print(f"  insérées            : {stats['inserees']}")
        print(f"  déjà en base        : {stats['deja']}")
        # Pas des anomalies : le jeu couvre la France entière. Ils sont là pour
        # qu'un zéro se lise — jeu vide, ou périmètre sans identifiant ?
        print(f"  hors périmètre      : {stats['hors_perimetre']}")
        print(f"  sans identifiant    : {stats['sans_identifiant']}")
        if stats["fdva_deja_accorde"]:
            print(f"  ⚠️ FDVA déjà lu comme ACCORDÉ (fdva-drajes) : {stats['fdva_deja_accorde']} "
                  f"ligne(s) — accordé et versé ne s'additionnent pas, rapprochement à coder "
                  f"(chantier subventions)")

    if not dry_run:
        conn.commit()
    conn.close()
    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"\n{prefix}✅ Jaune associations : {total} flux")
    # L'écrivain du magasin, lui, ne passe pas un millésime sous silence : la
    # passe de nuit doit le compter en échec, sans quoi le magasin vieillit.
    if manquants and ecrivain():
        raise RuntimeError(f"millésime(s) non déposé(s) au magasin : {', '.join(manquants)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
