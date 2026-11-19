"""
syndicats_comptes.py — Les comptes des syndicats auxquels l'intercommunalité adhère.

Source : « Balances comptables des syndicats depuis 2010 », DGFiP, sur
data.economie.gouv.fr. Une ligne par syndicat, exercice, budget et compte.

`banatic` pose les syndicats — SIAEP, SYMTOMA, EPTB, syndicat d'énergie, PETR —
et la relation `adhère_à` qui les relie à l'EPCI. Il n'en disait rien d'autre :
à Lasalle, huit fiches sans un acte, sans un flux, sans un chiffre. Or c'est là
que passe l'eau, les déchets, les rivières et l'électrification — des budgets
que la commune ne vote plus, et qu'elle finance pourtant par sa cotisation.

Le syndicat se désigne par son SIREN, jamais par son nom : la fiche `businesses`
quand il y en a une, la note que `banatic` écrit sinon. Rien n'est cherché par
le libellé — « SIAEP de Lasalle » et « SYNDICAT AEP DE LA REGION DE LASALLE »
sont deux personnes morales.

Ce qui est retenu, poste par poste, est écrit dans `POSTES` ; les montants sont
les OPÉRATIONS BUDGÉTAIRES NETTES de l'exercice (`obnetdeb` / `obnetcre`), et le
SOLDE pour l'encours de la dette. Les opérations d'ordre (amortissements,
cessions, travaux en régie) sont écartées : ce sont des écritures, pas de
l'argent qui entre ou qui sort.

Ce collecteur ne crée aucun flux : ce que la balance montre est la somme de
TOUS les membres (« contributions des membres »), pas la part de cet EPCI. En
tirer un flux EPCI → syndicat serait attribuer un montant qu'aucune pièce
n'attribue.

L'INTERCOMMUNALITÉ ELLE-MÊME (23/10/2026). Sa balance a la même forme, dans un
autre jeu (« groupements à fiscalité propre »), et aucun collecteur ne la
lisait. C'est elle qui dit, an par an, ce que l'EPCI reçoit de chaque financeur
et ce qu'il verse aux associations (6574 / 65748) — le total contre lequel se
mesurent les subventions nommées : à Lasalle, 247 à 467 k€ par an mandatés
(2019-2025), pour 16 à 33 flux nommés par an — et aucun certaines années. Mêmes postes, même table ; la source
(`dgfip-gfp`) dit de qui ce sont les comptes. Aucun flux non plus : la balance
ne nomme aucun bénéficiaire.

Usage :
  python3 -m collectors.syndicats_comptes
  python3 -m collectors.syndicats_comptes --dry-run
"""

import argparse
import re
import sqlite3
import urllib.parse
from collections import defaultdict

from .archive import fetch_json
from .config import DB_PATH, HEADERS

API = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
       "balances-comptables-des-syndicats-depuis-2010/exports/json")
SOURCE = "dgfip-syndicats"
API_EPCI = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
            "balances-comptables-des-groupements-a-fiscalite-propre-depuis-2010/exports/json")
SOURCE_EPCI = "dgfip-gfp"

