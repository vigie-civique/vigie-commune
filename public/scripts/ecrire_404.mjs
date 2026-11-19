// Donne un contenu à `404.html`, joué après `vite build` (cf. package.json).
//
// `404.html` est le « fallback » de l'adaptateur statique : un squelette sans
// rien dans le <body> que le script qui monte l'application. Un lecteur qui
// suit un lien cassé SANS JavaScript — ou dont le script ne se charge pas —
// voyait une page blanche (audit UX du 25/10/2026, 2 144 o sans un mot).
//
// Le bloc est dans <noscript> : avec JavaScript, l'application monte et rend
// la page d'erreur de SvelteKit comme avant, rien ne double. Le titre est
// posé dans le <head> pour l'onglet et pour les lecteurs d'écran.
//
// Aucun nom de commune ici : le fichier est celui du moteur, partagé par les
// instances.
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const BUILD = process.env.VIGIE_BUILD_DIR || 'build'
const FICHIER = join(BUILD, '404.html')

export const MARQUEUR = 'Cette page n’existe pas ou a changé d’adresse.'

const TITRE = 'Page introuvable'

const BLOC = `<noscript>
      <main style="max-width:36rem;margin:3rem auto;padding:0 1rem;font-family:system-ui,sans-serif;line-height:1.5">
        <h1>${TITRE}</h1>
        <p>${MARQUEUR}</p>
        <p><a href="/">Retour à l’accueil</a> · <a href="/recherche">Chercher dans le site</a> · <a href="/conseils">Les conseils en clair</a></p>
      </main>
    </noscript>`

if (!existsSync(FICHIER)) {
  console.error(`ecrire_404 : ${FICHIER} introuvable — le build n'a pas produit de fallback`)
  process.exit(1)
}

let html = readFileSync(FICHIER, 'utf8')
if (html.includes(MARQUEUR)) process.exit(0)   // déjà écrit : le rejouer ne change rien

if (!html.includes('</head>') || !html.includes('</body>')) {
  console.error(`ecrire_404 : ${FICHIER} n'a pas la forme attendue (</head> ou </body> absent)`)
  process.exit(1)
}

if (!/<title>/.test(html)) {
  html = html.replace('</head>', `  <title>${TITRE}</title>\n  </head>`)
}
html = html.replace('</body>', `  ${BLOC}\n  </body>`)

writeFileSync(FICHIER, html)
console.log(`ecrire_404 : ${FICHIER} écrit`)
