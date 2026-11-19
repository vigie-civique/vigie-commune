// Dates en français, avec « 1er ».
//
// `toLocaleDateString('fr-FR')` écrit « 1 octobre » ou « 1er octobre » selon la
// version d'ICU de la machine qui prérend : le site affichait « relue le 1
// octobre 2026 » (audit UX du 25/10/2026). L'ordinal est posé ici, une fois,
// plutôt que laissé au moteur du build.
//
// `d` est une date ISO (AAAA-MM-JJ) ; une valeur illisible est rendue telle
// quelle, jamais « Invalid Date ».
export function dateFr(d, options = { day: 'numeric', month: 'long', year: 'numeric' }) {
  if (!d) return ''
  const dt = new Date(d + 'T00:00:00')
  if (Number.isNaN(dt.getTime())) return d
  return new Intl.DateTimeFormat('fr-FR', options)
    .formatToParts(dt)
    .map((p) => (p.type === 'day' && Number(p.value) === 1 ? '1er' : p.value))
    .join('')
}
