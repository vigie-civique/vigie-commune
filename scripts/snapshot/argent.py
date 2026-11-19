"""L'argent public : les flux financiers publiés, et dans quel sens ils vont.

L'étape `flux` lit les flux en base, applique le verdict de l'atelier puis les
filtres de publication — confiance, cessions privées, extrémités non publiées
(écartées ou déliées), doublons — et donne à chaque flux son sens (entrant,
sortant, tiers) et son état (ce que les pièces attestent).
"""
from __future__ import annotations

import sys
from collections import Counter
from statistics import median

from collectors.etat_flux import etat_du_flux
from scripts.snapshot.revue import appliquer_revue
from scripts.snapshot.socle import RULES, rows, table_exists
from scripts.snapshot.textes import champ_publiable


# Sous ce nombre de ventes, une médiane ne résume rien : elle désigne une vente
# ou deux, donc un vendeur. Elle est publiée vide, avec le nombre qui l'explique.
DVF_SEUIL_MEDIANE = 5


def _mutations_dvf(lignes: list[dict]) -> list[dict]:
    """Regroupe les lignes DVF par mutation — la vente, telle que l'acte la porte.

    DVF écrit une ligne par local et par nature de culture de chaque parcelle :
    une maison vendue avec son garage, son jardin et un bois en fait quatre,
    toutes au prix de l'acte entier. Le site comptait ces lignes et les appelait
    des ventes : 339 à Lasalle sur 2021-2025 pour 141 mutations, 112 pour 34 en
    2025 (relevé du 14/11/2026, recompté sur le fichier de la DGFiP).

    L'identifiant est `id_mutation`. Une ligne entrée avant que la colonne
    existe n'en a pas : elle se range par date, prix et nature de mutation, que
    toutes les lignes d'un même acte partagent — repli exact sur Lasalle
    (141 pour 141), qui confondrait deux ventes du même jour au même prix.
    """
    groupes: dict = {}
    for l in lignes:
        cle = l.get("id_mutation") or (l["date"], l["price"], l.get("nature_mutation"))
        groupes.setdefault(cle, []).append(l)
    mutations = []
    for groupe in groupes.values():
        natures = [(l["nature_bien"] or "").lower() for l in groupe]

        def a(motif):
            return any(motif in n for n in natures)

        # UNE catégorie par mutation, la plus bâtie d'abord : la composition
        # retombe ainsi sur le total, ce qu'un compte par nature ne ferait pas.
        categorie = ("maison" if a("maison") else "appartement" if a("appartement")
                     else "local" if a("local")
                     else "dependance" if a("dépendance") or a("dependance")
                     else "non_bati")
        # Les logements distincts : la même maison revient sur chaque ligne de
        # culture de sa parcelle, avec la même surface.
        logements = {(l.get("cadastre_ref"), (l["nature_bien"] or "").lower(), l.get("surface_bati"))
                     for l in groupe
                     if ("maison" in (l["nature_bien"] or "").lower()
                         or "appartement" in (l["nature_bien"] or "").lower())
                     and l.get("surface_bati")}
        prix = next((l["price"] for l in groupe if l["price"]), None)
        # Le prix au m² ne se calcule que sur une vente d'UN logement, rapporté à
        # sa surface BÂTIE. Il était calculé par ligne, sur la surface du terrain
        # quand il y en avait un : 978 €/m² publiés à Lasalle, pour 1 483 ainsi.
        # Le prix d'un acte qui porte deux logements ne se partage pas entre eux.
        m2 = None
        if prix and len(logements) == 1:
            m2 = prix / next(iter(logements))[2]
        mutations.append({"annee": (groupe[0]["date"] or "")[:4], "categorie": categorie,
                          "prix": prix, "m2": m2,
                          "natures": {l["nature_bien"] or "-" for l in groupe}})
    return mutations


