<script>
  import { COMMUNE, COMMUNE_DE, INSEE, SITE_NOM, NOMS_BUDGETS } from '#lib/instance.js'
  import Niveau from '#lib/components/Niveau.svelte'
  import SourceAbsente from '#lib/components/SourceAbsente.svelte'
  import { euros } from '#lib/data.js'
  import Perimetre from './Perimetre.svelte'
  import Repartition from './Repartition.svelte'

  // Rendu au build par +page.server.js.
  export let data
  $: annuel = data.budget.annuel || []
  $: annexe = data.budget.annexe || []
  $: ofgl = data.ofgl.ofgl || []
  $: vote = data.budgetVote.budget_vote || []
  $: sourceVote = data.sourceVote || { etat: 'servie', sources: [] }
  // Exercice par défaut = année OFGL la plus récente (réalisé consolidé).
  // Calculé au premier rendu seulement : ensuite `year` suit le sélecteur.
  let year = null
  $: if (year === null && (ofgl.length || annuel.length)) {
    const ys = [...new Set(ofgl.map((r) => r.year))].sort((a, b) => b - a)
    year = ys[0] ?? [...new Set(annuel.map((r) => r.year))].sort((a, b) => b - a)[0] ?? null
  }

  // Années « réalisées » (OFGL, repli DGFiP) et années « budget voté » (CR, hors OFGL).
  $: ofglYearsSet = new Set(ofgl.map(r => r.year))
  $: voteYears = [...new Set(vote.map(r => r.year))]
  $: realizedYears = ofgl.length
    ? [...new Set(ofgl.map(r => r.year))]
    : [...new Set(annuel.map(r => r.year))]
  $: years = [...new Set([...realizedYears, ...voteYears])].sort((a, b) => b - a)
  $: isVoteOnly = (y) => voteYears.includes(y) && !ofglYearsSet.has(y)
  $: isVoteYear = isVoteOnly(year)
  // Données du budget voté pour l'exercice choisi.
  $: voteRows = vote.filter(r => r.year === year)
  $: votePrincipal = voteRows.filter(r => r.scope === 'principal')
  $: voteAnnexes = voteRows.filter(r => r.scope !== 'principal')
  // Une carte par budget annexe. Le collecteur verse plusieurs agrégats par
  // budget : les lister sous le seul nom du budget donnait « aj 181 k€ / aj
  // 105 k€ / aj 181 k€ », lu comme des doublons. Une section votée est
  // équilibrée : recettes = dépenses s'écrit alors une fois.
  const SECTIONS_VOTE = [
    { titre: 'Fonctionnement', rec: 'Recettes de fonctionnement', dep: 'Dépenses de fonctionnement' },
    { titre: 'Investissement', rec: "Recettes d'investissement", dep: "Dépenses d'investissement" },
  ]
  const DONT_VOTE = [
    { agregat: 'Charges de personnel', titre: 'dont personnel' },
    { agregat: 'Charges à caractère général', titre: 'dont charges générales' },
  ]
  $: voteCartes = [...new Set(voteAnnexes.map(r => r.scope))].map(scope => {
    const rows = voteAnnexes.filter(r => r.scope === scope)
    const de = (agregat) => rows.find(r => r.agregat === agregat) || null
    const lues = new Set()
    const lire = (agregat) => { const r = de(agregat); if (r) lues.add(r); return r }
    const sections = SECTIONS_VOTE.map(s => ({ titre: s.titre, rec: lire(s.rec), dep: lire(s.dep) }))
      .filter(s => s.rec || s.dep)
    const dont = DONT_VOTE.map(d => ({ titre: d.titre, r: lire(d.agregat) })).filter(d => d.r)
    return {
      nom: nomBudget(scope),
      sections,
      dont,
      autres: rows.filter(r => !lues.has(r)),
      notes: [...new Set(rows.map(r => r.note).filter(Boolean))],
      total: sections.find(s => s.titre === 'Fonctionnement')?.dep?.value ?? 0,
    }
  }).sort((a, b) => b.total - a.total)
  const equilibre = (s) => s.rec && s.dep && s.rec.value === s.dep.value
  // Le « solde d'exécution » d'un tableau voté inclut le résultat reporté et le
  // virement entre sections : ce n'est pas l'excédent de l'année.
  const LIBELLES_VOTE = {
    'Excédent net de fonctionnement': 'Solde de fonctionnement, reports compris',
    "Solde d'exécution d'investissement": "Solde d'investissement, reports compris",
  }

  // Un même budget s'écrit « aj » dans un PV et « CENTRE DE LOISIRS LASALLE »
  // dans la balance DGFiP : `noms_budgets` (instance.json) donne le nom public
  // de l'un et de l'autre. Sans entrée : le nom tel qu'écrit, sans le nom de la
  // commune et sans capitales — les sigles courts (« CG ») gardés.
  const MOTS_COURTS = new Set(['et', 'de', 'du', 'des', 'la', 'le', 'les', 'en'])
  function nomBudget(brut) {
    const cle = (brut || '').toLowerCase().trim()
    if (NOMS_BUDGETS?.[cle]) return NOMS_BUDGETS[cle]
    const sansCommune = cle.endsWith(' ' + COMMUNE.toLowerCase()) ? brut.trim().slice(0, -COMMUNE.length - 1) : brut.trim()
    const mots = sansCommune.split(/\s+/).map(m => (m.length <= 2 && !MOTS_COURTS.has(m.toLowerCase()) && m === m.toUpperCase()) ? m : m.toLowerCase())
    const s = mots.join(' ')
    return s.charAt(0).toUpperCase() + s.slice(1)
  }
  $: voteSource = voteRows[0]?.source ?? ''
  $: voteSourceUrl = voteRows[0]?.source_url ?? ''
  $: voteRec = votePrincipal.find(r => r.agregat === 'Recettes de fonctionnement')?.value ?? null
  $: voteDep = votePrincipal.find(r => r.agregat === 'Dépenses de fonctionnement')?.value ?? null
  $: voteEquilScale = Math.max(voteRec || 0, voteDep || 0, 1)
  function fmtVote(r) {
    if (r.value == null) return '-'
    if (r.unit === 'pct') return r.value.toLocaleString('fr-FR') + ' %'
    if (r.unit === 'annees') return r.value.toLocaleString('fr-FR') + ' ans'
    return (r.approx ? '≈ ' : '') + eurosC(r.value)
  }

  // Années où le détail par compte DGFiP existe (colonnes "Où va / D'où vient l'argent").
  $: dgfipYears = new Set(annuel.filter(r => r.categorie === 'depenses_fonctionnement').map(r => r.year))
  $: hasDetail = dgfipYears.has(year)
  $: lastDetailYear = Math.max(...dgfipYears, 0) || null

  // ─── OFGL : agrégats propres + €/habitant (source des chiffres-clés) ───────────
  $: ofglYearRows = ofgl.filter(r => r.year === year)
  $: population = ofglYearRows[0]?.population ?? null
  const ofglOf = (rows, name) => rows.find(r => r.agregat === name) || null
  $: gV = (name) => { const r = ofglOf(ofglYearRows, name); return r ? r.montant : null }
  $: gH = (name) => { const r = ofglOf(ofglYearRows, name); return r ? r.euros_par_habitant : null }

  // ─── DGFiP : détail par compte (où va / d'où vient l'argent) ───────────────────
  //
  // `$:` et non `const` : une fonction déclarée en `const` qui lit `annuel` et
  // `year` ne crée aucune dépendance réactive. `$: depRows = lines(…)` ne
  // mentionne ni l'un ni l'autre — Svelte ne réexécutait donc jamais ce calcul
  // quand on changeait d'exercice, et pire, l'ordonnait à l'hydratation AVANT
  // `$: annuel = …` : `annuel.filter` sur `undefined` cassait l'hydratation de
  // toute la page, ce qui figeait AUSSI le sélecteur. Même piège que dans
  // territoire/+page.svelte.
  $: lines = (cat) => annuel
    .filter(r => r.year === year && r.categorie === cat && (r.montant || 0) > 0)
    .sort((a, b) => b.montant - a.montant)
  $: depRows = lines('depenses_fonctionnement')
  $: recRows = lines('recettes_fonctionnement')
  $: depTotalDG = depRows.reduce((s, r) => s + r.montant, 0)
  $: recTotalDG = recRows.reduce((s, r) => s + r.montant, 0)
  $: depMax = Math.max(...depRows.map(r => r.montant), 1)
  $: recMax = Math.max(...recRows.map(r => r.montant), 1)

  // ─── Chiffres-clés (OFGL en priorité, DGFiP en repli) ──────────────────────────
  $: recF = gV('Recettes de fonctionnement') ?? recTotalDG
  $: depF = gV('Dépenses de fonctionnement') ?? depTotalDG
  $: epargne = gV('Epargne brute') ?? ((recF != null && depF != null) ? recF - depF : null)
  $: epargneNette = gV('Epargne nette')
  // Suffixe posé par collectors/ofgl.py sur les agrégats additionnés des budgets
  // annexes : même chaîne des deux côtés, sinon la page ne les retrouve plus.
  const SUFFIXE_ANNEXES = ' - budgets annexes'
  // Des annexes sur au moins un exercice (colonnes du tableau) ; et sur CET
  // exercice (chiffres-clés). OFGL publie parfois le principal avant les
  // annexes : lire l'absence comme « zéro annexe » ferait passer un exercice
  // incomplet pour un désendettement.
  $: aDesAnnexes = ofgl.some(r => r.agregat.endsWith(SUFFIXE_ANNEXES))
  $: detteAnnexes = aDesAnnexes ? gV('Encours de dette' + SUFFIXE_ANNEXES) : null
  $: detteAnnexesH = aDesAnnexes ? gH('Encours de dette' + SUFFIXE_ANNEXES) : null
  $: ofglAnnexes = detteAnnexes != null && detteAnnexesH != null
  $: annexesAbsentes = aDesAnnexes && !ofglAnnexes
  $: dettePrincipal = gV('Encours de dette')
  // La dette de la commune, c'est celle de tous ses budgets : le budget principal
  // seul peut l'amputer d'un emprunt porté par une annexe, ou la gonfler de celui
  // qu'une annexe lui a transmis. Sans annexe connue, c'est le principal.
  $: dette = dettePrincipal == null ? null : dettePrincipal + (ofglAnnexes ? detteAnnexes : 0)
  // Un exercice sans annexes publiées garde le principal seul, et la page le dit.
  $: detteH = gH('Encours de dette') == null ? null : gH('Encours de dette') + (ofglAnnexes ? detteAnnexesH : 0)
  $: epargneAnnexes = ofglAnnexes ? gV('Epargne brute' + SUFFIXE_ANNEXES) : null
  $: epargneTous = (gV('Epargne brute') != null && epargneAnnexes != null) ? gV('Epargne brute') + epargneAnnexes : null
  // Années de recettes d'épargne nécessaires pour tout rembourser : n'a de sens
  // que sur une épargne positive.
  $: anneesDesendettement = (dette != null && epargneTous != null && epargneTous > 0) ? dette / epargneTous : null
  $: annuite = gV('Annuité de la dette')
  $: equipement = gV("Dépenses d'équipement")
  $: recFH = gH('Recettes de fonctionnement')
  $: depFH = gH('Dépenses de fonctionnement')
  $: epargneH = gH('Epargne brute')
  $: equilScale = Math.max(recF || 0, depF || 0, 1)
  $: epargnePct = (recF && epargne != null) ? Math.round(100 * epargne / recF) : null

  // ─── Évolution de l'épargne brute (toutes années OFGL) ─────────────────────────
  $: ofglYears = [...new Set(ofgl.map(r => r.year))].sort((a, b) => a - b)
  $: epargneSerie = ofglYears
    .map(y => { const r = ofgl.find(x => x.year === y && x.agregat === 'Epargne brute'); return { year: y, val: r ? r.montant : null } })
    .filter(d => d.val != null)
  $: epSerieMax = Math.max(...epargneSerie.map(d => Math.abs(d.val)), 1)

  // ─── Agrégats OFGL : vue annualisée, groupée par thème (pivot année) ───────────
  const OFGL_GROUPS = [
    { title: 'Fonctionnement', items: ['Recettes de fonctionnement', 'Dépenses de fonctionnement', 'Frais de personnel', 'Achats et charges externes'] },
    { title: 'Épargne',        items: ['Epargne brute', 'Epargne nette', 'Epargne de gestion'] },
    { title: 'Investissement', items: ["Dépenses d'équipement", "Recettes d'investissement", 'FCTVA'] },
    { title: 'Dette',          items: ['Encours de dette', 'Annuité de la dette', 'Charges financières'] },
    { title: 'Fiscalité & dotations', items: ['Impôts locaux', 'Impôts et taxes', "Concours de l'Etat", 'Dotation globale de fonctionnement'] },
  ]
  let showPerHab = false
  $: ofglIndex = (() => { const m = new Map(); for (const r of ofgl) m.set(r.agregat + '|' + r.year, r); return m })()
  $: ofglCell = (name, y) => ofglIndex.get(name + '|' + y) || null
  // ─── Périmètre : budget principal, budgets annexes, tous budgets ──────────────
  // Une ligne par agrégat, et le périmètre choisi une fois pour les tuiles et le
  // tableau : deux lignes « — budget principal » / « — budgets annexes » par
  // agrégat doublaient le tableau et tronquaient les libellés.
  let perimetre = 'principal'
  $: if (!aDesAnnexes) perimetre = 'principal'
  // Ce qui s'additionne sans rien compter deux fois. La subvention d'équilibre
  // que le principal verse à une annexe est une dépense d'un côté, une recette
  // de l'autre : elle s'annule dans l'épargne, ne touche ni la dette ni le
  // personnel, mais gonflerait recettes et dépenses.
  const ADDITIONNABLES = new Set(['Epargne brute', 'Epargne nette', 'Epargne de gestion',
    'Encours de dette', 'Annuité de la dette', 'Charges financières', 'Frais de personnel', "Dépenses d'équipement"])
  const NON_ADD = { nonAdditionnable: true }
  // Comptes consolidés OFGL (collectors/ofgl.py) : le total « tous budgets »
  // dont les flux entre budgets sont retirés, et le montant de ces flux.
  const SUFFIXE_TOUS = ' - tous budgets'
  const SUFFIXE_FLUX = ' - flux entre budgets'
  $: aUneAnnexe = (name) => ofglYears.some(y => ofglCell(name + SUFFIXE_ANNEXES, y))
  $: aUnConsolide = (name) => ofglYears.some(y => ofglCell(name + SUFFIXE_TOUS, y))
  // { montant, euros_par_habitant, flux? }, null (pas de donnée) ou NON_ADD.
  $: valeur = (name, y, p) => {
    const pr = ofglCell(name, y), an = ofglCell(name + SUFFIXE_ANNEXES, y)
    if (p === 'principal') return pr
    if (p === 'annexes') return an
    if (!aUneAnnexe(name)) return pr
    if (aUnConsolide(name)) {
      const t = ofglCell(name + SUFFIXE_TOUS, y)
      return t ? { ...t, flux: ofglCell(name + SUFFIXE_FLUX, y)?.montant ?? 0 } : null
    }
    // Sans consolidé publié : la somme, seulement là où elle ne compte rien deux fois.
    if (!ADDITIONNABLES.has(name)) return NON_ADD
    if (!pr || !an) return null
    const h = (pr.euros_par_habitant == null || an.euros_par_habitant == null)
      ? null : pr.euros_par_habitant + an.euros_par_habitant
    return { montant: pr.montant + an.montant, euros_par_habitant: h }
  }
  const lisible = (v) => v && v !== NON_ADD
  // Un agrégat n'est affiché que s'il a au moins une valeur sur la période.
  $: ofglGroupsShown = OFGL_GROUPS
    .map(g => ({ title: g.title, items: g.items.filter(n => ofglYears.some(y => lisible(valeur(n, y, perimetre)))) }))
    .filter(g => g.items.length)
  $: nonAdditionnes = perimetre === 'tous'
    ? OFGL_GROUPS.flatMap(g => g.items).filter(n => aUneAnnexe(n) && !aUnConsolide(n) && !ADDITIONNABLES.has(n)) : []
  $: aDesConsolides = OFGL_GROUPS.some(g => g.items.some(n => aUnConsolide(n)))
  $: tousConsolide = perimetre === 'tous' && aDesConsolides
  // « Epargne », « l'Etat » : graphie de l'OFGL, gardée comme clé, pas à l'écran.
  const libelleAgregat = (n) => n.replace(/^Epargne/, 'Épargne').replace("l'Etat", "l'État")
  $: libellePerimetre = { principal: 'budget principal', annexes: 'budgets annexes', tous: 'tous budgets' }[perimetre]
  // Évolution sur un an, en clair, sur la colonne de l'exercice choisi. Pas de
  // pourcentage sur une base nulle ou négative : il ne voudrait rien dire.
  $: matriceEvol = ofglYears.includes(year) && ofglYears.includes(year - 1)
  $: evolLigne = (name) => {
    const a = valeur(name, year, perimetre), b = valeur(name, year - 1, perimetre)
    if (!lisible(a) || !lisible(b) || !(b.montant > 0) || a.montant == null) return null
    return Math.round(1000 * (a.montant - b.montant) / b.montant) / 10
  }
  // Sur téléphone, le tableau n'affiche que l'exercice et le précédent.
  let toutesAnnees = false
  const parHab = (v) => v == null ? '-' : Math.round(v).toLocaleString('fr-FR').replace('-', '−') + ' €'

  // Tuiles : le principal garde son repli DGFiP ; les autres périmètres lisent OFGL.
  $: tRec = perimetre === 'principal' ? { montant: recF, euros_par_habitant: recFH } : valeur('Recettes de fonctionnement', year, perimetre)
  $: tDep = perimetre === 'principal' ? { montant: depF, euros_par_habitant: depFH } : valeur('Dépenses de fonctionnement', year, perimetre)
  $: tEp  = perimetre === 'principal' ? { montant: epargne, euros_par_habitant: epargneH } : valeur('Epargne brute', year, perimetre)

  // ─── Budgets annexes : synthèse par structure et par année ─────────────────────
  const pickRow = (rows, re) => rows.find(r => re.test(r.libelle || ''))
  $: annexeByStruct = (() => {
    const m = new Map()
    for (const r of annexe) {
      const name = r.entity_name || 'Budget annexe'
      if (!m.has(name)) m.set(name, new Map())
      const yrs = m.get(name)
      if (!yrs.has(r.year)) yrs.set(r.year, [])
      yrs.get(r.year).push(r)
    }
    const structs = [...m.entries()].map(([name, yrs]) => {
      const years = [...yrs.values()].map(rows => {
        // Deux soldes coexistent : celui du fonctionnement et l'encours de dette
        // (section « dette »), que la table range aussi sous `sens = solde`.
        const solde = rows.find(r => r.sens === 'solde' && r.section !== 'dette')
        const dette = rows.find(r => r.sens === 'solde' && r.section === 'dette')
        const subv = pickRow(rows, /subvention.*(commune|exploitation)/i)
        return {
          year: rows[0].year,
          soldeVal: solde ? solde.montant : null,
          soldeLbl: solde ? (solde.libelle || '') : '',
          detteVal: dette ? dette.montant : null,
          cautions: dette?.cautions ?? null,
          subvComm: subv ? subv.montant : null,
          source: rows.find(r => r.source)?.source || '',
          entityId: rows.find(r => r.entity_id)?.entity_id ?? null,
          rows,
        }
      }).sort((a, b) => a.year - b.year)
      return {
        name,
        years,
        entityId: years.find(y => y.entityId)?.entityId ?? null,
        aDette: years.some(y => y.detteVal != null),
        aSubv: years.some(y => y.subvComm != null),
        dernier: years[years.length - 1].year,
        suite: annexe.find(r => r.entity_name === name && r.poursuivi_sous)?.poursuivi_sous ?? null,
      }
    }).map(s => {
      const d = [...s.years].reverse().find(y => y.detteVal != null)
      return { ...s, nom: nomBudget(s.name), detteFin: d ? d.detteVal : null, detteAn: d ? d.year : null }
    })
    // Par dette au dernier exercice, puis par nom ; un budget poursuivi sous un
    // autre nom se place juste avant sa suite : c'est le même service.
    const tries = structs.sort((a, b) => (b.detteFin ?? -1) - (a.detteFin ?? -1) || a.nom.localeCompare(b.nom))
    const suiteDe = new Map(tries.filter(s => s.suite).map(s => [s.suite, s]))
    const ordre = []
    for (const s of tries) {
      if (s.suite && tries.some(t => t.name === s.suite)) continue
      const pred = suiteDe.get(s.name)
      if (pred) ordre.push(pred)
      ordre.push({ ...s, precede: pred ? pred.name : null })
    }
    for (const s of tries) if (!ordre.some(o => o.name === s.name)) ordre.push(s)
    return ordre
  })()
  // Dernier exercice où l'un des budgets annexes apparaît : un budget qui
  // s'arrête avant se dit, sans en deviner la raison.
  $: derniereAnneeAnnexes = annexeByStruct.length ? Math.max(...annexeByStruct.map(s => s.dernier)) : null

  // ─── Dette par budget ───────────────────────────────────────────────────────────
  // La dette s'additionne sans double compte : c'est le seul « tous budgets »
  // qui se montre en total. Détail par annexe quand la balance DGFiP le donne
  // pour l'exercice, sinon les annexes en un bloc (OFGL).
  $: detteAnnexesDetail = annexeByStruct
    .map(s => { const y = s.years.find(y => y.year === year)
                return { nom: s.nom, href: s.entityId ? `/entite/${s.entityId}` : null,
                         montant: y?.detteVal ?? null, cautions: y?.cautions ?? 0 } })
    .filter(a => a.montant > 0)
    .sort((a, b) => b.montant - a.montant)
  $: detteCautions = detteAnnexesDetail.reduce((s, a) => s + (a.cautions || 0), 0)
  $: detteParts = dettePrincipal == null ? [] : [
    { nom: 'Budget principal', montant: dettePrincipal },
    ...(detteAnnexesDetail.length ? detteAnnexesDetail
        : (ofglAnnexes && detteAnnexes > 0 ? [{ nom: 'Budgets annexes', montant: detteAnnexes }] : [])),
  ]
  // Deux sources pour les annexes (balances DGFiP ici, agrégat OFGL dans la
  // tuile) : si elles divergent, d'un euro ou d'un million, la page le dit au
  // lieu de laisser deux totaux. Un seuil de 2 % taisait les 18 579 € de
  // cautions que l'OFGL compte et que le détail ne comptait pas.
  $: ecartDetteSources = (detteAnnexesDetail.length && ofglAnnexes && detteAnnexes)
    ? detteAnnexesDetail.reduce((s, a) => s + a.montant, 0) - detteAnnexes : 0

  // Budget voté : dépenses de fonctionnement par budget, sans total — la
  // subvention d'équilibre y serait comptée deux fois.
  $: voteParts = voteDep ? [
    { nom: 'Budget principal', montant: voteDep },
    ...voteCartes.map(c => ({ nom: c.nom, montant: c.sections.find(s => s.titre === 'Fonctionnement')?.dep?.value ?? 0 }))
      .filter(p => p.montant > 0),
  ] : []

  // ─── Sommaire : les sections que l'année affichée fait exister ─────────────────
  // La page est longue sur téléphone ; cinq ancres, dans l'ordre des sections.
  $: sommaire = [
    { id: 'essentiel', titre: "L'essentiel", ok: true },
    { id: 'recettes-depenses', titre: 'Recettes et dépenses',
      ok: isVoteYear ? Boolean((voteRec && voteDep) || voteParts.length > 1) : true },
    { id: 'dette', titre: 'La dette', ok: !isVoteYear && dette != null },
    { id: 'evolution', titre: "L'évolution", ok: epargneSerie.length > 1 || ofglGroupsShown.length > 0 },
    { id: 'annexes', titre: 'Les budgets annexes',
      ok: annexeByStruct.length > 0 || (isVoteYear && voteAnnexes.length > 0) },
  ].filter(s => year && s.ok)

  // ─── Formatage ─────────────────────────────────────────────────────────────────
  function eurosC(n) {
    if (n == null) return '-'
    const a = Math.abs(n)
    if (a >= 1e6) return (n / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 2 }) + ' M€'
    if (a >= 1e3) return Math.round(n / 1e3).toLocaleString('fr-FR') + ' k€'
    return euros(n)
  }
  const eurosS = (n) => eurosC(n).replace('-', '−')
  const perHab = (n) => n == null ? '-' : Math.round(n).toLocaleString('fr-FR') + ' €/hab.'

  // Un montant seul n'apprend rien : 1,83 M€, est-ce beaucoup ? Trois questions
  // rendent un chiffre lisible — combien, comparé à quoi, comment ça évolue.
  // Le €/hab répond à la deuxième, la série à la troisième.
  $: serieDe = (agregat) => {
    const m = new Map()
    for (const r of ofgl) if (r.agregat === agregat && r.montant != null) m.set(r.year, r.montant)
    return m
  }
  $: evolution = (agregat, depuis) => {
    const m = serieDe(agregat)
    const av = m.get(depuis), ap = m.get(year)
    if (av == null || ap == null || !av) return null
    // Une décimale : +0,4 % arrondi à l'entier s'affichait « Recettes 0 % »,
    // qui se lit comme une valeur absente plutôt qu'une quasi-stabilité.
    return Math.round(1000 * (ap - av) / av) / 10
  }
  $: anneesOfgl = [...new Set(ofgl.map((r) => r.year))].sort((a, b) => a - b)
  $: premiereAnnee = anneesOfgl[0] ?? null
  $: evolRec1 = evolution('Recettes de fonctionnement', year - 1)
  $: evolDep1 = evolution('Dépenses de fonctionnement', year - 1)
  // Série de la dette sur tous les budgets quand les annexes sont connues.
  $: serieDette = (() => {
    const p = serieDe('Encours de dette')
    if (!aDesAnnexes) return p
    const a = serieDe('Encours de dette' + SUFFIXE_ANNEXES)
    return new Map([...p].filter(([y]) => a.has(y)).map(([y, v]) => [y, v + a.get(y)]))
  })()
  $: evolDette = (depuis) => {
    const av = serieDette.get(depuis), ap = serieDette.get(year)
    return (av == null || ap == null || !av) ? null : Math.round(1000 * (ap - av) / av) / 10
  }
  $: evolDette1 = evolDette(year - 1)
  // Mouvement de l'exercice, budget par budget. Quand le principal et les annexes
  // vont en sens contraire et que le total bouge moins que le principal, une
  // part du mouvement est un transfert entre budgets, pas un emprunt ni un
  // remboursement : la page le dit, sans en nommer la cause qu'elle ignore.
  $: mouvementDette = (() => {
    if (!aDesAnnexes || !year) return null
    const p = serieDe('Encours de dette'), a = serieDe('Encours de dette' + SUFFIXE_ANNEXES)
    if (!p.has(year) || !p.has(year - 1) || !a.has(year) || !a.has(year - 1)) return null
    const dP = p.get(year) - p.get(year - 1), dA = a.get(year) - a.get(year - 1)
    const transfert = dP * dA < 0 && Math.abs(dP + dA) < Math.abs(dP)
    return { dP, dA, total: dP + dA, transfert }
  })()

  // Point bas de la dette sur la série : c'est lui qui donne l'échelle du
  // retournement, pas la première année observée.
  $: detteSerie = [...serieDette.entries()].sort((a, b) => a[0] - b[0])
  $: detteMin = detteSerie.length ? detteSerie.reduce((min, e) => (e[1] < min[1] ? e : min)) : null
  $: detteFacteur = (detteMin && dette && detteMin[1] && !annexesAbsentes) ? (dette / detteMin[1]) : null
  $: facteurDette = detteFacteur
    ? detteFacteur.toLocaleString('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
    : null

  const signe = (n) => (n == null ? '' : (n > 0 ? '+' : n < 0 ? '−' : '')
    + Math.abs(n).toLocaleString('fr-FR', { maximumFractionDigits: 1 }) + ' %')
  const pct = (n, total) => total ? Math.round(100 * n / total) + ' %' : ''
</script>

<svelte:head><title>Budget communal - {SITE_NOM}</title>
  <meta name="description" content="Budget de la commune {COMMUNE_DE} : recettes, dépenses, budgets annexes et comparaison avec les communes semblables (OFGL)." /></svelte:head>

<section>
  <header class="head">
    <div>
      <h1>Budget communal</h1>
      <p class="sub">Ce que la commune perçoit, dépense, épargne et doit, en un coup d'œil.</p>
    </div>
    {#if years.length}{' '}
      <label class="year">Année affichée
        <select bind:value={year}>{#each years as y}<option value={y}>{y}{isVoteOnly(y) ? ' · budget voté (prévision)' : ''}</option>{/each}</select>
      </label>
    {' '}{/if}
  </header>
  {#if sommaire.length > 1}
    <nav class="sommaire" aria-label="Sections de la page">
      {#each sommaire as s}<a href="#{s.id}">{s.titre}</a>{/each}
    </nav>
  {/if}
  {#if years.length}
    <p class="pilote">L'année choisie pilote la phrase{isVoteYear ? ' et les chiffres-clés' : ', les chiffres-clés et la dette par budget'}. Le tableau année par année et les budgets annexes montrent toutes les années.</p>
  {/if}
  {#if aDesAnnexes && (ofgl.length || annuel.length)}<Perimetre bind:value={perimetre} consolide={aDesConsolides} />{/if}

  {#if !ofgl.length && !annuel.length}<p class="muted">Aucune donnée budgétaire.</p>{/if}

  {#if sourceVote.etat === 'absente' && !vote.length}
    <SourceAbsente etat={sourceVote.etat} sources={sourceVote.sources}
                   dernier={sourceVote.dernier} quoi="Le budget primitif voté" />
  {/if}

  {#if year}
    {#if isVoteYear}
      <h2 id="essentiel">L'essentiel</h2>
      <!-- Budget primitif VOTÉ (prévisionnel) — source unique provisoire : CR du CM -->
      <div class="votebanner">
        <b>Budget primitif voté {year}</b> : chiffres <b>prévisionnels</b> adoptés par le conseil municipal,
        non encore consolidés par la DGFiP / OFGL. Source unique provisoire :
        {voteSource}{#if voteSourceUrl}{' '} · <a href={voteSourceUrl} target="_blank" rel="noopener">voir le CR<span aria-hidden="true"> ↗</span><span class="visually-hidden"> (nouvelle fenêtre)</span></a>{/if}.
      </div>

      <div class="tiles">
        {#each votePrincipal as r}
          <div class="tile vote">
            <span class="tlabel">{LIBELLES_VOTE[r.agregat] ?? r.agregat}</span>
            <span class="tval">{fmtVote(r)}</span>
            {#if r.note}<span class="tsub">{r.note}</span>{/if}
          </div>
        {/each}
      </div>

      <p class="hint">Ces montants proviennent exclusivement de la délibération budgétaire ; ils seront remplacés par le réalisé (comptes administratifs / OFGL) dès sa publication (~18 mois plus tard).</p>

      {#if (voteRec && voteDep) || voteParts.length > 1}
        <h2 id="recettes-depenses">Recettes et dépenses</h2>
      {#if voteRec && voteDep}
        <div class="equil">
          <div class="erow">
            <span class="ekey"><i class="sw in"></i> Recettes</span>
            <div class="etrack"><div class="efill in" style="width:{100 * voteRec / voteEquilScale}%"></div></div>
            <span class="eval">{eurosC(voteRec)}</span>
          </div>
          <div class="erow">
            <span class="ekey"><i class="sw out"></i> Dépenses</span>
            <div class="etrack"><div class="efill out" style="width:{100 * voteDep / voteEquilScale}%"></div></div>
            <span class="eval">{eurosC(voteDep)}</span>
          </div>
        </div>
      {/if}

      {#if voteParts.length > 1}
        <h3>Dépenses de fonctionnement votées, par budget</h3>
        <p class="hint">Part de chaque budget dans les dépenses de fonctionnement votées. Le budget principal verse une partie de son argent aux budgets annexes (subvention d'équilibre) : la même somme apparaît des deux côtés, d'où l'absence de total.</p>
        <Repartition parts={voteParts} format={eurosC} total={false}
                     titre="Budget voté {year} : dépenses de fonctionnement par budget" />
      {/if}

      {/if}
    {:else}
    <h2 id="essentiel">L'essentiel</h2>
    <!-- Synthèse en une phrase -->
    <p class="lede">
      En <b>{year}</b>{#if population}, {COMMUNE} (<b>{population.toLocaleString('fr-FR')}</b> hab.){/if}
      a perçu <b class="in">{eurosC(recF)}</b> de recettes de fonctionnement{#if aDesAnnexes}{' '}(budget principal){/if}
      et dépensé <b class="out">{eurosC(depF)}</b>,
      {#if epargne != null}dégageant <b class="save">{eurosC(epargne)}</b> d'épargne{#if aDesAnnexes}{' '}brute sur le budget principal{/if}{#if epargnePct != null}{' '}({epargnePct} % des recettes){/if} pour investir et se désendetter{/if}.
      {#if dette != null}Sa dette{#if ofglAnnexes}, tous budgets confondus,{/if} atteint <b class="debt">{eurosC(dette)}</b>{#if detteH != null}{' '}({perHab(detteH)}){/if}{#if ofglAnnexes && dettePrincipal != null}{' '}: {eurosC(dettePrincipal)} sur le budget principal et {eurosC(detteAnnexes)} sur les budgets annexes{/if}.{#if annexesAbsentes}{' '} Ce chiffre ne porte que sur le budget principal&nbsp;: les budgets annexes ne sont pas encore publiés pour {year}.{/if}{/if}
    </p>

    <!-- Deux chiffres de population coexistent sur le site : celui retenu par
         OFGL pour les ratios financiers (1 148) et la population municipale
         INSEE affichée sur /territoire (1 202). Les deux sont justes — elles ne
         comptent pas la même chose ni la même année — mais un lecteur qui passe
         d'une page à l'autre y voit une contradiction si personne ne le dit. -->
    {#if population}
      <p class="pop-note">
        Population retenue par l'Observatoire des finances locales pour cet
        exercice. Elle peut différer de la population municipale publiée par
        l'INSEE, indiquée sur <a href="/territoire">la page Territoire</a>&nbsp;:
        les deux ne portent ni sur la même année ni sur la même définition.
      </p>
    {/if}

    <!-- Combien / comparé à quoi / comment ça évolue. Sans la troisième
         question, « 1,78 M€ de dette » ne se distingue pas d'une situation
         stable depuis dix ans. -->
    {#if evolRec1 != null || evolDette1 != null}
      <p class="evolution">
        Par rapport à {year - 1}&nbsp;:
        {#if evolRec1 != null}recettes de fonctionnement{#if aDesAnnexes}{' '} (budget principal){/if} <b>{signe(evolRec1)}</b>{/if}
        {#if evolDep1 != null}{' '} · dépenses de fonctionnement <b>{signe(evolDep1)}</b>{/if}
        {#if evolDette1 != null}{' '} · dette{#if ofglAnnexes}{' '} (tous budgets){/if} <b>{signe(evolDette1)}</b>{/if}
        <span class="courants">Montants en euros courants, non corrigés de l'inflation&nbsp;: une hausse de quelques pour cent sur un an ne signifie pas une hausse en pouvoir d'achat.</span>
      </p>

    {/if}

    <!-- Distinction importante : ces montants ne sont PAS nos calculs. Ce sont
         les agrégats publiés par l'Observatoire des finances locales à partir
         des comptes de la commune. Les étiqueter « calcul » les affaiblirait à
         tort — ils sont plus solides que ce que nous savons produire. -->
    <Niveau type="fait" compact source="Agrégats publiés par OFGL / DGFiP, d'après les comptes de la commune" />
    <div class="tiles">
      {#each [{ cls: 'in', label: 'Recettes de fonctionnement', t: tRec }, { cls: 'out', label: 'Dépenses de fonctionnement', t: tDep }, { cls: 'save', label: 'Épargne brute', t: tEp }] as k}
        <div class="tile {k.cls}">
          <span class="tlabel">{k.label}{#if aDesAnnexes}<span class="tperim">{libellePerimetre}</span>{/if}</span>
          {#if k.t === NON_ADD}{' '}
            <span class="tval">—</span>
            <span class="tsub">pas de total : la même somme serait comptée deux fois</span>
          {' '}{:else}{' '}
            <span class="tval">{eurosS(k.t?.montant ?? null)}</span>
            <span class="tsub">{perHab(k.t?.euros_par_habitant ?? null)}{#if k.t?.flux}{' '}· flux entre budgets retirés : {eurosC(k.t.flux)}{/if}</span>
          {' '}{/if}
        </div>
      {/each}
      <div class="tile debt">
        <span class="tlabel">Dette (encours{#if ofglAnnexes}, tous budgets{:else if annexesAbsentes}, budget principal seul{/if})</span>
        <span class="tval">{eurosC(dette)}</span>
        <span class="tsub">{perHab(detteH)}{#if anneesDesendettement != null}{' '} · {anneesDesendettement.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} ans d'épargne brute (tous budgets){/if}</span>
      </div>
    </div>

    <h2 id="recettes-depenses">Recettes et dépenses</h2>
    <!-- L'équilibre en tête : recettes moins dépenses, l'épargne. -->
    {#if recF && depF}
      <p class="hint">Ce qui reste entre recettes et dépenses courantes forme l'épargne : la capacité de la commune à investir sans emprunter.</p>
      <div class="equil">
        <div class="erow">
          <span class="ekey"><i class="sw in"></i> Recettes</span>
          <div class="etrack"><div class="efill in" style="width:{100 * recF / equilScale}%"></div></div>
          <span class="eval">{eurosC(recF)}</span>
        </div>
        <div class="erow">
          <span class="ekey"><i class="sw out"></i> Dépenses</span>
          <div class="etrack"><div class="efill out" style="width:{100 * depF / equilScale}%"></div></div>
          <span class="eval">{eurosC(depF)}</span>
        </div>
        {#if epargne != null}
          <div class="erow save-row">
            <span class="ekey"><i class="sw save"></i> Épargne</span>
            <div class="etrack"><div class="efill save" style="width:{100 * Math.max(epargne, 0) / equilScale}%"></div></div>
            <span class="eval">{eurosC(epargne)}</span>
          </div>
        {/if}
      </div>
    {/if}

    <!-- Le détail DGFiP par compte. -->
    {#if !hasDetail}
      <p class="note">Le détail comptable par poste (« où va / d'où vient l'argent ») n'est pas encore publié pour {year} : la DGFiP diffuse les comptes détaillés avec environ 18 mois de décalage. Les chiffres-clés et l'épargne ci-dessus proviennent des agrégats OFGL, disponibles plus tôt.{#if lastDetailYear}{' '}Dernier exercice détaillé : <b>{lastDetailYear}</b>.{/if}</p>
    {/if}
    <div class="cols">
      {#if depRows.length}
        <div class="col">
          <h3 class="out-h">Où va l'argent</h3>
          <p class="hint">Dépenses de fonctionnement {year} : {eurosC(depTotalDG)}</p>
          <ul class="bars">
            {#each depRows as r}
              <li>
                <span class="blabel" title={r.libelle}>{r.libelle}</span>
                <span class="btrack"><span class="bfill out" style="width:{100 * r.montant / depMax}%"></span></span>
                <span class="bval">{eurosC(r.montant)}</span>
                <span class="bpct">{pct(r.montant, depTotalDG)}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      {#if recRows.length}
        <div class="col">
          <h3 class="in-h">D'où vient l'argent</h3>
          <p class="hint">Recettes de fonctionnement {year} : {eurosC(recTotalDG)}</p>
          <ul class="bars">
            {#each recRows as r}
              <li>
                <span class="blabel" title={r.libelle}>{r.libelle}</span>
                <span class="btrack"><span class="bfill in" style="width:{100 * r.montant / recMax}%"></span></span>
                <span class="bval">{eurosC(r.montant)}</span>
                <span class="bpct">{pct(r.montant, recTotalDG)}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </div>

    {#if gV("Concours de l'Etat") != null}
      <p class="hint">Concours de l'État (dotations et compensations) : <b>{eurosC(gV("Concours de l'Etat"))}</b>, {perHab(gH("Concours de l'Etat"))}.</p>
    {/if}

    {#if dette != null}
    <h2 id="dette">La dette</h2>
    {#if detteParts.length > 1}
      <h3>Au 31 décembre {year}, par budget</h3>
      <p class="hint">La dette de la commune, c'est celle de tous ses budgets : elles s'additionnent sans qu'aucune soit comptée deux fois.</p>
      <Repartition parts={detteParts} format={eurosC} titre="Dette au 31 décembre {year}, par budget" />
      {#if detteCautions > 0}
        <p class="hint">Dette des budgets annexes y compris {eurosC(detteCautions)} de dépôts et cautionnements reçus (des locataires, par exemple), que l'OFGL compte aussi dans l'encours.</p>
      {/if}
      {#if Math.abs(ecartDetteSources) >= 1}
        <p class="note">Le détail par budget annexe vient des balances DGFiP ; il s'écarte de {eurosC(Math.abs(ecartDetteSources))} du total des budgets annexes publié par l'OFGL ({eurosC(detteAnnexes)}), repris dans la tuile « Dette » de la section « L'essentiel ».</p>
      {/if}
    {/if}

    <div class="tiles small">
      <div class="tile"><span class="tlabel">Annuité de la dette{#if aDesAnnexes}{' '} (budget principal){/if}</span><span class="tval">{eurosC(annuite)}</span><span class="tsub">{perHab(gH('Annuité de la dette'))}</span></div>
      <div class="tile"><span class="tlabel">Épargne nette{#if aDesAnnexes}{' '} (budget principal){/if}</span><span class="tval">{eurosC(epargneNette)}</span><span class="tsub">{perHab(gH('Epargne nette'))}</span></div>
      <div class="tile"><span class="tlabel">Dépenses d'équipement</span><span class="tval">{eurosC(equipement)}</span><span class="tsub">{perHab(gH("Dépenses d'équipement"))}</span></div>
    </div>
      <!-- Cette phrase ne regardait que la trajectoire longue, et ignorait
           l'exercice affiché juste avant elle : sur 2025, la page écrivait
           « dette -4 % » puis, trois mots plus loin, « multipliée par 3,4 ».
           Les deux chiffres étaient justes et la page se contredisait quand
           même. Quand la dette recule sur l'exercice, c'est ce recul qui se
           dit en premier ; l'échelle longue vient ensuite, comme mesure de ce
           qui reste à rembourser. -->
      {#if detteFacteur && detteFacteur > 1.5 && detteMin}
        <p class="evolution">
          {#if evolDette1 != null && evolDette1 < 0}{' '}
            Sur {year}, la dette recule ({signe(evolDette1)}), mais reste <b>{facteurDette}</b> fois
            son point bas de {detteMin[0]} ({eurosC(detteMin[1])}).
          {' '}{:else}{' '}
            La dette a été multipliée par <b>{facteurDette}</b> depuis
            son point bas de {detteMin[0]} ({eurosC(detteMin[1])}).
          {' '}{/if}
        </p>
      {/if}
      {#if mouvementDette}
        <p class="lecture">
          <b>Budget principal et budgets annexes.</b>
          Sur l'exercice, l'encours varie de <b>{eurosS(mouvementDette.dP)}</b> sur le budget
          principal et de <b>{eurosS(mouvementDette.dA)}</b> sur les budgets annexes, soit
          <b>{eurosS(mouvementDette.total)}</b> au total.
          {#if mouvementDette.transfert}{' '}
            Les deux budgets vont en sens contraire : une part du mouvement peut
            tenir à un transfert de dette de l'un à l'autre plutôt qu'à un nouvel
            emprunt ou à un remboursement. Les chiffres du budget principal seul
            doivent se lire avec cette réserve.
          {' '}{/if}
        </p>
      {/if}

      {#if detteFacteur && detteFacteur > 1.5}
        <p class="lecture">
          <!-- « Une dette qui augmente » affirmait une hausse au moment même où
               l'exercice affichait une baisse : ce paragraphe se déclenche sur
               le facteur pluriannuel, pas sur l'année. Formulation sans
               direction — elle reste vraie que la dette monte ou reflue. -->
          <b>Ce que ce chiffre ne dit pas.</b> Un endettement élevé n'est pas en
          soi un mauvais signe&nbsp;: la dette finance des investissements, et emprunter
          quand les taux sont bas pour réaliser des travaux durables est une
          décision courante. Ce que ces chiffres ne disent pas, c'est
          <em>ce qui</em> a été financé : cela se cherche dans
          <a href="/deliberations">les délibérations</a> des années concernées.
        </p>
      {/if}
    {/if}
    {/if}

    {#if epargneSerie.length > 1 || ofglGroupsShown.length}
      <h2 id="evolution">L'évolution</h2>
    {/if}
    {#if epargneSerie.length > 1}
      <h3>Épargne brute</h3>
      <div class="trend">
        {#each epargneSerie as d}
          <div class="tcol" class:cur={d.year === year} title="{d.year} : {eurosC(d.val)}">
            <span class="tbar-val">{Math.round(d.val / 1000)}k</span>
            <span class="tbar" class:neg={d.val < 0} style="height:{Math.max(4, 100 * Math.abs(d.val) / epSerieMax)}%"></span>
            <span class="tyear">{d.year}</span>
          </div>
        {/each}
      </div>
    {/if}

    <!-- Agrégats OFGL : vue annualisée par thème -->
    {#if ofglGroupsShown.length}
      <div class="secthead">
        <h3>Année par année</h3>
        <div class="seg">
          <button class:on={!showPerHab} aria-pressed={!showPerHab} on:click={() => showPerHab = false}>Montant</button>
          <button class:on={showPerHab} aria-pressed={showPerHab} on:click={() => showPerHab = true}>€/hab.</button>
        </div>
      </div>
      <p class="hint">Les grands équilibres financiers de la commune sur {Math.min(...ofglYears)}–{Math.max(...ofglYears)} (source OFGL), toutes les années quelle que soit l'année affichée. Colonne <b>{year}</b> surlignée.{#if aDesAnnexes}{' '}Périmètre : <b>{libellePerimetre}</b> (<a href="#perimetre">changer</a>).{/if}{#if tousConsolide}{' '}En « tous budgets », les comptes consolidés de l'OFGL : les flux entre budgets sont retirés, et rien n'est compté deux fois.{/if}</p>
      {#if nonAdditionnes.length}
        <p class="hint">Absents en « tous budgets », parce que la même somme y serait comptée deux fois : {nonAdditionnes.map(n => /^[A-Z]{2}/.test(n) ? n : n.charAt(0).toLowerCase() + n.slice(1)).join(', ')}. Ils se lisent budget par budget.</p>
      {/if}
      <div class="scrollx">
        <table class="matrix" class:toutes={toutesAnnees || !matriceEvol}>
          <caption class="visually-hidden">Agrégats financiers, année par année ({libellePerimetre})</caption>
          <thead>
            <tr>
              <th scope="col" class="ag">Agrégat</th>
              {#each ofglYears as y}<th scope="col" class="r" class:cur={y === year} class:hors={y !== year && y !== year - 1}>{y}</th>{/each}
              {#if matriceEvol}<th scope="col" class="r">Évolution</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each ofglGroupsShown as g}
              <tr class="grp"><td colspan={ofglYears.length + 2}>{g.title}</td></tr>
              {#each g.items as name}
                {@const ev = evolLigne(name)}
                <tr>
                  <th scope="row" class="ag">{libelleAgregat(name)}</th>
                  {#each ofglYears as y}
                    {@const c = valeur(name, y, perimetre)}
                    {@const v = lisible(c) ? (showPerHab ? c.euros_par_habitant : c.montant) : null}
                    <td class="r" class:cur={y === year} class:hors={y !== year && y !== year - 1} class:neg={v != null && v < 0}>
                      {#if v != null}{showPerHab ? parHab(v) : eurosS(v)}{:else}·{/if}
                    </td>
                  {/each}
                  {#if matriceEvol}<td class="r evol">{ev != null ? `${signe(ev)} vs ${year - 1}` : '·'}</td>{/if}
                </tr>
              {/each}
            {/each}
          </tbody>
        </table>
      </div>
      {#if matriceEvol}{' '}
        <button type="button" class="voir-tout" aria-pressed={toutesAnnees} on:click={() => toutesAnnees = !toutesAnnees}>
          {toutesAnnees ? `Seulement ${year - 1} et ${year}` : 'Voir toutes les années'}
        </button>
      {' '}{/if}
    {/if}


    {#if annexeByStruct.length || (isVoteYear && voteAnnexes.length)}
      <h2 id="annexes">Les budgets annexes</h2>
    {/if}
    {#if isVoteYear}
      {#if voteAnnexes.length}
        <h3>Votés pour {year}</h3>
        <p class="hint">Un budget voté est équilibré section par section : quand les recettes égalent les dépenses, le montant n'est écrit qu'une fois.</p>
        <div class="votecards">
          {#each voteCartes as c}
            <div class="votecard">
              <h4>{c.nom}</h4>
              <dl>
                {#each c.sections as s}
                  <dt>{s.titre}</dt>
                  {#if equilibre(s)}
                    <dd><b>{fmtVote(s.dep)}</b> <span class="vnote">équilibré</span></dd>
                  {:else}
                    <dd>
                      {#if s.rec}<span>recettes <b>{fmtVote(s.rec)}</b></span>{/if}
                      {#if s.dep}<span>dépenses <b>{fmtVote(s.dep)}</b></span>{/if}
                    </dd>
                  {/if}
                {/each}
                {#each c.dont as d}
                  <dt class="dont">{d.titre}</dt><dd>{fmtVote(d.r)}</dd>
                {/each}
                {#each c.autres as r}
                  <dt class="dont">{LIBELLES_VOTE[r.agregat] ?? r.agregat}</dt><dd>{fmtVote(r)}</dd>
                {/each}
              </dl>
              {#each c.notes as n}<p class="vnote">{n}</p>{/each}
            </div>
          {/each}
        </div>
      {/if}

    {/if}
    <!-- Budgets annexes : synthèse par structure et par année -->
    {#if annexeByStruct.length}
      {#if isVoteYear && voteAnnexes.length}<h3>Comptes, année par année</h3>{/if}
      <p class="hint">{annexeByStruct.length} budgets annexes. Chacun tient ses propres comptes ; ouvrez-en un pour voir ses années (balance DGFiP), toutes les années quelle que soit l'année affichée.</p>
      {#each annexeByStruct as s}
        <details class="annexe-struct">
          <summary>
            <span class="anom">{s.nom}</span>
            <span class="aresume">
              {#if s.detteFin != null}dette {eurosC(s.detteFin)} fin {s.detteAn}{:else}sans emprunt{/if}{#if s.dernier < derniereAnneeAnnexes}{' '}· jusqu'en {s.dernier}{/if}
            </span>
          </summary>
          <p class="adgfip">Libellé DGFiP : {#if s.entityId}<a href="/entite/{s.entityId}">{s.name}</a>{:else}{s.name}{/if}</p>
          {#if s.precede}
            <p class="hint">Suite de « {nomBudget(s.precede)} » : comptes publiés sous ce nom à partir de {s.years[0].year}.</p>
          {/if}
          {#if s.dernier < derniereAnneeAnnexes}
            <p class="hint">Aucun compte publié pour ce budget après {s.dernier}{#if s.suite}{' '} ; poursuivi sous le nom « {nomBudget(s.suite)} », juste en dessous{/if}.</p>
          {/if}
          <div class="scrollx">
            <table class="annexe">
              <caption class="visually-hidden">Comptes de {s.nom}, année par année</caption>
              <thead><tr><th scope="col">Année</th>{#if s.aDette}<th scope="col" class="r" title="Emprunts et cautionnements reçus, balance DGFiP ; « - » : aucun compte de dette dans la balance">Encours de dette</th>{/if}<th scope="col" class="r">Solde de fonctionnement</th>{#if s.aSubv}<th scope="col" class="r">Subvention communale</th>{/if}<th scope="col">Source</th></tr></thead>
              <tbody>
                {#each s.years as y}
                  <tr>
                    <td>{y.year}</td>
                    {#if s.aDette}<td class="r">{y.detteVal != null ? eurosC(y.detteVal) : '-'}</td>{/if}
                    <td class="r">
                      {#if y.soldeVal != null}{' '}
                        <span class:neg={y.soldeVal < 0} class:pos={y.soldeVal >= 0}>{eurosS(y.soldeVal)}</span>
                        {#if y.soldeLbl}<span class="sublbl">{y.soldeLbl}</span>{/if}
                      {' '}{:else}-{/if}
                    </td>
                    {#if s.aSubv}<td class="r">{y.subvComm != null ? eurosC(y.subvComm) : '-'}</td>{/if}
                    <td class="src">{y.source}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          <details class="raw">
            <summary>Détail ligne à ligne</summary>
            <table>
              <caption class="visually-hidden">Comptes de {s.nom}, détail ligne à ligne</caption>
              <thead><tr><th scope="col">Année</th><th scope="col">Section</th><th scope="col">Sens</th><th scope="col">Libellé</th><th scope="col" class="r">Montant</th></tr></thead>
              <tbody>
                {#each s.years as y}{#each y.rows as r}
                  <tr><td>{r.year}</td><td>{r.section || ''}</td><td>{r.sens || ''}</td><td>{r.libelle || ''}</td><td class="r">{euros(r.montant)}</td></tr>
                {/each}{/each}
              </tbody>
            </table>
          </details>
        </details>
      {/each}
    {/if}

    <p class="foot">Sources : comptes administratifs DGFiP (détail par compte), agrégats OFGL (€/habitant), délibérations budgétaires du CM (budgets votés {voteYears.length ? voteYears.slice().sort((a,b)=>a-b).join(', ') : '-'}). Les agrégats OFGL couvrent {ofglYears.length ? `${Math.min(...ofglYears)}–${Math.max(...ofglYears)}` : '-'} ; le détail par compte court jusqu'à {lastDetailYear ?? '-'}.</p>
  {/if}
</section>

<style>
  .pop-note { margin: -.25rem 0 1rem; font-size: .78rem; color: var(--gris-clair); line-height: 1.5; max-width: 60ch; }
  .pop-note a { color: var(--ardoise); }

  .evolution { margin: .25rem 0 1rem; font-size: .92rem; color: var(--gris); line-height: 1.6; }
  .evolution b { color: var(--encre); font-variant-numeric: tabular-nums; }
  .courants { display: block; margin-top: .3rem; font-size: .76rem; color: var(--gris-clair); }
  .lecture {
    margin: .5rem 0 1.5rem; padding: .7rem .9rem;
    border-left: 3px solid var(--ambre); background: var(--ambre-pale);
    border-radius: 0 var(--rayon) var(--rayon) 0;
    font-size: .9rem; line-height: 1.55; color: var(--gris);
  }
  .lecture b { color: var(--encre); }
  .lecture a { color: var(--ardoise); }

  section { max-width: 1000px; margin: 0 auto; padding: 1.5rem; color: var(--encre); }

  .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
  h1 { margin: 0 0 .25rem; }
  .sub { color: var(--gris); margin: 0; }
  .year { color: var(--gris); font-size: .85rem; display: flex; flex-direction: column; gap: .25rem; }
  select { padding: .4rem .6rem; border: 1px solid var(--trait); border-radius: 6px; font-size: .95rem; }

  /* Couleurs sémantiques (toujours doublées d'un libellé texte) */
  .in   { color: var(--recette); }
  .out  { color: var(--depense); }
  .save { color: var(--ardoise); }
  .debt { color: var(--ambre); }

  .lede { font-size: 1.05rem; line-height: 1.6; margin: 1.25rem 0 1.5rem; color: var(--gris); }
  .lede b { font-weight: 700; }

  /* Tuiles chiffres-clés */
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-bottom: 1.75rem; }
  .tiles.small { margin-top: .75rem; }
  .tile { background: #fff; border: 1px solid var(--trait); border-top: 3px solid var(--trait); border-radius: 8px; padding: .8rem .9rem; display: flex; flex-direction: column; gap: .15rem; }
  .tile.in   { border-top-color: var(--recette); }
  .tile.out  { border-top-color: var(--depense); }
  .tile.save { border-top-color: var(--ardoise); }
  .tile.debt { border-top-color: var(--ambre); }
  .tlabel { font-size: .72rem; color: var(--gris); font-weight: 600; text-transform: uppercase; letter-spacing: .02em; }
  .tval { font-size: 1.45rem; font-weight: 700; color: var(--encre); }
  .tsub { font-size: .78rem; color: var(--gris-clair); font-variant-numeric: tabular-nums; }

  h2 { color: var(--encre); margin: 1.75rem 0 .25rem; font-size: 1.15rem; }
  h3 { color: var(--encre); margin: 1.5rem 0 .5rem; font-size: 1rem; }
  h2.out-h { color: #b3312f; } h2.in-h { color: var(--recette); }
  .hint { color: var(--gris); font-size: .85rem; margin: 0 0 .9rem; }

  /* Équilibre */
  .equil { display: flex; flex-direction: column; gap: .5rem; margin-bottom: .5rem; }
  .erow { display: grid; grid-template-columns: 90px 1fr auto; align-items: center; gap: .75rem; }
  .ekey { font-size: .85rem; color: var(--gris); display: flex; align-items: center; gap: .4rem; white-space: nowrap; }
  .sw { width: 11px; height: 11px; border-radius: 3px; flex-shrink: 0; }
  .sw.in { background: var(--recette); } .sw.out { background: var(--depense); } .sw.save { background: var(--ardoise); }
  .etrack { background: var(--trait-pale); border-radius: 5px; height: 22px; overflow: hidden; }
  .efill { height: 100%; border-radius: 5px; }
  .efill.in { background: var(--recette); } .efill.out { background: var(--depense); } .efill.save { background: var(--ardoise); }
  .eval { font-variant-numeric: tabular-nums; font-weight: 700; font-size: .9rem; white-space: nowrap; }
  .save-row .ekey, .save-row .eval { color: var(--ardoise-fonce); }

  /* Deux colonnes dépenses / recettes */
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 1rem; }
  .bars { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
  .bars li { display: grid; grid-template-columns: 1fr; gap: .15rem; }
  .blabel { font-size: .82rem; color: var(--gris); }
  .btrack { display: block; background: var(--trait-pale); border-radius: 4px; height: 14px; overflow: hidden; }
  .bfill { display: block; height: 100%; border-radius: 4px; }
  .bfill.out { background: var(--depense); } .bfill.in { background: var(--recette); }
  .bval { font-size: .82rem; font-weight: 700; color: var(--encre); font-variant-numeric: tabular-nums; }
  .bpct { font-size: .75rem; color: var(--gris-clair); }
  .bars li { grid-template-columns: minmax(0, 1fr) 70px 42px; align-items: center; column-gap: .6rem; }
  .blabel { grid-column: 1 / -1; }
  .btrack { grid-column: 1; }
  .bval { grid-column: 2; text-align: right; }
  .bpct { grid-column: 3; text-align: right; }

  /* Tendance épargne */
  .trend { display: flex; align-items: flex-end; gap: .5rem; height: 130px; padding: .5rem 0; border-bottom: 1px solid var(--trait); }
  .tcol { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: .25rem; }
  .tbar { width: 60%; max-width: 34px; background: var(--ardoise); border-radius: 4px 4px 0 0; min-height: 4px; }
  .tbar.neg { background: var(--depense); }
  .tcol.cur .tbar { outline: 2px solid var(--ardoise-fonce); outline-offset: 1px; }
  .tbar-val { font-size: .68rem; color: var(--gris-clair); font-variant-numeric: tabular-nums; }
  .tyear { font-size: .72rem; color: var(--gris); }
  .tcol.cur .tyear { color: var(--encre); font-weight: 700; }

  details { margin-top: 1.25rem; border-top: 1px solid var(--trait); padding-top: .5rem; }
  summary { cursor: pointer; color: var(--gris); font-size: .9rem; font-weight: 600; padding: .35rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; margin-top: .5rem; }
  th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--ardoise-pale); }
  th { color: var(--gris); font-weight: 600; }
  .r { text-align: right; font-variant-numeric: tabular-nums; }

  /* Vues annualisées (agrégats + annexes) */
  h4 { margin: 1rem 0 .35rem; font-size: .95rem; color: var(--encre); }
  .secthead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin: 1.75rem 0 .25rem; }
  .secthead h3 { margin: 0; }
  .seg { display: inline-flex; border: 1px solid var(--trait); border-radius: 6px; overflow: hidden; }
  .seg button { padding: .35rem .7rem; font-size: .8rem; background: #fff; color: var(--gris); border: none; border-left: 1px solid var(--trait); cursor: pointer; }
  .seg button:first-child { border-left: none; }
  .seg button.on { background: var(--ardoise); color: #fff; }
  .scrollx { overflow-x: auto; }
  table.matrix { min-width: 640px; }
  table.matrix th.ag { text-align: left; white-space: normal; min-width: 150px; max-width: 220px; position: sticky; left: 0; background: #fff; color: var(--gris); font-weight: 400; font-size: .82rem; }
  table.matrix thead th.ag { font-weight: 600; font-size: .78rem; }
  table.matrix th { font-size: .78rem; }
  table.matrix td.neg { color: var(--depense); font-weight: 600; }
  table.matrix td.evol { color: var(--gris); font-size: .78rem; }
  .voir-tout { display: none; margin: .5rem 0 0; min-height: 44px; padding: .4rem .9rem; border: 1px solid var(--trait); border-radius: 8px; background: #fff; color: var(--ardoise); font-size: .85rem; cursor: pointer; }
  .tperim { display: block; text-transform: none; font-weight: 400; letter-spacing: 0; color: var(--gris-clair); }
  table.matrix td { font-size: .82rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
  table.matrix .grp td { background: var(--trait-pale); font-weight: 700; font-size: .74rem; text-transform: uppercase; letter-spacing: .03em; color: var(--gris); }
  table.matrix th.cur, table.matrix td.cur { background: var(--ardoise-pale); }
  .annexe-struct { margin: 0; padding: 0; border: none; border-bottom: 1px solid var(--trait); }
  .annexe-struct > summary { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: .25rem 1rem; min-height: 44px; padding: .6rem .25rem; color: var(--encre); font-weight: 400; }
  .annexe-struct > summary .anom { font-weight: 600; }
  .annexe-struct > summary .aresume { font-size: .82rem; color: var(--gris); font-variant-numeric: tabular-nums; }
  .annexe-struct[open] { padding-bottom: .9rem; }
  .adgfip { margin: .1rem 0 .5rem; font-size: .76rem; color: var(--gris-clair); }
  .adgfip a { color: var(--ardoise); }
  .sommaire { display: flex; flex-wrap: wrap; gap: .25rem 1.1rem; margin: .9rem 0 0; font-size: .88rem; }
  .sommaire a { display: inline-flex; align-items: center; min-height: 44px; color: var(--ardoise); text-decoration: none; border-bottom: 1px solid transparent; }
  .sommaire a:hover { border-bottom-color: var(--ardoise); }
  .sommaire a:focus-visible { outline: 2px solid var(--ardoise); outline-offset: 2px; }
  h2[id] { margin-top: 2.5rem; padding-top: 1.1rem; border-top: 1px solid var(--trait); scroll-margin-top: 4.5rem; }
  h2#essentiel { margin-top: 1.5rem; }
  .pilote { margin: .4rem 0 0; font-size: .8rem; color: var(--gris); max-width: 70ch; }
  table.annexe { min-width: 520px; font-size: .85rem; }
  table.annexe .neg { color: var(--depense); font-weight: 700; }
  table.annexe .pos { color: var(--recette); font-weight: 700; }
  table.annexe .sublbl { display: block; font-size: .72rem; color: var(--gris-clair); font-weight: 400; }
  table.annexe .src { color: var(--gris-clair); font-size: .76rem; }
  details.raw { margin-top: .4rem; border: none; padding: 0; }
  details.raw summary { font-size: .8rem; color: var(--gris); font-weight: 500; }

  .muted { color: var(--gris); }
  .note { background: var(--ambre-pale); border: 1px solid #eadfc6; color: var(--ambre); border-radius: 8px; padding: .6rem .8rem; font-size: .85rem; margin: 1rem 0; max-width: 80ch; }

  /* Budget voté (prévisionnel, source CR) */
  .votebanner { background: #eef2ff; border: 1px solid #c7d2fe; border-left: 3px solid var(--ardoise-fonce); color: var(--ardoise-fonce); border-radius: 8px; padding: .7rem .9rem; font-size: .88rem; line-height: 1.5; margin: 1.25rem 0 1.5rem; }
  .votebanner a { color: var(--ardoise-fonce); font-weight: 600; }
  .tile.vote { border-top-color: var(--ardoise); }
  .votecards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .75rem; margin: .25rem 0 1rem; }
  .votecard { background: #fff; border: 1px solid var(--trait); border-top: 3px solid var(--ardoise); border-radius: 8px; padding: .75rem .9rem; }
  .votecard h4 { margin: 0 0 .5rem; }
  .votecard dl { display: grid; grid-template-columns: auto 1fr; column-gap: .8rem; row-gap: .3rem; margin: 0; font-size: .85rem; }
  .votecard dt { color: var(--gris); }
  .votecard dt.dont { padding-left: .6rem; font-size: .8rem; color: var(--gris-clair); }
  .votecard dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; color: var(--ardoise-fonce); display: flex; flex-direction: column; align-items: flex-end; }
  .votecard dd b { font-weight: 700; }
  .votecard dd > span, .votecard dd b { white-space: nowrap; }
  .vnote { font-size: .76rem; color: var(--gris-clair); font-weight: 400; margin: .3rem 0 0; }
  .note b { font-weight: 700; }
  .foot { color: var(--gris-clair); font-size: .78rem; margin-top: 1.5rem; }

  @media (max-width: 720px) {
    .tiles { grid-template-columns: repeat(2, 1fr); }
    .cols { grid-template-columns: 1fr; gap: 1.25rem; }
    table.matrix { min-width: 0; }
    table.matrix:not(.toutes) .hors { display: none; }
    .voir-tout { display: inline-block; }
  }
</style>
