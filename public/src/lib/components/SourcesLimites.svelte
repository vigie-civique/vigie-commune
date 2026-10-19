<script>
  // « Sources et limites » d'un bloc de donnée : d'où vient le chiffre, de
  // quand, et ce qu'il ne dit pas. Les textes vivent dans UNE déclaration
  // (`#lib/sources-limites.json`), jamais dans la page.
  //
  // Replié par défaut, sur une ligne : la page se lit d'abord par ses
  // chiffres, et qui veut la provenance l'ouvre. Ce repli ne remplace pas les
  // encadrés « Ce que ce chiffre ne dit pas » : une précaution qui change la
  // lecture d'un chiffre reste visible, ici ne vient que le détail.
  //
  // Usage :
  //   <SourcesLimites bloc="territoire.logement" millesime="2022" />
  //   <SourcesLimites blocs={['argent.impots', 'argent.marches']} />   (une grille)
  import declaration from '#lib/sources-limites.json'

  export let bloc = ''
  export let blocs = []
  export let millesime = ''
  // Population de la commune : décide si le recensement y procède par sondage.
  export let population = null

  // Le seuil de l'INSEE : sous 200, un effectif tiré d'un sondage « doit être
  // manié avec précaution ». L'exploitation complémentaire est un sondage partout ;
  // la principale ne l'est qu'à partir de 10 000 habitants — en dessous, la
  // commune est recensée en entier.
  const SEUIL_SONDAGE = 10000
  const sonde = (e) => e.sondage === 'complementaire'
    || (e.sondage === 'principale' && population != null && population >= SEUIL_SONDAGE)

  $: cles = bloc ? [bloc] : blocs
  $: entrees = cles.map((c) => ({ cle: c, ...declaration.blocs[c] }))
  $: seul = entrees.length === 1
</script>

<details class="limites">
  <summary>Sources et limites</summary>
  {#if seul}
    {@const e = entrees[0]}
    <p>
      <b>Source</b>&nbsp;: {#if e.lien}<a href={e.lien} rel="noopener">{e.source}</a>{:else}{e.source}{/if}{#if millesime || e.millesime}, {millesime || e.millesime}{/if}.
    </p>
    <p><b>Ce que le chiffre ne dit pas</b>&nbsp;: {e.limite}</p>
    {#if sonde(e)}
      <p>Le recensement procède ici par sondage&nbsp;: un effectif inférieur à 200 est à manier avec précaution (INSEE).</p>
    {/if}
  {:else}
    <dl>
      {#each entrees as e (e.cle)}
        <dt>{#if e.lien}<a href={e.lien} rel="noopener">{e.source}</a>{:else}{e.source}{/if}</dt>
        <dd>{e.limite}</dd>
      {/each}
    </dl>
  {/if}
</details>

<style>
  .limites { margin: .3rem 0 1.4rem; font-size: .8rem; color: var(--gris); max-width: 72ch; }
  .limites summary { cursor: pointer; width: fit-content; color: var(--gris); }
  .limites summary:hover { color: var(--encre); }
  .limites[open] summary { margin-bottom: .3rem; }
  .limites p { margin: .2rem 0; line-height: 1.5; }
  .limites b { color: var(--encre); font-weight: 600; }
  .limites dl { margin: .2rem 0; }
  .limites dt { color: var(--encre); margin-top: .45rem; }
  .limites dd { margin: .1rem 0 0; line-height: 1.5; }
</style>
