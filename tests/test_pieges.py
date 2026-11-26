"""Les pièges : ce qui ne doit jamais sortir, semé dans la base, et cherché dehors.

Une revue externe (05/10/2026) a semé trente-deux marqueurs dans la base de la
CI — une naissance dans un titre, un domicile dans l'objet d'un marché, une
piste dans les liens d'une élue, un particulier au registre des installations
classées. Vingt-huit sortaient, et le contrôleur rendait « OK ».

Cet essai refait ce geste à chaque passage. Tout ce qui ne doit pas sortir
porte `PIEGE_` ; ce qui doit sortir porte `TEMOIN_`, pour qu'un snapshot vide ne
passe pas pour un snapshot propre. Un filtre nouveau, une sortie nouvelle : son
piège s'ajoute ICI d'abord, et l'essai échoue tant que le filtre ne tient pas.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_reference import ROOT, construire  # noqa: E402

NE = "né le 05/03/1961"


def _pieger_les_textes(racine: Path) -> None:
    """Un dossier et une feuille de séance, relus — et porteurs d'un domicile."""
    dossier = racine / "tests" / "fixtures" / "dossiers" / "eau.md"
    dossier.write_text(
        dossier.read_text(encoding="utf-8")
        + f"\nM. Gaston PIEGE_DOSSIER, {NE}, domicilié 5 rue des Lilas.\n", encoding="utf-8")
    releve = racine / "tests" / "fixtures" / "conseils" / "2021-04-14-cm" / "releve.json"
    r = json.loads(releve.read_text(encoding="utf-8"))
    # Sans chiffre : le vérificateur des feuilles refuse déjà tout nombre absent
    # du procès-verbal, et ce n'est pas lui qu'on éprouve ici.
    r["en_clair"]["apres"]["chapeau"] += " Demande de M. Gaston PIEGE_SEANCE, né à Castres."
    releve.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def _pieger_la_base(db: Path) -> None:
    conn = sqlite3.connect(db)

    def un(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    mairie = un("SELECT id FROM entities WHERE name = 'Mairie d''épreuve'")
    boulangerie = un("SELECT id FROM entities WHERE name = 'Boulangerie d''épreuve'")
    elue = un("SELECT id FROM entities WHERE name = 'Élue de Testonville'")

    # Une piste : l'élue dirigerait la boulangerie, que la commune subventionne.
    conn.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'hypothesis')", (elue, boulangerie))
    conn.execute("INSERT INTO financial_flows (from_id, to_id, type, year, amount, description,"
                 " source, confidence) VALUES (?, ?, 'subvention', 2025, 1500,"
                 " 'Subvention TEMOIN_FLUX', 'ofgl', 'verified')", (mairie, boulangerie))
    # Le déport qui l'accompagnerait, dans un acte que rien n'autorise à sortir.
    conn.execute("INSERT INTO events (type, date, title, source, metadata) VALUES"
                 " ('deliberation', '2025-04-02', ?, 'source_inconnue', ?)",
                 (f"Subvention à la Boulangerie — Mme PIEGE_DEPORT, {NE}",
                  json.dumps({"conflit_interet": "Élue de Testonville ne participe pas"})))

    # Un acte publié, lui : la décision reste ; le particulier y est désigné
    # par son rôle (règle du 27/10/2026), sa naissance et son domicile masqués.
    acte = conn.execute(
        "INSERT INTO events (type, date, title, content, source) VALUES ('deliberation',"
        " '2026-02-01', ?, 'Le conseil décide.', 'interieur')",
        (f"Aide TEMOIN_ACTE à M. Jean PIEGENOMME, {NE}, demeurant 5 rue PIEGE_TITRE",)).lastrowid
    # Une note de travail sur cet acte, sans rien y corriger.
    conn.execute("INSERT INTO annotations (object_type, object_id, review_status, note)"
                 " VALUES ('deliberation', ?, 'retenu', 'voir M. PIEGE_NOTE, 5 rue des Lilas')",
                 (acte,))

    # Le registre des installations classées nomme et situe un particulier.
    conn.execute(
        "INSERT INTO icpe_installations (code_aiot, raison_sociale, insee, commune, adresse,"
        " regime, lat, lng) VALUES ('PIEGE_AIOT', 'PIEGE_ICPE Gaston (élevage)', '99001',"
        " 'Testonville', '5 chemin PIEGE_FERME', 'Enregistrement', 44.1, 3.9)")

    # Des champs courts, écrits par un acheteur, lus dans un PV ou saisis.
    conn.execute(
        "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id, titulaire_nom,"
        " objet, montant, date_notif, lieu_exec, source, confidence) VALUES ('219900010',"
        " 'Testonville', ?, 'Boulangerie d''épreuve', ?, 9000, '2026-03-01', ?, 'DECP',"
        " 'verified')",
        (boulangerie, "TEMOIN_MARCHE — mur de M. Quilimet, demeurant 5 rue PIEGE_MARCHE",
         "chez M. Quilimet, domicilié 5 rue PIEGE_LIEU"))
    conn.execute(
        "INSERT INTO approbations_projets (date, objet, montant_ht, citation, source,"
        " confidence) VALUES ('2026-02-01', 'TEMOIN_APPROBATION', 1000, ?, 'CM', 'verified')",
        ("participation de M. Quilimet, demeurant 5 rue PIEGE_CITATION",))
    conn.execute(
        "INSERT INTO budget_vote (year, agregat, value, note, source) VALUES (2026,"
        " 'fonctionnement', 1000, 'vu avec M. Quilimet, demeurant 5 rue PIEGE_BUDGET', 'CM')")
    conn.execute(
        "UPDATE relations SET metadata = ? WHERE relation_type = 'maire'",
        (json.dumps({"precision": "élue domiciliée 5 rue PIEGE_LIEN", "birth_year": 1961,
                     "note": "PIEGE_META"}),))

    # Deux flux jumeaux vers une entreprise individuelle publiée (par un marché) :
    # l'un neutre, qui porte le marqueur, l'autre à la description « un
    # particulier ». C'est le MASQUÉ qui reste ; le neutre, avec son marqueur, ne
    # sort jamais (décision de Julien du 24/11/2026, `dedupliquer_flux`).
    # Chaque ordre d'entrée (neutre d'abord, masqué d'abord), car l'ordre de la
    # requête ne départage pas les jumeaux.
    for nom, descriptions in (
            ("TEMOIN_EI_JUMELLE_A", ("Aide PIEGE_JUMEAU_NEUTRE_A", "Aide à un particulier")),
            ("TEMOIN_EI_JUMELLE_B", ("Aide à un particulier", "Aide PIEGE_JUMEAU_NEUTRE_B"))):
        ei = conn.execute(
            "INSERT INTO entities (type, name, commune, perimetre, confidence)"
            " VALUES ('business', ?, 'Testonville', 'C1', 'verified')", (nom,)).lastrowid
        conn.execute("INSERT INTO businesses (entity_id, legal_form_code, status)"
                     " VALUES (?, '1000', 'A')", (ei,))
        conn.execute(
            "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
            " titulaire_nom, objet, montant, date_notif, source, confidence) VALUES"
            " ('219900010', 'Testonville', ?, ?, 'TEMOIN_MARCHE_EI', 700, '2026-03-02',"
            " 'DECP', 'verified')", (ei, nom))
        for description in descriptions:
            conn.execute(
                "INSERT INTO financial_flows (from_id, to_id, type, year, amount, description,"
                " source, confidence) VALUES (?, ?, 'subvention', 2025, 321, ?, 'ofgl',"
                " 'verified')", (mairie, ei, description))

    # Quatre données de personnes que la fiche publiée (liste fermée de champs)
    # ne reprend pas aujourd'hui : chacune est posée sur un objet PUBLIÉ (l'élue,
    # la boulangerie, une association du périmètre), sinon le piège ne prouverait rien.
    asso = conn.execute(
        "INSERT INTO entities (type, name, commune, perimetre, confidence)"
        " VALUES ('association', 'Association TEMOIN_ASSO', 'Testonville', 'C1', 'verified')"
    ).lastrowid
    conn.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'subventionné', 'verified')", (asso, mairie))
    # 1. adresse et mois de naissance d'une personne publiée
    conn.execute("UPDATE entities SET address = '5 rue PIEGE_ADRESSE' WHERE id = ?", (elue,))
    conn.execute("INSERT OR REPLACE INTO persons (entity_id, firstname, lastname, birth_month)"
                 " VALUES (?, 'Élue', 'ÉPREUVE', 7)", (elue,))
    # 2. note d'association au format de collectors/rna.py
    conn.execute(
        "INSERT INTO entity_notes (entity_id, note, source, confidence) VALUES (?, ?, 'rna',"
        " 'probable')",
        (asso, json.dumps({"responsable": "PIEGE_RESPONSABLE",
                           "contacts": ["PIEGE_TEL_RNA", "PIEGE_MEL_RNA@exemple.fr"],
                           "url": "https://exemple.fr/PIEGE_RNA"}, ensure_ascii=False)))
    # 3. note d'une décision sur une relation dont un bout est une personne
    rel = un("SELECT id FROM relations WHERE relation_type = 'maire'")
    conn.execute("INSERT INTO annotations (object_type, object_id, review_status, note)"
                 " VALUES ('relation', ?, 'retenu', 'PIEGE_NOTE_RELATION')", (rel,))
    # 4. contacts et sites, sur une personne et sur une structure publiées
    for eid, tag in ((elue, "PERS"), (boulangerie, "STRUCT")):
        for type_, valeur in (("phone", f"PIEGE_TEL_{tag}"), ("email", f"PIEGE_MEL_{tag}@exemple.fr"),
                              ("website", f"https://exemple.fr/PIEGE_CONTACT_{tag}")):
            conn.execute("INSERT INTO contacts (entity_id, type, value) VALUES (?, ?, ?)",
                         (eid, type_, valeur))
        conn.execute("INSERT INTO entity_websites (entity_id, url, status) VALUES (?, ?,"
                     " 'validated')", (eid, f"https://exemple.fr/PIEGE_SITE_{tag}"))
    _pieger_les_ei(conn, asso)
    conn.commit()
    conn.close()


