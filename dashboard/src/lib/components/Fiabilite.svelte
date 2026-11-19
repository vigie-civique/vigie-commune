<script>
  // La fiabilité d'une donnée, en toutes lettres : un point coloré seul ne se lit
  // ni au toucher (pas de survol) ni sans distinguer les couleurs.
  import { libelleFiabilite, FIABILITE_AIDE } from '#lib/libelles.js'
  export let code
</script>

<span class="fiab fiab-{code}" title={FIABILITE_AIDE[code] ?? ''}>{libelleFiabilite(code)}</span>

<style>
  .fiab { font-size: .68rem; border-radius: 3px; padding: 1px 6px; white-space: nowrap;
          color: var(--texte-doux); background: var(--surface); }
  .fiab-verified, .fiab-confirmed { color: var(--succes); background: var(--succes-doux); }
  .fiab-probable { color: var(--alerte); background: var(--alerte-doux); }
</style>
