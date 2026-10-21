<script>
  // Une barre empilée, doublée d'un tableau : la largeur d'un segment ne se lit
  // ni au lecteur d'écran ni pour un petit budget (22 k€ à côté de 1,7 M€). Le
  // tableau porte les chiffres ; la barre ne fait que montrer les proportions.
  export let parts = []          // [{ nom, montant, href? }]
  export let format              // montant → texte
  export let titre = ''
  export let total = true        // false : les montants ne s'additionnent pas

  $: somme = parts.reduce((s, p) => s + (p.montant > 0 ? p.montant : 0), 0)
  const teinte = (i) => i === 0 ? 'var(--ardoise)' : 'var(--ambre)'
  const opacite = (i) => i === 0 ? 1 : Math.max(.3, 1 - (i - 1) * .17)
  const part = (m) => somme ? Math.round(100 * m / somme) + ' %' : ''
</script>

<div class="pile" aria-hidden="true">
  {#each parts as p, i}
    {#if p.montant > 0}{' '}
      <span style="width:{100 * p.montant / somme}%; background:{teinte(i)}; opacity:{opacite(i)}" title="{p.nom} : {format(p.montant)}"></span>
    {' '}{/if}
  {/each}
</div>
<table class="parts">
  <caption class="visually-hidden">{titre}</caption>
  <thead><tr><th scope="col">Budget</th><th scope="col" class="r">Montant</th>{#if total}<th scope="col" class="r">Part</th>{/if}</tr></thead>
  <tbody>
    {#each parts as p, i}
      <tr>
        <td><i class="sw" style="background:{teinte(i)}; opacity:{opacite(i)}"></i>{#if p.href}<a href={p.href}>{p.nom}</a>{:else}{p.nom}{/if}</td>
        <td class="r">{format(p.montant)}</td>
        {#if total}<td class="r muted">{part(p.montant)}</td>{/if}
      </tr>
    {/each}
  </tbody>
  {#if total}
    <tfoot><tr><th scope="row">Total</th><td class="r"><b>{format(somme)}</b></td><td></td></tr></tfoot>
  {/if}
</table>

<style>
  .pile { display: flex; height: 26px; border-radius: 6px; overflow: hidden; background: var(--trait-pale); margin: .5rem 0 .4rem; gap: 2px; }
  .pile span { display: block; height: 100%; min-width: 3px; }
  .parts { width: 100%; border-collapse: collapse; font-size: .85rem; margin-bottom: 1.25rem; }
  .parts th, .parts td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--ardoise-pale); }
  .parts th { color: var(--gris); font-weight: 600; }
  .parts .r { text-align: right; font-variant-numeric: tabular-nums; }
  .parts .muted { color: var(--gris-clair); }
  .parts tfoot th, .parts tfoot td { border-bottom: none; border-top: 1px solid var(--trait); }
  .parts a { color: var(--ardoise); }
  .sw { display: inline-block; width: 11px; height: 11px; border-radius: 3px; margin-right: .45rem; vertical-align: -1px; }
</style>
