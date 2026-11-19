<script>
  import { COMMUNE, COMMUNE_DE, EPCI } from '#lib/instance.js'
  import { PORTAIL_URL } from '#lib/portail.js'
  import Mini from '#lib/components/Mini.svelte'
  import SourcesLimites from '#lib/components/SourcesLimites.svelte'
  import { dateFr } from '#lib/dates.js'

  // Accueil refondu le 11/08/2026. C'était une carte plein écran : 1 135 points
  // en quatre couleurs, un encart flottant, et rien qui dise ce qu'est ce site
  // ni ce qu'on peut y chercher. La carte devient /carte ; l'accueil annonce,
  // oriente, puis montre ce qui vient de bouger.
  //
  // 09/11/2026 : mise en page de la maquette v2 (harmonisation des vigies avec
  // le portail). Un seul composant carte (`.carte`, `.carte.forte`), un kicker
  // mono au-dessus de chaque titre de section, quatre tuiles visibles et les
  // autres repliées, la signature du portail une fois sous l'entrée.
  export let data
  $: ({ tableau, recents, agenda, arreteLe, interco, prochains,
       dernieres, dossiers, sujets, anneesSeances } = data)

  const GENRES = {
    acte:      'Acte public',
    'marché':  'Marché public',
    argent:    'Argent public',
    'légal':   'Annonce légale',
    vie:       'Vie locale',
  }

  // Les titres BODACC finissent par la date de l'annonce, déjà affichée dans sa
  // propre colonne : « … — la commune (2026-08-09) » devient « … - la commune ».
  // Le tiret cadratin des sources devient un tiret simple, comme partout ici.
  const titre = (t) => (t || '').replace(/\s*\(\d{4}-\d{2}-\d{2}\)\s*$/, '').replace(/\s—\s/g, ' - ')

  // ── Les séances ─────────────────────────────────────────────────────────
  // « Conseil municipal du 10 septembre 2026 » perd sa date : la colonne de
  // gauche la porte déjà.
  const assemblee = (t) => (t || '').replace(/\s+du\s+\d.*$/i, '')
  const PIECES = {
    proces_verbal: 'procès-verbal',
    compte_rendu: 'compte rendu',
    deliberations: 'registre des délibérations',
    convocation: 'convocation',
    ordre_du_jour: 'ordre du jour',
    annexe: 'annexe',
    piece: 'pièce',
  }
  // ⚖️ Un zéro s'affiche avec sa raison, jamais nu. « 0 délibération » ferait
  // croire à une séance sans décision, alors qu'il dit que le découpage n'a
  // rien su lire dans les pièces — ce n'est pas la même information.
  const compte = (n) =>
    n > 0 ? `${n} délibération${n > 1 ? 's' : ''}`
          : 'aucune délibération lue dans les pièces'
  // L'année ne s'écrit que si elle n'est pas l'année en cours : « 14 avr. »
  // sans plus laissait croire à une séance du printemps dernier quand elle
  // date de 2021. Un flux d'argent n'a que son millésime (`date_approx`) :
  // il s'affiche par son année, jamais par un 31 décembre inventé.
  const ANNEE = new Date().getFullYear().toString()
  const jour = (d) => dateFr(d, d?.slice(0, 4) === ANNEE
    ? { day: 'numeric', month: 'short' }
    : { day: 'numeric', month: 'short', year: 'numeric' })
  const quand = (i) => (i.date_approx && i.annee ? String(i.annee) : jour(i.date))
  const dateLongue = (d) => dateFr(d)
  // La plus récente des dernières séances : ce que l'on sait du dernier
  // conseil quand aucun prochain n'est annoncé.
  $: derniere = dernieres?.length
    ? [...dernieres].sort((a, b) => b.date.localeCompare(a.date))[0] : null
  const dateDuJour = (d) => dateFr(d, { weekday: 'long', day: 'numeric', month: 'long' })

  // Le kicker des séances nomme les assemblées montrées, et elles seules.
  $: assemblees = [...new Set((dernieres || []).map((s) => s.code))]
    .map((c) => ({ cm: 'municipal', cc: 'communautaire' })[c]).filter(Boolean)
  $: kickerSeances = assemblees.join(' · ').replace(/^./, (l) => l.toUpperCase())

  // ── Les tuiles ──────────────────────────────────────────────────────────
  // `repli` est posé par #lib/tableau.server.js : les quatre premières se
  // voient, les autres se déplient.
  $: tete = (tableau || []).filter((t) => !t.repli)
  $: replis = (tableau || []).filter((t) => t.repli)

  // Une valeur longue (« 2,75 à 4,63 € ») prend un corps réduit, sans se
  // couper : à 390 px, elle tenait sur deux lignes.
  const longue = (v) => String(v).length > 9
