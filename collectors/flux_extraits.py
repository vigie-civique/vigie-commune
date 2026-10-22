#!/usr/bin/env python3
"""flux_extraits.py — faire entrer les subventions lues dans les actes, par l'atelier.

POURQUOI
L'essentiel de l'argent qu'une intercommunalité verse aux associations est voté
acte par acte — conventions d'objectifs, subventions exceptionnelles,
complémentaires — et non dans les tableaux annuels que lit `cm_finances`.
Relevé à Lasalle le 22/10/2026 : 250 à 380 k€ versés par an au compte 6574
(DGFiP), une vingtaine de flux par an en base jusqu'en 2024, aucun ensuite,
alors que les actes de 2022 à 2026 sont en base avec leur texte. Un outil
d'extraction hors dépôt (`vigie_subventions_cr.py`, modèle local, garde par
citation, montant et tiers) les lit ; il n'avait pour sortie qu'une écriture
directe dans un fichier de saisies local, hors de l'atelier — ce que la charte
d'usage de l'IA exclut : rien n'entre en base sans avoir été relu et validé
dans l'atelier.

LE CHEMIN — celui des marchés lus (`marches_extraits`), dont il reprend les
gestes : lecture d'une valeur, acte retrouvé après un redécoupage, citation
lue dans l'acte ou dans la même pièce, passage surligné, archivage du rapport.

    rapport JSON ──deposer()──▶ une PROPOSITION par ligne (nature `flux`)
                                 │ un validateur relit, l'acte sous les yeux :
                                 │ accepte, corrige, écarte
                                 ▼
                 saisie_de() ──▶ une saisie `flux` ──import_saisies()──▶ financial_flows

FORMAT — `{"format": "vigie-flux-extraits/1", "modele": {…}, "lignes": [...]}`.
`modele` suit la charte (engagement 4) : `{modele, empreinte, locale}`. Une
ligne porte les champs de `CHAMPS`. L'assemblée qui a voté ne vient JAMAIS du
rapport : elle se lit dans l'acte (`cm_finances.payeur`).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import propositions as _propositions
from .citations import compact, montants_cites
from .cle_acte import TYPES_ACTES, TYPES_SEANCES
from .extraction import CITATION_MIN, _aplatir, trace_ia
from .marches_extraits import (LigneRefusee, RapportRefuse, _acte, _valeur,
                               acte_de_proposition, archiver, empreinte,
                               extrait_de, retrouver_citation)

__all__ = ["LigneRefusee", "RapportRefuse", "acte_de_proposition", "archiver",
           "empreinte", "extrait_de"]

FORMAT = "vigie-flux-extraits/1"
NATURE = "flux"
RAPPORTS = Path(__file__).resolve().parent.parent / "data" / "extraits" / "flux"

#: (obligatoire, genre, ce qu'il dit) — les genres de `marches_extraits._valeur`.
CHAMPS = {
    "event_id":   (True,  "entier",  "L'acte d'où la ligne est lue (events.id de CETTE base)"),
    "date":       (True,  "date",    "Date de l'acte, AAAA-MM-JJ — vérifiée contre la base"),
    "citation":   (True,  "texte",   "Passage LITTÉRAL de l'acte qui atteste la ligne"),
    "sens":       (True,  "choix:VERSE,RECU", "VERSE par l'assemblée, ou RECU par la collectivité"),
    "tiers":      (True,  "texte",   "Le bénéficiaire (versé) ou le financeur (reçu)"),
    "montant":    (True,  "montant", "Montant en euros, tel que l'acte le donne"),
    "annee":      (False, "entier",  "L'exercice, s'il diffère de l'année de l'acte"),
    "nature":     (False, "texte",   "attribution, convention, complément, notification"),
    "titre_acte": (False, "texte",   "Le titre de l'acte, pour le relecteur"),
    "source_url": (False, "texte",   "L'adresse de la pièce, pour le relecteur"),
    # Le type d'une fiche à CRÉER ne se devine ni du nom ni du sens : c'est le
    # validateur qui le donne (cf. `saisies._entite_du_tiers`).
    "type_tiers": (False, "choix:ASSOCIATION,BUSINESS,SERVICE",
                   "Si aucune fiche n'est rapprochée : association, entreprise, service public"),
}

#: Ni l'acte ni la citation : ce sont les preuves relues.
CORRIGEABLES = ("tiers", "type_tiers", "montant", "annee", "sens", "nature")

#: Ce qu'un rapport de modèle peut écrire de plus absurde ne doit pas faire
#: tomber la file : une année de 20 chiffres débordait `doublons()`.
ANNEES = (1990, 2100)
MONTANT_MAX = 1e9


def _bornes(ligne: dict) -> None:
    if "annee" in ligne and not ANNEES[0] <= ligne["annee"] <= ANNEES[1]:
        raise LigneRefusee(f"annee : hors de {ANNEES[0]}-{ANNEES[1]}")
    if not 0 < ligne["montant"] <= MONTANT_MAX:
        raise LigneRefusee("montant : nul ou invraisemblable")


def _propre(champ: str, v):
    return v.lower() if champ in ("sens", "type_tiers") else v


def lire_ligne(brute) -> tuple[dict, list[str]]:
    if not isinstance(brute, dict):
        raise LigneRefusee("une ligne doit être un objet JSON")
    propre: dict = {}
    for champ, (obligatoire, genre, _) in CHAMPS.items():
        v = _valeur(champ, genre, brute.get(champ))
        if v is None:
            if obligatoire:
                raise LigneRefusee(f"{champ} : champ obligatoire")
            continue
        propre[champ] = _propre(champ, v)
    _bornes(propre)
    return propre, sorted(set(brute) - set(CHAMPS))


def lignes_du_rapport(rapport) -> list:
    if not isinstance(rapport, dict):
        raise RapportRefuse("le rapport doit être un objet JSON")
    if rapport.get("format") != FORMAT:
        raise RapportRefuse(f"format « {rapport.get('format')} » inconnu — attendu « {FORMAT} »")
    lignes = rapport.get("lignes")
    if not isinstance(lignes, list):
        raise RapportRefuse("le rapport n'a pas de liste « lignes »")
    return lignes


def cle_de(ligne: dict, acte_cle: str | None = None) -> str:
    """L'acte, le sens, le tiers et le montant, sous forme compacte."""
    parts = (f"cle:{acte_cle}" if acte_cle else str(ligne["event_id"]), ligne["sens"],
             compact(ligne["tiers"]), f"{float(ligne['montant']):.2f}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _source_de_l_acte(conn, event_id: int) -> str | None:
    r = conn.execute("SELECT source FROM events WHERE id=?", (event_id,)).fetchone()
    return r[0] if r else None


def fiche_du_tiers(conn, nom: str, resolveur=None) -> tuple[int | None, str | None]:
    """La fiche que le rapprochement du moteur donne à ce nom — montrée au
    validateur AVANT qu'il accepte : un rapprochement par les mots peut se
    tromper, et il est seul à pouvoir le voir."""
    from .cm_finances import Resolver
    # Le résolveur charge toutes les fiches : un par dépôt, pas un par ligne.
    eid, _ = (resolveur or Resolver(conn)).resolve(nom)
    if not eid:
        return None, None
    r = conn.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()
    return eid, (r[0] if r else None)


def examiner(conn, ligne: dict, resolveur=None) -> dict:
    """La charge d'une proposition, ou `LigneRefusee` avec la raison."""
    from .cm_finances import payeur
    if len(_aplatir(ligne["citation"])) < CITATION_MIN:
        raise LigneRefusee(f"citation trop courte pour attester quoi que ce soit "
                           f"(moins de {CITATION_MIN} caractères)")
    acte = _acte(conn, ligne["event_id"])
    if acte is None:
        raise LigneRefusee(f"acte {ligne['event_id']} absent de cette base")
    if acte["type"] not in TYPES_ACTES + TYPES_SEANCES:
        raise LigneRefusee(f"l'événement {ligne['event_id']} n'est pas un acte "
                           f"d'assemblée ({acte['type']})")
    if (acte["date"] or "")[:10] != ligne["date"]:
        raise LigneRefusee(f"l'acte {ligne['event_id']} est daté du {acte['date']}, "
                           f"la ligne du {ligne['date']} : le rapport vient-il d'une "
                           f"autre base ?")
    ou = retrouver_citation(conn, acte, ligne["citation"])
    if ou is None:
        raise LigneRefusee("citation introuvable dans le texte de l'acte")
    tiers_id, tiers_fiche = fiche_du_tiers(conn, ligne["tiers"], resolveur)
    charge = {c: ligne[c] for c in CHAMPS if c in ligne and c != "event_id"}
    charge.update({
        "assemblee": payeur(acte["type"], _source_de_l_acte(conn, acte["id"])),
        "trouvee_dans": ou,
        **({"acte_cle": acte["cle_acte"]} if acte.get("cle_acte") else {}),
        "montant_dans_citation": any(abs(m - ligne["montant"]) < 0.01
                                     for m in montants_cites(ligne["citation"])),
        **({"tiers_id": tiers_id, "tiers_fiche": tiers_fiche} if tiers_id else {}),
    })
    return charge


def deposer(conn, rapport, auteur: dict, empreinte_rapport: str = "") -> dict:
    """Une proposition par ligne nouvelle et attestée. N'écrit rien d'autre ;
    la transaction est celle de l'appelant."""
    _propositions.assurer_schema(conn)
    lignes = lignes_du_rapport(rapport)
    try:
        ia = trace_ia(rapport.get("modele"))
    except ValueError as e:
        raise RapportRefuse(str(e))
    bilan = {"lignes": len(lignes), "proposees": 0, "deja_proposees": 0,
             "deja_importees": 0, "refusees": [], "cles_ignorees": [],
             "par_assemblee": {}}
    ignorees: set[str] = set()
    from .cm_finances import Resolver
    resolveur = Resolver(conn)
    for i, brute in enumerate(lignes, start=1):
        try:
            ligne, inconnues = lire_ligne(brute)
            ignorees.update(inconnues)
            charge = examiner(conn, ligne, resolveur)
        except LigneRefusee as e:
            bilan["refusees"].append({"ligne": i, "motif": str(e)})
            continue
        cle = cle_de(ligne, charge.get("acte_cle"))
        if conn.execute("SELECT 1 FROM propositions WHERE nature=? AND cle=?",
                        (NATURE, cle)).fetchone():
            bilan["deja_proposees"] += 1
            continue
        if empreinte_rapport:
            charge["rapport"] = empreinte_rapport
        if ia:
            charge["ia"] = ia
        _propositions.proposer(conn, NATURE, "deliberation", ligne["event_id"], None,
                               charge, {}, auteur, cle=cle)
        bilan["proposees"] += 1
        bilan["par_assemblee"][charge["assemblee"]] = (
            bilan["par_assemblee"].get(charge["assemblee"], 0) + 1)
    bilan["cles_ignorees"] = sorted(ignorees)
    return bilan


def journaliser(conn, auteur: dict, empreinte_rapport: str, bilan: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, entity_id, table_name, action, field, new_value) "
        "VALUES(?,?,?,?,?,?)",
        (auteur["id"], None, "propositions", "deposer_flux_extraits",
         f"rapport/{empreinte_rapport}", json.dumps(
             {k: v for k, v in bilan.items() if k != "refusees"}, ensure_ascii=False)))