def agreger_dvf(lignes: list[dict]) -> dict:
    """DVF ne sort qu'en agrégats : par année, par nature de bien, et en synthèse.

    Le snapshot publiait chaque mutation — date, parcelle, prix, coordonnées —
    dans du HTML prérendu et un `dvf.json` signalé aux robots. Or les conditions
    de réutilisation de la DGFiP interdisent la ré-identification, même
    indirecte, des personnes (art. R112 A-3 LPF) et l'indexation par les moteurs
    de recherche. Dans une commune de mille habitants, une parcelle et une date
    suffisent à retrouver le vendeur (audit du 17/10/2026). La ligne reste en
    base, pour l'atelier ; le site n'en reçoit que des comptes et des médianes,
    et une médiane sur moins de `DVF_SEUIL_MEDIANE` ventes n'est pas publiée.

    L'unité de compte est la MUTATION (`_mutations_dvf`), jamais la ligne.
    """
    mutations = _mutations_dvf(lignes)

    def med(valeurs):
        valeurs = [v for v in valeurs if v]
        return round(median(valeurs)) if len(valeurs) >= DVF_SEUIL_MEDIANE else None

    def combien(categorie):
        return sum(1 for m in mutations if m["categorie"] == categorie)

    annees = sorted({m["annee"] for m in mutations} - {""})
    par_annee = []
    for annee in annees:
        dans = [m for m in mutations if m["annee"] == annee]
        m2 = [m["m2"] for m in dans if m["m2"]]
        par_annee.append({"annee": int(annee), "mutations": len(dans),
                          "ventes_bati": len(m2), "mediane_m2": med(m2)})

    # Une mutation compte dans chaque nature qu'elle porte : ces comptes-là ne
    # s'additionnent pas, et la page le dit.
    par_nature = Counter(n for m in mutations for n in m["natures"])
    return {
        "seuil_mediane": DVF_SEUIL_MEDIANE,
        "synthese": {
            "mutations": len(mutations),
            "lignes": len(lignes),
            "debut": int(annees[0]) if annees else None,
            "fin": int(annees[-1]) if annees else None,
            "maisons": combien("maison"),
            "appartements": combien("appartement"),
            "dependances": combien("dependance"),
            "locaux": combien("local"),
            "non_bati": combien("non_bati"),
            "mediane_prix_maison": med(m["prix"] for m in mutations
                                       if m["categorie"] == "maison"),
            "mediane_m2_bati": med(m["m2"] for m in mutations),
        },
        "par_annee": par_annee,
        "par_nature": [{"nature": n, "mutations": k} for n, k in par_nature.most_common()],
    }


def flux_extremites_publiees(flow: dict, public_ids: set[int]) -> bool:
    """Les deux extrémités NOMMÉES d'un flux désignent-elles des fiches publiées ?

    `is_public_relation` tient cette règle depuis toujours pour les relations
    (`endpoint_not_public`) ; les flux financiers ne l'avaient jamais eue. Le
    filtre voisin, lui, ne regarde que les personnes physiques — il laissait donc
    passer les flux dont l'extrémité est une entité MORALE non publiée, une
    association d'une commune voisine par exemple. La page des flux en faisait un
    lien vers une fiche que le snapshot n'écrit pas, et le build du site échouait
    sur `404 /entite/<id> (linked from /finances)` : l'instance entière devenait
    impubliable à cause de trois associations. Mesuré le 21/08/2026 sur la
    première commune : 10 flux, 3 440 €.

    Le flux est écarté, et non simplement délié : publier un montant dont le
    bénéficiaire n'a pas de fiche reviendrait à nommer une entité que le filtre
    de périmètre vient d'écarter, en la privant du contexte qui rendrait ce nom
    lisible.

    Une extrémité vide (`NULL`) ne bloque rien : un flux dont le bénéficiaire est
    inconnu ne prétend renvoyer nulle part. C'est le cas des lignes que les
    collecteurs n'ont pas su rattacher, traitées plus loin par la déduplication.
    """
    for cle in ("from_id", "to_id"):
        eid = flow.get(cle)
        if eid and eid not in public_ids:
            return False
    return True


def statut_extremites(flow: dict, public_ids: set[int],
                      ecartees_du_perimetre: set[int]) -> str:
    """« garde », « delie » ou « ecarte » — le sort d'un flux dont une extrémité
    n'a pas de fiche.

    Une subvention votée par la commune à une association dont le siège est dans
    une commune voisine est un fait COMMUNAL : c'est le budget d'ici qui la
    paie. Elle était pourtant écartée avec tout le reste, parce que le
    bénéficiaire est classé C2 et n'a donc pas de fiche
    (`publiable_dans_perimetre`). Mesuré le 16/09/2026 : 15 lignes sur 44 à
    Saillans, 21 à Lasalle — l'argent public le plus proche du lecteur, absent
    du site qui parle de son argent.

    Le flux est donc DÉLIÉ plutôt qu'écarté, comme l'est déjà un marché dont le
    titulaire n'a pas de fiche : le nom reste, le lien tombe, et aucune fiche
    n'est créée pour autant — la règle du périmètre n'est pas entamée.

    Deux bornes, et elles font tout le sens de cette exception :
      - l'extrémité sans fiche doit être écartée POUR LE SEUL PÉRIMÈTRE. Une
        personne physique sans rôle civique, une entité en `probable` sont
        écartées pour ce qu'elles sont : rien n'en sort, pas même un nom ;
      - l'autre extrémité doit, elle, être publiée. Un flux entre deux entités
        sans fiche ne dit rien de la commune.
    """
    if flux_extremites_publiees(flow, public_ids):
        return "garde"
    for cle in ("from_id", "to_id"):
        eid = flow.get(cle)
        if eid and eid not in public_ids and eid not in ecartees_du_perimetre:
            return "ecarte"
    publiee = any(flow.get(cle) in public_ids for cle in ("from_id", "to_id"))
    return "delie" if publiee else "ecarte"


def delier_extremites(flow: dict, public_ids: set[int]) -> None:
    """Coupe le renvoi vers une fiche que le snapshot n'écrit pas, en gardant le
    nom : `/finances` affiche déjà le nom nu quand l'identifiant manque."""
    for cle in ("from_id", "to_id"):
        eid = flow.get(cle)
        if eid and eid not in public_ids:
            flow[cle] = None


