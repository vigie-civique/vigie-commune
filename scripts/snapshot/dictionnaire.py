"""Le dictionnaire de données : le README servi à côté des fichiers du snapshot.

Régénéré à chaque construction, depuis les règles de publication et les
compteurs de cette construction-là : un README écrit à la main se périme en
silence.
"""
from __future__ import annotations

from scripts.snapshot.socle import RULES, rows, table_exists


def entites_osm_publiees(conn, public_ids) -> int:
    """Les entités publiées importées d'OpenStreetMap (`places.osm_id`), avec
    leurs coordonnées : la part OSM du jeu, celle qui fonde l'ODbL."""
    if not table_exists(conn, "places"):
        return 0
    osm = {r["entity_id"] for r in rows(conn,
           "SELECT DISTINCT entity_id FROM places WHERE osm_id IS NOT NULL")}
    return len(osm & set(public_ids))


_GEOCODAGE = ("viennent du géocodage de l'IGN (Géoplateforme) et de la Base Adresse "
              "Nationale, sous Licence Ouverte.")


def _origine_osm(n_osm: int, n: int) -> str:
    if not n_osm:
        return (f"Aucune des {n} entités publiées ne vient d'OpenStreetMap dans "
                f"cette construction. Les coordonnées {_GEOCODAGE}")
    return (f"Ce choix découle des sources : {n_osm} des {n} entités publiées "
            "sont des points d'intérêt OpenStreetMap, importés avec leurs "
            "coordonnées et fondus dans le jeu : c'est une base dérivée au "
            "sens de l'ODbL, et le partage à l'identique s'applique. Les "
            f"autres coordonnées {_GEOCODAGE}")


