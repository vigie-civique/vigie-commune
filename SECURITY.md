# Signaler un problème

## Une donnée publiée à tort

Si vous constatez sur un site produit par ce dispositif une donnée qui vous
concerne et qui n'a pas à y figurer, écrivez à l'adresse de contact indiquée sur
ce site (page « Mentions légales »). Toute rectification est appliquée puis
**signalée comme telle** : la donnée collectée est conservée à côté de la
rectification, jamais remplacée en silence. Une demande d'effacement fondée,
elle, retire la donnée.

Une personne physique figure sur un site public à deux titres seulement :
une fonction publique, un mandat électif ou une responsabilité inscrite dans un
registre public ; ou son nom dans un acte officiel voté et publié par la
collectivité (le titre d'une délibération, par exemple), cité tel que la
collectivité l'a publié. Dans les deux cas, son domicile, sa date de naissance
et ses coordonnées personnelles sont masqués. Si vous figurez sur un site hors
de ces deux cas, ou si l'une de ces données y apparaît, c'est un défaut du
filtre, et il sera traité comme tel — pas comme une demande de retrait
ordinaire.

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
- une route de l'atelier accessible sans jeton.

## Ce qui n'en est pas une

L'atelier sert la base de travail non filtrée : c'est **voulu**, et c'est
pourquoi `deploy/README.md` recommande de ne pas l'exposer. Un atelier accessible
publiquement est une erreur de déploiement, pas une faille du moteur.
