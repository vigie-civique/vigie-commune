// Chargement des données statiques exportées par build_public_snapshot.py
// Servies depuis /data/ (public/static/data/). Aucun backend. Le chargement
// passe par `charger.js`, qui sait aussi lire le dossier ouvert au double-clic.
import { chargerJSON } from './charger.js'

export const loadJSON = (name) => chargerJSON(`data/${name}`)

export const TYPE_LABELS = {
  person: 'Personne',
  business: 'Entreprise',
  association: 'Association',
  service: 'Service public',
  place: 'Lieu',
  property: 'Propriété',
}

export function euros(n) {
  if (n == null) return '-'
  return new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(n)
}
