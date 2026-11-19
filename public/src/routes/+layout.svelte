<script>
  import { COMMUNE, SITE_NOM, SITE_URL, STATUT_TYPE } from '#lib/instance.js'
  // La teinte est lue par l'espace de noms, pas par un import nommé : un
  // `instance.js` généré avant elle (sans `TEINTE`) ne casse pas le build, il
  // retombe sur pétrole.
  import * as instance from '#lib/instance.js'
  import { pageStore, updatedStore } from '#lib/etat-app'
  const page = pageStore()
  const updated = updatedStore()
  import { goto, beforeNavigate } from '$app/navigation'
  import EtatInstance from '#lib/components/EtatInstance.svelte'
  import { PORTAIL_URL } from '#lib/portail.js'

  // Chargé par +layout.server.js : la date de dernière collecte, qui rend le
  // statut vérifiable au lieu d'être seulement déclaré.
  export let data

  // L'icône de l'onglet dit le statut, comme le bandeau : la vigie est à son
  // poste (point plein) dès que quelqu'un tient le site sur place ; tant que
  // c'est une démonstration, le nid est vide (anneau).
  const icone = STATUT_TYPE === 'demonstration' ? '-demonstration' : ''

  // La teinte de la commune, dans la liste fermée de collectors/config.py
  // (validée à la génération d'instance.js). Posée sur <html> par un script
  // de <head>, avant le premier rendu : pas de saut de couleur au chargement.
  // Sans JavaScript, l'attribut manque et la teinte retombe sur l'encre.
  const TEINTE = instance.TEINTE || 'petrole'
  const poserTeinte = `<script>document.documentElement.dataset.teinte=${JSON.stringify(TEINTE)}</` + 'script>'

  // La tuile du portail (favicon.svg de vigie-civique.fr), couleurs fixes, en
  // en-tête et au pied. Décor : le nom est écrit à côté. Le favicon de
  // l'onglet, lui, garde sa variante de statut. Une chaîne et non un snippet :
  // Svelte refuse `{@render}` dans un composant qui a encore un `<slot>`.
  const tuile = '<svg class="tuile" viewBox="-8.5 -12 112 112" width="36" height="36" aria-hidden="true" focusable="false"><rect x="-8.5" y="-12" width="112" height="112" rx="22" fill="#28353A"/><g stroke="#fff" fill="none"><line x1="25" y1="8" x2="31" y2="36" stroke-width="5"/><line x1="70" y1="8" x2="64" y2="36" stroke-width="5"/><line x1="20" y1="36" x2="75" y2="36" stroke-width="5"/><line x1="47.5" y1="36" x2="47.5" y2="78" stroke-width="4.5"/><line x1="8" y1="78" x2="87" y2="78" stroke-width="5"/></g><circle cx="47.5" cy="20.5" r="7" fill="#CA240E"/></svg>'

  // Une version plus récente du site a été publiée pendant que cet onglet était
  // ouvert : on quitte la navigation interne pour un vrai chargement. Sans ça,
  // le client continue de réclamer des fragments `/_app/immutable/` empreintés
  // qui n'existent plus chez l'hébergeur, et le lecteur doit rafraîchir à la
  // main pour voir quoi que ce soit. Suppose `version.pollInterval` réglé dans
  // vite.config.js (greffon `sveltekit`).
  beforeNavigate(({ to, willUnload, cancel }) => {
    if ($updated && to?.url && !willUnload) {
      cancel()
      location.href = to.url.href
    }
  })

  // Public organisé par QUESTION citoyenne (modèle CivLab), pas par table.
  //
  // 30/09/2026 : sixième entrée, « Conseil en clair », à côté de « Qui décide »
  // — décidé par Julien. Une séance lue et relue est une question de départ,
  // pas une page de contexte.
  // Réduit de huit entrées à cinq le 11/08/2026 : huit portes de même poids ne
  // se retiennent pas. « Le territoire », « Environnement » et « Vie locale »
  // restent atteignables depuis l'accueil, le menu complet et le pied de page :
  // ce sont des pages de contexte, pas des questions de départ.
  // « Qui agit ? » mène à l'annuaire, pas à la carte : la carte ne montre que
  // les acteurs géolocalisés, l'annuaire les couvre tous.
  //
  // 01/10/2026 : « Conseils en clair » et « Dossiers thématiques » sont mis en
  // avant (`fort`) — demandé par Julien. La mise en avant suit le contenu :
  // sans séance relue, l'entrée reste ordinaire ; sans dossier publié,
  // « Dossiers » ne monte pas dans l'en-tête et reste au pied de page.
  //
  // 04/10/2026 : un libellé par page, le même partout — en-tête, `<title>`,
  // `<h1>`, fil d'Ariane, pied de page. Les trois questions gardent donc leur
  // point d'interrogation, comme leur titre de page (maquette v2, 09/11/2026).
  $: nav = [
    { href: '/conseils',        label: 'Les conseils en clair', fort: data?.aConseils },
    data?.aDossiers && { href: '/dossiers', label: 'Dossiers thématiques', fort: true },
    { href: '/qui-decide',      label: 'Qui décide ?' },
    { href: '/argent',          label: 'Où va l’argent ?' },
    { href: '/acteurs-publics', label: 'Qui agit ?', aussi: ['/carte', '/entite'] },
    { href: '/comprendre',      label: 'Comprendre' },
  ].filter(Boolean)
  $: path = $page.url.pathname
  // La carte et les fiches acteur appartiennent à « Qui agit ? » : sans ça,
  // aucune entrée n'était marquée une fois qu'on y arrivait.
  $: estActif = (n) =>
    path.startsWith(n.href) || (n.aussi || []).some((p) => path.startsWith(p))

  // La recherche du menu : un simple submit vers /recherche?q=, qui filtre
  // l'index côté client (le public est statique). Dès 80em, l'en-tête n'a plus
  // de champ mais une loupe qui mène à /recherche, sur toutes les pages.
  let q = ''
  let menuOuvert = false

  function chercher(e) {
    e.preventDefault()
    menuOuvert = false
    const v = q.trim()
    goto(v ? `/recherche?q=${encodeURIComponent(v)}` : '/recherche')
  }

  // Le menu ne doit pas rester ouvert par-dessus la page d'arrivée.
  $: if (path) menuOuvert = false