# (poste, sens, préfixes retenus, préfixes écartés). Les préfixes valent pour
# les nomenclatures M14, M57 et M49 (eau) — la classe 6 est une charge et la
# classe 7 un produit dans les trois ; les subdivisions de 747 et 131 nomment le
# financeur dans les trois.
#   charge  : débit réel − crédit réel
#   produit : crédit réel − débit réel
#   encours : sc − sd, au 31/12
# « Réel » : OBNETDEB et OBNETCRE comprennent les opérations d'ordre budgétaires
# (notice DGFiP `StructureBalD2025_OpenData`), isolées dans OOBDEB et OOBCRE —
# on les retranche colonne par colonne. Une liste de comptes d'ordre n'y
# suffisait pas : la variation de stocks (7135) gonflait les recettes d'un
# budget annexe au-delà de ses ventes, et la sortie d'un bien (21321 crédité
# par ordre) amputait de 220 k€ les dépenses d'équipement 2025 de la CC
# Causses Aigoual Cévennes, de 1 M€ celles de 2021 (relevé du 24/10/2026).
_ORDRE_6 = ("68", "675", "676")
_ORDRE_7 = ("72", "775", "776", "777", "78")
POSTES = (
    ("depenses_fonctionnement", "charge",  ("6",),  _ORDRE_6),
    ("recettes_fonctionnement", "produit", ("7",),  _ORDRE_7),
    # Le chapitre 012, pas le seul 64 : un budget annexe paie souvent son
    # personnel en 621, mis à disposition par la collectivité (6215 en M4 et
    # M49, 6211 en M57) — le budget Déchets de la CC n'a aucun salarié en 64,
    # et ses remboursements sur rémunérations (64198) le faisaient passer sous
    # zéro. Les remboursements en 9 sont au chapitre 013, en recettes : 6419,
    # 6459, 6479, et en M57 développée 6439 (depuis 2023) et 6449 (2024).
    # Composition vérifiée le 24/10/2026 sur les plans de comptes normatifs
    # (odm-budgetaire.org, M14/M57/M4/M41/M43/M49, 2019-2026) et le tome 2 de
    # l'instruction M57 (« 621, 631, 633 et 64 […] codifié 012 »).
    ("personnel",               "charge",  ("621", "631", "633", "64"),
                                           ("6419", "6439", "6449", "6459", "6479")),
    ("subventions_versees",     "charge",  ("657",), ()),
    # Dans 657 : les associations et autres personnes de droit privé (6574 en
    # M14, 6574x en M57) — à part des communes, CCAS et budgets annexes (6573x).
    ("subventions_personnes_privees", "charge", ("6574",), ()),
    ("ventes_et_redevances",    "produit", ("70",), ()),
    # Ce que paient les membres — communes, EPCI — pour faire vivre le syndicat.
    ("contributions_membres",   "produit", ("7474", "7475"), ()),
    ("depenses_investissement", "charge",  ("20", "21", "23"), ("204",)),
    ("subventions_equipement_versees", "charge", ("204",), ()),
    ("encours_dette",           "encours", ("16",), ("1688",)),
    # Subventions REÇUES, fonctionnement (747x) et investissement (131x), par
    # financeur. L'Agence de l'eau n'a pas de compte à elle : elle tombe dans
    # « autres », 7478 / 1318.
    ("subventions_etat",        "produit", ("7471", "1311"), ()),
    ("subventions_region",      "produit", ("7472", "1312"), ()),
    ("subventions_departement", "produit", ("7473", "1313"), ()),
    ("subventions_europe",      "produit", ("7477", "1317"), ()),
    ("subventions_autres",      "produit", ("7478", "1318"), ()),
)

_SIREN_NOTE = re.compile(r"SIREN:\s*(\d{9})")


def syndicats_du_territoire(conn) -> list[tuple[int, str, str]]:
    """(entity_id, siren, nom) des syndicats auxquels l'EPCI adhère.

    Le SIREN vient de `businesses` ; à défaut, de la note BANATIC, pour une
    base où `banatic` n'a pas encore été rejoué depuis qu'il le range."""
    out = {}
    for r in conn.execute(
        "SELECT DISTINCT e.id, e.name, b.siren FROM relations r"
        " JOIN entities e ON e.id = r.to_id"
        " LEFT JOIN businesses b ON b.entity_id = e.id"
        " WHERE r.relation_type = 'adhère_à' AND r.source = 'banatic'"
    ):
        siren = r[2] if r[2] and len(r[2]) == 9 else None
        if siren is None:
            for (note,) in conn.execute(
                "SELECT note FROM entity_notes WHERE entity_id=? AND source='BANATIC'",
                (r[0],)
            ):
                m = _SIREN_NOTE.search(note or "")
                if m:
                    siren = m.group(1)
                    break
        if siren:
            out[siren] = (r[0], siren, r[1])
    return list(out.values())


def _f(rec: dict, cle: str) -> float:
    try:
        return float(rec.get(cle) or 0)
    except (TypeError, ValueError):
        return 0.0


def _retenu(compte: str, prefixes: tuple, ecartes: tuple) -> bool:
    return compte.startswith(prefixes) and not compte.startswith(ecartes)