def _cle_beneficiaire(flow: dict):
    """L'identité du bénéficiaire, pour dédoublonner : sa fiche, ou son NOM.

    Un flux délié (bénéficiaire hors périmètre, cf. `statut_extremites`) n'a plus
    d'identifiant mais garde son nom. Dédoublonner sur le seul identifiant faisait
    alors disparaître une association derrière une autre : RASED et Prévention
    routière, 100 € chacune en 2023, ne laissaient qu'une ligne.
    """
    if flow.get("to_id"):
        return ("id", flow["to_id"])
    return ("nom", " ".join(str(flow.get("to_name") or "").split()).lower())


def beneficiaire_inconnu(flow: dict) -> bool:
    """Le flux ne nomme personne — ce que les collecteurs laissent quand ils n'ont
    pas su rattacher une ligne.

    L'absence de FICHE n'est pas l'absence de NOM. Les confondre écartait les flux
    déliés comme s'ils ne désignaient personne : sur la première instance, 9 des
    15 subventions rendues au public repartaient aussitôt.
    """
    return str(flow.get("to_name") or "").strip() in ("", "?", "∅")


def dedupliquer_flux(flows: list[dict]) -> list[dict]:
    """Les doublons exacts, puis les « jumeaux » sans bénéficiaire nommé.

    Deux passes distinctes : la même décision écrite deux fois par deux
    collecteurs, et la ligne anonyme qu'un collecteur laisse à côté d'une ligne
    nommée du même montant.
    """
    vus, dedup = set(), []
    for f in flows:
        cle = (f.get("year"), f.get("amount"), f.get("type"), f.get("from_id"),
               _cle_beneficiaire(f))
        if cle in vus:
            continue
        vus.add(cle)
        dedup.append(f)
    jumeau = lambda f: (f.get("year"), f.get("amount"), f.get("type"), f.get("from_id"))
    nommes = {jumeau(f) for f in dedup if not beneficiaire_inconnu(f)}
    return [f for f in dedup
            if not beneficiaire_inconnu(f) or jumeau(f) not in nommes]


def _commune_entity_id(conn) -> int | None:
    """Id de l'entité « Commune de … », ou None si elle n'est pas en base.

    Cherchée par nom NORMALISÉ, comme le fait `upsert_entity` : SIRENE l'écrit
    « COMMUNE DE LASALLE », et l'égalité exacte de SQLite respecte la casse.
    Le nom exact ne trouvait donc rien, sur les trois instances : aucun flux ne
    sortait `sortant`, `/finances` affichait « 0 € versé » pour chaque année
    alors que les fiches publiaient les subventions (audit du 24/09/2026).
    """
    from collectors.config import COMMUNE_NAME
    from collectors.nom_normalise import normaliser
    row = conn.execute(
        "SELECT id FROM entities WHERE type='service' AND name_norm=? "
        "ORDER BY (commune=?) DESC, id LIMIT 1",
        (normaliser(f"Commune de {COMMUNE_NAME}"), COMMUNE_NAME)).fetchone()
    return row["id"] if row else None


def _epci_entity_id(conn) -> int | None:
    """Id de la fiche de l'intercommunalité de l'instance (`epci_nom`), ou None."""
    from collectors.config import EPCI_NOM
    from collectors.nom_normalise import normaliser
    if not EPCI_NOM:
        return None
    row = conn.execute("SELECT id FROM entities WHERE type='service' AND name_norm=? "
                       "ORDER BY id LIMIT 1", (normaliser(EPCI_NOM),)).fetchone()
    return row["id"] if row else None


def nommer_acheteurs(marches: list[dict], noms: dict[int, str]) -> int:
    """L'acheteur porte le nom de SA FICHE, pas la graphie de chaque source.

    À Lasalle, les 58 marchés publiés avaient le même acheteur
    (`acheteur_id` identique) sous cinq noms — « COM COMMUNES CAUSSES AIGOUAL
    CEVENNES », « CC Causes Aigoual Cévennes »… — et /marches annonçait
    « Acheteurs : 5 » (relevé du 04/10/2026, refonte du contenu,
    défaut 5 et décision 8). La graphie de la source n'est pas perdue : elle
    reste dans `acheteur_libelle_source`, pour retrouver la ligne dans sa
    source. Un acheteur sans fiche publiée garde le nom que la source donne.

    Rend le nombre de marchés renommés.
    """
    renommes = 0
    for m in marches:
        nom = noms.get(m.get("acheteur_id"))
        m["acheteur_libelle_source"] = m.get("acheteur_nom")
        if nom and nom != m.get("acheteur_nom"):
            m["acheteur_nom"] = nom
            renommes += 1
    return renommes


