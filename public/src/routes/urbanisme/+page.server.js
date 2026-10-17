// Mutations foncières DVF, en agrégats seulement : le snapshot ne publie plus
// aucune vente à l'unité (cf. scripts/snapshot/argent.py, `agreger_dvf`).
// Lu dans le snapshot au build — cf. marches/+page.server.js pour le motif.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DATA_DIR } from '#lib/donnees.server.js'

export const prerender = true

const lire = (nom, defaut = {}) => {
  try { return JSON.parse(readFileSync(join(DATA_DIR, nom), 'utf8')) }
  catch { return defaut }  // snapshot absent : page vide plutôt que build cassé
}

export function load() {
  const d = lire('dvf.json', {})
  const dvf = {
    synthese: d.synthese || { mutations: 0 },
    parAnnee: d.par_annee || [],
    parNature: d.par_nature || [],
    seuil: d.seuil_mediane ?? 5,
  }

  // Le document d'urbanisme, tel que le Géoportail le connaît. Trois cas, et
  // il faut les distinguer à l'écran : la commune est au RNU (aucun document
  // local), elle a déposé un document, ou elle n'a rien déposé sans être au
  // RNU — ce dernier cas étant celui qui mérite une question.
  const u = lire('urbanisme.json', {})
  const statut = (u.statut || [])[0] || null
  const documents = u.documents || []
  // Les parts ne sont publiées que si le collecteur a vérifié que le document
  // couvre bien la commune. Il les laisse à NULL sinon : la page doit alors
  // dire pourquoi, pas afficher un vide.
  const zonage = (u.zonage || []).filter((z) => z.part_pct != null)
  const couverture = (u.zonage || [])[0]?.couverture ?? null

  // Constat sur le marché du bâti — et surtout sur ce qu'on ne peut PAS en
  // conclure. Avec vingt à quarante ventes par an, la médiane saute de 630 à
  // 1 284 €/m² d'une année sur l'autre : c'est du bruit d'échantillon, pas un
  // mouvement de marché. Afficher la courbe sans le dire inviterait à lire une
  // tendance là où il n'y en a pas de mesurable.
  // Seules les années dont la médiane est publiée entrent dans la fourchette :
  // sous le seuil, le snapshot la laisse vide.
  const annees = dvf.parAnnee
    .filter((a) => a.mediane_m2 != null)
    .map((a) => ({ annee: String(a.annee), n: a.ventes_bati, mediane: a.mediane_m2 }))

  const medianes = annees.map((a) => a.mediane)

  return {
    dvf,
    urbanisme: { statut, documents, zonage, couverture },
    constat: annees.length > 1 ? {
      annees,
      ventes: annees.reduce((t, a) => t + a.n, 0),
      minEchantillon: Math.min(...annees.map((a) => a.n)),
      maxEchantillon: Math.max(...annees.map((a) => a.n)),
      basse: Math.min(...medianes),
      haute: Math.max(...medianes),
    } : null,
  }
}