# Montants des flux semés par `_pieger_les_ei` : ceux qui doivent rester comptés
# (déliés, sans nom d'EI), et ceux qui doivent disparaître.
MONTANTS_EI_GARDES = {4321: "subvention du syndicat à une EI C2",
                      4322: "subvention du syndicat à une EI rejetée à l'atelier",
                      777: "loyer d'une EI sans fait au syndicat",
                      778: "loyer d'une EI sans forme juridique au syndicat",
                      4324: "subvention rectifiée, note « un particulier » (P1)"}
MONTANTS_EI_ECARTES = {13579: "subvention d'une association à une EI sans fait",
                       13578: "subvention d'une association à une EI sans forme juridique"}


def _pieger_les_ei(conn, asso) -> None:
    """Les entreprises individuelles (brief `ei-flux-marches`, 24/11/2026).

    Une partie publique (syndicat, forme 7355) et, autour d'elle : une EI
    retenue par un marché, une par une subvention, une sans forme juridique
    retenue par un marché (TÉMOINS, publiées) ; une EI sans fait, une EI C2,
    une EI rejetée à l'atelier, une EI sans forme juridique et sans fait
    (PIÈGES : leur nom ne sort nulle part, ni fiche, ni flux, ni marché).
    Leurs flux d'argent public restent comptés, déliés ; ceux qu'une association
    leur verse sont écartés."""
    def entite(type_, nom, perimetre="C1"):
        return conn.execute(
            "INSERT INTO entities (type, name, commune, perimetre, confidence)"
            " VALUES (?, ?, 'Testonville', ?, 'verified')", (type_, nom, perimetre)).lastrowid

    def ei(nom, perimetre="C1", forme="1000", siren=None):
        eid = entite("business", nom, perimetre)
        conn.execute("INSERT INTO businesses (entity_id, siren, legal_form_code, status)"
                     " VALUES (?, ?, ?, 'A')", (eid, siren, forme))
        return eid

    def sans_forme(nom):
        # Forme juridique inconnue ; son exploitant porte le même nom.
        eid = ei(nom, forme=None)
        personne = entite("person", nom)
        conn.execute("INSERT INTO relations (from_id, to_id, relation_type, source, confidence)"
                     " VALUES (?, ?, 'dirigeant', 'sirene', 'verified')", (personne, eid))
        return eid

    def flux(de, vers, type_, montant, description="Versement"):
        conn.execute("INSERT INTO financial_flows (from_id, to_id, type, year, amount,"
                     " description, source, confidence) VALUES (?, ?, ?, 2025, ?, ?, 'ofgl',"
                     " 'verified')", (de, vers, type_, montant, description))

    def marche(titulaire, nom, objet, siren=None):
        conn.execute(
            "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
            " titulaire_siren, titulaire_nom, objet, montant, date_notif, source, confidence)"
            " VALUES ('219900010', 'Testonville', ?, ?, ?, ?, 2500, '2026-03-03', 'DECP',"
            " 'verified')", (titulaire, siren, nom, objet))

    syndicat = entite("service", "Syndicat TEMOIN_SYNDICAT")
    conn.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '7355')",
                 (syndicat,))

    marche(ei("TEMOIN_EI_MARCHE"), "TEMOIN_EI_MARCHE", "TEMOIN_MARCHE_EI_RETENUE")
    flux(syndicat, ei("TEMOIN_EI_SUBVENTION"), "subvention", 2468, "Subvention TEMOIN_FLUX_EI")
    retenue_sans_forme = sans_forme("Lucien TEMOIN_EI_SANS_FORME")
    marche(retenue_sans_forme, "Lucien TEMOIN_EI_SANS_FORME", "TEMOIN_MARCHE_EI_SANS_FORME")

    sans_fait = ei("PIEGE_EI_SANS_FAIT", siren="999000777")
    flux(sans_fait, syndicat, "bail", 777, "Loyer")
    flux(asso, sans_fait, "subvention", 13579)
    # Un marché DECP que rien ne rattache à sa fiche, sauf le SIREN.
    marche(None, "PIEGE_EI_SANS_FAIT (DECP)", "TEMOIN_MARCHE_EI_SIREN", siren="999000777")

    voisine = ei("PIEGE_EI_C2", perimetre="C2")
    flux(syndicat, voisine, "subvention", 4321)
    marche(voisine, "PIEGE_EI_C2", "TEMOIN_MARCHE_EI_C2")

    rejetee = ei("PIEGE_EI_REJETEE")
    conn.execute("INSERT INTO annotations (object_type, object_id, review_status)"
                 " VALUES ('entity', ?, 'ecarte')", (rejetee,))
    flux(syndicat, rejetee, "subvention", 4322)
    marche(rejetee, "PIEGE_EI_REJETEE", "TEMOIN_MARCHE_EI_REJETEE")

    sans_forme_sans_fait = sans_forme("Gaston PIEGE_EI_SANS_FORME")
    flux(sans_forme_sans_fait, syndicat, "bail", 778, "Loyer")
    flux(asso, sans_forme_sans_fait, "subvention", 13578)

    # Audit du filtre (24/11/2026), P1 : le seul flux d'une EI, rectifié à
    # l'atelier avec une note « versée à un particulier ». La note sort et délie
    # le flux : il ne retient pas l'EI (sinon fiche sans fait, snapshot refusé).
    note = ei("PIEGE_EI_NOTE")
    flux(syndicat, note, "subvention", 4323)
    flux_note = conn.execute("SELECT id FROM financial_flows WHERE amount = 4323").fetchone()[0]
    conn.execute("INSERT INTO annotations (object_type, object_id, review_status, note,"
                 " corrections) VALUES ('flow', ?, 'retenu', 'versée à un particulier', ?)",
                 (flux_note, json.dumps({"amount": 4324})))

    # P4 : une EI typée `service` (forme 1000 sur sa ligne `businesses`), sans
    # fait, avec une position dans la commune : ni fiche, ni point sur la carte.
    # Sa voisine retenue par un marché est publiée, position masquée.
    for nom, retenue in (("Cabinet PIEGE_EI_SERVICE", False),
                         ("Cabinet TEMOIN_EI_SERVICE", True)):
        eid = entite("service", nom)
        conn.execute("UPDATE entities SET lat = 44.05, lng = 3.85 WHERE id = ?", (eid,))
        conn.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '1000')",
                     (eid,))
        if retenue:
            marche(eid, nom, "TEMOIN_MARCHE_EI_SERVICE")

    # Avis vigie-securite du 25/11/2026 : un marché lu dans un PV, sans
    # identifiant ni SIREN, au nom d'une EI écartée (point 2) ; une EI C2 nommée
    # dans l'objet de son marché et dans la description d'un autre flux (point 3).
    marche(None, "Gaston PIEGE_EI_SANS_FORME", "TEMOIN_MARCHE_EI_NOM_SEUL")
    objet = ei("Jeanne PIEGE_EI_OBJET", perimetre="C2")
    marche(objet, "Jeanne PIEGE_EI_OBJET", "TEMOIN_MARCHE_EI_OBJET confié à Jeanne PIEGE_EI_OBJET")
    flux(syndicat, asso, "subvention", 2469, "Aide TEMOIN_FLUX_OBJET pour jeanne piege_ei_objet")
    # Avis vigie-dpo du 25/11/2026 : l'avis de marché (acte `marché_public`, son
    # titre est l'objet) nomme la même EI C2 ; il sort dans events.json,
    # actualite.json et recherche_index.json.
    conn.execute("INSERT INTO events (type, date, title, source) VALUES ('marché_public',"
                 " '2026-03-05', 'Avis TEMOIN_AVIS_EI attribué à Jeanne PIEGE_EI_OBJET', 'BOAMP')")
    # Avis vigie-securite sur 2825b8a : l'avis d'un marché dont le titulaire est
    # effacé ne garde pas un mot SEUL de son nom (« lot PIEGE_MOT_SEUL ») ; le
    # marché est relié à l'acte (`marches_publics.event_id`).
    seul = ei("Jean PIEGE_MOT_SEUL", perimetre="C2")
    avis = conn.execute(
        "INSERT INTO events (type, date, title, source) VALUES ('marché_public', '2026-03-06',"
        " 'TEMOIN_AVIS_MOT Maconnerie lot PIEGE_MOT_SEUL', 'BOAMP')").lastrowid
    conn.execute(
        "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id, titulaire_nom,"
        " objet, montant, date_notif, source, confidence, event_id) VALUES ('219900010',"
        " 'Testonville', ?, 'Jean PIEGE_MOT_SEUL', 'TEMOIN_MARCHE_MOT Maconnerie lot"
        " PIEGE_MOT_SEUL', 2700, '2026-03-06', 'DECP', 'verified', ?)", (seul, avis))

    # Décisions de Julien du 25/11/2026. (1) Une EI C2 que la COMMUNE désigne
    # (marché dont elle est l'acheteur, versement) garde son nom sur ce marché et
    # ce flux, sans fiche ; sur le marché d'un autre acheteur, il tombe (essai
    # `test_une_ei_c2_designee_par_la_commune_garde_son_nom`).
    # (2) Une fiche C3 sans forme juridique est une partie publique : l'EI qu'elle
    # paie est retenue.
    from collectors.nom_normalise import normaliser
    commune = conn.execute(
        "INSERT INTO entities (type, name, name_norm, commune, perimetre, confidence)"
        " VALUES ('service', 'Commune de Testonville', ?, 'Testonville', 'C1', 'verified')",
        (normaliser("Commune de Testonville"),)).lastrowid
    designee = ei("Lucie TEMOIN_EI_C2_COMMUNE", perimetre="C2", siren="999000888")
    conn.execute(
        "INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom, titulaire_id,"
        " titulaire_nom, objet, montant, date_notif, source, confidence) VALUES (?, '219900017',"
        " 'Commune de Testonville', ?, 'Lucie TEMOIN_EI_C2_COMMUNE', 'TEMOIN_MARCHE_C2_COMMUNE',"
        " 3100, '2026-03-04', 'DECP', 'verified')", (commune, designee))
    flux(commune, designee, "subvention", 3101, "Subvention communale")
    # Avis vigie-securite A1, A2 : ailleurs, son nom tombe (marché du syndicat
    # rattaché par le seul SIREN, description d'un autre flux).
    marche(None, "TEMOIN_EI_C2_COMMUNE Lucie", "TEMOIN_MARCHE_C2_SYNDICAT", siren="999000888")
    flux(syndicat, asso, "subvention", 3103, "Aide pour Lucie TEMOIN_EI_C2_COMMUNE")
    departement = entite("service", "Conseil départemental TEMOIN_C3", perimetre="C3")
    flux(departement, ei("TEMOIN_EI_PAYEE_PAR_C3"), "subvention", 3102, "Aide départementale")
    # Décision de Julien (tour 4) : une ASSOCIATION classée C3 n'est pas une partie
    # publique. Son versement à une EI ne sort pas et ne la retient pas.
    federation = entite("association", "Fédération départementale TEMOIN_FED", perimetre="C3")
    flux(federation, ei("PIEGE_EI_PAYEE_PAR_FED"), "subvention", 3104, "Aide fédérale")

    # Avis vigie-dpo (tour 5) : l'élu « Marc TEMOIN_ELU_EI », publié par son
    # mandat, exploite l'EI du même nom, rejetée à l'atelier. Son nom reste sur
    # sa fiche, mais il n'épargne pas son EI : marché au seul nom et description
    # qui le citent sont masqués.
    elu = entite("person", "Marc TEMOIN_ELU_EI")
    conn.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?, ?, 'élu_cm', 'verified')", (elu, commune))
    ei_elu = ei("MARC TEMOIN_ELU_EI")
    conn.execute("INSERT INTO relations (from_id, to_id, relation_type, source, confidence)"
                 " VALUES (?, ?, 'dirigeant', 'sirene', 'verified')", (elu, ei_elu))
    conn.execute("INSERT INTO annotations (object_type, object_id, review_status)"
                 " VALUES ('entity', ?, 'ecarte')", (ei_elu,))
    marche(None, "Marc TEMOIN_ELU_EI", "TEMOIN_MARCHE_ELU")
    flux(syndicat, asso, "subvention", 3106, "Aide TEMOIN_FLUX_ELU via Marc TEMOIN_ELU_EI")

    # Tour 7 : un doublon `business` sans forme, classé C2, sans lien, homonyme
    # d'une EI sans fiche, reçoit un flux. Le flux sort délié, SANS ce nom.
    ei("Jeanne PIEGE_DOUBLON")
    doublon = entite("business", "JEANNE PIEGE_DOUBLON", perimetre="C2")
    flux(syndicat, doublon, "subvention", 3107, "Subvention")

    # Étape É0 de l'audit du 25/11/2026. Le verdict d'atelier (et sa confiance)
    # passe AVANT le filtre de confiance des marchés, dans la sortie comme dans
    # la retenue d'une EI.
    # H1 : un marché `probable` validé à l'atelier (`retenu`, `verified`) sort, et
    # retient l'EI dont il est le seul fait.
    def marche_revu(titulaire, nom, objet, confiance, verdict, confiance_revue):
        mid = conn.execute(
            "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
            " titulaire_nom, objet, montant, date_notif, source, confidence) VALUES"
            " ('219900010', 'Testonville', ?, ?, ?, 2600, '2026-03-07', 'DECP', ?)",
            (titulaire, nom, objet, confiance)).lastrowid
        conn.execute("INSERT INTO annotations (object_type, object_id, review_status,"
                     " confidence) VALUES ('marche', ?, ?, ?)",
                     (mid, verdict, confiance_revue))

    marche_revu(ei("TEMOIN_EI_MARCHE_VALIDE"), "TEMOIN_EI_MARCHE_VALIDE",
                "TEMOIN_MARCHE_VALIDE", "probable", "retenu", "verified")
    # H2 : un marché `verified` rétrogradé à `probable` à l'atelier ne sort pas
    # (ni l'EI qu'il était seul à retenir), et l'instance n'est pas refusée.
    marche_revu(ei("PIEGE_EI_MARCHE_RETROGRADE"), "PIEGE_EI_MARCHE_RETROGRADE",
                "PIEGE_MARCHE_RETROGRADE", "verified", "a_revoir", "probable")
    # Suite d'É0 (écart 1 de #323) : l'acte relié à un marché (avis BOAMP/DECP,
    # `marches_publics.event_id`) sort exactement quand son marché sort, après
    # le verdict de l'atelier. Écarté, rétrogradé, confiance NULL : l'avis ne
    # sort pas ; `probable` validé : il sort (témoin).
    def avis_revu(titre, confiance, verdict=None, confiance_revue=None,
                  type_="marché_public", source="BOAMP", titulaire=None):
        acte = conn.execute("INSERT INTO events (type, date, title, content, source) VALUES"
                            " (?, '2026-03-08', ?, 'Le conseil attribue.', ?)",
                            (type_, titre, source)).lastrowid
        mid = conn.execute(
            "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
            " objet, montant, date_notif, source, confidence, event_id) VALUES"
            " ('219900010', 'Testonville', ?, ?, 2800, '2026-03-08', 'BOAMP', ?, ?)",
            (titulaire, f"Marché de {titre}", confiance, acte)).lastrowid
        if verdict:
            conn.execute("INSERT INTO annotations (object_type, object_id, review_status,"
                         " confidence) VALUES ('marche', ?, ?, ?)", (mid, verdict, confiance_revue))
        return acte

    avis_revu("Avis PIEGE_AVIS_ECARTE", "verified", "ecarte")
    avis_revu("Avis PIEGE_AVIS_RETROGRADE", "verified", "a_revoir", "probable")
    avis_revu("Avis PIEGE_AVIS_CONFIANCE_NULLE", None)
    avis_revu("Avis TEMOIN_AVIS_VALIDE", "probable", "retenu", "verified")
    # Avis vigie-securite : un acte relié à DEUX marchés, l'un publié, l'autre
    # écarté, ne sort pas (« tous ses marchés », pas « au moins un »).
    deux = avis_revu("Avis PIEGE_AVIS_DEUX_MARCHES", "verified", "ecarte")
    conn.execute(
        "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, objet, montant,"
        " date_notif, source, confidence, event_id) VALUES ('219900010', 'Testonville',"
        " 'TEMOIN_MARCHE_SECOND_LOT', 2900, '2026-03-08', 'BOAMP', 'verified', ?)", (deux,))
    # Un avis (`marché_public`) ne retient pas une EI : seul un acte de la
    # collectivité le fait (`TYPES_ACTES_DE_LA_COLLECTIVITE`). Une délibération,
    # elle, peut porter un marché (`collectors/marches_extraits.py`). Son
    # marché écarté, elle ne sort pas, et l'EI qu'elle était seule à retenir
    # perd sa fiche, sans faire refuser l'instance.
    ei_acte = ei("PIEGE_EI_ACTE_MARCHE_ECARTE")
    deliberation = avis_revu("Attribution du marché de voirie à PIEGE_EI_ACTE_MARCHE_ECARTE",
                             "verified", "ecarte", type_="deliberation", source="interieur",
                             titulaire=ei_acte)
    conn.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?, ?,"
                 " 'prestataire')", (deliberation, ei_acte))
    # H3 : une subvention seulement DEMANDÉE, ou ANNULÉE, n'est pas un versement
    # (`SECURITY.md` : « qu'une personne publique lui verse ») : elle ne fait pas
    # nommer l'EI. L'état vient de la saisie (`statut`) ou du type
    # (`etat_du_flux`), comme pour la sortie.
    for nom, type_, statut, montant in (
            ("PIEGE_EI_DEMANDE", "subvention", "demande", 3108),
            ("PIEGE_EI_ANNULE", "subvention", "annule", 3109),
            ("PIEGE_EI_DEMANDE_TYPE", "subvention_demandee", None, 3110)):
        conn.execute("INSERT INTO financial_flows (from_id, to_id, type, year, amount,"
                     " description, source, confidence, statut) VALUES (?, ?, ?, 2025, ?,"
                     " 'Subvention', 'CR CM 2025', 'verified', COALESCE(?, 'realise'))",
                     (commune, ei(nom), type_, montant, statut))
    # Avis vigie-securite sur dbe2db4 (point 2) : deux flux jumeaux vers une EI,
    # l'un voté, l'autre seulement demandé ; la sortie ne les départage pas par
    # l'état. Aucun ne retient l'EI, et l'instance n'est pas refusée.
    for nom, statuts in (("PIEGE_EI_JUMEAU_DEMANDE_A", ("realise", "demande")),
                         ("PIEGE_EI_JUMEAU_DEMANDE_B", ("demande", "realise"))):
        jumelle = ei(nom)
        for statut in statuts:
            conn.execute("INSERT INTO financial_flows (from_id, to_id, type, year, amount,"
                         " description, source, confidence, statut) VALUES (?, ?,"
                         " 'subvention', 2025, 3111, 'Subvention', 'CR CM 2025', 'verified',"
                         " ?)", (commune, jumelle, statut))
    # Relecture de #323 : le jumeau DEMANDÉ est `probable`, il n'atteint pas la
    # déduplication (`etape_flux`) ; le versement voté retient l'EI, qui sort.
    jumelle = ei("TEMOIN_EI_JUMEAU_PROBABLE")
    for statut, confiance in (("realise", "verified"), ("demande", "probable")):
        conn.execute("INSERT INTO financial_flows (from_id, to_id, type, year, amount,"
                     " description, source, confidence, statut) VALUES (?, ?,"
                     " 'subvention', 2025, 3112, 'Subvention', 'CR CM 2025', ?, ?)",
                     (commune, jumelle, confiance, statut))