def acheteurs_par_siren(conn) -> dict[str, int]:
    """SIREN → fiche, pour la commune et l'intercommunalité de l'instance.

    Ce sont les deux acheteurs dont l'instance déclare le SIREN ; leurs fiches
    se retrouvent par le nom, comme partout ailleurs dans le snapshot (aucun
    identifiant de ligne écrit en dur).
    """
    from collectors.config import COMMUNE_SIRENS, EPCI_SIREN
    from collectors.marches_publics import siren_de
    sortie = {}
    commune = _commune_entity_id(conn)
    for siren in COMMUNE_SIRENS:
        if commune and siren_de(siren):
            sortie[siren_de(siren)] = commune
    epci = _epci_entity_id(conn)
    if epci and siren_de(EPCI_SIREN):
        sortie[siren_de(EPCI_SIREN)] = epci
    return sortie


def portee_marche(acheteur_id, commune_id, epci_id) -> str:
    """La portée d'un marché : QUI l'a passé, pas où siège l'acheteur.

    Elle se lisait sur le périmètre de la fiche (C1 → commune, C2 →
    intercommunalité). Mais C2 dit « une commune du territoire de l'EPCI »,
    pas « l'EPCI » : à Saillans, 14 marchés de la commune de Vercheny et 3 du
    département étaient publiés comme marchés de l'intercommunalité, et 3 du
    syndicat de la rivière Drôme (siège à Saillans, C1) comme marchés de la
    commune (relevé du 28/10/2026). Tout autre acheteur relève du territoire.
    """
    if acheteur_id is not None and acheteur_id == commune_id:
        return "commune"
    if acheteur_id is not None and acheteur_id == epci_id:
        return "intercommunalite"
    return "territoire"


def rattacher_acheteurs(marches: list[dict], par_siren: dict[str, int]) -> int:
    """Un marché sans fiche d'acheteur, mais dont l'acheteur porte le SIREN de
    la commune ou de l'intercommunalité, est rattaché à sa fiche — et prend
    donc son nom (`nommer_acheteurs`) et sa portée.

    Le SIREN est la clé ; le nom n'en est jamais une. Sans ce rattachement, une
    ligne saisie à la main ou lue dans un procès-verbal sous « CC Causses
    Aigoual » restait une sixième graphie de l'acheteur que les cinq autres
    désignaient déjà. Le SIREN lui-même ne sort pas : il n'était pas dans
    `marches.json`, et ce rattachement n'a pas à changer son format.

    Rend le nombre de marchés rattachés.
    """
    from collectors.marches_publics import siren_de
    rattaches = 0
    for m in marches:
        siren = siren_de(m.pop("acheteur_siren", None))
        if m.get("acheteur_id") is None and siren in par_siren:
            m["acheteur_id"] = par_siren[siren]
            rattaches += 1
    return rattaches


def delier_renvois_morts(marches: list[dict], public_ids: set[int]) -> int:
    """Coupe les renvois d'un marché vers une fiche que le snapshot n'écrit pas.

    Un flux financier est ÉCARTÉ dans ce cas (cf. `flux_extremites_publiees`) :
    un montant versé à quelqu'un qui n'a pas de fiche nomme une entité sans le
    contexte qui la rendrait lisible. Un marché, lui, reste — et c'est une
    différence de nature, pas une inconséquence. La pièce décrit un ACHAT de la
    collectivité : son objet, son montant, sa procédure informent même quand
    l'attributaire est une entreprise de Valence qui n'a rien d'autre à voir avec
    la commune. Le nom du titulaire vient du BOAMP ou des DECP, il est public, et
    l'effacer reviendrait à cacher qui a été payé.

    Seul le LIEN tombe. `/marches` et `/entite/<id>` affichent déjà le nom nu
    quand l'identifiant manque.

    Relevé sur Saillans le 23/08/2026, à la première collecte de marchés : deux
    titulaires d'un marché de la communauté de communes, écartés des fiches par
    `hors_fiche_perimetre_C2`, et l'invariant « renvoi vers une fiche non
    publiée » refusait le snapshot entier — trois fichiers, quatre renvois.
    """
    morts = 0
    for marche in marches:
        for cle in ("acheteur_id", "titulaire_id"):
            if marche.get(cle) is not None and marche[cle] not in public_ids:
                marche[cle] = None
                morts += 1
    return morts


def renvoyer_aux_pieces(flows: list[dict], public_event_ids: set,
                        pieces_des_fragments: dict | None = None) -> int:
    """Le renvoi de chaque flux vers ce qui l'atteste : `acte_id` quand l'acte
    est publié, `document` quand la pièce a une URL externe. Rend le nombre de
    flux sans aucun renvoi.

    Revue d'architecture du 07/11/2026, C1 : la requête ne lisait ni
    `event_id` ni `raw_document_id`, et `flows.json` ne portait que des
    étiquettes (« CR CM année ») — le front ne pouvait pas lier ce que les
    données ne portaient pas. Les identifiants internes ne sortent pas : un
    acte non publié n'a pas de page, une pièce déposée (`url` NULL) non plus.
    """
    sans = 0
    for f in flows:
        event_id = f.pop("event_id", None)
        f.pop("raw_document_id", None)
        url = f.pop("document_url", None)
        f["acte_id"] = event_id if event_id in public_event_ids else None
        # Lu dans un fragment de procès-verbal qui ne sort plus (`fragments_de_pv`) :
        # le flux renvoie au procès-verbal lui-même, qui l'atteste.
        if f["acte_id"] is None and not url:
            url = (pieces_des_fragments or {}).get(event_id)
        f["document"] = url if isinstance(url, str) and url.startswith(
            ("https://", "http://")) else None
        if f["acte_id"] is None and f["document"] is None:
            sans += 1
    return sans


