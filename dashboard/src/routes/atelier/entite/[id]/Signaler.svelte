<script>
  // Demander qu'une fiche sorte du site, soit fusionnée avec son doublon, ou
  // soit revue. Ouvert à tout compte, et toujours une demande : la fiche ne
  // bouge pas, un validateur tranche dans la file des propositions (décision
  // de Julien, 15/11/2026). Le contributeur n'écarte toujours rien lui-même.
  import { createEventDispatcher } from 'svelte'
  import { api } from '#lib/api.js'
  import { libelleType, phraseErreur } from '#lib/libelles.js'

  export let entity

  const dispatch = createEventDispatcher()
  const MOTIFS = [
    { cle: 'retirer', libelle: 'À retirer du site',
      aide: "Elle n'a rien à faire en ligne : erreur, structure dissoute, personne privée…" },
    { cle: 'doublon', libelle: "Doublon d'une autre fiche",
      aide: 'La même structure ou la même personne a déjà sa fiche. La fusion se fera à la main.' },
    { cle: 'revoir', libelle: 'À revoir',
      aide: "Quelque chose cloche, sans qu'il faille la retirer." },
  ]

  let motif = ''
  let texte = ''
  let envoi = false
  let erreur = ''

  // Doublon : l'autre fiche se choisit dans la recherche.
  let q = ''
  let resultats = []
  let autre = null
  let minuterie
  function chercher() {
    clearTimeout(minuterie); autre = null
    if (q.trim().length < 2) { resultats = []; return }
    minuterie = setTimeout(async () => {
      try { resultats = (await api.search(q.trim(), 8)).filter(e => e.id !== entity.id) }
      catch { resultats = [] }
    }, 250)
  }
  function choisir(e) { autre = e; q = e.name; resultats = []
    setTimeout(() => envoi_btn?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 50) }
  let envoi_btn

  $: pret = motif && (motif !== 'doublon' || autre)

  async function envoyer() {
    if (!pret || envoi) return
    envoi = true; erreur = ''
    try {
      const r = await api.signaler(entity.id, motif, texte.trim(), autre?.id ?? null)
      dispatch('envoye', r.message)
    } catch (e) {
      erreur = phraseErreur(e)
    } finally { envoi = false }
  }
</script>

<section class="signaler card" aria-labelledby="titre-signaler">
  <h2 id="titre-signaler">Signaler cette fiche</h2>
  <p class="muted">Votre demande part dans la file des propositions. La fiche ne change pas
    tant qu'un validateur n'a pas décidé.</p>

  <fieldset>
    <legend>Pourquoi ?</legend>
    {#each MOTIFS as m}
      <label class="choix">
        <input type="radio" name="motif" value={m.cle} bind:group={motif} />
        <span><strong>{m.libelle}</strong><br /><small>{m.aide}</small></span>
      </label>
    {/each}
  </fieldset>

  {#if motif === 'doublon'}{' '}
    <label class="doublon-recherche">
      L'autre fiche
      <input bind:value={q} on:input={chercher} placeholder="Chercher par nom…"
             autocomplete="off" class:choisie={autre} />
    </label>
    {#if resultats.length}
      <p class="muted">Touchez la bonne fiche dans la liste :</p>
      <ul class="resultats">
        {#each resultats as e (e.id)}
          <li><button type="button" on:click={() => choisir(e)}>
            {e.name} <em>{libelleType(e.type)}{e.address ? ` · ${e.address}` : ''}</em>
            {#if e.ecartee}<span class="ecartee">écartée du site</span>{/if}
          </button></li>
        {/each}
      </ul>
    {/if}
    {#if autre}<p class="muted">↳ doublon de <a href="/atelier/entite/{autre.id}">{autre.name}</a> (n° {autre.id})</p>{/if}
  {/if}

  <label>
    Précision (facultatif, 500 caractères au plus)
    <textarea bind:value={texte} maxlength="500" rows="2"
              placeholder="Par exemple : dissoute en 2019, voir le Journal officiel"></textarea>
  </label>

  {#if erreur}<p class="erreur" role="alert">{erreur}</p>{/if}

  <div class="gestes">
    <button class="envoyer" bind:this={envoi_btn} on:click={envoyer} disabled={!pret || envoi}>
      {envoi ? 'Envoi…' : 'Envoyer le signalement'}
    </button>
    <button class="fermer" on:click={() => dispatch('fermer')}>Fermer</button>
  </div>
</section>

<style>
  .signaler { display: flex; flex-direction: column; gap: .6rem; margin: .75rem 1rem 0; }
  fieldset { border: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .35rem; }
  legend { font-size: .76rem; color: var(--texte-doux); margin-bottom: .25rem; }
  .choix { flex-direction: row !important; align-items: flex-start; gap: .5rem !important;
           padding: .45rem .5rem; border: 1px solid var(--bordure); border-radius: 6px;
           color: var(--texte) !important; font-size: .82rem !important; cursor: pointer; min-height: 44px; }
  .choix input { width: auto !important; margin-top: .2rem; }
  .choix small { color: var(--texte-doux); }
  .doublon-recherche input.choisie { border-color: var(--succes-bordure); }
  .resultats { list-style: none; padding: 0; margin: 0; border: 1px solid var(--bordure); border-radius: 6px; }
  .resultats button::after { content: ' ›'; color: var(--info); }
  .resultats button { display: block; width: 100%; text-align: left; padding: .5rem; min-height: 44px;
                      font-size: .82rem; color: var(--texte); border-bottom: 1px solid var(--bordure-douce); }
  .resultats em { color: var(--texte-doux); font-style: normal; font-size: .74rem; }
  .ecartee { margin-left: .3rem; font-size: .7rem; color: var(--danger-texte); background: var(--danger-bordure);
             border-radius: 3px; padding: 0 4px; }
  .erreur { color: var(--danger); font-size: .82rem; }
  .gestes { display: flex; gap: .5rem; flex-wrap: wrap; }
  .gestes button { min-height: 44px; padding: .4rem .9rem; border-radius: 6px; font-size: .85rem; }
  .envoyer { background: var(--accent-fort); color: var(--sur-accent); font-weight: 600; }
  .envoyer:disabled { opacity: .45; }
  .fermer { border: 1px solid var(--bordure); color: var(--texte); }
  a { color: var(--lien); }
</style>
