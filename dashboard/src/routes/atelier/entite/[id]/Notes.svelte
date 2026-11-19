<script>
  import { cleLocale } from './brouillon.js'
  import { auMoins } from '#lib/roles.js'
  import { currentUser } from '#lib/stores/auth.js'
  import Fiabilite from '#lib/components/Fiabilite.svelte'
  import { choixFiabilite, libelleSource } from '#lib/libelles.js'

  export let notes = []
  export let brouillon            // { ajouts, modifs: {id: {note, source, confidence}}, suppr }

  // Un contributeur ne pose pas de fiabilité publiable : l'API refuse
  // `verified` venant de lui (audit du 15/11/2026).
  $: tranche = auMoins($currentUser, 'validator')
  $: QUALITES = tranche ? ['verified', 'probable', 'hypothesis', 'unverified']
                        : ['probable', 'hypothesis', 'unverified']
  const noteVide = () => ({ note: '', source: 'manual',
                            confidence: auMoins($currentUser, 'validator') ? 'verified' : 'probable' })

  let nouvelle = noteVide()
  let editingNoteId = null
  let edition = {}

  function ajouter() {
    if (!nouvelle.note.trim()) return
    brouillon.ajouts = [...brouillon.ajouts, { cle: cleLocale(), corps: { ...nouvelle } }]
    nouvelle = noteVide()
  }

  function startEditNote(n) {
    editingNoteId = n.id
    edition = { ...(brouillon.modifs[n.id] ?? { note: n.note, source: n.source, confidence: n.confidence }) }
  }

  function appliquer(n) {
    const { [n.id]: _, ...autres } = brouillon.modifs
    const change = ['note', 'source', 'confidence'].some(k => (edition[k] ?? '') !== (n[k] ?? ''))
    brouillon.modifs = change ? { ...autres, [n.id]: { ...edition } } : autres
    editingNoteId = null
  }

  function annulerModif(id) {
    const { [id]: _, ...autres } = brouillon.modifs
    brouillon.modifs = autres
  }

  const basculerSuppr = id => brouillon.suppr = brouillon.suppr.includes(id)
    ? brouillon.suppr.filter(x => x !== id) : [...brouillon.suppr, id]
  const retirer = cle => brouillon.ajouts = brouillon.ajouts.filter(a => a.cle !== cle)
</script>

<section class="card">
  <h2>Notes <span class="count-badge">{notes.length}</span></h2>

  <div class="add-note">
    <textarea bind:value={nouvelle.note} rows="2" placeholder="Nouvelle note…"></textarea>
    <div class="add-note-meta">
      <input bind:value={nouvelle.source} placeholder="source" />
      <select bind:value={nouvelle.confidence}>
        {#each choixFiabilite(QUALITES) as q}<option value={q.cle}>{q.libelle}</option>{/each}
      </select>
      <button class="btn-add-small" on:click={ajouter} disabled={!nouvelle.note.trim()}>
        + Ajouter à la fiche
      </button>
    </div>
  </div>

  {#if notes.length || brouillon.ajouts.length}
    <ul class="note-list">
      {#each brouillon.ajouts as a (a.cle)}
        <li class="note-item a-enregistrer">
          <div class="note-body">
            <span class="tag-attente">à enregistrer</span>
            <Fiabilite code={a.corps.confidence} />
            <span class="note-src muted">{libelleSource(a.corps.source)}</span>
            <p class="note-text">{a.corps.note}</p>
          </div>
          <button class="lien-annuler" on:click={() => retirer(a.cle)}>retirer</button>
        </li>
      {/each}
      {#each notes as n (n.id)}
        {@const m = brouillon.modifs[n.id]}
        {@const v = m ?? n}
        <li class="note-item" class:a-supprimer={brouillon.suppr.includes(n.id)} class:a-modifier={m}>
          {#if editingNoteId === n.id}
            <div class="note-edit">
              <textarea bind:value={edition.note} rows="3"></textarea>
              <div class="note-edit-meta">
                <input bind:value={edition.source} placeholder="source" />
                <select bind:value={edition.confidence}>
                  {#each choixFiabilite(QUALITES) as q}<option value={q.cle}>{q.libelle}</option>{/each}
                </select>
              </div>
              <div class="rel-edit-actions">
                <button class="btn-rel-save" on:click={() => appliquer(n)}>✓ Appliquer</button>
                <button class="btn-rel-cancel" on:click={() => editingNoteId = null}>Fermer</button>
              </div>
            </div>
          {:else}
            <div class="note-body">
              <span class="note-date">{n.date ?? ''}</span>
              <Fiabilite code={v.confidence} />
              <span class="note-src muted">{libelleSource(v.source)}</span>
              {#if brouillon.suppr.includes(n.id)}{' '}
                <span class="tag-attente">sera effacée quand vous enregistrerez la fiche</span>
                <button class="lien-annuler" on:click={() => basculerSuppr(n.id)}>rétablir</button>
              {' '}{:else if m}{' '}
                <span class="tag-attente">modifiée, à enregistrer</span>
                <button class="lien-annuler" on:click={() => annulerModif(n.id)}>annuler</button>
              {' '}{/if}
              <p class="note-text">{v.note}</p>
            </div>
            {#if !brouillon.suppr.includes(n.id)}
              <div class="note-actions">
                <button class="btn-texte" on:click={() => startEditNote(n)} aria-label="Modifier cette note">Modifier</button>
                <button class="btn-texte danger" on:click={() => basculerSuppr(n.id)}
                        title="Efface la note de la base" aria-label="Supprimer cette note : l'effacer de la base">Supprimer</button>
              </div>
            {/if}
          {/if}
        </li>
      {/each}
    </ul>
  {:else}
    <p class="muted">Aucune note.</p>
  {/if}
</section>

<style>
  .note-list { list-style: none; display: flex; flex-direction: column; gap: .5rem; margin-top: .6rem; }
  .note-item { display: flex; align-items: flex-start; gap: .4rem; padding: .45rem .5rem; background: var(--fond); border-radius: 5px; }
  .note-body { flex: 1; min-width: 0; }
  .note-date { font-size: .7rem; color: var(--texte-doux); margin-right: .3rem; }
  .note-src  { font-size: .7rem; margin-left: .3rem; }
  .note-text { margin: .25rem 0 0; font-size: .8rem; color: var(--texte-2); white-space: pre-wrap; word-break: break-word; }
  .note-actions { display: flex; flex-direction: column; gap: .2rem; flex-shrink: 0; }
  .note-edit { flex: 1; display: flex; flex-direction: column; gap: .35rem; }
  .note-edit textarea { width: 100%; }
  .note-edit-meta { display: flex; gap: .4rem; }
  .note-edit-meta input  { flex: 1; }
  .note-edit-meta select { width: 110px; }
  .add-note { display: flex; flex-direction: column; gap: .4rem; }
  .add-note textarea { width: 100%; resize: vertical; }
  .add-note-meta { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
  .add-note-meta input  { flex: 1; min-width: 80px; }
  .add-note-meta select { width: 110px; }
</style>
