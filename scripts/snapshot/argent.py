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
from scripts.snapshot.socle import CONFIANCE_MARCHES, RULES, rows, table_exists
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


def dedupliquer_flux(flows: list[dict], masque=None) -> list[dict]:
    """Les doublons exacts, puis les « jumeaux » sans bénéficiaire nommé.

    Deux passes distinctes : la même décision écrite deux fois par deux
    collecteurs, et la ligne anonyme qu'un collecteur laisse à côté d'une ligne
    nommée du même montant.

    `masque(flux)` dit qu'un flux sera délié ou masqué (« un particulier »,
    cf. `etape_flux`) : entre deux doublons, c'est CELUI-LÀ qui reste, jamais le
    neutre qui porterait le nom à côté d'un acte masqué (décision de Julien du
    24/11/2026). Sans lui, le premier reste, comme avant.
    """
    garde: dict = {}
    dedup = []
    for f in flows:
        cle = (f.get("year"), f.get("amount"), f.get("type"), f.get("from_id"),
               _cle_beneficiaire(f))
        if cle not in garde:
            garde[cle] = len(dedup)
            dedup.append(f)
        elif masque and masque(f) and not masque(dedup[garde[cle]]):
            dedup[garde[cle]] = f
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


def confiance_marche_publique(m: dict) -> bool:
    """Un marché sort si sa confiance, APRÈS la revue de l'atelier, est dans
    `CONFIANCE_MARCHES`. Une base sans colonne `confidence` ne filtre rien, sauf
    la confiance qu'un verdict aurait posée. Lue par `etape_finances` et
    `ids_retenus_par_un_fait` : la sortie et la retenue d'une EI ne divergent pas."""
    return "confidence" not in m or m["confidence"] in CONFIANCE_MARCHES


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