@pytest.fixture(scope="module")
def piege(tmp_path_factory):
    travail = tmp_path_factory.mktemp("pieges")
    out = construire(ROOT, travail, avant_amorcage=_pieger_les_textes,
                     apres_amorcage=_pieger_la_base)
    return out, travail / "moteur" / "db" / "ci.regles.json"


def _textes(out: Path) -> dict[str, str]:
    return {str(f.relative_to(out)): f.read_text(encoding="utf-8", errors="replace")
            for f in sorted(out.rglob("*")) if f.is_file()}


def test_le_snapshot_piege_nest_pas_vide(piege):
    """Sans témoins, « aucun piège dehors » se prouverait en ne publiant rien."""
    textes = _textes(piege[0])
    for temoin, fichier in (("TEMOIN_ACTE", "events.json"), ("TEMOIN_MARCHE", "marches.json"),
                            ("TEMOIN_FLUX", "flows.json"),
                            ("TEMOIN_APPROBATION", "approbations.json")):
        assert temoin in textes[fichier], f"{temoin} absent de {fichier}"
    assert "Aide TEMOIN_ACTE à un particulier" in textes["events.json"]
    assert not [nom for nom, texte in textes.items() if "PIEGENOMME" in texte]
    # Les objets qui portent les pièges de personnes sont bien publiés : sans
    # cela, un piège rattaché à rien de publié ne prouverait rien.
    for nom in ("Association TEMOIN_ASSO", "Élue de Testonville", "Boulangerie d'épreuve",
                "TEMOIN_EI_MARCHE", "TEMOIN_EI_SUBVENTION", "Lucien TEMOIN_EI_SANS_FORME",
                "Cabinet TEMOIN_EI_SERVICE", "TEMOIN_EI_PAYEE_PAR_C3"):
        assert nom in textes["entities.json"], f"{nom} absent de entities.json"
    for objet in ("TEMOIN_MARCHE_EI_NOM_SEUL", "TEMOIN_MARCHE_EI_OBJET",
                  "TEMOIN_MARCHE_EI_RETENUE", "TEMOIN_MARCHE_EI_SANS_FORME",
                  "TEMOIN_MARCHE_EI_SIREN", "TEMOIN_MARCHE_EI_C2", "TEMOIN_MARCHE_EI_REJETEE"):
        assert objet in textes["marches.json"], f"{objet} absent de marches.json"
    assert "TEMOIN_FLUX_EI" in textes["flows.json"] and "TEMOIN_FLUX_OBJET" in textes["flows.json"]
    assert "Avis TEMOIN_AVIS_EI attribué à [entreprise individuelle]" in textes["events.json"]
    assert "TEMOIN_AVIS_MOT Maconnerie lot [nom retiré]" in textes["events.json"]
    assert "TEMOIN_MARCHE_MOT Maconnerie lot [nom retiré]" in textes["marches.json"]
    # Un marché dont le titulaire est masqué reste ATTRIBUÉ pour le site.
    # Q2 : l'EI C2 que la commune désigne garde son nom, sans fiche ni lien.
    assert "Lucie TEMOIN_EI_C2_COMMUNE" not in textes["entities.json"]
    m = next(m for m in json.loads(textes["marches.json"])["marches"]
             if m["objet"] == "TEMOIN_MARCHE_C2_COMMUNE")
    assert m["titulaire_nom"] == "Lucie TEMOIN_EI_C2_COMMUNE" and m["titulaire_id"] is None
    fl = next(f for f in json.loads(textes["flows.json"])["flows"] if f["amount"] == 3101)
    assert fl["to_name"] == "Lucie TEMOIN_EI_C2_COMMUNE" and fl["to_id"] is None
    # L'élu exploitant : sa fiche sort, son nom ne protège pas son EI écartée.
    assert "Marc TEMOIN_ELU_EI" in textes["entities.json"]
    m = next(m for m in json.loads(textes["marches.json"])["marches"]
             if m["objet"] == "TEMOIN_MARCHE_ELU")
    assert m["titulaire_nom"] is None and m["titulaire_masque"]
    fl = next(f for f in json.loads(textes["flows.json"])["flows"] if f["amount"] == 3107)
    assert fl["to_id"] is None and fl["to_name"] is None
    fl = next(f for f in json.loads(textes["flows.json"])["flows"] if f["amount"] == 3106)
    assert fl["description"] == "Aide TEMOIN_FLUX_ELU via [entreprise individuelle]"
    m = next(m for m in json.loads(textes["marches.json"])["marches"]
             if m["objet"] == "TEMOIN_MARCHE_C2_SYNDICAT")
    assert m["titulaire_nom"] is None and m["titulaire_masque"]
    fl = next(f for f in json.loads(textes["flows.json"])["flows"] if f["amount"] == 3103)
    assert fl["description"] == "Aide pour [entreprise individuelle]"
    masques = [m for m in json.loads(textes["marches.json"])["marches"]
               if m.get("titulaire_masque")]
    assert len(masques) >= 5 and all(m["titulaire_nom"] is None and m["montant"]
                                     for m in masques)
    assert "Élue de Testonville" in textes["relations.json"]


