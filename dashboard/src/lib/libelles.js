// Les mots de l'atelier, en français, à UN seul endroit.
//
// Une page qui a besoin de dire « personne », « Probable » ou « la
// commune » l'importe d'ici ; elle n'a pas sa propre table. Les codes
// (`person`, `probable`, `C1`) restent ceux de la base et de l'API : ils ne
// s'affichent jamais. Les trois axes d'une donnée (origine, fiabilité,
// verdict) sont décrits dans `axes.js`, qui reprend la fiabilité d'ici.

// ── Types de fiche ──────────────────────────────────────────────────────────
export const LIBELLE_TYPE = {
  person: 'personne', business: 'entreprise', association: 'association',
  place: 'lieu', service: 'service public',
}
export const libelleType = t => LIBELLE_TYPE[t] ?? t ?? 'non renseigné'

// ── Fiabilité : ce que la machine sait d'une donnée ─────────────────────────
// `unverified` n'existe que pour les notes libres.
export const FIABILITE = {
  verified: 'Sûre', confirmed: 'Confirmée', probable: 'Probable',
  hypothesis: 'Hypothèse', unverified: 'Non vérifiée',
}
export const FIABILITE_AIDE = {
  verified: "Source officielle, ou posée par un validateur. Publiée si les règles l'admettent.",
  confirmed: 'Recoupée, ou saisie avec sa source. Publiée.',
  probable: "Déduite par croisement, ou écrite par un contributeur : ne sort pas sur le site tant qu'un validateur ne l'a pas retenue.",
  hypothesis: 'Supposée, à vérifier : ne sort pas sur le site.',
  unverified: "Pas encore vérifiée : ne sort pas sur le site.",
}
export const libelleFiabilite = c => FIABILITE[c] ?? c ?? 'non renseignée'

// ── Choix des formulaires de saisie : les codes de la base, dits en clair ────
export const CHOIX = {
  verse: 'Versé par la commune', recu: 'Reçu par la commune',
  vote: 'Voté', engage: 'Engagé', paye: 'Payé', demande: 'Seulement demandé', annule: 'Annulé',
  commune: 'La commune', epci: "L'intercommunalité",
}
export const libelleChoix = c => CHOIX[c] ?? c

// Les choix d'un menu de fiabilité, dans l'ordre du plus au moins sûr.
export const choixFiabilite = codes => codes.map(c => ({ cle: c, libelle: FIABILITE[c] ?? c }))

// ── Périmètre : le territoire d'une fiche ───────────────────────────────────
// Le code (C1, C2, C3, lien) est celui de la base ; l'écran dit la chose.
export const PERIMETRE_LIBELLE = {
  C1: 'Commune', C2: 'Intercommunalité', C3: 'Au-dessus de la commune', lien: 'Rattachée',
}
export const perimetreLibelle = p => PERIMETRE_LIBELLE[p] ?? 'Non classée'
// Une phrase, visible sans survol : ce que ce territoire contient et ce qu'on en publie.
export const PERIMETRE_AIDE = {
  C1: 'La commune elle-même : le cœur du projet.',
  C2: "L'intercommunalité et ses autres communes. Collectées pour comparer, publiées seulement en total.",
  C3: "Préfecture, département, région, agences de l'État.",
  lien: "Hors du territoire, mais liée à un acteur suivi (société d'un élu, titulaire d'un marché).",
  '': 'Tous les territoires, y compris les fiches pas encore classées.',
}
export const PERIMETRE_NON_CLASSE =
  "Le territoire de cette fiche n'est pas encore déterminé. Cela ne change rien à son contenu."

// ── Relations ───────────────────────────────────────────────────────────────
// Le type d'une relation s'écrit déjà en français dans la base, avec des « _ ».
export const libelleRelation = t => (t ?? '').replace(/_/g, ' ')

// La source d'une relation ou d'une ligne : `manual` est le mot de la base.
export const SOURCE_LIBELLE = { manual: 'saisie à la main', manuel: 'saisie à la main' }
export const libelleSource = s => SOURCE_LIBELLE[s] ?? s ?? ''

// ── Erreurs : une phrase qui dit quoi faire ─────────────────────────────────
// L'API répond un code ; le bénévole n'a pas à le lire. Le message de l'API,
// quand il existe, reste prioritaire : il sait mieux que nous ce qui s'est passé.
export function phraseStatut(status) {
  const s = Number(status)
  if (s === 401) return 'Votre session a expiré. Reconnectez-vous.'
  if (s === 403) return "Votre rôle ne permet pas ce geste. Demandez à un validateur ou à l'administrateur."
  if (s === 404) return "Cet élément n'existe plus ou n'a jamais existé. Revenez à la liste."
  if (s === 409) return "Quelqu'un a modifié cet élément entre-temps. Rechargez la page avant de reprendre."
  if (s === 413) return 'Le fichier est trop gros.'
  if (s === 422 || s === 400) return "Une valeur n'est pas acceptée. Relisez les champs et réessayez."
  if (s === 429) return 'Trop de demandes à la suite. Attendez une minute, puis réessayez.'
  if (s >= 500) return "Le serveur a rencontré un problème. Réessayez dans un instant ; si cela dure, prévenez l'administrateur."
  return "Le serveur n'a pas répondu comme prévu. Réessayez dans un instant."
}

// Une exception de l'atelier devient une phrase. Elle peut porter `status`
// (api.js), ou un message qui n'est que le code (« 500 »), ou une vraie phrase
// (« Session expirée », « Failed to fetch »).
export function phraseErreur(e) {
  const code = e?.status ?? (/^\d{3}\b/.test(e?.message ?? '') ? Number(e.message.slice(0, 3)) : null)
  if (code) {
    const d = e?.detail
    const detail = typeof d === 'object' ? d?.message : d
    return typeof detail === 'string' && detail && !/^\s*[{[]/.test(detail)
      ? detail : phraseStatut(code)
  }
  const m = e?.message ?? ''
  if (/failed to fetch|networkerror|load failed/i.test(m)) {
    return 'Le serveur ne répond pas. Vérifiez votre connexion, puis réessayez.'
  }
  return m || "Une erreur est survenue. Réessayez dans un instant."
}

// Une réponse HTTP en échec devient une Error dont le message est une phrase.
export function erreurHttp(res) {
  const e = new Error(phraseStatut(res.status))
  e.status = res.status
  return e
}

// Le motif donné par l'API (`detail`, chaîne ou objet), sinon une phrase tirée
// du code. `quoi` : ce qui a échoué, au passé composé (« L'enregistrement a échoué »).
export async function motifReponse(res, quoi) {
  const d = (await res.json().catch(() => ({}))).detail
  const m = typeof d === 'object' ? d?.message : d
  return typeof m === 'string' && m ? m : `${quoi}. ${phraseStatut(res.status)}`
}

// ── Phrases que l'API écrit pour l'écran ────────────────────────────────────
// Certains textes (collectors/files.py) sont rédigés côté serveur. Celui-ci
// dit encore « Vous proposez ; un validateur tranche » alors qu'un contributeur
// écrit directement sur une fiche non publiable : on le reformule à l'affichage.
export const REGLE_CONTRIBUTEUR =
  "Sur une fiche que le site publie, votre correction part en proposition : un validateur la relit avant qu'elle s'applique. Sur une fiche que le site ne publie pas, elle s'applique tout de suite, et rien n'en sort en ligne."
export function texteApi(s) {
  return (s ?? '').replace(/Vous proposez ; un validateur (confirme|tranche)\./g,
    'Sur une fiche publiée, une proposition est relue par un validateur ; sur une fiche non publiée, elle s’applique tout de suite.')
}