def etape_flux(conn, revue, entity_rows, public_person_ids, public_ids,
               ecartees_du_perimetre, redige, exclusions, public_events,
               pieces_des_fragments=None, actes_de_rubrique=frozenset()) -> dict:
    flow_rows = rows(conn, """
        SELECT ff.id, ff.type, ff.year, ff.amount, ff.description,
               ff.source, ff.confidence, ff.origine,
               ff.event_id, ff.raw_document_id, rd.url AS document_url,
               COALESCE(ff.type_norm, ff.type) AS type_norm,
               COALESCE(ff.perimetre, 'detail') AS perimetre,
               COALESCE(ff.statut, 'realise') AS statut,
               ff.from_id, ff.to_id,
               f.name AS from_name, t.name AS to_name
        FROM financial_flows ff
        LEFT JOIN entities f ON f.id = ff.from_id
        LEFT JOIN entities t ON t.id = ff.to_id
        LEFT JOIN raw_documents rd ON rd.id = ff.raw_document_id
        ORDER BY ff.year DESC, ff.amount DESC
    """)
    # La revue passe AVANT le filtre de confiance : c'est précisément son
    # rôle de faire passer un flux de `probable` à `verified` après contrôle
    # humain, ou l'inverse.
    revue_flux = revue.get("flow", {})
    revus = []
    for flow in flow_rows:
        flow = appliquer_revue(flow, revue_flux.get(flow["id"]))
        if flow is None:
            exclusions["flows"]["rejete_en_atelier"] += 1
            continue
        revus.append(flow)
    flow_rows = revus

    # Un flux lu dans la rubrique d'un procès-verbal où l'exécutif rend compte
    # de ses décisions par délégation : c'est une décision du maire ou du
    # président, qui n'est pas publiée depuis un PV (Julien, 14 et 15/11/2026),
    # ni par son titre ni par son montant (relecture vigie-securite du 15/11).
    if actes_de_rubrique:
        avant = len(flow_rows)
        flow_rows = [f for f in flow_rows if f.get("event_id") not in actes_de_rubrique]
        exclusions["flows"]["lu_dans_une_rubrique_de_decisions"] = avant - len(flow_rows)

    public_flows = [
        flow for flow in flow_rows
        if flow.get("confidence") in set(RULES["confidence"]["public"])
    ]
    exclusions["flows"]["private_confidence"] = len(flow_rows) - len(public_flows)

    # Cessions strictement privées (aucune des parties n'est la commune) :
    # ce sont des mutations commerciales (fonds de commerce), pas des flux
    # de finances publiques → hors périmètre de cette page.
    # Identifiant résolu par le nom : `63` était le numéro de ligne de la
    # commune dans la base de Lasalle. Sur une autre base il désigne une
    # entité quelconque, et le filtre des cessions privées laisse alors
    # passer ce qu'il devait écarter — silencieusement.
    COMMUNE_ID = _commune_entity_id(conn)
    if COMMUNE_ID is None and flow_rows:
        # Sans elle, aucun flux n'a de sens et toute cession passe pour
        # privée : le dire, plutôt que publier des zéros.
        print("⚠ entité « Commune de … » introuvable : sens des flux non "
              "déterminé, cessions communales écartées", file=sys.stderr)
    before = len(public_flows)
    public_flows = [
        f for f in public_flows
        if not (str(f.get("type", "")).startswith("cession")
                and COMMUNE_ID not in (f.get("from_id"), f.get("to_id")))
    ]
    exclusions["flows"]["private_cession"] = before - len(public_flows)

    # Un flux dont une extrémité est une personne physique non publiable
    # publiait son nom en clair (`to_name`) alors que l'entité elle-même est
    # écartée du snapshot : le filtre entités était contourné par les flux.
    personnes_privees = {
        r["id"] for r in entity_rows
        if r["type"] == "person" and r["id"] not in public_person_ids
    }
    before = len(public_flows)
    public_flows = [
        f for f in public_flows
        if not ({f.get("from_id"), f.get("to_id")} & personnes_privees)
    ]
    exclusions["flows"]["private_person_endpoint"] = before - len(public_flows)

    # Même contournement, une marche plus haut : le filtre ci-dessus ne voit
    # que les personnes physiques, et laissait passer les flux dont
    # l'extrémité est une entité morale non publiée — cf.
    # `flux_extremites_publiees`, qui porte la règle et son histoire.
    before = len(public_flows)
    gardes, delies = [], 0
    for f in public_flows:
        etat = statut_extremites(f, public_ids, ecartees_du_perimetre)
        if etat == "ecarte":
            continue
        if etat == "delie":
            delier_extremites(f, public_ids)
            delies += 1
        gardes.append(f)
    public_flows = gardes
    exclusions["flows"]["endpoint_not_public"] = before - len(public_flows)

    # Déduplication : doublons exacts (même année/montant/type/émetteur/destinataire)
    # et « jumeaux » non résolus (montant identique, bénéficiaire vide) laissés par
    # les collecteurs. On garde les bénéficiaires DISTINCTS de même montant.
    before = len(public_flows)
    public_flows = dedupliquer_flux(public_flows)
    exclusions["flows"]["duplicates"] = before - len(public_flows)

    # `sens` : la DGF encaissée par la commune (489 690 €) et la subvention
    # versée au Comité des fêtes (4 400 €) sortaient avec la même mise en
    # forme. Sans le sens du flux, la page se lit à contresens.
    # Un flux délié a perdu son identifiant (`None`) : tant que COMMUNE_ID
    # valait `None` lui aussi, il sortait « entrant ».
    for f in public_flows:
        f["description"] = redige(f.get("description"))
        if f.get("note_revue"):
            f["note_revue"] = redige(champ_publiable(f["note_revue"], None, set()))
        if COMMUNE_ID is None:
            f["sens"] = "tiers"
        elif f.get("to_id") == COMMUNE_ID and f.get("from_id") != COMMUNE_ID:
            f["sens"] = "entrant"
        elif f.get("from_id") == COMMUNE_ID:
            f["sens"] = "sortant"
        else:
            f["sens"] = "tiers"

    # `de_l_epci` : un flux intercommunalité → association est `tiers` (le sens
    # se mesure à la commune) et ne finissait que dans le tableau brut de
    # /finances, parmi les subventions de la Région et les cessions privées.
    # Le signaler permet de l'afficher à part — jamais additionné à la commune.
    EPCI_ID = _epci_entity_id(conn)
    for f in public_flows:
        f["de_l_epci"] = (f["sens"] == "tiers" and EPCI_ID is not None
                          and f.get("from_id") == EPCI_ID)

    # `statut` valait `realise` sur la totalité des flux — la subvention
    # votée, la dotation lue dans les comptes et la demande de DSIL avec le
    # même mot. La page en tirait « la commune a versé ». L'état se déduit
    # de ce qui documente le montant : cf. `collectors/etat_flux`.
    for f in public_flows:
        f["etat"] = etat_du_flux(f.get("type"), f.get("source"), f.get("statut"))
    flows_par_etat = dict(Counter(f["etat"] for f in public_flows))

    # Le renvoi à la pièce, une fois la sélection faite (C1, revue du 07/11).
    # Ce n'est pas une exclusion : le compte se lit dans `flows.json`, et
    # `verify_snapshot` refuse un flux `verbatim` sans renvoi.
    renvoyer_aux_pieces(public_flows, {e.get("id") for e in public_events},
                        pieces_des_fragments)

    # Également calculés, jamais publiés : `flux_delies_hors_perimetre`,
    # `flows_par_sens`, `flows_par_statut`. Seul l'état sort, dans `stats.json`.
    return {"flow_rows": flow_rows, "public_flows": public_flows,
            "flows_par_etat": flows_par_etat}