def test_ni_mois_de_naissance_ni_adresse_dune_personne(piege):
    """`birth_month` est un entier (7) et `address` est semée : aucune sortie ne
    porte ces deux clés. L'adresse (PIEGE_ADRESSE) est aussi couverte par
    `test_aucun_piege_ne_sort` ; le mois ne l'est que par ici."""
    for nom, texte in _textes(piege[0]).items():
        assert "birth_month" not in texte, f"birth_month dans {nom}"
        assert not re.search(r'"(address|adresse)"\s*:\s*"[^"]*PIEGE', texte), nom


def test_aucun_piege_ne_sort(piege):
    fuites = sorted(nom for nom, texte in _textes(piege[0]).items() if "PIEGE_" in texte)
    assert fuites == [], f"pièges publiés dans : {fuites}"


def test_ni_naissance_ni_piste_ni_note_de_travail(piege):
    out = piege[0]
    # Les empreintes de `manifeste.json` (SHA-256) contiennent « 1961 » une fois
    # sur vingt environ : on les retire avant de chercher l'année.
    tout = re.sub(r"[0-9a-f]{32,}", "", "\n".join(_textes(out).values()))
    assert "1961" not in tout
    assert json.loads((out / "conflits.json").read_text(encoding="utf-8"))["cas"] == []
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["dossiers"]["donnee_personnelle"] == ["eau"]
    assert len(stats["conseils_en_clair"]["donnee_personnelle"]) == 1


