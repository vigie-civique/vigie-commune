<script>
  // Le mini-graphique d'une tuile du tableau de bord (cf. #lib/tableau.server.js).
  // Du SVG écrit au prérendu : ni script, ni mesure dans le navigateur. Le
  // dessin est décoratif — la tuile porte déjà la valeur et sa période en
  // texte, c'est elle que lit un lecteur d'écran.
  export let graphe

  const L = 120
  const H = 34

  // ── barres ──
  $: barres = graphe.type === 'barres' ? (() => {
    const max = Math.max(...graphe.points.map((p) => p.y), 1)
    const pas = L / graphe.points.length
    return graphe.points.map((p, i) => {
      // Une barre à zéro garde un trait : l'année existe, elle est vide.
      const h = p.y ? Math.max((p.y / max) * H, 1.5) : .6
      return { x: i * pas + pas * .12, l: pas * .76, y: H - h, h, partiel: p.partiel }
    })
  })() : []

  // ── courbes ──
  // Une échelle commune aux séries d'une même tuile ; `depuisZero` pour ce qui
  // se lit en niveau (une population, un prix), sinon l'étendue des valeurs.
  // `bornes` : l'axe imposé par la tuile, qui l'écrit alors en clair dessous
  // (`echelle`) — un axe qui ne part pas de zéro se dit.
  // `escalier` : une valeur qui vaut pour l'année entière (un taux voté) ;
  // chaque année est une marche d'un an de large, la dernière comprise.
  $: courbes = graphe.type === 'courbe' ? (() => {
    const tous = graphe.series.flatMap((s) => s.points)
    const x0 = Math.min(...tous.map((p) => p.x))
    const x1 = Math.max(...tous.map((p) => p.x)) + (graphe.escalier ? 1 : 0)
    const haut = Math.max(...tous.map((p) => p.y))
    const bas = graphe.depuisZero ? 0 : Math.min(...tous.map((p) => p.y))
    const marge = (haut - bas) * .12 || 1
    const lo = graphe.bornes ? graphe.bornes[0] : graphe.depuisZero ? 0 : bas - marge
    const hi = graphe.bornes ? graphe.bornes[1] : haut + marge
    const sx = (x) => (x1 === x0 ? L / 2 : ((x - x0) / (x1 - x0)) * L)
    const sy = (y) => H - 2 - ((y - lo) / (hi - lo)) * (H - 4)
    // `annuel` : des points d'années entières. Une année absente coupe le
    // trait (un nouveau `M`) : le relier inventerait une pente sur des années
    // sans donnée. Un point isolé entre deux trous reste visible, en segment
    // nul que l'extrémité ronde du trait dessine en point. Sans `annuel` (un
    // recensement, des années espacées par nature), le tracé ne change pas.
    const coupe = (pts, i) => i === 0 || (graphe.annuel && pts[i].x - pts[i - 1].x > 1)
    // Les marches : un palier horizontal par année, une contremarche quand le
    // taux change. Une année absente coupe le trait, comme ci-dessous.
    const marches = (pts) => pts.map((p, i) => {
      const y = sy(p.y).toFixed(1)
      const fin = sx(p.x + 1).toFixed(1)
      return coupe(pts, i) ? `M${sx(p.x).toFixed(1)} ${y} H${fin}` : `V${y} H${fin}`
    }).join(' ')
    return graphe.series.map((s) => ({
      ton: s.ton,
      d: graphe.escalier ? marches(s.points) : s.points.map((p, i, pts) => {
        const xy = `${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`
        if (!coupe(pts, i)) return `L${xy}`
        const seul = graphe.annuel && pts.length > 1 && (i === pts.length - 1 || coupe(pts, i + 1))
        return seul ? `M${xy} L${xy}` : `M${xy}`
      }).join(' '),
    }))
  })() : []

  // ── parts ──
  $: parts = graphe.type === 'parts' ? (() => {
    const total = graphe.parts.reduce((t, p) => t + p.n, 0)
    let x = 0
    return graphe.parts.map((p, i) => {
      const l = (p.n / total) * L
      const part = { x, l: Math.max(l - .8, .4), i }
      x += l
      return part
    })
  })() : []
