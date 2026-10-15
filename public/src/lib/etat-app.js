// `$page` et `$updated` pour les composants écrits à l'ancienne (sans runes).
//
// SvelteKit 3 a retiré `$app/stores`. Ses remplaçants, dans `$app/state`, ne se
// suivent que sous les runes : un `$: id = page.params.id` d'un composant
// « legacy » ne verrait jamais la navigation suivante — et une fiche, une
// décision ou un résultat de recherche resteraient ceux de la page d'avant.
// `toStore` (Svelte 5) les rend en stores, que `$page` relit comme autrefois.
//
// À appeler PENDANT l'initialisation du composant : côté serveur, l'état de la
// page n'existe que le temps d'une requête.
//
//   import { pageStore } from '#lib/etat-app'
//   const page = pageStore()
import { page, updated } from '$app/state'
import { toStore } from 'svelte/store'

export function pageStore() {
  // Lire chaque champ : c'est ce qui abonne le store à leurs changements.
  return toStore(() => ({
    url: page.url, params: page.params, route: page.route, status: page.status,
    error: page.error, data: page.data, form: page.form, state: page.state,
  }))
}

export function updatedStore() {
  return toStore(() => updated.current)
}