def etape_dictionnaire(out, stats, location_quality, conn, public_ids) -> None:
    # ── Dictionnaire de données ──────────────────────────────────────────
    # Servi À CÔTÉ des JSON, et régénéré à chaque exécution : un README
    # écrit à la main se périme en silence — celui d'avant le 12/08/2026
    # annonçait encore 2 876 entités publiques pour 1 807 réelles, et ne
    # disait rien du contenu des fichiers. Un jeu de données sans
    # dictionnaire n'est pas réutilisable, quelle que soit sa qualité.
    markdown = [
        f"# Données publiques — {RULES['project']['public_name']}",
        "",
        f"Généré le {stats['generated_at']} depuis la base de travail, "
        "sans la modifier.",
        "",
        "Ces fichiers sont le snapshot public : ce que le site sert, et rien "
        "d'autre. Ils sont produits par `scripts/build_public_snapshot.py` "
        "et contrôlés par `scripts/verify_snapshot.py`, qui refuse de "
        "publier tout type de relation absent de l'allowlist.",
        "",
        "## Licence",
        "",
        f"Ce jeu de données est publié sous **{RULES['outputs']['license']}** "
        f"([Open Database License]({RULES['outputs']['license_url']})).",
        "",
        "Vous pouvez le copier, le modifier et l'utiliser, y compris "
        "commercialement, à trois conditions : **citer** la source, "
        "**partager à l'identique** toute base dérivée que vous "
        "redistribuez, et ne pas la diffuser sous verrou technique sans "
        "en fournir aussi une version libre.",
        "",
        # Compté à chaque construction : le chiffre écrit en dur (« 333 »,
        # et un « géocodage OSM » qui n'existe pas, le géocodeur étant celui
        # de l'IGN) était le même sur les trois sites (avis PI du 26/10/2026).
        _origine_osm(entites_osm_publiees(conn, public_ids), len(public_ids)),
        "",
        "### Attribution",
        "",
        f"> {RULES['outputs']['attribution']}",
        "",
    ]
    markdown += [f"- {a}" for a in RULES["outputs"]["source_attributions"]]
    markdown += [
        "",
        "Le **site** et ses visualisations sont un « Produced Work » au "
        "sens de l'ODbL : les reprendre demande l'attribution, pas le "
        "partage à l'identique. Le **code** relève d'une licence distincte "
        "(MIT) — l'ODbL ne porte pas sur le logiciel.",
        "",
        "**La licence ne dit rien du RGPD.** Ces données restent soumises "
        "au droit des données personnelles : une réutilisation doit avoir "
        "sa propre base légale.",
        "",
        "## Réplication",
        "",
        "Ce modèle est conçu pour être rejoué sur une autre commune. Le "
        "périmètre se pilote dans `collectors/config.py` et nulle part "
        "ailleurs : commune, intercommunalité, communes membres. Les "
        "collecteurs, le schéma et le site n'ont pas à être touchés.",
        "",
        "## Fichiers",
        "",
        "| Fichier | Contenu | Clé racine |",
        "|---|---|---|",
        "| `entities.json` | Acteurs publiés : personnes, entreprises, "
        "associations, services, lieux | `entities` |",
        "| `relations.json` | Liens entre acteurs, datés et sourcés | "
        "`relations` |",
        "| `popolo.json` | Les **mandats** au format [Popolo]"
        "(https://www.popoloproject.com/) — format d'interopérabilité | "
        "`persons`, `organizations`, `memberships`, `areas` |",
        "| `events.json` | Actes : délibérations, arrêtés, annonces | "
        "`events` |",
        "| `flows.json` | Flux financiers publics (subventions, "
        "participations) | `flows` |",
        "| `marches.json` | Marchés publics et attributaires | `marches` |",
        "| `budget.json` · `budget_vote.json` · `ofgl.json` | Budgets "
        "votés et agrégats financiers | `annuel`/`annexe`, `budget_vote`, "
        "`ofgl` |",
        "| `avant_fusion.json` | Commune nouvelle : ses anciennes communes, et "
        "les comptes de celles absorbées sous un autre code, à part (jamais "
        "additionnés) | `predecesseurs`, `absorbees` |",
        "| `intercommunalite.json` | Compétences, délégués, sièges de "
        "l'EPCI | racine |",
        "| `elus_rne.json` | Conseils municipaux (Répertoire National des "
        "Élus) | `elus` |",
        "| `elections.json` | Résultats des municipales par commune | "
        "`resultats` |",
        "| `fiscalite.json` · `impots` | Taux d'imposition comparés, et "
        "leur rang parmi les communes | `taux`, `reperes` |",
        "| `dvf.json` | Mutations immobilières (DVF), en agrégats : par année, "
        "par nature de bien, comptées par mutation et non par ligne — aucune vente à l'unité | `synthese`, `par_annee`, "
        "`par_nature` |",
        "| `urbanisme.json` | Autorisations d'urbanisme | `autorisations` |",
        "| `environnement.json` | Eau (prix, contrôle sanitaire), déchets, "
        "forêt et feux, risques, ICPE, catastrophes naturelles | racine |",
        "| `territoire.json` | Indicateurs INSEE, équipements, télécoms, "
        "écoles et accueil du jeune enfant | racine |",
        "| `conflits.json` | Cas de conflits d'intérêts potentiels | "
        "`cas` |",
        "| `stats.json` | Compteurs et paramètres de publication | racine |",
        "| `layers/*.geojson` | Couches cartographiques | FeatureCollection |",
        "| `entite/<id>.json` | Fiche complète d'un acteur | racine |",
        "| `extrait/<id>.json` | Texte d'une délibération, lu dans le "
        "document | `texte` |",
        "| `liens.json` | Clé datée d'un acte (`c-2021-41`) → les dossiers "
        "et séances en clair qui le citent ; table d'alias `#a{id}` → clé | "
        "`actes`, `alias` |",
        "| `lacunes.json` | Questions ouvertes des dossiers publiés et "
        "citations sans acte publié | `lacunes` |",
        "| `personnes_morales.json` | Par clé d'acte, les personnes morales "
        "publiées qu'il concerne (SIREN) | `actes` |",
        "| `seances.json` | Une ligne par séance publiée, relue ou non : date, "
        "assemblée, délibérations publiées, pièces, et la feuille en clair si "
        "elle existe | `seances` |",
        "| `sujets.json` | Pour chaque sujet (l'eau, les déchets…) : ses sections "
        "de données, s'il en a ici, et son dossier publié | `sujets` |",
        "",
        "Chaque fichier à liste porte aussi un `total`.",
        "",
        "## Ce qui n'est jamais publié",
        "",
        "- les affirmations de niveau `probable` ou `hypothesis` — seuls "
        f"`{'`, `'.join(sorted(RULES['confidence']['public']))}` sortent ;",
        "- les liens de famille, de domicile partagé et les doublons "
        f"présumés (marqueurs : `{'`, `'.join(sorted(RULES['relations']['private_markers']))}`) ;",
        "- les coordonnées des personnes, et les adresses des demandeurs "
        "particuliers en urbanisme ;",
        "- la date de naissance des élus (le RNE la diffuse, pas nous) ;",
        "- dans le texte des délibérations, le domicile, la date et le lieu "
        "de naissance d'une personne — masqués ; pour une personne publique, "
        "la date de naissance devient son âge à la date de l'acte. Les noms, "
        "eux, sont cités : un acte officiel cite ses particuliers ;",
        "- les conseils municipaux des communes hors intercommunalité.",
        "",
        "## Compteurs",
        "",
        f"- entités : {stats['entities_public']} publiées "
        f"sur {stats['entities_total_private']} en base",
        f"- relations : {stats['relations_public']} sur "
        f"{stats['relations_total_private']}",
        f"- actes : {stats['events_public']} sur "
        f"{stats['events_total_private']}",
        f"- points cartographiés : {stats['map_features_public']}",
        f"- sites web vérifiés : {stats['urls_public_confirmed']}",
        "",
        "## Qualité de localisation",
        "",
    ]
    for key, count in sorted(location_quality.items()):
        markdown.append(f"- `{key}` : {count}")
    markdown.extend([
        "",
        "## Exclusions — pourquoi une donnée n'est pas là",
        "",
    ])
    for section, counts in stats["exclusions"].items():
        markdown.append(f"### {section}")
        for reason, count in sorted(counts.items()):
            markdown.append(f"- `{reason}` : {count}")
        markdown.append("")
    (out / "README.md").write_text("\n".join(markdown), encoding="utf-8")
