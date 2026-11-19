// Le dossier remis doit marcher par un DOUBLE-CLIC sur une page, sans rien
// installer ni débloquer.
//
// Ouverte depuis le disque (`file://`), une page a pour origine « null », et le
// navigateur lui refuse deux choses dont le site vit :
//
//   1. charger un module ES (`import()`) — l'amorce SvelteKit n'en fait pas
//      d'autre : aucune hydratation, donc aucun sélecteur, filtre, recherche
//      ni carte ;
//   2. tout `fetch` — l'index de recherche, les couches et le fond de la carte,
//      le texte d'un acte.
//
// Le dossier embarquait donc un lanceur (`ouvrir-le-dossier.command` / `.bat`)
// qui le servait sur la boucle locale. Or macOS bloque un exécutable
// téléchargé (Gatekeeper : Réglages › Confidentialité › « Ouvrir quand même »),
// Windows le signale (SmartScreen) et n'a en général pas Python, Linux ouvre un
// `.command` dans un éditeur. Le lecteur non technicien s'arrêtait là, sur un
// site qui s'affiche et ne répond à rien (01/11/2026).
//
// Ce script, lancé APRÈS l'adaptateur sur un build `VIGIE_HORS_LIGNE=1`, lève
// les deux refus sans serveur :
//
//   1. tout le JavaScript de l'application est regroupé en UN script ordinaire,
//      `_app/double-clic.js`, que chaque page charge par `<script src>` — ce
//      que le navigateur accepte depuis le disque ;
//   2. chaque fichier chargé à la demande reçoit un jumeau `.js` qui se déclare
//      dans `window.__vigie_fichiers` ; `src/lib/charger.js` le lit au
//      double-clic à la place du `fetch`.
//
// Servi par un serveur (le site n'est jamais construit ainsi pour la mise en
// ligne, mais le dossier peut l'être), tout marche encore : le script ordinaire
// s'exécute pareil, et `charger.js` revient au `fetch`.
//
// Éprouvé par `scripts/verifier_hors_ligne.mjs`, en `file://`, page par page.
import { build } from 'vite'
import {
  existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { estALaDemande, estBinaire } from '../src/lib/fichiers-a-la-demande.js'

export const SCRIPT_UNIQUE = '_app/double-clic.js'
const ENTREE_TEMPORAIRE = '_app/__entree-double-clic.js'
const CALE_TEMPORAIRE = '_app/__cale-double-clic.js'

function* fichiers(dossier) {
  for (const nom of readdirSync(dossier)) {
    const chemin = join(dossier, nom)
    if (statSync(chemin).isDirectory()) yield* fichiers(chemin)
    else yield chemin
  }
}

const versPosix = (chemin) => chemin.split(sep).join('/')

/**
 * Le seul `fetch` admis côté navigateur est celui de `charger.js` : un autre
 * casserait le double-clic sans bruit. Les fichiers `.server.js`, `+server.js`
 * et `+page.server.js` ne tournent qu'au prérendu, sous Node.
 */
export function fetchHorsDuChargeur(src) {
  const fautes = []
  for (const chemin of fichiers(src)) {
    if (!/\.(js|ts|svelte)$/.test(chemin)) continue
    if (/(\.server\.(js|ts)|\+server\.(js|ts))$/.test(chemin)) continue
    if (chemin.endsWith(`${sep}charger.js`)) continue
    const lignes = readFileSync(chemin, 'utf8').split('\n')
    lignes.forEach((ligne, i) => {
      const code = ligne.replace(/\/\/.*$/, '')
      if (/\bfetch\s*\(/.test(code) && !/^\s*\*/.test(code)) {
        fautes.push(`${versPosix(relative(src, chemin))}:${i + 1}`)
      }
    })
  }
  return fautes
}

/** Repère, dans le build, les deux entrées que l'amorce SvelteKit importe. */
function entrees(dossier) {
  const rep = join(dossier, '_app', 'immutable', 'entry')
  const noms = readdirSync(rep)
  const trouver = (prefixe) => {
    const n = noms.filter((x) => x.startsWith(prefixe) && x.endsWith('.js'))
    if (n.length !== 1) throw new Error(`${prefixe}*.js : ${n.length} candidat(s) dans ${rep}`)
    return `./immutable/entry/${n[0]}`
  }
  return { start: trouver('start.'), app: trouver('app.') }
}

async function regrouper(dossier) {
  const { start, app } = entrees(dossier)

  // La cale passe AVANT l'application (un `import` est évalué dans l'ordre).
  //
  // Vite précharge la feuille de style d'un composant chargé à la demande en
  // posant `crossOrigin = ""` sur la balise `<link>`. Depuis le disque, une
  // requête « CORS » vers un fichier est refusée : la promesse de chargement
  // échoue, et avec elle l'hydratation de la page entière (« Unable to preload
  // CSS »). Sans l'attribut, la même feuille se charge. La cale ne vaut qu'en
  // `file://` et que pour les `<link>`.
  writeFileSync(join(dossier, CALE_TEMPORAIRE), `
if (location.protocol === 'file:') {
  const d = Object.getOwnPropertyDescriptor(HTMLLinkElement.prototype, 'crossOrigin')
  Object.defineProperty(HTMLLinkElement.prototype, 'crossOrigin', {
    configurable: true, get: d.get, set() {},
  })
}
// L'adresse d'un module, à laquelle Vite résout les chemins relatifs de ses
// dépendances : celle d'un fichier de \`immutable/entry/\`, d'où le build les a
// écrits.
window.__vigie_url_module = new URL('immutable/entry/', document.currentScript.src).href

// Les liens. Le site les écrit en absolu (\`/argent\`), et l'hydratation les
// REPOSE ainsi, par-dessus les chemins relatifs que la mise en forme d'archive
// avait écrits dans le HTML ; la barre de navigation, elle, n'existe qu'en
// JavaScript. Au double-clic, \`/argent\` désigne la racine du DISQUE. Tant que
// la page ne s'hydratait pas, le défaut ne se voyait pas : il est né avec le
// double-clic (01/11/2026).
//
// Chaque lien interne absolu est donc réécrit vers le fichier du dossier, au
// chargement puis à chaque ajout ou changement : un clic, un « ouvrir dans un
// nouvel onglet » et l'adresse affichée au survol mènent au bon fichier.
if (location.protocol === 'file:') {
  const racine = new URL('../', document.currentScript.src)
  const versFichier = (href) => {
    const u = new URL(href, 'http://dossier')
    let chemin = u.pathname
    if (!/\\.[a-z0-9]+$/i.test(chemin)) chemin = chemin.replace(/\\/?$/, '/') + 'index.html'
    return new URL('.' + chemin, racine).href + u.search + u.hash
  }
  const corriger = (a) => {
    const h = a.getAttribute('href')
    if (h && h[0] === '/' && h[1] !== '/') a.setAttribute('href', versFichier(h))
  }
  const balayer = (noeud) => {
    if (noeud.nodeType !== 1) return
    if (noeud.matches('a[href]')) corriger(noeud)
    noeud.querySelectorAll('a[href]').forEach(corriger)
  }
  new MutationObserver((changements) => {
    for (const c of changements) {
      if (c.type === 'attributes') corriger(c.target)
      else c.addedNodes.forEach(balayer)
    }
  }).observe(document.documentElement,
    { subtree: true, childList: true, attributes: true, attributeFilter: ['href'] })
  document.addEventListener('DOMContentLoaded', () => balayer(document.documentElement))
  balayer(document.documentElement)
}
`)
  writeFileSync(join(dossier, ENTREE_TEMPORAIRE), `
import './${CALE_TEMPORAIRE.split('/').pop()}'
import * as kit from '${start}'
import * as app from '${app}'
window.__vigie_kit = kit
window.__vigie_app = app
`)
  try {
    await build({
      configFile: false,
      logLevel: 'warn',
      root: dossier,
      // `import.meta` n'existe pas dans un script ordinaire.
      define: { 'import.meta.url': 'window.__vigie_url_module' },
      build: {
        outDir: join(dossier, '_app'),
        emptyOutDir: false,
        copyPublicDir: false,
        minify: true,
        lib: {
          entry: join(dossier, ENTREE_TEMPORAIRE),
          formats: ['iife'],
          name: '__vigie_double_clic',
          fileName: () => SCRIPT_UNIQUE.split('/').pop(),
        },
        rolldownOptions: { output: { codeSplitting: false } },
      },
    })
  } finally {
    rmSync(join(dossier, ENTREE_TEMPORAIRE), { force: true })
    rmSync(join(dossier, CALE_TEMPORAIRE), { force: true })
  }
  const sortie = join(dossier, SCRIPT_UNIQUE)
  if (!existsSync(sortie)) throw new Error(`${SCRIPT_UNIQUE} n'a pas été écrit`)
  const code = readFileSync(sortie, 'utf8')
  // Un `import(` resté dans le script le ferait échouer au double-clic.
  if (/\bimport\s*\(\s*["'`]/.test(code)) {
    throw new Error(`${SCRIPT_UNIQUE} contient encore un import() : le regroupement est incomplet`)
  }
  return code.length
}

const PRECHARGEMENT_RE = /<link\b[^>]*\brel="modulepreload"[^>]*>\s*/g
const IMPORT_START_RE = /import\(\s*"[^"]*\/_app\/immutable\/entry\/start\.[^"]+\.js"\s*\)/
const IMPORT_APP_RE = /await import\(\s*"[^"]*\/_app\/immutable\/entry\/app\.[^"]+\.js"\s*\)/

/** Chaque page charge le script unique, et son amorce ne fait plus d'import(). */
function reecrirePages(dossier) {
  let pages = 0
  const intactes = []
  for (const chemin of fichiers(dossier)) {
    if (!chemin.endsWith('.html')) continue
    const html = readFileSync(chemin, 'utf8')
    if (!html.includes('/_app/immutable/entry/start.')) continue
    if (!IMPORT_START_RE.test(html) || !IMPORT_APP_RE.test(html)) {
      intactes.push(versPosix(relative(dossier, chemin)))
      continue
    }
    const vers = versPosix(relative(resolve(chemin, '..'), join(dossier, SCRIPT_UNIQUE)))
    const reecrit = html
      .replace(PRECHARGEMENT_RE, '')
      .replace(IMPORT_START_RE, 'Promise.resolve(window.__vigie_kit)')
      .replace(IMPORT_APP_RE, 'window.__vigie_app')
      // Le script unique avant l'amorce : c'est elle qui l'utilise.
      .replace(/<script>(\s*\{\s*__sveltekit_)/, `<script src="${vers}"></script><script>$1`)
    if (!reecrit.includes(`<script src="${vers}">`)) {
      intactes.push(versPosix(relative(dossier, chemin)))
      continue
    }
    writeFileSync(chemin, reecrit)
    pages++
  }
  // Une page que la réécriture n'a pas reconnue resterait inerte au
  // double-clic. SvelteKit a changé la forme de son amorce : on s'arrête.
  if (intactes.length) {
    throw new Error(`amorce non reconnue dans ${intactes.length} page(s) : `
      + intactes.slice(0, 5).join(', '))
  }
  return pages
}

/** Le jumeau `.js` de chaque fichier chargé à la demande. */
function ecrireJumeaux(dossier) {
  let n = 0
  for (const chemin of [...fichiers(dossier)]) {
    const rel = versPosix(relative(dossier, chemin))
    if (!estALaDemande(rel)) continue
    const contenu = estBinaire(rel)
      ? JSON.stringify(readFileSync(chemin).toString('base64'))
      // Relu puis réécrit : un JSON invalide doit casser ici, pas chez le lecteur.
      : JSON.stringify(JSON.parse(readFileSync(chemin, 'utf8')))
    writeFileSync(`${chemin}.js`,
      `(window.__vigie_fichiers||={})[${JSON.stringify(rel)}]=${contenu};\n`)
    n++
  }
  return n
}

export async function rendreLisibleAuDoubleClic(dossier, src) {
  dossier = resolve(dossier)
  if (src) {
    const fautes = fetchHorsDuChargeur(resolve(src))
    if (fautes.length) {
      throw new Error('fetch() hors de src/lib/charger.js — il casserait le double-clic :\n  '
        + fautes.join('\n  '))
    }
  }
  const octets = await regrouper(dossier)
  const pages = reecrirePages(dossier)
  const jumeaux = ecrireJumeaux(dossier)
  console.log(`double-clic : ${pages} pages sur ${SCRIPT_UNIQUE} `
    + `(${Math.round(octets / 1024)} Ko), ${jumeaux} fichier(s) à la demande doublé(s) en .js`)
  return { pages, jumeaux, octets }
}

// Lancé seul : node scripts/pour_le_double_clic.mjs <dossier>
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  rendreLisibleAuDoubleClic(process.argv[2] || 'build', 'src')
    .catch((e) => { console.error('\n✖ ' + e.message + '\n'); process.exit(1) })
}
