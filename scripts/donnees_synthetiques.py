#!/usr/bin/env python3
"""Génère une base Vigie FICTIVE, à la forme d'une base réelle.

Pour tester, déboguer un collecteur ou montrer le moteur à un contributeur sans
jamais toucher à une donnée de personne réelle. Rien ici n'est tiré d'une base :
tout sort d'un générateur à graine fixe — mêmes arguments, même base.

    python3 scripts/donnees_synthetiques.py db/synthetique.db
    ... --graine 7 --taille 200      # plus gros, autre tirage

Garanties de fictivité (RGPD : aucune donnée à caractère personnel) :
  • noms de famille inventés par assemblage de syllabes, prénoms d'une liste
    de prénoms rares ou composés ; communes en `99xxx` (aucun code INSEE réel) ;
  • SIREN en `999…`, jamais attribué ; courriels en `example.org` ; pas de
    téléphone, pas de date de naissance complète (année seule) ;
  • la base porte une table `_synthetique` qui le dit, et son nom de fichier
    doit contenir « synth » : un outil qui garde l'accès aux bases réelles
    (celui d'un assistant de code, par exemple) peut ainsi laisser passer
    celle-ci, et elle seule.

Les phrases-pièges de `tests/fixtures/pieges_textes.txt` sont reprises dans les
textes d'actes : un correctif du masque se vérifie sur une base qui les contient.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMUNE = "Valmorin-sur-Test"
VOISINES = ("Quentaral-les-Prés", "Orbelin")

SYLLABES = ("val", "mor", "quen", "tar", "bel", "ior", "zan", "pel", "dru", "mez",
            "sol", "ran", "fyl", "gre", "hou", "lim", "nos", "pir", "tav", "ulm")
PRENOMS = ("Aldéric", "Maëline", "Thibéry", "Orlane", "Gwendal", "Séraphine",
           "Évariste", "Lyséa", "Armand-Louis", "Clémence-Anaïs", "Ysoline", "Balthazar")
SECTEURS = ("Boulangerie", "Menuiserie", "Garage", "Épicerie", "Plomberie",
            "Cabinet", "Atelier", "Maraîchage", "Imprimerie", "Transports")
ASSOS = ("Club de lecture", "Comité des fêtes", "Fanfare", "Amicale des pompiers",
         "Association sportive", "Jardins partagés")
AGREGATS = ("recettes_fonctionnement", "depenses_fonctionnement", "epargne_brute",
            "recettes_investissement", "depenses_investissement", "encours_dette")


def nom_famille(r: random.Random) -> str:
    return "".join(r.choice(SYLLABES) for _ in range(r.choice((2, 3)))).capitalize()


def pieges() -> list[str]:
    f = ROOT / "tests" / "fixtures" / "pieges_textes.txt"
    if not f.exists():
        return []
    out = []
    for ligne in f.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#") and "|" in ligne:
            out.append(ligne.split("|", 1)[0].strip().replace("\\n", "\n"))
    return out


def generer(db: Path, graine: int, taille: int) -> dict[str, int]:
    r = random.Random(graine)
    conn = sqlite3.connect(str(db))
    conn.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    conn.execute("CREATE TABLE _synthetique (graine INTEGER, taille INTEGER, avertissement TEXT)")
    conn.execute("INSERT INTO _synthetique VALUES (?,?,?)",
                 (graine, taille, "Base FICTIVE : aucune donnée réelle."))

    def entite(type_, nom, commune, perimetre, origine="institutionnel"):
        return conn.execute(
            "INSERT INTO entities (type,name,commune,perimetre,confidence,origine,"
            "created_at,updated_at) VALUES (?,?,?,?, 'verified',?, '2026-01-01','2026-01-01')",
            (type_, nom, commune, perimetre, origine)).lastrowid

    communes = [(COMMUNE, "C1")] + [(c, "C2") for c in VOISINES]
    mairie = entite("service", f"Mairie de {COMMUNE}", COMMUNE, "C1")

    personnes = []
    for _ in range(taille):
        nom, prenom = nom_famille(r), r.choice(PRENOMS)
        commune, perim = r.choice(communes)
        eid = entite("person", f"{prenom} {nom.upper()}", commune, perim)
        conn.execute("INSERT INTO persons (entity_id,firstname,lastname,birth_year,gender)"
                     " VALUES (?,?,?,?,?)",
                     (eid, prenom, nom.upper(), r.randint(1945, 2000), r.choice("MF")))
        personnes.append(eid)

    entreprises = []
    for i in range(max(8, taille // 3)):
        commune, perim = r.choice(communes)
        nom = f"{r.choice(SECTEURS)} {nom_famille(r)}"
        eid = entite("business", nom, commune, perim)
        siren = f"999{r.randint(0, 999999):06d}"
        conn.execute("INSERT OR IGNORE INTO businesses (entity_id, siren) VALUES (?,?)",
                     (eid, siren))
        entreprises.append((eid, nom, siren))

    assos = [entite("association", f"{a} de {COMMUNE}", COMMUNE, "C1") for a in ASSOS]

    def relation(a, b, type_, source):
        conn.execute("INSERT OR IGNORE INTO relations (from_id,to_id,relation_type,source,"
                     "confidence) VALUES (?,?,?,?, 'verified')", (a, b, type_, source))

    for p in r.sample(personnes, min(15, len(personnes))):
        relation(p, mairie, r.choice(("maire", "adjoint", "conseiller")), "cm")
    for p in r.sample(personnes, min(taille // 2, len(personnes))):
        relation(p, r.choice(entreprises)[0], "dirigeant", "sirene")
    for p in r.sample(personnes, min(taille // 3, len(personnes))):
        relation(p, r.choice(assos), r.choice(("président", "trésorier", "membre")), "rna")

    textes = pieges()
    debut = date(2020, 1, 15)
    actes = []
    for i in range(max(12, taille // 4)):
        jour = debut + timedelta(days=r.randint(0, 6 * 365))
        corps = (f"Le conseil municipal de {COMMUNE}, réuni le {jour:%d/%m/%Y}, "
                 f"délibère sur l'objet n° {i + 1}. "
                 + (r.choice(textes) if textes else ""))
        actes.append(conn.execute(
            "INSERT INTO events (type,date,title,content,source,origine,cle_acte) "
            "VALUES ('deliberation',?,?,?,'CM','verbatim',?)",
            (jour.isoformat(), f"Délibération d'épreuve n° {i + 1}", corps,
             f"c-{jour.year}-{i + 1}")).lastrowid)

    for annee in range(2020, 2026):
        for _ in range(taille // 6):
            eid, nom, siren = r.choice(entreprises)
            montant = r.randint(500, 80000)
            conn.execute(
                "INSERT INTO financial_flows (type,year,amount,from_id,to_id,event_id,"
                "description,source,origine) VALUES (?,?,?,?,?,?,?,'CM','verbatim')",
                (r.choice(("subvention", "marché", "bail")), annee, montant, mairie,
                 eid, r.choice(actes), f"Flux d'épreuve {annee}"))
        for agregat in AGREGATS:
            conn.execute(
                "INSERT INTO budget_vote (year,scope,agregat,value,unit,source,origine) "
                "VALUES (?,'principal',?,?,'EUR','CM','verbatim')",
                (annee, agregat, r.randint(100_000, 3_000_000)))

    for i in range(max(10, taille // 5)):
        eid, nom, siren = r.choice(entreprises)
        conn.execute(
            "INSERT INTO marches_publics (acheteur_id,acheteur_siren,acheteur_nom,"
            "titulaire_id,titulaire_siren,titulaire_nom,objet,montant,montant_base,"
            "date_notif,source,raw_id,origine) VALUES (?, '999000001',?,?,?,?,?,?,'HT',?,"
            "'synthetique',?,'institutionnel')",
            (mairie, f"Mairie de {COMMUNE}", eid, siren, nom,
             f"Travaux d'épreuve n° {i + 1}", r.randint(2000, 150000),
             (debut + timedelta(days=r.randint(0, 2000))).isoformat(), f"synth-{graine}-{i}"))

    conn.commit()
    bilan = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("entities", "relations", "events", "financial_flows",
                       "marches_publics", "budget_vote")}
    conn.close()
    return bilan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sortie", type=Path)
    p.add_argument("--graine", type=int, default=1)
    p.add_argument("--taille", type=int, default=60, help="nombre de personnes (défaut 60)")
    p.add_argument("--force", action="store_true", help="écraser une base fictive existante")
    a = p.parse_args()

    nom = a.sortie.name.lower()
    if "synth" not in nom:
        sys.exit("le nom du fichier doit contenir « synth » : une base fictive doit se reconnaître.")
    if any(z in str(a.sortie.resolve()) for z in ("/public/static", "/public-data", "/public/build")):
        sys.exit("jamais dans une zone publique.")
    if a.sortie.exists():
        if not a.force:
            sys.exit(f"{a.sortie} existe — --force pour l'écraser.")
        try:
            sqlite3.connect(f"file:{a.sortie}?mode=ro", uri=True).execute(
                "SELECT 1 FROM _synthetique")
        except sqlite3.Error:
            sys.exit(f"{a.sortie} n'est PAS une base fictive : je ne l'écrase pas.")
        a.sortie.unlink()
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    bilan = generer(a.sortie, a.graine, a.taille)
    print(f"[synthétique] {a.sortie} — " + ", ".join(f"{k} {v}" for k, v in bilan.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
