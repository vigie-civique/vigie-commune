// Le lien vers le portail national, dont l'accueil ouvre sur l'aperçu communal (05/11/2026).
//
// Les lecteurs de la presse arrivent sur la vigie de Lasalle, et la plupart
// n'y habitent pas : rien ne les menait au portail, où ils peuvent voir leur
// propre commune (relevé du 05/11 : zéro passage de Lasalle vers le portail).
//
// L'étiquette `?de=` dit le SITE d'où l'on vient, jamais la personne : même
// forme que portail-local/app/lien-direct.ts ([a-z0-9-], 24 caractères au plus),
// tirée du premier élément du nom de domaine (lasalle.vigie-civique.fr →
// « lasalle »). Une forme non conforme est rangée « sans étiquette » par le
// portail, sans autre effet.
import { SITE_URL } from '#lib/instance.js'

const etiquette = (() => {
  try {
    return new URL(SITE_URL).hostname.split('.')[0].replace(/[^a-z0-9-]/g, '').slice(0, 24)
  } catch {
    return ''
  }
})()

export const PORTAIL_URL = `https://vigie-civique.fr/${etiquette ? `?de=${etiquette}` : ''}`
