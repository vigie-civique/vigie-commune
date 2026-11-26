"""Les fiches publiées : quelles entités sortent, sous quelle forme, et pourquoi les autres non.

L'étape `fiches` lit toutes les entités de la base et rend celles qui ont
droit à une fiche publique (`public_entities`, `public_ids`), et pour les
autres la raison de leur exclusion. Le verdict de l'atelier passe avant les
règles ; le périmètre et les règles de publication font le reste
(`public_entity`).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from collectors.etat_flux import etat_du_flux
from collectors.verdict import ecarte
from scripts.snapshot.actes import (_DESIGNE_UNE_PERSONNE, TYPES_DECISION, TYPES_DELIBERES,
                                    _annee_de_trace)
from scripts.snapshot.perimetre import publiable_dans_perimetre
from scripts.snapshot.socle import ROOT, RULES, rows, write_json_compact


POP_NOTICE = "https://www.pop.culture.gouv.fr/notice/merimee/{ref}"


def monuments_historiques(osm_value, tags_json) -> list[dict]:
    """Les protections MH d'un lieu, lues dans `places.tags` : type, référence, notice POP.

    Deux formes en base : `monuments_historiques` (liste, depuis le 02/11/2026,
    posée même sur une fiche fusionnée avec OSM) et, avant, `reference` et
    `protection` à la racine des tags d'une ligne `monument_historique`. Rien
    d'autre des tags ne sort : ceux d'OSM ou de Pappers portent téléphones et
    adresses de courriel.
    """
    try:
        tags = json.loads(tags_json or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(tags, dict):
        return []
    entrees = tags.get("monuments_historiques")
    if not isinstance(entrees, list):
        entrees = ([{"reference": tags.get("reference"), "protection": tags.get("protection")}]
                   if osm_value == "monument_historique" and tags.get("reference") else [])
    sortie = []
    for m in entrees:
        ref = str((m or {}).get("reference") or "").strip() if isinstance(m, dict) else ""
        if not ref:
            continue
        sortie.append({"reference": ref,
                       "protection": (m.get("protection") or None),
                       "notice": POP_NOTICE.format(ref=ref)})
    return sortie


def in_commune_bbox(lat, lng) -> bool:
    if lat is None or lng is None:
        return False
    bbox = RULES["locations"]["bbox"]
    return (
        bbox["lat_min"] <= lat <= bbox["lat_max"]
        and bbox["lng_min"] <= lng <= bbox["lng_max"]
    )


def in_center_box(lat, lng) -> bool:
    if lat is None or lng is None:
        return False
    box = RULES["locations"]["center_fallback_box"]
    return (
        box["lat_min"] <= lat <= box["lat_max"]
        and box["lng_min"] <= lng <= box["lng_max"]
    )


def domain_for(url: str | None) -> str:
    if not url:
        return ""
    value = url.strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return urlparse(value).netloc.lower().removeprefix("www.")


def load_confirmed_urls() -> dict[int, list[dict]]:
    path = ROOT / "config" / "sites_locaux.json"
    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    if isinstance(data, dict):
        for category, items in data.items():
            for item in items or []:
                item = dict(item)
                item["_category"] = category
                entries.append(item)
    elif isinstance(data, list):
        entries = data

    by_entity: dict[int, list[dict]] = defaultdict(list)
    for item in entries:
        entity_id = item.get("entity_id")
        url = (item.get("url") or "").strip()
        if not entity_id or not url or item.get("confirmed") is not True:
            continue
        dom = domain_for(url)
        if dom in set(RULES["urls"]["exclude_generic_domains"]):
            continue
        by_entity[int(entity_id)].append({
            "url": url,
            "domain": dom,
            "label": item.get("name") or dom,
            "source": "sites_locaux.confirmed",
        })
    return by_entity


# ── Ce qu'une fiche ne disait pas d'elle-même ────────────────────────────────
#
# ACTIVITÉ. Sur les 744 « entreprises » publiées à Lasalle, 377 étaient CESSÉES
# — plus de la moitié. Sur les 294 associations, 22 sont dissoutes au Journal
# officiel et 15 cessées au répertoire. Un annuaire qui les aligne sans le dire
# décrit une commune qui n'existe plus, et un habitant qui cherche un artisan
# tombe une fois sur deux sur une entreprise fermée depuis dix ans.
#
# Ce que la source dit vraiment : SIRENE porte `A` (active) ou `C`/`F` (cessée,
# fermée), le RNA porte `A` ou `D` (dissoute). Elle ne dit RIEN de plus — une
# association qui a cessé de se réunir sans déclarer sa dissolution reste `A`
# pour toujours. `actif` vaut donc None quand la source se tait : c'est une
# ignorance, pas une présomption d'activité.
FIN_DACTIVITE = {"C", "F", "D"}


def etat_activite(row_data: dict) -> tuple[bool | None, str | None]:
    """(actif, date de fin) d'après les registres. None quand ils se taisent."""
    statut = row_data.get("asso_status") or row_data.get("biz_status")
    fin = row_data.get("dissolution_date") or row_data.get("business_closing_date")
    if not statut:
        return (None, fin or None)
    return (statut.upper() not in FIN_DACTIVITE, fin or None)


# NATURE. « 744 entreprises » comptait 524 entreprises individuelles et 85
# sociétés dont l'activité déclarée est la gestion immobilière — des structures
# qui détiennent un patrimoine, pas des entreprises qui produisent. Les
# distinguer n'est pas un jugement : c'est le code NAF et la forme juridique,
# tels que l'INSEE les publie.
#
# La forme juridique 1000 (entreprise individuelle) l'emporte sur l'activité
# (décision du 24/11/2026) : c'est une personne physique, et la règle de
# publication (`public_entity`) la traite comme telle ; la ranger parmi les
# « sociétés de patrimoine » parce qu'elle loue (NAF 68) la présentait comme une
# société et sous-comptait les EI. Pour les autres formes, l'activité l'emporte :
# une SCI en NAF 41 (construction) construit, une SCI en NAF 68 détient.
NAF_IMMOBILIER = "68"


def forme_personne_physique(code) -> bool:
    """Catégorie juridique INSEE de niveau I « 1 » : personne physique. 1000
    (entrepreneur individuel) depuis le 01/07/2018 ; 1100 à 1900 (artisan-
    commerçant, profession libérale, exploitant agricole…), supprimées à cette
    date et encore portées par des unités anciennes (avis vigie-dpo, 25/11/2026)."""
    code = str(code or "").strip()
    return len(code) == 4 and code.isdigit() and code[0] == "1"


def est_ei(row_data: dict) -> bool:
    """Une entreprise individuelle : forme juridique de personne physique
    (`forme_personne_physique`), ou forme inconnue et présumée EI
    (`ei_presumees`, marqué `ei_presumee` par `etape_fiches`)."""
    return (forme_personne_physique(row_data.get("legal_form_code"))
            or bool(row_data.get("ei_presumee")))


def ei_presumees(conn, relations_ecartees) -> set[int]:
    """Les entreprises SANS forme juridique connue qui sont, presque à coup sûr,
    des entreprises individuelles : un lien les rattache à une personne
    physique qui porte le MÊME nom normalisé (« Sacha CALIMET » ↔ « SACHA
    CALIMET »), le repérage que `relation_pertinente` faisait déjà pour les
    liens (`relations.py`). Sans forme, elles échappaient à toute la règle des
    EI et à `check_ei_sans_fait` (brief `ei-flux-marches`, 24/11/2026).

    Une forme connue autre que 1000 l'emporte : une société au nom de son
    fondateur reste une société. Une forme vide ou absente, une ligne
    `businesses` manquante : forme inconnue.

    « Même nom » : les mots du plus court des deux noms (deux au moins) sont
    tous dans l'autre, dans n'importe quel ordre. SIRENE écrit « DUPONT JEAN »
    ou tous les prénoms (« Jeanne Marie DUPONT ») ; les deux bouts sont déjà
    reliés, l'écart d'écriture ne fait pas deux personnes (avis vigie-dpo et
    vigie-securite, 25/11/2026).

    Tout lien compte, de tout type et de toute confiance, sauf celui que
    l'atelier a rejeté (`relations_ecartees`, ids). Présumer une EI ne publie
    rien : cela retient la fiche jusqu'à un fait public. Les fiches sans forme
    viennent surtout du web, d'OSM ou des PV, dont les collecteurs écrivent
    `gérant`, `président`, `responsable` (`collectors/dirigeants_web.py`) ; ne
    lire que `dirigeant` les republiait sous le nom de la personne (avis
    vigie-securite du 25/11/2026, qui écarte la restriction demandée en
    relecture de code)."""
    return {b for b, _, forme in homonymes_relies(conn, relations_ecartees) if not forme}