def agreger(lignes: list[dict]) -> dict[tuple, dict]:
    """{(exercice, budget) : {libellé, nomenclature, postes{poste: montant}}}."""
    out: dict[tuple, dict] = {}
    for rec in lignes:
        compte = str(rec.get("compte") or "")
        annee = str(rec.get("exer") or "")[:4]
        budget = str(rec.get("ident") or "")
        if not (compte and annee.isdigit() and budget):
            continue
        cle = (int(annee), budget)
        bloc = out.setdefault(cle, {"libelle": rec.get("lbudg"),
                                    "nomenclature": rec.get("nomen"),
                                    "postes": defaultdict(float)})
        for poste, sens, prefixes, ecartes in POSTES:
            if not _retenu(compte, prefixes, ecartes):
                continue
            debit = _f(rec, "obnetdeb") - _f(rec, "oobdeb")
            credit = _f(rec, "obnetcre") - _f(rec, "oobcre")
            if sens == "charge":
                v = debit - credit
            elif sens == "produit":
                v = credit - debit
            else:
                v = _f(rec, "sc") - _f(rec, "sd")
            bloc["postes"][poste] += v
    for bloc in out.values():
        # Un encours négatif n'est pas une dette : c'est un compte d'emprunt
        # remboursé sans avoir jamais été crédité — au budget eau de la CC, des
        # emprunts repris en 2023 que la balance ne porte pas, et 900 653 € de
        # capital remboursé servis comme « −900 653 € de dette ». L'encours
        # n'est pas lisible : on le dit, on ne l'affiche pas.
        if bloc["postes"].get("encours_dette", 0) < 0:
            bloc["postes"]["encours_dette_non_lisible"] = -bloc["postes"].pop("encours_dette")
    return out


def telecharger(siren: str, api: str = API, source: str = SOURCE) -> list[dict]:
    if not re.fullmatch(r"\d{9}", siren):
        raise ValueError(f"SIREN illisible : {siren!r}")
    requete = urllib.parse.urlencode({"where": 'siren="%s"' % siren})
    url = f"{api}?{requete}"
    # 15 s au plus d'ordinaire (31/10) : au-delà, l'export est en panne, et le
    # service rejoue le step plus tard plutôt que d'attendre ici.
    # Le compte de l'EPCI se relit en entier. Celui des syndicats aussi, mais
    # leur LISTE vient de la base (adhésions BANATIC) : quand elle change, le
    # total bouge sans que la source ait rendu moins.
    return fetch_json(url, source=source, timeout=60, headers=HEADERS,
                      complete=source == SOURCE_EPCI)


def epci_du_territoire(conn) -> tuple[int, str, str] | None:
    """(entity_id, siren, nom) de l'intercommunalité de l'instance.

    La fiche par le SIREN de la configuration d'abord (`entite_par_siren`, comme
    `upsert_entity`), puis par son nom normalisé (`epci_nom`) ; le SIREN par la
    configuration, jamais par une recherche de libellé.

    Le nom seul ne suffit pas : quand SIRENE a déjà écrit la communauté sous le
    libellé du registre, `pivot_ids` et les autres collecteurs retrouvent CETTE
    fiche par le SIREN et n'en créent aucune au nom de `epci_nom`. Dossier
    national du 31/10/2026 (CC du Pays Sabolien) : « aucune fiche à ce nom »,
    balance non lue."""
    from .config import EPCI_NOM, EPCI_SIREN
    from .db import entite_par_siren
    from .nom_normalise import normaliser
    if not (EPCI_NOM and re.fullmatch(r"\d{9}", EPCI_SIREN or "")):
        return None
    eid = entite_par_siren(conn, EPCI_SIREN)
    if eid is None:
        r = conn.execute("SELECT id FROM entities WHERE type='service' AND name_norm=?"
                         " ORDER BY id LIMIT 1", (normaliser(EPCI_NOM),)).fetchone()
        eid = r[0] if r else None
    return (eid, EPCI_SIREN, EPCI_NOM)