def _controler(dossier: Path, regles: Path, audits: Path | None = None) -> dict:
    # Le relevé des EI (`audits/controle_ei.json`) est écrit sous la racine du
    # moteur COPIÉ par `construire` : `<travail>/moteur/audits`.
    audits = audits or regles.parent.parent / "audits"
    r = subprocess.run([sys.executable, "scripts/verify_snapshot.py", str(dossier), "--json"],
                       cwd=ROOT, env={**os.environ, "VIGIE_RULES": str(regles),
                                      "VIGIE_AUDITS": str(audits)},
                       capture_output=True, text=True)
    return json.loads(r.stdout)


def _json(out: Path, nom: str, cle: str) -> list:
    return json.loads((out / nom).read_text(encoding="utf-8"))[cle]


def test_l_argent_des_ei_ecartees_reste_compte_sans_nom(piege):
    """Les flux entre la partie publique et une EI sans fiche sortent déliés :
    montant gardé, ni nom, ni identifiant, ni description côté EI. Ceux qu'une
    association verse à une EI écartée ne sortent pas."""
    flux = _json(piege[0], "flows.json", "flows")
    par_montant = {f["amount"]: f for f in flux}
    for montant, quoi in MONTANTS_EI_GARDES.items():
        assert montant in par_montant, f"{quoi} : absent de flows.json"
        f = par_montant[montant]
        cote = "to" if f["from_name"] == "Syndicat TEMOIN_SYNDICAT" else "from"
        assert f[f"{cote}_id"] is None and f[f"{cote}_name"] is None, quoi
        assert f["description"] is None, quoi
    for montant, quoi in MONTANTS_EI_ECARTES.items():
        assert montant not in par_montant, f"{quoi} : publiée"
    assert 3104 not in par_montant, "versement d'une association C3 à une EI : publié"