HOMONYMES_COLONNES = ("r.id AS rid, b.id AS bid, b.name AS bnom, p.id AS pid, p.name AS pnom, "
                      "COALESCE(TRIM(bz.legal_form_code), '') AS forme")


def homonymes_relies(conn, relations_ecartees) -> list[tuple[int, int, str]]:
    """Les paires (entreprise, personne) reliées par un lien que l'atelier n'a
    pas rejeté et qui portent le même nom : les mots du plus court des deux noms
    (deux au moins) sont tous dans l'autre. Rend (entreprise, personne, forme
    juridique ou ""). Lue par `ei_presumees` (forme vide) et `exploitants_des_ei`
    (la personne qui exploite une EI écartée, avis vigie-dpo du 25/11/2026)."""
    from scripts.snapshot.textes import norm_nom
    ecartees = set(relations_ecartees)
    paires = []
    # Deux jointures sur l'ÉGALITÉ, une par sens du lien, réunies : `b.id IN
    # (r.from_id, r.to_id)` faisait balayer `entities` pour chaque lien, en temps
    # cubique sans `ANALYZE` (8,6 s pour 400 entreprises, 400 personnes, 1 200
    # liens ; tour 8 de la relecture). Les types diffèrent (`business`,
    # `person`) : un lien ne s'apparie que dans un sens, sans doublon.
    for r in rows(conn, f"""
            SELECT {HOMONYMES_COLONNES}
            FROM relations r
            JOIN entities p ON p.id = r.from_id AND p.type = 'person'
            JOIN entities b ON b.id = r.to_id AND b.type = 'business'
            LEFT JOIN businesses bz ON bz.entity_id = b.id
            UNION ALL
            SELECT {HOMONYMES_COLONNES}
            FROM relations r
            JOIN entities b ON b.id = r.from_id AND b.type = 'business'
            JOIN entities p ON p.id = r.to_id AND p.type = 'person'
            LEFT JOIN businesses bz ON bz.entity_id = b.id"""):
        if r["rid"] in ecartees:
            continue
        a, b = set(norm_nom(r["bnom"]).split()), set(norm_nom(r["pnom"]).split())
        court, long_ = sorted((a, b), key=len)
        if len(court) >= 2 and court <= long_:
            paires.append((r["bid"], r["pid"], r["forme"]))
    return paires


def exploitants_des_ei(conn, relations_ecartees, ei) -> set[int]:
    """Les personnes qui exploitent une des EI `ei` (`homonymes_relies`)."""
    ei = set(ei)
    return {p for b, p, _ in homonymes_relies(conn, relations_ecartees) if b in ei}


def nature_entreprise(row_data: dict) -> str:
    """`patrimoniale`, `individuelle` ou `societe`."""
    if est_ei(row_data):
        return "individuelle"
    if str(row_data.get("naf_code") or "").startswith(NAF_IMMOBILIER):
        return "patrimoniale"
    return "societe"


def parties_publiques(conn) -> set[int]:
    """Les entités qui sont une PARTIE PUBLIQUE : commune, intercommunalité,
    syndicat, État, autre collectivité. Deux champs, et rien d'autre :
    - `businesses.legal_form_code` en 7xxx (personnes morales de droit public) ou
      en 4xxx (personnes morales de droit public soumises au droit commercial :
      EPIC, ADEME…), catégories juridiques INSEE ;
    - l'entité de la commune et celle de l'intercommunalité de l'instance
      (`_commune_entity_id`, `_epci_entity_id`), qui n'ont pas toujours de forme
      juridique en base.
    - les fiches classées C3 (`entities.perimetre`, autorité supra-communale :
      préfecture, département, région, agences d'État, cf. `classer_perimetre`),
      qui n'ont souvent pas de forme juridique en base (décision de Julien du
      25/11/2026, avis vigie-dpo F) : seulement de type `service`, et de forme
      7xxx, 4xxx ou sans forme. Le classement C3 se fait par le nom
      (« Fédération départementale… ») : une association, un `business`, une
      personne n'en sont pas, même classés C3 (décision de Julien, tour 4).
    `entities.type = 'service'` ne suffit PAS : il couvre aussi les fiches FINESS
    (pharmacies, laboratoires) et OSM. Une association, une société, une
    personne physique n'en sont pas (décision de Julien du 24/11/2026)."""
    from scripts.snapshot.argent import _commune_entity_id, _epci_entity_id
    publics = {r["entity_id"] for r in rows(conn, """
        SELECT entity_id FROM businesses
        WHERE legal_form_code LIKE '7%' OR legal_form_code LIKE '4%'""")}
    publics.update(r["id"] for r in rows(conn, """
        SELECT e.id FROM entities e LEFT JOIN businesses b ON b.entity_id = e.id
        WHERE e.perimetre = 'C3' AND e.type = 'service'
          AND (COALESCE(TRIM(b.legal_form_code), '') = ''
               OR b.legal_form_code LIKE '7%' OR b.legal_form_code LIKE '4%')"""))
    publics.update(i for i in (_commune_entity_id(conn), _epci_entity_id(conn))
                   if i is not None)
    return publics


# Les actes DE LA COLLECTIVITÉ : délibérations et décisions de la commune ou de
# l'intercommunalité de l'instance. Liste fermée, par type d'événement : ni les
# rapports des chambres des comptes, ni SITADEL, ni le Géoportail, ni les
# résultats électoraux, ni BODACC, ni BOAMP/DECP (ceux-ci comptent comme des
# MARCHÉS, via `marches_publics`, dont l'acheteur est public).
TYPES_ACTES_DE_LA_COLLECTIVITE = (*TYPES_DELIBERES, *TYPES_DECISION)


# États d'un flux (`collectors/etat_flux.py`) qui n'attestent aucun versement :
# ils ne retiennent pas une EI. Ceux qu'écarte `estVersement`
# (`public/src/routes/finances/constat.js`) ; lus aussi par `check_ei_sans_fait`.
ETATS_SANS_VERSEMENT = ("demande", "annule")


# Rôles d'un lien acte ↔ entité qui ne retiennent PAS une EI : `demandeur`
# (registre d'urbanisme) et `bénéficiaire`, que les collecteurs posent pour tout
# flux lu dans un acte, même `probable`, rejeté ou privé (`cm_finances.py`,
# `relier_beneficiaires.py`) : ce lien rouvrirait ce que le filtre des flux ferme.
# Le fait, pour un bénéficiaire, est le FLUX publié, évalué à part.
ROLES_QUI_NE_RETIENNENT_PAS = ("demandeur", "bénéficiaire", "beneficiaire")


