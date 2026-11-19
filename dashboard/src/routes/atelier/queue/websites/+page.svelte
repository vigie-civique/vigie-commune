<script>
  import { lienSur } from '#lib/liens.js'
  import { onMount } from 'svelte'
  import { COMMUNE } from '#lib/instance.js'
  import { authFetch, currentUser } from '#lib/stores/auth.js'
  import { auMoins } from '#lib/roles.js'
  import { libelleType, erreurHttp, motifReponse, phraseErreur } from '#lib/libelles.js'

  // Qui réserve, c'est le compte connecté : l'API ne lit plus le nom envoyé.
  $: me = $currentUser?.email || ''
  $: admin = $currentUser?.role === 'admin'
  $: tranche = auMoins($currentUser, 'validator')
  let avis = ''         // ce qu'une action n'a pas pu faire — à la place d'un alert() bloquant

  let candidates   = []
  let loading      = true
  let error        = ''
  let statusFilter = 'candidate'
  let saving       = {}
  let claiming     = {}  // {id: true} pendant le claim

  onMount(() => load())

  async function load() {
    loading = true; error = ''
    try {
      const res = await authFetch(`/atelier/queue/websites?status=${statusFilter}&limit=200`)
      if (!res.ok) throw erreurHttp(res)
      candidates = await res.json()
    } catch(e) { error = phraseErreur(e) }
    finally { loading = false }
  }

  async function setStatus(id, status) {
    saving = { ...saving, [id]: true }
    avis = ''
    try {
      const res = await authFetch(`/atelier/websites/${id}`, {
        method: 'PATCH', body: JSON.stringify({ status })
      })
      if (res.ok) candidates = candidates.filter(c => c.id !== id)
      else {
        avis = await motifReponse(res, "L'enregistrement a échoué")
        if (res.status === 409) await load()
      }
    } finally {
      const s = { ...saving }; delete s[id]; saving = s
    }
  }

  async function claimItem(id) {
    claiming = { ...claiming, [id]: true }
    try {
      avis = ''
      const res = await authFetch(`/atelier/queue/${id}/claim`, {
        method: 'POST',
        body: JSON.stringify({ table: 'entity_websites' }),
      })
      if (res.ok) {
        const r = await res.json()
        candidates = candidates.map(c =>
          c.id === id ? { ...c, reservation: { par: r.locked_by, expire_dans_min: r.expires_in_min } } : c
        )
      } else {
        avis = await motifReponse(res, 'La réservation a échoué')
        if (res.status === 409) await load()
      }
    } finally {
      const cl = { ...claiming }; delete cl[id]; claiming = cl
    }
  }

  async function liberer(id) {
    avis = ''
    const res = await authFetch(`/atelier/queue/${id}/claim?table=entity_websites`, { method: 'DELETE' })
    if (res.ok) candidates = candidates.map(c => c.id === id ? { ...c, reservation: null } : c)
    else avis = await motifReponse(res, 'La libération a échoué')
  }

  function scoreColor(s) {
    if (!s) return 'var(--texte-doux)'
    if (s >= 0.7) return 'var(--succes)'
    if (s >= 0.5) return 'var(--alerte)'
    return 'var(--danger)'
  }

  $: typeGroups = candidates.reduce((acc, c) => {
    acc[c.entity_type] = (acc[c.entity_type] || [])
    acc[c.entity_type].push(c)
    return acc
  }, {})
</script>

<svelte:head><title>Adresses de sites — Atelier {COMMUNE}</title></svelte:head>

