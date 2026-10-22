# Charte d'usage de l'intelligence artificielle

> Version 1.0, adoptée le 22/10/2026.

Vigie publie des faits sur des décisions publiques, et ces faits servent à les
discuter, parfois à les contester. Une ligne fausse y coûte plus cher que dix
lignes manquantes. Cette charte dit où l'intelligence artificielle intervient
dans le dispositif, où elle n'intervient pas, et de qui nous dépendons pour
cela. Elle est publiée sur la page « Méthode » du site.

## 1. Le constat de départ

Il n'existe pas aujourd'hui de modèle européen au niveau des meilleurs. Les
modèles de pointe sont américains ou chinois ; Mistral, seul acteur européen
sérieux, est un cran en dessous. Nous ne prétendons donc pas à une IA
« souveraine ». Nous distinguons trois dépendances, qui n'appellent pas la même
réponse :

| Dépendance | Ce qu'elle expose | Notre réponse |
|---|---|---|
| **Les données** — ce qui sort de la machine | le droit des données personnelles, l'accès d'un État étranger | rien de non publié ne part vers un service hors de l'Union |
| **L'exécution** — qui fait tourner le modèle | une coupure, un changement de prix, une version retirée | des modèles à poids ouverts, exécutés sur nos machines |
| **La capacité** — ce qui exige un modèle de pointe | aucune alternative européenne | assumée, limitée au développement et à la relecture, sur des données publiées ou fictives |

Un modèle à poids ouverts exécuté sur nos machines ne fait sortir aucune
donnée, quelle que soit la nationalité de celui qui l'a entraîné. Il ne dit
rien, en revanche, de ses biais. D'où une règle unique, quelle que soit
l'origine du modèle : **on ne lui confie que ce qui se vérifie** — une
extraction contre le texte d'origine, du code contre les essais — et tout ce
qui s'écrit pour le lecteur passe par un humain.

C'est pourquoi les modèles à poids ouverts sont admis sur nos machines quelle
que soit leur origine — américaine, comme Gemma (Google), ou chinoise, comme
Qwen (Alibaba) — pour l'extraction et pour le code : leur travail se vérifie,
et rien ne sort.

## 2. Où l'IA intervient

**Extraction assistée des procès-verbaux.** Un modèle local (aujourd'hui
Gemma 4 de Google ou Qwen d'Alibaba, à poids ouverts) lit un
procès-verbal et **propose** des lignes : un flux financier, un marché, un acte.
Chaque proposition cite la phrase qui la justifie, et la citation est vérifiée
mécaniquement contre le texte : un modèle qui invente ne retrouve pas sa phrase.
Les propositions dont la citation est introuvable sont signalées, pas
masquées. Rien n'est écrit en base sans qu'un humain l'ait relu et validé dans
l'atelier. (La reconnaissance de caractères des documents scannés, elle,
n'utilise pas de modèle de langage.)

**Recherche par le sens, dans l'atelier.** Un modèle local (`nomic-embed-text`) calcule des vecteurs
qui permettent de retrouver une notion sans en avoir le vocabulaire. L'index
reste dans l'atelier ; le site public n'en a aucune trace.

**Synthèse, dans l'atelier.** Absente tant qu'on ne la configure pas. Vers un
modèle local, rien ne sort. Vers un service distant, c'est une sortie de
données non filtrées : le dispositif ne la permet que sur une décision
explicite (`IA_HORS_MACHINE=1`), jamais parce qu'une adresse a été renseignée.
L'atelier en ligne de Vigie n'en a aucune.

**Rédaction du site.** L'IA aide à rédiger les textes du site : dossiers
thématiques, pages d'explication, libellés. Elle le fait sous la supervision
d'un humain, qui relit, édite et valide chaque texte avant publication. Les
citations d'un dossier renvoient aux actes publiés, et ce renvoi est contrôlé
mécaniquement. Cette charte, publiée sur la page « Méthode », en tient lieu de
mention : les textes ne la répètent pas un à un.

**Développement du code.** Le code du moteur est écrit avec l'aide d'un
assistant de pointe américain (Claude, d'Anthropic). Il travaille sur le code,
sur des données publiées et sur des bases fictives ; il ne lit pas les bases de
travail, et un garde-fou mécanique le lui interdit. Tout ce qu'il produit passe
par les mêmes essais automatiques que le reste, et par une relecture humaine
avant fusion. Anthropic est un fournisseur payé par À Bout de Champ, sous
son offre professionnelle (Claude Team) : il est inscrit comme sous-traitant au
registre des traitements de l'association, et son accord de traitement des
données encadre le transfert vers les États-Unis par les clauses contractuelles
types de la Commission européenne. Ce transfert reste limité aux données
publiées ou fictives. Il en est ainsi depuis le 21/10/2026 ; avant, l'assistant
relevait d'un abonnement individuel, aux conditions grand public, sans accord
de traitement.

**Audits et essais externes.** Le code et le site publiés ont été audités et
mis à l'épreuve par d'autres modèles de pointe américains — GPT (OpenAI), et
dans une moindre mesure Grok (xAI) —, par des offres payantes souscrites le
temps de ces essais. Ils n'ont travaillé que sur ce qui est public : les
données publiées des sites, le portail et le dépôt public du moteur. Un défaut
qu'un modèle ne voit pas, un autre le trouve parfois.

## 3. Où elle n'intervient pas

- **Elle ne publie rien.** Toute donnée publiée a été validée par un humain, et
  le contrôle d'étanchéité qui précède chaque publication ne fait appel à aucun
  modèle.
- **Elle ne décide pas** de ce qui est publiable, ni de ce qui relève de la
  fonction publique d'une personne.
- **Elle n'établit aucun lien seule.** Un modèle local peut relever, dans une
  page qu'une association ou une entreprise publie elle-même, les personnes
  nommées et leurs liens avec d'autres acteurs locaux (le bureau d'une
  association, par exemple). Ce sont des propositions : elles restent dans
  l'atelier, et aucune ne sort sans qu'un humain l'ait vérifiée à la source.

## 4. Nos engagements

1. **Aucune donnée non publiée** n'est traitée par un modèle exécuté hors de
   l'Union européenne.
2. **L'extraction se fait sur nos machines**, avec des modèles à poids ouverts
   dont la version est figée. Une extraction doit pouvoir être refaite à
   l'identique dans un an : un service distant qui change de modèle sans
   prévenir ne le permet pas.
3. **L'IA propose, un humain dispose.** Aucune donnée n'est publiée sur la
   seule foi d'un modèle.
4. **La trace est gardée.** Pour chaque valeur extraite avec l'aide d'un
   modèle : quel modèle, sous quelle version exacte, et qui l'a validée. La
   version exacte d'un modèle local est son empreinte (l'identifiant que donne
   le serveur de modèles, par exemple `5642e97495e1`), pas son nom : un même
   nom peut désigner deux versions successives.
   *Depuis le 21/10/2026, le modèle et son empreinte sont conservés avec
   chaque saisie et chaque marché extrait.*