</script>

{#if graphe.type === 'barres'}{' '}
  <svg viewBox="0 0 {L} {H}" preserveAspectRatio="none" aria-hidden="true">
    {#each barres as b}
      <rect x={b.x} y={b.y} width={b.l} height={b.h} class:partiel={b.partiel} />
    {/each}
  </svg>
{' '}{:else if graphe.type === 'courbe'}{' '}
  <svg viewBox="0 0 {L} {H}" preserveAspectRatio="none" aria-hidden="true">
    {#each courbes as c}
      <path d={c.d} class="trait {c.ton}" />
    {/each}
  </svg>
  <!-- Deux courbes se distinguent par le trait (plein, pointillé) autant que
       par la couleur : la légende le montre. -->
  {#if graphe.series.some((x) => x.ton === 'depense')}{' '}
    <span class="legende" aria-hidden="true"><span><i></i>recettes</span> <span><i class="dep"></i>dépenses</span></span>
  {' '}{/if}
  {#if graphe.echelle}<span class="echelle">{graphe.echelle}</span>{/if}
{' '}{:else if graphe.type === 'frise'}{' '}
  <svg viewBox="0 0 {L} {H}" preserveAspectRatio="none" aria-hidden="true">
    {#each graphe.lignes as ligne, i}
      {@const h = H / graphe.lignes.length}
      <line x1="0" x2={L} y1={h * i + h / 2} y2={h * i + h / 2} class="axe" />
      {#each ligne as p}
        <rect x={p.x * (L - 1.6)} y={h * i + h * .18} width="1.6" height={h * .64}
              class:partiel={!p.plein} class:second={i > 0} />
      {/each}
    {/each}
  </svg>
{' '}{:else if graphe.type === 'parts'}{' '}
  <svg class="bas" viewBox="0 0 {L} 8" preserveAspectRatio="none" aria-hidden="true">
    {#each parts as p}
      <rect x={p.x} y="0" width={p.l} height="8" style="opacity:{1 - p.i * .24}" />
    {/each}
  </svg>
{' '}{:else if graphe.type === 'jauge'}{' '}
  <svg class="bas" viewBox="0 0 {L} 8" preserveAspectRatio="none" aria-hidden="true">
    <rect x="0" y="0" width={L} height="8" class="fond" />
    <rect x="0" y="0" width={Math.max(0, Math.min(1, graphe.part)) * L} height="8" />
  </svg>
{' '}{/if}

<style>
  /* Barres et courbes à l'encre ; recettes et dépenses dans leurs couleurs de
     données. Jamais la teinte de la commune : elle n'est pas une donnée. */
  svg { display: block; width: 100%; height: 2.1rem; overflow: visible; }
  svg.bas { height: .5rem; margin-top: 1.6rem; }
  rect { fill: var(--encre); }
  rect.partiel { opacity: .38; }
  rect.second { fill: var(--gris); }
  rect.fond { fill: var(--filet); }
  .axe { stroke: var(--filet); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .trait {
    fill: none; stroke: var(--encre); stroke-width: 2;
    stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke;
  }
  .trait.recette { stroke: var(--recette); }
  .trait.depense { stroke: var(--depense); stroke-dasharray: 4 3; }
  .legende { display: flex; flex-wrap: wrap; gap: 2px 10px; margin-top: 6px; color: var(--gris); font-size: 13px; line-height: 1.3; }
  .legende i { display: inline-block; width: 14px; height: 0; margin-right: 4px; border-top: 2px solid var(--recette); vertical-align: 3px; }
  .legende i.dep { border-top: 2px dashed var(--depense); }
  .echelle { display: block; margin-top: 4px; color: var(--gris); font: 400 13px/1.3 var(--mono); font-variant-numeric: tabular-nums; }
</style>
