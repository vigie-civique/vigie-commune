"""La couverture : ce que le snapshot couvre, source par source, et depuis quand.

`couverture.json` dit pour chaque collecteur sa dernière passe et le rythme
qu'on en attend, et pour chaque période les actes qu'on a — ce que la page
`/couverture` publie pour qu'une lacune se voie au lieu de se deviner.
"""
from __future__ import annotations

from datetime import datetime

# Le rythme attendu de chaque collecteur. La page `/couverture` le publie :
# sans lui, elle jugeait tout le monde au même seuil (45 jours), et la source
# qui bouge le plus — le site municipal, déclaré à 3 jours — était celle que ce
# seuil couvrait le moins.
from collectors.config import SOURCES_NON_CONFIGUREES, STEP_META
from collectors.marches_extraits import releve as releve_extraits
from scripts.snapshot.socle import rows, table_exists, write_json


# Qui peut verser de l'argent public à un acteur, et quels collecteurs le
# relèvent. Une fiche d'association sans subvention du département ne disait
# pas qu'AUCUN collecteur ne lit le département : le lecteur concluait à
# l'absence d'aide, alors qu'il s'agit d'une absence de collecte
# (refonte du contenu, défaut 4). Publiée une fois ici, pas dans chaque
# fiche : la table est la même pour toutes.
FINANCEURS = (
    ("commune", "La commune", ("cm", "cm_archive", "subv_ouvertes")),
    ("intercommunalite", "L'intercommunalité", ("cc_epci", "subv_ouvertes")),
    ("departement", "Le département", ()),
    ("region", "La région", ("region",)),
    ("etat", "L'État", ("jaune", "fdva", "subventions")),
)


def financeurs(collecteurs: dict) -> list[dict]:
    """Pour chaque financeur : relevé ou non, et par quoi.

    `releve` : un de ses collecteurs a déjà rapporté quelque chose sur cette
    instance. Sans collecteur, ou sans passe utile, le financeur n'est pas
    relevé — et une fiche qui n'en montre rien ne dit rien de lui.
    """
    sortie = []
    for cle, libelle, noms in FINANCEURS:
        utiles = [n for n in noms if (collecteurs.get(n) or {}).get("a_rapporte")]
        sortie.append({"financeur": cle, "libelle": libelle,
                       "collecteurs": list(noms), "releve": bool(utiles)})
    return sortie


def lacune_documents(avec_piece: int, total: int, deliberations: int | None = None) -> str:
    """La lacune « documents des délibérations », chiffrée sur l'instance.

    Sans délibération relevée, les actes comptés sont des permis, des annonces
    légales, des marchés : dire que leur lien « mène au compte rendu de la
    séance » était faux (relevé national de Vallouise-Pelvoux, 01/11/2026,
    « 1 014 actes sur 1 014 » sans un seul compte rendu).
    """
    if deliberations == 0:
        return ("Aucune délibération n'a été relevée pour ce territoire. Les actes "
                "repris ici viennent de registres nationaux (urbanisme, annonces "
                "légales, marchés, élections), pas des comptes rendus du conseil.")
    sans = total - avec_piece
    sans_fr, total_fr = (f"{n:,}".replace(",", " ") for n in (sans, total))
    return (f"Pour {sans_fr} acte{'s' if sans > 1 else ''} sur {total_fr}, "
            "le lien mène au compte rendu de la séance, et non à la délibération "
            "elle-même. Il faut donc chercher le passage dans le document.")


def lacune_decisions(public_events: list[dict]) -> dict:
    """Ce que le site dit des décisions prises par délégation, selon ce qu'il publie.

    Une phrase écrite d'avance devient fausse le jour où la collecte change :
    c'est ce que l'avis DPO du 14/11/2026 relevait sur la première version.
    """
    publiees = sum(1 for e in public_events
                   if e.get("type") in ("decision_delegation", "decision_delegation_cc"))
    commun = (" Elles ne sont jamais comptées parmi les délibérations.")
    if not publiees:
        return {"sujet": "Décisions prises par délégation", "etat": "absent",
                "detail": ("Les décisions que le maire ou le président de l'intercommunalité "
                           "prennent par délégation du conseil ne sont pas publiées une à une."
                           + commun)}
    return {"sujet": "Décisions prises par délégation", "etat": "partiel",
            "detail": (f"{publiees} décision(s) prises par délégation sont publiées, en titre "
                       "seul, quand le portail d'actes de la collectivité les publie à part. "
                       "Les autres ne sont que comptées : celles qui touchent ou peuvent "
                       "toucher un particulier (renonciation à préempter, concession "
                       "funéraire, logement loué, aide individuelle, contentieux, personnel, "
                       "objet qui nomme une personne), et celles qu'un tri automatique ne "
                       "sait pas classer, par prudence. Celles que le maire mentionne dans "
                       "un procès-verbal ne sont pas encore publiées." + commun)}