def ids_retenus_par_un_fait(conn, *, ids_actes_publics: set[int] | frozenset,
                            extremites_ok: set[int] | frozenset, redige,
                            actes_de_rubrique: set[int] | frozenset = frozenset(),
                            revue: dict | None = None,
                            actes_masques: set[int] | frozenset = frozenset(),
                            seulement_commune: bool = False) -> set[int]:
    """Les fiches qu'un fait PUBLIC désigne, ceux que `SECURITY.md` nomme :
    « marché public, paiement ou subvention publics, acte de la collectivité qui
    la cite ». C'est ce qui permet de nommer en ligne un entrepreneur individuel
    (cf. `public_entity`).

    Le fait doit sortir, au plus près des filtres de publication :
    - acte : un acte DE LA COLLECTIVITÉ (`TYPES_ACTES_DE_LA_COLLECTIVITE`), de
      source publique, et seulement ceux qu'`etape_actes` publie
      (`ids_actes_publics`, d'où son passage AVANT les fiches) ; pas le rôle
      `demandeur` ;
    - flux : il doit aller d'une PARTIE PUBLIQUE (`parties_publiques`) VERS
      l'EI : un flux d'une association ou d'une société vers l'EI ne retient
      pas, ni un flux sans émetteur, ni un flux que l'EI verse (loyer,
      redevance, prix d'achat). En plus : confiance publique, hors cession dont la
      commune n'est pas partie (fonds de commerce), hors flux lu dans une
      rubrique de décisions par délégation (`actes_de_rubrique`), et seulement
      si l'autre partie ne le fait pas écarter dans `etape_flux` (`extremites_ok`,
      cf. `extremites_du_flux_ok`). Le verdict d'atelier passe
      AVANT la confiance (`revue["flow"]`, comme `etape_flux`) : un flux rejeté
      ne retient pas, un flux validé retient. Un flux d'agrégat
      (`perimetre == 'agregat'`) ne retient pas : `write_entity_bundles` le retire ;
    - marché : le verdict d'atelier (`revue["marche"]`, sa confiance comprise)
      PUIS `CONFIANCE_MARCHES` (`confiance_marche_publique`, partagée avec
      `etape_finances`) : un marché validé retient, un marché rétrogradé non.
      BOAMP et DECP entrent ici : l'acheteur d'un marché est public par nature.
    `verify_snapshot` (`check_ei_sans_fait`) contrôle ensuite, sur ce qui est
    servi, que chaque EI publiée a bien son fait.

    `seulement_commune` : seuls comptent un flux VENU DE LA COMMUNE et un marché
    dont la commune est l'acheteur ; un acte ne compte pas. C'est ce qui garde
    le nom d'une EI classée C2 (`etape_fiches`, décision de Julien du 25/11).

    `ids_actes_publics`, `extremites_ok` et `redige` sont obligatoires : une
    valeur par défaut permissive (tous les actes, toutes les extrémités, aucun
    masque) faisait décider la retenue autrement que la sortie (leçon de #317)."""
    from scripts.snapshot.revue import appliquer_revue
    revue = revue or {}
    from scripts.snapshot.argent import _commune_entity_id
    publiques = sorted(RULES["confidence"]["public"])
    sources = sorted(RULES["events"]["public_sources"])
    ph_s = ",".join("?" * len(sources))
    ph_t = ",".join("?" * len(TYPES_ACTES_DE_LA_COLLECTIVITE))
    ph_r = ",".join("?" * len(ROLES_QUI_NE_RETIENNENT_PAS))
    commune = _commune_entity_id(conn)
    publics = parties_publiques(conn)
    ids: set[int] = set()

    def _autre_partie_ok(eid) -> bool:
        if seulement_commune and eid != commune:
            return False
        return eid is not None and eid in publics and eid in extremites_ok

    revue_flux = revue.get("flow", {})
    flux = []
    for f in rows(conn, """
            SELECT id, type, year, amount, from_id, to_id, event_id, confidence,
                   description, COALESCE(perimetre, 'detail') AS perimetre,
                   source, COALESCE(statut, 'realise') AS statut
            FROM financial_flows"""):
        f = appliquer_revue(f, revue_flux.get(f["id"]))
        if f is not None:
            flux.append(f)

    def _note(f) -> str:
        # La note de revue telle qu'elle SORTIRA (`etape_flux`, même expression) :
        # une note « un particulier » délie le flux, il ne retient donc pas
        # (audit du filtre du 24/11/2026, P1).
        from scripts.snapshot.textes import champ_publiable
        if not f.get("note_revue"):
            return ""
        return redige(champ_publiable(f["note_revue"], None, set())) or ""

    def _masque(f) -> bool:
        return (f["event_id"] in actes_masques
                or bool(_DESIGNE_UNE_PERSONNE.search(redige(f.get("description")) or ""))
                or bool(_DESIGNE_UNE_PERSONNE.search(_note(f))))

    def _sans_versement(f) -> bool:
        # Une subvention seulement DEMANDÉE, ou ANNULÉE, n'est pas un versement :
        # `SECURITY.md` dit « qu'une personne publique lui verse ». Même état que
        # la sortie (`etape_flux`), calculé après la revue, et mêmes valeurs que
        # celles que `estVersement` écarte côté site (audit du 25/11/2026, H3).
        return etat_du_flux(f.get("type"), f.get("source"), f.get("statut")) \
            in ETATS_SANS_VERSEMENT

    # Deux flux jumeaux (même année, montant, type, émetteur, bénéficiaire) dont
    # l'un est masqué : `dedupliquer_flux` garde le masqué, donc aucun des deux ne
    # retient l'EI (sinon fiche sans fait, snapshot refusé). De même si l'un est
    # une demande ou une annulation : `dedupliquer_flux` ne départage pas par
    # l'état et peut garder celui-là (avis vigie-securite sur dbe2db4, point 2) ;
    # mais seulement s'il ATTEINT la déduplication (`motif_hors_deduplication`,
    # celle d'`etape_flux`) : une demande `probable` n'y arrive pas et ne fait pas
    # perdre la fiche que son jumeau voté retient (relecture de #323).
    from scripts.snapshot.argent import motif_hors_deduplication
    jumeaux_masques = {(f.get("year"), f.get("amount"), f.get("type"), f["from_id"], f["to_id"])
                       for f in flux
                       if _masque(f) or (_sans_versement(f) and motif_hors_deduplication(
                           f, actes_de_rubrique, commune) is None)}
    for f in flux:
        if (f.get("year"), f.get("amount"), f.get("type"), f["from_id"], f["to_id"]) \
                in jumeaux_masques:
            continue
        if f["confidence"] not in publiques or f["perimetre"] == "agregat":
            continue
        if _sans_versement(f):
            continue
        if (str(f.get("type") or "").startswith("cession")
                and commune not in (f["from_id"], f["to_id"])):
            continue
        if f["event_id"] in actes_de_rubrique:
            continue
        # Un flux lu dans un acte au titre masqué, ou à la description masquée,
        # ne retient pas : par son montant, son année et la pièce, il redonnerait
        # le nom que le titre retire (avis vigie-securite du 24/11).
        if f["event_id"] in actes_masques:
            continue
        # La description passe par `redige` comme dans `etape_flux` : « Jeanne
        # EXEMPLE » devient « un particulier » si une personne non publique porte
        # ce nom, et le flux est alors délié.
        if (_DESIGNE_UNE_PERSONNE.search(redige(f.get("description")) or "")
                or _DESIGNE_UNE_PERSONNE.search(_note(f))):
            continue
        # Dans UN seul sens : de la partie publique VERS l'entreprise (paiement,
        # subvention, aide). Ce que l'EI verse (loyer, redevance, prix d'achat)
        # ne la retient pas (avis vigie-dpo du 24/11/2026).
        de, vers = f["from_id"], f["to_id"]
        if vers is not None and _autre_partie_ok(de):
            ids.add(vers)
    colonnes_mp = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    conf_mp = ", confidence" if "confidence" in colonnes_mp else ""
    revue_marches = revue.get("marche", {})
    from scripts.snapshot.argent import (acheteurs_par_siren, confiance_marche_publique,
                                         siren_titulaire)
    par_siren = acheteurs_par_siren(conn) if seulement_commune else {}
    for m in rows(conn, f"""
            SELECT id, titulaire_id, acheteur_id, acheteur_siren{conf_mp}
            FROM marches_publics"""):
        # L'acheteur rattaché par SIREN, comme `rattacher_acheteurs`, avant la revue.
        if m["acheteur_id"] is None:
            m["acheteur_id"] = par_siren.get(siren_titulaire(m["acheteur_siren"]))
        # Le verdict d'atelier, PUIS la confiance, comme `etape_finances` (H1, H2).
        m = appliquer_revue(m, revue_marches.get(m["id"]))
        if m is None or not confiance_marche_publique(m):
            continue
        if seulement_commune and (commune is None or m.get("acheteur_id") != commune):
            continue
        if m["titulaire_id"] is not None:  # le titulaire, pas l'acheteur public
            ids.add(m["titulaire_id"])
    if seulement_commune:
        return ids
    for l in rows(conn, f"""
            SELECT ee.event_id, ee.entity_id FROM event_entities ee
            JOIN events ev ON ev.id = ee.event_id
            WHERE ee.entity_id IS NOT NULL
              AND ev.source IN ({ph_s})
              AND ev.type IN ({ph_t})
              AND COALESCE(ee.role, '') NOT IN ({ph_r})""",
            (*sources, *TYPES_ACTES_DE_LA_COLLECTIVITE, *ROLES_QUI_NE_RETIENNENT_PAS)):
        if l["event_id"] in ids_actes_publics:
            ids.add(l["entity_id"])
    return ids


