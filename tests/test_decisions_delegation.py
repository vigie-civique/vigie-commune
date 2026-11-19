"""Décisions prises par délégation : type propre, titre seul, catégories comptées.

Avis DPO du 14/11/2026, validé par Julien le 15/11 : une décision du maire, du
président ou du bureau n'est pas une délibération ; elle sort en titre seul ;
ce qui touche un particulier ne sort qu'en nombre ; une préemption exercée sort
sans nom ni parcelle. La base n'est jamais simulée (`conftest.base`).
"""
from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from collectors.connecteurs.base import DocumentPublie
from collectors.connecteurs.dematdoc import DECISIONS_DEFAUT, DELIBERATIONS_DEFAUT
from collectors.decisions import categorie, enregistrer

ROOT = Path(__file__).resolve().parent.parent


# ── Le classement, sur des objets publiés par une intercommunalité ───────────

@pytest.mark.parametrize("objet", [
    "1.1 Marché public - ACQUISITION DE COLONNES SEMI-ENTERRÉES",
    "DEMANDE DE SUBVENTION AUPRES DU CONSEIL DEPARTEMENTAL POUR LA POLITIQUE JEUNESSE",
    "PORTANT AUTORISATION POUR LA REALISATION D'UN EMPRUNT DE 365 000 €",
    "VIREMENT DE CREDITS ENTRE CHAPITRES DE LA SECTION DE FONCTIONNEMENT",
    "Convention d'occupation du domaine privé à titre gratuit pour une barrière",
    "Avenant n°1 au lot n°12 du marché de travaux de réhabilitation",
    "Convention de partenariat avec l'association Radio Saint Ferréol",
    "Convention avec la Cité scolaire François-Jean Armorin relative à l'Espace Jeunes",
    "PORTANT AUTORISATION D'UN EMPRUNT AUPRES DE LA BANQUE POPULAIRE DE 365 000 €",
    "DEMANDE DE SUBVENTION AU TITRE DU FONDS VERT POUR UN POSTE DE CHARGÉ DE MISSION",
    "Travaux de réfection sur 500 m de voirie",
    "EMPRUNT DE 365 000 € - Annule et remplace la décision DC2026-029",
    "Marché de travaux, lot 3 : AB 12 000 €",
    # Troisième relecture du 15/11 : comptés à tort avant le lexique d'organismes.
    "Convention avec l'EPF Occitanie pour le portage foncier",
    "Adhésion au SIVOM Cévennes",
    "Contrat de prêt avec le Crédit Agricole",
    "Convention avec La Poste pour l'agence postale",
    "Marché d'assurance avec Groupama Méditerranée",
    "Mission confiée au bureau d'études SETEC",
    "Marché de fourniture d'un véhicule Renault Kangoo",
    "Travaux sur la RD 907",
    "Passage à la nomenclature M57",
    "Décision modificative n°2",
    "Convention France Services",
    "Convention avec Pôle Emploi",
    "Cotisation à l'AMF 30",
])
def test_ce_qui_se_publie(objet):
    assert categorie(objet) == "publiable"


@pytest.mark.parametrize("objet, attendu", [
    ("Renonciation à exercer le droit de préemption sur la DIA n°12", "preemption_dia"),
    ("Déclaration d'intention d'aliéner - parcelle AB 12", "preemption_dia"),
    ("Exercice du droit de préemption urbain sur la parcelle AB 12", "preemption_exercee"),
    ("Délivrance d'une concession funéraire au cimetière communal", "concession_funeraire"),
    ("Ester en justice devant le tribunal administratif", "justice"),
    ("Bail de location d'un logement communal", "louage_particulier"),
    ("Recrutement d'un agent contractuel", "personnel"),
    ("Attribution d'une aide individuelle", "aide_individuelle"),
    ("Délégation du droit de préemption à l'EPF pour la parcelle AC 12", "preemption_deleguee"),
    ("Acquisition amiable de la parcelle C 789", "parcelle"),
    ("Création d'un tarif pour la salle polyvalente", "publiable"),
    ("Mise en place d'une barrière au col", "non_classee"),
])
def test_ce_qui_se_compte_ou_se_masque(objet, attendu):
    assert categorie(objet) == attendu


