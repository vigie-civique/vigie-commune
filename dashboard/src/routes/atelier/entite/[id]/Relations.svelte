<script>
  import { authFetch, currentUser } from '#lib/stores/auth.js'
  import { api } from '#lib/api.js'
  import { heureLocale } from '#lib/heure.js'
  import { phraseErreur } from '#lib/libelles.js'
  import { auMoins } from '#lib/roles.js'
  import Fiabilite from '#lib/components/Fiabilite.svelte'
  import { libelleRelation, libelleSource, libelleType } from '#lib/libelles.js'
  import { cleLocale } from './brouillon.js'
  import { adresseFiche } from '../../fiches/liens-retour.js'
  const LIBELLE_TYPE = new Proxy({}, { get: (_, t) => libelleType(t) })

  export let relations = []
  export let entityId
  export let de = ''              // d'où l'on est venu : repris par chaque lien vers une fiche liée
  export let brouillon            // { ajouts, modifs: {id: champs}, suppr, motifs: {id: motif} }
  // Supprimer un lien, lever ou garder un refus : réservés au validateur (l'API
  // refuse le reste). Un contributeur signale la fiche ou propose une modification.
  export let tranche = false
  export let refusVersion = 0     // incrémenté par la page après une suppression

  const REL_TYPES = [
    'dirigeant','gérant','associé','président','trésorier','secrétaire','membre',
    'élu_cm','élu_cc','candidat','agent_communal','membre_commission',
    'locataire_commune','bailleur_commune','subventionné','prestataire',
    'famille_présumé','époux_présumé','enfant_présumé','proche_présumé',
    'même_adresse','même_lieu_dit',
  ]
  const CHAMPS = ['relation_type', 'since', 'until', 'source', 'confidence']

  // L'autre bout de la relation : un lien vers sa fiche.
  function autre(r) {
    return r.from_id === entityId
      ? { sens: '→', id: r.to_id, nom: r.to_name, type: r.to_type }
      : { sens: '←', id: r.from_id, nom: r.from_name, type: r.from_type }
  }

  // ── Modifier une relation existante ──────────────────────────────────────
  let editingRelId = null
  let editRelForm  = {}

  const valeurs = r => ({ relation_type: r.relation_type, since: r.since ?? '', until: r.until ?? '',
                          source: r.source ?? 'manual', confidence: r.confidence ?? 'verified' })

  function startEditRel(r) {
    editingRelId = r.id
    editRelForm  = { ...(brouillon.modifs[r.id] ?? valeurs(r)) }
  }

  function appliquer(r) {
    const avant = valeurs(r)
    const { [r.id]: _, ...autres } = brouillon.modifs
    const change = CHAMPS.some(k => String(editRelForm[k] ?? '') !== String(avant[k] ?? ''))
    brouillon.modifs = change ? { ...autres, [r.id]: { ...editRelForm } } : autres
    editingRelId = null
  }

  function annulerModif(id) {
    const { [id]: _, ...autres } = brouillon.modifs
    brouillon.modifs = autres
  }

  // Supprimer demande un motif : pour un lien venu d'un collecteur, la
  // suppression le refuse aux collectes suivantes, et le validateur qui
  // relira ce refus doit savoir pourquoi. Demandé pour tout lien, par
  // simplicité : un lien posé à la main le garde au journal.
  let motifPour = null     // id du lien dont on écrit le motif
  let motifSaisi = ''
  function demanderSuppr(id) { motifPour = id; motifSaisi = ''; editingRelId = null }
  function confirmerSuppr() {
    if (!motifSaisi.trim()) return
    brouillon.suppr = [...brouillon.suppr, motifPour]
    brouillon.motifs = { ...(brouillon.motifs ?? {}), [motifPour]: motifSaisi.trim() }
    motifPour = null
  }
  function retablir(id) {
    brouillon.suppr = brouillon.suppr.filter(x => x !== id)
    const { [id]: _, ...autres } = brouillon.motifs ?? {}
    brouillon.motifs = autres
  }

  // ── Liens refusés de cette fiche (validateur) ────────────────────────────
  let refus = []
  let refusErreur = ''
  let refusMotif = {}      // id → motif de la levée ou de la revue
  let refusEnCours = null
  let refusAvis = ''
  async function chargerRefus() {
    if (!tranche) { refus = []; return }
    try { refus = (await api.refusDeLaFiche(entityId)).refus ?? []; refusErreur = '' }
    catch (e) { refusErreur = phraseErreur(e) }
  }
  $: entityId, refusVersion, tranche, chargerRefus()

  async function garder(x) {
    refusEnCours = x.id; refusAvis = ''
    try {
      await api.refusGarder(x.id, (refusMotif[x.id] ?? '').trim())
      refusAvis = 'Refus gardé : les sources qui le redisent aujourd\'hui sont tenues pour vues.'
      await chargerRefus()
    } catch (e) { refusAvis = phraseErreur(e) } finally { refusEnCours = null }
  }
  async function lever(x) {
    const motif = (refusMotif[x.id] ?? '').trim()
    if (!motif) { refusAvis = 'Lever un refus demande de dire pourquoi.'; return }
    refusEnCours = x.id; refusAvis = ''
    try {
      await api.refusLever(x.id, motif)
      refusAvis = 'Refus levé : la prochaine collecte pourra réécrire ce lien. Rien n\'est recréé maintenant.'
      await chargerRefus()
    } catch (e) { refusAvis = phraseErreur(e) } finally { refusEnCours = null }
  }
  const autreBout = x => x.from_id === entityId ? `→ ${x.to_name}` : `← ${x.from_name}`

  // ── Ajouter une relation ─────────────────────────────────────────────────
  const relVide = () => ({ direction:'from', relation_type:'dirigeant', since:'', until:'', source:'manual',
                           confidence: auMoins($currentUser, 'validator') ? 'verified' : 'probable' })
  let newRel = relVide()
  let relSearch = ''
  let relSearchResults = []
  let relTarget = null
  let relSearchOpen = false
  let _searchTimer = null

  function onRelSearch() {
    clearTimeout(_searchTimer); relTarget = null
    if (relSearch.length < 2) { relSearchResults = []; relSearchOpen = false; return }
    _searchTimer = setTimeout(async () => {
      const r = await authFetch(`/search?q=${encodeURIComponent(relSearch)}&limit=8`)
      if (r.ok) { relSearchResults = await r.json(); relSearchOpen = relSearchResults.length > 0 }
    }, 280)
  }

  function selectTarget(e) { relTarget = e; relSearch = e.name; relSearchOpen = false }

  function ajouter() {
    if (!relTarget) return
    brouillon.ajouts = [...brouillon.ajouts,
      { cle: cleLocale(), cible: { id: relTarget.id, name: relTarget.name, type: relTarget.type },
        corps: { ...newRel } }]
    newRel = relVide()
    relTarget = null; relSearch = ''
  }

  const retirer = cle => brouillon.ajouts = brouillon.ajouts.filter(a => a.cle !== cle)