def public_entity(
    row_data: dict,
    urls: list[dict],
    public_person_ids: set[int],
    ids_conseil_communautaire: set[int] = frozenset(),
    ids_retenus: set[int] = frozenset(),
) -> tuple[dict | None, list[str]]:
    reasons: list[str] = []
    confidence = row_data.get("confidence")
    if confidence not in set(RULES["confidence"]["public"]):
        return None, ["private_confidence"]

    entity_type = row_data.get("type")
    if entity_type == "person" and row_data["id"] not in public_person_ids:
        return None, ["person_without_public_civic_role"]

    perimetre = row_data.get("perimetre")
    if not publiable_dans_perimetre(perimetre, entity_type,
                                    row_data["id"] in ids_conseil_communautaire):
        return None, [f"hors_fiche_perimetre_{perimetre}"]

    # Un entrepreneur individuel, c'est le NOM d'une personne, qu'il exerce ou
    # non. Il n'est nommé que si un fait public le désigne : un marché, de
    # l'argent public (flux), ou une mention dans un acte (Julien, 24/11/2026,
    # qui remplace la décision du 08/10 ; registre des traitements, point 9).
    # La fiche reste en base.
    # Une entreprise sans forme connue, présumée EI (`ei_presumees`), aussi.
    # Quel que soit le `type` : une fiche `service` ou `place` qui porte la forme
    # 1000 (SIRENE écrit `businesses` quel que soit le type) est une EI (audit
    # du filtre du 24/11/2026, P4).
    # Une PERSONNE n'y entre pas : son rôle civique la publie, et une ligne
    # `businesses` égarée sur sa fiche n'efface pas un élu (avis vigie-dpo).
    if (entity_type != "person" and est_ei(row_data)
            and row_data["id"] not in ids_retenus):
        return None, ["ei_sans_fait_public"]

    lat = row_data.get("lat")
    lng = row_data.get("lng")
    has_public_location = False
    location_quality = "missing"

    if entity_type == "person":
        location_quality = "hidden_person"
        lat = None
        lng = None
    elif row_data.get("lat") is None or row_data.get("lng") is None:
        location_quality = "missing"
    elif (entity_type != "person" and est_ei(row_data)
          and row_data.get("geocode_source") != "manual"):
        # RGPD : entrepreneur individuel — le siège est très souvent le domicile.
        # Coords retirées du public, sauf placement manuel délibéré à l'atelier
        # (structure avec un vrai local). cf. known-issues « vigilance RGPD ».
        location_quality = "hidden_ei_domicile"
        lat = None
        lng = None
    elif not in_commune_bbox(row_data["lat"], row_data["lng"]):
        location_quality = "outside_commune_bbox"
        lat = None
        lng = None
    elif in_center_box(row_data["lat"], row_data["lng"]):
        location_quality = "approx_center"
        has_public_location = entity_type in {"place", "service"}
        if not has_public_location:
            lat = None
            lng = None
    else:
        location_quality = "usable"
        has_public_location = True

    public = {
        "id": row_data["id"],
        "type": entity_type,
        "name": row_data["name"],
        "short_name": row_data.get("short_name"),
        # La commune de rattachement : la collecte couvre les 15 communes de
        # l'intercommunalité, et rien ne le disait sur une fiche — un lecteur
        # pouvait croire que tout était à Lasalle.
        "commune": row_data.get("commune"),
        # C1 = Lasalle, C2 = l'intercommunalité, C3 = autorité supra-communale,
        # lien = rattaché à un acteur suivi sans être sur le territoire.
        # L'UI doit le montrer : une fiche C2 ou `lien` ne se lit pas comme une
        # fiche lasalloise.
        "perimetre": row_data.get("perimetre"),
        "confidence": confidence,
        "location_quality": location_quality,
        "lat": lat,
        "lng": lng,
        "has_public_location": has_public_location,
        "urls": urls,
    }

    if entity_type in ("business", "association"):
        actif, fin = etat_activite(row_data)
        # `actif` à None = les registres ne disent rien. À ne jamais lire comme
        # « en activité » : c'est justement la case où se cachent les structures
        # dormantes qui n'ont jamais déclaré leur fin.
        public["actif"] = actif
        public["fin_activite"] = fin

    if entity_type == "business":
        public.update({
            "siren": row_data.get("siren"),
            "naf_code": row_data.get("naf_code"),
            "naf_label": row_data.get("naf_label"),
            "status": row_data.get("biz_status"),
            "creation_date": row_data.get("business_creation_date"),
            "nature": nature_entreprise(row_data),
        })
    elif entity_type == "association":
        public.update({
            "rna_id": row_data.get("rna_id"),
            "object": row_data.get("asso_object"),
            "status": row_data.get("asso_status"),
            "creation_date": row_data.get("asso_creation_date"),
        })
    elif entity_type == "place":
        public.update({
            "osm_category": row_data.get("osm_category"),
            "osm_value": row_data.get("osm_value"),
        })
        mh = monuments_historiques(row_data.get("osm_value"), row_data.get("place_tags"))
        if mh:
            public["monuments_historiques"] = mh
    elif entity_type == "service":
        public.update({
            "category": row_data.get("service_category"),
            "operator": row_data.get("operator"),
            "opening_hours": row_data.get("opening_hours"),
        })
    elif entity_type == "person":
        public.update({
            "firstname": row_data.get("firstname"),
            "lastname": row_data.get("lastname"),
        })
    # Une EI typée `service` ou `place` se dit aussi `individuelle` : c'est ce que
    # `check_ei_sans_fait` lit pour exiger son fait (audit du 24/11/2026, P4).
    if entity_type not in ("business", "person") and est_ei(row_data):
        public["nature"] = "individuelle"

    return public, reasons


