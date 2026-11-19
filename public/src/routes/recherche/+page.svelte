<script>
  import { COMMUNE_DE, SITE_NOM } from '#lib/instance.js'
  import { onMount } from 'svelte'
  import { pageStore } from '#lib/etat-app'
  const page = pageStore()
  import { TYPE_LABELS, loadJSON, euros } from '#lib/data.js'
  import { chercher, motsDeRequete } from '#lib/recherche.js'

  // La recherche ne portait que sur les acteurs. Or on ne cherche pas seulement
  // « qui » : on cherche « école », « assainissement », « voirie », un montant,
  // une année. Ne trouver que des noms d'entreprises donnait l'impression que le
  // site ignorait un sujet dont il détient pourtant les actes.
  //
  // Deux index, pour ne pas alourdir la page : celui des acteurs est embarqué au
  // build (~140 Ko) et répond dès la première frappe ; l'index transversal
  // (6 474 entrées, ~940 Ko) est chargé en arrière-plan et prend le relais. Tout
  // embarquer aurait fait une page à 1 Mo — la régression même qu'on a corrigée
  // ailleurs.
  export let data
  $: acteurs = data.index

  let complet = null          // index transversal, une fois chargé
  // Faux au rendu : la page est prérendue, et un « Chargement… » écrit dans le
  // HTML y restait pour toujours quand le script ne partait pas (audit UX du
  // 25/10/2026). Il ne s'affiche que si le chargement a réellement commencé.
  let chargement = false

  // Le texte des actes : chargé à la première recherche et non à l'ouverture
  // (environ 1 Mo compressé pour dix ans d'actes). Absent d'un site qui n'a pas
  // de lexique : la recherche reste alors sur les titres, sans rien dire.
  let texte = null
  let texteEtat = 'attente'   // attente | charge | pret | absent
  async function chargerTexte() {
    texteEtat = 'charge'
    try {
      texte = (await loadJSON('recherche_texte.json')).mots || null
      texteEtat = texte ? 'pret' : 'absent'
    } catch { texte = null; texteEtat = 'absent' }
  }

  let q = ''
  let categorie = ''
  let commune = ''

  // Requête pré-remplie depuis l'URL : ?q= rend les résultats partageables.
  // Lu après le montage et non au rendu : la page est prérendue, il n'y a pas
  // de query string au build (SvelteKit interdit d'y toucher à ce moment-là).
  // Relu à chaque navigation : la loupe du menu, utilisée depuis cette page
  // même, change l'adresse sans remonter le composant, et l'ancienne requête
  // restait affichée.
  let monte = false
  const lireRequete = (url) => { q = url.searchParams.get('q') || '' }
  $: if (monte) lireRequete($page.url)

  onMount(async () => {
    monte = true
    chargement = true
    try {
      complet = (await loadJSON('recherche_index.json')).index || []
    } catch { complet = null }   // on reste sur les acteurs seuls
    finally { chargement = false }
  })

  const LIMITE = 60   // au-delà, affiner la requête vaut mieux que dérouler

  const CATEGORIES = {
    dossier: 'Dossier thématique',
    seance: 'Conseil en clair',
    acteur: 'Acteur',
    acte: 'Acte',
    marche: 'Marché',
    versement: 'Versement',
  }

  // Tant que l'index transversal n'est pas là, on cherche dans les acteurs, mis
  // au même format : le lecteur n'a pas à savoir qu'il y a deux fichiers.
  $: source = complet ?? acteurs.map((e) => ({
    k: 'acteur', t: e.n, s: e.s, u: `/entite/${e.id}`, c: e.c, n: 1000 + (e.nb || 0),
  }))

  // Le champ `c` ne veut pas dire la même chose selon la catégorie : commune
  // pour un acteur, source pour un acte, acheteur pour un marché, payeur pour
  // un versement. Alimenter un filtre « communes » avec tout cela y mettait
  // `bodacc`, le domaine du site communal et « État - Fonds Vert » côtoient
  // Colognac. Le filtre n'est donc proposé que sur les acteurs, où il a un sens.
  $: communes = [...new Set(
    source.filter((e) => e.k === 'acteur').map((e) => e.c).filter(Boolean)
  )].sort()
  $: filtreCommuneUtile = categorie === 'acteur'
  $: if (!filtreCommuneUtile && commune) commune = ''
  // Des mots, pas une suite de lettres, et un score calculé à la requête :
  // `#lib/recherche.js`. Sans mot cherchable (moins de deux lettres), la liste
  // n'apparaît que si une catégorie ou une commune est choisie.
  $: assezDeLettres = q.trim().length >= 2 && motsDeRequete(q).length > 0
  $: if (assezDeLettres && complet && texteEtat === 'attente') chargerTexte()
  $: trouve = !assezDeLettres && !categorie && !commune
    ? { resultats: [], partiel: false, dansLeTexte: new Set() }
    : chercher(source, assezDeLettres ? q : '',
        (e) => (!categorie || e.k === categorie) && (!commune || e.c === commune),
        texte)
  $: resultats = trouve.resultats
  $: affiches = resultats.slice(0, LIMITE)
  $: parCategorie = resultats.reduce((acc, r) => {
    acc[r.k] = (acc[r.k] || 0) + 1
    return acc
  }, {})
