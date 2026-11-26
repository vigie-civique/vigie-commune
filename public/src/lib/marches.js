// Un marché ATTRIBUÉ a un titulaire et un montant ; tout le reste — avis de
// marché, de publicité, d'intention de conclure, résultat sans titulaire ou
// sans montant — est un AVIS PUBLIÉ, jamais un marché.
// Un seul prédicat pour tout le site : l'accueil annonçait « 58 marchés »
// quand /marches n'en comptait aucun d'attribué (audit du 24/09/2026).
//
// Un titulaire MASQUÉ (`titulaire_masque`) est une entreprise individuelle que
// le filtre de publication ne nomme pas : le marché reste attribué et compté,
// sans nom (brief `ei-flux-marches`, avis vigie-dpo du 25/11/2026).
export const estAttribue = (m) => Boolean((m.titulaire_nom || m.titulaire_masque) && m.montant)

export const TITULAIRE_MASQUE = 'une entreprise individuelle (non nommée)'

// Ce qu'on affiche pour le titulaire.
export const nomTitulaire = (m) => m.titulaire_nom || (m.titulaire_masque ? TITULAIRE_MASQUE : '')

// La clé qui regroupe les marchés d'un même titulaire. Les titulaires masqués ne
// se regroupent pas entre eux : chacun compte à part, sinon tous formeraient un
// seul titulaire et fausseraient la concentration.
export const cleTitulaire = (m) => (m.titulaire_masque ? `masque:${m.id}` : m.titulaire_nom)