def _siren_col(conn, table: str) -> str:
    """`siren` si la table l'a (colonne du 02/11/2026), sinon un NULL nommé."""
    return ("siren" if table_exists(conn, table) and "siren" in {
        r["name"] for r in rows(conn, f"PRAGMA table_info({table})")} else "NULL AS siren")


def marquer_avant_fusion(lignes: list[dict], avant: dict[str, str]) -> int:
    """Une ligne lue sous le SIREN d'une ancienne commune porte `avant_fusion`
    (son nom) et son `siren` ; les autres perdent la colonne, qui ne sert qu'à
    ce marquage. Sans lui, un exercice de l'ancienne commune sortait sous le
    nom actuel (relevé de Vallouise-Pelvoux, 01/11/2026). Rend le nombre de
    lignes marquées."""
    marquees = 0
    for ligne in lignes:
        siren = ligne.pop("siren", None)
        if siren and siren in avant:
            ligne["avant_fusion"] = avant[siren]
            ligne["siren"] = siren
            marquees += 1
    return marquees


def marquer_marches_avant_fusion(marches: list[dict], avant: dict[str, str]) -> int:
    """`avant_fusion` (nom de l'ancienne commune) sur un marché passé sous un
    SIREN d'avant la fusion. L'ancienne commune au même code reste de la
    commune, marquée ; une absorbée d'un autre code n'a pas de fiche : portée
    « territoire », marquée aussi, jamais comptée avec la commune.

    À appeler sur les lignes telles que lues, AVANT `rattacher_acheteurs` qui
    retire `acheteur_siren`. Rend le nombre de marchés marqués."""
    from collectors.marches_publics import siren_de
    marques = 0
    for m in marches:
        nom = avant.get(siren_de(m.get("acheteur_siren") or ""))
        if nom:
            m["avant_fusion"] = nom
            marques += 1
    return marques