def test_le_verdict_d_atelier_passe_avant_la_confiance_des_marches(piege):
    """É0, H1 et H2 : un marché `probable` validé à l'atelier sort, avec la
    confiance de l'atelier, et retient son EI ; un marché `verified` rétrogradé
    ne sort pas, ni l'EI qu'il retenait (`test_aucun_piege_ne_sort`), sans faire
    refuser l'instance (`test_le_controleur_passe_sur_le_snapshot_filtre`)."""
    out = piege[0]
    m = next((m for m in _json(out, "marches.json", "marches")
              if m["objet"] == "TEMOIN_MARCHE_VALIDE"), None)
    assert m is not None, "marché validé à l'atelier absent de marches.json"
    assert m["confidence"] == "verified"
    assert "TEMOIN_EI_MARCHE_VALIDE" in (out / "entities.json").read_text(encoding="utf-8")
    assert not [m for m in _json(out, "marches.json", "marches")
                if m.get("confidence") not in ("verified", "confirmed")]


def test_l_avis_d_un_marche_sort_avec_son_marche(piege):
    """Suite d'É0 : l'avis d'un marché `probable` validé à l'atelier sort avec
    son marché ; les avis écarté, rétrogradé, à confiance NULL ne sortent pas
    (`test_aucun_piege_ne_sort`). Sans ce témoin, l'essai passerait à vide."""
    out = piege[0]
    titres = [e["title"] for e in _json(out, "events.json", "events")]
    assert "Avis TEMOIN_AVIS_VALIDE" in titres, "avis du marché validé absent de events.json"
    objets = {m["objet"] for m in _json(out, "marches.json", "marches")}
    assert "Marché de Avis TEMOIN_AVIS_VALIDE" in objets
    # Le lot publié de l'avis à deux marchés sort ; son avis, non.
    assert "TEMOIN_MARCHE_SECOND_LOT" in objets


