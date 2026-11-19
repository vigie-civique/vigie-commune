// Le menu de l'atelier, et ce que chaque rôle peut y ouvrir.
//
// Un seul registre : le menu latéral le lit pour se dessiner, l'accueil pour ne
// pas renvoyer quelqu'un vers une page qui lui est fermée. Le droit, lui, reste
// tenu par l'API ; ici on évite seulement d'ouvrir une page qui refuserait tout.
import { auMoins, LIBELLE_ROLE } from './roles.js'

// `min` : le rôle à partir duquel la page sert à quelque chose.
// `grise` : sous ce rôle l'entrée reste au menu, éteinte, avec sa raison écrite
// dessous (le contributeur sait que la page existe et à qui la demander). Sans
// `grise`, elle disparaît. `ia` : s'éteint tant qu'aucun modèle n'est branché.
export const GROUPES = [
  { titre: '', entrees: [
    { href: '/atelier', label: "Aujourd'hui" },
  ] },
  { titre: 'À relire', entrees: [
    { href: '/atelier/propositions',    label: 'Propositions' },
    { href: '/atelier/relations',       label: 'Liens présumés', min: 'validator', grise: true },
    { href: '/atelier/queue/websites',  label: 'Adresses de sites' },
    { href: '/atelier/geo',             label: 'Points sur la carte' },
    { href: '/atelier/donnees',         label: 'Chiffres à confirmer' },
  ] },
  { titre: 'Fiches et données', entrees: [
    { href: '/atelier/fiches',          label: 'Toutes les fiches' },
    { href: '/atelier/saisie?objet=entite', label: 'Nouvelle fiche' },
    { href: '/atelier/saisie',          label: 'Saisir une donnée' },
    { href: '/atelier/ia',              label: 'Recherche par sens', ia: true },
  ] },
  { titre: 'Dossiers', entrees: [
    { href: '/atelier/dossiers',        label: 'Dossiers' },
    { href: '/atelier/taches',          label: 'Ce que les dossiers ne savent pas' },
    { href: '/atelier/conseils',        label: 'Conseils en clair' },
    { href: '/atelier/analyses',        label: 'Analyses croisées', min: 'validator' },
  ] },
  { titre: 'Site public', entrees: [
    { href: '/atelier/publication',     label: 'Publication', min: 'validator', grise: true },
    { href: '/atelier/journal',         label: 'Journal', min: 'validator', grise: true },
  ] },
  { titre: 'Administration', entrees: [
    { href: '/atelier/comptes',         label: 'Comptes', min: 'admin' },
  ] },
]

const TOUTES = GROUPES.flatMap(g => g.entrees)

// Pourquoi une entrée est éteinte : `null` = elle disparaît, '' = elle est ouverte.
export function raison(n, user, ia) {
  if (n.min && !auMoins(user, n.min)) {
    return n.grise ? `Réservé au rôle ${LIBELLE_ROLE[n.min].toLowerCase()} et au-dessus.` : null
  }
  // L'admin garde l'entrée : c'est de cette page qu'il branche un modèle.
  if (n.ia && ia === false && !auMoins(user, 'admin')) return "Aucun modèle n'est branché sur cet atelier."
  return ''
}

// Le menu tel que ce compte le voit : les groupes, sans les entrées masquées.
export function menuPour(user, ia) {
  return GROUPES
    .map(g => ({ ...g, entrees: g.entrees
      .map(n => ({ ...n, raison: raison(n, user, ia) }))
      .filter(n => n.raison !== null) }))
    .filter(g => g.entrees.length)
}

// Cette page est-elle ouverte à ce rôle ? Une page absente du menu l'est (la
// file « Toutes les fiches » liste, elle, tout à tous).
export function pageOuverte(route, user) {
  const n = TOUTES.find(e => e.href === route)
  return !n || !n.min || auMoins(user, n.min)
}

export function rolePour(route) {
  return TOUTES.find(e => e.href === route)?.min ?? 'contributor'
}
