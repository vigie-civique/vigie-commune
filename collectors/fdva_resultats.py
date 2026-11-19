"""
fdva_resultats.py — Subventions FDVA accordées aux associations du territoire.

Source : le tableau des résultats d'une campagne du Fonds pour le développement
de la vie associative (FDVA 2 : fonctionnement global, projet innovant), publié
en PDF par la direction régionale chargée de la vie associative (DRAJES). Pour
l'Occitanie : ac-montpellier.fr, « FDVA 2 … les résultats ». Une ligne par
demande : SIRET, n° de dossier Compte Asso, association, intitulé, description,
coût du projet, montant demandé, montant accordé (« - € » : refusé).

Pourquoi ce collecteur, alors que le jaune budgétaire (`jaune_associations`)
porte déjà le programme 163 : le jaune liste ce qui a été VERSÉ, avec trois ans
de retard (PLF 2025 = versements 2023). Les résultats de la DRAJES disent ce qui
est ACCORDÉ l'année même. Les deux se complètent ; ils ne s'additionnent pas.

Le fichier se DÉPOSE à la main au magasin national (`NATIONAL_STORE/fdva/`) :
le site de l'académie refuse les clients automatisés (403), et la règle est de
ne pas contourner un refus. Un magasin sans fichier n'est pas une panne.

Ce qui entre en base (avis DPO du 07/11/2026) :
  - les seules demandes ACCORDÉES, d'une ASSOCIATION du périmètre, rattachées
    par SIRET puis SIREN au siège (`db.beneficiaires_locaux`), jamais par le
    nom. Les refus ne sont pas gardés : rangés dans `financial_flows`, ils
    sortiraient sur le site ;
  - l'intitulé et le montant accordé ; ni la description, ni le coût du projet
    (budget de l'association en « fonctionnement global », coût d'un projet en
    « projet innovant » : une même colonne pour deux sens), ni le demandé ;
  - l'état `engage` : une attribution n'atteste pas un paiement ;
  - un intitulé qui semble nommer une personne entre en `probable` et attend
    la relecture de l'atelier.
Le PDF n'est pas copié dans `raw_documents` : il couvre toute la région, refus
et descriptions compris. Son empreinte, sa campagne et ses comptes sont gardés
dans `fdva_campagnes` — de quoi prouver la source.

Usage :
  python3 -m collectors.fdva_resultats            # importe les PDF du magasin
  python3 -m collectors.fdva_resultats --dry-run
  python3 -m collectors.fdva_resultats --fichier chemin.pdf --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from pathlib import Path

from . import mesure
from .config import DB_PATH, NATIONAL_STORE
from .db import beneficiaire_local, beneficiaires_locaux, pivot_ids

SOURCE = "fdva-drajes"
CACHE = NATIONAL_STORE / "fdva"

ENTETE = ("Nb", "Service", "N° Siret", "N° Dossier Compte Asso", "Association",
          "Intitulé de la demande", "Description", "Coût total du projet",
          "Montant demandé", "Montant accordé")
_CAMPAGNE = re.compile(r"campagne\s+(\d{4})", re.I)
# Bornes d'un dépôt manuel : la campagne 2026 d'Occitanie fait 649 pages. Au-delà,
# la lecture menacerait le plafond mémoire de la passe de nuit (MemoryMax).
OCTETS_MAX = 50 * 1024 * 1024
PAGES_MAX = 1500


class RefusFdva(ValueError):
    """Un document refusé pour sa FORME, par ce collecteur : son message ne cite
    que le nom du fichier, une page, un compte. Seul ce refus est noté dans
    `fdva_refus` ; toute autre erreur (panne, droits, mémoire, pdfminer) se
    retente à la passe suivante et ne recopie aucun texte du PDF."""

# Ce qui, dans un intitulé, peut désigner une personne : une civilité suivie
# d'un nom, un « Prénom NOM », un courriel, un téléphone, une naissance.
# Large exprès : un faux positif part en relecture, un faux négatif est publié.
_PERSONNE = re.compile(
    r"\b(?:M\.|Mme|Mlle|Monsieur|Madame)\s+[A-ZÉÈ]"
    r"|\b[A-ZÉÈ][a-zéèëïîôç]+(?:-[A-ZÉÈ][a-zéèëïîôç]+)?\s+[A-ZÉÈ]{2,}(?:[ -][A-ZÉÈ]{2,})*\b"
    r"|@|\b0\d(?:[ .]?\d{2}){4}\b|\bn[ée]e?\s+le\b")


def _propre(valeur) -> str:
    return re.sub(r"\s+", " ", str(valeur or "")).strip()


def montant(valeur) -> int:
    """« 2 000 € » → 2000 ; « - € » (refusé) → 0."""
    t = _propre(valeur)
    if t.startswith("-"):
        return 0
    chiffres = re.sub(r"[^\d]", "", t.split(",")[0])
    return int(chiffres) if chiffres else 0


def nomme_une_personne(intitule: str) -> bool:
    return bool(_PERSONNE.search(intitule or ""))


def lire_pdf(chemin: Path) -> tuple[int, list[dict]]:
    """(campagne, lignes) : la campagne se lit dans le document, pas dans le
    nom du fichier ; les lignes sont celles du tableau, en-têtes retirés."""
    import pdfplumber

    lignes, campagne = [], None
    with pdfplumber.open(str(chemin)) as pdf:
        if len(pdf.pages) > PAGES_MAX:
            raise RefusFdva(f"{chemin.name} : {len(pdf.pages)} pages, plus que les "
                             f"{PAGES_MAX} admises")
        for page in pdf.pages:
            if campagne is None:
                m = _CAMPAGNE.search(page.extract_text() or "")
                campagne = int(m.group(1)) if m else None
            for table in page.extract_tables():
                for brute in table:
                    cellules = [_propre(c) for c in brute]
                    if not cellules or cellules[0] == ENTETE[0]:
                        continue
                    if len(cellules) != len(ENTETE) or not cellules[0].isdigit():
                        raise RefusFdva(f"{chemin.name}, p. {page.page_number} : ligne "
                                         f"de {len(cellules)} colonnes, forme inattendue")
                    lignes.append(dict(zip(ENTETE, cellules)))
            # pdfplumber garde en cache les objets de chaque page tant que le
            # document est ouvert : sans fermeture, la mémoire croît page à page.
            page.close()
    if campagne is None:
        raise RefusFdva(f"{chemin.name} : campagne introuvable dans le document")
    if not lignes:
        raise RefusFdva(f"{chemin.name} : aucun tableau de résultats lu")
    return campagne, lignes


def est_association(conn, entity_id: int) -> bool:
    """Une association, et non une entreprise individuelle qu'un SIRET erroné
    aurait désignée : fiche RNA, ou catégorie juridique SIRENE 92xx."""
    if conn.execute("SELECT 1 FROM associations WHERE entity_id=?", (entity_id,)).fetchone():
        return True
    row = conn.execute("SELECT raw_data FROM businesses WHERE entity_id=?", (entity_id,)).fetchone()
    if not row:
        return False
    m = re.search(r'"nature_juridique"\s*:\s*"?(\d{2})', row["raw_data"] or "")
    return bool(m and m.group(1) == "92")


def _table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS fdva_campagnes (
                        sha256    TEXT PRIMARY KEY,
                        fichier   TEXT NOT NULL,
                        campagne  INTEGER NOT NULL,
                        lignes    INTEGER NOT NULL,
                        accordees INTEGER NOT NULL,
                        retenues  INTEGER NOT NULL,
                        lu_le     TEXT DEFAULT (datetime('now')))""")
    # Un fichier refusé n'est pas relu à chaque passe : il faut le redéposer
    # (nouvelle empreinte) pour qu'il soit tenté de nouveau.
    conn.execute("""CREATE TABLE IF NOT EXISTS fdva_refus (
                        sha256  TEXT PRIMARY KEY,
                        fichier TEXT NOT NULL,
                        motif   TEXT NOT NULL,
                        lu_le   TEXT DEFAULT (datetime('now')))""")