def export_couverture(conn, public_events: list[dict], stats: dict) -> dict:
    """Ce que la collecte couvre, et surtout ce qu'elle ne couvre pas.

    Un observatoire qui n'affiche que ce qu'il sait ressemble à une boîte
    noire : le lecteur ne peut pas distinguer « il ne s'est rien passé en
    2019 » de « nous n'avons pas collecté 2019 ». Publier les trous coûte peu
    et vaut mieux que de paraître complet.

    Trois choses différentes, à ne pas confondre :
      - la PÉRIODE réellement couverte par source ;
      - la FRAÎCHEUR, c'est-à-dire la dernière collecte et son issue ;
      - les EXCLUSIONS délibérées (périmètre, vie privée), qui ne sont pas
        des lacunes mais des choix, et qui sont déjà dans `stats`.
    """
    # La période couverte s'arrête à la date d'arrêt : un concert annoncé pour
    # le 14/11 faisait « couvrir » lasalle.fr jusqu'en novembre, deux mois après
    # la collecte (audit du 24/09/2026). L'agenda à venir est compté à part.
    arret = (stats.get("generated_at") or datetime.now().isoformat())[:10]
    par_source: dict[str, dict] = {}
    for e in public_events:
        src = e.get("source") or "inconnue"
        d = par_source.setdefault(src, {"source": src, "actes": 0,
                                        "debut": None, "fin": None,
                                        "avec_document": 0,
                                        "a_venir": 0, "annonce_jusqu_au": None})
        d["actes"] += 1
        if e.get("document") == "acte":
            d["avec_document"] += 1
        date = e.get("date")
        if date and date[:10] > arret:
            d["a_venir"] += 1
            if d["annonce_jusqu_au"] is None or date > d["annonce_jusqu_au"]:
                d["annonce_jusqu_au"] = date
        elif date:
            if d["debut"] is None or date < d["debut"]:
                d["debut"] = date
            if d["fin"] is None or date > d["fin"]:
                d["fin"] = date

    # Dernier passage de chaque collecteur : un collecteur muet depuis des mois
    # est une lacune en formation, pas encore visible dans les compteurs.
    derniers = {}
    if table_exists(conn, "collector_runs"):
        for r in rows(conn, """
            SELECT collector, status, MAX(started_at) AS dernier,
                   SUM(CASE WHEN status = 'ok' OR items_added > 0 THEN 1 ELSE 0 END)
                       AS passes_utiles
            FROM collector_runs GROUP BY collector
        """):
            derniers[r["collector"]] = {
                "statut": r["status"],
                "dernier": r["dernier"],
                # `statut` est celui de la DERNIÈRE passe : un collecteur
                # incrémental qui n'a rien trouvé de neuf vaut `empty` alors
                # que ses lignes sont publiées — `marches` valait `empty` sur
                # les trois instances à côté de 58 marchés (04/10/2026). Un
                # zéro se juge sur « a déjà rapporté », pas sur la dernière fois.
                "a_rapporte": bool(r["passes_utiles"]),
                # Le seuil vient de la source unique de vérité, jamais d'un
                # nombre écrit dans la page : un collecteur dont on change le
                # rythme change de seuil le jour même.
                "ttl": STEP_META[r["collector"]][0] if r["collector"] in STEP_META else None,
            }
    # Un step sans source déclarée : ni `empty` (« interrogé, rien »), ni un
    # échec. Il est dit à part, avec sa raison, qu'il ait tourné ou non.
    for nom, raison in SOURCES_NON_CONFIGUREES.items():
        derniers.setdefault(nom, {"statut": None, "dernier": None,
                                  "a_rapporte": False, "ttl": None})
        derniers[nom]["non_configure"] = raison

    doc = stats.get("provenance", {}).get("document", {})
    total_actes = sum(doc.values()) or 1

    return {
        "arrete_le": stats.get("generated_at"),
        "sources": sorted(par_source.values(), key=lambda d: -d["actes"]),
        "collecteurs": derniers,
        "financeurs": financeurs(derniers),
        # Ce qui a été lu dans les textes et attend une relecture : des
        # NOMBRES, jamais les lignes (décision 6 du 04/10/2026). C'est ce qui
        # permet à /marches de dire « 77 attributions lues, en attente » au
        # lieu d'un zéro nu. None : aucun rapport n'a jamais été déposé.
        "extraits": {"marches": releve_extraits(conn)},
        # Les années que les consolidés DECP lus couvrent, et celles qui
        # manquent entre la première et l'année d'arrêt : un « aucun marché »
        # ne vaut que pour les premières.
        "decp_millesimes": millesimes_decp(conn, int(arret[:4])),
        # Le chiffre le plus inconfortable du site, donc celui qu'il faut donner
        # en premier : la proportion d'actes dont la pièce elle-même est
        # consultable, par opposition à la page qui la contient.
        "actes_avec_piece": doc.get("acte", 0),
        "actes_total": total_actes,
        "part_avec_piece": round(100 * doc.get("acte", 0) / total_actes, 1),
        "lacunes_connues": [
            {
                "sujet": "Documents des délibérations",
                "etat": "absent" if stats.get("deliberations_public") == 0 else "partiel",
                # Le chiffre, pas un adjectif : « la très grande majorité » est
                # restée écrite ici quand 74 % des actes de Lasalle menaient déjà
                # à leur pièce, sous le pourcentage qui disait l'inverse (28/10/2026).
                "detail": lacune_documents(doc.get("acte", 0), sum(doc.values()),
                                           stats.get("deliberations_public")),
            },
            {
                "sujet": "Mandatures antérieures à 2020",
                "etat": "incomplet",
                "detail": ("L'historique des mandats est lacunaire avant 2020, ce qui empêche "
                           "de dire si une personne était élue à la date d'un versement "
                           "ancien. Les situations concernées sont signalées comme telles."),
            },
            {
                "sujet": "Dirigeants d'associations",
                "etat": "incomplet",
                "detail": ("Aucune source ouverte ne publie les dirigeants d'associations. "
                           "Ceux qui figurent ici proviennent de documents publics les "
                           "nommant, jamais d'un registre exhaustif."),
            },
            {
                "sujet": "Recoupement entre sources",
                "etat": "absent",
                "detail": ("Aucune donnée n'est aujourd'hui confirmée par deux sources "
                           "indépendantes : la chaîne ne sait pas encore le faire."),
            },
            # Ce qu'une commune et son intercommunalité décident hors des
            # délibérations, et que le site ne publie pas encore (Julien,
            # 14/11/2026 : « signaler dans /couverture »). Le titre de la page
            # promet des décisions votées en conseil ; le reste du pouvoir
            # local doit être dit absent, pas laissé deviner.
            lacune_decisions(public_events),
            {
                "sujet": "Arrêtés",
                "etat": "absent",
                "detail": ("Les arrêtés du maire et du président de l'intercommunalité "
                           "(stationnement, police, voirie, personnel…) ne sont pas encore "
                           "collectés."),
            },
            {
                "sujet": "Syndicats intercommunaux",
                "etat": "absent",
                "detail": ("Les actes des syndicats dont la commune est membre (eau, "
                           "déchets, énergie, écoles…) ne sont pas suivis. Ils décident "
                           "pourtant de services et de dépenses qui la concernent."),
            },
            {
                "sujet": "Commissions",
                "etat": "incomplet",
                "detail": ("La composition des commissions est relevée dans les procès-verbaux "
                           "et ne sort qu'après relecture. Un membre non élu n'a pas de fiche à "
                           "ce titre ; le texte de l'acte qui le désigne reste cité. Les travaux des commissions "
                           "(ordres du jour, comptes rendus) ne sont pas publiés ici."),
            },
            *sources_absentes(conn),
        ],
    }


