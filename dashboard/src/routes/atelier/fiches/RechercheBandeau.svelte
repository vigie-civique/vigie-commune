<script>
  // Champ de recherche par NOM, dans le bandeau de toutes les pages de l'atelier.
  // Taper : les premières fiches s'affichent sous le champ (une flèche, Entrée,
  // ou un clic : on est sur la fiche). Entrée sans choix : tous les résultats,
  // groupés par nature (fiches, actes et événements, marchés).
  // « / » met le curseur dans le champ, d'où que l'on soit.
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { api } from '#lib/api.js'
  import { pageStore } from '#lib/etat-app'
  import { adresseFiche } from './liens-retour.js'
  import { LIBELLE_TYPE } from '#lib/libelles.js'

  const page = pageStore()
  // D'où l'on part : la page courante telle qu'elle est À CET INSTANT (la liste
  // réécrit son adresse sans navigation, `$page` ne la suit pas), ou — depuis une
  // fiche — la page dont elle venait.
  function deCourant() {
    if (typeof location === 'undefined') return ''
    return location.pathname.startsWith('/atelier/entite/')
      ? new URLSearchParams(location.search).get('de') ?? ''
      : location.pathname + location.search
  }
  $: de = ($page.url, ouvert, fiches, deCourant())   // recalculé à chaque navigation et à chaque ouverture


  let champ
  let mobile = false
  onMount(() => {
    mobile = matchMedia('(max-width: 640px)').matches
    // Arrivé sur les résultats par l'adresse : le champ reprend le mot cherché.
    if (location.pathname === '/atelier/fiches/recherche') q = new URLSearchParams(location.search).get('q') ?? ''
  })
  let q = ''
  let fiches = []
  let ouvert = false
  let echec = false
  let actif = -1          // -1 : aucune ligne choisie ; fiches.length : « tous les résultats »
  let annonce = ''
  let minuteur
  let jeton = 0

  $: voirTout = fiches.length   // index de la dernière ligne
  $: id_actif = ouvert && actif >= 0 ? `rb-opt-${actif}` : undefined

  function surSaisie() {
    clearTimeout(minuteur)
    const t = q.trim()
    actif = -1
    if (t.length < 2) { fermer(); fiches = []; annonce = ''; return }
    minuteur = setTimeout(() => chercher(t), 250)
  }

  async function chercher(t) {
    const moi = ++jeton
    try {
      const r = await api.search(t, 7)
      if (moi !== jeton) return
      fiches = Array.isArray(r) ? r : []
      echec = false
      ouvert = true
      annonce = fiches.length
        ? `${fiches.length} fiche${fiches.length > 1 ? 's' : ''} proposée${fiches.length > 1 ? 's' : ''}. Entrée pour tous les résultats.`
        : 'Aucune fiche à ce nom. Entrée pour chercher aussi dans les actes et les marchés.'
    } catch {
      if (moi !== jeton) return
      fiches = []; echec = true; ouvert = true
      annonce = 'La recherche a échoué. Entrée pour réessayer.'
    }
  }

  function fermer() { ouvert = false; actif = -1 }

  function tousLesResultats() {
    const t = q.trim()
    if (t.length < 2) { annonce = 'Au moins deux lettres.'; return }
    fermer()
    goto(`/atelier/fiches/recherche?q=${encodeURIComponent(t)}`)
  }

  function ouvrirFiche(f) {
    fermer(); q = ''; fiches = []
    goto(adresseFiche(f.id, de))
  }

  function surEnvoi(e) {
    e.preventDefault()
    if (ouvert && actif >= 0 && actif < fiches.length) ouvrirFiche(fiches[actif])
    else tousLesResultats()
  }

  function surTouche(e) {
    if (e.key === 'Escape') { fermer(); return }
    if (!ouvert) {
      if (e.key === 'ArrowDown' && fiches.length) { ouvert = true; actif = 0; e.preventDefault() }
      return
    }
    const n = fiches.length + 1
    if (e.key === 'ArrowDown') { actif = (actif + 1) % n; e.preventDefault() }
    else if (e.key === 'ArrowUp') { actif = (actif - 1 + n) % n; e.preventDefault() }
  }

  function surTouchePage(e) {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
    const c = e.target
    if (c instanceof HTMLElement && (c.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(c.tagName))) return
    e.preventDefault()
    champ?.focus()
  }

  function surFocusHors(e) {
    // Une ligne cliquée garde le focus dans la zone : on ne ferme que si on en sort.
    if (!e.currentTarget.contains(e.relatedTarget)) fermer()
  }
