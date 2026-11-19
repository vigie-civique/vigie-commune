<script>
  import { lienSur } from '#lib/liens.js'
  import { COMMUNE, COMMUNE_A, EPCI, EPCI_NB_AUTRES } from '#lib/instance.js'
  import { onMount } from 'svelte'
  import { authFetch, currentUser } from '#lib/stores/auth.js'
  import { auMoins } from '#lib/roles.js'
  import { heureLocale } from '#lib/heure.js'
  import { VERDICTS, VERDICT, FIABILITE, FIABILITE_AIDE } from '#lib/axes.js'
  import { api } from '#lib/api.js'
  import { pageStore } from '#lib/etat-app'
  import { replaceState } from '$app/navigation'
  import { adresseFiche } from './liens-retour.js'
  import { LIBELLE_TYPE, perimetreLibelle, PERIMETRE_AIDE, PERIMETRE_NON_CLASSE, erreurHttp, phraseErreur, phraseStatut, motifReponse } from '#lib/libelles.js'

  const page = pageStore()

  let data       = null
  let stats      = {}
  let loading    = true
  let error      = ''
  let avis       = ''      // ce qu'une action n'a pas pu faire, sans masquer la liste
  $: tranche = auMoins($currentUser, 'validator')
  // Aucun filtre au départ (15/11/2026) : la liste montre toutes les fiches. Le
  // défaut d'avant (« jamais relu » + périmètre C1) cachait une fiche relue ou
  // créée par saisie, sans périmètre, à qui venait la chercher : elle semblait
  // ne pas exister. Les filtres restent là, et sont gardés dans l'adresse.
  let filter     = ''        // '' = tous les verdicts
  let typeFilter = ''
  let perimFilter = ''       // '' = tous périmètres, fiches sans périmètre comprises
  let nom        = ''
  let pages      = 1         // pages de LIMIT fiches chargées, par verdict
  let plus       = false     // « Charger plus » en cours
  let pret       = false
  const LIMIT    = 50
  const VERDICT_CLES = VERDICTS.map(v => v.cle)

  // Recherche par nom : /api/search (FTS), toute la base. Ses lignes sont plus
  // pauvres que celles de la file (ni verdict, ni périmètre, ni contacts).
  $: modeNom = nom.trim().length >= 2
  let trouvees = null        // null : pas de recherche ; sinon { items, plein }
  let minuteurNom
  let jetonNom = 0

  const TYPES    = ['person', 'business', 'association', 'place', 'service']

  const PERIMETRES = [
    { key: 'C1',   label: 'La commune',       tip: `${COMMUNE}, le cœur du projet.` },
    { key: 'C2',   label: 'Intercommunalité', tip: `${EPCI} et ses ${EPCI_NB_AUTRES} autres communes membres. Collectées pour comparer, publiées seulement en total.` },
    { key: 'C3',   label: 'Au-dessus de la commune', tip: PERIMETRE_AIDE.C3 },
    { key: 'lien', label: 'Rattachée',        tip: PERIMETRE_AIDE.lien },
    { key: '',     label: 'Tout',             tip: PERIMETRE_AIDE[''] },
  ]

  $: aidePerim = PERIMETRES.find(p => p.key === perimFilter)?.tip ?? ''

  const PERIM_COLORS = {
    C1: 'var(--perimetre-c1)', C2: 'var(--perimetre-c2)', C3: 'var(--perimetre-c3)', lien: 'var(--perimetre-lien)',
  }

  const TYPE_COLORS = {
    person: 'var(--type-personne)', business: 'var(--type-entreprise)', association: 'var(--type-association)',
    place: 'var(--type-lieu)', service: 'var(--type-service)',
  }

  onMount(() => {
    const u = $page.url.searchParams
    filter = VERDICT_CLES.includes(u.get('statut')) ? u.get('statut') : ''
    typeFilter = TYPES.includes(u.get('type')) ? u.get('type') : ''
    perimFilter = PERIMETRES.some(p => p.key && p.key === u.get('perim')) ? u.get('perim') : ''
    nom = (u.get('q') ?? '').slice(0, 100)
    pages = Math.min(Math.max(parseInt(u.get('pages')) || 1, 1), 20)
    pret = true
    loadStats(); loadQueue(); if (nom.trim().length >= 2) chercherNom()
  })

  // L'adresse de la liste dans son état : ce que les fiches ouvertes d'ici
  // reprendront pour « revenir à la liste ».
  function adresseListe(filter, typeFilter, perimFilter, nom, pages) {
    const p = new URLSearchParams()
    if (filter) p.set('statut', filter)
    if (typeFilter) p.set('type', typeFilter)
    if (perimFilter) p.set('perim', perimFilter)
    if (nom.trim()) p.set('q', nom.trim())
    if (pages > 1) p.set('pages', pages)
    const s = p.toString()
    return '/atelier/fiches' + (s ? '?' + s : '')
  }
  $: ici = adresseListe(filter, typeFilter, perimFilter, nom, pages)
  $: if (pret) { try { replaceState(ici, {}) } catch { /* l'adresse n'est qu'un confort */ } }

  async function loadStats() {
    try {
      const res = await authFetch('/atelier/stats')
      if (res.ok) stats = await res.json()
    } catch {}
  }

  const unePage = async (verdict, p) => {
    const qs = new URLSearchParams({ status: verdict, limit: LIMIT, offset: p * LIMIT })
    if (typeFilter) qs.set('type', typeFilter)
    if (perimFilter) qs.set('perimetre', perimFilter)
    const res = await authFetch(`/atelier/workqueue?${qs}`)
    if (!res.ok) throw erreurHttp(res)
    return { verdict, p, ...(await res.json()) }
  }

  // `/atelier/workqueue` exige un verdict : « tous » en demande quatre, à la
  // fois, et les met bout à bout. `ajout` : seule la page suivante est demandée,
  // et elle s'ajoute à la liste au lieu de la remplacer.
  let totaux = {}
  async function loadQueue({ ajout = false } = {}) {
    if (ajout) plus = true; else loading = true
    error = ''
    try {
      const verdicts = filter ? [filter] : VERDICT_CLES
      const debut = ajout ? pages - 1 : 0
      const demandes = verdicts.flatMap(v =>
        Array.from({ length: pages - debut }, (_, i) => unePage(v, debut + i)))
      const recues = await Promise.all(demandes)
      const neuves = recues.flatMap(r => r.items)
      const par = ajout ? totaux : {}
      for (const r of recues) par[r.verdict] = r.total
      totaux = par
      const total = Object.values(par).reduce((a, n) => a + n, 0)
      data = { total, items: ajout ? [...data.items, ...neuves] : neuves }
    } catch (e) {
      error = phraseErreur(e)
      if (ajout) pages = Math.max(1, pages - 1)
    } finally {
      loading = false; plus = false
    }
  }

  function chargerPlus() { pages += 1; loadQueue({ ajout: true }) }

  function surNom() {
    clearTimeout(minuteurNom)
    if (!modeNom) { trouvees = null; jetonNom++; return }
    minuteurNom = setTimeout(chercherNom, 300)
  }

  async function chercherNom() {
    const moi = ++jetonNom
    try {
      const items = await api.search(nom.trim(), 100)
      if (moi === jetonNom) { trouvees = { items, plein: items.length === 100 }; error = '' }
    } catch (e) {
      if (moi === jetonNom) { trouvees = null; error = 'La recherche par nom a échoué.' }
    }
  }
  $: visibles = trouvees ? trouvees.items.filter(f => !typeFilter || f.type === typeFilter) : []

  // Poser un verdict. Depuis le 21/09/2026 il est lu par la publication :
  // « Écarter » retire vraiment la fiche du site — ce que l'ancien ✗ ne faisait
  // pas. `statut_lu` : le verdict affiché dans la liste. La liste reste ouverte
  // des heures ; si quelqu'un a tranché la fiche entre-temps, l'API refuse (409)
  // au lieu d'écraser sa décision — et dit qui l'a prise.
  async function setStatus(item, verdict) {
    avis = ''
    const res = await authFetch(`/atelier/entities/${item.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ verdict, statut_lu: item.verdict || 'jamais_relu' }),
    })
    if (res.ok) {
      if (filter) {
        data.items = data.items.filter(e => e.id !== item.id)
        data.total = Math.max(0, data.total - 1)
      } else {
        // Liste « tous » : la fiche reste, son verdict change.
        data.items = data.items.map(e => e.id === item.id ? { ...e, verdict } : e)
      }
      // La ligne quitte cette file : dire où elle est allée, et comment revenir.
      avis = `« ${item.name} » → ${VERDICT[verdict].libelle}. ${VERDICT[verdict].effet}`
        + (verdict !== 'jamais_relu' ? ' Pour revenir en arrière : onglet « '
           + VERDICT[verdict].libelle + ' », « Remettre à relire ».' : '')
      await loadStats()
    } else if (res.status === 409) {
      const d = (await res.json()).detail || {}
      avis = `« ${item.name} » : ${d.message || 'le statut a changé entre-temps.'}`
        + (d.par ? ` Par ${d.par}` : '') + (d.le ? `, le ${heureLocale(d.le)}` : '')
        + '. Rien n\'a été écrasé ; la liste a été rechargée.'
      await Promise.all([loadQueue(), loadStats()])
    } else {
      // Un échec passait sans un mot : la ligne restait, et rien ne disait pourquoi.
      avis = `« ${item.name} » : l'enregistrement a échoué. ${phraseStatut(res.status)}`
    }
  }

  function changeFilter(s) { filter = s; pages = 1; loadQueue() }
  function changeType(t)   { typeFilter = t; pages = 1; loadQueue() }
  function changePerim(p)  { perimFilter = p; pages = 1; loadQueue() }

  function activite(item) {
    if (item.type === 'business')    return item.naf_label || item.siren || ''
    if (item.type === 'association') return item.asso_object || item.rna_id || ''
    if (item.type === 'service')     return item.svc_category || ''
    if (item.type === 'place')       return item.osm_value || ''
    return ''
  }