def importer(conn, lignes: list[dict], campagne: int, etat_id: int,
             dry_run: bool = False) -> dict:
    index = beneficiaires_locaux(conn)
    stats = {"lignes": len(lignes), "refusees": 0, "sans_identifiant": 0,
             "hors_perimetre": 0, "pas_une_association": 0, "retenues": 0,
             "a_relire": 0, "nomme_peut_etre": 0, "inserees": 0, "deja": 0}
    for rec in lignes:
        accorde = montant(rec["Montant accordé"])
        if accorde <= 0:
            stats["refusees"] += 1
            continue
        siret = re.sub(r"\D", "", rec["N° Siret"])
        if len(siret) not in (9, 14):
            stats["sans_identifiant"] += 1
            continue
        to_id = beneficiaire_local(index, siret)
        if to_id is None:
            stats["hors_perimetre"] += 1
            continue
        if not est_association(conn, to_id):
            stats["pas_une_association"] += 1
            continue
        stats["retenues"] += 1
        intitule = rec["Intitulé de la demande"]
        # Tout flux FDVA attend l'atelier (relecture vigie-securite du 08/11) :
        # l'intitulé est un texte libre, et aucune expression ne reconnaît sûrement
        # une personne (« Atelier animé par Jean Dupont » échappait à `_PERSONNE`).
        # Le repérage reste, pour signaler en relecture ce qui demande le plus d'attention.
        confiance = "probable"
        stats["a_relire"] += 1
        stats["nomme_peut_etre"] += nomme_une_personne(intitule)
        # Le n° de dossier fait partie de la clé : un même dossier porte
        # parfois deux lignes (deux volets), et deux dossiers le même intitulé.
        # Il passe AVANT l'intitulé, que la troncature coupe sans emporter la clé.
        description = (f"[P163] FDVA 2 {campagne} — dossier "
                       f"{rec['N° Dossier Compte Asso']} — {intitule}")[:250]
        if conn.execute(
                "SELECT 1 FROM financial_flows WHERE source=? AND to_id=? AND year=?"
                " AND description=? LIMIT 1",
                (SOURCE, to_id, campagne, description)).fetchone():
            stats["deja"] += 1
            continue
        if dry_run:
            print(f"  [DRY] {campagne} {accorde:>7} € → #{to_id} {confiance:9} {description[:70]}")
            stats["inserees"] += 1
            continue
        conn.execute(
            "INSERT INTO financial_flows (type, year, amount, from_id, to_id, description,"
            " source, confidence, statut) VALUES"
            " ('subvention_etat_association', ?, ?, ?, ?, ?, ?, ?, 'engage')",
            (campagne, accorde, etat_id, to_id, description, SOURCE, confiance))
        stats["inserees"] += 1
    return stats


def empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def run(dry_run: bool = False, fichiers: list[Path] | None = None) -> None:
    fichiers = fichiers or sorted(p for p in CACHE.glob("*.pdf") if p.is_file())
    if not fichiers:
        # Le dépôt est manuel : pas de fichier n'est pas une panne, mais le
        # zéro doit se lire comme tel.
        print(f"[fdva] aucun résultat FDVA déposé dans {CACHE} : rien à lire")
        return
    # Une simulation n'écrit RIEN : base en lecture seule. Sans quoi le
    # `CREATE TABLE` (validé aussitôt par SQLite) et l'entité « État » créée par
    # `pivot_ids` restaient dans la base qu'on croyait seulement interroger.
    if dry_run:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    if not dry_run:
        _table(conn)
    etat_id = None if dry_run else pivot_ids(conn)["etat"]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('fdva_campagnes', 'fdva_refus')")}
    for chemin in fichiers:
        try:
            sha = empreinte(chemin)
        except OSError as exc:
            print(f"  ⚠️ {chemin.name} illisible ({type(exc).__name__}) : passé")
            continue
        if "fdva_campagnes" in tables and conn.execute(
                "SELECT 1 FROM fdva_campagnes WHERE sha256=?", (sha,)).fetchone():
            print(f"[fdva] {chemin.name} déjà lu (même empreinte)")
            continue
        if "fdva_refus" in tables and conn.execute(
                "SELECT 1 FROM fdva_refus WHERE sha256=?", (sha,)).fetchone():
            print(f"[fdva] {chemin.name} déjà refusé (même empreinte) : à redéposer corrigé")
            continue
        print(f"\n[fdva] {chemin.name} — lecture du tableau (quelques minutes)")
        try:
            if chemin.stat().st_size > OCTETS_MAX:
                raise RefusFdva(f"{chemin.name} : {chemin.stat().st_size} octets, plus que "
                                 f"les {OCTETS_MAX} admis")
            campagne, lignes = lire_pdf(chemin)
        except RefusFdva as exc:
            motif = str(exc)[:300]
            print(f"  ⚠️ refusé : {motif}")
            if not dry_run:
                conn.execute("INSERT OR REPLACE INTO fdva_refus (sha256, fichier, motif)"
                             " VALUES (?,?,?)", (sha, chemin.name, motif))
                conn.commit()
            continue
        except Exception as exc:  # noqa: BLE001 — un fichier en échec n'arrête pas les autres
            # Ni message (il peut citer le PDF), ni trace en base : retenté demain.
            print(f"  ⚠️ {chemin.name} : lecture en échec ({type(exc).__name__}), retentée "
                  "à la prochaine passe")
            continue
        mesure.lu(f"fdva-{campagne}", len(lignes), list(ENTETE), complete=True)
        stats = importer(conn, lignes, campagne, etat_id, dry_run=dry_run)
        print(f"  campagne {campagne}, empreinte {sha[:16]}… (dépôt manuel : "
              f"le magasin n'en garantit que l'intégrité)")
        for cle, valeur in stats.items():
            print(f"  {cle:20}: {valeur}")
        if not dry_run:
            conn.execute("INSERT INTO fdva_campagnes (sha256, fichier, campagne, lignes,"
                         " accordees, retenues) VALUES (?,?,?,?,?,?)",
                         (sha, chemin.name, campagne, stats["lignes"],
                          stats["lignes"] - stats["refusees"], stats["retenues"]))
            conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fichier", type=Path, action="append",
                        help="un PDF de résultats, à la place de ceux du magasin")
    args = parser.parse_args()
    run(dry_run=args.dry_run, fichiers=args.fichier)