def millesimes_decp(conn, annee_arret: int) -> dict | None:
    """`{"lus": [...], "absents": [...]}`, ou None si aucune passe complète
    n'a encore relevé les millésimes (collectors/marches_publics.py)."""
    if not table_exists(conn, "decp_millesimes"):
        return None
    # Bornée ici aussi : une base relevée avant la borne du collecteur peut
    # porter une année aberrante, qui ouvrirait la liste des absents.
    lus = sorted(r["millesime"] for r in rows(
        conn, "SELECT millesime FROM decp_millesimes WHERE fichiers > 0"
              " AND millesime BETWEEN 2000 AND ?", (annee_arret,)))
    if not lus:
        return None
    return {"lus": lus,
            "absents": [a for a in range(lus[0], annee_arret + 1) if a not in lus]}


def sources_absentes(conn) -> list[dict]:
    """Les jeux nationaux qui ne couvrent pas une commune suivie.

    Relevées par le collecteur lui-même (`collectors/absences.py`) : sans elles,
    une rubrique vide ne distinguait pas « la source ne parle pas de la
    commune » de « la collecte n'a pas tourné » (décision du 29/10/2026).
    """
    if not table_exists(conn, "sources_absentes"):
        return []
    return [{
        "sujet": r["jeu"],
        "etat": "absent",
        "detail": (f"Le fichier lu (millésime {r['millesime']}) ne contient pas la commune "
                   f"{r['insee']} : ce jeu ne dit rien d'elle, et ce n'est pas un défaut de la collecte."),
        "insee": r["insee"],
        "step": r["step"],
        "page_jeu": r["page_jeu"],
        "source_url": r["source_url"],
    } for r in rows(conn, """
        SELECT step, insee, jeu, page_jeu, source_url, millesime
          FROM sources_absentes ORDER BY step, insee
    """)]


def etape_compteurs_provisoires(out, stats) -> None:
    """`stats.json` tel qu'il est à ce point, premier fichier écrit.

    L'étape `stats` le réécrit en dernier, complet : c'est cette version-là
    qui fait foi. Celle-ci n'a jamais servi qu'à ce qu'un snapshot interrompu
    en porte au moins une — elle ne prouve donc pas qu'un snapshot est entier.
    """
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "stats.json", stats)


def etape_couverture(conn, out, public_events, stats) -> None:
    write_json(out / "couverture.json", export_couverture(conn, public_events, stats))