def doublons(conn, charge: dict, limite: int = 3) -> list[dict]:
    """Les flux DÉJÀ en base de la même année et du même montant (à l'euro) :
    lus dans un tableau, saisis à la main, ou venus d'un jeu ouvert. Un signal
    pour le validateur, jamais un refus."""
    annee = charge.get("annee") or int((charge.get("date") or "0")[:4])
    rows = conn.execute(
        "SELECT f.id, f.year, f.amount, f.source, f.confidence, "
        "       ef.name AS de, et.name AS vers "
        "FROM financial_flows f LEFT JOIN entities ef ON ef.id=f.from_id "
        "LEFT JOIN entities et ON et.id=f.to_id "
        "WHERE f.year=? AND ABS(f.amount - ?) < 1 LIMIT ?",
        (annee, charge["montant"], limite)).fetchall()
    return [dict(zip(("id", "year", "amount", "source", "confidence", "de", "vers"), r))
            for r in rows]


def corriger(charge: dict, corrections: dict) -> dict:
    interdits = set(corrections or {}) - set(CORRIGEABLES)
    if interdits:
        raise LigneRefusee(f"non corrigeable : {', '.join(sorted(interdits))} — "
                           f"l'acte et la citation sont la preuve relue")
    sortie = dict(charge)
    for champ, v in (corrections or {}).items():
        obligatoire, genre, _ = CHAMPS[champ]
        propre = _valeur(champ, genre, v)
        if propre is None:
            if obligatoire:
                raise LigneRefusee(f"{champ} : ne peut pas être vidé")
            sortie.pop(champ, None)
        else:
            sortie[champ] = _propre(champ, propre)
    _bornes(sortie)
    if "tiers" in (corrections or {}):
        # Le nom a changé : la fiche montrée au dépôt ne vaut plus, et aucune
        # autre n'est cherchée en douce — une fiche est créée sous ce nom.
        sortie.pop("tiers_id", None)
        sortie.pop("tiers_fiche", None)
    return sortie