</script>

<section class="card">
  <h2>Relations <span class="count-badge">{relations.length}</span></h2>

  {#if !tranche && relations.length}
    <p class="muted aide-droits">Supprimer un lien demande le rôle de validateur. Un lien faux :
      « Modifier » propose la correction, « Signaler » (en haut) demande de revoir la fiche.</p>
  {/if}
  {#if relations.length === 0 && brouillon.ajouts.length === 0}
    <p class="muted">Aucune relation connue.</p>
  {:else}
    <ul class="relation-list">
      {#each relations as r (r.id)}
        {@const m = brouillon.modifs[r.id]}
        {@const v = m ?? r}
        <li class="rel-item" class:editing={editingRelId === r.id}
            class:a-supprimer={brouillon.suppr.includes(r.id)} class:a-modifier={m}>
          {#if editingRelId === r.id}
            <div class="rel-edit-form">
              <div class="rel-edit-row">
                <label>Nature du lien
                  <select bind:value={editRelForm.relation_type}>
                    {#each REL_TYPES as t}<option value={t}>{libelleRelation(t)}</option>{/each}
                  </select>
                </label>
                <label>Depuis<input bind:value={editRelForm.since} placeholder="AAAA-MM-JJ" /></label>
                <label>Jusqu'au<input bind:value={editRelForm.until} placeholder="AAAA-MM-JJ" /></label>
                <label>Source<input bind:value={editRelForm.source} /></label>
                <label>Fiabilité
                  <select bind:value={editRelForm.confidence}>
                    <option value="verified">Sûre</option>
                    <option value="probable">Probable</option>
                    <option value="hypothesis">Hypothèse</option>
                  </select>
                </label>
              </div>
              <div class="rel-edit-actions">
                <button class="btn-rel-save" on:click={() => appliquer(r)}>✓ Appliquer</button>
                <button class="btn-rel-cancel" on:click={() => editingRelId = null}>Fermer</button>
              </div>
            </div>
          {:else}{' '}
            <span class="rel-type">{libelleRelation(v.relation_type)}</span>
            <span class="rel-dir">{autre(r).sens} <a href={adresseFiche(autre(r).id, de)}>{autre(r).nom}</a> ({LIBELLE_TYPE[autre(r).type] ?? autre(r).type})</span>
            {#if v.since || v.until}{' '}
              <span class="rel-dates">{v.since || '?'}{v.until ? ' → '+v.until : ''}</span>
            {' '}{/if}
            <span class="rel-src">{libelleSource(v.source)}</span>
            <Fiabilite code={v.confidence} />
            {#if brouillon.suppr.includes(r.id)}{' '}
              <span class="tag-attente">sera effacé quand vous enregistrerez la fiche — « {brouillon.motifs?.[r.id]} »</span>
              <button class="lien-annuler" on:click={() => retablir(r.id)}>rétablir</button>
            {' '}{:else if m}{' '}
              <span class="tag-attente">modifiée, à enregistrer</span>
              <button class="lien-annuler" on:click={() => annulerModif(r.id)}>annuler</button>
            {' '}{/if}
            {#if !brouillon.suppr.includes(r.id) && motifPour !== r.id}
              <div class="rel-row-actions">
                <button class="btn-texte" on:click={() => startEditRel(r)}
                        aria-label="Modifier ce lien">Modifier</button>
                {#if tranche}{' '}
                  <button class="btn-texte danger" on:click={() => demanderSuppr(r.id)}
                          aria-label="Supprimer ce lien : l'effacer de la base">Supprimer</button>
                {' '}{/if}
              </div>
            {/if}
            {#if motifPour === r.id}
              <div class="motif-suppr">
                <label>Supprimer efface ce lien de la base. S'il vient d'un registre ou d'un
                  document, les collectes suivantes ne le réécriront plus. Pourquoi ?
                  <input bind:value={motifSaisi} maxlength="500"
                         placeholder="Ex. : homonyme" />
                </label>
                {#if !motifSaisi.trim()}<p class="muted">Motif obligatoire pour supprimer.</p>{/if}
                <div class="rel-edit-actions">
                  <button class="btn-suppr-confirmer" disabled={!motifSaisi.trim()}
                          on:click={confirmerSuppr}>Supprimer ce lien</button>
                  <button class="btn-rel-cancel" on:click={() => motifPour = null}>Annuler</button>
                </div>
              </div>
            {/if}
          {/if}
        </li>
      {/each}
      {#each brouillon.ajouts as a (a.cle)}
        <li class="rel-item a-enregistrer">
          <span class="rel-type">{libelleRelation(a.corps.relation_type)}</span>
          <span class="rel-dir">{a.corps.direction === 'from' ? '→' : '←'} <a href={adresseFiche(a.cible.id, de)}>{a.cible.name}</a> ({LIBELLE_TYPE[a.cible.type] ?? a.cible.type})</span>
          {#if a.corps.since || a.corps.until}{' '}
            <span class="rel-dates">{a.corps.since || '?'}{a.corps.until ? ' → '+a.corps.until : ''}</span>
          {' '}{/if}
          <span class="rel-src">{libelleSource(a.corps.source)}</span>
          <Fiabilite code={a.corps.confidence} />
          <span class="tag-attente">à enregistrer</span>
          <button class="lien-annuler" on:click={() => retirer(a.cle)}>retirer</button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if tranche && (refus.length || refusErreur)}
    <div class="refus" id="refus">
      <h3>Liens refusés <span class="count-badge">{refus.length}</span></h3>
      <p class="muted">Supprimés par un validateur : la collecte ne les réécrit plus.
        « Garder » dit que le refus tient encore ; « Lever » laisse la prochaine collecte le réécrire.</p>
      {#if refusErreur}<p class="erreur">{refusErreur}</p>{/if}
      {#if refusAvis}<p class="avis" role="status">{refusAvis}</p>{/if}
      <ul>
        {#each refus as x (x.id)}
          <li>
            <p><span class="rel-type">{libelleRelation(x.relation_type)}</span>
              {autreBout(x)}</p>
            <p class="muted">Refusé par {x.refuse_par ?? '—'}, {heureLocale(x.refuse_le)}
              — {x.motif ? `« ${x.motif} »` : 'sans motif'}
              {#if x.revu_le}· revu par {x.revu_par}, {heureLocale(x.revu_le)}{/if}</p>
            {#if x.sources?.length}
              <p class="muted">Redit depuis par :
                {#each x.sources as s, i}{i ? ', ' : ''}<span class:nouvelle={s.nouvelle}>{libelleSource(s.source)}
                  ({s.tentatives} fois, dernière le {heureLocale(s.derniere)}){s.nouvelle ? ', non revue' : ''}</span>{/each}
              </p>
            {:else}
              <p class="muted">Aucune source ne l'a redit depuis.</p>
            {/if}
            <div class="refus-gestes">
              <input bind:value={refusMotif[x.id]} maxlength="500"
                     aria-label="Motif de la revue ou de la levée"
                     placeholder="Motif (seulement pour lever le refus)" />
              <button class="btn-texte" disabled={refusEnCours === x.id} on:click={() => garder(x)}>Garder le refus</button>
              <button class="btn-texte danger" disabled={refusEnCours === x.id} on:click={() => lever(x)}>Lever le refus</button>
            </div>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Ajouter une relation -->
  <div class="add-rel">
    <h3>Ajouter une relation</h3>
    <div class="add-rel-grid">

      <div class="col2 champ">
        <span>Direction</span>
        <div class="dir-toggle">
          <button class="dir-btn" class:active={newRel.direction==='from'} on:click={() => newRel.direction='from'}>
            Cette fiche → fiche liée
          </button>
          <button class="dir-btn" class:active={newRel.direction==='to'} on:click={() => newRel.direction='to'}>
            Fiche liée → cette fiche
          </button>
        </div>
      </div>

      <label class="col2 search-wrap">
        Fiche à relier
        <input bind:value={relSearch} on:input={onRelSearch}
               on:blur={() => setTimeout(() => relSearchOpen=false, 180)}
               placeholder="Rechercher par nom…"
               class:has-target={relTarget !== null} />
        {#if relSearchOpen && relSearchResults.length > 0}
          <ul class="search-dropdown">
            {#each relSearchResults as e (e.id)}
              <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
              <li on:mousedown={() => selectTarget(e)}>
                <span class="sd-type sd-{e.type}">{libelleType(e.type)}</span>
                <span class="sd-name">{e.name}</span>
                {#if e.address}<span class="sd-addr">{e.address}</span>{/if}
              </li>
            {/each}
          </ul>
        {/if}
      </label>

      <label>Nature du lien
        <select bind:value={newRel.relation_type}>
          {#each REL_TYPES as t}<option value={t}>{libelleRelation(t)}</option>{/each}
        </select>
      </label>
      <label>Fiabilité
        <select bind:value={newRel.confidence}>
          <option value="verified">Sûre</option>
          <option value="probable">Probable</option>
          <option value="hypothesis">Hypothèse</option>
        </select>
      </label>
      <label>Depuis<input bind:value={newRel.since} placeholder="AAAA-MM-JJ" /></label>
      <label>Jusqu'au<input bind:value={newRel.until} placeholder="AAAA-MM-JJ" /></label>
      <label class="col2">Source<input bind:value={newRel.source} placeholder="Par exemple : saisie à la main, SIRENE" /></label>
    </div>

    <button class="btn-add-small" on:click={ajouter} disabled={!relTarget}>
      + Ajouter la relation à la fiche
    </button>
  </div>
</section>

<style>
  .relation-list { list-style: none; display: flex; flex-direction: column; gap: .25rem; }

  .rel-type  { background: var(--surface-2); color: var(--texte); border-radius: 3px; padding: 1px 6px; font-size: .7rem; white-space: nowrap; }
  .rel-dir   { flex: 1; color: var(--info); }
  .rel-dir a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
  .rel-dir a:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  .rel-dates { color: var(--texte-doux); font-size: .72rem; white-space: nowrap; }
  .rel-src   { color: var(--texte-doux); font-size: .7rem; }

  .rel-item { border-bottom: 1px solid var(--bordure); }
  .rel-item:last-child { border-bottom: none; }
  .rel-item:not(.editing) {
    display: flex; align-items: center; gap: .4rem; flex-wrap: wrap;
    padding: .35rem .3rem; font-size: .78rem;
  }
  .rel-item.editing { padding: .55rem; margin: .25rem 0; background: var(--fond); border-radius: 6px; }

  .rel-row-actions { margin-left: auto; display: flex; gap: .2rem; }

  .rel-edit-form { display: flex; flex-direction: column; gap: .4rem; }
  .rel-edit-row  { display: flex; gap: .45rem; flex-wrap: wrap; }
  .rel-edit-row label { flex: 1; min-width: 90px; }

  .btn-suppr-confirmer {
    padding: .3rem .65rem; border-radius: 5px; font-size: .75rem; font-weight: 600; cursor: pointer;
    background: var(--danger-bordure); border: 1px solid var(--danger-bordure); color: var(--danger-texte);
  }
  .btn-suppr-confirmer:disabled { background: transparent; color: var(--texte-doux); border-color: var(--bordure); cursor: default; }
  .motif-suppr { flex-basis: 100%; display: flex; flex-direction: column; gap: .35rem; padding: .3rem 0; }
  .aide-droits { margin-bottom: .5rem; }
  .refus { margin-top: .85rem; padding-top: .85rem; border-top: 1px solid var(--bordure); }
  .refus h3 { font-size: .72rem; font-weight: 600; color: var(--texte-doux); text-transform: uppercase;
              letter-spacing: .04em; margin-bottom: .4rem; display: flex; gap: .4rem; align-items: center; }
  .refus ul { list-style: none; padding: 0; display: flex; flex-direction: column; gap: .5rem; }
  .refus li { border: 1px solid var(--bordure); border-radius: 6px; padding: .5rem; font-size: .78rem;
              display: flex; flex-direction: column; gap: .25rem; }
  .refus .nouvelle { color: var(--alerte-texte); font-weight: 600; }
  .refus-gestes { display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
  .refus-gestes input { flex: 1 1 12rem; }
  .refus .avis { color: var(--texte); font-size: .78rem; }
  .refus .erreur { color: var(--danger); font-size: .78rem; }
  .add-rel { margin-top: .85rem; padding-top: .85rem; border-top: 1px solid var(--bordure); }
  .add-rel h3 {
    font-size: .72rem; font-weight: 600; color: var(--texte-doux);
    text-transform: uppercase; letter-spacing: .04em; margin-bottom: .6rem;
  }
  .add-rel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; margin-bottom: .6rem; }
  .champ { display: flex; flex-direction: column; gap: .3rem; font-size: .76rem; color: var(--texte-doux); }

  .dir-toggle { display: flex; gap: .25rem; }
  .dir-btn {
    flex: 1; padding: .3rem .35rem; border-radius: 4px; border: 1px solid var(--bordure);
    background: transparent; color: var(--texte-doux); font-size: .72rem; cursor: pointer;
    text-align: center; transition: all .12s;
  }
  .dir-btn.active { background: var(--accent-fort); border-color: var(--accent-fort); color: var(--sur-accent); }

  .search-wrap { position: relative; }
  .search-wrap input.has-target { border-color: var(--succes-bordure); color: var(--succes); }

  .search-dropdown {
    position: absolute; top: 100%; left: 0; right: 0; z-index: 200;
    background: var(--surface); border: 1px solid var(--bordure); border-radius: 6px;
    list-style: none; max-height: 200px; overflow-y: auto;
    box-shadow: 0 8px 24px var(--ombre);
  }
  .search-dropdown li {
    display: flex; align-items: center; gap: .4rem;
    padding: .4rem .55rem; cursor: pointer; font-size: .78rem; transition: background .1s;
  }
  .search-dropdown li:hover { background: var(--surface-2); }

  .sd-type { font-size: .64rem; padding: 1px 5px; border-radius: 3px; color: var(--sur-accent); font-weight: 600; white-space: nowrap; }
  .sd-person      { background: var(--type-personne); }
  .sd-business    { background: var(--type-entreprise); }
  .sd-association { background: var(--type-association); }
  .sd-place       { background: var(--type-lieu); }
  .sd-service     { background: var(--type-service); }
  .sd-name  { flex: 1; color: var(--texte); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sd-addr  { color: var(--texte-doux); font-size: .7rem; white-space: nowrap; }

  @media (max-width: 640px) {
    .add-rel-grid { grid-template-columns: 1fr; }
  }
</style>
