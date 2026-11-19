<script>
  // Commune nouvelle : dire d'où vient chaque chiffre d'avant la fusion.
  //
  // Un exercice lu sous le SIREN de l'ancienne commune sortait sous le nom
  // actuel, et une commune absorbée sous un autre code (Véronne dans Saillans)
  // n'apparaissait nulle part. Tranché le 02/11/2026 : l'ancienne commune au
  // même code reste dans la série, marquée ; chaque absorbée est montrée À PART,
  // jamais additionnée. Le snapshot écrit `avant_fusion.json` et marque les
  // lignes (`avant_fusion` : nom de l'ancienne commune).
  import { COMMUNE } from '#lib/instance.js'
  import { euros } from '#lib/data.js'

  export let af = {}          // avant_fusion.json
  export let lignes = []      // les lignes de la page (exercices ou marchés)
  export let quoi = 'comptes' // 'comptes' | 'marches'
  export let serie = false    // montrer les comptes OFGL des absorbées

  $: predecesseurs = af?.predecesseurs || []
  $: memeCode = predecesseurs.filter((p) => p.sorte === 'meme_code')
  $: absorbees = predecesseurs.filter((p) => p.sorte === 'absorbee')
  $: annees = [...new Set(lignes.filter((l) => l.avant_fusion && l.year)
    .map((l) => l.year))].sort((a, b) => a - b)
  $: nbMarches = lignes.filter((l) => l.avant_fusion).length
  $: noms = (liste) => liste.map((p) => `${p.nom} (SIREN ${p.siren})`).join(', ')
  const plage = (a) => a.length === 1 ? `L'exercice ${a[0]} est celui`
    : `Les exercices ${a[0]} à ${a[a.length - 1]} sont ceux`

  // Les comptes des absorbées : quelques agrégats, une colonne par année.
  const AGREGATS = ['Recettes de fonctionnement', 'Dépenses de fonctionnement',
                    'Epargne brute', 'Encours de dette']
  $: ofgl = af?.absorbees?.ofgl || []
  $: tables = absorbees.map((p) => {
    const lignesP = ofgl.filter((r) => r.siren === p.siren)
    const ans = [...new Set(lignesP.map((r) => r.year))].sort((a, b) => a - b)
    const cel = (ag, y) => lignesP.find((r) => r.agregat === ag && r.year === y)
    return { ...p, ans, cel, pop: (y) => lignesP.find((r) => r.year === y)?.population }
  }).filter((t) => t.ans.length)
  // Une absorbée sans aucune ligne : la base de l'OFGL commence à l'exercice
  // 2018, et Vallouise, absorbée en 2017, n'y figure pas (dossier 05101 du
  // 02/11/2026). L'encadré annonçait « donnés à part » sur un tableau absent.
  $: sansComptes = absorbees.filter((p) => !tables.some((t) => t.siren === p.siren))
</script>

{#if predecesseurs.length}
  <aside class="avant" id="avant-fusion" aria-label="Avant la fusion">
    <p><b>Commune nouvelle.</b>
      {#if quoi === 'comptes' && annees.length && memeCode.length}{' '}
        {plage(annees)} de l'ancienne commune de {noms(memeCode)}, avant la fusion :
        ils sont marqués « avant fusion ».
      {' '}{:else if quoi === 'marches' && nbMarches}{' '}
        {nbMarches} marché{nbMarches > 1 ? 's' : ''} {nbMarches > 1 ? 'ont été passés' : 'a été passé'}
        par une ancienne commune ({noms(predecesseurs)}) et {nbMarches > 1 ? 'portent' : 'porte'}
        la mention « avant fusion ».
      {' '}{/if}
      {#if absorbees.length}{' '}
        {absorbees.length > 1 ? 'Les communes absorbées' : 'La commune absorbée'}, {noms(absorbees)},
        {absorbees.length > 1 ? 'avaient leurs' : 'avait ses'} propres comptes :
        {#if quoi === 'marches'}{' '}
          {absorbees.length > 1 ? 'leurs' : 'ses'} marchés sont rangés hors de la commune, jamais comptés avec ceux de {COMMUNE}.
        {' '}{:else}{' '}
          ils sont donnés à part, jamais ajoutés à ceux de {COMMUNE}.
        {' '}{/if}
      {/if}
    </p>
    {#if serie}
      {#if tables.length}
        <!-- Tranché le 02/11/2026 : dire la limite plutôt que de laisser croire
             à un budget principal seul, ou à un détail que l'on n'a pas. -->
        <p class="limite">Ces comptes sont ceux de l'OFGL : budget principal et
          budgets annexes additionnés par la source. Le détail budget par budget
          n'est pas relevé pour {absorbees.length > 1 ? 'une commune absorbée' : 'la commune absorbée'}.</p>
      {/if}
      {#if sansComptes.length}
        <p class="limite">La base de l'OFGL ne publie aucun compte pour
          {noms(sansComptes)} : elle commence à l'exercice 2018.</p>
      {/if}
      {#each tables as t}
        <div class="table-wrap">
          <table>
            <caption>{t.nom}, commune absorbée (SIREN {t.siren}), budget principal et annexes, source OFGL</caption>
            <thead><tr><th scope="col">Agrégat</th>{#each t.ans as y}<th scope="col" class="r">{y}</th>{/each}</tr></thead>
            <tbody>
              {#each AGREGATS as ag}
                <tr><th scope="row">{ag.replace('Epargne', 'Épargne')}</th>
                  {#each t.ans as y}<td class="r">{t.cel(ag, y) ? euros(t.cel(ag, y).montant) : '·'}</td>{/each}</tr>
              {/each}
              <tr><th scope="row">Population</th>{#each t.ans as y}<td class="r">{t.pop(y) ?? '·'}</td>{/each}</tr>
            </tbody>
          </table>
        </div>
      {/each}
    {/if}
  </aside>
{/if}

<style>
  .avant { border-left: 3px solid var(--ardoise); padding: .5rem .9rem; margin: 1rem 0;
           background: var(--ardoise-pale); border-radius: var(--rayon); }
  .avant p { margin: 0 0 .5rem; }
  .limite { font-size: .85rem; color: var(--gris); }
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; font-size: .85rem; margin: .5rem 0; }
  caption { text-align: left; color: var(--gris); padding-bottom: .3rem; }
  th, td { padding: .2rem .5rem; border-bottom: 1px solid var(--trait-pale); }
  th[scope="row"] { text-align: left; font-weight: 500; }
  .r { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