</script>

<svelte:head><title>Toutes les fiches — Atelier {COMMUNE}</title></svelte:head>

<div class="page">

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <h1>Toutes les fiches</h1>
      <a class="retour" href="/atelier">← Aujourd'hui</a>
      {#if stats.total !== undefined}{' '}
        <span class="total">
          {stats.perimetre?.C1?.toLocaleString('fr-FR') ?? '—'} {COMMUNE_A}
          <span class="total-sub">sur {stats.total?.toLocaleString('fr-FR')} fiches au total</span>
        </span>
      {' '}{/if}
      <a class="tool-link" href="/atelier/geo">Points sur la carte</a>
      <!-- Créer une fiche : la saisie d'une entité, qui demande une source et
           dit avant d'enregistrer si le nom rejoint une fiche existante. -->
      <a class="nouvelle-fiche" href="/atelier/saisie?objet=entite">+ Nouvelle fiche</a>
    </div>
    <div class="stat-chips">
      <button class="chip" class:active={filter === ''} aria-pressed={filter === ''} disabled={modeNom}
              title="Toutes les fiches, quel que soit leur verdict"
              on:click={() => changeFilter('')}>
        Tous
        <span class="chip-count">{VERDICTS.reduce((n, v) => n + (stats[v.cle] ?? 0), 0)}</span>
      </button>
      {#each VERDICTS as v}
        <button class="chip" class:active={filter === v.cle} aria-pressed={filter === v.cle} disabled={modeNom} title={v.effet}
                on:click={() => changeFilter(v.cle)}>
          {v.libelle}
          <span class="chip-count">{stats[v.cle] ?? 0}</span>
        </button>
      {/each}
    </div>
  </div>

  <p class="aide-verdicts">Jamais relu : aucun validateur ne l'a encore regardée. À revoir : on y reviendra. Retenu : un validateur l'a confirmée. Écarté : un validateur l'a refusée.</p>

  <!-- Chercher une fiche par son nom, dans toute la base -->
  <div class="nom-bar">
    <label for="fiches-nom">Filtrer la liste par nom</label>
    <input id="fiches-nom" type="search" autocomplete="off" bind:value={nom} on:input={surNom}
           placeholder="Une fiche connue : nom, enseigne, association…" />
    {#if modeNom}{' '}
      <button class="chip" on:click={() => { nom = ''; trouvees = null; jetonNom++ }}>Effacer le nom</button>
    {' '}{/if}
  </div>

  <!-- Filtre périmètre : quel territoire on traite -->
  {#if !modeNom}
  <div class="perim-bar">
    <span class="perim-label">Territoire</span>
    {#each PERIMETRES as p}
      <button class="perim-btn" class:active={perimFilter === p.key}
              style="--pc:{PERIM_COLORS[p.key] ?? 'var(--surface-2)'}"
              title={p.tip} on:click={() => changePerim(p.key)}>
        {p.label}
        {#if p.key && stats.perimetre?.[p.key] !== undefined}{' '}
          <span class="perim-count">{stats.perimetre[p.key]}</span>
        {' '}{/if}
      </button>
    {/each}
  </div>
  <p class="perim-aide">{aidePerim}</p>
  {/if}

  <!-- Filtre type -->
  <div class="type-bar">
    <button class="type-btn" class:active={typeFilter === ''} on:click={() => changeType('')}>Tous</button>
    {#each TYPES as t}
      <button class="type-btn" class:active={typeFilter === t}
              style="--tc:{TYPE_COLORS[t]}" on:click={() => changeType(t)}>{LIBELLE_TYPE[t] ?? t}</button>
    {/each}
  </div>

  {#if avis}<div class="avis" role="status">{avis}</div>{/if}
  {#if $currentUser && !tranche}<div class="msg msg-role">Vous pouvez corriger les fiches (✏️). Sur une fiche que le site publie, votre correction part en proposition : un validateur la relit avant qu'elle s'applique. Sur une fiche que le site ne publie pas, elle s'applique tout de suite, et rien n'en sort en ligne.</div>{/if}

  <!-- Table -->
  {#if modeNom}
    <div class="table-wrap">
      {#if trouvees === null && !error}
        <div class="msg">Recherche…</div>
      {:else if trouvees}
        <div class="table-meta" role="status">
          {visibles.length}{trouvees.plein ? '+' : ''} fiche{visibles.length !== 1 ? 's' : ''} pour « {nom.trim()} »,
          tous verdicts et périmètres confondus. Les filtres de verdict et de périmètre sont suspendus pendant une recherche par nom.
          {#if trouvees.plein}Seules les 100 premières sont montrées : précisez le nom.{/if}
        </div>
        {#if visibles.length === 0}
          <div class="msg">Aucune fiche à ce nom{typeFilter ? ` parmi les « ${LIBELLE_TYPE[typeFilter] ?? typeFilter} »` : ''}.</div>
        {:else}
          <table class="par-nom">
            <thead><tr><th>Type</th><th>Nom</th><th>Adresse</th><th>Fiabilité</th><th>Actions</th></tr></thead>
            <tbody>
              {#each visibles as f (f.id)}
                <tr>
                  <td><span class="type-badge" style="background:{TYPE_COLORS[f.type] ?? 'var(--surface-2)'}">{LIBELLE_TYPE[f.type] ?? f.type}</span></td>
                  <td class="name-cell"><a href={adresseFiche(f.id, ici)} class="entity-link" title={f.name}>{f.name}</a></td>
                  <td class="addr-cell">{f.address ?? '—'}</td>
                  <td class="conf-cell">
                    <span class="conf conf-{f.confidence}" title={FIABILITE_AIDE[f.confidence] ?? ''}>{FIABILITE[f.confidence] ?? f.confidence ?? '—'}</span>
                  </td>
                  <td class="actions-cell"><a href={adresseFiche(f.id, ici)} class="act act-edit" title="Éditer" aria-label="Éditer {f.name}">✏️</a></td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      {/if}
      {#if error}<div class="msg error" role="alert">{error}</div>{/if}
    </div>
  {:else if loading}
    <div class="msg">Chargement…</div>
  {:else if error}
    <div class="msg error" role="alert">{error}</div>
  {:else if data}
    <div class="table-wrap">
      <div class="table-meta">{data.total} résultat{data.total !== 1 ? 's' : ''}</div>

      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Territoire</th>
            <th>Nom</th>
            <th>Adresse</th>
            <th>Activité ou objet</th>
            <th>Responsable</th>
            <th>Contact</th>
            <th title="Ce que la machine sait de la fiche — pas un jugement humain">Fiabilité</th>
            <th>Relations</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each data.items as item (item.id)}
            <tr>
              <td data-label="Type">
                <span class="type-badge" style="background:{TYPE_COLORS[item.type] ?? 'var(--surface-2)'}">
                  {LIBELLE_TYPE[item.type] ?? item.type}
                </span>
              </td>
              <td data-label="Territoire">
                {#if item.perimetre}{' '}
                  <span class="perim-badge" style="--pc:{PERIM_COLORS[item.perimetre] ?? 'var(--surface-2)'}"
                        title={item.commune ?? 'commune inconnue'}>{perimetreLibelle(item.perimetre)}</span>
                {' '}{:else}{' '}
                  <span class="perim-badge unset" title={PERIMETRE_NON_CLASSE}>{perimetreLibelle('')}</span>
                {' '}{/if}
              </td>
              <td class="name-cell" data-label="Nom">
                <a href={adresseFiche(item.id, ici)} class="entity-link">{item.name}</a>
                {#if !filter && item.verdict}{' '}
                  <span class="verdict-hint">{VERDICT[item.verdict]?.libelle ?? item.verdict}</span>
                {' '}{/if}
                {#if item.perimetre !== 'C1' && item.commune}{' '}
                  <span class="commune-hint">{item.commune}</span>
                {' '}{/if}
              </td>
              <td class="addr-cell" data-label="Adresse">{item.address ?? '—'}</td>
              <td class="detail-cell" data-label="Activité ou objet">{activite(item) || '—'}</td>
              <td class="resp-cell" data-label="Responsable">{item.responsible ?? '—'}</td>
              <td class="contact-cell" data-label="Contact">
                {#if item.website}{' '}
                  <a href={lienSur(item.website)} target="_blank" rel="noopener" title={item.website} aria-label="Site de {item.name}">🌐</a>
                {' '}{/if}
                {#if item.contacts_count > 0 && !item.website}{' '}
                  <span title="{item.contacts_count} contact(s)">📋<span class="sr-only"> {item.contacts_count} contact(s)</span></span>
                {' '}{/if}
                {#if item.contacts_count === null}{' '}
                  <span class="empty-contact" title="Contacts réservés aux validateurs">réservé</span>
                {' '}{:else if !item.website && !item.contacts_count}{' '}
                  <span class="empty-contact">—</span>
                {' '}{/if}
              </td>
              <td class="conf-cell" data-label="Fiabilité">
                <span class="conf conf-{item.confidence}" title={FIABILITE_AIDE[item.confidence] ?? ''}>
                  {FIABILITE[item.confidence] ?? item.confidence ?? '—'}
                </span>
              </td>
              <td class="center" data-label="Relations">{item.rel_count ?? 0}</td>
              <td class="actions-cell" data-label="">
                <a href={adresseFiche(item.id, ici)} class="act act-edit" title="Éditer" aria-label="Éditer {item.name}">✏️</a>
                {#if tranche && item.ecrit_par_contributeurs > 0}{' '}
                  <a href={adresseFiche(item.id, ici)} class="ecrit-contrib">
                    {item.ecrit_par_contributeurs} modification{item.ecrit_par_contributeurs > 1 ? 's' : ''} de contributeur à relire</a>
                {' '}{/if}
                {#if tranche}
                  {#each VERDICTS.filter(v => v.cle !== (filter || item.verdict || 'jamais_relu')) as v}
                    <button class="act act-mot act-{v.cle}" title={v.effet}
                            on:click={() => setStatus(item, v.cle)}>{v.geste}</button>
                  {/each}
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>

      {#if data.items.length === 0}
        <div class="msg">Aucune fiche dans cette file.</div>
      {/if}

      {#if data.total > data.items.length}{' '}
        <button class="load-more" disabled={plus} on:click={chargerPlus}>
          {plus ? 'Chargement…' : `Charger plus (${data.total - data.items.length} restantes)`}
        </button>
      {' '}{/if}
    </div>
  {/if}
</div>

<style>
  .nouvelle-fiche {
    display: inline-flex; align-items: center; min-height: 36px; padding: .3rem .8rem;
    border-radius: 6px; background: var(--accent-fort); color: var(--sur-accent);
    font-size: .82rem; font-weight: 600; text-decoration: none; white-space: nowrap;
  }
  @media (max-width: 900px) { .nouvelle-fiche { min-height: 44px; } }
  .page {
    padding: .9rem 1.1rem;
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: .6rem;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .header-left { display: flex; align-items: baseline; gap: .65rem; }
  h1 { font-size: 1rem; font-weight: 700; color: var(--texte); }
  .total { font-size: .75rem; color: var(--texte-doux); }
.nom-bar { display: flex; align-items: center; gap: .5rem; font-size: .8rem; }
  .chip:disabled { opacity: .45; cursor: not-allowed; }
  table.par-nom .name-cell { max-width: none; white-space: normal; }
  .nom-bar label { color: var(--texte-doux); }
  .nom-bar input {
    flex: 1; max-width: 420px; font: inherit; font-size: .82rem; color: var(--texte);
    background: var(--surface); border: 1px solid var(--bordure); border-radius: 6px; padding: .35rem .6rem;
  }
  @media (max-width: 640px) { .nom-bar input { font-size: max(16px, .82rem); } }
  .nom-bar input:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
  .verdict-hint { font-size: .68rem; color: var(--texte-doux); border: 1px solid var(--bordure); border-radius: 999px; padding: 0 6px; margin-left: .3rem; white-space: nowrap; }
  .retour { font-size: .78rem; color: var(--info); text-decoration: none; }

  .stat-chips { display: flex; gap: .3rem; flex-wrap: wrap; }
  .chip {
    display: flex; align-items: center; gap: .3rem;
    padding: .25rem .6rem; border-radius: 999px;
    border: 1px solid var(--bordure); background: var(--surface);
    color: var(--texte-doux); font-size: .73rem; cursor: pointer; transition: all .12s;
  }
  .chip.active { background: var(--accent); border-color: var(--accent); color: var(--sur-accent); }
  .chip:hover:not(.active) { border-color: var(--bordure-forte); color: var(--texte); }
  .chip-count {
    background: color-mix(in srgb, var(--sur-accent) 15%, transparent); border-radius: 999px;
    padding: 0 5px; font-size: .68rem; font-weight: 700;
  }

  .type-bar { display: flex; gap: .25rem; flex-wrap: wrap; }
  .type-btn {
    padding: .2rem .5rem; border-radius: 4px;
    border: 1px solid var(--bordure); background: transparent;
    color: var(--texte-doux); font-size: .72rem; cursor: pointer; transition: all .12s;
  }
  .type-btn.active { background: var(--tc, var(--accent)); border-color: var(--tc, var(--accent)); color: var(--sur-accent); }
  .type-btn:hover:not(.active) { border-color: var(--bordure-forte); color: var(--texte); }

  .table-wrap { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: .4rem; }
  .table-meta { font-size: .73rem; color: var(--texte-doux); padding: .15rem 0; }

  table { width: 100%; border-collapse: collapse; font-size: .78rem; }

  th {
    text-align: left; padding: .38rem .5rem;
    color: var(--texte-doux); font-size: .69rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    border-bottom: 1px solid var(--bordure); white-space: nowrap;
    cursor: default;
  }

  td { padding: .4rem .5rem; border-bottom: 1px solid var(--bordure-douce); vertical-align: middle; color: var(--texte-2); }
  tr:hover td { background: var(--surface); }

  .perim-bar {
    display: flex; align-items: center; gap: .4rem;
    margin-bottom: .6rem; flex-wrap: wrap;
  }
  .perim-label {
    font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
    opacity: .6; margin-right: .3rem;
  }
  .perim-btn {
    border: 1px solid var(--pc); background: transparent; color: var(--pc);
    border-radius: 999px; padding: .18rem .6rem; font-size: .78rem;
    cursor: pointer; display: inline-flex; align-items: center; gap: .35rem;
  }
  .perim-btn.active { background: var(--pc); color: var(--sur-accent); }
  .perim-count { font-variant-numeric: tabular-nums; opacity: .8; font-size: .72rem; }
  .perim-badge {
    display: inline-block; text-align: center; white-space: nowrap;
    background: var(--pc); color: var(--sur-accent); border-radius: 4px;
    padding: .1rem .3rem; font-size: .68rem; font-weight: 600;
  }
  .perim-badge.unset { background: var(--danger-bordure); }
  .commune-hint {
    display: block; font-size: .7rem; opacity: .65; margin-top: .1rem;
  }
  .total-sub { opacity: .6; font-weight: 400; }
  .aide-verdicts { font-size: .76rem; color: var(--texte-doux); margin: 0; }
  .perim-aide { font-size: .78rem; color: var(--texte-doux); margin: -.3rem 0 .4rem; }
  .msg-role { padding: .4rem 0; text-align: left; font-size: .8rem; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

  .type-badge {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: .66rem; font-weight: 600; color: var(--sur-accent); white-space: nowrap;
  }

  .name-cell { max-width: 180px; }
  .entity-link {
    color: var(--info); font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;
  }
  table.par-nom .entity-link { white-space: normal; overflow: visible; }
  .entity-link:hover { color: var(--info); text-decoration: underline; }

  .addr-cell   { max-width: 160px; color: var(--texte-doux); font-size: .74rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .detail-cell { max-width: 180px; color: var(--texte-doux); font-size: .74rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .resp-cell   { max-width: 120px; color: var(--texte-2); font-size: .76rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .contact-cell { text-align: center; font-size: .88rem; }
  .empty-contact { color: var(--texte-doux); }

  .conf { font-size: .7rem; border-radius: 3px; padding: 1px 5px; }
  .conf-verified   { color: var(--succes); background: var(--succes-doux); }
  .conf-probable   { color: var(--alerte); background: var(--alerte-doux); }
  .conf-hypothesis { color: var(--texte-doux); background: var(--surface); }

  .center { text-align: center; color: var(--texte-doux); }

  .actions-cell { display: flex; gap: .2rem; align-items: center; white-space: nowrap; }

  .ecrit-contrib { color: var(--info); font-size: .8rem; white-space: normal; }
  .act {
    width: 26px; height: 26px; border-radius: 4px;
    border: 1px solid var(--bordure); font-size: .78rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center; transition: all .12s;
    text-decoration: none;
  }
  .act-edit   { background: var(--surface); color: var(--texte); font-size: .82rem; }
  .act-edit:hover { background: var(--surface-2); border-color: var(--bordure-forte); }
  /* Des mots, plus des ✓ ✗ nus : on sait ce que fait le bouton avant d'appuyer. */
  .act-mot { width: auto; padding: 0 .45rem; font-size: .72rem; }
  .act-jamais_relu { background: var(--surface); color: var(--texte-2); }
  .act-jamais_relu:hover { background: var(--surface-2); border-color: var(--bordure-forte); }
  .act-a_revoir { background: var(--info-doux); color: var(--info); }
  .act-a_revoir:hover { background: var(--accent-fort); border-color: var(--accent-fort); color: var(--sur-accent); }
  .act-retenu  { background: var(--succes-doux); color: var(--succes); }
  .act-retenu:hover { background: var(--succes-bordure); border-color: var(--succes-bordure); }
  .act-ecarte  { background: var(--danger-doux); color: var(--danger); }
  .act-ecarte:hover { background: var(--danger-bordure); border-color: var(--danger-bordure); color: var(--danger-texte); }

  .msg { padding: 2rem; text-align: center; color: var(--texte-doux); font-size: .85rem; }
  .msg.error { color: var(--danger); }
  .avis {
    margin: .25rem 0 .5rem; padding: .5rem .75rem;
    background: var(--alerte-doux); border: 1px solid var(--alerte-bordure); border-radius: 6px;
    color: var(--alerte-texte); font-size: .82rem;
  }

  .load-more {
    align-self: center; margin: .4rem 0;
    padding: .4rem 1.1rem; border: 1px solid var(--bordure);
    border-radius: 6px; background: var(--surface); color: var(--texte-doux);
    font-size: .78rem; cursor: pointer;
  }
  .load-more:hover { border-color: var(--bordure-forte); color: var(--texte); }

  /* Téléphone : chaque fiche devient une carte, un champ par ligne, au lieu
     d'un tableau de dix colonnes qu'il faudrait faire défiler de côté. */
  @media (max-width: 900px) {
    .page { padding: .7rem .7rem 1.5rem; height: auto; }
    .header, .header-left { flex-wrap: wrap; }
    .nom-bar { flex-wrap: wrap; }
    .nom-bar input { max-width: none; flex: 1 1 100%; }
    .stat-chips, .perim-bar, .type-bar { gap: .4rem; }
    .chip, .perim-btn, .type-btn { min-height: 44px; }
    table, tbody, tr, td { display: block; width: 100%; }
    thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    tr { border: 1px solid var(--bordure); border-radius: 8px; padding: .4rem .6rem; margin-bottom: .6rem; background: var(--surface); }
    td { display: flex; gap: .6rem; align-items: baseline; padding: .25rem 0; border: none; max-width: none;
         white-space: normal; overflow: visible; }
    td[data-label]::before { content: attr(data-label); flex: 0 0 7.5rem; font-size: .7rem; text-transform: uppercase;
         letter-spacing: .04em; color: var(--texte-doux); }
    td[data-label=""]::before { display: none; }
    td.name-cell { order: -1; display: block; font-size: .95rem; }
    td.center { text-align: left; }
    .name-cell::before { display: none !important; }
    .entity-link { white-space: normal; }
    .actions-cell { flex-wrap: wrap; gap: .4rem; padding-top: .5rem; }
    .act { width: 44px; height: 44px; }
    .act-mot { width: auto; padding: 0 .8rem; }
    .contact-cell { text-align: left; }
    tr:hover td { background: transparent; }
  }
</style>
