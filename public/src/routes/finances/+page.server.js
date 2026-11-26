// Flux financiers (subventions, versements).
// Lu dans le snapshot au build — cf. marches/+page.server.js pour le motif.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DATA_DIR } from '#lib/donnees.server.js'
import { constat } from './constat.js'

export const prerender = true

const lire = (nom, defaut = {}) => {
  try { return JSON.parse(readFileSync(join(DATA_DIR, nom), 'utf8')) }
  catch { return defaut }  // snapshot absent : page vide plutôt que build cassé
}

export function load() {
  const d = lire('flows.json', [])
  const flows = Array.isArray(d) ? d : d.flows || []

  return {
    flows,
    constat: constat(flows),
  }
}