def avant_fusion(conn) -> dict:
    """`avant_fusion.json` : les prédécesseurs de la commune, et les séries des
    communes absorbées d'un autre code, À PART — elles ne s'additionnent
    jamais à celle de la commune (tranché le 02/11/2026)."""
    from collectors.config import (COMMUNE_NAME, COMMUNE_SIREN, COMMUNE_SIRENS_ANCIENS,
                                   COMMUNES_ABSORBEES)
    predecesseurs = [{"siren": s, "nom": COMMUNE_NAME, "sorte": "meme_code"}
                     for s in COMMUNE_SIRENS_ANCIENS]
    predecesseurs += [{"siren": s, "nom": d["nom"], "insee": d["insee"], "sorte": "absorbee"}
                      for s, d in COMMUNES_ABSORBEES.items()]
    sirens = tuple(COMMUNES_ABSORBEES)
    marques = ",".join("?" * len(sirens))

    def serie(table: str, colonnes: str, ordre: str) -> list[dict]:
        if not sirens or not table_exists(conn, table):
            return []
        return rows(conn, f"SELECT siren, {colonnes} FROM {table} WHERE siren IN ({marques})"
                          f" ORDER BY siren, {ordre}", sirens)

    return {
        "commune": {"siren": COMMUNE_SIREN, "nom": COMMUNE_NAME},
        "predecesseurs": predecesseurs,
        "absorbees": {
            "ofgl": serie("ofgl_agregats_absorbees",
                          "year, agregat, montant, euros_par_habitant, population",
                          "year DESC, agregat"),
            "budget": serie("budget_annuel_absorbees",
                            "year, categorie, compte, libelle, montant, source",
                            "year DESC, categorie, montant DESC"),
        },
    }