def saisie_de(conn, proposition: dict, corrections: dict, validateur: dict) -> dict:
    """La saisie `flux` qu'écrit une proposition acceptée."""
    charge = corriger(proposition["charge"], corrections)
    acte = acte_de_proposition(conn, proposition) or {}
    doc = None
    if acte.get("raw_document_id"):
        doc = conn.execute("SELECT id, sha256, url FROM raw_documents WHERE id=?",
                           (acte["raw_document_id"],)).fetchone()
    source = {
        "citation": charge["citation"],
        "event_id": acte.get("id", proposition["object_id"]),
        "acte_cle": acte.get("cle_acte"),
        "acte_date": (acte.get("date") or "")[:10] or None,
        "url": (doc[2] if doc else None) or acte.get("source_url") or charge.get("source_url"),
    }
    if doc:
        source.update(raw_document_id=doc[0], sha256=doc[1])
    if charge.get("ia"):
        source["ia"] = charge["ia"]
    # Seule la fiche MONTRÉE au validateur est retenue : aucun rapprochement
    # n'est refait à l'acceptation, il rattacherait le flux à une fiche que
    # personne n'a vue.
    tiers_id = charge.get("tiers_id")
    if tiers_id is not None:
        if not conn.execute("SELECT 1 FROM entities WHERE id=?", (tiers_id,)).fetchone():
            raise LigneRefusee("la fiche rapprochée n'existe plus : écarter la ligne, "
                               "puis redéposer le rapport")
        tiers = {"id": tiers_id}
    elif charge.get("type_tiers"):
        tiers = {"nom": charge["tiers"], "type": charge["type_tiers"]}
    else:
        raise LigneRefusee("aucune fiche rapprochée : donner le type du tiers "
                           "(association, entreprise, service public) pour la créer")
    titre = charge.get("titre_acte") or acte.get("title") or ""
    valeurs = {
        "type": "subvention",
        "year": charge.get("annee") or int(charge["date"][:4]),
        "amount": charge["montant"],
        "sens": charge["sens"],
        "tiers": tiers,
        "description": f"{charge.get('nature') or 'subvention'} — {titre}"[:250],
        # Un acte d'attribution atteste un VOTE, pas un paiement.
        "statut": "engage" if charge.get("nature") == "notification" else "vote",
        "assemblee": charge["assemblee"],
    }
    return {
        "id": f"pv-flux-{proposition['cle']}",
        "objet": "flux",
        "valeurs": valeurs,
        "source": {k: v for k, v in source.items() if v is not None},
        "confidence": "confirmed",
        "saisi_par": validateur["email"],
        "saisi_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "proposition": proposition["id"],
        **({"corrige": sorted(corrections)} if corrections else {}),
    }
