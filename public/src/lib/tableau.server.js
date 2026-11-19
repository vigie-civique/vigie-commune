// Le tableau de bord de l'accueil : douze indicateurs, calculés au build.
//
// Chaque tuile porte un libellé, la dernière valeur, un mini-graphique et sa
// provenance, et mène à la page qui détaille. Rien n'est tracé dans le
// navigateur : les séries sortent d'ici, le SVG est écrit au prérendu.
//
// Trois règles tiennent toutes les tuiles :
//  - la COMMUNE seule. `territoire.json`, `fiscalite.json` et `urbanisme.json`
//    portent toutes les communes de l'intercommunalité : chaque lecture filtre
//    sur le code INSEE de l'instance ;
//  - une tuile sans donnée ne s'affiche pas (`null`), et une série de moins de
//    trois points ne se trace pas : la valeur reste, seule ;
//  - l'année en cours est partielle, et sa barre le montre (`partiel`).
import { estAttribue } from '#lib/marches.js'
import { evolutionTuile, pointsATracer, tauxAvecAvant2021, INDICATEUR_DEP, ANNEE_RUPTURE } from '../routes/impots/rupture.js'

const nombre = (n) => Math.round(n).toLocaleString('fr-FR')
const decimal = (n, d = 2) =>
  n.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d })
const millions = (n) =>
  n >= 1e6 ? `${(n / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} M€`
           : `${nombre(n / 1e3)} k€`
const s = (n) => (n > 1 ? 's' : '')
const tracable = (points) => (points.length >= 3 ? points : null)

/** L'échelle d'une courbe qui ne part pas de zéro : des bornes rondes, et la
 *  phrase qui les dit sous le graphique. Un axe tronqué grossit l'écart ; le
 *  lecteur doit savoir d'où part le bas (décision de Julien, 09/11/2026). */
function echelleRonde(valeurs, unite) {
  const bas = Math.min(...valeurs)
  const haut = Math.max(...valeurs)
  const pas = 10 ** Math.floor(Math.log10((haut - bas) || Math.abs(haut) / 10 || 1))
  const lo = Math.floor(bas / pas) * pas
  let hi = Math.ceil(haut / pas) * pas
  if (hi === lo) hi += pas
  if (lo <= 0) return null
  const f = (n) => n.toLocaleString('fr-FR', { maximumFractionDigits: 2 })
  const texte = unite === '€'
    ? (hi >= 1e6 ? `${f(lo / 1e6)} à ${f(hi / 1e6)} M€` : `${f(lo / 1e3)} à ${f(hi / 1e3)} k€`)
    : `${f(lo)} à ${f(hi)} ${unite}`
  return { bornes: [lo, hi], echelle: `échelle de ${texte}` }
}

/** Compte par année, sans trou : une année sans rien est une barre à zéro. */
function parAnnee(dates, anneeCourante, max = 10) {
  const comptes = {}
  for (const d of dates) {
    const a = Number(String(d || '').slice(0, 4))
    if (a > 1900 && a <= anneeCourante) comptes[a] = (comptes[a] || 0) + 1
  }
  const annees = Object.keys(comptes).map(Number)
  if (!annees.length) return []
  const fin = Math.max(...annees)
  const debut = Math.max(Math.min(...annees), fin - max + 1)
  const points = []
  for (let a = debut; a <= fin; a++) {
    points.push({ x: a, y: comptes[a] || 0, partiel: a === anneeCourante })
  }
  return points
}

/** Ce qu'est un service d'eau, d'après le champ `type_collectivite` publié
 * par SISPEA (« Syndicat Intercommunal à Vocation Unique » → « le syndicat
 * intercommunal »). Jamais son nom : le nom publié est en capitales sans
 * accents, et le moteur ne nomme aucune commune. Un type absent ou inconnu ne
 * dit rien. */