</script>

<svelte:head>
  <title>Rechercher - {SITE_NOM}</title>
  <meta name="description" content="Chercher dans tout le site : acteurs, délibérations, marchés publics et versements de la commune {COMMUNE_DE} et de son intercommunalité." />
</svelte:head>

<section class="rech">
  <h1>Rechercher</h1>
  <p class="sub">
    Dossiers thématiques, conseils en clair, acteurs, délibérations, marchés
    publics et versements : tout en même temps.
    Cherchez un nom, mais aussi un sujet («&nbsp;voirie&nbsp;», «&nbsp;école&nbsp;»),
    un montant ou une année.
  </p>

  <noscript>
    <p class="etat">
      La recherche demande JavaScript. À défaut, parcourez
      <a href="/conseils">les conseils en clair</a>,
      <a href="/dossiers">les dossiers thématiques</a>
      ou <a href="/deliberations">les délibérations</a>.
    </p>
  </noscript>

  <div class="filtres">
    <input
      type="search"
      bind:value={q}
      placeholder="Un sujet, un nom, un montant…"
      aria-label="Rechercher un sujet, un nom, un montant ou une année"
      autocomplete="off" />
    <select bind:value={categorie} aria-label="Filtrer par catégorie">
      <option value="">Tout</option>
      <option value="dossier">Dossiers thématiques</option>
      <option value="seance">Conseils en clair</option>
      <option value="acteur">Acteurs</option>
      <option value="acte">Actes et délibérations</option>
      <option value="marche">Marchés publics</option>
      <option value="versement">Versements</option>
    </select>
    {#if filtreCommuneUtile}{' '}
      <select bind:value={commune} aria-label="Filtrer par commune">
        <option value="">Toutes les communes</option>
        {#each communes as c}<option value={c}>{c}</option>{/each}
      </select>
    {' '}{/if}
  </div>

  {#if !resultats.length}
    <p class="etat">
      {assezDeLettres || categorie || commune
        ? 'Rien ne correspond.'
        : `Tapez au moins deux lettres, ou choisissez une catégorie. ${source.length.toLocaleString('fr-FR')} entrées consultables.`}
    </p>
  {:else}
    {#if trouve.partiel}
      <p class="etat">Aucune entrée ne contient tous ces mots : voici celles qui en contiennent le plus.</p>
    {/if}
    <p class="compte">
      {resultats.length.toLocaleString('fr-FR')} résultat{resultats.length > 1 ? 's' : ''}
      {#if Object.keys(parCategorie).length > 1}{' '}
        <em>: {Object.entries(parCategorie)
              .map(([k, n]) => `${n} ${(CATEGORIES[k] || k).toLowerCase()}${n > 1 ? 's' : ''}`)
              .join(' · ')}</em>
      {' '}{/if}
      {#if resultats.length > LIMITE}<em>, {LIMITE} premiers affichés : affinez la recherche</em>{/if}
    </p>
    <ul class="liste">
      {#each affiches as e, i (e.u + i)}
        <!-- Un événement que le site n'affiche pas acte par acte (BODACC,
             permis, agenda) mène à sa source d'origine, dans un nouvel onglet ;
             sans source, il s'affiche sans lien plutôt qu'avec un lien faux. -->
        <li>
          <svelte:element this={e.u ? 'a' : 'div'} class="ligne" href={e.u}
            target={e.u?.startsWith('http') ? '_blank' : undefined}
            rel={e.u?.startsWith('http') ? 'noopener' : undefined}>
            <span class="badge {e.k}">{CATEGORIES[e.k] || e.k}</span>
            <span class="nom">{e.t}</span>
            {#if trouve.dansLeTexte.has(e)}<span class="externe">dans le texte</span>{/if}
            {#if e.u?.startsWith('http')}<span class="externe">source ↗</span>{/if}
            {#if e.d}<span class="commune">{e.d}</span>{/if}
            {#if e.c}<span class="commune">{e.c}</span>{/if}
            {#if e.m != null}<span class="actes">{euros(e.m)}</span>{/if}
          </svelte:element>
        </li>
      {/each}
    </ul>
  {/if}

  {#if chargement}
    <p class="etat chargeant">Chargement de l'index complet : la recherche porte pour l'instant sur les acteurs seuls.</p>
  {/if}
</section>

<style>
  .rech { max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem 3rem; }
  h1 { font-size: 1.7rem; margin: 0 0 .4rem; color: var(--encre); }
  .sub { color: var(--gris); max-width: 62ch; line-height: 1.5; margin: 0 0 1.5rem; }

  .filtres { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
  input[type="search"] {
    flex: 1 1 22rem; padding: .65rem .9rem; font-size: 1rem;
    border: 1px solid var(--trait); border-radius: 10px; background: var(--carte); color: var(--encre);
  }
  input[type="search"]:focus, select:focus { outline: 2px solid var(--ardoise); outline-offset: 1px; border-color: var(--ardoise); }
  select {
    padding: .65rem .7rem; font-size: .9rem; border: 1px solid var(--trait);
    border-radius: 10px; background: var(--carte); color: var(--gris);
  }

  .etat { color: var(--gris); }
  .etat.err { color: var(--depense); }
  .compte { font-size: .85rem; color: var(--gris); margin: 0 0 .6rem; }
  .compte em { font-style: normal; color: var(--gris-clair); }

  .liste { list-style: none; margin: 0; padding: 0; display: grid; gap: .4rem; }
  .liste .ligne {
    display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
    padding: .6rem .8rem; background: var(--carte); border: 1px solid var(--trait);
    border-radius: 10px; color: inherit; text-decoration: none;
  }
  a.ligne:hover { border-color: var(--ardoise); box-shadow: 0 3px 10px rgba(37,99,235,.1); text-decoration: none; }
  .nom { font-weight: 600; color: var(--encre); }
  .alias { font-size: .8rem; color: var(--gris-clair); }
  .commune { font-size: .78rem; color: var(--gris); margin-left: auto; }
  .actes { font-size: .72rem; color: var(--ardoise); background: var(--ardoise-pale); padding: .1rem .45rem; border-radius: 999px; }

  .badge {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .03em;
    padding: .15rem .45rem; border-radius: 5px; background: var(--trait-pale); color: var(--gris); white-space: nowrap;
  }
  .badge.business { background: var(--ambre-pale); color: var(--ambre); }
  .badge.association { background: var(--vert-pale); color: var(--vert-fonce); }
  .badge.service { background: var(--ardoise-pale); color: var(--ardoise-fonce); }
  .badge.place { background: var(--ardoise-pale); color: #5b21b6; }
  .badge.person { background: var(--depense-pale); color: var(--depense); }
  .badge.dossier, .badge.seance { background: var(--ardoise); color: var(--blanc); }
  .externe { font-size: .72rem; color: var(--gris); }

  @media (max-width: 620px) {
    .commune { margin-left: 0; }
  }
</style>
