# Données publiques — Vigie Civique Testonville

Généré le 2026-11-14T22:27:08 depuis la base de travail, sans la modifier.

Ces fichiers sont le snapshot public : ce que le site sert, et rien d'autre. Ils sont produits par `scripts/build_public_snapshot.py` et contrôlés par `scripts/verify_snapshot.py`, qui refuse de publier tout type de relation absent de l'allowlist.

## Licence

Ce jeu de données est publié sous **ODbL-1.0** ([Open Database License](https://opendatacommons.org/licenses/odbl/1-0/)).

Vous pouvez le copier, le modifier et l'utiliser, y compris commercialement, à trois conditions : **citer** la source, **partager à l'identique** toute base dérivée que vous redistribuez, et ne pas la diffuser sous verrou technique sans en fournir aussi une version libre.

Aucune des 3 entités publiées ne vient d'OpenStreetMap dans cette construction. Les coordonnées viennent du géocodage de l'IGN (Géoplateforme) et de la Base Adresse Nationale, sous Licence Ouverte.

### Attribution

> 

- Contient des informations d'OpenStreetMap, mises à disposition sous Open Database License (ODbL) — © les contributeurs OpenStreetMap
- Contient des données publiques sous Licence Ouverte v2 (Etalab) : SIRENE et indicateurs INSEE, RNA, DVF, BODACC, OFGL et DGFiP, DECP et BOAMP, Répertoire National des Élus, résultats électoraux du ministère de l'Intérieur, BANATIC
- Contient des données IGN (géocodage) sous Licence Ouverte v2
- Contient des données de l'Observatoire des services publics d'eau et d'assainissement (SISPEA, OFB), composition communale des services et indicateurs, téléchargées sur services.eaufrance.fr, sous Licence Ouverte v2
- Contient les résultats des campagnes FDVA 2 publiés par la DRAJES Occitanie, © www.ac-montpellier.fr (https://www.ac-montpellier.fr/fdva-2-fonctionnement-global-et-nouveaux-projets-les-resultats-122195) : intitulé et montant accordé, pour les associations du territoire

Le **site** et ses visualisations sont un « Produced Work » au sens de l'ODbL : les reprendre demande l'attribution, pas le partage à l'identique. Le **code** relève d'une licence distincte (MIT) — l'ODbL ne porte pas sur le logiciel.

**La licence ne dit rien du RGPD.** Ces données restent soumises au droit des données personnelles : une réutilisation doit avoir sa propre base légale.

## Réplication

Ce modèle est conçu pour être rejoué sur une autre commune. Le périmètre se pilote dans `collectors/config.py` et nulle part ailleurs : commune, intercommunalité, communes membres. Les collecteurs, le schéma et le site n'ont pas à être touchés.

## Fichiers

| Fichier | Contenu | Clé racine |
|---|---|---|
| `entities.json` | Acteurs publiés : personnes, entreprises, associations, services, lieux | `entities` |
| `relations.json` | Liens entre acteurs, datés et sourcés | `relations` |
| `popolo.json` | Les **mandats** au format [Popolo](https://www.popoloproject.com/) — format d'interopérabilité | `persons`, `organizations`, `memberships`, `areas` |
| `events.json` | Actes : délibérations, arrêtés, annonces | `events` |
| `flows.json` | Flux financiers publics (subventions, participations) | `flows` |
| `marches.json` | Marchés publics et attributaires | `marches` |
| `budget.json` · `budget_vote.json` · `ofgl.json` | Budgets votés et agrégats financiers | `annuel`/`annexe`, `budget_vote`, `ofgl` |
| `avant_fusion.json` | Commune nouvelle : ses anciennes communes, et les comptes de celles absorbées sous un autre code, à part (jamais additionnés) | `predecesseurs`, `absorbees` |
| `intercommunalite.json` | Compétences, délégués, sièges de l'EPCI | racine |
| `elus_rne.json` | Conseils municipaux (Répertoire National des Élus) | `elus` |
| `elections.json` | Résultats des municipales par commune | `resultats` |
| `fiscalite.json` · `impots` | Taux d'imposition comparés, et leur rang parmi les communes | `taux`, `reperes` |
| `dvf.json` | Mutations immobilières (DVF), en agrégats : par année, par nature de bien, comptées par mutation et non par ligne — aucune vente à l'unité | `synthese`, `par_annee`, `par_nature` |
| `urbanisme.json` | Autorisations d'urbanisme | `autorisations` |
| `environnement.json` | Eau (prix, contrôle sanitaire), déchets, forêt et feux, risques, ICPE, catastrophes naturelles | racine |
| `territoire.json` | Indicateurs INSEE, équipements, télécoms, écoles et accueil du jeune enfant | racine |
| `conflits.json` | Cas de conflits d'intérêts potentiels | `cas` |
| `stats.json` | Compteurs et paramètres de publication | racine |
| `layers/*.geojson` | Couches cartographiques | FeatureCollection |
| `entite/<id>.json` | Fiche complète d'un acteur | racine |
| `extrait/<id>.json` | Texte d'une délibération, lu dans le document | `texte` |
| `liens.json` | Clé datée d'un acte (`c-2021-41`) → les dossiers et séances en clair qui le citent ; table d'alias `#a{id}` → clé | `actes`, `alias` |
| `lacunes.json` | Questions ouvertes des dossiers publiés et citations sans acte publié | `lacunes` |
| `personnes_morales.json` | Par clé d'acte, les personnes morales publiées qu'il concerne (SIREN) | `actes` |
| `seances.json` | Une ligne par séance publiée, relue ou non : date, assemblée, délibérations publiées, pièces, et la feuille en clair si elle existe | `seances` |
| `sujets.json` | Pour chaque sujet (l'eau, les déchets…) : ses sections de données, s'il en a ici, et son dossier publié | `sujets` |

Chaque fichier à liste porte aussi un `total`.

## Ce qui n'est jamais publié

- les affirmations de niveau `probable` ou `hypothesis` — seuls `confirmed`, `verified` sortent ;
- les liens de famille, de domicile partagé et les doublons présumés (marqueurs : `doublon`, `enfant`, `famille`, `même_adresse`, `même_lieu_dit`, `proche`, `présumé`, `époux`) ;
- les coordonnées des personnes, et les adresses des demandeurs particuliers en urbanisme ;
- la date de naissance des élus (le RNE la diffuse, pas nous) ;
- dans le texte des délibérations, le domicile, la date et le lieu de naissance d'une personne — masqués ; pour une personne publique, la date de naissance devient son âge à la date de l'acte. Les noms, eux, sont cités : un acte officiel cite ses particuliers ;
- les conseils municipaux des communes hors intercommunalité.

## Compteurs

- entités : 3 publiées sur 6 en base
- relations : 1 sur 1
- actes : 10 sur 10
- points cartographiés : 0
- sites web vérifiés : 0

## Qualité de localisation

- `hidden_person` : 1
- `missing` : 2

## Exclusions — pourquoi une donnée n'est pas là

### entities
- `hors_fiche_perimetre_C2` : 1
- `person_without_public_civic_role` : 2

### event_links
- `endpoint_not_public` : 0

### flows
- `duplicates` : 0
- `endpoint_not_public` : 0
- `private_cession` : 0
- `private_confidence` : 0
- `private_person_endpoint` : 0

### marches
- `rejete_en_atelier` : 0
- `renvoi_vers_fiche_non_publiee` : 0