</script>

<svelte:head>
  <title>{COMMUNE} au clair : la commune par les données publiques</title>
  <meta name="description" content="Qui décide, où va l'argent, qui agit : {COMMUNE} et son intercommunalité, la {EPCI}, à partir des seules données publiques." />
</svelte:head>

<!-- L'entrée : le titre sur deux lignes (la seconde dans la teinte de la
     commune), le lien vers le portail juste dessous (05/11/2026 : les lecteurs
     d'un article arrivent ici sans habiter la commune), deux phrases de
     présentation, la recherche, puis les trois portes (conseil design du
     09/11/2026 : sur un téléphone, menu replié, elles sont la seule entrée
     visible vers le reste du site). Portes : titre seul (option 1, Julien). -->
<section class="gouttiere entree">
  <div class="titre-bloc">
    <h1>Vigie Civique <span>{COMMUNE_DE}</span></h1>
    <p class="autre">Une autre commune&nbsp;? <a href={PORTAIL_URL}>Le portail Vigie Civique&nbsp;<span aria-hidden="true">→</span></a></p>
  </div>
  <!-- Deux phrases (conseil design du 09/11/2026) : ce qu'on trouve ici,
       d'où ça vient. Le reste se lit sur /methode et /couverture. -->
  <p class="presentation">
    Ce site rassemble ce qui est public sur {COMMUNE}&nbsp;: les séances du
    conseil, les décisions et qui les prend, le budget, les marchés, les
    subventions. Tout vient de documents officiels, et chaque page cite sa
    source.
  </p>
  <div class="sous-entree">
    <form class="cherche" action="/recherche" method="get" role="search" aria-label="Recherche dans le site">
      <input class="champ" type="search" name="q" placeholder="Un sujet, un nom, un montant…"
             aria-label="Rechercher un sujet, un nom, un montant" autocomplete="off" />
      <button class="bouton" type="submit">Chercher</button>
    </form>
    <nav aria-label="Les trois questions du site">
      <ul class="portes">
        <li><a href="/qui-decide">Qui décide&nbsp;?</a></li>
        <li><a href="/argent">Où va l'argent&nbsp;?</a></li>
        <li><a href="/acteurs-publics">Qui agit&nbsp;?</a></li>
      </ul>
    </nav>
  </div>
</section>

<!-- La signature du portail : filet de 5 px, point rouge au milieu. Une fois
     par page, et c'est ici. -->
<div class="gouttiere"><div class="signature" aria-hidden="true"></div></div>

<!-- Au conseil : la dernière séance de CHAQUE assemblée, nommée. Les deux ne
     se mêlent pas — ni les mêmes élus, ni le même bulletin de vote. La séance
     mise en clair est la carte forte (contour encre, ombre dure). -->
{#if dernieres?.length || prochains?.length}
  <section class="gouttiere section" aria-labelledby="t-conseils">
    {#if kickerSeances}<p class="kicker">{kickerSeances}</p>{/if}
    <div class="tete-section">
      <h2 id="t-conseils" data-sans-donnees>Les conseils en clair</h2>
      <!-- Le lien dit l'année tant que les séances publiées n'en couvrent
           qu'une : « toutes » promettrait un historique qui n'y est pas. -->
      <a class="tout" href="/conseils">Toutes les séances{anneesSeances?.length === 1 ? ` ${anneesSeances[0]}` : ''} <span aria-hidden="true">→</span></a>
    </div>
    <!-- « En clair » n'était défini que dans « Sources et limites ». Ce que
         le mot recouvre (cf. scripts/snapshot/en_clair.py) : une feuille
         rédigée d'après les documents publiés, aux chiffres contrôlés
         automatiquement, publiée seulement si une personne l'a relue et
         retenue à l'atelier. -->
    <p class="chapo">Une séance <b>en clair</b>, c'est un résumé en langage courant, tiré des documents publiés et relu par une personne avant sa mise en ligne.</p>
    {#if dernieres?.length}
      <div class="grille-2">
        {#each dernieres as s}
          <a class="carte" class:forte={s.en_clair} href="/conseils/{s.id}">
            <span class="etiquette">{s.assemblee}</span>
            <span class="date">{dateLongue(s.date)}</span>
            {#if s.en_clair}{' '}
              <span class="titre-carte">{s.en_clair.titre}</span>
              <span class="tampon">Résumé en clair, relu le {dateLongue(s.en_clair.relu_le)}</span>
            {' '}{:else}{' '}
              <span class="titre-carte">{compte(s.nb_actes)}</span>
              <span class="tampon a-venir">Résumé à venir</span>
            {' '}{/if}
          </a>
        {/each}
      </div>
    {/if}
    {#if prochains?.length}
    <!-- Le prochain conseil, tel que sa convocation l'annonce : avant la séance,
         c'est la seule chose qu'un habitant peut en savoir. -->
    <div class="prochains">
      {#each prochains as s}
        <article class="carte">
          <span class="etiquette">Prochain conseil</span>
          <p class="quand"><strong>{assemblee(s.titre)}</strong>, {dateDuJour(s.date)}{#if s.convocation?.heure}, {s.convocation.heure}{/if}{#if s.convocation?.lieu}, {s.convocation.lieu}{/if}</p>
          {#if s.convocation?.ordre_du_jour?.length}
            <details>
              <summary>L'ordre du jour : {s.convocation.ordre_du_jour.length} point{s.convocation.ordre_du_jour.length > 1 ? 's' : ''}</summary>
              <ol>{#each s.convocation.ordre_du_jour as point}<li>{point}</li>{/each}</ol>
              <p class="source">Lu dans la convocation{#if s.convocation.convoque_le}{' '}du {dateLongue(s.convocation.convoque_le)}{/if}{#if s.convocation.url}{' '}(<a href={s.convocation.url} target="_blank" rel="noopener">la lire<span class="visually-hidden">{' (nouvelle fenêtre)'}</span></a>){/if}.
                Séance publique : chacun peut y assister.</p>
            </details>
          {:else if s.convocation?.url}
            <p class="source"><a href={s.convocation.url} target="_blank" rel="noopener">La convocation<span class="visually-hidden">{' (nouvelle fenêtre)'}</span></a></p>
          {/if}
        </article>
      {/each}
    </div>
    {:else if derniere}
    <!-- Aucune séance à venir dans les sources : on le dit, sans inventer de
         date, et l'on redonne la seule date publiée, celle de la dernière.
         « Relevée » et non « publiée » : `prochains` lit les séances datées
         après l'arrêt des données, pas les convocations, et deux des
         instances n'en ont jamais relevé (vérificateur, 09/11/2026). -->
    <p class="prochain-aucun">Aucune séance à venir relevée pour l'instant. Dernière séance&nbsp;: {derniere.assemblee.toLowerCase()} du {dateLongue(derniere.date)}.</p>
    {/if}
  </section>
{/if}

{#if !dossiers?.length && sujets?.length}
  <section class="gouttiere section" aria-labelledby="t-sujets">
    <p class="kicker">Pas encore de dossier</p>
    <div class="tete-section">
      <h2 id="t-sujets" data-sans-donnees>Ce que les données disent de…</h2>
      <a class="tout" href="/dossiers">Dossiers thématiques <span aria-hidden="true">→</span></a>
    </div>
    <p class="chapo">Aucun dossier n'est encore écrit ici. Sur ces sujets, les sources publiques ont déjà des faits&nbsp;:</p>
    <ul class="grille-3">
      {#each sujets as s}<li class="carte dossier"><h3><a href={s.lien}>{s.titre}</a></h3></li>{/each}
    </ul>
  </section>
{/if}

<!-- Trois dossiers, tirés au sort à chaque publication (+page.server.js). -->
{#if dossiers?.length}
  <section class="gouttiere section" aria-labelledby="t-dossiers">
    <p class="kicker">Trois dossiers à lire</p>
    <div class="tete-section">
      <h2 id="t-dossiers" data-sans-donnees>Dossiers thématiques</h2>
      <a class="tout" href="/dossiers">Tous les dossiers <span aria-hidden="true">→</span></a>
    </div>
    <ul class="grille-3">
      {#each dossiers as d}
        <li class="carte dossier"><h3><a href="/dossiers/{d.slug}">{d.titre}</a></h3>{#if d.chapeau}<p>{d.chapeau}</p>{/if}</li>
      {/each}
    </ul>
  </section>
{/if}

<!-- Le tableau de bord : une tuile par indicateur, entière cliquable vers la
     page qui le détaille. Chaque tuile porte le chiffre de la COMMUNE ; celui
     de l'intercommunalité suit, nommé, jamais additionné. Un zéro garde sa
     raison. Les séries et leurs règles : #lib/tableau.server.js. Les quatre
     premières se voient ; les autres, et les sources, se déplient. -->
{#if tableau?.length}
  <section class="gouttiere section" aria-labelledby="t-chiffres">
    <p class="kicker">Chiffres de la commune · sources dépliables en bas</p>
    <div class="tete-section">
      <h2 id="t-chiffres">{COMMUNE} en chiffres</h2>
      <a class="tout" href="/carte">La carte <span aria-hidden="true">→</span></a>
    </div>
    <div class="tuiles">
      {#each tete as t (t.cle)}
        <a class="carte tuile" class:vide={t.vide} href={t.href}>
          <span class="quoi">{t.libelle}</span>
          <b class:longue={longue(t.valeur)}>{t.valeur}</b>
          <!-- La place du graphique est réservée même sans série : les
               valeurs restent alignées d'une tuile à l'autre. -->
          <span class="graphe">{#if t.graphe}<Mini graphe={t.graphe} />{/if}</span>
          {#if t.raison}<span class="aussi raison">{t.raison}</span>{/if}
          {#if t.note}<span class="aussi">{t.note}</span>{/if}
          {#if t.aussi}<span class="aussi">{t.aussi}</span>{/if}
        </a>
      {/each}
    </div>
    {#if replis.length}
      <details class="pli">
        <summary>Tous les indicateurs</summary>
        <div class="tuiles">
          {#each replis as t (t.cle)}
            <a class="carte tuile" class:vide={t.vide} href={t.href}>
              <span class="quoi">{t.libelle}</span>
              <b class:longue={longue(t.valeur)}>{t.valeur}</b>
              <span class="graphe">{#if t.graphe}<Mini graphe={t.graphe} />{/if}</span>
              {#if t.raison}<span class="aussi raison">{t.raison}</span>{/if}
              {#if t.note}<span class="aussi">{t.note}</span>{/if}
              {#if t.aussi}<span class="aussi">{t.aussi}</span>{/if}
            </a>
          {/each}
        </div>
      </details>
    {/if}
    <!-- Les sources des tuiles, repliées : le composant commun, mis au pli
         de la maquette par les règles `.sources` plus bas. -->
    <div class="sources"><SourcesLimites blocs={tableau.map((t) => `accueil.${t.cle}`)} /></div>
    <p class="provenance">
      Chiffres de la commune.
      <a href="/com-com">Ceux de l'intercommunalité</a> · <a href="/methode">Méthode</a>
    </p>
  </section>
{/if}

<!-- « Ce que la commune vient de décider » coiffait aussi des séances, des
     scrutins, des permis et des marchés : le genre « acte » les mêle tous
     (vérificateur, 09/11/2026). Le titre ne promet que ce que la liste est :
     des entrées des registres publics, les plus récentes. Pas de titre
     au-dessus d'une liste vide ; le lien vers le flux entier, lui, reste.
     La date d'arrêt des données passe dans le kicker. -->
{#if recents.length || interco?.recents}
  <section class="gouttiere section" aria-labelledby={recents.length ? 't-fil' : undefined}>
    {#if recents.length}
      <p class="kicker">{#if arreteLe}Données arrêtées au {dateLongue(arreteLe)}{:else}Registres publics{/if}</p>
    {/if}
    <div class="tete-section">
      {#if recents.length}<h2 id="t-fil" data-sans-donnees>Dernières entrées publiques</h2>{/if}
      <a class="tout" href="/nouveautes">Tout le flux <span aria-hidden="true">→</span></a>
    </div>
    {#if recents.length}
    <ul class="fil">
      {#each recents as item}
        <li>
          <time datetime={item.date}>{quand(item)}</time>
          <!-- `id` désigne l'événement, pas un acteur : pas de lien vers une
               fiche. La seule cible utile est la source d'origine. -->
          <span class="quoi">
            {#if item.nb_actes != null}
              <!-- Une séance renvoie vers sa page (cf. +page.server.js) ; ses
                   PIÈCES renvoient vers l'archive d'origine. -->
              <a href={item.lien || '/deliberations'}>{assemblee(item.titre)}</a>
              <span class="compte">: {compte(item.nb_actes)}</span>
              {#if item.pieces?.length}{' '}
                <span class="pieces">
                  {#each item.pieces as p, i}{#if i}<span class="sep">{' · '}</span>{/if}<a
                    href={p.url} target="_blank" rel="noopener">{PIECES[p.nature] || 'pièce'}<span class="visually-hidden">{' (nouvelle fenêtre)'}</span></a>{/each}
                </span>
              {' '}{/if}
            {:else if item.url}{' '}
              <a href={item.url} target="_blank" rel="noopener">{titre(item.titre)}<span class="visually-hidden">{' (nouvelle fenêtre)'}</span></a>
            {' '}{:else}{titre(item.titre)}{/if}
          </span>
          <span class="genre">{GENRES[item.genre] || item.genre}</span>
        </li>
      {/each}
    </ul>
    {/if}
    {#if interco?.recents}
      <!-- Le sigle de l'intercommunalité ne s'écrit pas seul : il ne dit rien
           à qui n'habite pas le canton. Son nom entier, une fois.
           La phrase dit ce que le site PUBLIE, pas ce que l'intercommunalité a
           voté ni depuis quand : la collecte n'est pas réputée complète, et
           `nb_actes` compte quelques occurrences non dédoublonnées
           (vérificateur, 09/11/2026). -->
      <p class="ailleurs">
        {#if interco.recents > 1}{interco.recents.toLocaleString('fr-FR')} délibérations de l'intercommunalité{#if EPCI}{' '}({EPCI}){/if} sont publiées ici{#if interco.depuis}, la plus ancienne du {dateLongue(interco.depuis)}{/if}.{:else}Une délibération de l'intercommunalité{#if EPCI}{' '}({EPCI}){/if} est publiée ici{#if interco.depuis}, datée du {dateLongue(interco.depuis)}{/if}.{/if}
        <a href="/deliberations">Les voir avec les autres actes</a>
      </p>
    {/if}
  </section>
{/if}

<!-- L'agenda garde sa place, mais après la décision publique et sous son propre
     nom. Mélangé au reste et trié par date, il occupait tout le fil. -->
{#if agenda?.length}
  <section class="gouttiere section agenda" aria-labelledby="t-agenda">
    <p class="kicker">Vie locale · site de la mairie</p>
    <div class="tete-section">
      <h2 id="t-agenda" data-sans-donnees>Et dans la vie de la commune</h2>
      <a class="tout" href="/vie-locale">Vie locale <span aria-hidden="true">→</span></a>
    </div>
    <ul class="fil">
      {#each agenda as item}
        <li>
          <time datetime={item.date}>{quand(item)}</time>
          <span class="quoi">
            {#if item.url}{' '}
              <a href={item.url} target="_blank" rel="noopener">{titre(item.titre)}<span class="visually-hidden">{' (nouvelle fenêtre)'}</span></a>
            {' '}{:else}{titre(item.titre)}{/if}
          </span>
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  /* Maquette v2 du 09/11/2026 (vigie-maquettes/2026-11-09-harmonisation-
     instances). Les jetons (--papier, --carte, --encre, --gris, --filet,
     --teinte, --e1 à --e8, --r-carte…) sont ceux du layout. */

  /* La gouttière du layout est locale à son composant : la même, ici. */
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

  /* ── Entrée ── */
  .entree { padding-top: var(--e6); padding-bottom: var(--e4); }
  .entree h1 { margin: 0; font-size: clamp(44px, 11.5vw, 84px); line-height: .98; letter-spacing: -.035em; }
  /* Rôle 1 de la teinte : la 2e ligne du titre. */
  .entree h1 span { display: block; color: var(--teinte); }
  .autre { margin: var(--e4) 0 0; font-size: 16px; font-weight: 600; }
  .autre a { display: inline-block; padding-block: 10px; color: var(--encre); }
  .presentation { max-width: 40rem; margin: var(--e3) 0 0; font-size: 18px; line-height: 1.5; }
  .cherche { display: flex; gap: var(--e2); max-width: 34rem; margin-top: var(--e5); }
  .champ {
    flex: 1; min-width: 0; min-height: 52px; padding: 0 14px;
    color: var(--encre); background: var(--carte); border: var(--t-contour) solid var(--encre);
    border-radius: var(--r-carte); font: inherit; font-size: 16px;
  }
  .champ::placeholder { color: var(--gris); opacity: 1; }
  .bouton {
    display: inline-flex; align-items: center; justify-content: center; min-height: 52px; padding: 0 20px;
    color: var(--papier); background: var(--encre); border: var(--t-contour) solid var(--encre);
    border-radius: var(--r-carte); font: 600 16px var(--sans); cursor: pointer;
  }
  .bouton:hover { opacity: .9; }
  @media (min-width: 1000px) {
    .entree { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 30rem); column-gap: var(--e8); align-items: end; }
    .entree .titre-bloc { grid-row: 1 / span 2; }
    .presentation { margin: 0; }
    .sous-entree { display: contents; }
    .entree nav { grid-column: 1 / -1; }
    .entree .portes { margin-top: var(--e6); }
  }

  /* ── Les trois portes, sous la recherche : titre seul ── */
  .portes { display: grid; grid-template-columns: 1fr; gap: var(--e2); margin: var(--e2) 0 0; padding: 0; list-style: none; }
  .portes a {
    display: flex; align-items: center; justify-content: space-between; gap: var(--e3);
    min-height: 48px; padding: 6px var(--e4);
    color: var(--encre); background: var(--carte); border: var(--t-contour) solid var(--encre);
    border-radius: var(--r-carte); font: 600 20px/1.15 var(--titre); letter-spacing: -.01em; text-decoration: none;
  }
  .portes a::after { content: "→" / ""; font: 400 20px var(--sans); }
  .portes a:hover { color: var(--papier); background: var(--encre); text-decoration: none; }
  @media (min-width: 700px) {
    .portes { grid-template-columns: repeat(3, 1fr); gap: var(--e3); }
    .portes a { min-height: 72px; padding: var(--e3) var(--e5); font-size: 24px; }
  }

  /* La signature du portail : filet de 5 px, point rouge au milieu. */
  .signature { position: relative; height: var(--t-signature); margin: var(--e6) 0 0; background: var(--encre); }
  .signature::before {
    content: ""; position: absolute; top: -5px; left: calc(50% - 7px); width: 14px; height: 14px;
    border-radius: 50%; background: var(--rouge); box-shadow: 0 0 0 3px var(--papier);
  }

  /* ── Sections : kicker, titre, lien « tout » ── */
  .section { padding-top: var(--e7); }
  .kicker {
    display: flex; align-items: center; gap: var(--e2); margin: 0 0 var(--e2);
    color: var(--gris); font: 500 13px/1.3 var(--mono); letter-spacing: .06em; text-transform: uppercase;
  }
  /* Rôle 3 de la teinte : le filet du kicker. */
  .kicker::before { content: ""; flex: none; width: 24px; height: var(--t-traverse); background: var(--teinte); }
  .tete-section {
    display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
    gap: var(--e2) var(--e4); margin-bottom: var(--e4);
  }
  .tete-section h2 { margin: 0; font-size: clamp(30px, 7vw, 40px); line-height: 1.08; letter-spacing: -.025em; }
  .agenda .tete-section h2 { font-size: clamp(24px, 5.5vw, 30px); }
  .tout {
    display: inline-flex; align-items: center; gap: 6px; min-height: 44px; margin-left: auto;
    color: var(--encre); font-weight: 600; font-size: 15px;
    text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px;
  }
  .tout:hover { text-decoration-thickness: 2px; }
  .chapo { max-width: 40rem; margin: calc(-1 * var(--e2)) 0 var(--e4); color: var(--gris); font-size: 16px; line-height: 1.5; }
  .chapo b { color: var(--encre); }

  /* ── LE composant carte, réglé par ses jetons ── */
  .carte {
    --c-fond: var(--carte); --c-bord: var(--filet); --c-ombre: none;
    display: flex; flex-direction: column; gap: var(--e2); min-width: 0; margin: 0;
    padding: var(--e4) var(--e5) var(--e5);
    color: var(--encre); background: var(--c-fond); border: var(--t-contour) solid var(--c-bord);
    border-radius: var(--r-carte); box-shadow: var(--c-ombre); text-decoration: none;
  }
  a.carte:hover { --c-bord: var(--encre); text-decoration: none; }
  .carte.forte { --c-bord: var(--encre); --c-ombre: var(--ombre-forte); }
  .etiquette {
    align-self: flex-start; padding: 2px 8px; color: var(--encre); border: 1px solid var(--encre);
    border-radius: var(--r-petit); font: 500 13px/1.4 var(--mono); letter-spacing: .05em; text-transform: uppercase;
  }
  .carte .date { font: 600 17px/1.3 var(--sans); }
  .titre-carte { font: 600 22px/1.2 var(--titre); letter-spacing: -.01em; }
  .tampon { margin: 0; color: var(--gris); font: 400 14px/1.45 var(--mono); }
  .tampon::before {
    content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 7px;
    border-radius: 1px; background: var(--encre); vertical-align: 1px;
  }
  .tampon.a-venir::before { background: transparent; box-shadow: inset 0 0 0 1.5px var(--gris); }

  .grille-2 { display: grid; grid-template-columns: 1fr; gap: var(--e4); }
  .grille-3 { display: grid; grid-template-columns: 1fr; gap: var(--e4); margin: 0; padding: 0; list-style: none; }
  @media (min-width: 700px) { .grille-2 { grid-template-columns: 1fr 1fr; gap: var(--e5); } }
  @media (min-width: 900px) { .grille-3 { grid-template-columns: repeat(3, 1fr); gap: var(--e5); } }

  .dossier h3 { margin: 0; }
  .dossier h3 a {
    color: var(--encre); font-size: 22px;
    text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 4px;
  }
  .dossier p { margin: 0; color: var(--gris); font-size: 15px; line-height: 1.5; }

  /* Le prochain conseil : une carte de plus, sa convocation dépliable. */
  .prochains { display: flex; flex-direction: column; gap: var(--e3); margin-top: var(--e4); }
  .quand { margin: 0; font-size: 16px; line-height: 1.5; }
  .prochains summary { cursor: pointer; font-size: 15px; }
  .prochains ol { margin: var(--e2) 0; padding-left: 1.4rem; font-size: 15px; line-height: 1.5; }
  .prochains .source { margin: var(--e1) 0 0; color: var(--gris); font-size: 14px; }
  .prochain-aucun { margin: var(--e4) 0 0; color: var(--gris); font-size: 15px; }

  /* ── Chiffres : quatre tuiles, les autres dans un pli ── */
  .tuiles { display: grid; grid-template-columns: 1fr 1fr; gap: var(--e3); }
  @media (min-width: 900px) { .tuiles { grid-template-columns: repeat(4, 1fr); gap: var(--e4); } }
  /* `container-type` : la valeur longue se règle sur la largeur de SA tuile. */
  .tuile { padding: var(--e3) var(--e4) var(--e4); gap: 6px; container-type: inline-size; }
  .tuile .quoi {
    min-height: 2.6em; color: var(--gris);
    font: 500 13px/1.3 var(--mono); letter-spacing: .05em; text-transform: uppercase;
  }
  .tuile b {
    font: 600 clamp(30px, 8vw, 40px)/1 var(--titre); letter-spacing: -.02em;
    font-variant-numeric: tabular-nums; white-space: nowrap;
  }
  /* « 2,75 à 4,63 € » ne se coupe pas : corps réduit à la largeur de la tuile. */
  .tuile b.longue { font-size: min(32px, 15.5cqi); line-height: 1.05; }
  .tuile.vide b { color: var(--gris); }
  .tuile .graphe { display: block; min-height: 34px; margin: 2px 0; }
  .tuile .aussi { color: var(--gris); font-size: 14px; line-height: 1.35; font-variant-numeric: tabular-nums; }
  .tuile .raison { color: var(--ambre); }

  .pli { margin-top: var(--e4); border-top: var(--t-contour) solid var(--encre); }
  .pli > summary {
    position: relative; display: block; min-height: 48px; padding: 12px 36px 12px 0;
    cursor: pointer; list-style: none; font-weight: 600;
  }
  .pli > summary::-webkit-details-marker { display: none; }
  .pli > summary::after {
    content: ""; position: absolute; right: 10px; top: 18px; width: 9px; height: 9px;
    border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg);
  }
  .pli[open] > summary::after { top: 22px; transform: rotate(-135deg); }
  .pli .tuiles { margin-bottom: var(--e4); }
  /* « Sources et limites » (composant commun) au pli de la maquette. */
  .sources.sources :global(.limites) { max-width: none; margin: var(--e4) 0 0; font-size: 15px; color: var(--gris); border-top: var(--t-contour) solid var(--encre); }
  .sources.sources :global(.limites > summary) {
    position: relative; display: block; width: auto; min-height: 48px; padding: 12px 36px 12px 0;
    cursor: pointer; list-style: none; color: var(--encre); font-weight: 600;
  }
  .sources.sources :global(.limites > summary::-webkit-details-marker) { display: none; }
  .sources.sources :global(.limites > summary::after) {
    content: ""; position: absolute; right: 10px; top: 18px; width: 9px; height: 9px;
    border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg);
  }
  .sources.sources :global(.limites[open] > summary::after) { top: 22px; transform: rotate(-135deg); }
  .sources.sources :global(.limites dl) { max-width: 48rem; margin: 0 0 var(--e4); }
  .sources.sources :global(.limites dt) { margin-top: var(--e3); color: var(--encre); font-weight: 600; }
  .sources.sources :global(.limites dd) { margin: 2px 0 0; line-height: 1.5; }
  .sources.sources :global(.limites a) { color: var(--encre); text-decoration: underline; text-underline-offset: 3px; }
  .provenance { margin: var(--e3) 0 0; color: var(--gris); font-size: 15px; }
  .provenance a { color: var(--encre); }

  /* ── Le fil ── */
  .fil {
    margin: 0; padding: 0; list-style: none; background: var(--carte);
    border: var(--t-contour) solid var(--filet); border-radius: var(--r-carte);
  }
  .fil li {
    display: grid; grid-template-columns: 4.4rem minmax(0, 1fr); gap: 4px var(--e3);
    padding: var(--e3) var(--e4); border-bottom: 1px solid var(--filet);
  }
  .fil li:last-child { border-bottom: 0; }
  .fil time { color: var(--gris); font: 400 14px/1.5 var(--mono); font-variant-numeric: tabular-nums; }
  .fil a { color: var(--encre); text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
  .fil a:hover { text-decoration-thickness: 2px; }
  .fil .quoi > a { font-weight: 600; }
  .fil .compte { color: var(--gris); }
  .fil .pieces { display: block; margin-top: 2px; font-size: 14px; }
  .fil .pieces a { display: inline-block; padding-block: 4px; }
  .fil .pieces .sep { color: var(--gris); }
  .fil .genre {
    grid-column: 2; justify-self: start; padding: 1px 7px; white-space: nowrap;
    color: var(--gris); border: 1px solid var(--filet); border-radius: var(--r-petit);
    font: 500 14px/1.5 var(--mono); letter-spacing: .04em; text-transform: uppercase;
  }
  @media (min-width: 800px) {
    .fil li { grid-template-columns: 5rem minmax(0, 1fr) auto; align-items: baseline; }
    .fil .genre { grid-column: 3; }
  }
  .ailleurs { margin: var(--e3) 0 0; color: var(--gris); font-size: 15px; }

  @media (prefers-reduced-motion: no-preference) {
    .portes a, a.carte, .bouton { transition: background-color .12s ease, color .12s ease, border-color .12s ease; }
  }
</style>
