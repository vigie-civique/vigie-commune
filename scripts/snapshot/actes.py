"""Les actes publiés : délibérations, séances, arrêtés, annonces.

L'étape `actes` lit les événements en base, applique le verdict de l'atelier
et les règles de publication, et rend les actes publiés avec leur détail
délibératif (vote, montant décidé), leur provenance sur trois axes, et le
texte des délibérations qui peut sortir — masqué, cf.
`scripts/snapshot/textes.py`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from collectors.citations import acte_de_ligne, index_de, lignes_en_base
from collectors.cle_acte import cle_de_ligne
from collectors.decisions import COMPTEES as DECISIONS_COMPTEES, TYPES as TYPES_DECISION_PAR_PORTEE
from collectors.decisions import categorie as categorie_decision
from collectors.pv_parsers import (NUMEROTATION_DE_TETE, rubrique_de_decisions, titre_en_debris_de_ponctuation,
                                   titre_en_debris_de_tableau, titre_en_entete_de_tampon,
                                   titre_en_fragment, titre_en_mots_colles,
                                   titre_numero_d_acte_seul, titre_questions_diverses)
from scripts.snapshot.revue import TYPES_REVUS, appliquer_revue
from scripts.snapshot.socle import (RULES, URL_COMMUNE, URL_EPCI, rows, safe_url,
                                    write_json_compact)
from scripts.snapshot.textes import (champ_publiable, convocation_publique,
                                     motif_sans_extrait, nettoyer_titre_evenement,
                                     norm_nom, texte_publiable,
                                     titre_d_acte)


# Ce qu'un conseil a effectivement délibéré — communal et intercommunal. Sert
# le compteur affiché à l'accueil : un site de contrôle de l'action publique
# doit pouvoir dire ce qu'il compte quand il écrit « délibérations ». Les
# séances (`conseil_municipal`, `conseil_communautaire`) sont des contenants,
# pas des décisions : les compter doublerait les actes qu'elles portent.
TYPES_DELIBERES = ("deliberation", "deliberation_cc")
# L'événement ombrelle d'une séance. Il n'est pas un acte : il ne décide rien,
# il rassemble ce qui a été décidé ce jour-là, et porte les pièces qui
# l'attestent — convocation, registre, procès-verbal.
TYPES_SEANCE = ("conseil_municipal", "conseil_communautaire")
# Les décisions prises par délégation du conseil (`collectors/decisions.py`) :
# des actes, pas des délibérations. Titre seul, jamais de texte ; certaines
# catégories ne sortent qu'en nombre (avis DPO du 14/11/2026, Julien le 15/11).
TYPES_DECISION = tuple(TYPES_DECISION_PAR_PORTEE.values())
_MONTANT_TITRE = re.compile(r"(\d{1,3}(?:[ \u00a0.]\d{3})+|\d+)(?:,\d+)?\s*(?:€|euros?\b)", re.I)


def titre_de_preemption(objet: str | None) -> str:
    """Le titre publié d'une préemption exercée : ni nom, ni parcelle.

    L'objet d'une décision de préemption donne la parcelle cadastrale, et la
    parcelle donne le propriétaire. On garde le fait et son montant, quand
    l'objet l'écrit ; le lieu n'est pas extrait, faute de pouvoir le distinguer
    d'une référence cadastrale ou d'une adresse.
    """
    m = _MONTANT_TITRE.search(objet or "")
    return "Exercice du droit de préemption sur un bien" + (
        f" ({m.group(0).strip()})" if m else "")


_DESIGNE_UNE_PERSONNE = re.compile(r"\b(un particulier|une particuli[eè]re|un agent|une agente"
                                   r"|une personne)\b", re.I)


def categorie_publiee(metadata: dict, titre: str | None) -> str:
    """La catégorie qui décide de la sortie : la plus restrictive entre celle de
    la collecte et celle du titre relu aujourd'hui.

    Une catégorie figée à la collecte survivrait à la correction des motifs : le
    portail retire la pièce, la décision n'est plus recollectée. Une métadonnée
    absente ne vaut pas « publiable ».
    """
    stockee = metadata.get("categorie_delegation")
    if not stockee or stockee in DECISIONS_COMPTEES:
        return stockee or "non_classee"
    return categorie_decision(titre or "")


def _derniere_passe(event_rows: list[dict]) -> dict[str, str]:
    """Pour chaque type de décision, le jour de la dernière passe du collecteur."""
    passes: dict[str, str] = {}
    for e in event_rows:
        if e.get("type") in TYPES_DECISION:
            try:
                vu = json.loads(e.get("metadata") or "{}").get("vu_le") or ""
            except json.JSONDecodeError:
                continue
            passes[e["type"]] = max(passes.get(e["type"], ""), vu)
    return passes


# Fourchette de montants publiables pour un acte local : en dessous, c'est un
# numéro d'article pris pour un euro ; au-dessus, une concaténation OCR.
MONTANT_MIN = 10
MONTANT_MAX = 50_000_000


# Ce qu'un montant est, dans une délibération.
#
# Le contexte de chaque montant est capté à l'extraction (80 caractères avant,
# 40 après) : il dit souvent ce que le chiffre représente. Une délibération de
# subvention écrit « Montant demandé : 2 200 € ; Montant attribué : 500 € » —
# retenir le plus élevé, c'est publier la DEMANDE comme si le conseil l'avait
# votée. C'est ce qui affichait « 2 200 € » sur un acte où 100 € ont été votés.
DEMANDE_RE = re.compile(
    r"(demand\w+|sollicit\w+|estim\w+|propos\w+|devis|prévisionnel\w*)", re.I)
ACCORDE_RE = re.compile(
    r"(attribu\w+|accord\w+|allou\w+|vot\w+|vers\w+|octroi\w+)", re.I)


def _sans_accents(t: str) -> str:
    return unicodedata.normalize("NFKD", (t or "").lower()) \
        .encode("ascii", "ignore").decode()


def montant_de_la_decision(montants: list, titre: str | None = None) -> tuple[float | None, list]:
    """Le montant DÉCIDÉ, et les autres — plutôt que le plus gros du texte.

    Trois règles, de la plus sûre à la plus faible :

    1. si des montants sont dits ACCORDÉS (« DECIDE d'attribuer … 100 € »),
       la décision est parmi eux ;
    2. sinon, les montants explicitement DEMANDÉS sont écartés du calcul ;
    3. entre plusieurs candidats, celui dont le contexte nomme le titre de
       l'acte l'emporte — un bloc de subventions en contient plusieurs, un par
       association, et le titre dit de laquelle il s'agit.

    À défaut, on retombe sur le plus élevé, comme avant.
    """
    retenus = []
    for m in montants:
        v = m.get("montant", m.get("value")) if isinstance(m, dict) else m
        if not isinstance(v, (int, float)) or not (MONTANT_MIN <= v <= MONTANT_MAX):
            continue
        ctx = m.get("context", "") if isinstance(m, dict) else ""
        retenus.append((v, ctx, bool(ACCORDE_RE.search(ctx)), bool(DEMANDE_RE.search(ctx))))
    if not retenus:
        return None, []

    accordes = [r for r in retenus if r[2] and not r[3]]
    candidats = accordes or [r for r in retenus if not r[3]] or retenus

    cle = _sans_accents(titre or "")
    if len(cle) > 5:
        nommes = [r for r in candidats if cle in _sans_accents(r[1])]
        if nommes:
            candidats = nommes

    principal = max(c[0] for c in candidats)
    return principal, sorted({r[0] for r in retenus}, reverse=True)[:5]


def public_event_detail(metadata: dict, event_type: str, titre: str | None = None) -> dict:
    """Détail publiable d'un événement : vote, montants, thème, ordre du jour.

    Ne sort JAMAIS `personnes_citees` : ce sont des noms bruts extraits par OCR,
    non arbitrés. Les personnes n'apparaissent au public que via les liens
    event_entities, qui ne portent que des entités déjà publiques.
    """
    detail: dict = {}

    # Les parsers successifs n'ont pas écrit le même schéma : `abstention` ou
    # `abstentions`, `montant` ou `value`. On accepte les deux plutôt que de
    # perdre la moitié des décomptes.
    vote = metadata.get("vote")
    if isinstance(vote, dict):
        pour = vote.get("pour")
        contre = vote.get("contre")
        abst = vote.get("abstention", vote.get("abstentions"))
        if any(v is not None for v in (pour, contre, abst)) or vote.get("unanimite"):
            detail["vote"] = {
                "pour": pour, "contre": contre, "abstention": abst,
                "unanimite": bool(vote.get("unanimite")),
            }

    montants = metadata.get("montants")
    if isinstance(montants, list) and montants:
        # Bornes de plausibilité appliquées dans `montant_de_la_decision` :
        # l'extraction OCR rapporte des numéros d'article comme des euros
        # (« 1 € ») et des concaténations de chiffres comme des montants
        # (213 087 450 € pour une communauté de communes dont le budget tient en
        # 10 M€). Publier ces valeurs décrédibilise toute la colonne montants.
        principal, valeurs = montant_de_la_decision(montants, titre)
        if valeurs:
            detail["montant_principal"] = principal
            detail["montants"] = valeurs
            # `montant_principal` est le plus élevé des montants cités, pas le
            # coût de l'acte : sur un document qui porte 20 délibérations, il
            # affichait 2 197 037 € en face d'une ligne, comme si c'était son
            # montant propre. On le signale au lieu de le taire.
            if len(valeurs) > 1 or event_type in {
                    "conseil_municipal", "délibérations_cc", "pv_cc"}:
                detail["montant_indicatif"] = True

    if metadata.get("categorie"):
        detail["categorie"] = metadata["categorie"]
    if isinstance(metadata.get("tags"), list) and metadata["tags"]:
        detail["tags"] = metadata["tags"][:6]

    # RETIRÉ le 21/08/2026 : `ordre_du_jour`, `organisateur` et `lieu` étaient
    # recopiés tels quels depuis `metadata`, sans passer par `redige()` — le
    # masque « un particulier » qui protège `title` et les descriptions de flux.
    # Un ordre du jour porte des noms (« Demande de M. X », « recrutement de
    # Mme Y) ; ces trois champs les auraient publiés en clair.
    #
    # Supprimés plutôt que masqués : aucun collecteur du moteur n'écrit ces
    # clés — 0 événement sur les trois instances, en base comme au snapshot.
    # C'était du code mort qui portait un risque. S'ils reviennent un jour, ils
    # reviendront par `redige()`, comme tout texte publié.
    #
    # Relevé par un audit externe le 21/08/2026.

    if metadata.get("archived_copy"):
        detail["copie_archivee"] = True
        detail["archive_note"] = metadata.get("archive_note")

    return detail


def provenance(event: dict, source: str | None, event_type: str | None,
               pdf_url: str | None, source_url: str | None) -> dict:
    """Les trois axes de provenance d'un acte publié.

    Remplace le label unique `verified` / `confirmed`, retiré le 12/08/2026 :
    ces deux niveaux mélangeaient deux questions indépendantes — d'où vient
    l'information, et combien de sources le disent — sur une seule échelle.
    Empilées ainsi, elles obligeaient à choisir entre deux qualités qui n'ont
    pas de rapport, et `confirmed` n'a de fait jamais été attribué à une seule
    ligne.

    Trois questions séparées, chacune vérifiable par le lecteur :

    - `provenance`   d'où vient l'information ?
    - `document`     peut-il consulter la pièce ?
    - `traitement`   qu'avons-nous fait entre la source et l'affichage ?

    L'axe « concordance » (source unique / concordantes / contradictoires)
    proposé par la critique n'est délibérément PAS produit : rien dans la
    chaîne ne recoupe aujourd'hui deux sources indépendantes. Un axe qui
    vaudrait invariablement « source unique » répéterait exactement l'erreur
    de `confirmed` — annoncer au lecteur une garantie qui n'existe pas.
    """
    regles = RULES.get("provenance", {})

    # Une source inconnue tombe en `secondaire` : le doute joue contre nous.
    origine = regles.get("sources", {}).get(source or "", "secondaire")

    if pdf_url:
        document = "acte"          # la pièce elle-même
    elif source_url:
        document = "page_source"   # la page qui la contient
    else:
        document = "aucun"

    if event.get("corrige"):
        # Une rectification humaine tracée prime : c'est le seul cas où un
        # regard s'est posé sur la donnée.
        traitement = "rectifie"
    elif event_type in set(regles.get("types_extraits", [])):
        traitement = "extraction"  # lu dans un document rédigé — OCR ou LLM
    else:
        traitement = "structure"   # flux structuré, aucune étape d'interprétation

    return {"provenance": origine, "document": document, "traitement": traitement}


# ── Portée d'un acte : la commune, ou l'intercommunalité qui décide pour elle ─
#
# Un site communal qui mélange les deux sur sa page de garde dit au lecteur que
# tout se vaut. Or ce ne sont pas les mêmes élus, pas le même budget, pas le
# même bulletin de vote : c'est la distinction la plus utile qu'un habitant
# puisse faire, et elle disparaissait dans un compteur unique.
#
# Trois valeurs seulement, et la troisième est une honnêteté :
#   commune           la commune l'a décidé, ou l'acte la concerne directement ;
#   intercommunalite  l'EPCI l'a décidé, ou l'acte concerne une autre commune
#                     membre — ce sont des compétences transférées, pas
#                     confisquées, et le lecteur a le droit de les voir à part ;
#   territoire        ni l'un ni l'autre n'agit : une annonce BODACC, une
#                     autorisation d'urbanisme, un fait qui SE PASSE ici sans
#                     que personne d'élu l'ait voté. Les ranger sous « commune »
#                     gonflerait le compteur de l'action publique avec la vie
#                     des entreprises.
PORTEE_PAR_TYPE = {
    "deliberation":          "commune",
    "conseil_municipal":     "commune",
    "deliberation_cc":       "intercommunalite",
    "conseil_communautaire": "intercommunalite",
    "decision_delegation":   "commune",
    "decision_delegation_cc": "intercommunalite",
}


def domaine(url: str | None) -> str:
    """Domaine nu d'une adresse ou d'un libellé de source, sans `www.`."""
    d = (url or "").strip().lower()
    d = d.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return d[4:] if d.startswith("www.") else d


def _annee_de_trace(valeur) -> int | None:
    """Année d'une date ou d'un millésime, None si la valeur n'en porte pas.

    L'ANNÉE, et pas la date : un flux financier n'a que son millésime. Lui
    donner un jour le ferait passer pour plus précis qu'il n'est.
    """
    texte = str(valeur or "")[:4]
    return int(texte) if texte.isdigit() else None


def portee_evenement(event_type: str | None, perimetres: set[str],
                     source: str | None = None) -> str:
    """Portée d'un acte : son assemblée, sinon son éditeur, sinon ses acteurs.

    L'assemblée prime : une délibération du conseil communautaire est
    intercommunale même quand elle ne cite que des acteurs de la commune —
    c'est l'EPCI qui l'a votée.

    Vient ensuite L'ÉDITEUR, et c'est ce qui manquait : les 39 annonces
    d'agenda publiées par la mairie sur son propre site n'ont aucun acteur
    rattaché, et sortaient donc en « territoire ». L'agenda communal a
    disparu de la page de garde le jour où elle est devenue communale — un
    filtre correct sur une donnée incomplète. Ce que la mairie publie
    elle-même concerne la commune, c'est le sens même de le publier.

    En dernier ressort, les acteurs rattachés disent de qui l'acte parle, et
    C1 l'emporte sur C2 : un acte qui touche la commune et une voisine
    intéresse d'abord la commune.
    """
    connue = PORTEE_PAR_TYPE.get(event_type or "")
    if connue:
        return connue
    src = domaine(source)
    if src:
        if src == domaine(URL_COMMUNE):
            return "commune"
        if src == domaine(URL_EPCI):
            return "intercommunalite"
    if "C1" in perimetres:
        return "commune"
    if "C2" in perimetres:
        return "intercommunalite"
    return "territoire"


PORTEE_PAR_PERIMETRE = {"C1": "commune", "C2": "intercommunalite"}


def write_act_extracts(out: Path, textes: dict[int, str]) -> int:
    """Un fichier par délibération publiée : `extrait/<id>.json`, son texte.

    La page d'un millésime ne montrait d'un acte que son titre, et renvoyait au
    PDF de la séance entière — une liasse de quarante pages où retrouver la
    sienne. Le texte de chaque délibération est pourtant en base depuis la
    collecte : le lecteur le déplie désormais sous le titre.

    Un fichier par acte plutôt que le texte dans `events.json` : chaque page de
    millésime embarque ses actes dans son HTML, et les 578 Ko de texte d'une
    seule année y seraient partis pour un lecteur qui n'en ouvre qu'un. Même
    motif que `entite/<id>.json`.

    Le texte est celui que `texte_publiable` rend : les noms y sont, domicile et
    naissance y sont masqués. Il garde la forme et les fautes de l'extraction :
    c'est une LECTURE du document, et la page le dit — la pièce qui fait foi
    reste celle de la collectivité.
    """
    dest = out / "extrait"
    dest.mkdir(parents=True, exist_ok=True)

    # PURGE AVANT ÉCRITURE, pour la raison écrite dans `write_entity_bundles` :
    # un acte retiré de la publication garderait sinon son texte en ligne.
    attendus = {f"{i}.json" for i in textes}
    for f in dest.glob("*.json"):
        if f.name not in attendus:
            f.unlink()

    for i, texte in textes.items():
        write_json_compact(dest / f"{i}.json", {"id": i, "texte": texte})
    return len(textes)


def bodacc_personne_physique(commercant: str | None, indice_contenu: bool) -> bool:
    """Une annonce du BODACC qui vise une PERSONNE, pas une société.

    Le BODACC est une source ouverte, la règle du projet est plus étroite : un
    commerçant individuel ou une liquidation judiciaire ne relèvent d'aucune
    fonction publique (SECURITY.md). Le registre ne porte pas la nature de la
    personne : on la lit à ce que la source écrit — « NOM, Prénom » dans le
    libellé du commerçant, ou « personne physique » / `radiationPP` dans
    l'annonce. Le doute exclut : une société à virgule écartée à tort se
    rattrape, un nom publié à tort ne se rattrape pas.
    """
    return bool(indice_contenu) or "," in (commercant or "")


# L'agenda : ce qu'une commune ou une association ANNONCE au public. Son titre
# n'est pas celui d'un acte, et le filtre des titres d'actes n'a rien à y
# chercher.
TYPES_AGENDA = ("local_event",)


# Les motifs d'un titre de délibération qui n'est pas un objet. Chacun garde, à
# l'exclusion, le renvoi à la pièce qui atteste les flux lus dans l'acte.
MOTIFS_DE_TITRE_DOUTEUX = ("titre_fragment_de_phrase", "titre_mots_colles",
                           "titre_debris_de_tableau", "titre_nom_de_la_commune",
                           "titre_entete_de_tampon", "titre_questions_diverses",
                           "titre_numero_d_acte_seul", "titre_debris_de_ponctuation")


def motif_de_titre_douteux(titre: str, nom_commune: str) -> str | None:
    """Pourquoi ce titre de délibération ne sort pas, ou None.

    Un titre douteux vaut mieux écarté que faux (Julien, 16/11/2026). Le titre
    égal au seul nom de la commune de l'instance (« SAILLANS », Saillans,
    01/10/2024) est l'en-tête d'une page, pas un objet : le nom se lit dans la
    configuration de l'instance, jamais en dur. Un titre court sans débris
    (« MATERNELLES », « PLU ») n'est pas touché. L'en-tête de tampon se reconnaît
    à sa forme, jamais à un lieu (`titre_en_entete_de_tampon`).
    """
    # La rubrique des décisions par délégation (règle de 686afb1) passe avant tout :
    # un titre qu'elle reconnaît reste une rubrique, même à mots collés, et part avec
    # ses flux (`actes_de_rubrique`), pas comme titre douteux.
    if rubrique_de_decisions(titre):
        return None
    if titre_en_fragment(titre):
        return "titre_fragment_de_phrase"
    if titre_en_mots_colles(titre):
        return "titre_mots_colles"
    if titre_en_debris_de_tableau(titre):
        return "titre_debris_de_tableau"
    nom = norm_nom(nom_commune)
    # Une numérotation de tête (« 2) SAILLANS ») n'y change rien.
    sans_numero = NUMEROTATION_DE_TETE.sub("", titre)
    if nom and norm_nom(sans_numero.strip(" \t\n:;,.|¦_()-–—")) == nom:
        return "titre_nom_de_la_commune"
    # L'en-tête de tampon d'un extrait scanné (« DEPARTEMENT : GARD ») et le point
    # « Questions diverses », sans vote : ni l'un ni l'autre n'est un objet.
    if titre_en_entete_de_tampon(titre):
        return "titre_entete_de_tampon"
    if titre_questions_diverses(titre):
        return "titre_questions_diverses"
    # Le numéro d'acte du même tampon, seul, et le guillemet ou la parenthèse
    # orphelins d'un titre coupé : pas un objet, et jamais recollés.
    if titre_numero_d_acte_seul(titre):
        return "titre_numero_d_acte_seul"
    if titre_en_debris_de_ponctuation(titre):
        return "titre_debris_de_ponctuation"
    return None


def titre_de_l_evenement(event_type: str | None, titre_brut: str | None, jour: str | None,
                         noms_publics: set[str], masquages: Counter
                         ) -> tuple[str | None, str | None]:
    """Le titre publié d'un événement, ou `(None, motif)`.

    Le filtre des titres d'actes désigne un particulier par son rôle. Appliqué à
    tout événement, il a réécrit l'agenda : « Sur les traces de Monsieur Castor »,
    une sortie nature, est devenu « Sur les traces d'un particulier » (Lasalle,
    14/11/2026). Une annonce publique nomme qui elle veut faire connaître : son
    titre sort tel que la source l'a écrit.
    """
    titre = nettoyer_titre_evenement(titre_brut)
    if event_type in TYPES_AGENDA:
        return titre, None
    return titre_d_acte(titre, jour, noms_publics, masquages)


# Les assemblées où le recueil fait foi sur le procès-verbal. L'intercommunalité
# n'y est pas : ses doublons recueil / procès-verbal attendent une relecture à
# la main (70 cas incertains, dont des budgets annexes distincts), et la règle
# en écarterait 330 actes à Lasalle sans que personne les ait regardés.
TYPES_OU_LE_RECUEIL_FAIT_FOI = ("deliberation",)


def fragments_de_pv(event_rows: list[dict]) -> dict[int, str | None]:
    """Les actes tirés du procès-verbal d'une séance dont le recueil est publié.

    Une séance publiée deux fois — le recueil des délibérations, qui numérote
    ses actes, et le procès-verbal, qui ne les numérote pas — entrait deux fois.
    Et le découpage du procès-verbal prend les lignes d'un tableau pour des
    actes : au conseil du 28/05/2026 de Lasalle, 63 « délibérations » publiées
    pour 28 au registre, dont « TRANSMISSION, 10 pour, 3 contre » là où le
    procès-verbal écrit « Aucune demande » (relevé du 14/11/2026).

    Quand une séance porte des actes NUMÉROTÉS venus d'un document, ceux qui
    n'ont pour clé que leur titre et viennent d'un AUTRE document ne sortent
    pas : le recueil fait foi. Ils restent en base, pour l'atelier. Une séance
    connue par son seul procès-verbal n'est pas touchée.

    `event_rows` : les lignes qui peuvent sortir (source publique, non rejetées
    à l'atelier). Rend {identifiant: URL du procès-verbal} : un flux lu dans un de ces
    fragments renvoie à la pièce, faute de fiche.
    """
    seances: dict = defaultdict(lambda: {"numerotes": set(), "par_titre": []})
    for e in event_rows:
        try:
            metadata = json.loads(e.get("metadata") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        cle = cle_de_ligne(e.get("type"), e.get("date"), metadata, e.get("title"))
        if cle is None or e.get("type") not in TYPES_OU_LE_RECUEIL_FAIT_FOI:
            continue
        # Sans pièce publiable, ni recueil ni fragment : un acte numéroté dont on
        # ne sait pas le document ne dit pas qu'un recueil existe, et un fragment
        # sans pièce laisserait ses flux sans aucun renvoi.
        document = safe_url(metadata.get("pdf_url") or e.get("source_url"))
        if not document:
            continue
        seance = seances[(e["type"], (e["date"] or "")[:10])]
        if cle.par == "numero":
            seance["numerotes"].add(document)
        elif cle.faible:
            seance["par_titre"].append((e["id"], document))
    return {identifiant: document
            for seance in seances.values() if seance["numerotes"]
            for identifiant, document in seance["par_titre"]
            if document not in seance["numerotes"]}


def etape_actes(conn, revue, noms_publics, redige, exclusions) -> dict:
    from collectors.config import COMMUNE_NAME

    # Un acte de marché ne se publie pas si son marché n'est pas publiable.
    #
    # Le filtre de confiance posé sur `marches_publics` ne suffisait pas :
    # la page d'accueil, le flux et les millésimes lisent les ACTES, pas la
    # table des marchés. Le 20/08/2026, le site servait 8 marchés et
    # 717 actes de marché — dont les avis d'un EPCI de Seine-Saint-Denis
    # attrapés par le mot « Terres ». Filtrer un côté et pas l'autre ne
    # ferme rien : les deux vues doivent dire la même chose.
    colonnes_mp = {r["name"] for r in rows(conn, "PRAGMA table_info(marches_publics)")}
    filtre_actes_marches = ("""
        AND NOT EXISTS (SELECT 1 FROM marches_publics mp
                         WHERE mp.event_id = events.id
                           AND mp.confidence NOT IN ('verified','confirmed'))
    """ if "confidence" in colonnes_mp else "")
    event_rows = rows(conn, f"""
        SELECT id, type, date, title, source, source_url, metadata,
               CASE WHEN type IN ({", ".join(f"'{t}'" for t in TYPES_DELIBERES)})
                    THEN content END AS texte_acte,
               (source = 'bodacc' AND (content LIKE '%personne physique%'
                                       OR content LIKE '%radiationPP%')) AS bodacc_pp
        FROM events
        WHERE 1=1 {filtre_actes_marches}
        ORDER BY date DESC, id DESC
    """)
    public_events: list[dict] = []
    event_exclusions: list[dict] = []
    textes_extraits: dict[int, str] = {}
    masquages = Counter()
    revue_delib = revue.get("deliberation", {})
    # Le recueil ne fait foi que s'il SORT : un acte numéroté rejeté à l'atelier
    # ou venu d'une source non publique n'écarte les fragments de personne.
    types_exclus = set(RULES["events"]["exclude_types"])
    sources_publiques = set(RULES["events"]["public_sources"])
    fragments = fragments_de_pv([
        e for e in event_rows
        if e.get("type") not in types_exclus and e.get("source") in sources_publiques
        and not (e.get("type") in TYPES_REVUS["deliberation"]
                 and appliquer_revue(e, revue_delib.get(e["id"])) is None)])
    pieces_des_fragments: dict[int, str | None] = {}
    # Les actes écartés comme rubrique des décisions par délégation : un flux
    # lu dans leur texte est une décision du maire ou du président, et ne sort
    # pas non plus (`etape_flux`).
    actes_de_rubrique: set[int] = set()
    derniere_passe = _derniere_passe(event_rows)
    for event in event_rows:
        # Verdict de l'atelier : ne s'applique qu'aux types qu'il présente
        # à la revue (`/atelier/donnees`), pour ne pas laisser un id partagé
        # avec une autre table rejeter un acte par accident.
        if event.get("type") in TYPES_REVUS["deliberation"]:
            event = appliquer_revue(event, revue_delib.get(event["id"]))
            if event is None:
                exclusions["events"]["rejete_en_atelier"] += 1
                continue
        source = event.get("source")
        event_type = event.get("type")
        try:
            metadata = json.loads(event.get("metadata") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        reason = None
        if event_type in set(RULES["events"]["exclude_types"]):
            reason = "excluded_event_type"
        elif source not in set(RULES["events"]["public_sources"]):
            reason = "source_not_public_allowlist"
        elif source == "bodacc" and bodacc_personne_physique(
                metadata.get("commercant"), event.get("bodacc_pp")):
            reason = "bodacc_personne_physique"
        elif event["id"] in fragments:
            reason = "fragment_de_pv_d_une_seance_numerotee"
        elif event_type in TYPES_DELIBERES and rubrique_de_decisions(
                nettoyer_titre_evenement(event["title"])):
            # La rubrique du procès-verbal où l'exécutif rend compte de ses
            # décisions par délégation : le conseil n'y délibère de rien, et
            # ces décisions ne sont pas publiées depuis un PV (Julien, 14 et
            # 15/11/2026). Ni titre, ni texte, ni place dans le compteur.
            reason = "rubrique_decisions_par_delegation"
        elif event_type in TYPES_DELIBERES and (motif_titre := motif_de_titre_douteux(
                nettoyer_titre_evenement(event["title"]), COMMUNE_NAME)):
            # Un morceau de phrase, des mots collés, un débris de tableau ou le
            # nom de la commune seul ne sont pas l'objet d'un acte. Le morceau de
            # phrase se corrige aussi au découpage : la base en garde d'avant
            # (15/11/2026), jusqu'au redécoupage. Les mots collés, les débris et
            # le nom de la commune ne sont écartés QU'ICI, donc pour de bon : un
            # redécoupage ne les rend pas (`_recoller` les fondrait dans l'acte
            # d'avant). Un titre douteux vaut mieux écarté que faux.
            reason = motif_titre
        elif event_type in TYPES_DECISION and categorie_publiee(
                metadata, event["title"]) in DECISIONS_COMPTEES:
            # Le nombre sort, par catégorie (`stats.json`, exclusions) ; ni le
            # titre, ni la date, ni le lien.
            exclusions["decisions_par_delegation"][categorie_publiee(
                metadata, event["title"])] += 1
            reason = "decision_comptee_seulement"

        if reason == "fragment_de_pv_d_une_seance_numerotee":
            # Seul un fragment écarté POUR CE MOTIF prête son procès-verbal aux
            # flux : un acte rejeté à l'atelier ou venu d'une source non publique
            # est sorti plus haut, et son URL ne sort pas par ce détour.
            pieces_des_fragments[event["id"]] = fragments[event["id"]]
        elif reason in MOTIFS_DE_TITRE_DOUTEUX:
            # Même renvoi pour un titre douteux : sa source est publique, et
            # le flux qu'on y a lu reste attesté par la pièce.
            pieces_des_fragments[event["id"]] = safe_url(
                metadata.get("pdf_url") or event.get("source_url"))
        elif reason == "rubrique_decisions_par_delegation":
            actes_de_rubrique.add(event["id"])
        if reason:
            exclusions["events"][reason] += 1
            event_exclusions.append({
                "id": event["id"],
                "type": event_type,
                "title": event["title"],
                "source": source,
                "reason": reason,
            })
            continue

        # Un titre réduit à un nom, ou un morceau de tableau, n'est pas un
        # acte : rien ne se publie, ni lui, ni son texte.
        titre, refus_titre = titre_de_l_evenement(
            event_type, event["title"], event["date"], noms_publics, masquages)
        if refus_titre:
            exclusions["events"][refus_titre] += 1
            if event_type in TYPES_DECISION:
                exclusions["decisions_par_delegation"]["titre_refuse"] += 1
            event_exclusions.append({"id": event["id"], "type": event_type,
                                     "title": event["title"], "source": source,
                                     "reason": refus_titre})
            continue

        source_url, pdf_meta, piece_retiree = event["source_url"], metadata.get("pdf_url"), False
        if event_type in TYPES_DECISION:
            if categorie_publiee(metadata, event["title"]) == "preemption_exercee":
                # Titre neutre, d'abord : il ne reprend de l'objet que le montant.
                # La pièce et son numéro donnent la parcelle et le vendeur : le
                # titre neutre ne vaut que sans eux.
                titre = titre_de_preemption(event["title"])
                source_url, pdf_meta = None, None
                metadata = {**metadata, "numero_acte": None}
            elif _DESIGNE_UNE_PERSONNE.search(redige(titre or "")):
                # Le masque des titres, ou celui des noms que la base connaît
                # (`redige`, appliqué à l'écriture), a remplacé quelqu'un : la
                # décision le concernait, elle se compte (relecture du 15/11).
                exclusions["decisions_par_delegation"]["nominative"] += 1
                exclusions["events"]["decision_comptee_seulement"] += 1
                continue
            exclusions["decisions_par_delegation"]["publiee"] += 1
            # Le portail retire la pièce après l'affichage légal : une décision
            # que la dernière passe n'a plus vue n'a plus de lien à donner.
            if (metadata.get("vu_le") or "") < derniere_passe.get(event_type, ""):
                source_url, pdf_meta, piece_retiree = None, None, True
                exclusions["decisions_par_delegation"]["piece_retiree"] += 1

        delibere = event_type in TYPES_DELIBERES
        if delibere and (event.get("texte_acte") or "").strip():
            # Un acte qui porte sur la situation d'une personne garde son titre,
            # son montant et son lien ; son texte ne sort pas (14/11/2026).
            refus_extrait = motif_sans_extrait(
                event["title"], event["texte_acte"], noms_publics)
            if not refus_extrait:
                texte, refus_extrait = texte_publiable(
                    event["texte_acte"], event["date"], noms_publics, masquages)
            if refus_extrait:
                exclusions["extraits"][refus_extrait] += 1
            else:
                textes_extraits[event["id"]] = texte

        public_events.append({
            "id": event["id"],
            "type": event_type,
            "date": event["date"],
            "date_end": metadata.get("date_end"),
            # Le titre désigne un particulier par son rôle (règle du 27/10) ;
            # `redige` rattrape ceux que la base connaît et qui sont écrits
            # sans civilité. Le texte de l'acte, lui, reste cité (16/09).
            "title": redige(titre),
            "source": source,
            "source_url": safe_url(source_url),
            "page_url": safe_url(metadata.get("page_url")),
            # Le texte de la délibération se déplie sous son titre ; il est
            # écrit à part, cf. `write_act_extracts`. Le drapeau dit à la
            # page qu'il y a quelque chose à déplier.
            **({"extrait": True} if event["id"] in textes_extraits else {}),
            **({"piece_retiree": True} if piece_retiree else {}),
            **({"categorie_delegation": categorie_publiee(metadata, event["title"]),
                "numero_acte": metadata.get("numero_acte") or None}
               if event_type in TYPES_DECISION else {}),
            # Les pièces d'une séance — registre, procès-verbal, convocation.
            # Elles n'existent que sur l'ombrelle, et c'est par elles que le
            # lecteur atteint l'archive : la fiche de séance ne peut pas
            # renvoyer à un seul document quand la séance en a produit trois.
            "pieces": [
                {"nature": p.get("nature"), "libelle": p.get("libelle"),
                 "url": safe_url(p.get("url"))}
                for p in (metadata.get("pieces") or [])
                if safe_url(p.get("url"))
            ] or None,
            # Ce que la convocation annonce (collectors/convocation.py) :
            # l'heure, le lieu, l'ordre du jour. C'est ce qui permet
            # d'annoncer un conseil AVANT qu'il ait lieu. Un point annoncé
            # n'est pas un acte voté : il passe par `redige()`.
            **({"convocation": convocation_publique(
                    metadata["convocation"], event["date"], noms_publics, masquages, redige)}
               if event_type in TYPES_SEANCE and metadata.get("convocation") else {}),
            "pdf_url": safe_url(pdf_meta) or (
                safe_url(source_url)
                if ".pdf" in (source_url or "").lower() else None
            ),
            # Le détail délibératif était produit par les parsers puis jeté à
            # l'export : 493 délibérations sur 508 portent le décompte du vote
            # et 471 un montant, et la page publique n'affichait qu'une ligne
            # date + titre. C'est la matière même du contrôle citoyen.
            **public_event_detail(metadata, event_type, event["title"]),
            # Trois axes vérifiables au lieu d'un label à croire.
            **provenance(
                event, source, event_type,
                safe_url(pdf_meta) or (
                    safe_url(source_url)
                    if ".pdf" in (source_url or "").lower() else None
                ),
                safe_url(source_url),
            ),
        })

        # Le montant d'un acte est CALCULÉ (le plus élevé des montants
        # cités), il n'existe pas comme colonne : une correction de montant
        # doit donc écraser le résultat du calcul, pas un champ source. Elle
        # lève aussi le caractère « indicatif », puisqu'un humain a tranché.
        corrections_ev = set(event.get("corrige") or [])
        if "montant" in corrections_ev:
            public_events[-1]["montant_principal"] = event["montant"]
            public_events[-1].pop("montant_indicatif", None)
        if corrections_ev:
            public_events[-1]["corrige"] = sorted(corrections_ev)
        if event.get("note_revue"):
            public_events[-1]["note_revue"] = redige(champ_publiable(
                event["note_revue"], event["date"], noms_publics))

    return {
        "event_rows": event_rows,
        "public_events": public_events,
        "event_exclusions": event_exclusions,
        "textes_extraits": textes_extraits,
        "masquages": masquages,
        "pieces_des_fragments": pieces_des_fragments,
        "actes_de_rubrique": actes_de_rubrique,
    }


def etape_liens_actes(conn, public_ids, public_entities, public_events,
                      exclusions) -> dict:
    # ── Croisements acteur ↔ événement ───────────────────────────────────
    # 6 154 liens existaient en base sans jamais être exportés : la fiche
    # publique d'un acteur n'affichait donc ni les délibérations qui le
    # concernent, ni les annonces le visant. On ne publie un lien que si
    # ses deux extrémités sont elles-mêmes publiques.
    public_event_ids = {e["id"] for e in public_events}
    link_rows = rows(conn, """
        SELECT ee.event_id, ee.entity_id, ee.role
        FROM event_entities ee
        ORDER BY ee.event_id
    """)
    public_links = [
        {"event_id": l["event_id"], "entity_id": l["entity_id"], "role": l["role"]}
        for l in link_rows
        if l["event_id"] in public_event_ids and l["entity_id"] in public_ids
    ]
    exclusions["event_links"]["endpoint_not_public"] = len(link_rows) - len(public_links)

    # ── Portée de chaque acte ────────────────────────────────────────────
    # Après les liens, parce que c'est un acte SANS type d'assemblée connu
    # qui a besoin de ses acteurs pour dire de qui il parle. Une annonce
    # BODACC visant une entreprise de Lasalle est communale ; la même
    # visant Val-d'Aigoual ne l'est pas.
    perimetre_par_entite = {e["id"]: e.get("perimetre") for e in public_entities}
    perimetres_par_acte: dict[int, set[str]] = defaultdict(set)
    for l in public_links:
        per = perimetre_par_entite.get(l["entity_id"])
        if per:
            perimetres_par_acte[l["event_id"]].add(per)
    for e in public_events:
        e["portee"] = portee_evenement(e["type"], perimetres_par_acte[e["id"]],
                                       e.get("source"))

    return {"public_links": public_links, "perimetre_par_entite": perimetre_par_entite}


def etape_cles_actes(conn, public_events) -> dict:
    public_event_ids = {e["id"] for e in public_events}
    # ── L'identité datée de chaque acte et séance ────────────────────────
    # `events.id` change à chaque rejeu : l'ancre publique d'un acte est sa
    # clé (`collectors/cle_acte.py`), calculée sur la ligne BRUTE de la base
    # — la même lecture que l'atelier au moment de sceller un dossier.
    # Une clé faible (date + titre) ou portée par deux actes publiés garde
    # l'ancre `a{id}` : elle n'est jamais présentée comme stable.
    lignes_actes = lignes_en_base(conn)
    index_actes = index_de(lignes_actes, public_event_ids)
    cles_brutes = {l["id"]: a for l in lignes_actes
                   if l["id"] in public_event_ids and (a := acte_de_ligne(l))}
    for e in public_events:
        a = cles_brutes.get(e["id"])
        if not a:
            continue
        e["cle"] = a.cle
        if a.faible:
            e["cle_faible"] = True
        e["ancre"] = a.ancre if a.cle not in index_actes.collisions else f"a{e['id']}"
    affiches = {e["id"]: e for e in public_events}
    for a in index_actes.par_cle.values():
        # Le titre affiché par le résolveur (infobulles) est le titre PUBLIÉ,
        # masques compris — jamais celui de la base.
        a.titre = affiches[a.id].get("title") or a.titre
    cles_stats = {
        "stables": sum(1 for e in public_events if e.get("cle") and e.get("ancre") == e["cle"]),
        "faibles": sum(1 for e in public_events if e.get("cle_faible")),
        "en_collision": len(index_actes.collisions),
    }

    return {"index_actes": index_actes, "affiches": affiches, "cles_stats": cles_stats}


def etape_extraits(out, textes_extraits) -> dict:
    """Le texte de chaque délibération qui peut sortir, un fichier par acte."""
    return {"extraits_actes": write_act_extracts(out, textes_extraits)}
