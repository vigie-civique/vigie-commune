// Constat sur les VERSEMENTS de la commune (pur : testé par tests/test_finances_constat.py).

// Un bénéficiaire sans nom : flux délié (particulier, entreprise individuelle
// sans fait public, fiche écartée) ou nom inconnu de la source.
// Même règle que `beneficiaire_inconnu` (scripts/snapshot/argent.py), strip compris.
export const unknownName = (n) => ['', '?', '∅'].includes(String(n ?? '').trim())

// Piège à ne pas reproduire : une cession de patrimoine est une VENTE. Le
// flux est stocké commune → acquéreur (le sens du bien), mais l'argent va
// dans l'autre sens — la commune encaisse. Compter les cessions parmi les
// versements ferait apparaître l'acquéreur d'un terrain communal comme
// premier bénéficiaire de l'argent public alors qu'il a payé la commune.
export const estVersement = (f) =>
  f.sens === 'sortant' && f.amount && f.etat !== 'demande' && f.etat !== 'annule'
  && !/cession/i.test(f.type || '')

export function constat(flows) {
  const versements = flows.filter(estVersement)
  if (!versements.length) return null

  // Les flux sans nom ne forment PAS un bénéficiaire : regroupés sous une clé
  // nulle, ils comptaient pour un seul « bénéficiaire » et faussaient la part
  // des 80 %. Ils sont comptés à part (nombre de paiements, montant).
  const nommes = versements.filter((f) => !unknownName(f.to_name))
  const nonNommes = versements.filter((f) => unknownName(f.to_name))

  const total = versements.reduce((s, f) => s + f.amount, 0)
  const totalNommes = nommes.reduce((s, f) => s + f.amount, 0)
  const parBenef = new Map()
  for (const f of nommes) parBenef.set(f.to_name, (parBenef.get(f.to_name) || 0) + f.amount)
  const classement = [...parBenef.values()].sort((a, b) => b - a)

  // Combien de bénéficiaires NOMMÉS réunissent 80 % des montants qui leur vont.
  let cumul = 0, pour80 = 0
  for (const v of classement) {
    cumul += v; pour80++
    if (cumul >= totalNommes * 0.8) break
  }

  const annees = [...new Set(versements.map((f) => f.year).filter(Boolean))].sort()
  return {
    nb: versements.length,
    total,
    totalNommes,
    beneficiaires: parBenef.size,
    pour80,
    nonNommes: { nb: nonNommes.length, total: nonNommes.reduce((s, f) => s + f.amount, 0) },
    periode: annees.length ? [annees[0], annees[annees.length - 1]] : null,
  }
}

// La phrase sur les flux sans nom (texte brut ; `fmt` formate un montant).
// Sans bénéficiaire nommé, il n'y a ni « reste » ni bénéficiaires « ci-dessus ».
export function phraseNonNommes(c, fmt) {
  const { nb, total } = c.nonNommes
  if (!nb) return ''
  const cause = 'des bénéficiaires non nommés (nom non publié sur ce site, ou absent de la source)'
  const flux = `${nb} flux`
  if (!c.beneficiaires) return `Aucun bénéficiaire nommé : les ${fmt(total)} (${flux}) vont tous à ${cause}.`
  return `Les ${fmt(total)} restants (${flux}) vont à ${cause} : ils ne comptent pas parmi les bénéficiaires ci-dessus.`
}
