"""
decisions.py — Les décisions prises par délégation du conseil.

Le maire (CGCT L2122-22) et le président ou le bureau d'une intercommunalité
(L5211-10) décident seuls, par délégation, de ce que le conseil leur a confié :
marchés, emprunts, demandes de subvention, conventions, mais aussi préemptions,
concessions funéraires, louages, actions en justice. Ce sont des actes publics.
Ce ne sont pas des délibérations : ils ont leur propre type d'acte et ne
comptent jamais parmi elles (`TYPES_DELIBERES`, `scripts/snapshot/actes.py`).

**Ce qui sort, et ce qui ne sort pas** (avis DPO du 14/11/2026, validé par
Julien le 15/11) :

  - le TITRE seul, avec la date, le montant éventuel et le lien vers la pièce ;
    jamais le texte, qui n'est ni lu ni stocké ici ;
  - certaines catégories ne sortent qu'en NOMBRE, parce qu'elles portent sur un
    particulier qu'un titre ou une parcelle désignerait (`COMPTEES`) ;
  - une préemption EXERCÉE sort, sous un titre qui ne nomme personne et ne
    donne pas la parcelle (`titre_de_preemption`, côté snapshot).

Le classement lit l'OBJET déclaré par la collectivité. Il penche du côté
prudent : un objet qui évoque à la fois une convention et un contentieux est
compté, pas publié.

**La date** est celle de télétransmission (`DATEACTE` du portail) : une
décision n'a pas de séance, et c'est la seule date que la source déclare.

**La pièce** reste sur le portail, qui la retire après l'affichage légal
(deux mois environ). `vu_le` dit quand elle y était encore : le snapshot
retire le lien d'une pièce que le portail ne sert plus, plutôt que de publier
un lien mort.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

from .config import EPCI_NOM

TYPES = {"commune": "decision_delegation", "epci": "decision_delegation_cc"}

#: Catégories qui ne sortent qu'en nombre. Tout ce qui n'est pas reconnu comme
#: impersonnel y tombe (`non_classee`) : relecture vigie-securite du 15/11, sur
#: 45 objets nominatifs plausibles, une liste noire en laissait passer 40.
COMPTEES = frozenset({
    "preemption_dia", "preemption_deleguee", "concession_funeraire",
    "louage_particulier", "aide_individuelle", "justice", "personnel",
    "nominative", "parcelle", "non_classee",
})


def _plat(texte: str) -> str:
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Lus sur l'objet BRUT (la casse compte) : ce qui désigne une personne.
# « Mme. Durand », « M DUPONT » : le point est facultatif (seconde relecture du 15/11).
# Les formes courtes gardent leur casse : « 500 m de voirie » n'est personne.
CIVILITE = re.compile(r"\b(M|MM|Mmes?|Mlle|Mr|Dr|Me|MME|MLLE)(?:\.\s?|\s|$)(?=[A-ZÉÈ]|$)"
                      r"|\b(?i:docteur|monsieur|madame|mademoiselle|ma[iî]tre)\b")
_PROCHE = re.compile(r"\b(famille|consorts|indivision|succession|veuve|epoux|epouse|heritiers?)\b"
                     # Une activité tenue par une personne (troisième relecture du 15/11).
                     r"|\bet fils\b|auto.?entrepreneu|micro.?entrepris|food.?truck")
# Une enseigne au nom de son exploitant : « la boulangerie Dupont », « la ferme Martin ».
_ENSEIGNE = re.compile(r"\b(?i:boulangerie|boucherie|[ée]picerie|ferme|exploitation|commerce|bar"
                       r"|restaurant|caf[ée]|atelier|salon|magasin|domaine|gîte|gite|camping)"
                       r"\s+[A-ZÉÈ][A-Za-zéèêëàâîïôöûüç-]+\b")
# Ce qu'une personne possède ou qui elle est : « la maison Martin », « l'enfant Lucas ».
_POSSESSION = re.compile(r"\b(propri[ée]t[ée]|maison|enfant|v[ée]hicule|terrain|jardin)\s+([A-ZÉÈ][a-zéèêëàâîïôöûüç]+)\b")
# Un métier suivi d'un nom : « l'architecte DUPONT », « le géomètre Martin ».
_METIER = re.compile(r"\b(?i:architecte|g[ée]om[èe]tre|notaire|avocat|huissier|expert|m[ée]decin"
                     r"|exploitant|agriculteur|artisan|commer[çc]ant)"
                     r"\s+[A-ZÉÈ][A-Za-zéèêëàâîïôöûüç-]+\b")
# Une exploitation ou une société à un seul associé porte le nom de l'exploitant.
_FORME_PERSONNELLE = re.compile(r"\b(EARL|GAEC|SCEA|EIRL|EURL|EI)\b")
# Un nom en capitales collé à un prénom : « DUPONT Jean », « Lou SALOMBE ».
NOM_CAPITALES = re.compile(r"\b[A-ZÉÈ][a-zéèêëàâîïôöûüç]+\s+[A-ZÉÈÀÂ]{2,}\b"
                           r"|\b[A-ZÉÈÀÂ]{2,}\s+[A-ZÉÈ][a-zéèêëàâîïôöûüç]+\b")
# Prénom Nom, en casse mêlée (« Léa Martin »). Un nom en capitales sans civilité
# (« LÉA MARTIN ») n'est pas reconnaissable d'un intitulé : limite connue.
PRENOM_NOM = re.compile(
    r"\b[A-ZÉÈ][a-zéèêëàâîïôöûüç]+(?:-[A-ZÉÈ][a-zéèêëàâîïôöûüç]+)?\s+[A-ZÉÈ][a-zéèêëàâîïôöûüç]{2,}\b")
# … sauf quand il nomme un organisme : « l'association Radio Saint Ferréol »,
# « la Cité scolaire François-Jean Armorin ».
# Le mot d'organisme doit précéder le nom sans « et » ni « de » entre eux : « la
# commune et Pierre Durand » nomme une personne. Ni « société » ni « entreprise » :
# une entreprise individuelle, c'est une personne.
_ORGANISME = re.compile(r"(association|asso|radio|cite|college|lycee|ecole|centre|fondation"
                        r"|club|ehpad|sivom|syndicat|communaute|commune"
                        r"|departement|region|banque|caisse|office|agence|maison|foyer"
                        r"|residence|salle|stade|gymnase|rue|avenue|place|chemin|route)\W+"
                        r"((?!et\b|de\b|du\b|des\b)\w+\W+){0,2}$")
# Les organismes qu'un objet de décision nomme couramment : un sigle ou un nom
# d'organisme collé à un mot capitalisé n'est pas une personne (« EPF Occitanie »,
# « Crédit Agricole »). Troisième relecture du 15/11 : sans lui, 24 objets
# impersonnels sur 46 étaient comptés « nominatifs ». Lexique à compléter au fil
# des objets réels : un oubli fait compter à tort, il ne fait rien publier.
ORGANISMES = {
    "amf", "amrf", "adcf", "aric", "epf", "sdis", "sivom", "sivu", "sictom", "smictom", "caue", "caf", "cpam", "msa", "urssaf",
    "enedis", "edf", "grdf", "gdf", "engie", "veolia", "saur", "suez", "orange", "sfr", "free",
    "detr", "dsil", "dsr", "dsid", "fctva", "feder", "feader", "leader", "ademe", "anah", "ars",
    "dreal", "ddt", "ddtm", "ddfip", "dgfip", "cerema", "insee", "onf", "ofb", "inrap",
    "colas", "eiffage", "vinci", "spie", "eurovia", "setec", "cereg", "jvs", "mairistem",
    "berger", "levrault", "credit", "agricole", "mutuel", "poste", "groupama", "smacl", "maif",
    "macif", "axa", "allianz", "banque", "populaire", "caisse", "depots", "france", "services",
    "habitat", "parc", "national", "regional", "agence", "eau", "syndicat", "mixte", "spl",
    "sem", "sa", "sas", "sarl", "scic", "scop", "gip", "epci", "cc", "cias", "ccas", "ehpad",
    "renault", "peugeot", "citroen", "kangoo", "multisports", "travail", "emploi", "controles",
    "alpes", "rhone", "mediterranee", "occitanie", "auvergne", "provence", "gard", "drome",
    "herault", "ardeche", "lozere", "cevennes", "gardons", "prefecture", "region", "departement",
}
# … ou un lieu, un service : « l'Espace Jeunes », « Pôle Enfance », « Val Drôme ».
_NOMS_COMMUNS = {"espace", "pole", "maison", "centre", "conseil", "office", "point", "relais",
                 "pays", "coeur", "haut", "haute", "bas", "basse", "saint", "sainte",
                 "mont", "vallee", "communaute", "departement", "region", "banque", "plan",
                 "projet", "programme", "fonds", "etat", "ville", "service", "pont", "bois"}
# Une parcelle désigne son propriétaire par le cadastre : « AB 12 », « AB12 »,
# « AB n°12 », « DE 45 » (DE, LA, AU… sont aussi des sections). Pas un montant
# (« DE 365 000 € »), un numéro d'acte (« DC2026-029 »), une route (« RD 907 »),
# une nomenclature (« M57 »), une décision modificative (« DM n°2 »), une classe
# (« CE1 ») ni un bâtiment (« bâtiment B 2 »).
PARCELLE = re.compile(r"parcelle|cadastr|\bsection\s+[A-Z]{1,2}\b"
                      r"|(?<!timent )(?<!classe )(?<!classes )(?<!bloc )(?<!lot )"
                      r"\b(?!R[DN]\b|DM\b|C[EMP]\d|M\d{2}\b)[A-Z]{1,2}\s?(?:n[°o]\s?)?\d{1,4}\b"
                      r"(?![\s\u00a0.]\d{3}|\s*(?:€|euros?\b)|[-/])")
# Un nom seul en capitales après « avec », « à », « au profit de » dans un objet
# en casse mêlée : « Convention avec DURAND » (mais pas « avec ENEDIS »).
_NOM_SEUL = re.compile(r"\b(?:avec|à|au profit de|attribu\w* à|confi\w* à)\s+(?:l'|la |le |les )?"
                       r"([A-ZÉÈÀÂ]{3,})\b(?!\s+[A-ZÉÈ]{2,})")


def nominatif(objet: str) -> bool:
    """L'objet désigne-t-il une personne ? Le doute joue contre la publication."""
    if (CIVILITE.search(objet) or _PROCHE.search(_plat(objet))
            or _FORME_PERSONNELLE.search(objet) or _METIER.search(objet)
            or _ENSEIGNE.search(objet)):
        return True
    connus = ORGANISMES | _NOMS_COMMUNS

    def mots(texte: str) -> set[str]:
        return {_plat(w) for w in re.split(r"[\s'’-]+", texte) if w}

    for m in _POSSESSION.finditer(objet):
        if _plat(m.group(2)) not in connus:
            return True
    for m in NOM_CAPITALES.finditer(objet):
        if not (mots(m.group()) & connus) and not _ORGANISME.search(_plat(objet[:m.start()])):
            return True
    for m in PRENOM_NOM.finditer(objet):
        if mots(m.group()) & connus or _ORGANISME.search(_plat(objet[:m.start()])):
            continue
        return True
    if re.search(r"[a-zé]{3}", objet):        # casse mêlée : un mot en capitales s'y remarque
        for m in _NOM_SEUL.finditer(objet):
            if _plat(m.group(1)) not in connus:
                return True
    return False

