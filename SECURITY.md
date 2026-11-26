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
- elle exerce en entreprise individuelle (une forme juridique de personne
  physique, ou, pour une fiche d'entreprise sans forme juridique, un lien qui
  la rattache à une personne de même nom), en activité ou non, et un fait
  public la désigne : un marché public dont elle est titulaire, un paiement ou
  une subvention qu'une personne publique lui verse, ou un acte de la
  collectivité qui la cite, hors comme demandeur ou bénéficiaire (le
  bénéficiaire ne compte que par le versement publié) et hors un acte dont le
  titre publié désigne quelqu'un par son rôle. Sa fiche d'entreprise ne se
  publie qu'à ce titre : nom, SIREN, activité, dates, commune de rattachement,
  site confirmé et faits publiés qui la désignent (une fiche de lieu ou de
  service publie à la place catégorie, opérateur et horaires, sans SIREN),
  sans adresse ni position (sauf un local placé à la main à l'atelier). Sans
  un tel fait, sa fiche d'entreprise n'est pas publiée ; elle reste dans la
  base de l'instance, rien n'y est supprimé.

Pour une entreprise individuelle sans fiche publiée (aucun fait public,
classée hors de la commune, ou écartée à l'atelier), le nom est retiré d'un
flux d'argent (identifiant, nom, description et note effacés ; restent le
montant, l'année, le type, la partie publique, la source et le lien vers
l'acte ou la pièce ; un flux dont l'autre partie n'est pas publique n'est pas
publié) et d'un marché (identifiant et nom effacés ; restent l'objet, masqué
de ce nom, le montant, l'acheteur et la source). L'entreprise est reconnue par
son identifiant, son SIREN, ou le nom exact de sa fiche ou de ses marchés :
une autre graphie n'est pas retirée, ni un nom qui est aussi, en tout ou
partie, celui d'une fiche publiée (structure de même nom, élu homonyme). Le
demandeur d'un permis d'urbanisme n'est pas publié quand c'est une entreprise
individuelle, publiée ou non, ou quand sa forme juridique est inconnue :
restent le numéro de l'autorisation, les dates, le lieu-dit, la parcelle, les
surfaces et la nature du projet, sans adresse. Une entreprise individuelle
n'entre pas dans le rapprochement entre sites par SIREN. Un contrôle lit les
flux, les marchés, les fiches, le fil d'actualité et les avis de marché, et
refuse la mise en ligne si un nom d'au moins deux mots d'une telle entreprise
y reste, hors les réserves ci-dessus ; ce contrôle ne lit rien d'autre : ni la
recherche, ni les titres d'actes, ni les dossiers et séances, ni les permis.
Trois exceptions restent, et elles sont dites ici :

- une entreprise individuelle classée hors de la commune (intercommunalité)
  que la commune désigne garde son nom, sans fiche ni lien, sur un marché de
  la commune qui la désigne par sa fiche et sur un versement de la commune ;
- une entreprise individuelle dont la forme juridique est inconnue, que ne
  rattache aucune personne de même nom (ou qui n'est pas une fiche
  d'entreprise), est traitée comme une société : fiche et position comprises
  si son périmètre le permet, et, sans fiche, son nom reste sur ses flux et
  ses marchés ;
- le texte cité d'une délibération est reproduit tel que publié, hors domicile
  et naissance ; les dossiers et les séances, rédigés par le projet puis
  relus, sortent tels que relus ; le titre d'un acte (annonce BODACC comprise,
  sauf celle d'une personne physique reconnue, qui n'est pas publiée) masque
  les noms précédés d'une civilité et ceux de personnes connues de la base,
  l'ordre du jour ceux de personnes connues de la base, aucun des deux un
  autre nom. Tous peuvent nommer une entreprise individuelle sans fiche, et un
  avis de marché garde le nom sous les mêmes réserves que le reste.

Hors de ces cas et de l'exception sur les noms sans civilité, une personne
citée dans le titre d'un acte est désignée par son rôle : « un particulier »
pour un pétitionnaire ou le bénéficiaire d'une aide, « un agent » et sa
fonction pour une mesure individuelle, « le président de l'association » pour
un bénévole. Le titre garde la nature de la décision, son montant et le lien
vers l'acte publié par la collectivité, où il se lit en entier. Un titre
réduit à un nom, sans décision, n'est pas publié.

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