5. **La dépendance est mesurée et a une porte de sortie.** Nous publions, une
   fois par an, ce qui dépend d'un service hors de l'Union et par quoi nous le
   remplacerions. Les portes de sortie à ce jour :
   - **pour le code** : des modèles à poids ouverts exécutés localement — Qwen
     (Alibaba), déjà employé au banc d'extraction, et Devstral Small 2 (Mistral,
     licence Apache 2.0), européen et assez léger pour un poste de 32 Go. Leur
     niveau reste en deçà d'un assistant de pointe : la sortie coûterait du
     temps, pas la faisabilité. Épreuve du 20/10/2026 : deux tâches réelles du
     moteur (corriger un défaut, écrire une fonction depuis sa spécification),
     jugées par des essais, trois fois chacune, sur le poste de 32 Go — la
     première consiste à extraire le domaine d'une adresse web (`domain_of`),
     la seconde à valider la trace du modèle décrite au point 4 (`trace_ia`) :

     | Modèle | Corriger un défaut | Écrire depuis la spécification | Durée moyenne |
     |---|---|---|---|
     | Qwen 3.8 27B | 3/3 | 3/3 | 28 s et 82 s |
     | Devstral Small 2 24B | 1/3 | 2/3 | 7 s et 18 s |

     Les échecs de Devstral sont de vrais défauts, pas des détails de forme :
     un préfixe « WWW. » en majuscules non retiré, un champ obligatoire de la
     spécification non exigé. Qwen est donc la porte de sortie éprouvée ;
     Devstral, européen mais moins fiable, n'est pas retenu pour coder seul.
     Deux tâches ne font pas un niveau : l'épreuve dit que la sortie est
     possible, pas qu'elle serait sans coût. Les durées sont indicatives : le
     poste faisait tourner d'autres applications pendant l'épreuve ;
   - **pour un service hébergé** : Mistral, aux conditions suivantes, lues dans
     sa documentation et son accord de traitement des données le 20/10/2026 :
     - passer par le point d'accès européen (`api.eu.mistral.ai`) : l'inférence
       y est faite dans l'Union ou l'AELE, pour 10 % de plus. Le point d'accès
       par défaut ne s'engage sur aucun lieu ;
     - n'utiliser que les fonctions qu'il couvre : les agents, le traitement
       par lots et les fichiers n'y sont pas, et une charge qui s'en sert perd
       la garantie ;
     - activer la non-conservation des données (*zero data retention*) : le
       lieu de stockage, lui, n'est pas garanti par la région ;
     - refuser l'entraînement sur nos données : Mistral s'y autorise sauf
       opposition du client ;
     - savoir que la gestion du compte (clés, facturation, statistiques
       d'usage) reste hors de la région, et que des sous-traitants hors de
       l'Union restent possibles, sous les garanties prévues par le RGPD.

## 5. Qui reprend le moteur

Le moteur est libre. Une commune ou une association qui le reprend n'est pas
liée par cette charte : le code permet un service distant, et nous ne
l'interdisons pas. Nous le déconseillons pour tout ce qui touche la base de
travail, et le dispositif oblige à le décider explicitement.