# Lus sur l'objet aplati (minuscules, sans accents). L'ordre compte.
_COMPTEES = (
    ("concession_funeraire", re.compile(r"concession|cimetiere|columbarium|caveau|sepulture|inhum|obseques")),
    ("justice", re.compile(r"en justice|contentieu|recours|tribunal|\binstance\b|avocat|requete|refere"
                           r"|plainte|partie civile|litige|affaire|defense de la commune|cabinet"
                           r"|indemni|sinistre|amiable")),
    ("personnel", re.compile(r"recrut|\bagent|(?<!pole )emploi|nomination|regisseu|stage|vacataire|contractuel"
                             r"|contrat de travail|contrat d.engagement|disponibilit|animateur|saisonnier"
                             r"|delegation de signature")),
    ("aide_individuelle", re.compile(r"\baide\b|secours|bourse|allocation|remise gracieuse|non.valeur"
                                     r"|indigent|exoner|\bprime\b|gratification|tarif reduit|remboursement")),
    ("louage_particulier", re.compile(
        r"(bail|location|louage|loyer|occupation|mise a disposition|attribution)\w*.{0,60}"
        r"(logement|habitation|appartement|maison|gite|presbytere|villa|garage|local|salle|terrain)")),
)
# Ce qui se publie : des objets impersonnels par nature. Rien d'autre.
PUBLIABLE = re.compile(
    r"march|avenant|\blot\b|accord.cadre|consultation|groupement de commandes|emprunt"
    r"|ligne de tresorerie|refinancement|subvention|fonds vert|\bdetr\b|\bdsil\b|dotation"
    r"|virement de credits|decision modificative|budget|tarif|redevance|reglement"
    r"|adhesion|cotisation|contribution|participation|nomenclature|contrat"
    r"|convention|travaux|maitrise d.ouvrage|mission"
    r"|etude|assurance|contrat de (maintenance|prestation|fourniture)")


