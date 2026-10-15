# Dossiers thématiques — gabarit

> Brouillon de conception, 30/09/2026. Rien n'est encore codé.

Un dossier thématique répond à une question de citoyen — *qu'est-ce que je
paie, qui décide, depuis quand, qu'est-ce que je reçois* — en assemblant ce que
la base sait déjà (actes, comptes, indicateurs) et en **montrant ce qu'elle ne
sait pas**. Le dossier est aussi l'outil qui révèle les lacunes : chaque
rubrique déclare les données qui la remplissent ; une rubrique vide l'affiche
d'elle-même, avec la manière de la combler.

## 1. Deux familles

| Famille | Exemples | Remplissage | Instances |
|---------|----------|-------------|-----------|
| **Générique** | eau, déchets, télécoms, rivières/crues, forêt/incendie, transferts à l'EPCI | gabarit du moteur, requêtes paramétrées par `:insee` / `:epci_siren` | toutes, sans écriture |
| **Local** | un tiers-lieu, un équipement communal | récit rédigé, adossé à la même frise et aux mêmes chiffres | une seule |

Un dossier local qui touche un intérêt de l'auteur porte une **déclaration
d'intérêt** visible en tête.

## 2. Le gabarit — sept rubriques

| # | Rubrique | Question | Nature |
|---|----------|----------|--------|
| 0 | **La question** | ce que je paie, ce que je reçois | texte court, fixe par thème |
| 1 | **Qui décide** | commune → syndicat → EPCI → Département/État ; exploitant ; dates de transfert | chaîne datée, calculée |
| 2 | **L'objet physique** | le réseau, les ouvrages, la carte ; renvoi au chapitre relief | carte + liste |
| 3 | **Le service rendu** | qualité, continuité, couverture | indicateurs dans le temps |
| 4 | **Le prix** | combien, décomposé comment, évolution | série + décomposition |
| 5 | **L'argent public** | budgets annexes, comptes du syndicat, subventions, marchés | tableaux |
| 6 | **La frise** | délibérations, arrêtés, marchés, jalons | liste datée, liens vers les actes |
| 7 | **Ce qu'on ne sait pas** | lacunes, énigmes, et comment les combler | **calculée** à partir de 1–6 |

Le **relief** n'est pas une rubrique : c'est un chapitre transversal (« un
territoire en pente » : altitudes, hameaux, dispersion) auquel chaque dossier
renvoie depuis sa rubrique 2.

### Déclaration d'une rubrique

Chaque rubrique d'un dossier générique est une déclaration, pas du texte :

```toml
[[rubrique]]
id        = "eau.prix"
titre     = "Le prix de l'eau"
requete   = "SELECT annee, valeur FROM sispea_indicateurs i JOIN sispea_services s USING(code_service) WHERE s.communes LIKE '%'||:insee||'%' AND i.code='D102.0'"
attendu   = { lignes_min = 1, annees_depuis = 2015 }   # en deçà : « partiel »
combler   = { source = "SISPEA", action = "collecteur sispea", sinon = "RPQS de l'exploitant (CADA)" }
```

Le statut de chaque rubrique — **rempli / partiel / lacune** — est calculé, pas
déclaré. La rubrique 7 est la liste des rubriques non remplies et des énigmes
posées à la main (rubrique `enigme`, sans requête : une question ouverte avec
les pièces qui la fondent).

### Frise et actes : identité stable

Les conseils ont déjà été rejoués (recollecte, ré-OCR, re-découpage) : les
`events.id` changent d'un rejeu à l'autre. La frise ne référence donc jamais un
`id` ; elle retient la **clé naturelle de l'acte** (source, année, numéro
d'acte, date) et la résout au rendu. Les résumés de conseils de l'autre
chantier sont une source de la frise, pas une copie : le dossier pointe le
résumé et l'acte.

**Fait le 03/10/2026** — la clé datée (`collectors/cle_acte.py`) :

| Clé | Ce qu'elle désigne |
|-----|--------------------|
| `c-2021-41` | commune, année 2021, acte n° 41 |
| `cc-2025-12` | intercommunalité, 2025, acte n° 12 |
| `c-2021-03-04-s3` | acte sans numéro : 3ᵉ de la séance du 04/03/2021 |
| `c-2021-03-04-t1a2b3c4` | ni numéro ni rang : date + empreinte du titre — **faible**, jamais une ancre |
| `c-2021-03-04` | la séance elle-même |

L'ancre publique d'un acte est `/deliberations/{année}#{clé}` ; `#a{id}` reste
servi un cycle par la table d'alias de `liens.json`.

### Citer un acte dans un dossier

Rien à changer à la façon d'écrire : le snapshot relie seul les citations
naturelles (`collectors/citations.py`), contre les actes **publiés** seulement :

- `(CM du 14/04/2021)`, `(délibération CC du 02/04/2025)`,
  `conseil municipal du 4 mars 2026`, `délibération n°41/2021` ;
- une ligne de frise `| 17/02/2016 | Motion … | CM |` (la date est reliée).

Une séance qui a pris plusieurs actes est départagée par ce que la phrase dit
d'autre — une citation « entre guillemets » retrouvée dans le texte d'un seul
acte, un montant voté par un seul, l'objet d'une ligne de frise proche d'un
seul titre. Sinon le lien mène à la **séance**, signalé « imprécis ». Pour
lever l'ambiguïté, la syntaxe explicite :

```markdown
[le budget de la régie](acte:c-2021-42)      un acte, par sa clé
[la séance de mars](seance:c-2021-03-04)     une séance
[le registre signé](piece:c-2021-42)         la pièce source de l'acte
```

Une citation qui ne mène à rien de publié n'a pas de lien : elle entre au
relevé interne (`audits/citations_non_resolues.json`) et dans `lacunes.json`
comme une question. L'atelier montre, à l'ouverture d'un dossier, ce que
chaque citation deviendra.

Le rattachement d'un acte à un thème ne peut pas reposer sur une recherche
plein texte (« station » ramène aussi bien une station d'épuration qu'une station de ski).
Il faut une **étiquette thématique** posée une fois par acte, par règles puis
contrôle — mécanisme à décider avec le chantier des résumés pour n'en avoir
qu'un.

## 3. Suite

1. Décider le format de déclaration (TOML ci-dessus ou table SQLite) et le
   mécanisme d'étiquette thématique, avec le chantier des résumés.
2. Jouer le gabarit sur plusieurs communes pour vérifier qu'il est générique :
   un inventaire par rubrique (rempli, partiel, lacune) dit, pour chaque
   instance, ce que la base sait déjà et ce qu'il reste à collecter.