def comptes_syndicats_par_entite(conn) -> dict[int, list[dict]]:
    """Les comptes d'un syndicat — ou de l'intercommunalité —, par exercice
    puis par budget. `de` dit lequel : la source `dgfip-gfp` est la balance
    des groupements à fiscalité propre, l'EPCI de l'instance.

    Écrits par `collectors/syndicats_comptes` depuis les balances DGFiP. Deux
    budgets d'un même syndicat (principal, annexe) restent SÉPARÉS : les
    additionner compterait deux fois ce que l'un reverse à l'autre. Une base
    antérieure au 24/09/2026 n'a pas la table — la fiche n'a alors pas d'encart,
    ce qui est exact : rien n'a été collecté.
    """
    try:
        rows = conn.execute(
            "SELECT entity_id, year, budget, libelle_budget, nomenclature, poste,"
            " montant, source FROM comptes_syndicats WHERE entity_id IS NOT NULL"
            " ORDER BY entity_id, year DESC, budget").fetchall()
    except sqlite3.OperationalError:
        return {}
    par: dict[int, dict[int, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    epci = {r["entity_id"] for r in rows if r["source"] == "dgfip-gfp"}
    for r in rows:
        bloc = par[r["entity_id"]][r["year"]].setdefault(r["budget"], {
            "libelle": r["libelle_budget"], "nomenclature": r["nomenclature"],
            "postes": {}})
        bloc["postes"][r["poste"]] = round(r["montant"] or 0)
    return {
        eid: [{"year": an, "de": "epci" if eid in epci else "syndicat",
               "budgets": list(budgets.values())}
              for an, budgets in sorted(annees.items(), reverse=True)]
        for eid, annees in par.items()
    }


def write_entity_bundles(out: Path, public_entities, public_relations,
                         public_events, public_links, public_flows,
                         marches_data, comptes_syndicats=None) -> int:
    """Un fichier par acteur : `entite/<id>.json`, tout pré-résolu.

    Avant ça, afficher une fiche imposait de télécharger `entities.json`
    (1,1 Mo) + `events.json` (1 Mo) + `event_links.json` (387 Ko) +
    `relations.json` + `flows.json` + `marches.json` — près de 3 Mo pour lire
    une association, et tout le filtrage fait dans le navigateur. Sur le réseau
    des Cévennes c'est disqualifiant. Chaque bundle fait quelques kilo-octets et
    contient exactement ce que la page affiche.

    Ces fichiers alimentent aussi le prérendu (`+page.server.js`) : le contenu
    part dans le HTML, donc les fiches sont enfin indexables et partageables.
    """
    names = {e["id"]: e["name"] for e in public_entities}
    events_by_id = {e["id"]: e for e in public_events}

    liens_par_entite: dict[int, list[dict]] = defaultdict(list)
    for link in public_links:
        event = events_by_id.get(link["event_id"])
        if event:
            liens_par_entite[link["entity_id"]].append({**event, "role": link["role"]})

    relations_par_entite: dict[int, list[dict]] = defaultdict(list)
    for rel in public_relations:
        for side, autre_id in (("from_id", rel["to_id"]), ("to_id", rel["from_id"])):
            eid = rel[side]
            relations_par_entite[eid].append({
                **rel,
                "autre_id": autre_id,
                "autre": names.get(autre_id),
            })

    flows_par_entite: dict[int, list[dict]] = defaultdict(list)
    for flow in public_flows:
        if flow.get("perimetre") == "agregat":
            continue
        for eid in {flow.get("from_id"), flow.get("to_id")} - {None}:
            flows_par_entite[eid].append(flow)

    marches_par_entite: dict[int, list[dict]] = defaultdict(list)
    for marche in marches_data:
        for eid in {marche.get("titulaire_id"), marche.get("acheteur_id")} - {None}:
            marches_par_entite[eid].append(marche)

    dest = out / "entite"
    dest.mkdir(parents=True, exist_ok=True)

    # PURGE AVANT ÉCRITURE. Sans elle, une entité retirée de la publication
    # gardait sa fiche ici, et la synchro miroir la recopiait fidèlement vers le
    # site : le 19/08/2026, deux sites en ligne servaient des fiches périmées en
    # `/data/entite/<id>.json` — 1 229 sur l'un dont 80 personnes physiques,
    # 9 697 sur l'autre dont 156 — alors que le filtre les avait écartées.
    # Personne ne le voyait, parce que la page HTML de ces entités rendait bien
    # 404 : seul le fichier de données restait accessible. Écrire par-dessus ne
    # suffit pas, il faut retirer ce qui ne doit plus sortir.
    attendus = {f"{e['id']}.json" for e in public_entities}
    perimees = [f for f in dest.glob("*.json") if f.name not in attendus]
    for f in perimees:
        f.unlink()
    if perimees:
        print(f"   {len(perimees)} fiche(s) périmée(s) retirée(s) de {dest.name}/")

    for entity in public_entities:
        eid = entity["id"]
        write_json_compact(dest / f"{eid}.json", {
            "entity": entity,
            "relations": relations_par_entite.get(eid, []),
            "liens": sorted(liens_par_entite.get(eid, []),
                            key=lambda e: (e.get("date") or ""), reverse=True),
            "flows": sorted(flows_par_entite.get(eid, []),
                            key=lambda f: (f.get("year") or 0), reverse=True),
            "marches": marches_par_entite.get(eid, []),
            **({"comptes_syndicat": (comptes_syndicats or {})[eid]}
               if eid in (comptes_syndicats or {}) else {}),
        })
    return len(public_entities)


def extremites_du_flux_ok(entity_rows, revue_entites, public_person_ids,
                          ids_conseil_communautaire, urls) -> set[int]:
    """Les fiches qui auront une fiche PUBLIÉE, donc un flux LIÉ dans `etape_flux`.

    Rejoue `public_entity` (une seule règle) en tenant toute EI pour retenue :
    qu'elle le soit se décide après, et c'est elle qu'on est en train de juger.

    Seul le payeur qui garde son lien retient une EI. Une partie publique
    écartée pour le périmètre ou rejetée à l'atelier fait sortir le flux DÉLIÉ
    (`from_id` nul) : le contrôleur, qui ne compte qu'un payeur public nommé par
    son identifiant, refusait alors le snapshot entier (avis vigie-securite du
    25/11/2026, point 1). Elle ne retient donc plus ; son flux sort quand même,
    délié des deux côtés (`etape_flux`)."""
    ei = {r["id"] for r in entity_rows if r["type"] != "person" and est_ei(r)}
    ok: set[int] = set()
    for e in entity_rows:
        verdict = revue_entites.get(e["id"])
        if verdict and ecarte(verdict["statut"]):
            continue
        item, _ = public_entity(e, urls.get(e["id"], []), public_person_ids,
                                ids_conseil_communautaire, ei)
        if item is not None:
            ok.add(e["id"])
    return ok


def etape_fiches(conn, revue, public_person_ids, ids_conseil_communautaire,
                 exclusions, public_events,
                 actes_de_rubrique=frozenset(), *, redige) -> dict:
    confirmed_urls = load_confirmed_urls()
    entity_rows = rows(conn, """
        SELECT
            e.id, e.type, e.name, e.short_name, e.lat, e.lng, e.address, e.confidence,
            e.geocode_source, e.commune, e.perimetre,
            p.firstname, p.lastname, p.birth_year,
            b.siren, b.naf_code, b.naf_label, b.status AS biz_status,
            b.legal_form_code,
            b.creation_date AS business_creation_date,
            b.closing_date AS business_closing_date,
            a.rna_id, a.object AS asso_object,
            a.status AS asso_status, a.dissolution_date,
            a.creation_date AS asso_creation_date,
            pl.osm_category, pl.osm_value, pl.tags AS place_tags,
            s.category AS service_category, s.operator, s.opening_hours
        FROM entities e
        LEFT JOIN persons p ON p.entity_id = e.id
        LEFT JOIN businesses b ON b.entity_id = e.id
        LEFT JOIN associations a ON a.entity_id = e.id
        LEFT JOIN places pl ON pl.entity_id = e.id
        LEFT JOIN services s ON s.entity_id = e.id
        ORDER BY e.name
    """)

    # Entreprises individuelles : le lien « dirigeant » y est tautologique.
    # Forme 1000, ou forme inconnue et présumée EI : marquée ici, sur la ligne,
    # pour que `public_entity`, `nature_entreprise` et `extremites_du_flux_ok`
    # n'aient qu'une seule lecture (`est_ei`).
    from scripts.snapshot.revue import ecartes
    presumees = ei_presumees(conn, ecartes(revue, "relation"))
    for r in entity_rows:
        if r["id"] in presumees:
            r["ei_presumee"] = True
    ei_ids = {r["id"] for r in entity_rows if r["type"] != "person" and est_ei(r)}
    retenue = dict(
        ids_actes_publics={e["id"] for e in public_events
                           if not _DESIGNE_UNE_PERSONNE.search(e.get("title") or "")},
        actes_de_rubrique=actes_de_rubrique,
        extremites_ok=extremites_du_flux_ok(entity_rows, revue.get("entity", {}),
                                            public_person_ids, ids_conseil_communautaire,
                                            confirmed_urls),
        revue=revue,
        actes_masques={e["id"] for e in public_events
                       if _DESIGNE_UNE_PERSONNE.search(e.get("title") or "")},
        redige=redige)
    ids_retenus = ids_retenus_par_un_fait(conn, **retenue)
    # Les EI que la COMMUNE désigne (un marché dont elle est l'acheteur, un
    # versement qu'elle fait) : classées C2, elles n'ont pas de fiche, mais leur
    # nom reste sur ce marché et ce flux (décision de Julien du 25/11/2026, Q2 de
    # l'audit). Calculées par la même retenue, restreinte à la commune.
    designees_par_la_commune = ids_retenus_par_un_fait(conn, **retenue,
                                                       seulement_commune=True)
    public_entities: list[dict] = []
    entity_exclusions: list[dict] = []
    # Les entités MORALES écartées pour le seul périmètre, ou par un verdict
    # de l'atelier : leurs flux d'argent public se publient déliés (cf.
    # `statut_extremites`). Le nom de l'ensemble date du premier cas.
    ecartees_du_perimetre: set[int] = set()
    location_quality = Counter()
    revue_fiches = revue.get("entity", {})
    for entity in entity_rows:
        # Le verdict de l'atelier passe AVANT les règles : écarter une fiche
        # n'a pas à attendre que le filtre soit d'accord. Il ne peut en
        # revanche rien OUVRIR — une fiche que les règles tiennent privée le
        # reste, même `retenu` (cf. `collectors/verdict.py`).
        verdict = revue_fiches.get(entity["id"])
        if verdict and ecarte(verdict["statut"]):
            exclusions["entities"]["rejete_en_atelier"] += 1
            # Écarter une ORGANISATION retire sa fiche, pas l'argent public
            # qu'elle a reçu ou versé : ses flux sortaient des totaux (−264 009 €
            # sur la base fictive, audit du 15/11/2026). Ils se publient déliés,
            # le nom gardé, comme ceux d'une fiche hors périmètre (Julien,
            # 15/11). Une PERSONNE écartée n'est pas concernée : ses flux restent
            # retirés, comme ceux de toute personne non publiable
            # (`private_person_endpoint`, `argent.py`).
            if entity["type"] != "person":
                ecartees_du_perimetre.add(entity["id"])
            entity_exclusions.append({
                "id": entity["id"],
                "type": entity["type"],
                "name": entity["name"],
                "confidence": entity["confidence"],
                "reasons": ["rejete_en_atelier"],
            })
            continue
        item, reasons = public_entity(
            entity,
            confirmed_urls.get(entity["id"], []),
            public_person_ids,
            ids_conseil_communautaire,
            ids_retenus,
        )
        if item is None:
            for reason in reasons:
                exclusions["entities"][reason] += 1
            # Une EI écartée faute de fait public (`ei_sans_fait_public`) y entre
            # aussi : son flux d'argent public se publie DÉLIÉ (montant et partie
            # publique), sans son nom (`etape_flux`), au lieu de disparaître des
            # totaux. Les autres motifs (confiance privée, personne sans rôle)
            # écartent le flux, comme avant.
            if (entity["type"] != "person"
                    and all(r.startswith("hors_fiche_perimetre_")
                            or r == "ei_sans_fait_public" for r in reasons)):
                ecartees_du_perimetre.add(entity["id"])
            entity_exclusions.append({
                "id": entity["id"],
                "type": entity["type"],
                "name": entity["name"],
                "confidence": entity["confidence"],
                "reasons": reasons,
            })
            continue
        public_entities.append(item)
        location_quality[item["location_quality"]] += 1

    public_ids = {e["id"] for e in public_entities}
    # Une EI rejetée à l'atelier n'y entre pas : elle reste effacée partout.
    ei_nommees = {x["id"] for x in entity_exclusions
                  if x["id"] in ei_ids and x["id"] in designees_par_la_commune
                  and x["reasons"] == ["hors_fiche_perimetre_C2"]}

    return {
        "entity_rows": entity_rows,
        "ei_ids": ei_ids,
        "ei_nommees": ei_nommees,
        "public_entities": public_entities,
        "entity_exclusions": entity_exclusions,
        "ecartees_du_perimetre": ecartees_du_perimetre,
        "location_quality": location_quality,
        "public_ids": public_ids,
    }


# Le relevé que `verify_snapshot` lit pour contrôler les EI (`check_ei_sans_fait`).
# HORS du snapshot : `audits/`, sous la racine du moteur, comme le rapport de
# revue. `VIGIE_AUDITS` le déplace (essais).
CONTROLE_EI = "controle_ei.json"
# Un relevé par snapshot, sous l'EMPREINTE de son contenu (`empreinte_snapshot`) :
# deux constructions dans la même seconde ne s'écrasent plus, un aperçu publié
# après d'autres constructions garde le sien. Rétention : les relevés de moins
# de `RETENTION_RELEVES_JOURS` jours, et au moins les `RELEVES_EI_MINIMUM` plus
# récents (relecture de code du 25/11/2026).
RETENTION_RELEVES_JOURS = 30
RELEVES_EI_MINIMUM = 20
#: Les fichiers dont le contenu fait l'empreinte : ceux que le contrôle des EI lit
#: et qu'aucune étape ne réécrit après `controle_ei`. Copie dans le contrôleur.
FICHIERS_EMPREINTE = ("entities.json", "flows.json", "marches.json")


def cle_de_nom(nom) -> str:
    """L'empreinte d'un nom, pour le relevé des EI écartées : SHA-256 du nom
    sans accents, en majuscules, tirets en espaces, blancs compactés. Empreinte
    non salée : une pseudonymisation (donnée personnelle), pas un anonymat ; le
    relevé n'a simplement aucun nom en clair. Copie dans `verify_snapshot.py`,
    gardée identique par un essai."""
    import hashlib
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nom or "")).encode("ascii", "ignore").decode()
    s = " ".join(s.upper().replace("-", " ").split())
    return hashlib.sha256(s.encode()).hexdigest() if s else ""


