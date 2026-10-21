<script>
  // Un seul choix de périmètre pour la page : il est dit une fois, ici, au
  // lieu d'un « (budget principal) » collé à chaque libellé.
  export let value = 'principal'
  // Comptes consolidés OFGL disponibles : « tous budgets » additionne alors
  // aussi recettes et dépenses, flux entre budgets retirés.
  export let consolide = false

  const CHOIX = [
    { id: 'principal', label: 'Budget principal' },
    { id: 'annexes',   label: 'Budgets annexes' },
    { id: 'tous',      label: 'Tous budgets' },
  ]
  $: PHRASE = {
    principal: "Le budget de la commune elle-même, sans les services qui tiennent leurs propres comptes (budgets annexes, détaillés plus bas).",
    annexes: "Les budgets annexes additionnés : les services qui tiennent leurs propres comptes, détaillés un par un plus bas.",
    tous: consolide
      ? "Budget principal et budgets annexes ensemble. La commune verse de l'argent à ses budgets annexes (subventions, personnel remboursé) : ces flux entre budgets sont retirés, sinon la même somme serait comptée deux fois."
      : "Budget principal et budgets annexes ensemble. Recettes et dépenses n'y sont pas additionnées : le budget principal verse une partie de son argent aux budgets annexes, et la même somme serait comptée deux fois.",
  }
</script>

<div class="perimetre" id="perimetre">
  <span class="plabel" id="perimetre-label">Périmètre</span>
  <div class="pseg" role="group" aria-labelledby="perimetre-label">
    {#each CHOIX as c}
      <button type="button" class:on={value === c.id} aria-pressed={value === c.id}
              on:click={() => value = c.id}>{c.label}</button>
    {/each}
  </div>
  <p class="pphrase" aria-live="polite">{PHRASE[value]}</p>
</div>

<style>
  .perimetre { margin: 1rem 0 .9rem; }
  .plabel { display: block; font-size: .78rem; color: var(--gris); font-weight: 600; margin-bottom: .3rem; }
  .pseg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--trait); border-radius: 8px; overflow: hidden; }
  .pseg button { min-height: 44px; padding: .4rem .9rem; font-size: .88rem; background: #fff; color: var(--gris); border: none; border-left: 1px solid var(--trait); cursor: pointer; }
  .pseg button:first-child { border-left: none; }
  .pseg button.on { background: var(--ardoise); color: #fff; font-weight: 600; }
  .pphrase { margin: .45rem 0 0; font-size: .85rem; color: var(--gris); line-height: 1.5; max-width: 70ch; }
</style>
