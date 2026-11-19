// Rupture de 2021 sur la taxe foncière bâtie : la part du département est
// passée à la commune. Le taux voté par la commune monte du taux départemental
// de 2020, sans que le contribuable paie davantage. Lire ce saut comme une
// hausse voterait la commune d'une décision qui n'est pas la sienne.
//
// Module pur (aucun accès fichier), partagé par la tuile de l'accueil
// (`lib/tableau.server.js`) et la page /impots, testé par `tests/test_impots_rupture.py`.

export const ANNEE_RUPTURE = 2021
// Indicateur du taux départemental 2020 dans fiscalite.json (brief
// fiscalite-taux-2015-2020) : à confirmer contre sa PR fusionnée.
export const INDICATEUR_DEP = 'TFB_DEP'

// Les taux s'affichent à deux décimales : « nul » veut dire nul à cet arrondi.
const r2 = (v) => Math.round(v * 100) / 100

// La rupture se corrige si la série porte 2020 ET 2021 et si le taux
// départemental 2020 est connu. Sinon, aucune estimation, aucune valeur en dur.
function correction(points, dep) {
  const franchit = points.some((p) => p.x < ANNEE_RUPTURE) && points.some((p) => p.x >= ANNEE_RUPTURE)
  const chiffrable = dep != null
    && points.some((p) => p.x === ANNEE_RUPTURE - 1) && points.some((p) => p.x === ANNEE_RUPTURE)
  return { franchit, chiffrable }
}

// Point `p` ramené au périmètre d'après 2021 (part du département comprise).
const comparable = (p, dep, chiffrable) => (p.x < ANNEE_RUPTURE && chiffrable ? p.y + dep : p.y)

// Dernier palier d'une série de taux votés [{x: année, y: taux}], triée.
// → { debut, avant, ecart, rupture }
//   debut  : année où le palier commence ;
//   avant  : point précédent (null si le taux n'a pas bougé sur toute la série) ;
//   ecart  : écart corrigé en points, null s'il ne peut pas être chiffré ;
//   rupture: null (hors sujet), 'neutre' (taux cumulé commune + département inchangé),
//            'corrigee' (palier de 2021, écart non nul hors transfert),
//            'non-chiffree'.
const p0 = (points, i) => points[i].x

export function palier(points, dep = null) {
  const fin = points.at(-1)
  const { franchit, chiffrable } = correction(points, dep)
  let i = points.length - 1
  while (i > 0) {
    const p = points[i - 1]
    // Sans correction possible, on ne compare pas à travers la rupture.
    if (p.x < ANNEE_RUPTURE && fin.x >= ANNEE_RUPTURE && !chiffrable) break
    if (r2(comparable(p, dep, chiffrable)) !== r2(fin.y)) break
    i--
  }
  const avant = i > 0 ? points[i - 1] : null
  const traverse = franchit && (avant ? avant.x < ANNEE_RUPTURE : points[0].x < ANNEE_RUPTURE)
  let ecart = null
  if (avant && !(traverse && !chiffrable)) ecart = r2(fin.y - comparable(avant, dep, chiffrable))
  if (!avant) ecart = 0
  let rupture = null
  // Un palier qui commence avant 2021 passe la rupture sans bouger (corrigée) :
  // son écart est celui d'avant 2021, déjà brut. Un palier qui commence en 2021
  // est celui d'un vrai changement de taux payé, mesuré hors transfert.
  if (traverse) rupture = !chiffrable ? 'non-chiffree' : p0(points, i) < ANNEE_RUPTURE ? 'neutre' : 'corrigee'
  return { debut: points[i].x, avant, ecart, rupture }
}

const decimal = (n) => n.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const FAIT = 'la part du département est passée à la commune'

// Chaîne d'évolution de la tuile. `fin` = dernier point de la série.
export function evolutionTuile(points, dep = null) {
  const fin = points.at(-1)
  const p = palier(points, dep)
  const pts = (e) => `${Math.abs(e) >= 2 ? 'points' : 'point'}`
  if (p.rupture === 'non-chiffree') {
    return `le taux voté change en ${ANNEE_RUPTURE} car ${FAIT}, taux transféré non recoupé, écart non chiffré`
  }
  if (!p.avant) {
    return p.rupture === 'neutre'
      ? `inchangé hors transfert de ${points[0].x} à ${fin.x} (en ${ANNEE_RUPTURE} ${FAIT}, taux cumulé commune et département inchangé)`
      : `inchangé de ${points[0].x} à ${fin.x}`
  }
  const sens = `${p.ecart > 0 ? 'relevé' : 'baissé'} en ${p.debut} (${p.ecart > 0 ? '+' : '−'}${decimal(Math.abs(p.ecart))} ${pts(p.ecart)}`
  if (p.rupture === 'corrigee') return `${sens}, hors transfert de la part du département en ${ANNEE_RUPTURE})`
  if (p.rupture === 'neutre') return `${sens}) ; en ${ANNEE_RUPTURE} ${FAIT}, taux cumulé commune et département inchangé`
  return `${sens})`
}

// Évolution relative de la page /impots entre la première et la dernière année.
// → { pct, rupture, debut } ; debut = première année de la série ; pct null si indéterminable ou si la rupture n'est pas chiffrable.
export function evolutionPage(points, dep = null) {
  if (points.length < 2) return { pct: null, rupture: null, debut: null }
  const a = points[0]
  const b = points.at(-1)
  const { franchit, chiffrable } = correction(points, dep)
  if (franchit && !chiffrable) return { pct: null, rupture: 'non-chiffree', debut: a.x }
  const base = comparable(a, dep, chiffrable)
  if (base === 0) return { pct: null, rupture: franchit ? 'corrigee' : null, debut: a.x }
  return { pct: ((b.y - base) / base) * 100, rupture: franchit ? 'corrigee' : null, debut: a.x }
}

// fiscalite.json sépare les lignes d'avant 2021 (TFB_VOTE 2015-2020, TFB_DEP 2020)
// dans `avant_2021`, clé absente quand elle est vide. Elles ont la forme de `taux`
// sans le nom de la commune, que la page retrouve par le code INSEE.
export function tauxAvecAvant2021(d) {
  const taux = d?.taux || []
  const avant = d?.avant_2021 || []
  if (!avant.length) return taux
  const noms = new Map(taux.filter((t) => t.commune).map((t) => [t.insee, t.commune]))
  return [...taux, ...avant.map((t) => ({ ...t, commune: t.commune ?? noms.get(t.insee) }))
    .filter((t) => t.commune)]
}

// Points à tracer : la série brute d'avant 2021 est dans un autre périmètre
// (sans la part du département) et ferait voir le transfert comme une marche.
// On ne trace que depuis 2021 ; l'avant se dit dans le texte.
export function pointsATracer(points) {
  return points.some((p) => p.x >= ANNEE_RUPTURE) ? points.filter((p) => p.x >= ANNEE_RUPTURE) : points
}