#: Un mot d'un texte libre : lettres, chiffres, `_`, apostrophes internes ; un
#: point final fait d'une lettre seule une initiale (« J. »). Copie dans le
#: contrôleur.
MOT_RE = r"\w+(?:['’]\w+)*\.?"


def mots_normalises(texte) -> list[tuple[int, int, str, bool]]:
    """Les mots d'un texte : (début, fin, mot sans accents en majuscules,
    initiale ?). Un point final ne fait pas partie d'un mot plein. Copie dans
    `verify_snapshot.py`."""
    import re
    import unicodedata
    sortie = []
    for m in re.finditer(MOT_RE, str(texte or "")):
        brut = m.group(0)
        mot = unicodedata.normalize("NFKD", brut.rstrip(".").replace("’", "'"))
        mot = mot.encode("ascii", "ignore").decode().upper()
        if not mot:
            continue
        initiale = brut.endswith(".") and len(mot) == 1
        fin = m.end() if initiale or not brut.endswith(".") else m.end() - 1
        sortie.append((m.start(), fin, mot, initiale))
    return sortie


#: Les mots qui ne désignent personne : transparents dans une suite de mots
#: (« Jean de La Fontaine »), jamais cherchés seuls. Copie dans le contrôleur.
MOTS_VIDES = frozenset({"A", "AU", "AUX", "D", "DE", "DES", "DU", "EN", "ET", "L", "LA",
                        "LE", "LES", "PAR", "POUR", "SOUS", "SUR", "UN", "UNE",
                        # Les mots de la mention qui remplace un nom.
                        "ENTREPRISE", "INDIVIDUELLE"})
#: Ce qui peut séparer deux mots d'un même nom. Copie dans le contrôleur.
SEPARATEUR_RE = r"[\s\-‐–]*"


def jetons_lies(texte) -> list[tuple[int, int, str, bool, bool]]:
    """Les mots d'un texte qui peuvent appartenir à un nom : (début, fin, mot,
    initiale ?, lié au précédent ?). Les `MOTS_VIDES` sont retirés mais
    transparents : deux mots séparés par « de la » restent liés. « Lié » : entre
    ce mot et le précédent retenu, rien que des séparateurs et des mots vides.
    Copie dans `verify_snapshot.py`."""
    import re
    texte = str(texte or "")
    sep = re.compile(SEPARATEUR_RE)
    sortie, fin_prec, lie_possible = [], None, False
    for d, f, mot, initiale in mots_normalises(texte):
        lie = lie_possible and bool(sep.fullmatch(texte[fin_prec:d]))
        if mot in MOTS_VIDES and not initiale:
            lie_possible, fin_prec = lie, f
            continue
        sortie.append((d, f, mot, initiale, lie))
        fin_prec, lie_possible = f, True
    return sortie


def mots_du_nom(nom) -> frozenset:
    """Les mots pleins d'un nom, normalisés, sans les `MOTS_VIDES`."""
    return frozenset(m for _, _, m, initiale, _ in jetons_lies(nom) if not initiale)


def suite_du_nom(nom) -> tuple:
    """Les mots pleins d'un nom, dans l'ordre (variantes DECP : suite exacte)."""
    return tuple(m for _, _, m, initiale, _ in jetons_lies(nom) if not initiale)


def empreintes_de_mots(nom) -> set[str]:
    """Les empreintes (SHA-256) de chaque groupe de 2 à 4 mots du nom, rangés :
    ce que le contrôleur cherche dans le texte libre, quel que soit l'ordre et
    même si le texte omet un prénom. Copie dans le contrôleur."""
    import hashlib
    from itertools import combinations
    mots = sorted(mots_du_nom(nom))
    return {hashlib.sha256(" ".join(c).encode()).hexdigest()
            for k in range(2, min(4, len(mots)) + 1) for c in combinations(mots, k)}