# Relecture vigie-securite du 15/11 : une liste noire laissait passer ces objets.
@pytest.mark.parametrize("objet", [
    "Délivrance d'une concession trentenaire au nom de M. Marc Calignac",
    "Reprise de la sépulture de la famille Garnier",
    "Attribution d'un logement communal à M. Paul Roux",
    "Location du presbytère à M. Caron",
    "Subvention exceptionnelle à M. Rey pour le ravalement de façade",
    "Remise gracieuse de dette accordée à Mme Gilles",
    "Tarif réduit cantine pour la famille Diallo",
    "Règlement amiable du litige avec M. Bernard",
    "Désignation de Maître Lambert pour représenter la commune",
    "Avenant au contrat de travail de M. Durand",
    "Convention de stage avec M. Hugo Blanc",
    "Gratification du stage de Léa Martin",
    "Aide au permis de conduire de Lucas Martin",
    "Bornage contradictoire avec M. Tissot, parcelle B 456",
    "Arrêté de délégation de signature",
    # Seconde relecture du 15/11.
    "Convention d'occupation du domaine public avec Mme. Durand",
    "Convention d'occupation avec Mlle. Robert",
    "Convention d'occupation du domaine public avec M DUPONT Jean",
    "Convention avec DUPONT Jean pour l'entretien du chemin",
    "Convention d'occupation avec Léa Fenouret",
    "Marché de maîtrise d'œuvre confié à l'architecte DUPONT",
    "Convention avec la commune et Pierre Durand",
    "Marché attribué à l'entreprise individuelle Dupont Pierre",
    "Subvention pour la façade de la maison Martin",
    "Tarif cantine appliqué à l'enfant Lucas",
    "Convention de servitude avec l'EARL Martin",
    "Acquisition des terrains AB12 et AB13",
    "Acquisition du terrain AB n°12",
    "Acquisition du bien DE 45",
    "Convention avec DURAND",
    "Subvention à la boulangerie Dupont",
    "Convention avec Durand et fils",
    "Convention avec l'auto-entrepreneur Pierre",
    "Convention avec M.DURAND",
    "Redevance du kiosque de Mme",
])
def test_un_objet_nominatif_ne_se_publie_jamais(objet):
    from collectors.decisions import COMPTEES
    assert categorie(objet) in COMPTEES


def test_le_connecteur_vise_les_decisions_pas_les_arretes():
    """Les arrêtés du Président nomment des agents : ils ne sont pas des
    décisions par délégation, et le motif ne les prend pas."""
    decision = re.compile(DECISIONS_DEFAUT, re.I)
    assert decision.search("Décision du Président")
    assert decision.search("Décision du bureau communautaire")
    assert not decision.search("Arrêté du Président")
    assert not decision.search("Délibération du conseil communautaire")
    assert not re.search(DELIBERATIONS_DEFAUT, "Décision du Président", re.I)


# ── De la base au snapshot ───────────────────────────────────────────────────

def _decision(numero: str, objet: str, jour: str = "2026-10-07") -> DocumentPublie:
    from scripts.snapshot.socle import RULES
    source = sorted(RULES["events"]["public_sources"])[0]
    return DocumentPublie(
        date=jour, url=f"https://portail.example.org/pieces/{numero}.pdf", libelle=objet,
        source=source,
        acte={"numero": numero, "objet": objet, "type": "Décision du Président",
              "date_teletransmission": jour, "portail": "https://portail.example.org/public/14"})


def _publier(base):
    from scripts.snapshot.actes import etape_actes
    exclusions = defaultdict(Counter)
    sortie = etape_actes(base, {}, set(), lambda t: t, exclusions)
    return {e["id"]: e for e in sortie["public_events"]}, exclusions, sortie


def test_titre_seul_et_categories_comptees(base):
    marche = enregistrer(base, _decision("DC2026037", "AVENANT N°1 AU LOT N°1 DU MARCHE"), "epci")
    dia = enregistrer(base, _decision("DC2026040", "Renonciation à préempter, DIA n°12"), "epci")
    preemption = enregistrer(base, _decision(
        "DC2026041", "Exercice du droit de préemption sur la parcelle AB 12 pour 85 000 €"), "epci")
    base.commit()

    publies, exclusions, sortie = _publier(base)

    assert publies[marche]["type"] == "decision_delegation_cc"
    assert publies[marche]["title"] == "AVENANT N°1 AU LOT N°1 DU MARCHE"
    assert marche not in sortie["textes_extraits"]
    assert dia not in publies
    assert exclusions["decisions_par_delegation"]["preemption_dia"] == 1
    assert publies[preemption]["title"] == "Exercice du droit de préemption sur un bien (85 000 €)"
    # La pièce et le numéro donneraient la parcelle : ils ne sortent pas.
    assert publies[preemption]["source_url"] is None and publies[preemption]["pdf_url"] is None
    assert publies[preemption]["numero_acte"] is None


