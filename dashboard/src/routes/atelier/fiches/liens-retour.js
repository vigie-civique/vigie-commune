// Aller à une fiche, et en revenir là d'où l'on vient.
//
// La liste (filtres, nombre de pages chargées) et la recherche vivent dans
// l'adresse. Un lien vers une fiche emporte cette adresse dans `?de=` ; le bouton
// « retour » de la fiche la reprend, et les liens de fiche à fiche la gardent :
// après trois sauts de relation en relation, on revient encore à la liste.

// Seules les adresses de l'atelier sont reprises : `?de=` vient de l'extérieur.
export function retourValide(de) {
  return typeof de === 'string' && /^\/atelier\/(?!login|invitation)[\w\-/]*(\?.*)?$/.test(de)
    && !de.startsWith('//')
}

export function adresseFiche(id, de) {
  const base = `/atelier/entite/${id}`
  return retourValide(de) ? `${base}?de=${encodeURIComponent(de)}` : base
}

// Ce que dit le bouton de retour, selon la page d'où l'on vient.
export function etiquetteRetour(de) {
  if (!retourValide(de)) return '← File de travail'
  if (de.startsWith('/atelier/fiches/recherche')) return '← Résultats de la recherche'
  if (de.startsWith('/atelier/fiches')) return '← Toutes les fiches'
  return '← Page précédente'
}

// Les types de fiche : lib/libelles.js, seul endroit où ils s'écrivent.
export { LIBELLE_TYPE } from '#lib/libelles.js'

export const RETOUR_PAR_DEFAUT = '/atelier'
