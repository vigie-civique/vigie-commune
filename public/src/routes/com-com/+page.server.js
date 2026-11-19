// Intercommunalité : compétences, délégués, actes.
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
  // Les actes de la communauté sont les `deliberation_cc` : c'est le type que la
  // collecte écrit (`TYPES_DELIBERES`, scripts/snapshot/actes.py). L'ancien filtre
  // cherchait des types qui n'existent pas (`délibérations_cc`, `pv_cc`) et se
  // rabattait sur « communaut » dans le titre : il servait des délibérations
  // MUNICIPALES qui citent la communauté, et aucune de celles de la communauté.
  const events = (lire('events.json', { events: [] }).events || [])
    .filter((x) => x.type === 'deliberation_cc')
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
    .slice(0, 40)

  return { events, cc: lire('intercommunalite.json', {}) }
}