def test_la_categorie_se_relit_a_la_publication(base):
    """Une catégorie figée à la collecte ne suffit pas : métadonnée absente ou
    classement d'hier trop large, le titre est relu."""
    ancienne = enregistrer(base, _decision("DC2026060", "Subvention à M. Rey"), "epci")
    base.execute("UPDATE events SET metadata=json_set(metadata,'$.categorie_delegation',"
                 "'publiable') WHERE id=?", (ancienne,))
    sans = enregistrer(base, _decision("DC2026061", "DEMANDE DE SUBVENTION"), "epci")
    base.execute("UPDATE events SET metadata=json_remove(metadata,'$.categorie_delegation')"
                 " WHERE id=?", (sans,))
    base.commit()

    publies, exclusions, _ = _publier(base)

    assert ancienne not in publies and sans not in publies
    assert exclusions["decisions_par_delegation"]["nominative"] == 1
    assert exclusions["decisions_par_delegation"]["non_classee"] == 1


def test_un_nom_que_seule_la_base_connait_fait_compter(base):
    """`redige` masque à l'écriture les noms que la base connaît : la décision
    qu'il aurait réécrite se compte, au lieu de sortir avec « un particulier »
    et de faire refuser tout le snapshot par le contrôle."""
    from scripts.snapshot.actes import etape_actes
    # En capitales sans civilité : le classement ne le reconnaît pas (limite connue).
    eid = enregistrer(base, _decision("DC2026070", "CONVENTION AVEC CAMILLE HABITANTE"), "epci")
    base.execute("UPDATE events SET metadata=json_set(metadata,'$.categorie_delegation',"
                 "'publiable') WHERE id=?", (eid,))
    base.commit()
    exclusions = defaultdict(Counter)
    sortie = etape_actes(base, {}, set(),
                         lambda t: (t or "").replace("CAMILLE HABITANTE", "un particulier"),
                         exclusions)
    assert eid not in {e["id"] for e in sortie["public_events"]}
    assert exclusions["decisions_par_delegation"]["nominative"] == 1


def test_une_decision_n_est_pas_une_deliberation():
    from scripts.snapshot.actes import TYPES_DECISION, TYPES_DELIBERES
    assert not set(TYPES_DECISION) & set(TYPES_DELIBERES)


def test_piece_retiree_du_portail(base):
    """La dernière passe ne voit plus la pièce : le lien ne sort plus."""
    ancienne = enregistrer(base, _decision("DC2026001", "VIREMENT DE CREDITS", "2026-08-01"), "epci")
    recente = enregistrer(base, _decision("DC2026050", "DEMANDE DE SUBVENTION"), "epci")
    base.execute("UPDATE events SET metadata=json_set(metadata,'$.vu_le','2026-09-01') WHERE id=?",
                 (ancienne,))
    base.execute("UPDATE events SET metadata=json_set(metadata,'$.vu_le','2026-11-15') WHERE id=?",
                 (recente,))
    base.commit()

    publies, _, _ = _publier(base)

    assert publies[ancienne]["piece_retiree"] is True
    assert publies[ancienne]["source_url"] is None and publies[ancienne]["pdf_url"] is None
    assert publies[recente]["source_url"]
    assert "piece_retiree" not in publies[recente]


# ── Le contrôle adverse ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def vs():
    spec = importlib.util.spec_from_file_location(
        "verify_snapshot", ROOT / "scripts" / "verify_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_le_controle_refuse_ce_qui_designe_une_personne(vs, tmp_path):
    (tmp_path / "events.json").write_text(json.dumps({"events": [
        {"id": 1, "type": "decision_delegation_cc", "title": "Concession",
         "categorie_delegation": "concession_funeraire"},
        {"id": 2, "type": "decision_delegation", "title": "Bornage, parcelle AB 12",
         "categorie_delegation": "publiable"},
        {"id": 3, "type": "decision_delegation", "title": "Subvention à un particulier",
         "categorie_delegation": "publiable"},
        {"id": 4, "type": "decision_delegation", "title": "Exercice du droit de préemption",
         "categorie_delegation": "preemption_exercee", "pdf_url": "https://x.example/p.pdf"},
        {"id": 5, "type": "decision_delegation_cc", "title": "Emprunt",
         "categorie_delegation": "publiable", "source_url": "https://x.example/e.pdf"},
    ]}), encoding="utf-8")
    rep = vs.Report()
    vs.check_decisions_par_delegation(tmp_path, rep)
    assert set(rep.errors) == {"décision d'une catégorie comptée publiée une à une",
                               "décision publiée qui donne une parcelle",
                               "décision publiée qui désigne une personne",
                               "préemption publiée avec sa pièce ou son numéro"}