def test_une_subvention_demandee_ou_annulee_ne_nomme_pas_une_ei(piege):
    """É0, H3 : l'EI dont le seul fait est une subvention demandée ou annulée
    n'a pas de fiche ; le flux, s'il sort, sort délié côté EI."""
    out = piege[0]
    entites = (out / "entities.json").read_text(encoding="utf-8")
    for nom in ("PIEGE_EI_DEMANDE", "PIEGE_EI_ANNULE", "PIEGE_EI_DEMANDE_TYPE",
                "PIEGE_EI_JUMEAU_DEMANDE_A", "PIEGE_EI_JUMEAU_DEMANDE_B"):
        assert nom not in entites, f"{nom} publiée"
    assert "TEMOIN_EI_JUMEAU_PROBABLE" in entites, "jumeau probable demandé : fiche perdue"
    flux = [f for f in _json(out, "flows.json", "flows") if f["amount"] in (3108, 3109, 3110, 3111)]
    # Présents, sinon la boucle passerait à vide (relecture de #323). 3111 : un
    # seul des deux jumeaux de chaque EI sort (`dedupliquer_flux`).
    assert {f["amount"] for f in flux} == {3108, 3109, 3110, 3111}
    for f in flux:
        assert f["to_id"] is None and f["to_name"] is None, f