def _sha_mots(mots) -> str:
    """Empreinte d'un ensemble de mots rangés (`exploitants_ecartes`)."""
    import hashlib
    return hashlib.sha256(" ".join(sorted(mots)).encode()).hexdigest()


def empreinte_de_suite(nom) -> str:
    """L'empreinte de la suite exacte des mots d'un nom (2 à 6 mots), ou ""."""
    import hashlib
    suite = suite_du_nom(nom)
    return (hashlib.sha256(" ".join(suite).encode()).hexdigest()
            if 2 <= len(suite) <= 6 else "")


def empreinte_snapshot(out: Path) -> str:
    """SHA-256 du contenu de `FICHIERS_EMPREINTE` (nom, octet nul, octets ; un
    fichier absent compte vide). Copie dans `verify_snapshot.py`."""
    import hashlib
    h = hashlib.sha256()
    for nom in FICHIERS_EMPREINTE:
        fp = Path(out) / nom
        h.update(nom.encode() + b"\0" + (fp.read_bytes() if fp.is_file() else b"") + b"\0")
    return h.hexdigest()


def noms_des_ei_ecartees(conn, entity_rows, ecartees: set[int],
                         public_ids) -> tuple[set[str], set[str]]:
    """Les noms qui désignent une EI non publiée : (noms de sa FICHE, VARIANTES).
    Les variantes sont le `titulaire_nom` des marchés qui la désignent par
    identifiant ou par SIREN : souvent une enseigne (« DUPONT ESPACES VERTS »),
    elles ne se cherchent dans le texte libre qu'en suite exacte, jamais par
    groupes de mots (avis vigie-securite du 25/11/2026, b). Sauf le nom d'une
    fiche PUBLIÉE qui porte le même : il la désigne, elle (relecture de code).
    Lus par `etape_flux`, `etape_finances` (effacement, masque du texte libre)
    et `etape_controle_ei` (relevé) : une seule liste partout."""
    from scripts.snapshot.argent import siren_titulaire
    noms = {r[c] for r in entity_rows if r["id"] in ecartees
            for c in ("name", "short_name") if r.get(c)}
    sirens = {siren_titulaire(r.get("siren")) for r in entity_rows
              if r["id"] in ecartees} - {""}
    colonnes = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    siren_mp = "titulaire_siren" if "titulaire_siren" in colonnes else "NULL AS titulaire_siren"
    for m in rows(conn, f"SELECT titulaire_id, {siren_mp}, titulaire_nom FROM marches_publics"):
        if m["titulaire_nom"] and (m["titulaire_id"] in ecartees
                                   or siren_titulaire(m["titulaire_siren"]) in sirens):
            noms.add(m["titulaire_nom"])
    # Une fiche PERSONNE n'épargne jamais le nom : une personne n'est titulaire
    # ou bénéficiaire qu'à travers une EI (avis vigie-dpo du 25/11/2026).
    publies = {cle_de_nom(r[c]) for r in rows(
                   conn, "SELECT id, name, short_name FROM entities WHERE type != 'person'")
               if r["id"] in public_ids for c in ("name", "short_name") if r[c]}
    fiches = {r[c] for r in entity_rows if r["id"] in ecartees
              for c in ("name", "short_name") if r.get(c)}
    garde = lambda ens: {n for n in ens if cle_de_nom(n) not in publies}
    return garde(fiches), garde(noms - fiches)


def mots_des_exploitants(conn, relations_ecartees, ei_sans_fiche, public_ids) -> set:
    """Les ensembles de mots des noms des fiches PERSONNE publiées qui exploitent
    une EI sans fiche (`exploitants_des_ei`) : l'élu « Jean DUPONT » n'épargne
    pas le nom de son EI écartée dans le texte libre (avis vigie-dpo du
    25/11/2026). Le relevé en porte les empreintes pour le contrôleur."""
    exploitants = exploitants_des_ei(conn, relations_ecartees, ei_sans_fiche) & set(public_ids)
    return {m for r in rows(conn, "SELECT id, name, short_name FROM entities")
            if r["id"] in exploitants for c in ("name", "short_name") if r[c]
            for m in [mots_du_nom(r[c])] if len(m) >= 2}


def mots_des_fiches_publiees(conn, public_ids, *, mots_exclus) -> tuple:
    """Les ensembles de mots des noms des fiches publiées : une suite de mots
    qui tient dans l'un d'eux n'est pas masquée (homonyme publié, avis
    vigie-securite du 25/11/2026, b). Sauf `mots_exclus` (`mots_des_exploitants`).
    Le contrôleur les lit dans `entities.json`, moins les mêmes exclus."""
    return tuple({m for r in rows(conn, "SELECT id, name, short_name FROM entities")
                  if r["id"] in public_ids for c in ("name", "short_name") if r[c]
                  for m in [mots_du_nom(r[c])] if len(m) >= 2} - set(mots_exclus))


def ei_a_taire(ei_ids, public_ids, ei_nommees) -> set[int]:
    """Les EI dont le nom ne sort pas : sans fiche, hors celles que la commune
    désigne (`ei_nommees`, C2)."""
    return set(ei_ids) - set(public_ids) - set(ei_nommees)


def etape_controle_ei(conn, out, revue, stats, entity_rows, ei_ids, public_ids,
                      ei_nommees) -> None:
    """Écrit, dans `audits/controle_ei.json`, le relevé de CE snapshot, sous
    l'empreinte de son contenu (`empreinte_snapshot`) : ce que le contrôleur ne
    peut pas lire dans le snapshot.
    - `parties_publiques` : les identifiants de `parties_publiques` (aucune
      donnée personnelle), pour qu'un flux ne compte comme fait d'une EI que s'il
      vient de l'une d'elles ;
    - `ei_ecartees` : les EMPREINTES (`cle_de_nom`) des noms des EI qui ne
      doivent pas sortir (`noms_des_ei_ecartees`, `ei_a_taire`), pour qu'aucun
      `titulaire_nom`, `from_name` ou `to_name` publié n'en désigne une ;
    - `construit_le` (heure réelle) et `generated_at`, pour la rétention et pour
      que le contrôleur dise de quand date ce qu'il a."""
    import os
    from datetime import datetime, timedelta
    import tempfile

    from collectors import verrou as verrou_fichier
    from scripts.snapshot.revue import ecartes
    fiches_ecartees, variantes = noms_des_ei_ecartees(
        conn, entity_rows, ei_a_taire(ei_ids, public_ids, ei_nommees), public_ids)
    fiches_nommees, variantes_nommees = noms_des_ei_ecartees(
        conn, entity_rows, set(ei_nommees), public_ids)
    noms = fiches_ecartees | variantes
    noms_nommees = fiches_nommees | variantes_nommees
    dossier = Path(os.environ.get("VIGIE_AUDITS") or ROOT / "audits")
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / CONTROLE_EI
    maintenant = datetime.now()
    releve = {
        "construit_le": maintenant.isoformat(timespec="seconds"),
        "generated_at": stats.get("generated_at"),
        "parties_publiques": sorted(parties_publiques(conn)),
        "ei_ecartees": sorted({c for c in map(cle_de_nom, noms) if c}),
        # Les EI C2 que la commune désigne : leur nom n'est admis que sur un
        # marché de la commune et un flux qu'elle verse (avis vigie-securite A2).
        "ei_nommees": sorted({c for c in map(cle_de_nom, noms_nommees) if c}),
        # Le texte libre (avis vigie-securite A3, b) : groupes de mots des noms
        # de FICHE, suites exactes des variantes.
        "mots_ei": sorted(set().union(*map(empreintes_de_mots, fiches_ecartees))),
        "mots_ei_nommees": sorted(set().union(*map(empreintes_de_mots, fiches_nommees))),
        "suites_ei": sorted(set(map(empreinte_de_suite, variantes)) - {""}),
        "suites_ei_nommees": sorted(set(map(empreinte_de_suite, variantes_nommees)) - {""}),
        # Les fiches personne qui n'épargnent pas le texte libre (exploitants
        # d'une EI sans fiche) : empreintes de leurs mots rangés (avis vigie-dpo).
        "exploitants_ecartes": sorted(
            _sha_mots(m) for m in mots_des_exploitants(
                conn, ecartes(revue, "relation"), set(ei_ids) - set(public_ids), public_ids)),
    }
    # La rétention couvre au moins la durée de vie d'un aperçu (`publication.py`).
    jours = max(RETENTION_RELEVES_JOURS, int(os.environ.get("VIGIE_APERCU_JOURS") or 7) + 1)
    limite = (maintenant - timedelta(days=jours)).isoformat(timespec="seconds")
    # Lecture, ajout, écriture sous verrou, remplacement atomique : une collecte
    # et un aperçu simultanés ne perdent pas d'entrée et le contrôleur ne lit
    # jamais un fichier à moitié écrit (avis vigie-securite, mineur C).
    # `collectors.verrou` : `fcntl` n'existe pas sous Windows (`test_verrou.py`).
    with open(dossier / (CONTROLE_EI + ".verrou"), "a+") as verrou:
        verrou_fichier.prendre(verrou)
        # Fichier absent : premier relevé. Illisible (JSON invalide) : on repart
        # d'un relevé neuf, et on le dit. Toute AUTRE erreur (permissions…)
        # arrête la construction : effacer les relevés sans bruit ferait refuser
        # les aperçus en attente pour une mauvaise raison (tour 5).
        try:
            releves = json.loads(chemin.read_text(encoding="utf-8")).get("releves") or {}
            if not isinstance(releves, dict):
                raise ValueError("« releves » n'est pas un objet")
        except FileNotFoundError:
            releves = {}
        except (ValueError, AttributeError) as e:
            import sys
            print(f"⚠ {chemin} illisible ({e}) : relevés précédents abandonnés",
                  file=sys.stderr)
            releves = {}
        releves[empreinte_snapshot(out)] = releve
        recents = sorted(releves.items(), key=lambda kv: str(kv[1].get("construit_le") or ""),
                         reverse=True)
        gardes = {k: v for i, (k, v) in enumerate(recents)
                  if i < RELEVES_EI_MINIMUM or str(v.get("construit_le") or "") >= limite}
        with tempfile.NamedTemporaryFile("w", dir=dossier, delete=False, encoding="utf-8",
                                         suffix=".tmp") as tmp:
            json.dump({"releves": gardes}, tmp, ensure_ascii=False, indent=1)
        # 0644 comme le reste d'`audits/` (le fichier temporaire naît en 0600).
        # En cas d'échec, le fichier temporaire ne reste pas dans `audits/`.
        try:
            os.chmod(tmp.name, 0o644)
            os.replace(tmp.name, chemin)
        except OSError:
            os.unlink(tmp.name)
            raise
        verrou_fichier.rendre(verrou)


