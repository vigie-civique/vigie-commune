// Index des actes institutionnels : un millésime par page.
//
// La page unique embarquait les 592 actes complets — 586 Ko de HTML, dont
// 271 Ko de payload d'hydratation, pour une liste que personne ne déroule
// jusqu'à 2016. Le découpage par année donne aussi à chaque millésime une URL
// stable, citable et archivable, ce qui est le point d'un site de vigie.
//
// Cette page-ci ne porte que le sommaire. La recherche, transverse aux années,
// lit l'index des titres dans `/deliberations-index.json`, chargé à la première
// frappe : embarqué ici, il pesait 583 Ko de script d'hydratation sur 615 Ko de
// page (audit UX du 25/10/2026).
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { INSTITUTIONAL, anneeDe, ancreActe, estDecision, estSeance, instanceDe } from '#lib/actes.js'
import { DATA_DIR, lireJSON } from '#lib/donnees.server.js'
import { etatSource } from '#lib/couverture.js'

export const prerender = true

export function load() {
  let d = { events: [] }
  try {
    d = JSON.parse(readFileSync(join(DATA_DIR, 'events.json'), 'utf8'))
  } catch { /* snapshot absent : page vide plutôt que build cassé */ }

  const actes = (d.events || []).filter((e) => INSTITUTIONAL[e.type])

  const parAnnee = new Map()
  for (const e of actes) {
    const a = anneeDe(e)
    if (!parAnnee.has(a)) parAnnee.set(a, { annee: a, total: 0, cm: 0, cc: 0 })
    const g = parAnnee.get(a)
    // Une décision par délégation n'est pas un acte d'assemblée : elle a sa
    // tuile, pas sa place dans « délibérations et séances en {année} ».
    if (estDecision(e)) continue
    g.total++
    if (instanceDe(e) === 'CM') g.cm++
    else g.cc++
  }

  // Années récentes d'abord ; « sans-date » en dernier plutôt qu'en tête, où le
  // tri alphabétique la placerait.
  const annees = [...parAnnee.values()].sort((a, b) =>
    a.annee === 'sans-date' ? 1 : b.annee === 'sans-date' ? -1 : b.annee.localeCompare(a.annee))

  // Constat sur les votes.
  //
  // Attention au piège : `vote.unanimite` n'est vrai que lorsque le compte rendu
  // emploie le mot « unanimité ». Beaucoup d'actes portent un décompte (13 pour,
  // 0 contre, 0 abstention) sans ce mot : les compter comme « divisés » ferait
  // passer 104 délibérations pour contestées alors qu'aucune ne l'est. Un vote
  // n'est divisé que s'il y a au moins un contre ou une abstention.
  const divise = (v) => Boolean(v && (v.contre || v.abstention))
  const avecVote = actes.filter((e) => e.vote && !estSeance(e) && !estDecision(e))
  const divises = avecVote.filter((e) => divise(e.vote))

  const anneeCourante = new Date().toISOString().slice(0, 4)

  return {
    // Un dossier national exclut par conception les collecteurs de PV : sans
    // cette information la page affichait « 0 acte » comme un résultat.
    source: etatSource(lireJSON('couverture.json', {}), 'deliberations'),
    annees,
    total: actes.filter((e) => !estDecision(e)).length,
    nCM: actes.filter((e) => instanceDe(e) === 'CM').length,
    nCC: actes.filter((e) => instanceDe(e) === 'CC').length,
    delibCM: actes.filter((e) => instanceDe(e) === 'CM' && !estSeance(e) && !estDecision(e)).length,
    delibCC: actes.filter((e) => instanceDe(e) === 'CC' && !estSeance(e) && !estDecision(e)).length,
    decisions: actes.filter(estDecision).length,
    seances: actes.filter(estSeance).length,
    anneeCourante,
    nCourante: parAnnee.get(anneeCourante)?.total ?? 0,
    votes: avecVote.length ? {
      connus: avecVote.length,
      sansOpposition: avecVote.length - divises.length,
      divises: divises.length,
      // Les rares actes qui ont fait débat, remontés plutôt que noyés : c'est
      // sur eux qu'un conseil municipal se lit.
      exemples: divises
        .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
        .slice(0, 5)
        .map((e) => ({
          id: e.id, ancre: ancreActe(e), date: e.date, titre: e.title,
          annee: anneeDe(e), vote: e.vote,
        })),
    } : null,
  }
}
