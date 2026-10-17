<script>
  import { COMMUNE_A, SITE_NOM } from '#lib/instance.js'
  import { euros } from '#lib/data.js'
  import Niveau from '#lib/components/Niveau.svelte'

  // Données rendues au build par +page.server.js. DVF n'arrive qu'en agrégats :
  // plus de carte ni de tableau vente par vente — une parcelle et une date
  // suffisaient à retrouver le vendeur (cf. scripts/snapshot/argent.py).
  export let data
  $: dvf = data.dvf
  $: syn = dvf.synthese
  $: constat = data.constat
  $: urba = data.urbanisme || {}
  $: statutPlu = urba.statut
  $: documents = urba.documents || []
  $: zonage = urba.zonage || []
  const QUOI = {
    PLU: "plan local d'urbanisme", PLUi: "plan local d'urbanisme intercommunal",
    CC: 'carte communale', POS: "plan d'occupation des sols",
    PSMV: 'plan de sauvegarde et de mise en valeur',
  }

  $: periode = syn.debut ? `${syn.debut}–${syn.fin}` : '-'

  // Marché immobilier par an : prix médian €/m² du bâti, et volume de mutations.
  //
  // La HAUTEUR porte le prix, pas le nombre : c'est le prix qu'on vient lire sur
  // un graphique intitulé « le marché immobilier ». L'échelle part de zéro —
  // tronquée, elle ferait passer 5 % d'écart pour un doublement.
  //
  // `ventes_bati` est compté à part parce que la médiane ne repose que sur lui :
  // une commune peut enregistrer trente mutations dans l'année dont deux
  // maisons. Sous `dvf.seuil` ventes, le snapshot ne publie pas la médiane — sur
  // deux ventes elle désignerait un vendeur, et annoncerait une flambée de
  // 764 % (le cas réel de Montselgues en 2022).
  $: dvfByYear = dvf.parAnnee
  $: dvfM2Max = Math.max(...dvfByYear.map(d => d.mediane_m2 || 0), 1)
  // Au-dessus, elle cesse d'être du bruit d'échantillon. Le paragraphe « ce que
  // ces chiffres ne disent pas » était écrit pour une commune à vingt ventes
  // par an : il affirmait « aucune tendance mesurable » à une ville qui en
  // enregistre quatre cents, où c'est faux.
  const ECHANTILLON_SOLIDE = 30

  $: byNature = dvf.parNature
  $: natMax = Math.max(...byNature.map(n => n.mutations), 1)

  function eurosC(n) {
    if (n == null) return '-'
    const a = Math.abs(n)
    if (a >= 1e6) return (n / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 2 }) + ' M€'
    if (a >= 1e3) return Math.round(n / 1e3).toLocaleString('fr-FR') + ' k€'
    return euros(n)
  }
</script>

<svelte:head><title>Urbanisme &amp; foncier - {SITE_NOM}</title>
  <meta name="description" content="Transactions foncières (DVF) et marchés publics de travaux {COMMUNE_A}." /></svelte:head>