</script>

<svelte:window on:keydown={surTouchePage} />

<form class="recherche" role="search" on:submit={surEnvoi} on:focusout={surFocusHors}>
  <label class="sr-only" for="rb-champ">Chercher une fiche, un acte ou un marché public : par un nom, ou par un mot de l'objet d'un marché. Raccourci : touche /</label>
  <input
    id="rb-champ" bind:this={champ} bind:value={q}
    type="search" autocomplete="off" enterkeyhint="search"
    placeholder={mobile ? 'Chercher un nom' : 'Chercher un nom ou un mot : fiche, acte, marché public'}
    title="Chercher un nom (raccourci : touche /)"
    role="combobox" aria-expanded={ouvert} aria-controls={ouvert ? 'rb-liste' : undefined}
    aria-autocomplete="list" aria-activedescendant={id_actif}
    on:input={surSaisie} on:keydown={surTouche}
  />
  <button type="submit" class="envoi">Chercher</button>

  {#if ouvert}
    <ul id="rb-liste" class="liste" role="listbox" aria-label="Fiches proposées">
      {#each fiches as f, i (f.id)}
        <li id="rb-opt-{i}" role="option" aria-selected={actif === i} class:actif={actif === i}>
          <a href={adresseFiche(f.id, de)} tabindex="-1"
             on:click|preventDefault={() => ouvrirFiche(f)}>
            <span class="nom">{f.name}</span>
            <span class="meta">{LIBELLE_TYPE[f.type] ?? f.type}{f.address ? ` · ${f.address}` : ''}</span>
          </a>
        </li>
      {/each}
      <li id="rb-opt-{voirTout}" role="option" aria-selected={actif === voirTout}
          class="tout" class:actif={actif === voirTout}>
        <a href="/atelier/fiches/recherche?q={encodeURIComponent(q.trim())}" tabindex="-1"
           on:click|preventDefault={tousLesResultats}>
          {echec ? 'La recherche a échoué. Entrée pour réessayer.' : fiches.length ? 'Tous les résultats : fiches, actes, marchés' : 'Aucune fiche à ce nom. Chercher aussi dans les actes et les marchés'}
        </a>
      </li>
    </ul>
  {/if}
  <p class="sr-only" role="status" aria-live="polite">{annonce}</p>
</form>

<style>
  .recherche { position: relative; display: flex; gap: .4rem; flex: 1; max-width: 560px; }
  input {
    flex: 1; min-width: 0; font: inherit; font-size: .85rem; color: var(--texte);
    background: var(--surface); border: 1px solid var(--bordure); border-radius: 6px;
    padding: .4rem .7rem;
  }
  input:focus-visible, .envoi:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
  .envoi {
    font: inherit; font-size: .8rem; padding: .4rem .8rem; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--bordure); background: var(--surface-2); color: var(--texte);
  }
  .liste {
    position: absolute; top: 100%; left: 0; right: 0; z-index: 50; margin-top: 3px;
    list-style: none; padding: 0; max-height: 60vh; overflow-y: auto;
    background: var(--surface); border: 1px solid var(--bordure); border-radius: 6px;
    box-shadow: 0 8px 24px var(--ombre);
  }
  li a {
    display: flex; flex-direction: column; gap: 1px; padding: .5rem .75rem;
    color: var(--texte); text-decoration: none; font-size: .84rem;
  }
  li.actif, li a:hover { background: var(--surface-2); }
  li.actif { box-shadow: inset 3px 0 0 var(--focus); }
  .nom { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { color: var(--texte-doux); font-size: .72rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tout a { color: var(--info); border-top: 1px solid var(--bordure); }
  .sr-only {
    position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }
  @media (max-width: 640px) { .envoi { display: none; } .recherche { max-width: none; } input { font-size: max(16px, .85rem); } }
</style>
