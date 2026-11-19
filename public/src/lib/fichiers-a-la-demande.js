// Les fichiers que le navigateur va chercher APRÈS le chargement de la page.
//
// Tout le reste est dans le HTML prérendu. Ceux-ci ne le sont pas, par choix :
// trop lourds pour chaque page (l'index de recherche, le texte d'un acte, le
// fond de carte), ou utiles à une seule page qu'on n'ouvre pas toujours.
//
// Liste UNIQUE, lue à deux endroits qui ne doivent jamais diverger :
//
//   · `src/lib/charger.js`, qui refuse tout chemin absent d'ici — sur le site
//     en ligne aussi, pour qu'un oubli se voie dès la première ouverture et non
//     dans un dossier déjà remis ;
//   · `scripts/pour_le_double_clic.mjs`, qui écrit pour chacun de ces fichiers
//     un jumeau `.js` dans le dossier remis : ouvert par un double-clic
//     (`file://`), le navigateur refuse tout `fetch`, mais il charge encore un
//     `<script>` ordinaire.
//
// Sans dépendance : le script de build l'importe sous Node, hors de SvelteKit.
export const A_LA_DEMANDE = [
  /^data\/recherche_index\.json$/,
  /^data\/recherche_texte\.json$/,
  /^data\/layers\/[\w-]+\.geojson$/,
  /^data\/extrait\/[\w.-]+\.json$/,
  /^deliberations-index\.json$/,
  /^carte\/fond\.pmtiles$/,
]

export const estALaDemande = (chemin) => A_LA_DEMANDE.some((motif) => motif.test(chemin))

// Le jumeau d'un fichier binaire porte son contenu en base64 ; celui d'un JSON,
// le JSON lui-même (tout JSON est une expression JavaScript valide).
export const estBinaire = (chemin) => chemin.endsWith('.pmtiles')