<section>
  <h1>Urbanisme &amp; transactions foncières</h1>
  <p class="sub">Mutations immobilières (DVF, Cerema) recensées sur la commune. Les <a href="/marches">marchés publics</a> ont leur page dédiée.</p>

  {#if statutPlu}
    <h2>Ce qui règle la construction</h2>
    {#if statutPlu.rnu}
      <Niveau type="fait" source="Géoportail de l'urbanisme"
              href="https://www.geoportail-urbanisme.gouv.fr/">
        La commune n'a <b>aucun document d'urbanisme local</b> : c'est le règlement
        national d'urbanisme (RNU) qui s'y applique. Les autorisations se
        décident au regard des règles nationales, et la constructibilité y est
        beaucoup plus restreinte qu'avec un plan local.
      </Niveau>
    {:else if documents.length}
      {#each documents as doc}
        <Niveau type="fait" source="Géoportail de l'urbanisme"
                href="https://www.geoportail-urbanisme.gouv.fr/">
          {QUOI[doc.du_type] || doc.du_type}
          {#if doc.portee === 'intercommunal'}porté par l'intercommunalité
            {#if doc.titre}(<i>{doc.titre}</i>){/if}{/if},
          {#if doc.date_appro}approuvé le <b>{new Date(doc.date_appro).toLocaleDateString('fr-FR')}</b>{:else}sans date d'approbation lisible{/if},
          déposé au Géoportail de l'urbanisme, c'est ce dépôt qui le rend opposable.
        </Niveau>
      {/each}
    {:else}
      <Niveau type="fait" source="Géoportail de l'urbanisme"
              href="https://www.geoportail-urbanisme.gouv.fr/">
        <b>Aucun document d'urbanisme n'est déposé au Géoportail</b>, et la commune
        n'est pas pour autant au RNU. Depuis 2020, un plan qui n'y est pas publié
        n'est pas opposable : la question mérite d'être posée à la mairie.
      </Niveau>
    {/if}

    {#if zonage.length}
      <p class="hint">Part du territoire communal couverte par chaque type de zone,
        mesurée sur {statutPlu.aire_km2?.toLocaleString('fr-FR')} km².</p>
      <div class="tiles">
        {#each zonage as z}
          <div class="tile">
            <span class="tlabel">{z.famille || z.typezone} ({z.typezone})</span>
            <span class="tval">{z.part_pct.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %</span>
            <span class="tsub">{(z.aire_m2 / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} km² · {z.zones} zone(s)</span>
          </div>
        {/each}
      </div>
    {:else if !statutPlu.rnu && documents.length}
      <p class="muted">La répartition du territoire par type de zone n'est pas
        publiée ici : le document déposé ne couvre qu'une partie de la commune
        {#if urba.couverture != null}({Math.round(urba.couverture * 100)} % du territoire){/if} :
        le plus souvent parce qu'il est antérieur à une fusion de communes. Une
        part calculée sur ce seul morceau ne dirait pas ce qu'elle prétend.</p>
    {/if}
  {/if}

  {#if !syn.mutations}<p class="muted">Aucune mutation dans ce jeu de données.</p>{/if}

  {#if constat}
    <Niveau type="calcul" base="les ventes de maisons et d'appartements enregistrées dans DVF">
      Sur {constat.annees[0].annee}–{constat.annees[constat.annees.length - 1].annee},
      <b>{constat.ventes}</b> ventes de bâti sont enregistrées, soit
      {constat.minEchantillon} à {constat.maxEchantillon} par an. Le prix médian
      annuel oscille entre <b>{constat.basse.toLocaleString('fr-FR')}</b> et
      <b>{constat.haute.toLocaleString('fr-FR')} €/m²</b>.
    </Niveau>
    <p class="lecture">
      <b>Ce que ces chiffres ne disent pas.</b>
      {#if constat.maxEchantillon < ECHANTILLON_SOLIDE}{' '}
        Cet écart n'est pas un mouvement du marché&nbsp;: avec
        {constat.minEchantillon} à {constat.maxEchantillon} ventes par an, une
        seule maison atypique déplace la médiane de plusieurs centaines d'euros
        au mètre carré. Il n'y a pas de tendance mesurable à lire ici, et la
        courbe ci-dessous ne doit pas être interprétée comme telle.
      {' '}{:else}{' '}
        Avec {constat.minEchantillon} à {constat.maxEchantillon} ventes par an,
        la médiane annuelle est stable et l'écart ci-dessus se lit&nbsp;: une
        vente atypique ne la déplace plus. Elle ne dit rien, en revanche, de la
        composition du marché : une année où il se vend surtout de petits
        appartements de centre-ville rend un prix au m² plus élevé sans qu'aucun
        bien n'ait renchéri.
      {' '}{/if}
      DVF enregistre par ailleurs des transactions sans leur contexte&nbsp;: une
      vente entre proches, un bien à rénover et une maison restaurée y figurent
      au même titre.
    </p>
  {/if}
  {#if syn.mutations}
    <div class="tiles">
      <div class="tile">
        <span class="tlabel">Mutations recensées</span>
        <span class="tval">{syn.mutations.toLocaleString('fr-FR')}</span>
        <span class="tsub">{periode}</span>
      </div>
      <div class="tile">
        <span class="tlabel">Ventes de maisons</span>
        <span class="tval">{syn.maisons}</span>
        <span class="tsub">{syn.mediane_prix_maison ? `prix médian ${eurosC(syn.mediane_prix_maison)}` : `trop peu pour une médiane`}</span>
      </div>
      <div class="tile">
        <span class="tlabel">Prix médian bâti</span>
        <span class="tval">{syn.mediane_m2_bati ? syn.mediane_m2_bati.toLocaleString('fr-FR') + ' €/m²' : '-'}</span>
        <span class="tsub">{syn.mediane_m2_bati ? 'maisons & appartements' : `moins de ${dvf.seuil} ventes`}</span>
      </div>
      <div class="tile">
        <span class="tlabel">Ventes d'appartements</span>
        <span class="tval">{syn.appartements}</span>
        <span class="tsub">comptées dans le prix médian bâti</span>
      </div>
    </div>
    <p class="hint composition">
      Une mutation est une ligne DVF&nbsp;: un bien vendu, pas un acte. Les
      {syn.mutations.toLocaleString('fr-FR')} de {periode}&nbsp;: {syn.maisons} maisons,
      {syn.appartements} appartements{#if syn.dependances}, {syn.dependances} dépendances
      (garages, remises){/if}{#if syn.locaux}, {syn.locaux} locaux d'activité{/if}{#if syn.non_bati > 0}{' '}et
      {syn.non_bati} parcelles non bâties (terrains, bois, jardins, sols agricoles){/if}.
      Seules les maisons et les appartements entrent dans les prix médians.
    </p>
  {/if}

  {#if dvfByYear.length > 1}
    <h2>Le marché immobilier, année par année</h2>
    <p class="hint">Prix médian au m² du bâti (maisons &amp; appartements) et nombre de mutations enregistrées, sur {periode}.</p>
    <div class="scrollx">
      <div class="yrbars">
        {#each dvfByYear as d}
          <div class="ycol" title="{d.annee} : {d.mediane_m2 ? d.mediane_m2 + ' €/m² médian sur ' + d.ventes_bati + ' ventes de bâti' : d.ventes_bati ? `moins de ${dvf.seuil} ventes de bâti, médiane non publiée` : 'aucune vente de bâti chiffrée'} · {d.mutations} mutation(s) au total">
            <span class="ym2">{d.mediane_m2 ? d.mediane_m2.toLocaleString('fr-FR') + ' €/m²' : '-'}</span>
            <span class="ytrack"><span class="ybar"
                  style="height:{d.mediane_m2 ? Math.max(2, 100 * d.mediane_m2 / dvfM2Max) : 0}%"></span></span>
            <span class="yn" class:fragile={!d.mediane_m2}>{d.mutations}</span>
            <span class="yyear">{d.annee}</span>
          </div>
        {/each}
      </div>
    </div>
    <p class="ylegend"><span class="sw"></span> hauteur et valeur du haut = prix médian €/m² du bâti, échelle depuis zéro ·
      nombre du bas = mutations enregistrées dans l'année
      {#if dvfByYear.some(d => d.ventes_bati > 0 && !d.mediane_m2)}{' '}
        · « - » : moins de {dvf.seuil} ventes de bâti dans l'année, la médiane n'est pas publiée : elle désignerait une vente ou deux, pas un marché
      {' '}{/if}
    </p>
  {/if}

  {#if byNature.length}
    <h2>Répartition par nature de bien</h2>
    <ul class="bars">
      {#each byNature as n}
        <li>
          <span class="blabel">{n.nature}</span>
          <span class="btrack"><span class="bfill" style="width:{100 * n.mutations / natMax}%"></span></span>
          <span class="bval">{n.mutations}</span>
        </li>
      {/each}
    </ul>
    <p class="muted">Le site ne publie aucune vente à l'unité : une parcelle et une
      date suffisent, dans une petite commune, à retrouver le vendeur. Le détail
      vente par vente est consultable sur
      <a href="https://explore.data.gouv.fr/immobilier" target="_blank" rel="noopener">DVF sur data.gouv.fr<span class="visually-hidden"> (nouvelle fenêtre)</span></a>.</p>
  {/if}

  <p class="pointer">🏗 Les <a href="/marches">marchés publics</a> (travaux, études, fournitures) ont désormais leur propre page dédiée.</p>
</section>

<style>
  .lecture {
    margin: .5rem 0 1.5rem; padding: .7rem .9rem;
    border-left: 3px solid var(--ambre); background: var(--ambre-pale);
    border-radius: 0 var(--rayon) var(--rayon) 0;
    font-size: .9rem; line-height: 1.55; color: var(--gris);
  }
  .lecture b { color: var(--encre); }

  section { max-width: 1050px; margin: 0 auto; padding: 1.5rem; color: var(--encre); }
  h1 { margin: 0 0 .25rem; } h2 { color: var(--encre); margin: 1.5rem 0 .5rem; font-size: 1.1rem; }
  .sub { color: var(--gris); margin: 0 0 1rem; }
  .hint { color: var(--gris); font-size: .85rem; margin: 0 0 .75rem; }
  .composition { margin-top: -.5rem; max-width: 74ch; }

  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-bottom: 1.25rem; }
  .tile { background: #fff; border: 1px solid var(--trait); border-top: 3px solid #f97316; border-radius: 8px; padding: .8rem .9rem; display: flex; flex-direction: column; gap: .15rem; }
  .tlabel { font-size: .72rem; color: var(--gris); font-weight: 600; text-transform: uppercase; letter-spacing: .02em; }
  .tval { font-size: 1.35rem; font-weight: 700; color: var(--encre); }
  .tsub { font-size: .78rem; color: var(--gris-clair); }


  .bars { list-style: none; padding: 0; margin: 0 0 .5rem; display: flex; flex-direction: column; gap: .4rem; }
  .bars li { display: grid; grid-template-columns: minmax(120px, 1fr) 2fr 40px; align-items: center; gap: .6rem; }
  .blabel { font-size: .82rem; color: var(--gris); }
  .btrack { background: var(--trait-pale); border-radius: 4px; height: 13px; overflow: hidden; }
  .bfill { display: block; height: 100%; background: var(--ambre); border-radius: 4px; }
  .bval { text-align: right; font-size: .8rem; font-weight: 700; font-variant-numeric: tabular-nums; }

  .scrollx { overflow-x: auto; }
  .yrbars { display: flex; align-items: flex-end; gap: .55rem; height: 170px; padding: .5rem 0; border-bottom: 1px solid var(--trait); min-width: 420px; }
  /* En grille, pas en flex-column : une barre en pourcentage placée à côté de
     trois libellés est un élément flexible comme eux, donc `flex-shrink` la
     rabote — les cinq barres finissaient TOUTES à 94 px, quelle que soit leur
     valeur. Le pourcentage ne veut dire quelque chose que dans une piste qui
     lui est réservée. */
  .ycol { flex: 1; min-width: 34px; display: grid; grid-template-rows: auto 1fr auto auto;
          justify-items: center; height: 100%; gap: .15rem; }
  .ytrack { display: flex; align-items: flex-end; width: 100%; height: 100%; justify-content: center; }
  .ym2 { font-size: .66rem; color: var(--ambre); font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .ybar { width: 62%; max-width: 38px; background: var(--ambre); border-radius: 4px 4px 0 0; min-height: 2px; }
  .yn { font-size: .72rem; color: var(--gris); font-weight: 700; font-variant-numeric: tabular-nums; }
  .yyear { font-size: .72rem; color: var(--gris); }
  .ylegend { color: var(--gris-clair); font-size: .76rem; margin: .5rem 0 0; display: flex; align-items: center; gap: .4rem; }
  .ylegend .sw { width: 11px; height: 11px; border-radius: 3px; background: var(--ambre); display: inline-block; }
  .yn.fragile { color: var(--gris-clair); }
  .pointer { margin-top: 1.5rem; padding: .7rem .9rem; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; font-size: .9rem; color: #9a3412; }
  .pointer a { color: #c2410c; font-weight: 600; }
  .muted { color: var(--gris-clair); font-size: .85rem; }
  @media (max-width: 720px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
</style>