def etape_finances(conn, revue, public_ids, public_entities, perimetre_par_entite,
                   noms_publics, exclusions) -> dict:
    from collectors.config import SIRENS_AVANT_FUSION
    # ── Données financières & foncières officielles (open data) ───────────
    # DGFiP, OFGL, DECP : faits publics par nature → export complet. DVF : agrégats
    # seulement, cf. `agreger_dvf`.
    budget_annuel = rows(conn, f"""
        SELECT year, categorie, compte, libelle, montant, source, {_siren_col(conn, "budget_annuel")}
        FROM budget_annuel ORDER BY year DESC, categorie, montant DESC
    """)
    budget_annexe = rows(conn, f"""
        SELECT ba.year, ba.section, ba.sens, ba.libelle, ba.montant, ba.source,
               ba.entity_id, e.name AS entity_name, {"ba.siren" if _siren_col(conn, "budget_annexe") == "siren" else "NULL AS siren"}
        FROM budget_annexe ba LEFT JOIN entities e ON e.id = ba.entity_id
        ORDER BY ba.year DESC, ba.section
    """)
    # Le détail DGFiP par budget annexe (`collectors/budgets_annexes`) prend la forme
    # des lignes ci-dessus : la page les groupe par nom. Le nom est celui de la
    # DGFiP, pas celui d'une fiche ; le lien ne sort que vers une fiche publiée.
    if table_exists(conn, "budget_annexe_balance"):
        from collectors.budgets_annexes import suites
        poursuites = suites()
        # `cautions` : colonne du 21/10/2026, absente d'une base plus ancienne.
        cautions = ("cautions" if "cautions" in {r["name"] for r in rows(
            conn, "PRAGMA table_info(budget_annexe_balance)")} else "NULL AS cautions")
        for b in rows(conn, f"""
            SELECT year, budget, entity_id, encours_dette, {cautions}, solde_fonctionnement, source,
                   {_siren_col(conn, "budget_annexe_balance")}
            FROM budget_annexe_balance ORDER BY year DESC, budget
        """):
            commun = {"year": b["year"], "source": b["source"], "entity_name": b["budget"],
                      "entity_id": b["entity_id"] if b["entity_id"] in public_ids else None,
                      "siren": b["siren"]}
            if b["budget"] in poursuites:
                commun["poursuivi_sous"] = poursuites[b["budget"]]
            # Emprunts et cautionnements reçus (compte 165) : le périmètre de
            # l'« Encours de dette » OFGL, pour que la somme des budgets y retombe.
            if b["encours_dette"] is not None or b["cautions"] is not None:
                budget_annexe.append({**commun, "section": "dette", "sens": "solde",
                                      "libelle": "Encours de dette (emprunts et cautionnements, balance DGFiP)",
                                      "montant": (b["encours_dette"] or 0) + (b["cautions"] or 0),
                                      "cautions": b["cautions"]})
            if b["solde_fonctionnement"] is not None:
                budget_annexe.append({**commun, "section": "fonctionnement", "sens": "solde",
                                      "libelle": "Produits − charges de l'exercice (balance DGFiP)",
                                      "montant": b["solde_fonctionnement"]})
    ofgl_data = rows(conn, f"""
        SELECT year, agregat, montant, euros_par_habitant, population, {_siren_col(conn, "ofgl_agregats")}
        FROM ofgl_agregats ORDER BY year DESC, agregat
    """)
    for lignes in (budget_annuel, budget_annexe, ofgl_data):
        marquer_avant_fusion(lignes, SIRENS_AVANT_FUSION)
    # Budgets primitifs VOTÉS (prévisionnel) extraits des CR — comble le trou
    # après OFGL (>2024). Fait public (délibération) → export complet.
    budget_vote = rows(conn, """
        SELECT year, scope, agregat, value, unit, approx, note, source, source_url
        FROM budget_vote ORDER BY year DESC, scope, id
    """) if table_exists(conn, "budget_vote") else []
    for b in budget_vote:
        b["note"] = champ_publiable(b["note"], None, noms_publics)
    dvf_data = agreger_dvf(rows(conn, """
        SELECT * FROM dvf_transactions
    """))
    # Le filtre de confiance manquait ici, alors qu'il s'applique partout
    # ailleurs : la table publiait TOUT. Un marché dont l'acheteur n'a pas
    # pu être établi affirmait donc qu'une collectivité avait acheté ce
    # qu'elle n'avait pas acheté. `probable` reste en base et attend
    # l'atelier ; il ne sort pas.
    colonnes_mp = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    filtre_mp = ("WHERE confidence IN ('verified', 'confirmed')"
                 if "confidence" in colonnes_mp else "")
    # `montant_base` (HT ou TTC) depuis le 04/10/2026 : un marché lu dans un
    # procès-verbal porte l'un ou l'autre. Absente d'une base plus ancienne.
    base_mp = "montant_base" if "montant_base" in colonnes_mp else "NULL AS montant_base"
    # La confiance sort avec la ligne : le contrôleur la vérifie quand il la
    # voit, et il ne la voyait pas — le filtre ci-dessus n'avait aucun témoin.
    confiance_mp = ", confidence" if "confidence" in colonnes_mp else ""
    marches_data = rows(conn, f"""
        SELECT id, acheteur_id, acheteur_siren, acheteur_nom, titulaire_id, titulaire_nom,
               objet, nature, procedure, montant, {base_mp}, cpv_label, date_notif,
               lieu_exec, source, source_url{confiance_mp}
        FROM marches_publics {filtre_mp} ORDER BY date_notif DESC, montant DESC
    """)
    # AVANT `rattacher_acheteurs`, qui retire `acheteur_siren` : marqué après,
    # aucun marché ne l'était (constaté le 02/11/2026 sur Saillans en ligne).
    marquer_marches_avant_fusion(marches_data, SIRENS_AVANT_FUSION)
    rattacher_acheteurs(marches_data, acheteurs_par_siren(conn))
    for m in marches_data:
        if m.get("montant_base") is None:
            m.pop("montant_base", None)
    revue_marches = revue.get("marche", {})
    avant_revue = len(marches_data)
    marches_data = [m for m in (appliquer_revue(m, revue_marches.get(m["id"]))
                                for m in marches_data) if m is not None]
    exclusions["marches"]["rejete_en_atelier"] = avant_revue - len(marches_data)
    # Ce qui est écrit par un acheteur, lu dans un procès-verbal ou saisi à
    # l'atelier passe par le masque, comme le titre d'un acte.
    for m in marches_data:
        for champ in ("objet", "lieu_exec"):
            m[champ] = champ_publiable(m[champ], m.get("date_notif"), noms_publics)
        if m.get("note_revue"):
            m["note_revue"] = champ_publiable(m["note_revue"], None, noms_publics)

    # Le marché reste, le lien vers une fiche non publiée tombe.
    exclusions["marches"]["renvoi_vers_fiche_non_publiee"] = \
        delier_renvois_morts(marches_data, public_ids)
    nommer_acheteurs(marches_data, {e["id"]: e["name"] for e in public_entities})

    # La portée d'un marché est celle de son ACHETEUR. Un marché de la
    # communauté de communes n'est pas un marché de la commune, même quand
    # il est exécuté sur son territoire — et sans ce champ, la page des
    # marchés les additionnait sans le dire.
    commune_id, epci_id = _commune_entity_id(conn), _epci_entity_id(conn)
    for m in marches_data:
        m["portee"] = portee_marche(m.get("acheteur_id"), commune_id, epci_id)

    # Plans de financement votés (participations aux opérations du syndicat
    # d'électrification). Exportés à part des marchés : aucune entreprise
    # n'est retenue, les mêler fausserait le décompte des attributions.
    approbations_data = rows(conn, """
        SELECT id, event_id, date, objet, montant_ht, montant_ttc,
               maitre_ouvrage, citation, source, source_url, confidence
        FROM approbations_projets
        WHERE confidence IN ('verified', 'confirmed')
        ORDER BY date DESC
    """) if table_exists(conn, "approbations_projets") else []
    for a in approbations_data:
        for champ in ("objet", "maitre_ouvrage", "citation"):
            a[champ] = champ_publiable(a[champ], a["date"], noms_publics)

    return {
        "avant_fusion_data": avant_fusion(conn),
        "budget_annuel": budget_annuel,
        "budget_annexe": budget_annexe,
        "ofgl_data": ofgl_data,
        "budget_vote": budget_vote,
        "dvf_data": dvf_data,
        "marches_data": marches_data,
        "approbations_data": approbations_data,
    }