<div class="queue-page">
  <div class="queue-header">
    <h1>Adresses de sites à confirmer</h1>
    <div class="queue-controls">
      <select bind:value={statusFilter} on:change={load} aria-label="Adresses à afficher">
        <option value="candidate">À confirmer</option>
        <option value="validated">Confirmées</option>
        <option value="rejected">Écartées</option>
      </select>
      <button class="btn-reload" on:click={load}>↺ Recharger</button>
      <span class="count-info">{candidates.length} adresse{candidates.length !== 1 ? 's' : ''}</span>
    </div>
  </div>

  {#if error}<p class="err-msg">{error}</p>{/if}
  {#if avis}<p class="avis" role="status">{avis}</p>{/if}
  {#if $currentUser && !tranche}<p class="avis" role="status">Vous pouvez lire ces adresses. Seul un validateur les confirme ou les écarte.</p>{/if}
  {#if loading}<p class="muted-center">Chargement…</p>
  {:else if candidates.length === 0}
    <p class="muted-center">Aucune adresse {statusFilter === "candidate" ? "à confirmer" : statusFilter === "validated" ? "confirmée" : "écartée"}.</p>
  {:else}
    <div class="candidate-list">
      {#each candidates as c (c.id)}
        <div class="candidate-row" class:low-score={c.score < 0.5}>
          <div class="cand-entity">
            <a href="/atelier/entite/{c.entity_id}" class="entity-link">{c.entity_name}</a>
            <span class="type-badge type-{c.entity_type}">{libelleType(c.entity_type)}</span>
          </div>
          <a href={lienSur(c.url)} target="_blank" rel="noopener" class="cand-url">{c.url}</a>
          <span class="cand-score" style="color:{scoreColor(c.score)}" title="Indice de ressemblance entre le site trouvé et la fiche, de 0 à 1">
            {c.score != null ? c.score.toFixed(2).replace('.', ',') : '—'}
          </span>
          <span class="cand-source muted">{c.found_by}</span>
          {#if c.reservation}{' '}
            <span class="cand-lock" title="Réservé encore {c.reservation.expire_dans_min} min">
              🔒 {c.reservation.par === me ? 'vous' : c.reservation.par}
              {#if c.reservation.par === me || admin}{' '}
                <button class="btn-liberer" on:click={() => liberer(c.id)}>Libérer</button>
              {' '}{/if}
            </span>
          {' '}{:else if tranche}{' '}
            <button class="btn-claim" on:click={() => claimItem(c.id)}
                    disabled={claiming[c.id]}>→ Prendre en charge</button>
          {' '}{:else}{' '}
            <span></span>
          {' '}{/if}
          <div class="cand-actions">
            {#if tranche}
            {#if statusFilter !== 'validated'}{' '}
              <button class="btn-validate" on:click={() => setStatus(c.id,'validated')}
                      disabled={saving[c.id] || (c.reservation && c.reservation.par !== me)}>✓ Confirmer</button>
            {' '}{/if}
            {#if statusFilter !== 'rejected'}{' '}
              <button class="btn-reject"   on:click={() => setStatus(c.id,'rejected')}
                      disabled={saving[c.id] || (c.reservation && c.reservation.par !== me)}>✕ Écarter</button>
            {' '}{/if}
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .queue-page { padding: 1.2rem; max-width: 1100px; }
  .queue-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
  h1 { font-size: 1.1rem; font-weight: 700; color: var(--texte); margin: 0; }
  .queue-controls { display: flex; align-items: center; gap: .5rem; margin-left: auto; }
  .queue-controls select { background: var(--surface); border: 1px solid var(--bordure); color: var(--texte); border-radius: 5px; padding: .3rem .5rem; font-size: .8rem; }
  .btn-reload { background: var(--surface); border: 1px solid var(--bordure); color: var(--texte-doux); border-radius: 5px; padding: .3rem .6rem; font-size: .78rem; cursor: pointer; }
  .btn-reload:hover { border-color: var(--lien); color: var(--lien); }
  .count-info { font-size: .78rem; color: var(--texte-doux); }

  .candidate-list { display: flex; flex-direction: column; gap: .3rem; }
  .candidate-row {
    display: grid;
    grid-template-columns: 220px 1fr 48px 80px auto;
    align-items: center;
    gap: .6rem;
    padding: .45rem .75rem;
    background: var(--surface);
    border-radius: 6px;
    border: 1px solid var(--bordure);
    font-size: .78rem;
  }
  .candidate-row.low-score { opacity: .65; }
  .cand-entity { display: flex; align-items: center; gap: .35rem; min-width: 0; }
  .entity-link { color: var(--info); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .entity-link:hover { text-decoration: underline; }
  .type-badge { font-size: .62rem; padding: 1px 5px; border-radius: 3px; color: var(--sur-accent); font-weight: 600; white-space: nowrap; flex-shrink: 0; }
  .type-association { background: var(--type-association); }
  .type-business    { background: var(--type-entreprise); }
  .type-place       { background: var(--type-lieu); }
  .type-service     { background: var(--type-service); }
  .type-person      { background: var(--type-personne); }

  .cand-url { color: var(--lien); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cand-url:hover { text-decoration: underline; }
  .cand-score { font-weight: 700; text-align: center; font-size: .8rem; }
  .cand-source { white-space: nowrap; }
  .muted { color: var(--texte-doux); }
  .cand-actions { display: flex; gap: .3rem; }

  .btn-validate { background: var(--succes-bordure); border: 1px solid var(--succes-bordure); color: var(--succes-texte); border-radius: 4px; padding: .25rem .55rem; font-size: .72rem; font-weight: 600; cursor: pointer; }
  .btn-validate:hover:not(:disabled) { background: var(--succes-bordure); }
  .btn-reject   { background: var(--danger-doux); border: 1px solid var(--danger-bordure); color: var(--danger); border-radius: 4px; padding: .25rem .55rem; font-size: .72rem; font-weight: 600; cursor: pointer; }
  .btn-reject:hover:not(:disabled)   { background: var(--danger-bordure); color: var(--danger-texte); }
  button:disabled { opacity: .45; cursor: default; }

  .err-msg { color: var(--danger); font-size: .83rem; }
  .avis { margin: 0 0 .6rem; padding: .5rem .75rem; background: var(--alerte-doux); border: 1px solid var(--alerte-bordure);
          border-radius: 6px; color: var(--alerte-texte); font-size: .82rem; }
  .cand-lock { display: flex; align-items: center; gap: .35rem; color: var(--alerte); white-space: nowrap; }
  .btn-liberer { border: 1px solid var(--alerte-bordure); border-radius: 4px; padding: .1rem .4rem;
                 font-size: .68rem; color: var(--alerte-texte); cursor: pointer; }
  @media (max-width: 900px) {
    .queue-page { padding: .8rem; }
    .queue-controls { margin-left: 0; flex-wrap: wrap; }
    .candidate-row { grid-template-columns: 1fr auto; row-gap: .4rem; }
    .cand-entity, .cand-url { grid-column: 1 / -1; }
    .cand-actions { grid-column: 1 / -1; flex-wrap: wrap; }
    .cand-url { white-space: normal; overflow-wrap: anywhere; }
  }
  .muted-center { color: var(--texte-doux); text-align: center; margin-top: 2rem; }
</style>
