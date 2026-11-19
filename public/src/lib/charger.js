// Le seul endroit du site qui va chercher un fichier après le chargement.
//
// Servi (le site en ligne, ou le dossier remis servi par un serveur local) :
// un `fetch`, comme avant.
//
// Ouvert par un double-clic (`file://`) : le navigateur refuse TOUT `fetch` —
// la recherche restait vide, la carte sans repères ni fond, et rien ne le
// disait. Le dossier remis porte donc, pour chaque fichier chargé à la
// demande, un jumeau `<fichier>.js` qui se déclare dans `window.__vigie_fichiers`
// (écrit par `scripts/pour_le_double_clic.mjs`). Un `<script>` ordinaire, lui,
// se charge depuis le disque.
//
// Un chemin absent de `A_LA_DEMANDE` est REFUSÉ, en ligne comme hors-ligne :
// un nouveau fichier oublié dans la liste casserait le double-clic sans bruit ;
// refusé partout, il casse dès qu'un développeur ouvre la page.
import { asset } from '$app/paths'
import { estALaDemande, estBinaire } from './fichiers-a-la-demande.js'

const auDoubleClic = () =>
  __VIGIE_HORS_LIGNE__ && typeof location !== 'undefined' && location.protocol === 'file:'

const enCours = new Map()

function parScript(chemin) {
  if (!enCours.has(chemin)) {
    enCours.set(chemin, new Promise((rendre, refuser) => {
      const recus = (window.__vigie_fichiers ||= {})
      const balise = document.createElement('script')
      balise.src = asset(chemin + '.js')
      balise.onload = () => (chemin in recus
        ? rendre(recus[chemin])
        : refuser(new Error(`${chemin} : le jumeau ne déclare rien`)))
      balise.onerror = () => {
        enCours.delete(chemin)
        refuser(new Error(`${chemin} : absent du dossier`))
      }
      document.head.appendChild(balise)
    }))
  }
  return enCours.get(chemin)
}

function verifier(chemin) {
  if (!estALaDemande(chemin)) {
    throw new Error(`${chemin} n'est pas déclaré dans src/lib/fichiers-a-la-demande.js`)
  }
}

/** @param {string} chemin relatif à la racine du site, sans `/` initial */
export async function chargerJSON(chemin) {
  verifier(chemin)
  if (auDoubleClic()) return parScript(chemin)
  const r = await fetch(asset(chemin))
  if (!r.ok) throw new Error(`${chemin} : HTTP ${r.status}`)
  return r.json()
}

/** @param {string} chemin relatif à la racine du site, sans `/` initial */
export async function chargerOctets(chemin) {
  verifier(chemin)
  if (auDoubleClic()) {
    if (!estBinaire(chemin)) throw new Error(`${chemin} n'est pas déclaré binaire`)
    const texte = atob(await parScript(chemin))
    const octets = new Uint8Array(texte.length)
    for (let i = 0; i < texte.length; i++) octets[i] = texte.charCodeAt(i)
    return octets.buffer
  }
  const r = await fetch(asset(chemin))
  if (!r.ok) throw new Error(`${r.status} sur ${chemin}`)
  return r.arrayBuffer()
}