def test_une_ei_sans_forme_juridique_est_une_ei(piege):
    """Retenue par un marché : publiée comme EI (nature, domicile masqué). Sans
    fait : aucune fiche (le piège `Gaston PIEGE_EI_SANS_FORME` le vérifie)."""
    fiche = next(e for e in _json(piege[0], "entities.json", "entities")
                 if e["name"] == "Lucien TEMOIN_EI_SANS_FORME")
    assert fiche["nature"] == "individuelle"
    assert fiche["lat"] is None and fiche["lng"] is None


def test_une_ei_typee_service_est_une_ei(piege):
    """Audit du 24/11/2026, P4 : retenue, elle sort `individuelle`, sans position ;
    sans fait, elle ne sort pas (piège `Cabinet PIEGE_EI_SERVICE`)."""
    fiche = next(e for e in _json(piege[0], "entities.json", "entities")
                 if e["name"] == "Cabinet TEMOIN_EI_SERVICE")
    assert fiche["nature"] == "individuelle"
    assert fiche["lat"] is None and fiche["lng"] is None and not fiche["has_public_location"]


def test_le_releve_des_ei_ne_porte_aucun_nom(piege):
    texte = (piege[1].parent.parent / "audits" / "controle_ei.json").read_text(encoding="utf-8")
    assert "PIEGE" not in texte and "TEMOIN" not in texte
    (releve,) = json.loads(texte)["releves"].values()
    assert len(releve["ei_ecartees"]) >= 5


@pytest.mark.parametrize("fichier,cle,champ,valeur", [
    ("flows.json", "flows", "to_name", "PIEGE_EI_C2"),
    ("marches.json", "marches", "titulaire_nom", "pIEGE_ei_rejetee"),
    ("marches.json", "marches", "titulaire_nom", "PIEGE_EI_SANS_FAIT (DECP)"),
    ("flows.json", "flows", "from_name", "Gaston  PIEGE_EI_SANS_FORME"),
])
def test_le_controleur_refuse_le_nom_d_une_ei_ecartee(piege, tmp_path, fichier, cle, champ, valeur):
    """Le builder défait à la main : un nom d'EI non publiée remis dans un flux
    ou un marché délié. Le contrôleur le voit seul (casse et blancs ignorés).

    Le relevé est réécrit sous l'empreinte du contenu modifié, comme le ferait
    un builder fautif qui écrit lui-même ce nom ; sans cela, le contrôleur
    refuse plus tôt (« relevé absent », cf. l'essai suivant)."""
    out, regles = piege
    fuite = tmp_path / "snap"
    shutil.copytree(out, fuite)
    donnees = json.loads((fuite / fichier).read_text(encoding="utf-8"))
    cible = next(x for x in donnees[cle] if x.get(champ) is None
                 and x.get(champ.replace("_name", "_id").replace("_nom", "_id")) is None)
    cible[champ] = valeur
    (fuite / fichier).write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    from scripts.snapshot.fiches import empreinte_snapshot
    (releve,) = json.loads((regles.parent.parent / "audits" / "controle_ei.json")
                           .read_text(encoding="utf-8"))["releves"].values()
    audits = tmp_path / "audits"
    audits.mkdir()
    (audits / "controle_ei.json").write_text(json.dumps(
        {"releves": {empreinte_snapshot(fuite): releve}}), encoding="utf-8")
    regles_vues = {e["regle"] for e in _controler(fuite, regles, audits)["erreurs"]}
    assert "nom d'une entreprise individuelle non publiée" in regles_vues, regles_vues


def test_un_snapshot_modifie_apres_sa_construction_n_a_pas_de_releve(piege, tmp_path):
    """Le relevé est rangé sous l'empreinte du contenu : modifié après coup, le
    snapshot n'en a plus, et le contrôle le refuse en disant pourquoi."""
    out, regles = piege
    fuite = tmp_path / "snap"
    shutil.copytree(out, fuite)
    (fuite / "flows.json").write_text((fuite / "flows.json").read_text(encoding="utf-8")
                                      .replace("TEMOIN_FLUX_EI", "TEMOIN_FLUX_EI2"),
                                      encoding="utf-8")
    erreurs = _controler(fuite, regles)["erreurs"]
    regle = "relevé des entreprises individuelles absent pour ce snapshot"
    assert regle in {e["regle"] for e in erreurs}
    assert "modifié après sa construction" in json.dumps(erreurs, ensure_ascii=False)


def test_le_controleur_passe_sur_le_snapshot_filtre(piege):
    rapport = _controler(*piege)
    assert rapport["ok"], rapport["rapport"]


def test_le_controleur_refuse_ce_que_le_filtre_aurait_laisse(piege, tmp_path):
    """Le builder défait à la main : le contrôleur, qui ne partage rien avec
    lui, doit le voir seul."""
    out, regles = piege
    fuite = tmp_path / "snap"
    shutil.copytree(out, fuite)
    actes = fuite / "events.json"
    actes.write_text(actes.read_text(encoding="utf-8")
                     .replace("[date masquée]", "05/03/1961")
                     .replace("[domicile masqué]", "5 rue des Lilas"), encoding="utf-8")
    regles_vues = {e["regle"] for e in _controler(fuite, regles)["erreurs"]}
    assert {"date de naissance publiée", "domicile publié"} <= regles_vues, regles_vues