def etape_couches(public_entities) -> dict:
    """Les points de la carte, une couche par type d'acteur localisé.

    Chaque point emporte une COPIE des propriétés de sa fiche, prise ici :
    ce qu'une étape ajoute aux fiches plus tard (`citations`) n'entre pas dans
    les couches. C'est la sortie telle qu'elle a toujours été ; l'ordre du
    registre la garde.
    """
    public_layers = {
        "businesses": [],
        "associations": [],
        "places": [],
        "services": [],
    }
    for entity in public_entities:
        if not entity.get("has_public_location"):
            continue
        layer_key = {
            "business": "businesses",
            "association": "associations",
            "place": "places",
            "service": "services",
        }.get(entity["type"])
        if not layer_key:
            continue
        public_layers[layer_key].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [entity["lng"], entity["lat"]]},
            "properties": {k: v for k, v in entity.items() if k not in {"lat", "lng"}},
        })

    return {"public_layers": public_layers}


def etape_citations(public_events, public_links, public_relations, public_flows,
                    marches_data, public_entities, stats) -> None:
    """Ce que les actes, liens, flux et marchés publiés disent de chaque fiche :
    combien de fois elle y est citée, et l'année de sa dernière trace. Écrit
    dans les fiches en place, et compté dans `stats`."""
    # ── Citations : l'acteur est-il nommé dans un acte public ? ───────────
    # « Cité » = nommé dans une décision publique : délibération, conseil,
    # marché, autorisation d'urbanisme, flux financier, mandat. Les ~1 800
    # entreprises importées de SIRENE n'y figurent pas et noyaient la page
    # publique (2 700 cartes à parcourir à l'œil). Tout le répertoire reste
    # publié, mais le compteur permet de mettre en avant ce qui est documenté.
    #
    # Les annonces BODACC (dépôts de comptes, immatriculations…) sont
    # exclues : purement déclaratives, elles font remonter n'importe quelle
    # société ayant déposé ses comptes devant les associations subventionnées.
    events_by_id_all = {e["id"]: e for e in public_events}
    citations = Counter()
    for lien in public_links:
        ev = events_by_id_all.get(lien["event_id"])
        if not ev or str(ev.get("type", "")).startswith("bodacc"):
            continue
        citations[lien["entity_id"]] += 1
    for source, paires in (
        (public_relations, ("from_id", "to_id")),
        (public_flows, ("from_id", "to_id")),
        (marches_data, ("acheteur_id", "titulaire_id")),
    ):
        for row in source:
            for eid in {row.get(k) for k in paires} - {None}:
                citations[eid] += 1
    # ── Dernière trace publique ──────────────────────────────────────────
    #
    # 28 associations et 92 entreprises de Lasalle ont des registres MUETS :
    # ni cessées ni dissoutes, mais rien ne dit non plus qu'elles vivent.
    # Une association qui a cessé de se réunir sans déclarer sa dissolution
    # reste « A » au Journal officiel pour toujours — le registre ne
    # l'apprend jamais.
    #
    # Ce qu'on peut dire sans rien inventer : LA DERNIÈRE FOIS QU'UNE SOURCE
    # PUBLIQUE L'A NOMMÉE. Ce n'est pas un verdict de dormance, c'est un
    # fait daté, et il laisse le lecteur conclure — « dernière trace : 2014 »
    # se lit tout seul.
    #
    # À la différence des citations, les annonces BODACC comptent ici : une
    # radiation de 2019 est une trace, et c'est même la plus parlante.
    #
    # L'ANNÉE, et pas la date : un flux financier n'a que son millésime.
    # Lui donner un jour le ferait passer pour plus précis qu'il n'est, et
    # un acteur documenté par un seul flux paraîtrait plus récent qu'un
    # acteur cité dans une délibération du même exercice.
    traces: dict[int, int] = {}
    _annee = _annee_de_trace

    def _tracer(eid, an):
        if eid and an and an > traces.get(eid, 0):
            traces[eid] = an

    for lien in public_links:
        ev = events_by_id_all.get(lien["event_id"])
        if ev:
            _tracer(lien["entity_id"], _annee(ev.get("date")))
    for rel in public_relations:
        for champ in ("since", "until"):
            for eid in (rel.get("from_id"), rel.get("to_id")):
                _tracer(eid, _annee(rel.get(champ)))
    for flux in public_flows:
        for eid in (flux.get("from_id"), flux.get("to_id")):
            _tracer(eid, _annee(flux.get("year")))
    for marche in marches_data:
        for eid in (marche.get("acheteur_id"), marche.get("titulaire_id")):
            _tracer(eid, _annee(marche.get("date_notif")))

    for e in public_entities:
        e["citations"] = citations.get(e["id"], 0)
        e["derniere_trace"] = traces.get(e["id"])
    stats["entities_cited"] = sum(1 for e in public_entities if e["citations"])
    # Ce que les registres taisent, et depuis quand la source publique
    # s'est tue. Publié pour que la lacune se mesure au lieu de se deviner.
    muettes = [e for e in public_entities
               if e["type"] in ("business", "association") and e.get("actif") is None]
    stats["entities_registres_muets"] = {
        "total": len(muettes),
        "sans_aucune_trace": sum(1 for e in muettes if not e.get("derniere_trace")),
        "par_derniere_trace": dict(sorted(Counter(
            e.get("derniere_trace") for e in muettes if e.get("derniere_trace")
        ).items(), reverse=True)),
    }


def etape_fiches_acteurs(conn, out, public_entities, public_relations, public_events,
                         public_links, public_flows, marches_data) -> dict:
    """Un fichier par fiche publiée, tout pré-résolu ; les fiches retirées sortent."""
    bundles = write_entity_bundles(out, public_entities, public_relations,
                                   public_events, public_links, public_flows,
                                   marches_data,
                                   comptes_syndicats_par_entite(conn))
    return {"bundles": bundles}