def categorie(objet: str) -> str:
    """La catégorie d'une décision, lue dans son objet.

    `publiable` seulement si l'objet est reconnu impersonnel et ne désigne ni
    personne ni parcelle ; `non_classee` (comptée) pour tout le reste.
    """
    brut = objet or ""
    plat = _plat(brut)
    if re.search(r"\bdia\b|intention d.alien|renon\w*.{0,30}preempt|ne pas (exercer|preempter)", plat):
        return "preemption_dia"
    if re.search(r"delegation du droit de preempt", plat):
        return "preemption_deleguee"
    if "preempt" in plat:
        # Publiée sous un titre neutre, sans lien ni numéro (`actes.py`).
        return "preemption_exercee"
    if nominatif(brut):
        return "nominative"
    if PARCELLE.search(brut):
        return "parcelle"
    for nom, motif in _COMPTEES:
        if motif.search(plat):
            return nom
    return "publiable" if PUBLIABLE.search(plat) else "non_classee"


def enregistrer(conn, doc, portee: str, passe: str | None = None) -> int:
    """Une décision = un événement, identifié par son numéro dans son année.

    Seuls le numéro, l'objet, le type déclaré, la catégorie et le lien sont
    écrits : le texte de la pièce n'est pas lu (titre seul, 14/11/2026).
    """
    type_ = TYPES[portee]
    acte = doc.acte or {}
    numero = (acte.get("numero") or "").strip()
    objet = (acte.get("objet") or doc.libelle or "").strip()
    meta = {
        "numero_acte": numero,
        "type_acte": acte.get("type") or "",
        "categorie_delegation": categorie(objet),
        "portail": acte.get("portail") or "",
        "date_teletransmission": acte.get("date_teletransmission") or doc.date,
        "date_source": "teletransmission",
        # Une date par PASSE, fixée une fois : une passe qui franchit minuit ne
        # doit pas faire croire que la moitié du catalogue a disparu.
        "vu_le": passe or date.today().isoformat(),
    }
    if portee == "epci" and EPCI_NOM:
        meta["instance"] = EPCI_NOM

    row = None
    if numero:
        row = conn.execute(
            "SELECT id FROM events WHERE type=? AND json_extract(metadata,'$.numero_acte')=?"
            " AND substr(date,1,4)=?", (type_, numero, (doc.date or "")[:4])).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM events WHERE type=? AND date=? AND title=?",
            (type_, doc.date, objet)).fetchone()
    corps = json.dumps(meta, ensure_ascii=False)
    if row:
        conn.execute("UPDATE events SET date=?, title=?, metadata=?, source=?, source_url=?"
                     " WHERE id=?", (doc.date, objet, corps, doc.source, doc.url, row[0]))
        return row[0]
    return conn.execute(
        "INSERT INTO events (type,date,title,source,source_url,metadata) VALUES (?,?,?,?,?,?)",
        (type_, doc.date, objet, doc.source, doc.url, corps)).lastrowid


def collecter(portee: str, documents: list) -> dict:
    """Enregistre les décisions cataloguées ; rend le compte par catégorie."""
    from .db import transaction

    compte: dict[str, int] = {}
    passe = date.today().isoformat()
    with transaction() as conn:
        for doc in documents:
            enregistrer(conn, doc, portee, passe)
            cat = categorie((doc.acte or {}).get("objet") or doc.libelle)
            compte[cat] = compte.get(cat, 0) + 1
    return compte
