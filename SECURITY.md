# Signaler un problème

## Une donnée publiée à tort

Si vous constatez sur un site produit par ce dispositif une donnée qui vous
concerne et qui n'a pas à y figurer, écrivez à l'adresse de contact indiquée sur
ce site (page « Mentions légales »). Toute rectification est appliquée puis
**signalée comme telle** : la donnée collectée est conservée à côté de la
rectification, jamais remplacée en silence. Une demande d'effacement fondée,
elle, retire la donnée.

Hors du texte cité d'une délibération, une personne physique n'est nommée sur
un site public que dans trois cas :

- elle exerce une fonction publique ou tient un mandat électif, y compris
  quand un acte la désigne pour représenter la collectivité ;
- elle dirige une entreprise, un registre public l'inscrit comme telle, et
  l'acte la cite à ce titre ;
- elle est titulaire d'un marché public en entreprise individuelle : elle est
  nommée comme titulaire de ce marché, et nulle part ailleurs.

Hors de ces cas, une personne citée dans le titre d'un acte est désignée par
son rôle : « un particulier » pour un pétitionnaire ou le bénéficiaire d'une
aide, « un agent » et sa fonction pour une mesure individuelle, « le président
de l'association » pour un bénévole. Le titre garde la nature de la décision,
son montant et le lien vers l'acte publié par la collectivité, où il se lit en
entier. Un titre réduit à un nom, sans décision, n'est pas publié.

Le texte d'une délibération, quand il est reproduit sous son titre, est cité
tel que la collectivité l'a publié. Quand la recherche du site lit ce texte,
elle n'en retient que les mots courants : aucun nom de personne n'y est indexé
(`scripts/snapshot/recherche.py`, recontrôlé par `scripts/verify_snapshot.py`).
Ce texte n'est pas proposé à l'indexation des moteurs de recherche externes
(`X-Robots-Tag: noindex`, `deploy/nginx-site.conf`).

Quand une décision porte sur la situation d'une personne (créance effacée ou
impayé, aide sociale, logement d'habitation, mesure individuelle concernant un
agent, litige avec un particulier), le site n'en reproduit pas le texte : il en
donne le titre, la date, le montant et le lien vers la pièce publiée par la
collectivité (`motif_sans_extrait`, `scripts/snapshot/textes.py`). Dans le texte
d'une aide à la rénovation d'un bien, l'adresse du bien est masquée
(`masquer_adresses_du_bien`).

Dans tous les cas, un domicile, une date de naissance ou des coordonnées
personnelles n'ont pas vocation à être publiés : un masquage automatique les
retire, titres et textes compris. Si vous figurez sur un site hors de ces cas, ou si
l'une de ces données y apparaît, c'est un défaut du filtre, et il sera traité
comme tel, pas comme une demande de retrait ordinaire.

## Une faille dans le code

Les défauts les plus graves de ce dispositif ne sont pas des failles
d'exécution : ce sont des fuites du filtre de publication. Si vous en trouvez
une, **ne la démontrez pas sur un site en ligne** et ne l'ouvrez pas en issue
publique. Passez par un signalement privé (bouton « Report a vulnerability » de
l'onglet Security), en décrivant le chemin.

Sont particulièrement concernés :

- une donnée `probable` ou `hypothesis` atteignant un site public ;
- une entité hors périmètre publiée comme si elle était de la commune ;
- un fichier d'instance ou une base entrant dans l'archive distribuable ;
- une route de l'atelier accessible sans jeton ;
- un fichier du magasin national partagé qu'un lecteur peut modifier, ou qui
  y entre sans avoir été contrôlé.

## Le magasin national partagé

Plusieurs instances, et le générateur de relevés nationaux, lisent les mêmes
gros fichiers nationaux (`VIGIE_NATIONAL_STORE`, `collectors/national_store.py`).
Quand ce magasin est partagé :

- **un seul compte l'écrit** (`scripts/rafraichir_magasin.py`), qui n'est
  membre d'aucun groupe d'instance ; les instances et le générateur le lisent
  en lecture seule. Un collecteur qui ouvre des documents venus de l'extérieur
  ne doit pas pouvoir réécrire ce que les autres publient ;
- **un fichier n'y entre que contrôlé** (`valider_contenu`) : un `.json` se lit
  jusqu'au bout, un `.gz` se décompresse entièrement, un `.zip` passe son
  contrôle d'intégrité et ne nomme aucun membre absolu ni remontant. Tout
  fichier, ces trois formats comme les autres (`.csv`, `.7z`, classeurs), ne
  doit pas être plus de moitié plus petit que celui qu'il remplace, sauf
  acceptation à la main. Puis remplacement atomique ; un lecteur ne voit
  jamais de fichier partiel ;
- **une URL de catalogue n'est suivie qu'en https** ;
- **un fichier périmé de moins de deux fois sa validité** peut être lu quand
  l'écrivain a du retard, et le rapport du step le dit ; au-delà, il est repris
  à la source, et le step échoue si la source manque. On contrôle l'intégrité
  des fichiers, pas leur authenticité : aucune source ne les signe.

## Ce qui n'en est pas une

L'atelier sert la base de travail non filtrée : c'est **voulu**, et c'est
pourquoi `deploy/README.md` recommande de ne pas l'exposer. Un atelier accessible
publiquement est une erreur de déploiement, pas une faille du moteur.