def delier_renvois_morts(marches: list[dict], public_ids: set[int], *,
                         ei_ids: set[int] | frozenset,
                         sirens_ei_ecartees: set[str] | frozenset,
                         noms_ei_ecartees: set[str] | frozenset,
                         ei_nommees: set[int] | frozenset,
                         commune_id: int | None) -> int:
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

    Exception : le titulaire est une ENTREPRISE INDIVIDUELLE non publiée
    (`ei_ids`, hors des `public_ids` : C2, rejetée à l'atelier, sans fait
    public), désignée par son identifiant, par le SIREN du marché
    (`sirens_ei_ecartees`) ou, sans l'un ni l'autre, par son nom
    (`noms_ei_ecartees`, empreintes `cle_de_nom`, celles que le contrôleur
    compare). Son nom est celui d'une personne : il tombe avec le lien, la note
    de revue aussi, et `titulaire_masque` dit que le marché reste ATTRIBUÉ
    (`estAttribue` du site). Restent l'objet, le montant, l'acheteur (brief
    `ei-flux-marches`, 24/11/2026). `titulaire_siren` est lu ici et ne sort pas.

    Sauf une EI classée C2 que la commune désigne (`ei_nommees`) : sur un marché
    dont la COMMUNE est l'acheteur, son nom reste, sans lien (décision de Julien
    du 25/11/2026). Sur un autre marché, il tombe.
    """
    morts = 0
    for marche in marches:
        siren = siren_titulaire(marche.pop("titulaire_siren", None))
        tid = marche.get("titulaire_id")
        ei_ecartee = titulaire_a_taire(
            marche, siren, public_ids, ei_ids=ei_ids, sirens_ei_ecartees=sirens_ei_ecartees,
            noms_ei_ecartees=noms_ei_ecartees, ei_nommees=ei_nommees, commune_id=commune_id)
        for cle in ("acheteur_id", "titulaire_id"):
            if marche.get(cle) is not None and marche[cle] not in public_ids:
                marche[cle] = None
                morts += 1
        if ei_ecartee:
            # Les mots du nom effacé, pour le masque de l'objet de CE marché.
            from scripts.snapshot.fiches import mots_du_nom
            marche["_mots_retires"] = set(mots_du_nom(marche.get("titulaire_nom")))
            marche["_titulaire_ei"] = tid
            marche["titulaire_nom"] = None
            marche["titulaire_masque"] = True
            marche.pop("note_revue", None)
    return morts


def titulaire_a_taire(marche, siren, public_ids, *, ei_ids, sirens_ei_ecartees,
                      noms_ei_ecartees, ei_nommees, commune_id) -> bool:
    """Le titulaire d'un marché est-il une EI dont le nom ne sort pas ? Lu AVANT
    le déliement (`titulaire_id`, `acheteur_id` encore là), par
    `delier_renvois_morts` et par `etape_avis_de_marche` : une seule règle.
    Cf. `delier_renvois_morts` pour les trois façons de désigner l'EI et
    l'exception de l'EI C2 que la commune désigne."""
    from scripts.snapshot.fiches import cle_de_nom
    tid = marche.get("titulaire_id")
    if (tid in ei_nommees and commune_id is not None
            and marche.get("acheteur_id") == commune_id):
        return False
    return bool((tid in ei_ids and tid not in public_ids)
                or (tid is None and siren and siren in sirens_ei_ecartees)
                or (tid not in public_ids and marche.get("titulaire_nom")
                    and cle_de_nom(marche["titulaire_nom"]) in noms_ei_ecartees))


# Entre crochets, comme « [nom retiré] » : un caviardage se voit, il ne se lit
# pas comme le texte de la source (avis vigie-dpo du 25/11/2026).
MENTION_EI = "[entreprise individuelle]"
MOT_RETIRE = "[nom retiré]"


#: Ce qui fait d'un mot du territoire un LIEU : le mot qui le précède.
PREPOSITIONS_DE_LIEU = frozenset({"A", "DE", "SUR", "EN"})


def mots_du_territoire() -> frozenset:
    """Les mots du nom de la commune et de l'intercommunalité de l'instance :
    pas retirés comme mots isolés quand ils désignent le LIEU (« Travaux à
    Lasalle » face à une EI « Jean LASALLE », tour 7 de la relecture). Hors d'un
    contexte de lieu (« Ets Lasalle »), ils restent retirés : c'est aussi un nom
    de famille (avis vigie-securite, tour 7)."""
    from collectors.config import COMMUNE_NAME, EPCI_NOM
    from scripts.snapshot.fiches import mots_du_nom
    return frozenset(mots_du_nom(COMMUNE_NAME) | mots_du_nom(EPCI_NOM))


def masquer_noms(texte, motif, mots_isoles=frozenset()):
    """Remplace dans un texte libre les noms d'EI non publiées (`motif`, cf.
    `motif_des_noms`) par « une entreprise individuelle ». Avis vigie-securite et
    vigie-dpo du 25/11/2026 : l'objet d'un marché, la description d'un flux
    peuvent nommer l'EI dont on vient d'effacer le titulaire. La mention est
    écrite entre crochets (`MENTION_EI`).

    Les mots du texte sont lus par `fiches.jetons_lies` : accents, casse,
    apostrophe typographique ignorés ; mots vides (« de », « la ») transparents ;
    deux mots ne se suivent que séparés par des blancs ou des tirets.
    - nom de FICHE : une suite d'au moins deux mots du texte qui sont TOUS des
      mots de ce nom (ou l'initiale d'un de ses mots), dont un mot plein, dans
      n'importe quel ordre, prénom omis ou non (« DUPONT JEAN », « J. Dupont ») ;
    - VARIANTE (titulaire DECP, souvent une enseigne) : sa suite exacte de mots ;
    - une suite dont tous les mots tiennent dans le nom d'une fiche PUBLIÉE
      n'est pas masquée : elle désigne peut-être celle-ci ;
    - deux zones qui se chevauchent sont fusionnées.
    Un nom d'un seul mot n'est pas cherché : il masquerait des mots courants.

    `mots_isoles` : sur un marché dont le titulaire est effacé, chaque mot plein
    de son nom (trois lettres au moins, hors mots vides et hors mots du nom de
    la commune ou de l'intercommunalité, `mots_du_territoire`) qui reste
    devient « [nom retiré] »."""
    from scripts.snapshot.fiches import jetons_lies, mots_normalises
    if not texte or (not motif and not mots_isoles):
        return texte
    texte = str(texte)
    jetons = jetons_lies(texte)
    groupes, suites, publies = motif or ((), (), ())
    territoire = mots_du_territoire()
    tous = mots_normalises(texte)

    def lieu(debut):
        """Le mot qui précède ce jeton est-il « à », « de », « sur », « en » ?"""
        avant = [m for d, f, m, _ in tous if f <= debut]
        return bool(avant) and avant[-1] in PREPOSITIONS_DE_LIEU

    def publie(mots):
        return any(mots <= p for p in publies)

    zones = []
    for nom in groupes:
        initiales = {w[0] for w in nom}
        i = 0
        while i < len(jetons):
            j, mots = i, set()
            while j < len(jetons):
                _, _, mot, initiale, lie = jetons[j]
                if (j > i and not lie) or not (mot in initiales if initiale else mot in nom):
                    break
                if not initiale:
                    mots.add(mot)
                j += 1
            # « Publiée » demande deux mots pleins : « J. Dupont » ne tient pas
            # dans « DUPONT TP » (avis vigie-securite, b1).
            if j - i >= 2 and mots and not (len(mots) >= 2 and publie(mots)):
                zones.append([jetons[i][0], jetons[j - 1][1]])
                i = j
            else:
                i += 1
    for suite in suites:
        n = len(suite)
        for i in range(len(jetons) - n + 1):
            fenetre = jetons[i:i + n]
            if (all(not j[3] for j in fenetre) and all(j[4] for j in fenetre[1:])
                    and tuple(j[2] for j in fenetre) == suite and not publie(set(suite))):
                zones.append([fenetre[0][0], fenetre[-1][1]])
    # Zones qui se chevauchent : fusionnées (avis vigie-securite, c).
    fusion = []
    for d, f in sorted(zones):
        if fusion and d <= fusion[-1][1]:
            fusion[-1][1] = max(fusion[-1][1], f)
        else:
            fusion.append([d, f])
    remplacements = [(d, f, MENTION_EI) for d, f in fusion]
    remplacements += [(d, f, MOT_RETIRE) for d, f, mot, initiale, _ in jetons
                      if not initiale and len(mot) >= 3 and mot in mots_isoles
                      and not (mot in territoire and lieu(d))
                      and not any(zd < f and d < zf for zd, zf in fusion)]
    for d, f, r in sorted(remplacements, reverse=True):
        texte = texte[:d] + r + texte[f:]
    return texte


def avec_extremites(motif, ligne, cles, noms_par_id, public_ids):
    """Le motif, en épargnant aussi les noms des extrémités PUBLIÉES de cette
    ligne (`cles` : `from_id`/`to_id` d'un flux, `titulaire_id`/`acheteur_id`
    d'un marché). L'élu « Marc DUPRAT », exploitant d'une EI écartée, n'épargne
    pas le nom de son EI ailleurs (`mots_des_exploitants`), mais sur SA ligne
    (« Indemnité de fonction de Marc Duprat ») son nom le désigne, lui (tour 6 de
    la relecture). Même règle dans le contrôleur."""
    if motif is None:
        return None
    from scripts.snapshot.fiches import mots_du_nom
    extra = tuple(m for c in cles for i in [ligne.get(c)]
                  if i is not None and i in public_ids
                  for n in noms_par_id.get(i, ()) if n
                  for m in [mots_du_nom(n)] if len(m) >= 2)
    if not extra:
        return motif
    groupes, suites, publies = motif
    return groupes, suites, tuple(publies) + extra


def motif_des_noms(noms, publies=()):
    """Ce que `masquer_noms` cherche : `noms` = (noms de fiche, variantes), cf.
    `fiches.noms_des_ei_ecartees` ; `publies` = `fiches.mots_des_fiches_publiees`.
    None s'il n'y a rien à chercher."""
    from scripts.snapshot.fiches import mots_du_nom, suite_du_nom
    fiches, variantes = noms
    groupes = tuple(sorted({m for m in map(mots_du_nom, fiches) if len(m) >= 2}, key=sorted))
    suites = tuple(sorted({s for s in map(suite_du_nom, variantes) if len(s) >= 2}))
    if not groupes and not suites:
        return None
    return groupes, suites, tuple(publies)


def etape_avis_de_marche(conn, revue, entity_rows, ei_ids, ei_nommees, public_ids,
                         public_events) -> None:
    """Le titre d'un acte `marché_public` (avis BOAMP, DECP) est l'objet du
    marché (`collectors/marches_publics.py`) : il passe par le même masque que
    l'objet dans `marches.json`, sinon le nom retiré là ressortait dans
    `events.json`, `actualite.json` (à côté de la version masquée) et
    `recherche_index.json` (avis vigie-dpo du 25/11/2026). Juste après les
    fiches, avant tout ce qui lit les titres.

    Comme sur le marché : si un marché relié à l'acte (`marches_publics.event_id`)
    a pour titulaire une EI dont le nom ne sort pas (`titulaire_a_taire`, même
    règle que `delier_renvois_morts`), chaque mot de ce nom qui reste devient
    « [nom retiré] » (avis vigie-securite sur 2825b8a). La `note_revue` passe par
    le même masque ; aujourd'hui un acte `marché_public` n'en porte pas (la revue
    ne vise que les délibérations, `TYPES_REVUS`), c'est une défense."""
    from scripts.snapshot.fiches import (cle_de_nom, mots_des_exploitants,
                                         mots_des_fiches_publiees, mots_du_nom,
                                         noms_des_ei_ecartees)
    from scripts.snapshot.revue import ecartes
    avis = [e for e in public_events if e.get("type") == "marché_public"]
    if not avis:
        return
    sans_fiche = set(ei_ids) - set(public_ids)
    fiches_ei, variantes = noms_des_ei_ecartees(conn, entity_rows, sans_fiche, public_ids)
    motif = motif_des_noms((fiches_ei, variantes), mots_des_fiches_publiees(
        conn, public_ids, mots_exclus=mots_des_exploitants(
            conn, ecartes(revue, "relation"), set(ei_ids) - set(public_ids), public_ids)))
    regle = dict(
        ei_ids=ei_ids, ei_nommees=ei_nommees, commune_id=_commune_entity_id(conn),
        sirens_ei_ecartees={siren_titulaire(r.get("siren")) for r in entity_rows
                            if r["id"] in sans_fiche} - {""},
        noms_ei_ecartees={cle_de_nom(n) for n in fiches_ei | variantes} - {""})
    noms_par_ei = {r["id"]: (r["name"], r.get("short_name")) for r in entity_rows}
    colonnes = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    if "event_id" not in colonnes:
        par_acte = {}
    else:
        siren_tit = ("titulaire_siren" if "titulaire_siren" in colonnes
                     else "NULL AS titulaire_siren")
        ids_avis = {e["id"] for e in avis}
        par_siren = acheteurs_par_siren(conn)
        revue_marches = (revue or {}).get("marche", {})
        par_acte: dict = {}
        for m in rows(conn, f"""
                SELECT id, event_id, titulaire_id, {siren_tit}, titulaire_nom,
                       acheteur_id, acheteur_siren
                FROM marches_publics WHERE event_id IS NOT NULL"""):
            if m["event_id"] not in ids_avis:
                continue
            # Comme `etape_finances` : acheteur rattaché par SIREN, puis la revue.
            if m["acheteur_id"] is None:
                m["acheteur_id"] = par_siren.get(siren_titulaire(m["acheteur_siren"]))
            m = appliquer_revue(m, revue_marches.get(m["id"])) or m
            if titulaire_a_taire(m, siren_titulaire(m.get("titulaire_siren")), public_ids,
                                 **regle):
                mots = set(mots_du_nom(m.get("titulaire_nom")))
                for nom in noms_par_ei.get(m.get("titulaire_id"), ()):
                    mots |= mots_du_nom(nom)
                par_acte.setdefault(m["event_id"], set()).update(mots)
    if motif is None and not par_acte:
        return
    for e in avis:
        isoles = frozenset(par_acte.get(e["id"], ()))
        for champ in ("title", "note_revue"):
            if isinstance(e.get(champ), str):
                e[champ] = masquer_noms(e[champ], motif, isoles)


def siren_titulaire(valeur) -> str:
    """`siren_de` (neuf chiffres, ou "") sans charger le collecteur à l'import."""
    from collectors.marches_publics import siren_de
    return siren_de(valeur)


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


def motif_hors_deduplication(f: dict, actes_de_rubrique, commune_id) -> str | None:
    """Pourquoi un flux revu par l'atelier n'atteint pas `dedupliquer_flux`, en
    ne regardant que LUI (pas ses extrémités, que deux jumeaux partagent) ; None
    s'il l'atteint. Dans l'ordre d'`etape_flux`, qui en tire ses compteurs :
    - `lu_dans_une_rubrique_de_decisions` : flux lu dans la rubrique d'un
      procès-verbal où l'exécutif rend compte de ses décisions par délégation.
      C'est une décision du maire ou du président, qui n'est pas publiée depuis
      un PV (Julien, 14 et 15/11/2026), ni par son titre ni par son montant
      (relecture vigie-securite du 15/11) ;
    - `private_confidence` : confiance hors `RULES["confidence"]["public"]` ;
    - `private_cession` : cession strictement privée (aucune des parties n'est
      la commune), mutation commerciale (fonds de commerce), hors périmètre.
      La commune est résolue par le nom (`_commune_entity_id`) : `63` était le
      numéro de ligne de la commune dans la base de Lasalle ; sur une autre base
      il désignait une entité quelconque.
    Lue aussi par `ids_retenus_par_un_fait` : seul un jumeau qui atteint la
    déduplication peut y être gardé à la place d'un autre (relecture de #323)."""
    if actes_de_rubrique and f.get("event_id") in actes_de_rubrique:
        return "lu_dans_une_rubrique_de_decisions"
    if f.get("confidence") not in set(RULES["confidence"]["public"]):
        return "private_confidence"
    if (str(f.get("type", "")).startswith("cession")
            and commune_id not in (f.get("from_id"), f.get("to_id"))):
        return "private_cession"
    return None


def etape_flux(conn, revue, entity_rows, public_person_ids, public_ids,
               ecartees_du_perimetre, redige, exclusions, public_events,
               pieces_des_fragments=None, actes_de_rubrique=frozenset(),
               *, ei_ids, ei_nommees) -> dict:
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

    # Rubrique de décisions, confiance, cession privée : `motif_hors_deduplication`,
    # lue aussi par `ids_retenus_par_un_fait`. Mêmes compteurs qu'avant.
    COMMUNE_ID = _commune_entity_id(conn)
    motifs = Counter()
    public_flows = []
    for f in flow_rows:
        motif = motif_hors_deduplication(f, actes_de_rubrique, COMMUNE_ID)
        if motif:
            motifs[motif] += 1
        else:
            public_flows.append(f)
    if actes_de_rubrique:
        exclusions["flows"]["lu_dans_une_rubrique_de_decisions"] = \
            motifs["lu_dans_une_rubrique_de_decisions"]
        # `flow_rows` sort de l'étape (`flows_total_private`) SANS la rubrique,
        # comme avant : elle n'est publiée ni par son titre ni par son montant.
        flow_rows = [f for f in flow_rows if f.get("event_id") not in actes_de_rubrique]
    exclusions["flows"]["private_confidence"] = motifs["private_confidence"]
    if COMMUNE_ID is None and flow_rows:
        # Sans elle, aucun flux n'a de sens et toute cession passe pour
        # privée : le dire, plutôt que publier des zéros.
        print("⚠ entité « Commune de … » introuvable : sens des flux non "
              "déterminé, cessions communales écartées", file=sys.stderr)
    exclusions["flows"]["private_cession"] = motifs["private_cession"]

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
    from scripts.snapshot.fiches import parties_publiques
    publiques = parties_publiques(conn)
    ei_sans_fiche = (set(ei_ids) - set(public_ids)) & set(ecartees_du_perimetre)
    # Les empreintes des noms d'EI sans fiche, nommées comprises : un bout NON
    # PUBLIÉ qui en porte une (un doublon `business` « Jeanne Doublon », classé
    # C2, sans forme ni lien) perd son nom, comme `titulaire_a_taire` le fait
    # pour les marchés ; sinon le contrôleur refusait l'instance (tour 7).
    from scripts.snapshot.fiches import cle_de_nom, noms_des_ei_ecartees
    _fiches, _variantes = noms_des_ei_ecartees(conn, entity_rows,
                                               set(ei_ids) - set(public_ids), public_ids)
    cles_a_taire = {cle_de_nom(n) for n in _fiches | _variantes} - {""}
    before = len(public_flows)
    gardes, delies = [], 0
    for f in public_flows:
        etat = statut_extremites(f, public_ids, ecartees_du_perimetre)
        # Une EI sans fiche et une partie publique elle-même déliée (écartée pour
        # le périmètre, rejetée à l'atelier) : aucune extrémité n'est publiée,
        # mais c'est de l'argent public ; il sort délié des deux côtés, montant
        # compté. Depuis l'avis vigie-securite du 25/11/2026 (point 1), une telle
        # partie publique ne retient plus l'EI (`extremites_du_flux_ok`).
        bouts = (f.get("from_id"), f.get("to_id"))
        if (etat == "ecarte" and any(b in ei_sans_fiche for b in bouts)
                and all(b in ei_sans_fiche
                        or (b in publiques and b in ecartees_du_perimetre) for b in bouts)
                and not all(b in ei_sans_fiche for b in bouts)):
            etat = "delie"
        if etat == "ecarte":
            continue
        # Le côté EI se relève AVANT le déliement : `delier_extremites` met l'id à
        # `None` en gardant le nom, et le masque d'une EI hors périmètre ou
        # rejetée à l'atelier ne retrouverait plus son côté (avis vigie-securite).
        f["_cote_ei"] = [c for c in ("from", "to") if f.get(f"{c}_id") in ei_ids]
        # Le côté EI qui n'a pas de fiche publiée : son nom ne sort pas non plus,
        # sauf une EI C2 à qui la COMMUNE verse (`ei_nommees`, décision de Julien
        # du 25/11/2026) : sur ce flux, son nom reste, sans lien.
        nommee_ici = lambda c: (c == "to" and f.get("to_id") in ei_nommees
                                and COMMUNE_ID is not None and f.get("from_id") == COMMUNE_ID)
        f["_ei_sans_fiche"] = [
            c for c in f["_cote_ei"] if f.get(f"{c}_id") not in public_ids
            and not nommee_ici(c)]
        f["_nom_a_taire"] = [
            c for c in ("from", "to")
            if f.get(f"{c}_id") is not None and f.get(f"{c}_id") not in public_ids
            and c not in f["_cote_ei"] and not nommee_ici(c)
            and cle_de_nom(f.get(f"{c}_name")) in cles_a_taire]
        # Un flux vers ou depuis une EI sans fiche ne sort (délié) que si l'autre
        # extrémité est une PARTIE PUBLIQUE (`parties_publiques`) : c'est de
        # l'argent public, il reste compté. Entre une association ou une société
        # et une EI écartée, ce n'est pas un fait de la commune : écarté (brief
        # `ei-flux-marches`, 24/11/2026). Compté dans `endpoint_not_public`.
        if f["_ei_sans_fiche"]:
            autres = [f.get(f"{c}_id") for c in ("from", "to") if c not in f["_ei_sans_fiche"]]
            if not autres or not all(a in publiques for a in autres):
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
    from scripts.snapshot.actes import _DESIGNE_UNE_PERSONNE
    titres = {e.get("id"): e.get("title") or "" for e in public_events}

    def _sera_masque(f) -> bool:
        # Comme le masque final : la description passe par `redige` (« Jeanne
        # EXEMPLE » devient « un particulier »), la note par
        # `redige(champ_publiable(…))`, l'expression de la sortie plus bas (audit
        # du filtre du 24/11/2026, P1 ; même lecture dans `ids_retenus_par_un_fait`).
        note = (redige(champ_publiable(f["note_revue"], None, set()))
                if f.get("note_revue") else None)
        return bool(f.get("_cote_ei")) and any(
            _DESIGNE_UNE_PERSONNE.search(t or "")
            for t in (redige(f.get("description")), note,
                      titres.get(f.get("event_id"), "")))

    public_flows = dedupliquer_flux(public_flows, _sera_masque)
    exclusions["flows"]["duplicates"] = before - len(public_flows)

    # `sens` : la DGF encaissée par la commune (489 690 €) et la subvention
    # versée au Comité des fêtes (4 400 €) sortaient avec la même mise en
    # forme. Sans le sens du flux, la page se lit à contresens.
    # Un flux délié a perdu son identifiant (`None`) : tant que COMMUNE_ID
    # valait `None` lui aussi, il sortait « entrant ».
    # Le nom d'une EI non publiée ne sort pas non plus d'un texte libre : une
    # description peut nommer l'EI d'un autre flux (avis du 25/11/2026).
    # Toutes les EI sans fiche, nommées comprises (avis vigie-securite A2).
    from scripts.snapshot.fiches import (mots_des_exploitants, mots_des_fiches_publiees,
                                         noms_des_ei_ecartees)
    from scripts.snapshot.revue import ecartes
    motif = motif_des_noms(
        noms_des_ei_ecartees(conn, entity_rows, set(ei_ids) - set(public_ids), public_ids),
        mots_des_fiches_publiees(conn, public_ids, mots_exclus=mots_des_exploitants(
            conn, ecartes(revue, "relation"), set(ei_ids) - set(public_ids), public_ids)))
    noms_par_id = {r["id"]: (r["name"], r.get("short_name")) for r in entity_rows}
    for f in public_flows:
        motif_f = avec_extremites(motif, f, ("from_id", "to_id"), noms_par_id, public_ids)
        f["description"] = masquer_noms(redige(f.get("description")), motif_f)
        if f.get("note_revue"):
            f["note_revue"] = masquer_noms(
                redige(champ_publiable(f["note_revue"], None, set())), motif_f)
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

    # Un flux qui touche une EI est DÉLIÉ de l'EI dans deux cas, sans disparaître
    # des totaux : (1) l'EI n'a pas de fiche publiée (écartée faute de fait public,
    # hors périmètre, rejetée à l'atelier) ; (2) la description, la note ou l'acte
    # publié dit « un particulier » (le nom, avec le montant, l'année et la pièce,
    # ré-identifierait le particulier que le titre masque). Restent publiés : le
    # montant, l'année, le type, la partie publique, et l'acte s'il est publié.
    # Sont effacés : l'identifiant et le nom du côté EI, la description et la note
    # de revue (avis vigie-securite du 24/11).
    for f in public_flows:
        cote_ei = f.pop("_cote_ei", [])
        sans_fiche = f.pop("_ei_sans_fiche", [])
        for c in f.pop("_nom_a_taire", []):
            f[f"{c}_name"] = None
        masque = bool(cote_ei) and (
            _DESIGNE_UNE_PERSONNE.search(f.get("description") or "")
            or _DESIGNE_UNE_PERSONNE.search(f.get("note_revue") or "")
            or _DESIGNE_UNE_PERSONNE.search(titres.get(f.get("acte_id"), "")))
        cotes = cote_ei if masque else sans_fiche
        if cotes:
            for c in cotes:
                f[f"{c}_id"] = None
                f[f"{c}_name"] = None
            f["description"] = None
            f.pop("note_revue", None)

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
                   noms_publics, exclusions, *, ei_ids, ei_nommees) -> dict:
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
    colonnes_mp = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    # `montant_base` (HT ou TTC) depuis le 04/10/2026 : un marché lu dans un
    # procès-verbal porte l'un ou l'autre. Absente d'une base plus ancienne.
    base_mp = "montant_base" if "montant_base" in colonnes_mp else "NULL AS montant_base"
    # La confiance sort avec la ligne : le contrôleur la vérifie quand il la
    # voit, et il ne la voyait pas — le filtre (après la revue, plus bas) n'avait
    # aucun témoin.
    confiance_mp = ", confidence" if "confidence" in colonnes_mp else ""
    siren_tit = ("titulaire_siren" if "titulaire_siren" in colonnes_mp
                 else "NULL AS titulaire_siren")
    marches_data = rows(conn, f"""
        SELECT id, acheteur_id, acheteur_siren, acheteur_nom, titulaire_id, titulaire_nom,
               {siren_tit},
               objet, nature, procedure, montant, {base_mp}, cpv_label, date_notif,
               lieu_exec, source, source_url{confiance_mp}
        FROM marches_publics ORDER BY date_notif DESC, montant DESC
    """)
    # AVANT `rattacher_acheteurs`, qui retire `acheteur_siren` : marqué après,
    # aucun marché ne l'était (constaté le 02/11/2026 sur Saillans en ligne).
    marquer_marches_avant_fusion(marches_data, SIRENS_AVANT_FUSION)
    rattacher_acheteurs(marches_data, acheteurs_par_siren(conn))
    for m in marches_data:
        if m.get("montant_base") is None:
            m.pop("montant_base", None)
    revue_marches = revue.get("marche", {})
    # `rejete_en_atelier` garde son sens d'avant : les marchés écartés à
    # l'atelier parmi ceux que leur confiance EN BASE aurait publiés. Un
    # marché `probable` écarté n'y est pas compté (relecture de #323).
    revus = []
    rejetes = 0
    for m in marches_data:
        corrige = appliquer_revue(m, revue_marches.get(m["id"]))
        if corrige is None:
            rejetes += confiance_marche_publique(m)
        else:
            revus.append(corrige)
    marches_data = revus
    exclusions["marches"]["rejete_en_atelier"] = rejetes
    # Le filtre de confiance manquait ici, alors qu'il s'applique partout
    # ailleurs : la table publiait TOUT. Un marché dont l'acheteur n'a pas
    # pu être établi affirmait donc qu'une collectivité avait acheté ce
    # qu'elle n'avait pas acheté. `probable` reste en base et attend
    # l'atelier ; il ne sort pas.
    # APRÈS la revue (`collectors/verdict.py` : verdict, corrections, puis
    # confiance) : un marché `probable` validé à l'atelier sort, un marché
    # rétrogradé ne sort pas (audit du 25/11/2026, H1 et H2). En SQL, le filtre
    # passait avant : le premier restait dehors, le second sortait `probable` et
    # faisait refuser toute l'instance. Même règle dans `ids_retenus_par_un_fait`.
    marches_data = [m for m in marches_data if confiance_marche_publique(m)]
    # Ce qui est écrit par un acheteur, lu dans un procès-verbal ou saisi à
    # l'atelier passe par le masque, comme le titre d'un acte.
    for m in marches_data:
        for champ in ("objet", "lieu_exec"):
            m[champ] = champ_publiable(m[champ], m.get("date_notif"), noms_publics)
        if m.get("note_revue"):
            m["note_revue"] = champ_publiable(m["note_revue"], None, noms_publics)

    # Le marché reste, le lien vers une fiche non publiée tombe ; le nom d'une
    # EI non publiée aussi, dans le titulaire comme dans le texte libre.
    from scripts.snapshot.fiches import (cle_de_nom, mots_des_exploitants,
                                         mots_des_fiches_publiees, mots_du_nom,
                                         noms_des_ei_ecartees)
    from scripts.snapshot.revue import ecartes
    # TOUTES les EI sans fiche, celles que la commune désigne (`ei_nommees`)
    # comprises : leur nom ne reste que sur le marché dont la commune est
    # l'acheteur et qui les désigne par identifiant (`delier_renvois_morts`) ;
    # sur un autre marché, par SIREN ou par nom, il tombe (avis vigie-securite
    # du 25/11/2026, A1).
    ei_ecartees = set(ei_ids) - set(public_ids)
    lignes_ei = rows(conn, """
        SELECT e.id, e.name, e.short_name, b.siren FROM entities e
        LEFT JOIN businesses b ON b.entity_id = e.id""")
    lignes_ei = [r for r in lignes_ei if r["id"] in ei_ecartees]
    sirens_ei_ecartees = {siren_titulaire(r["siren"]) for r in lignes_ei} - {""}
    fiches_ei, variantes = noms_des_ei_ecartees(conn, lignes_ei, ei_ecartees, public_ids)
    noms = fiches_ei | variantes
    exclusions["marches"]["renvoi_vers_fiche_non_publiee"] = delier_renvois_morts(
        marches_data, public_ids, ei_ids=ei_ids, sirens_ei_ecartees=sirens_ei_ecartees,
        noms_ei_ecartees={cle_de_nom(n) for n in noms} - {""},
        ei_nommees=ei_nommees, commune_id=_commune_entity_id(conn))
    motif = motif_des_noms((fiches_ei, variantes), mots_des_fiches_publiees(
        conn, public_ids, mots_exclus=mots_des_exploitants(
            conn, ecartes(revue, "relation"), set(ei_ids) - set(public_ids), public_ids)))
    noms_par_ei = {r["id"]: (r["name"], r["short_name"]) for r in lignes_ei}
    noms_publies = {e["id"]: (e.get("name"), e.get("short_name")) for e in public_entities}
    for m in marches_data:
        isoles = m.pop("_mots_retires", set())
        for nom in noms_par_ei.get(m.pop("_titulaire_ei", None), ()):
            isoles |= mots_du_nom(nom)
        motif_m = avec_extremites(motif, m, ("titulaire_id", "acheteur_id"), noms_publies,
                                  public_ids)
        for champ in ("objet", "lieu_exec", "note_revue"):
            if m.get(champ):
                m[champ] = masquer_noms(m[champ], motif_m, frozenset(isoles))
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
