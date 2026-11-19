<script>
  import { CODE_POSTAL, COMMUNE_DE, CONTACT_EMAIL, DESTINATAIRES, DIRECTEUR_PUBLICATION, EDITEUR_NOM, EDITEUR_SIEGE, EDITEUR_STATUT, EDITEUR_TELEPHONE, EPCI, HEBERGEUR, PREFECTURE, SITE_NOM } from '#lib/instance.js'
</script>

<svelte:head><title>Mentions légales - {SITE_NOM}</title>
  <meta name="description" content="Mentions légales du site : éditeur, hébergeur, traitement des données et voies de recours." /></svelte:head>

<section>
  <h1>Mentions légales</h1>

  <h2>Éditeur</h2>
  <!--
    L'éditeur vient de `config/instance.json` (clé editeur : nom, statut, siege,
    directeur_publication), comme l'hébergeur. Les constantes EDITEUR_* étaient
    générées et exportées sans que la page les lise : elle affirmait « édité à
    titre non professionnel » même pour une instance portée par une association.
    Une personne morale ne relève pas de l'anonymat de l'article 1-1, II de la
    LCEN : ses nom, siège, téléphone et directeur de la publication sont publics
    (article 1-1, I — numérotation issue de la loi SREN du 21/05/2024 ; c'était
    l'article 6, III avant).
  -->
  {#if EDITEUR_NOM}
    <p>
      Ce site est édité par <strong>{EDITEUR_NOM}</strong>{#if EDITEUR_STATUT}, {EDITEUR_STATUT}{/if}{#if EDITEUR_SIEGE},
      dont le siège est situé {EDITEUR_SIEGE}{/if}, sous le nom d'usage <strong>{SITE_NOM}</strong>.
    </p>
    {#if EDITEUR_TELEPHONE}
      <p>Téléphone : {EDITEUR_TELEPHONE}.</p>
    {/if}
    {#if DIRECTEUR_PUBLICATION}
      <p>Directeur de la publication : {DIRECTEUR_PUBLICATION}.</p>
    {/if}
  {:else}
    <p>
      Ce site est édité à titre non professionnel sous le nom <strong>{SITE_NOM}</strong>.
      Conformément à l'article 1-1, II de la loi n°&nbsp;2004-575 du 21 juin 2004 pour la confiance
      dans l'économie numérique (LCEN), l'éditeur a communiqué ses éléments d'identification
      personnelle à l'hébergeur du site.
    </p>
  {/if}
  <p>Contact : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>

  <!--
    L'hébergeur vient de `config/instance.json` (clé editeur.hebergeur), jamais
    du code : le moteur est repris par d'autres, qui n'hébergent pas là où nous
    hébergeons — cf. la liberté du repreneur.

    Il a pourtant été écrit en dur ici, et une instance a déclaré pendant une
    semaine un hébergeur qu'elle avait quitté, alors que sa propre configuration
    portait le bon. La constante HEBERGEUR était générée, exportée, vérifiée par
    generer_libelles.py — et personne ne la lisait. Un fichier déclarait une
    intention, un autre une capacité, et les deux divergeaient en silence.

    Vide, on le DIT plutôt que de taire une mention qu'impose la LCEN.
  -->
  <h2>Hébergement</h2>
  {#if HEBERGEUR}
    <p>Site public : {HEBERGEUR}.</p>
  {:else}
    <p>
      Hébergeur non renseigné. Cette instance doit compléter la clé
      <code>editeur.hebergeur</code> de son fichier <code>config/instance.json</code> :
      l'article 1-1, I de la LCEN impose de le nommer.
    </p>
  {/if}

  <h2>Finalité</h2>
  <p>
    Information citoyenne sur la gouvernance locale de la commune {COMMUNE_DE} ({CODE_POSTAL}) et de la
    {EPCI}, dont elle est membre : qui décide, comment
    l'argent public est utilisé, quels acteurs interviennent sur le territoire. Le traitement
    poursuit un objectif de transparence démocratique.
  </p>

  <h2>Base légale (RGPD)</h2>
  <ul>
    <li>Article 6.1.f : intérêt légitime (transparence démocratique, information du public).</li>
    <li>Article 9.2.e : pour la seule appartenance politique que les candidats ont eux-mêmes rendue
      publique (listes et étiquettes déposées). Les données de SIRENE, du RNA et des mandats ne relèvent
      pas de l'article 9.</li>
    <!-- L'article 80 de la loi Informatique et Libertés était cité ici. Il
         réserve son régime au journalisme exercé à titre professionnel et à
         l'expression universitaire, artistique ou littéraire : l'éditeur n'en
         relève pas (avis du 27/10/2026). L'article 85 du RGPD suivait : il
         renvoie aux États le soin de concilier, et n'est pas une base légale
         (avis du 14/11/2026). -->
  </ul>

  <h2>Données publiées</h2>
  <p>
    Le site ne publie que des données issues de sources publiques officielles (SIRENE, RNA, DVF,
    BODACC, OFGL, DGFiP, Répertoire national des élus, délibérations municipales et communautaires,
    publications de la {PREFECTURE}), citées et datées.
  </p>
  <!-- La page promettait trois cas (fonction, mandat, registre) alors que les
       titres de délibérations nommaient aussi des particuliers et des agents.
       La règle ci-dessous est celle de SECURITY.md : les deux textes se
       modifient ensemble. -->
  <p>Hors du texte cité d'une délibération, une personne physique n'est nommée que dans trois cas :</p>
  <ul>
    <li>elle exerce une fonction publique ou tient un mandat électif, y compris lorsqu'un acte
      la désigne pour représenter la collectivité ;</li>
    <li>elle dirige une entreprise, un registre public l'inscrit comme telle, et l'acte la cite
      à ce titre ;</li>
    <li>elle est titulaire d'un marché public en entreprise individuelle, et seulement comme
      titulaire de ce marché.</li>
  </ul>
  <p>
    Hors de ces cas, une personne citée dans le titre d'un acte est désignée par son rôle
    (« un particulier », « un agent » et sa fonction, « le président de l'association »). La
    nature de la décision, son montant et le lien vers l'acte publié par la collectivité sont
    conservés. Un titre réduit à un nom, sans décision, n'est pas publié.
    Le texte d'une délibération, quand il est reproduit sous son titre, est cité tel que la
    collectivité l'a publié. Quand la recherche du site lit ce texte, elle n'en retient que
    les mots courants : aucun nom de personne n'y est indexé. Ce texte n'est pas proposé à
    l'indexation des moteurs de recherche externes.
  </p>
  <p>
    Quand une décision porte sur la situation d'une personne (créance effacée ou impayé, aide
    sociale, logement d'habitation, mesure individuelle concernant un agent, litige avec un
    particulier), le site n'en reproduit pas le texte : il en donne le titre, la date, le
    montant et le lien vers la pièce publiée par la collectivité. Dans le texte d'une aide à
    la rénovation d'un bien, l'adresse du bien est masquée.
  </p>
  <p>
    Aucune adresse personnelle, date de naissance ni coordonnée privée n'a vocation à être
    publiée : un masquage automatique les retire, titres et textes compris, et tout défaut
    relevé est corrigé, puis signalé. La méthode complète est décrite sur la page
    <a href="/methode">Méthode</a>.
  </p>

  {#if EDITEUR_NOM}
    <h2>Responsable du traitement</h2>
    <p>{EDITEUR_NOM}{#if EDITEUR_SIEGE}, {EDITEUR_SIEGE}{/if}. Contact : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
  {/if}

  <!--
    Destinataires et transferts hors de l'UE : clé editeur.destinataires de
    config/instance.json, une liste de paragraphes. Comme l'hébergeur, ils
    dépendent de l'éditeur et non du code : écrits ici, ils feraient déclarer
    à un repreneur les prestataires d'un autre.
  -->
  {#if DESTINATAIRES.length}
    <h2>Destinataires et transferts hors de l'Union européenne</h2>
    {#each DESTINATAIRES as paragraphe}
      <p>{paragraphe}</p>
    {/each}
  {/if}

  <h2>Durée de conservation</h2>
  <p>
    Une donnée est conservée tant que l'acte public dont elle provient reste utile à l'information
    du public sur la décision qu'il porte. Elle est retirée à la suite d'une demande d'effacement
    fondée, ou rectifiée à la suite d'une demande de rectification.
  </p>

  <h2>Droits des personnes</h2>
  <p>
    Deux régimes distincts, tous deux via la page
    <a href="/contact">Contact &amp; droit de réponse</a> :
  </p>
  <ul>
    <li>
      <strong>Droits sur vos données</strong> (accès, rectification, opposition, effacement —
      RGPD, articles 15 à 21) : réponse dans un délai d'un mois, prolongeable de deux mois si la
      demande est complexe (article 12.3). Les données liées à un mandat public en cours peuvent
      être conservées malgré une demande d'effacement, après examen, lorsque leur publication est
      nécessaire à l'information du public (article 17.3.a).
    </li>
    <li>
      <strong>Droit de réponse</strong> (LCEN, article 1-1, III) : toute personne nommée ou
      désignée peut le demander dans les trois mois suivant la mise en ligne ; la réponse est
      insérée dans les trois jours suivant sa réception.
    </li>
  </ul>

  <h2>Cookies et mesure d'audience</h2>
  <p>Ce site est entièrement statique : il ne dépose aucun cookie et n'embarque aucun traceur.</p>

  <h2>Licence et réutilisation</h2>
  <p>
    Les données publiées sont sous
    <a href="https://opendatacommons.org/licenses/odbl/1-0/" rel="noopener">Open Database
    License 1.0</a> (ODbL) : réutilisation libre, sous réserve de citer la source et de
    partager à l'identique toute base dérivée redistribuée. Le code qui produit ce site est
    sous licence MIT. Le détail des sources et des attributions figure sur la page
    <a href="/methode">Méthode &amp; sources</a>.
  </p>
  <p>
    Cette licence ne dispense pas du respect du règlement général sur la protection des
    données : une partie de ces données sont des données personnelles, et leur réutilisation
    suppose une base légale propre au réutilisateur.
  </p>
</section>

<style>
  section { max-width: 800px; margin: 0 auto; padding: 1.5rem; line-height: 1.6; }
  h1 { color: var(--encre); } h2 { color: var(--encre); margin-top: 1.4rem; }
</style>