</script>

<!-- Adresse canonique. Un site publié sur un hébergeur répond souvent à deux
     adresses : son domaine et le sous-domaine de l'hébergeur, que celui-ci garde
     actif et qui n'est pas dans la zone du domaine — aucune redirection ne peut
     donc le rattraper. Sans cette balise, les deux se concurrencent et l'autorité
     de référencement reste chez l'hébergeur, qui n'appartient pas au projet.
     Posée une seule fois ici : chaque page en hérite.

     La condition est DANS la balise et non autour : `<svelte:head>` ne peut pas
     se trouver à l'intérieur d'un bloc, le compilateur refuse. -->
<svelte:head>
  {@html poserTeinte}
  <!-- Le PNG d'abord, pour les navigateurs qui ne lisent pas une icône SVG ;
       les autres prennent le SVG, qui suit le thème clair ou sombre. Ces PNG
       sont les seuls binaires du dépôt : scripts/build_kit.py les nomme un
       par un (IMAGES_ADMISES). -->
  <link rel="icon" href="/favicon{icone}-32.png" sizes="32x32" type="image/png" />
  <link rel="icon" href="/favicon{icone}.svg" type="image/svg+xml" />
  <link rel="apple-touch-icon" href="/apple-touch-icon{icone}.png" />
  {#if SITE_URL}
    <link rel="canonical" href="{SITE_URL}{path}" />
    <meta property="og:url" content="{SITE_URL}{path}" />
    <meta property="og:site_name" content={SITE_NOM} />
    <meta property="og:type" content="website" />
    <meta property="og:locale" content="fr_FR" />
    <meta name="twitter:card" content="summary" />
  {/if}
</svelte:head>

<svelte:window on:keydown={(e) => { if (e.key === 'Escape') menuOuvert = false }} />

<div class="app">
  <!-- RGAA 12.7 : premier élément focalisable, il saute l'en-tête et la
       navigation ; caché tant qu'il n'a pas le focus. -->
  <a class="evitement" href="#contenu">Aller au contenu</a>
  <header class="entete">
    <div class="gouttiere entete-in">
      <a class="marque" href="/">
        {@html tuile}
        <span class="marque-nom"><strong>Vigie Civique</strong><span class="plaque">{COMMUNE}</span></span>
      </a>

      <nav class="principale" aria-label="Navigation principale">
        {#each nav as n}
          <a href={n.href} class:fort={n.fort} aria-current={estActif(n) ? 'page' : undefined}>{n.label}</a>
        {/each}
      </nav>

      <div class="outils">
        <a class="loupe" href="/recherche" aria-label="Rechercher">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.5" y1="15.5" x2="21" y2="21"/></svg>
        </a>
        <!-- L'adhésion mène à « Soutenir », page du site : le départ vers
             HelloAsso se fait de là, et le lecteur d'écran l'apprend dès ici.
             La classe n'est pas `adhesion` : des listes de filtres (Brave,
             uBlock) masquent `.adhesion` sur tous les sites. -->
        <a class="appui" href="/soutenir">Adhérer à l'association<span class="visually-hidden"> À Bout de Champ ; l'adhésion se fait ensuite sur HelloAsso, site extérieur</span></a>

        <!-- Menu sans JavaScript : un <details>. Sous 80em il porte toute la
             navigation et la recherche ; sous 36em, l'adhésion aussi. -->
        <details class="menu" bind:open={menuOuvert}>
          <summary aria-label="Menu"><span class="burger" aria-hidden="true"></span></summary>
          <nav class="menu-panneau" aria-label="Navigation complète">
            {#each nav as n}
              <a href={n.href} class:fort={n.fort} aria-current={estActif(n) ? 'page' : undefined}>{n.label}</a>
            {/each}
            <a href="/territoire" aria-current={path.startsWith('/territoire') ? 'page' : undefined}>Le territoire</a>
            <a href="/environnement" aria-current={path.startsWith('/environnement') ? 'page' : undefined}>Environnement</a>
            {#if !data?.aDossiers}<a href="/dossiers" aria-current={path.startsWith('/dossiers') ? 'page' : undefined}>Dossiers thématiques</a>{/if}
            <a href="/vie-locale" aria-current={path.startsWith('/vie-locale') ? 'page' : undefined}>Vie locale</a>
            <!-- `action` et `name` : sans JavaScript, le formulaire envoie quand
                 même la requête à /recherche (la page l'y lit au montage). -->
            <form action="/recherche" method="get" on:submit={chercher} role="search" aria-label="Recherche, menu">
              <input class="champ" type="search" name="q" bind:value={q} placeholder="Sujet, nom, montant"
                     aria-label="Rechercher un sujet, un nom, un montant" autocomplete="off" />
            </form>
            <a class="appui-menu" href="/soutenir">Adhérer à l'association<span class="visually-hidden"> À Bout de Champ ; l'adhésion se fait ensuite sur HelloAsso, site extérieur</span></a>
          </nav>
        </details>
      </div>
    </div>
  </header>

  <main id="contenu" tabindex="-1">
    <EtatInstance derniereCollecte={data?.derniereCollecte} />
    <slot />
  </main>

  <footer class="pied">
    <div class="gouttiere">
      <a class="marque" href="/">
        {@html tuile}
        <span class="marque-nom"><strong>Vigie Civique</strong><span class="plaque">{COMMUNE}</span></span>
      </a>
      <!-- Une ligne de liens. La phrase qui énumérait les sources est sur
           /methode ; « Dossiers thématiques » ne reste ici que si l'en-tête ne
           le porte pas. -->
      <nav aria-label="Pied de page">
        <a href="/nouveautes">Ce qui a changé</a>
        <a href="/territoire">Le territoire</a>
        <a href="/environnement">Environnement</a>
        <a href="/vie-locale">Vie locale</a>
        {#if !data?.aDossiers}<a href="/dossiers">Dossiers thématiques</a>{/if}
        <a href="/methode">Méthode &amp; sources</a>
        <a href="/corrections">Corrections</a>
        <a href="/repliquer">Répliquer</a>
        <a href="/soutenir">Soutenir</a>
        <a href="/mentions-legales">Mentions légales</a>
        <a href="/contact">Droit de réponse</a>
        <a href={PORTAIL_URL}>Une autre commune&nbsp;? Le portail</a>
      </nav>
    </div>
  </footer>
</div>

<style>
  /* ------------------------------------------------------------------
     Polices du portail (SIL OFL 1.1 : static/fonts/OFL-*.txt ; provenance
     et empreintes : static/fonts/PROVENANCE-IBM-Plex.txt). Sous-ensemble
     latin, 105 932 octets les cinq. Servies par l'instance, jamais par le
     portail : une vigie tient seule. Fraunces et Plex Sans 400 sont
     préchargées dans app.html.
     ------------------------------------------------------------------ */
  @font-face { font-family: "Fraunces"; src: url(/fonts/fraunces-latin-normal.woff2) format("woff2"); font-weight: 100 900; font-style: normal; font-display: swap; }
  @font-face { font-family: "IBM Plex Sans"; src: url(/fonts/ibm-plex-sans-latin-400.woff2) format("woff2"); font-weight: 400; font-style: normal; font-display: swap; }
  @font-face { font-family: "IBM Plex Sans"; src: url(/fonts/ibm-plex-sans-latin-600.woff2) format("woff2"); font-weight: 600; font-style: normal; font-display: swap; }
  @font-face { font-family: "IBM Plex Mono"; src: url(/fonts/ibm-plex-mono-latin-400.woff2) format("woff2"); font-weight: 400; font-style: normal; font-display: swap; }
  @font-face { font-family: "IBM Plex Mono"; src: url(/fonts/ibm-plex-mono-latin-500.woff2) format("woff2"); font-weight: 500; font-style: normal; font-display: swap; }
  /* Repli pendant le chargement : Arial recalé sur Plex Sans (valeurs du
     portail, estimées et non mesurées). */
  @font-face { font-family: "Plex Repli"; src: local("Arial"); size-adjust: 101%; ascent-override: 101.5%; descent-override: 27.2%; line-gap-override: 0%; }

  /* ------------------------------------------------------------------
     La teinte de la commune : liste FERMÉE de huit (collectors/config.py),
     posée en `data-teinte` sur <html>. --commune vaut en clair, --commune-s
     en sombre. Chacune tient au moins 5,9:1 sur papier et carte en clair,
     5,8:1 en sombre, et s'écarte de l'encre d'au moins 1,98:1 (maquette du
     09/11/2026, teintes.html). :where() les garde à spécificité nulle.
     Quatre rôles et pas un de plus : 2e ligne du titre de l'accueil, plaque,
     filet du kicker, navigation active. Jamais les liens ni les graphiques.
     « framboise » (#a8316c) n'est qu'à 36° de --depense : capture de
     contrôle des pages budget avant de l'attribuer à une commune.
     ------------------------------------------------------------------ */
  :global(:where([data-teinte="petrole"]))   { --commune: #1f6683; --commune-s: #4ea8cc; }
  :global(:where([data-teinte="prusse"]))    { --commune: #2f60a0; --commune-s: #73a1dc; }
  :global(:where([data-teinte="indigo"]))    { --commune: #4b56b6; --commune-s: #8f98e5; }
  :global(:where([data-teinte="violet"]))    { --commune: #6b4cb2; --commune-s: #a891df; }
  :global(:where([data-teinte="prune"]))     { --commune: #83469b; --commune-s: #be8bd3; }
  :global(:where([data-teinte="mure"]))      { --commune: #904189; --commune-s: #cd86c7; }
  :global(:where([data-teinte="framboise"])) { --commune: #a8316c; --commune-s: #db81ae; }
  :global(:where([data-teinte="mousse"]))    { --commune: #4d681e; --commune-s: #84aa41; }

  /* ------------------------------------------------------------------
     Mode sombre, sur la préférence du système (ou `data-theme="dark"`).
     Mêmes jetons que le portail en sombre.

     ÉCRIT AVANT le :root clair, exprès : `scripts/verifier_build.mjs` lit
     la DERNIÈRE valeur de chaque jeton dans ce fichier et ne mesure que
     le clair. Les sélecteurs sombres ont une spécificité plus haute
     (0,2,0) : l'ordre ne change rien à la cascade. Contrastes sombres
     mesurés à part (description de la PR).
     ------------------------------------------------------------------ */
  @media (prefers-color-scheme: dark) {
    :global(:root:not([data-theme="light"])) {
      --papier: #12191c;  --carte: #1b2428;  --blanc: #1b2428;
      --encre: #ECE6D8;   --gris: #A9B6B9;   --gris-clair: #A1AFB2;
      --ardoise: #ECE6D8; --ardoise-fonce: #ECE6D8; --ardoise-pale: #223036;
      --ambre: #e0b45c;   --ambre-pale: #2a2418;
      --recette: #6fc39a; --depense: #f08a7c;
      --filet: rgba(236,230,216,.2); --trait: rgba(236,230,216,.2); --trait-pale: rgba(236,230,216,.1);
      --teinte: var(--commune-s, var(--encre));
      --pied-fond: #0b1113; --pied-texte: #d9e1e3;
      --ombre: none;
      --ombre-forte: 6px 6px 0 rgba(236,230,216,.28);
      --tuile-contour: rgba(236,230,216,.28);
      /* Fonds d'état des pages : voir le bloc clair. */
      --recette-pale: #1c3027; --recette-voile: #172520; --recette-bord: #2f5a44;
      --depense-pale: #382321; --violet-pale: #2a2332; --violet: #c9b2e0;
      --vert-pale: #163429; --vert-fonce: #8fdcbc; --vert-texte: #86d6b2;
      --indigo-pale: #232a45; --indigo-voile: #1e2438; --indigo-bord: #39436b; --lavande-voile: #231f2c;
      --orange-pale: #382814; --orange: #f0b36a; --orange-voile: #2c2116; --orange-bord: #5a3d1c;
      --brique-texte: #f2a27c; --ocre-pale: #33291a; --ocre: #e6bd72;
      color-scheme: dark;
    }
  }
  :global(:root[data-theme="dark"]) {
    --papier: #12191c;  --carte: #1b2428;  --blanc: #1b2428;
    --encre: #ECE6D8;   --gris: #A9B6B9;   --gris-clair: #A1AFB2;
    --ardoise: #ECE6D8; --ardoise-fonce: #ECE6D8; --ardoise-pale: #223036;
    --ambre: #e0b45c;   --ambre-pale: #2a2418;
    --recette: #6fc39a; --depense: #f08a7c;
    --filet: rgba(236,230,216,.2); --trait: rgba(236,230,216,.2); --trait-pale: rgba(236,230,216,.1);
    --teinte: var(--commune-s, var(--encre));
    --pied-fond: #0b1113; --pied-texte: #d9e1e3;
    --ombre: none;
    --ombre-forte: 6px 6px 0 rgba(236,230,216,.28);
    --tuile-contour: rgba(236,230,216,.28);
    /* Fonds d'état des pages : voir le bloc clair. */
    --recette-pale: #1c3027; --recette-voile: #172520; --recette-bord: #2f5a44;
    --depense-pale: #382321; --violet-pale: #2a2332; --violet: #c9b2e0;
    --vert-pale: #163429; --vert-fonce: #8fdcbc; --vert-texte: #86d6b2;
    --indigo-pale: #232a45; --indigo-voile: #1e2438; --indigo-bord: #39436b; --lavande-voile: #231f2c;
    --orange-pale: #382814; --orange: #f0b36a; --orange-voile: #2c2116; --orange-bord: #5a3d1c;
    --brique-texte: #f2a27c; --ocre-pale: #33291a; --ocre: #e6bd72;
    color-scheme: dark;
  }

  /* ------------------------------------------------------------------
     Jetons C7, ceux du portail (portail-local/app/globals.css) : papier,
     encre, et le rouge du point en décor seul, jamais en texte. Les anciens
     noms (--ardoise, --blanc, --trait…) restent : quarante pages les lisent.
     Dans C7, l'ardoise EST l'encre ; liens et boutons sont donc à l'encre,
     la teinte de la commune n'en est jamais le signal.
     Les couleurs de données (--recette, --depense, --ambre) restent hors de
     la marque, inchangées en clair.
     Toute couleur de TEXTE tient 4,5:1 sur papier, blanc et les deux fonds
     pâles — `scripts/verifier_build.mjs` le mesure et refuse le build sinon.
     Un jeton mesuré par lui s'écrit seul sur sa ligne.
     ------------------------------------------------------------------ */
  :global(:root) {
    --papier:     #FBF7EE;
    --carte:      #FFFFFF;
    --blanc:      #ffffff;
    --encre:      #28353A;
    --gris:       #4B5A5F;
    --gris-clair: #56666B;
    --ardoise:    #28353A;
    --ardoise-fonce: #1d272b;
    --ardoise-pale:  #F2EEE4;
    --ambre:      #8e620e;
    --ambre-pale: #f7f1e4;
    --recette:    #2c6e4f;
    --depense:    #a4453a;
    --rouge:      #CA240E;
    --filet:      rgba(40,53,58,.22);
    --trait:      rgba(40,53,58,.18);
    --trait-pale: rgba(40,53,58,.09);
    --teinte:     var(--commune, var(--encre));
    --pied-fond:  #28353A;
    --pied-texte: #d9e1e3;
    --ombre-forte: 6px 6px 0 var(--encre);
    --tuile-contour: transparent;

    /* Fonds d'état des pages (badges, bandeaux, étiquettes), écrits en dur
       dans les pages jusqu'au 09/11/2026 et repris ici À L'IDENTIQUE en
       clair ; leur valeur sombre est dans les deux blocs plus haut. Chaque
       couple texte/fond tient 4,5:1 dans les deux thèmes (mesuré, PR #243). */
    --recette-pale:  #e7f0ea;
    --recette-voile: #f2f7f4;
    --recette-bord:  #bbf7d0;
    --depense-pale:  #f6e7e5;
    --violet-pale:   #efeaf2;
    --violet:        #62487a;
    --vert-pale:     #d1fae5;
    --vert-fonce:    #065f46;
    --vert-texte:    #047857;
    --indigo-pale:   #e0e7ff;
    --indigo-voile:  #eef2ff;
    --indigo-bord:   #c7d2fe;
    --lavande-voile: #faf5ff;
    --orange-pale:   #fdebd0;
    --orange:        #8a4b00;
    --orange-voile:  #fff7ed;
    --orange-bord:   #fed7aa;
    --brique-texte:  #9a3412;
    --ocre-pale:     #fdf1e0;
    --ocre:          #8a5a10;

    --titre:   "Fraunces", Georgia, "Times New Roman", serif;
    --sans:    "IBM Plex Sans", "Plex Repli", Arial, sans-serif;
    --mono:    "IBM Plex Mono", ui-monospace, Menlo, monospace;
    /* Anciens noms, lus par les pages. */
    --display: var(--titre);
    --texte:   var(--sans);
    --data:    var(--mono);

    --r-objet: 22px; --r-carte: 12px; --r-petit: 4px;
    --rayon: var(--r-petit);
    --t-contour: 2px; --t-traverse: 4px; --t-signature: 5px;
    --e1: 4px; --e2: 8px; --e3: 12px; --e4: 16px; --e5: 24px; --e6: 32px; --e7: 48px; --e8: 64px;
    --large: 1120px;
    --ombre: 0 1px 2px rgba(40,53,58,.05), 0 6px 18px rgba(40,53,58,.05);
    color-scheme: light;
  }

  :global(*) { box-sizing: border-box; }
  /* L'en-tête est collant : une ancre ne doit pas finir dessous. */
  :global(html) { scroll-padding-top: 88px; }
  /* Lu par les lecteurs d'écran, invisible à l'œil : titre de tableau qui
     double un intertitre visible, « (nouvelle fenêtre) » d'un lien externe. */
  :global(.visually-hidden) {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }
  .evitement {
    position: absolute; left: 12px; top: -60px; z-index: 100;
    padding: 12px 16px; color: var(--papier); background: var(--encre); font-weight: 600;
  }
  .evitement:focus { top: 12px; }
  :global(main:focus) { outline: none; }
  /* RGAA 13.8 : qui a demandé moins de mouvement à son système n'en reçoit pas. */
  @media (prefers-reduced-motion: reduce) {
    :global(*), :global(*::before), :global(*::after) {
      animation-duration: .01ms !important; animation-iteration-count: 1 !important;
      transition-duration: .01ms !important; scroll-behavior: auto !important;
    }
  }
  :global(body) {
    margin: 0; background: var(--papier); color: var(--encre);
    font-family: var(--sans); line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  :global(:is(button, input, select, textarea)) { font-family: inherit; }
  :global(a) { color: var(--ardoise); text-decoration: none; }
  :global(a:hover) { text-decoration: underline; }
  /* Dans une phrase, un lien ne se reconnaît pas à sa seule couleur : il est
     souligné. Les liens de navigation, de liste et de tuile ne le sont pas. */
  :global(p a) { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
  :global(p a:hover) { text-decoration-thickness: 2px; }
  :global(:focus-visible) { outline: 3px solid var(--encre); outline-offset: 3px; border-radius: 2px; }
  :global(h1), :global(h2), :global(h3) { font-family: var(--titre); font-weight: 600; letter-spacing: -.01em; }
  :global(h1) { text-wrap: balance; }

  .app { min-height: 100vh; display: flex; flex-direction: column; }
  main { flex: 1; }

  .gouttiere {
    max-width: var(--large); margin: 0 auto;
    padding-left: max(16px, env(safe-area-inset-left, 0px));
    padding-right: max(16px, env(safe-area-inset-right, 0px));
  }
  @media (min-width: 700px) {
    .gouttiere {
      padding-left: max(32px, env(safe-area-inset-left, 0px));
      padding-right: max(32px, env(safe-area-inset-right, 0px));
    }
  }

  /* ── En-tête ─────────────────────────────────────────────────────── */
  .entete { position: sticky; top: 0; z-index: 1000; background: var(--papier); border-bottom: 1px solid var(--filet); }
  .entete .gouttiere { max-width: 1440px; }
  .entete-in {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center;
    gap: var(--e4); min-height: 72px; padding-block: var(--e2);
  }
  .marque { display: flex; align-items: center; gap: var(--e3); min-height: 44px; color: var(--encre); text-decoration: none; }
  .marque:hover { text-decoration: none; }
  /* La tuile telle quelle. En sombre, un contour crème la détache du fond. */
  .marque :global(.tuile) { display: block; width: 36px; height: 36px; flex: none; border-radius: 7px; outline: 1px solid var(--tuile-contour); }
  .marque-nom { display: flex; flex-direction: column; line-height: 1.05; }
  .marque-nom strong { font: 600 20px/1.05 var(--titre); letter-spacing: -.02em; white-space: nowrap; }
  /* Rôle 2 de la teinte : la plaque. */
  .plaque { margin-top: 3px; color: var(--teinte); font: 500 13px/1.2 var(--mono); letter-spacing: .1em; text-transform: uppercase; }

  .principale { display: none; justify-content: center; gap: 14px; }
  .principale a {
    display: inline-flex; align-items: center; min-height: 44px;
    color: var(--gris); font-size: 15px; white-space: nowrap; text-decoration: none;
    border-bottom: 3px solid transparent;
  }
  .principale a:hover { color: var(--encre); text-decoration: none; }
  .principale a.fort { color: var(--encre); font-weight: 600; }
  /* Rôle 4 de la teinte : la navigation active. */
  .principale a[aria-current="page"] { color: var(--encre); border-bottom-color: var(--teinte); }

  .outils { display: flex; align-items: center; gap: var(--e3); justify-self: end; grid-column: 3; }
  .loupe { display: none; }

  /* Adhérer : un bouton plein, en pilule. */
  .appui {
    display: none; align-items: center; min-height: 44px; padding: 0 18px;
    color: var(--papier); background: var(--encre); border-radius: var(--r-objet);
    font-weight: 600; font-size: 15px; text-decoration: none; white-space: nowrap;
  }
  .appui:hover { text-decoration: underline; }
  @media (min-width: 36em) { .appui { display: inline-flex; } }

  .menu { position: relative; }
  .menu > summary {
    display: flex; align-items: center; justify-content: center; width: 44px; height: 44px;
    cursor: pointer; list-style: none; border: var(--t-contour) solid transparent; border-radius: var(--r-carte);
  }
  .menu > summary::-webkit-details-marker { display: none; }
  .menu > summary:hover { border-color: var(--filet); }
  .burger { position: relative; width: 20px; height: 2px; background: var(--encre); }
  .burger::before, .burger::after { content: ""; position: absolute; left: 0; width: 20px; height: 2px; background: var(--encre); }
  .burger::before { top: -6px; }
  .burger::after { top: 6px; }
  .menu[open] .burger { background: transparent; }
  .menu[open] .burger::before { top: 0; transform: rotate(45deg); }
  .menu[open] .burger::after { top: 0; transform: rotate(-45deg); }
  .menu-panneau {
    position: fixed; left: 0; right: 0; top: 73px;
    max-height: calc(100dvh - 73px); overflow-y: auto; overscroll-behavior: contain;
    padding: var(--e3) max(16px, env(safe-area-inset-right, 0px)) var(--e5) max(16px, env(safe-area-inset-left, 0px));
    background: var(--papier); border-bottom: var(--t-traverse) solid var(--encre);
  }
  .menu-panneau a {
    display: flex; align-items: center; min-height: 48px;
    color: var(--encre); font-size: 17px; text-decoration: none; border-bottom: 1px solid var(--filet);
  }
  .menu-panneau a.fort { font-weight: 600; }
  .menu-panneau a[aria-current="page"] { box-shadow: inset 4px 0 0 var(--teinte); padding-left: 12px; }
  .champ {
    width: 100%; min-height: 44px; margin-top: var(--e4); padding: 0 14px;
    color: var(--encre); background: var(--carte); border: var(--t-contour) solid var(--encre);
    border-radius: var(--r-carte); font: inherit; font-size: 16px;
  }
  .champ::placeholder { color: var(--gris); opacity: 1; }
  .menu-panneau .appui-menu {
    margin-top: var(--e4); justify-content: center; border: 0;
    color: var(--papier); background: var(--encre); border-radius: var(--r-objet); font-weight: 600;
  }
  @media (min-width: 36em) { .menu-panneau .appui-menu { display: none; } }

  /* ── Pied ────────────────────────────────────────────────────────── */
  .pied {
    margin-top: var(--e8); padding-block: var(--e6);
    color: var(--pied-texte); background: var(--pied-fond);
    border-top: 1px solid var(--filet);
  }
  .pied .marque { color: #fff; }
  .pied :global(.tuile) { outline: 1px solid rgba(236,230,216,.28); }
  .pied .plaque { color: var(--pied-texte); }
  .pied nav { display: flex; flex-wrap: wrap; gap: 0 var(--e5); margin-top: var(--e4); }
  .pied nav a {
    display: inline-flex; align-items: center; min-height: 44px; color: var(--pied-texte); font-size: 15px;
    text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px;
    text-decoration-color: rgba(217,225,227,.45);
  }
  .pied nav a:hover { color: #fff; text-decoration-color: currentColor; }
  .pied :focus-visible { outline-color: #fff; }

  /* ── Grand écran : le menu se centre entre la marque et les outils ── */
  @media (min-width: 80em) {
    .principale { display: flex; }
    .loupe {
      display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px;
      border: var(--t-contour) solid var(--encre); border-radius: var(--r-carte);
    }
    .loupe svg { width: 18px; height: 18px; fill: none; stroke: var(--encre); stroke-width: 2.2; stroke-linecap: round; }
    .menu { display: none; }
  }

  @media (prefers-reduced-motion: no-preference) {
    .appui { transition: background-color .12s ease, color .12s ease; }
  }
</style>
