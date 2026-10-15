import adapter from '@sveltejs/adapter-static'
import { sveltekit } from '@sveltejs/kit/vite'
import { defineConfig } from 'vite'
import { createReadStream, cpSync, existsSync, statSync } from 'node:fs'
import { join, normalize, resolve, sep } from 'node:path'

// L'aperçu de l'atelier construit le site sur le BROUILLON (`VIGIE_DATA_DIR`),
// mais les fichiers servis sous `/data/` viennent de `static/data`, c'est-à-dire
// de ce qui est déjà en ligne. Un lien vers une donnée NOUVELLE du brouillon
// (une feuille « en clair » tout juste retenue) n'y est pas encore : le
// prérendu le voyait en 404 et refusait tout l'aperçu (01/10/2026). Il n'est
// toléré que si le fichier existe dans le brouillon — l'atelier le recopie dans
// l'aperçu après le build. Un build de production n'a pas `VIGIE_DATA_DIR` :
// rien n'y change, et tout lien mort y reste bloquant.
const BROUILLON = process.env.VIGIE_DATA_DIR

function dansLeBrouillon(path) {
  if (!BROUILLON || !path.startsWith('/data/')) return false
  const rel = normalize(decodeURIComponent(path.slice('/data/'.length)))
  return !rel.startsWith('..') && existsSync(join(BROUILLON, rel))
}

/**
 * L'aperçu de l'atelier fait tourner CE site sur un snapshot brouillon.
 *
 * Les pages prérendues lisent déjà `VIGIE_DATA_DIR` (cf. `src/lib/donnees.server.js`),
 * mais une partie des données part en `fetch('/data/…')` depuis le navigateur, et
 * celles-là sortiraient de `static/data` — le snapshot PUBLIÉ. Sans ce raccord,
 * l'aperçu mélangerait deux snapshots sans le dire, ce qui est pire que pas
 * d'aperçu du tout : on croirait avoir vérifié.
 *
 * Variable absente — tout build de production, toute la CI — le plugin ne fait
 * rien et le site lit `static/` comme avant.
 */
function donneesBrouillon() {
  const declare = process.env.VIGIE_DATA_DIR
  const racine = declare ? resolve(declare) : null

  return {
    name: 'vigie-donnees-brouillon',

    // Aperçu vivant : `vite dev` sert /data/ depuis le brouillon.
    configureServer(server) {
      if (!racine) return
      server.middlewares.use('/data', (req, res, next) => {
        const demande = decodeURIComponent((req.url || '/').split('?')[0])
        const cible = resolve(join(racine, normalize(demande)))
        // Vérifié SOUS la racine : `/data/../../.env` ne sort pas du snapshot.
        if (cible !== racine && !cible.startsWith(racine + sep)) {
          res.statusCode = 403
          return res.end('hors du snapshot')
        }
        if (!existsSync(cible) || !statSync(cible).isFile()) return next()
        res.setHeader('Content-Type',
          cible.endsWith('.geojson') || cible.endsWith('.json')
            ? 'application/json; charset=utf-8'
            : 'application/octet-stream')
        createReadStream(cible).pipe(res)
      })
    },

    // Aperçu figé : un `npm run build` avec la variable produit un site dont les
    // pages ET les fichiers `/data/` viennent du brouillon. Sans ça, le build
    // embarquerait `static/data`, c'est-à-dire le snapshot publié.
    closeBundle() {
      if (!racine || !existsSync(racine)) return
      const sortie = join(process.cwd(), process.env.VIGIE_BUILD_DIR || 'build', 'data')
      cpSync(racine, sortie, { recursive: true, force: true })
    },
  }
}

export default defineConfig({
  // La configuration de SvelteKit vit ici depuis SvelteKit 3 (15/10/2026) :
  // `svelte.config.js` n'est plus lu.
  plugins: [
    sveltekit({
      // `VIGIE_BUILD_DIR` sert à construire un aperçu figé sans écraser le
      // build de production ; non définie, rien ne change.
      adapter: adapter({
        pages:      process.env.VIGIE_BUILD_DIR || 'build',
        assets:     process.env.VIGIE_BUILD_DIR || 'build',
        fallback:   '404.html',
        precompress: false,
        strict:     false,
      }),

      // Détection des nouvelles versions du site. Sans ce réglage, un onglet
      // resté ouvert garde le JavaScript de la version qu'il a chargée : à la
      // publication suivante, les fragments qu'il réclame sous
      // `/_app/immutable/` sont empreintés et n'existent plus chez l'hébergeur,
      // la navigation interne échoue en silence, et le lecteur croit le site
      // cassé. Le client interroge `_app/version.json` toutes les cinq
      // minutes ; le layout force un vrai chargement au clic suivant.
      version: { pollInterval: 300000 },

      prerender: {
        handleHttpError: ({ path, message }) => {
          if (dansLeBrouillon(path)) return
          throw new Error(message)
        },
      },
    }),
    donneesBrouillon(),
  ],

  // Le build SAIT s'il fabrique le site en ligne ou le dossier remis.
  //
  // Une seule chose en dépend — la forme des adresses écrites sur le disque,
  // cf. src/routes/+layout.js — mais elle ne peut pas se décider à l'exécution :
  // `trailingSlash` est lu au prérendu, quand les fichiers sont nommés.
  //
  // Variable absente, la constante vaut `false` : tout build de production et
  // toute la CI produisent exactement ce qu'ils produisaient.
  define: {
    __VIGIE_HORS_LIGNE__: JSON.stringify(Boolean(process.env.VIGIE_HORS_LIGNE)),
  },
})