def importer(conn, entity_id: int, siren: str, lignes: list[dict],
             dry_run: bool = False, source: str = SOURCE) -> int:
    n = 0
    if not dry_run:
        # La balance relue remplace la précédente : un poste qu'une règle ne
        # produit plus (l'encours devenu illisible) ne doit pas lui survivre.
        conn.execute("DELETE FROM comptes_syndicats WHERE siren=? AND source=?",
                     (siren, source))
    for (annee, budget), bloc in sorted(agreger(lignes).items()):
        for poste, montant in bloc["postes"].items():
            n += 1
            if dry_run:
                continue
            conn.execute(
                "INSERT INTO comptes_syndicats (entity_id, siren, year, budget,"
                " libelle_budget, nomenclature, poste, montant, source)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(siren, year, budget, poste) DO UPDATE SET"
                " entity_id=excluded.entity_id, montant=excluded.montant,"
                " libelle_budget=excluded.libelle_budget,"
                " nomenclature=excluded.nomenclature",
                (entity_id, siren, annee, budget, bloc["libelle"],
                 bloc["nomenclature"], poste, round(montant, 2), source))
    return n


def _echec(nom: str, siren: str, exc: Exception) -> None:
    # La ligne `[step] … : échec → …` est celle que le service local classe :
    # un délai dépassé partout fait rejouer le step, au lieu d'un défaut (31/10,
    # l'export DGFiP muet un quart d'heure). Le SIREN sert de clé : un nom de
    # syndicat peut porter une virgule, qui couperait le résumé.
    print(f"  ⚠️ {nom} ({siren}) : {exc}")
    print(f"  [syndicats] {siren} : échec → {type(exc).__name__}: {exc}")


def run(dry_run: bool = False):
    conn = sqlite3.connect(str(DB_PATH))
    # `entite_par_siren` lit ses lignes par nom de colonne.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    echecs = []

    epci = epci_du_territoire(conn)
    if epci and epci[0] is None:
        # Sans fiche, la balance s'écrirait avec `entity_id` NULL, que le
        # snapshot écarte : lue, comptée au journal, et invisible.
        print(f"  ⚠️ {epci[2]} ({epci[1]}) : aucune fiche à ce nom — balance non lue")
        print(f"  [syndicats] {epci[1]} : échec → aucune fiche à ce nom")
        echecs.append(epci[1])
    elif epci:
        entity_id, siren, nom = epci
        try:
            lignes = telecharger(siren, API_EPCI, SOURCE_EPCI)
        except Exception as exc:
            _echec(nom, siren, exc)
            echecs.append(siren)
        else:
            n = importer(conn, entity_id, siren, lignes, dry_run=dry_run, source=SOURCE_EPCI)
            annees = sorted({str(l.get("exer") or "")[:4] for l in lignes} - {""})
            portee = f"{annees[0]}-{annees[-1]}" if annees else "aucun exercice publié"
            print(f"  {nom[:55]:<55} {siren}  {len(lignes):>5} comptes → {n:>4} postes  ({portee})")
    else:
        print("  Pas d'intercommunalité déclarée (`epci_nom`, `epci_siren`) : ses comptes ne sont pas lus.")

    syndicats = syndicats_du_territoire(conn)
    if not syndicats:
        # Zéro se lit : soit l'EPCI n'adhère à aucun syndicat mixte, soit
        # `banatic` n'a pas tourné.
        print("  Aucun syndicat adhérent connu (lancer d'abord `--step banatic`).")

    for entity_id, siren, nom in syndicats:
        try:
            lignes = telecharger(siren)
        except Exception as exc:
            _echec(nom, siren, exc)
            echecs.append(siren)
            continue
        n = importer(conn, entity_id, siren, lignes, dry_run=dry_run)
        annees = sorted({str(l.get("exer") or "")[:4] for l in lignes} - {""})
        portee = f"{annees[0]}-{annees[-1]}" if annees else "aucun exercice publié"
        print(f"  {nom[:55]:<55} {siren}  {len(lignes):>5} comptes → {n:>4} postes  ({portee})")

    if not dry_run:
        conn.commit()
    conn.close()
    # Une balance non lue n'est pas une balance vide : rendre la main en silence
    # ferait passer le step pour abouti jusqu'au bout de son TTL (cf. guichets).
    if echecs:
        raise RuntimeError(f"relevés en échec : {', '.join(echecs)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
