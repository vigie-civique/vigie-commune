// Index des titres d'actes, pour la recherche de /deliberations.
//
// Il était embarqué dans la page : 583 Ko de script d'hydratation, pour un
// lecteur sur deux qui ne tape rien. La page le demande à la première frappe.
// Prérendu comme le flux RSS : un fichier statique, aucune route dynamique.
//
// Strictement ce qu'affiche une ligne de résultat.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { INSTITUTIONAL, anneeDe, ancreActe, instanceDe } from '#lib/actes.js'
import { DATA_DIR } from '#lib/donnees.server.js'

export const prerender = true

export function GET() {
  let d = { events: [] }
  try {
    d = JSON.parse(readFileSync(join(DATA_DIR, 'events.json'), 'utf8'))
  } catch { /* snapshot absent : index vide plutôt que build cassé */ }

  const index = (d.events || [])
    .filter((e) => INSTITUTIONAL[e.type])
    .map((e) => ({
      id: e.id,
      ancre: ancreActe(e),
      date: e.date || null,
      titre: e.title || '',
      annee: anneeDe(e),
      instance: instanceDe(e),
      label: INSTITUTIONAL[e.type].label,
    }))
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''))

  return new Response(JSON.stringify(index), {
    headers: { 'content-type': 'application/json; charset=utf-8' },
  })
}
