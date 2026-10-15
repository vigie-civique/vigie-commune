# Contribuer

Ce dépôt est le **moteur** : le code qui collecte, filtre et publie. Il ne
contient aucune donnée de commune, et ne doit jamais en contenir.

Avant d'ouvrir un ticket : `CODE_OF_CONDUCT.md` — il ne parle pas seulement de
politesse, il dit ce qu'on ne colle pas dans une issue publique quand on
travaille sur une base qui nomme des gens. Et une faille du filtre de
publication ne s'ouvre **jamais** en issue : `SECURITY.md`.

## La règle qui prime sur toutes les autres

**Une particularité locale ne va pas dans le code.** Ni un code INSEE, ni un
SIREN, ni une URL de mairie, ni un identifiant de ligne (`XXX_ID = 63` — c'est
un numéro qui désigne autre chose dans une autre base), ni un nom de commune
dans une chaîne exécutée. Tout cela vit dans `config/instance.json`.

```bash
python3 scripts/verifier_generique.py
```

Ce contrôle échoue si une particularité est entrée dans le moteur. Il tourne
aussi en intégration continue et sert d'admission à l'archive distribuable.

## Lancer les contrôles

```bash
pip install pytest
python3 -m pytest                        # 953 essais, ~10 s, sans réseau
python3 scripts/verifier_generique.py    # généricité
python3 scripts/build_kit.py             # l'archive se construit-elle ?
```

Les tests tournent sur une instance **factice** (`tests/instance_test.json`) et
sur une base construite depuis `db/schema.sql`. Ils n'ont besoin ni d'une
instance configurée, ni du réseau, ni d'Ollama : un dépôt fraîchement cloné doit
pouvoir les lancer, sinon ils ne servent à rien.

## Ce qui doit être couvert par un test

Trois fonctions décident de ce qui sort du dispositif. Chacune a laissé passer
un défaut réel, et aucune n'était couverte avant août 2026 :

| Fonction | Ce qu'elle décide | Tests |
|---|---|---|
| `public_entity` | quelle fiche est publiée | `tests/test_publication.py` |
| `publiable_dans_perimetre` | ce que le périmètre autorise | `tests/test_perimetre.py` |
| `build_kit.fichiers` | ce qui entre dans l'archive publique | `tests/test_kit.py` |

Une modification de l'une des trois sans test correspondant ne sera pas
fusionnée. Ce n'est pas une exigence de forme : le défaut du 14/08/2026 —
`NULL` traité comme « dans le périmètre » — faisait publier 10 735 fiches dont
6 151 relevaient d'une commune voisine, et rien ne l'a signalé.

## Les données ne se versionnent pas

Ni base, ni snapshot publié, ni fichier nominatif, ni `config/instance.json`.
`build_kit.py` construit sa liste depuis `git ls-files` : ce qui n'est pas
versionné n'est pas distribué. Si un fichier de données arrive dans un commit,
c'est un incident, pas une inattention.

## Travailler sans donnée réelle

Aucun contributeur n'a besoin de lire une base d'instance, et aucun outil
tiers (assistant de code, IA hébergée) ne doit en lire une : ce sont des
données de personnes, même issues de sources publiques.

```bash
python3 scripts/donnees_synthetiques.py db/synthetique.db   # base FICTIVE, graine fixe
python3 scripts/sonde_base.py compter db/synthetique.db entities --par type
```

- `donnees_synthetiques.py` fabrique une base à la forme réelle : noms
  inventés, communes `99xxx`, SIREN `999…`, courriels `example.org`. Son nom de
  fichier contient « synth » : une base fictive doit se reconnaître.
- `sonde_base.py` répond par des **comptages** : lecture seule, regroupement sur
  colonne catégorielle seulement, effectifs inférieurs à 5 masqués.
- Un défaut vu sur une base réelle se **reproduit** sur la base fictive (ou par
  une phrase de `tests/fixtures/pieges_textes.txt`) avant d'être corrigé, et
  c'est ce cas fictif qui entre dans la suite d'essais — jamais un extrait réel.
  Des essais plus anciens partent d'extraits réels : ils ont été pseudonymisés
  avant la publication du dépôt. Tout nouveau cas est fictif.

## Style

Commentaires et noms en français, comme le reste. Un commentaire explique
**pourquoi**, pas quoi : le quoi se lit dans le code. Quand une décision vient
d'un défaut constaté, le dire — c'est ce qui empêche de la défaire.
