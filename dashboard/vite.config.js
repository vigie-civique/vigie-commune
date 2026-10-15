import adapter from '@sveltejs/adapter-static'
import { sveltekit } from '@sveltejs/kit/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  // La configuration de SvelteKit vit ici depuis SvelteKit 3 (15/10/2026) :
  // `svelte.config.js` n'est plus lu.
  plugins: [
    sveltekit({
      adapter: adapter({
        pages: 'dist',
        assets: 'dist',
        fallback: 'index.html',
        precompress: false,
        strict: false
      }),
      paths: {
        base: ''
      },
      // Le build SIGNE son bloc d'amorçage (une empreinte dans une balise
      // <meta> de la page) : c'est ce qui permet d'interdire tout autre script
      // écrit dans la page, sans `'unsafe-inline'`. Une balise injectée dans
      // une donnée affichée ne s'exécute plus, même si un échappement est
      // oublié. Le reste de la politique (cadres, images, connexions) est posé
      // par le serveur — cf. deploy/nginx-atelier.conf — qui laisse
      // `script-src` ici.
      csp: {
        mode: 'hash',
        directives: { 'script-src': ['self'] }
      }
    })
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // `127.0.0.1` et non `localhost` : sur macOS, `localhost` résout
        // d'abord en IPv6 (`::1`), alors qu'`uvicorn` n'écoute par défaut qu'en
        // IPv4. Le proxy visait donc une adresse où rien n'écoutait, et
        // l'atelier affichait « Serveur inaccessible » avec les deux services
        // pourtant démarrés et une API qui répondait très bien — le diagnostic
        // le plus coûteux qui soit, puisque tout a l'air en ordre.
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      }
    }
  }
})