const NATURES_DE_SERVICE = [
  [/^syndicat intercommunal/i, 'le syndicat intercommunal'],
  [/^syndicat mixte/i, 'le syndicat mixte'],
  [/^communaut[ée] de communes/i, 'la communauté de communes'],
  [/^communaut[ée] d.agglom[ée]ration/i, "la communauté d'agglomération"],
  [/^commune$/i, 'la commune'],
]
const natureDuService = (x) =>
  NATURES_DE_SERVICE.find(([motif]) => motif.test(String(x?.type_collectivite || '').trim()))?.[1]

/** La dernière année ENTIÈRE d'une série annuelle, à défaut la seule connue. */
const derniereEntiere = (points) =>
  [...points].reverse().find((p) => !p.partiel) || points.at(-1)

export function tableauDeBord({ lire, insee, aujourdhui, chiffres, interco,
                                sourceMarches, extraitsMarches }) {
  const anneeCourante = Number(aujourdhui.slice(0, 4))
  const tuiles = []

  // ── 1. Délibérations ────────────────────────────────────────────────────
  // Celles du conseil municipal. Les délibérations communautaires ne s'y
  // ajoutent pas : nommées à part, comme sur la tuile d'avant.
  {
    const events = lire('events.json', { events: [] }).events || []
    // Première séance communautaire passée dont un acte est publié (comme
    // `interco.depuis` de +page.server.js) : une séance sans acte ne date rien.
    const depuisCc = (lire('seances.json', {}).seances || [])
      .filter((x) => x.code === 'cc' && x.date <= aujourdhui && x.nb_actes > 0).map((x) => String(x.date).slice(0, 4)).sort()[0]
    const points = parAnnee(
      events.filter((e) => e.type === 'deliberation').map((e) => e.date), anneeCourante)
    if (chiffres.deliberations != null) {
      tuiles.push({
        cle: 'deliberations', href: '/deliberations', libelle: 'Délibérations',
        valeur: nombre(chiffres.deliberations),
        note: points.length ? `municipales, ${points[0].x} à ${points.at(-1).x}` : 'municipales',
        aussi: interco?.deliberations
          ? `intercommunalité : ${nombre(interco.deliberations)}${depuisCc ? ` depuis ${depuisCc}` : ''}` : null,
        graphe: tracable(points) && { type: 'barres', points },
      })
    }
  }

  // ── 2. Séances ──────────────────────────────────────────────────────────
  // Douze mois de conseils, une marque par séance : le conseil municipal en
  // haut, le conseil communautaire dessous. Une séance mise en clair est
  // pleine, les autres pâles.
  {
    const seances = lire('seances.json', {}).seances || []
    const fin = new Date(aujourdhui + 'T00:00:00')
    const debut = new Date(fin)
    debut.setFullYear(debut.getFullYear() - 1)
    const duree = fin - debut
    const recentes = seances
      .map((x) => ({ ...x, t: new Date(x.date + 'T00:00:00') }))
      .filter((x) => x.t > debut && x.t <= fin)
    const cm = recentes.filter((x) => x.code === 'cm')
    const cc = recentes.filter((x) => x.code === 'cc')
    if (recentes.length) {
      // Le compte « en clair » porte sur les MÊMES séances que la valeur : pris
      // sur les deux assemblées, il affichait « 15 séances, 16 en clair ».
      const enClair = cm.filter((x) => x.en_clair).length
      // Deux périmètres que le lecteur confondait avec /couverture : les douze
      // derniers mois ici, tout l'historique là-bas. Les deux sont dits.
      const toutesCm = seances.filter((x) => x.code === 'cm')
      const depuis = toutesCm.map((x) => x.date.slice(0, 4)).sort()[0]
      const historique = toutesCm.length > cm.length && depuis
        ? ` (${nombre(toutesCm.length)} depuis ${depuis})` : ''
      tuiles.push({
        cle: 'seances', href: '/conseils', libelle: 'Séances du conseil',
        valeur: nombre(cm.length),
        note: `municipales, 12 derniers mois${historique}${enClair ? `, dont ${enClair} en clair` : ''}`,
        aussi: cc.length ? `conseil communautaire : ${nombre(cc.length)} séance${s(cc.length)}` : null,
        graphe: {
          type: 'frise',
          lignes: [cm, cc].filter((l) => l.length).map((l) =>
            l.map((x) => ({ x: (x.t - debut) / duree, plein: Boolean(x.en_clair) }))),
        },
      })
    }
  }

  // ── 3. Recettes et dépenses de fonctionnement ───────────────────────────
  {
    const lignes = lire('ofgl.json', { ofgl: [] }).ofgl || []
    const serie = (agregat) => lignes
      .filter((l) => l.agregat === agregat && l.montant != null)
      .sort((a, b) => a.year - b.year)
      .map((l) => ({ x: l.year, y: l.montant }))
    const recettes = serie('Recettes de fonctionnement')
    const depenses = serie('Dépenses de fonctionnement')
    if (recettes.length) {
      const r = recettes.at(-1)
      const d = depenses.find((p) => p.x === r.x)
      const bas = recettes.reduce((m, p) => (p.y < m.y ? p : m))
      const haut = recettes.reduce((m, p) => (p.y > m.y ? p : m))
      const bornes = recettes.length >= 3 && bas.y !== haut.y
        ? `entre ${millions(bas.y)} (${bas.x}) et ${millions(haut.y)} (${haut.x})` : null
      tuiles.push({
        cle: 'budget', href: '/budgets', libelle: 'Recettes de fonctionnement',
        valeur: millions(r.y),
        // Pas de `depuisZero` : la courbe suit l'écart. Les bornes sont donc écrites.
        note: `${r.x}, comptes publics (OFGL)` + (bornes ? `, ${bornes}` : ''),
        aussi: d ? `dépenses : ${millions(d.y)}` : null,
        graphe: tracable(recettes) && (() => {
          const series = [{ ton: 'recette', points: recettes },
                          depenses.length >= 3 && { ton: 'depense', points: depenses }].filter(Boolean)
          // L'axe ne part pas de zéro : ses bornes sont dites sous la courbe.
          return { type: 'courbe', series,
                   ...echelleRonde(series.flatMap((x) => x.points.map((p) => p.y)), '€') }
        })(),
      })
    }
  }

  // ── 4. Taxe foncière ────────────────────────────────────────────────────
  // Le taux VOTÉ par la commune, pas le taux global payé (qui ajoute celui de
  // l'intercommunalité) : c'est celui dont le conseil municipal décide.
  {
    const lignesTaux = tauxAvecAvant2021(lire('fiscalite.json', { taux: [] }))
      .filter((t) => t.insee === insee && t.taux != null)
    const taux = lignesTaux
      .filter((t) => t.indicateur === 'TFB_VOTE')
      .sort((a, b) => a.annee - b.annee)
      .map((t) => ({ x: t.annee, y: t.taux }))
    // Part départementale de 2020, passée à la commune en 2021 : sert à corriger
    // l'écart à travers la rupture (absente : la rupture est dite, pas chiffrée).
    const dep = lignesTaux.find((t) => t.indicateur === INDICATEUR_DEP && t.annee === ANNEE_RUPTURE - 1)?.taux ?? null
    if (taux.length) {
      const t = taux.at(-1)
      const evolution = evolutionTuile(taux, dep)
      tuiles.push({
        cle: 'foncier-bati', href: '/impots', libelle: 'Taxe foncière',
        valeur: `${decimal(t.y)} %`,
        note: `taux voté par la commune en ${t.x}, ${evolution}`,
        // Un taux est VOTÉ pour l'année entière : des marches, pas une pente
        // d'une année à l'autre. Une année absente coupe le trait (`annuel`).
        // L'axe part des bornes rondes du taux, dites sous le graphique : depuis
        // zéro, un relèvement d'un point sur quarante-quatre ne se verrait pas.
        // Depuis 2021 seulement : avant, le taux voté n'incluait pas la part du
        // département (cf. rupture.js), la marche de 2021 se lirait comme une hausse.
        graphe: tracable(pointsATracer(taux)) && {
          type: 'courbe', escalier: true, annuel: true,
          series: [{ ton: 'ardoise', points: pointsATracer(taux) }],
          ...echelleRonde(pointsATracer(taux).map((p) => p.y), '%'),
        },
      })
    }
  }

  // ── 5. Marchés attribués ────────────────────────────────────────────────
  // Le compte et la raison de son zéro sont ceux de l'accueil d'avant
  // (`chiffres.marches`, `sourceMarches`, `extraitsMarches`) : rien n'est
  // recalculé ici, seules les barres s'ajoutent.
  if (chiffres.marches != null) {
    const marches = lire('marches.json', { marches: [] }).marches || []
    const points = parAnnee(
      marches.filter((m) => (!m.portee || m.portee === 'commune') && estAttribue(m))
        .map((m) => m.date_notif), anneeCourante)
    const n = chiffres.marches
    const raisons = []
    if (n === 0) {
      raisons.push(sourceMarches === 'absente' ? 'collecte pas encore lancée'
                                               : `aucun marché de la commune au portail national (publication obligatoire à partir de 40 000 € HT)`)
    }
    const lus = n === 0 && extraitsMarches?.cas === 'en_attente' ? extraitsMarches.enAttente : 0
    tuiles.push({
      cle: 'marches', href: '/marches',
      libelle: n > 1 ? 'Marchés attribués' : 'Marché attribué',
      valeur: nombre(n), vide: n === 0,
      raison: raisons[0] || null,
      // La formule est celle de /marches, et la CI la cherche sur l'accueil.
      note: lus ? `${nombre(lus)} lu${s(lus)} dans les procès-verbaux, qui ${lus > 1 ? 'attendent leur' : 'attend sa'} relecture`
          : n > 0 ? `par la commune ; sous 40 000 € HT, seuls ceux qu'un procès-verbal relu mentionne` : null,
      aussi: interco?.marches
        ? `intercommunalité : ${nombre(interco.marches)} marché${s(interco.marches)} attribué${s(interco.marches)}` : null,
      graphe: n > 0 && tracable(points) ? { type: 'barres', points } : null,
    })
  }

  // ── 6. Ventes foncières ─────────────────────────────────────────────────
  {
    // DVF n'arrive qu'en comptes annuels : une date par mutation les remet
    // dans la forme que `parAnnee` attend.
    const dates = (lire('dvf.json', {}).par_annee || [])
      .flatMap((a) => Array(a.mutations).fill(String(a.annee)))
    const points = parAnnee(dates, anneeCourante)
    if (points.length) {
      const p = derniereEntiere(points)
      tuiles.push({
        cle: 'ventes', href: '/urbanisme', libelle: `Vente${s(p.y)} immobilière${s(p.y)}`,
        valeur: nombre(p.y), note: `${p.x}, valeurs foncières (DVF)`,
        graphe: tracable(points) && { type: 'barres', points },
      })
    }
  }

  // ── 7. Acteurs recensés ─────────────────────────────────────────────────
  // Le même ensemble que l'annuaire (cf. +page.server.js) : la barre le
  // découpe par nature, sans rien y ajouter.
  if (chiffres.acteurs != null) {
    const parts = [
      { label: 'entreprises', n: chiffres.entreprises },
      { label: 'associations', n: chiffres.associations },
      { label: 'services publics', n: chiffres.services },
      { label: 'lieux', n: chiffres.lieux },
    ].filter((p) => p.n > 0)
    tuiles.push({
      cle: 'acteurs', href: '/acteurs-publics', libelle: 'Acteurs recensés',
      valeur: nombre(chiffres.acteurs),
      note: chiffres.associations ? `dont ${nombre(chiffres.associations)} associations` : null,
      graphe: parts.length > 1 ? { type: 'parts', parts } : null,
    })
  }

  // ── 8 et 9. Habitants, résidences secondaires ───────────────────────────
  {
    const lignes = (lire('territoire.json', { insee: [] }).insee || [])
      .filter((r) => r.insee === insee && r.valeur != null)
    const serie = (code) => lignes.filter((r) => r.indicateur === code)
      .sort((a, b) => String(a.annee).localeCompare(String(b.annee)))
      .map((r) => ({ x: Number(r.annee), y: r.valeur }))
    const pop = serie('POP')
    const legale = serie('POPREF_PMUN').at(-1) || pop.at(-1)
    if (legale) {
      tuiles.push({
        cle: 'habitants', href: '/territoire', libelle: `Habitants en ${legale.x}`,
        valeur: nombre(legale.y),
        note: pop.length > 1 ? `Insee, courbe de ${pop[0].x} à ${pop.at(-1).x}` : 'Insee',
        graphe: tracable(pop) && { type: 'courbe', depuisZero: true,
                                   series: [{ ton: 'ardoise', points: pop }] },
      })
    }
    const logements = serie('DWELLINGS')
    const part = serie('DWELLINGS_DW_SEC_DW_OCC')
      .map((p) => {
        const total = logements.find((l) => l.x === p.x)?.y
        return total ? { x: p.x, y: (100 * p.y) / total } : null
      })
      .filter(Boolean)
    if (part.length) {
      const p = part.at(-1)
      tuiles.push({
        cle: 'secondaires', href: '/territoire#logement', libelle: 'Résidences secondaires',
        valeur: `${nombre(p.y)} %`,
        note: `des logements, ${p.x}, Insee`,
        graphe: tracable(part) && { type: 'courbe', depuisZero: true,
                                    series: [{ ton: 'ardoise', points: part }] },
      })
    }
  }

  // ── 10. Prix de l'eau ───────────────────────────────────────────────────
  // Un service par courbe. Deux services peuvent desservir la même commune à
  // des prix différents (cf. /environnement) : la tuile donne alors les deux
  // bornes, jamais une moyenne que personne ne paie.
  //
  // Les services d'AUJOURD'HUI seulement : ceux de la composition communale
  // SISPEA la plus récente (`derniere_composition`, écrit par
  // collectors/sispea.py). La régie de Lasalle, reprise par la communauté de
  // communes en 2023, n'y est plus : son prix de 2022 n'est celui de personne.
  // Elle reste sur /environnement, qui est l'historique. Un fichier publié
  // sans ce champ garde tous les services, faute de mieux.
  {
    const env = lire('environnement.json', {})
    const aep = (env.sispea_services || []).filter((x) => x.competence === 'AEP')
    const composition = (x) => Number(x.derniere_composition) || 0
    const recente = Math.max(0, ...aep.map(composition))
    const actuels = recente ? aep.filter((x) => composition(x) === recente) : aep
    const services = actuels
      .map((x) => (env.sispea_indicateurs || [])
        .filter((i) => i.code_service === x.code_service && i.code === 'D102.0' && i.valeur != null)
        .sort((a, b) => a.annee - b.annee)
        .map((i) => ({ x: i.annee, y: i.valeur })))
    const series = services.filter((points) => points.length)
    // Un service qui dessert la commune sans prix publié (Saillans : le secteur
    // de Véronne, « en attente de saisie ») se dit, il ne disparaît pas. Sans
    // la composition, rien ne dit qu'il dessert encore la commune : on se tait.
    const sansPrix = recente ? services.length - series.length : 0
    // Une courbe par service à trois points au moins, comme les autres tuiles.
    const tracees = series.filter((points) => points.length >= 3)
    // Plusieurs prix, une seule courbe : la note dit de quel service elle est,
    // sinon on la lit comme celle de toute la commune. Par sa nature
    // (`natureDuService`), et seulement si elle le distingue des autres
    // services qui ont un prix ; sinon, la note se tait.
    const natures = services.map((points, i) => points.length ? natureDuService(actuels[i]) : null)
    const laNature = tracees.length === 1 ? natures[services.indexOf(tracees[0])] : null
    const laCourbe = series.length > 1 && laNature
      && natures.filter((n) => n === laNature).length === 1 && laNature
    if (series.length) {
      const derniers = series.map((points) => points.at(-1))
      const prix = derniers.map((p) => p.y).sort((a, b) => a - b)
      const annees = derniers.map((p) => p.x).sort()
      tuiles.push({
        cle: 'eau', href: '/environnement#eau-du-robinet', libelle: "Prix de l'eau",
        valeur: series.length > 1 ? `${decimal(prix[0])} à ${decimal(prix.at(-1))} €`
                                  : `${decimal(prix[0])} €`,
        note: `le m³ toutes taxes comprises, ${annees[0] === annees.at(-1) ? annees[0] : `${annees[0]} et ${annees.at(-1)}`}`
            + (series.length > 1 ? `, ${series.length} services` : '')
            + (sansPrix ? `, ${sansPrix > 1 ? `${sansPrix} autres services` : 'un autre service'}`
                          + ' sans prix publié' : '')
            + (laCourbe ? `, courbe\u00a0: ${laCourbe}` : ''),
        // `annuel` : une année sans prix coupe le trait (cf. Mini.svelte).
        graphe: tracees.length > 0 && {
          type: 'courbe', depuisZero: true, annuel: true,
          series: tracees.map((points) => ({ ton: 'ardoise', points })),
        },
      })
    }
  }

  // ── 11. Permis et déclarations ──────────────────────────────────────────
  {
    const points = parAnnee(
      (lire('urbanisme.json', { autorisations: [] }).autorisations || [])
        .filter((a) => !a.insee || a.insee === insee).map((a) => a.date_depot),
      anneeCourante)
    // Le total de la période tracée, pas la dernière année : une commune de
    // cette taille dépose une poignée de demandes par an, et les dernières
    // années du fichier sont encore incomplètes.
    const total = points.reduce((t, p) => t + p.y, 0)
    if (total) {
      tuiles.push({
        cle: 'autorisations', href: '/urbanisme', libelle: "Autorisations d'urbanisme",
        valeur: nombre(total), note: `déposées de ${points[0].x} à ${points.at(-1).x}`,
        graphe: tracable(points) && { type: 'barres', points },
      })
    }
  }

  // ── 12. Actes avec leur pièce ───────────────────────────────────────────
  // Ce que le site a pu rattacher à un document : sa propre mesure, à côté de
  // celles de la commune.
  {
    const c = lire('couverture.json', {})
    if (c.part_avec_piece != null) {
      tuiles.push({
        cle: 'pieces', href: '/couverture', libelle: 'Actes avec leur pièce',
        valeur: `${nombre(c.part_avec_piece)} %`,
        note: c.actes_total ? `sur ${nombre(c.actes_total)} actes publiés` : null,
        graphe: { type: 'jauge', part: c.part_avec_piece / 100 },
      })
    }
  }

  // Les quatre qui parlent le plus à un habitant passent en tête, dans cet
  // ordre, pour les trois instances ; les autres suivent dans l'ordre d'écriture
  // et portent `repli: true` (le rendu les replie). Une tuile absente laisse sa
  // place à la suivante : le repli commence au cinquième rang réel.
  const TETE = ['foncier-bati', 'budget', 'eau', 'habitants']
  const ordre = [
    ...TETE.map((c) => tuiles.find((x) => x.cle === c)).filter(Boolean),
    ...tuiles.filter((x) => !TETE.includes(x.cle)),
  ]
  return ordre.map((x, rang) => ({ ...x, repli: rang >= TETE.length }))
}
