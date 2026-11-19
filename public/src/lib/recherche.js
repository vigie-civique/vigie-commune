// La recherche du site public : des MOTS, et un score calculé à la requête.
//
// Relevé du 13/11/2026 (constat d'un habitant : résultats hors sujet, résultats
// absents). La page cherchait la saisie ENTIÈRE comme une suite de lettres :
// « eau » trouvait « bureau » et « Château », « sainte croix » ne trouvait pas
// « Sainte-Croix », « école cantine » exigeait ces deux mots collés dans cet
// ordre. L'ordre des résultats était fixé à la construction de l'index, par
// catégorie : un acteur dont le nom contenait les lettres passait toujours
// devant l'acte qui répondait à la question.
//
// Ici : la saisie est découpée en mots ; chaque mot doit commencer un mot de
// l'entrée (jamais son milieu) ; le score préfère le mot entier, le titre
// court, la suite de mots exacte ; la catégorie ne départage plus que les
// ex æquo. Module sans dépendance ni Svelte : il se teste avec `node` seul
// (tests/test_recherche_site.py).

// Casse, accents, apostrophes (droite et typographique), tirets et ponctuation
// repliés : « L’École » et « l'ecole » se rejoignent, « Sainte-Croix » se lit
// en deux mots.
export function plier(s) {
  return String(s ?? '')
    .toLowerCase().replace(/œ/g, 'oe').replace(/æ/g, 'ae')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

// Mots qui ne disent rien du sujet. Tapés seuls, ils sont gardés : on ne
// vide pas une requête.
const VIDES = new Set(('le la les l de des du d un une et en au aux a ou que qui '
  + 'pour par sur dans avec ce cette ces son sa ses leur leurs').split(' '))

// Singulier et pluriel se rejoignent : « écoles » trouve « école », « travaux »
// et « travail » restent distincts (on ne racinise pas, on retire une marque).
const racine = (m) => (m.length > 3 && /[sx]$/.test(m) ? m.slice(0, -1) : m)

// « 15 000 » et « 15 000 € » : un montant tapé avec son espace de milliers.
const joindreMilliers = (s) => s.replace(/(\d)[ \u00a0\u202f](?=\d{3}(?!\d))/g, '$1')

// La saisie, en mots cherchables.
export function motsDeRequete(q) {
  const tous = plier(joindreMilliers(String(q ?? ''))).split(' ').filter(Boolean)
  const utiles = tous.filter((m) => !VIDES.has(m) && (m.length > 1 || /\d/.test(m)))
  return (utiles.length ? utiles : tous).map(racine)
}

// Ce qu'on cherche dans une entrée, calculé une fois et gardé sur elle.
// `t` le titre, `s` le sigle d'un acteur. Le champ `c` n'est cherché que là où
// il nomme quelqu'un (l'acheteur d'un marché, le payeur d'un versement) : pour
// un acte il porte le domaine du site source, et taper le nom de la commune
// ramenait tout ce qui en venait.
function preparer(e) {
  if (e._r) return e._r
  const titre = plier(e.t).split(' ').filter(Boolean).map(racine)
  const sigle = plier(e.s).split(' ').filter(Boolean)
  const contexte = (e.k === 'marche' || e.k === 'versement')
    ? plier(e.c).split(' ').filter(Boolean).map(racine) : []
  const r = { titre, sigle, contexte, phrase: ' ' + titre.join(' ') + ' ' }
  Object.defineProperty(e, '_r', { value: r, enumerable: false })
  return r
}

// Le texte des actes (`recherche_texte.json`, { mot: [identifiants d'actes] }).
// L'index ne porte que des mots courants écrits en minuscules dans l'acte :
// aucun nom de personne ne s'y cherche (scripts/snapshot/recherche.py). Pour
// chaque mot tapé, les actes dont le texte porte un mot qui commence ainsi.
const clesDuTexte = new WeakMap()
function actesParMot(mots, texte) {
  if (!texte) return null
  let cles = clesDuTexte.get(texte)
  if (!cles) { cles = Object.keys(texte); clesDuTexte.set(texte, cles) }
  return mots.map((m) => {
    const actes = new Set()
    if (m.length < 3) return actes
    for (const k of cles) {
      if (k.startsWith(m)) for (const i of texte[k]) actes.add(i)
    }
    return actes
  })
}

// Force d'un mot de la requête dans une entrée : 0 s'il n'y est pas.
function forceDuMot(m, e, r) {
  let f = 0
  for (const w of r.titre) {
    if (w === m) { f = 3; break }
    if (w.startsWith(m)) f = Math.max(f, 2)
  }
  if (f < 3 && r.sigle.includes(m)) f = 3
  if (!f) {
    for (const w of r.contexte) {
      if (w === m || w.startsWith(m)) { f = 1; break }
    }
  }
  if (!f && /^\d+$/.test(m)) {
    // Un nombre : le montant (entier, sans les centimes), ou l'année de la date.
    if (e.m != null && String(Math.round(Number(e.m))) === m) f = 3
    else if (m.length === 4 && String(e.d ?? '').startsWith(m)) f = 1
  }
  return f
}

// Score d'une entrée pour une liste de mots ; `trouves` = combien y sont ;
// `texte` = un mot au moins n'est que dans le texte de l'acte, pas dans son titre.
function noter(mots, e, parTexte) {
  const r = preparer(e)
  let score = 0, trouves = 0, texte = false
  mots.forEach((m, n) => {
    let f = forceDuMot(m, e, r)
    // Un mot du texte pèse moins qu'un mot du titre : le titre dit le sujet,
    // le texte le mentionne.
    if (!f && parTexte && e.i != null && parTexte[n].has(e.i)) { f = 1; texte = true }
    if (f) { trouves += 1; score += f }
  })
  if (!trouves) return { score: 0, trouves: 0, texte }
  // La suite exacte des mots tapés, quand il y en a plusieurs.
  if (mots.length > 1 && r.phrase.includes(' ' + mots.join(' '))) score += 3
  // Un titre court que la requête couvre répond mieux qu'un long qui la cite.
  score += Math.min(1, trouves / Math.max(1, r.titre.length))
  // La catégorie (poids `n` de l'index : dossier, séance relue, acteur cité…)
  // ne fait que départager : moins d'un point, jamais plus qu'un mot trouvé.
  score += Math.min(0.9, (e.n || 0) / 2500)
  return { score, trouves, texte }
}

// Cherche `q` dans `index`. Rend { resultats, partiel } :
//  - tous les mots trouvés, du mieux noté au moins bien ;
//  - à défaut d'entrée qui les contienne tous, celles qui en contiennent le
//    plus (`partiel` vrai : la page le dit, plutôt que « rien ne correspond »).
// `filtre` écarte une entrée avant toute notation (catégorie, commune).
// `texte` : les mots de `recherche_texte.json`, quand il est chargé ;
// `dansLeTexte` rend alors les actes trouvés par leur texte et non leur titre.
export function chercher(index, q, filtre = () => true, texte = null) {
  const mots = motsDeRequete(q)
  const candidats = index.filter(filtre)
  const dansLeTexte = new Set()
  if (!mots.length) return { resultats: candidats, partiel: false, mots, dansLeTexte }
  const parTexte = actesParMot(mots, texte)
  const notes = []
  let meilleur = 0
  for (const e of candidats) {
    const n = noter(mots, e, parTexte)
    if (!n.trouves) continue
    if (n.trouves > meilleur) meilleur = n.trouves
    notes.push({ e, ...n })
  }
  const partiel = meilleur > 0 && meilleur < mots.length
  const resultats = notes
    .filter((x) => x.trouves === meilleur)
    .sort((a, b) => b.score - a.score
      || String(b.e.d ?? '').localeCompare(String(a.e.d ?? ''))
      || String(a.e.t ?? '').localeCompare(String(b.e.t ?? ''), 'fr'))
    .map((x) => { if (x.texte) dansLeTexte.add(x.e); return x.e })
  return { resultats, partiel, mots, dansLeTexte }
}

// Pour les champs de filtre d'une liste (délibérations, annuaire) : tous les
// mots tapés commencent un mot du texte. Même repli que la recherche : « ecole »
// y trouve « école ».
export function contientLesMots(texte, q) {
  const mots = motsDeRequete(q)
  if (!mots.length) return true
  const w = plier(texte).split(' ').map(racine)
  return mots.every((m) => w.some((x) => x.startsWith(m)))
}
