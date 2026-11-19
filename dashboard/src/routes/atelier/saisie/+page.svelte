<script>
  // Saisie manuelle — ce qu'aucun collecteur ne peut deviner.
  //
  // 49 % des flux financiers de la production sont saisis à la main, dans onze
  // scripts Python écrits pour une commune et jamais rejoués ailleurs. Le budget
  // primitif voté n'existe que dans les comptes rendus du conseil : le détail
  // DGFiP s'arrête à 2023, les agrégats OFGL à 2024. Sans cette page, ce travail
  // se refait en écrivant du Python — ce qu'aucun repreneur ne fera.
  //
  // Ce qui est saisi ici part dans config/saisies.json et non directement en
  // base : le collecteur `saisies` le rejoue à chaque collecte, donc une
  // reconstruction complète de la base ne l'efface pas.
  import { api } from '#lib/api.js'
  import { goto } from '$app/navigation'
  import { pageStore } from '#lib/etat-app'
  const page = pageStore()
  import { currentUser } from '#lib/stores/auth.js'
  import { auMoins } from '#lib/roles.js'
  import { libelleType, libelleChoix, choixFiabilite, phraseErreur } from '#lib/libelles.js'

  let contrat = {}          // {objet: {champ*: [genre, aide]}}
  let confiances = []
  // Saisir une donnée « confirmée », c'est la publier : réservé au validateur.
  $: tranche = auMoins($currentUser, 'validator')
  $: confiancesPermises = tranche ? confiances : confiances.filter(c => c !== 'confirmed')
  $: if (!tranche && confidence === 'confirmed') confidence = 'probable'
  let objet = 'flux'
  let valeurs = {}
  let confidence = 'confirmed'
  let erreur = ''
  let succes = ''
  let avertissement = ''
  let envoi = false

  // ─── Une fiche : celle qu'on rejoindrait ───────────────────────────────────
  // Une variante d'écriture (« LES AMIS DU FOUR ») rejoint la fiche existante
  // au lieu d'en créer une : on le dit AVANT d'enregistrer, et l'on demande
  // un second clic (audit du 15/11/2026).
  // « Nouvelle fiche » (menu, liste des fiches) mène ici avec `?objet=entite` ;
  // suivi aussi quand on y revient depuis cette même page.
  let objetDemande = null
  $: demande = $page.url.searchParams.get('objet')
  $: if (demande && contrat[demande] && demande !== objetDemande) {
    objetDemande = demande; objet = demande; reinitialiser()
  }

  let rejoint = null        // { id, name, type, commune, verdict } ou null
  $: cleRejoint = objet === 'entite'
    ? JSON.stringify([valeurs.type, valeurs.name, valeurs.commune, valeurs.siren]) : ''
  $: { cleRejoint; rejoint = null }

  // ─── Source ────────────────────────────────────────────────────────────────
  // Obligatoire. Les procès-verbaux 2017-2019 de la commune ont disparu du site
  // de la mairie : une URL ne prouve plus rien une fois la page retirée. On
  // s'adosse donc à un document archivé sur le disque — ou l'on explique
  // pourquoi il n'y en a pas, et cette explication est publiée avec la ligne.
  let documents = []
  let rechercheDoc = ''
  let docChoisi = null
  let sansDocumentMotif = ''
  let citation = ''
  let fichierEnCours = false

  let saisies = []

  // ─── Extraction assistée ───────────────────────────────────────────────────
  // Le modèle ne saisit rien : il lit un acte déjà en base et propose des
  // lignes, que l'on reprend une par une dans le formulaire. Chaque proposition
  // porte la phrase du texte qui la justifie, et le serveur a vérifié que cette
  // phrase existe vraiment — une ligne marquée « citation introuvable » est le
  // signe qu'il a brodé.
  let iaDispo = { configuree: false }
  let actes = []
  let acteChoisi = null
  let rechercheActe = ''
  let propositions = null
  let iaEnCours = false
  // Le modèle qui a lu la proposition reprise : il part avec la saisie
  // (`source.ia`), pour qu'on sache dans un an quelle version a lu la ligne.
  let iaTrace = null

  $: actesFiltres = rechercheActe
      ? actes.filter(a => `${a.date} ${a.title || ''}`.toLowerCase()
                           .includes(rechercheActe.toLowerCase()))
      : actes
  $: objetExtractible = ['flux', 'acte', 'marche', 'budget_vote'].includes(objet)

  async function chargerActes() {
    try {
      const r = await api.donnees('deliberation', '', 300)
      actes = (r || []).filter(a => a.excerpt)
    } catch { actes = [] }
  }

  async function proposer() {
    if (!acteChoisi) return
    iaEnCours = true; erreur = ''; propositions = null
    try {
      propositions = await api.iaExtraire(objet, { event_id: acteChoisi.id })
      if (propositions.source?.raw_document_id && !docChoisi) {
        docChoisi = documents.find(d => d.id === propositions.source.raw_document_id)
            || { id: propositions.source.raw_document_id,
                 title: propositions.source.titre }
      }
    } catch (e) {
      erreur = phraseErreur(e)
    } finally {
      iaEnCours = false
    }
  }

  // `montant_dans_citation` vaut null quand il n'y a pas de montant à contrôler.
  const fondee = (p) => !!p.citation_verifiee && p.montant_dans_citation !== false
  // Chaque autre champ rendu doit aussi se lire dans la citation (audit du
  // 06/11/2026 : un PV piégé obtenait « confirmé » avec une année inventée).
  const recoupee = (p) => fondee(p) && p.champs_recoupes === true
  // Le sens d'un flux, la date d'un acte : non recoupés, ils ne retiennent pas
  // en `probable` mais se signalent (décision du 07/11/2026).
  const aVerifier = (p) => (p.champs_a_verifier || []).length
    ? ` · ${p.champs_a_verifier.join(', ')} à vérifier` : ''
  const marque = (p) => !p.citation_verifiee ? '✗ citation introuvable'
    : p.montant_dans_citation === false ? '✗ montant absent de la citation'
    : !recoupee(p) ? `✓ citation · à relire : ${(p.champs_non_recoupes || []).join(', ')}`
    : `✓${aVerifier(p)}`
  const CONTROLES = ['citation', 'citation_verifiee', 'montant_dans_citation',
                     'champs_recoupes', 'champs_non_recoupes', 'champs_a_verifier']

  // Reprendre une proposition = remplir le formulaire, pas enregistrer. Le
  // dernier geste reste humain, y compris quand la citation est vérifiée.
  // Seuls les champs que le gabarit demande au modèle sont repris.
  function reprendre(p) {
    const v = Object.fromEntries((propositions?.champs || [])
      .filter(k => p[k] !== undefined && p[k] !== null).map(k => [k, p[k]]))
    if (objet === 'flux' && v.tiers_nom) {
      // Le modèle donne un nom, jamais un type : « Association Nevesque » existe
      // dans cette base en entreprise ET en association. On lance la recherche
      // et on laisse choisir.
      rechercheEntite['tiers'] = v.tiers_nom
      delete v.tiers_nom
      chercherEntite('tiers')
    }
    valeurs = v
    citation = p.citation || ''
    iaTrace = propositions?.modele
      ? { modele: propositions.modele,
          ...(propositions.empreinte ? { empreinte: propositions.empreinte } : {}),
          locale: !!propositions.locale }
      : null
    // Une ligne reprise d'un modèle n'est pas une ligne lue par un humain :
    // elle part en `probable` tant que personne n'a rouvert la source. Une
    // phrase retrouvée ne suffit pas : le montant doit y être écrit (banc du
    // 31/10/2026, des montants pris ailleurs passaient pour vérifiés), ni
    // les autres champs.
    confidence = recoupee(p) ? 'confirmed' : 'probable'
    succes = 'Proposition reprise — à relire avant d\'enregistrer.' + aVerifier(p)
  }

  $: champsDuType = Object.entries(contrat[objet] || {})
      .filter(([cle]) => !cle.startsWith('_'))
  $: libelleObjet = (contrat[objet] || {})._libelle || objet
  $: sourceOk = !!docChoisi || sansDocumentMotif.trim().length > 2

  async function charger() {
    try {
      const c = await api.saisiesChamps()
      contrat = c.objets || {}
      confiances = c.confiances || []
      documents = await api.documents('', 30)
      await chargerSaisies()
      iaDispo = await api.iaConfig()
      if (iaDispo.configuree) chargerActes()
    } catch (e) {
      erreur = phraseErreur(e)
    }
  }

  async function chargerSaisies() {
    const r = await api.saisies()
    saisies = r.saisies || []
  }

  async function chercherDocs() {
    documents = await api.documents(rechercheDoc, 30)
  }

  async function deposer(event) {
    const fichier = event.target.files?.[0]
    if (!fichier) return
    fichierEnCours = true
    erreur = ''
    try {
      const r = await api.documentDeposer(fichier, fichier.name)
      documents = await api.documents('', 30)
      docChoisi = documents.find(d => d.id === r.id)
          || { id: r.id, title: fichier.name, source: 'atelier' }
      succes = r.deja_present
        ? 'Ce document était déjà archivé — il est réutilisé, pas dupliqué.'
        : 'Document archivé.'
    } catch (e) {
      erreur = phraseErreur(e)
    } finally {
      fichierEnCours = false
      event.target.value = ''
    }
  }

  // Le champ « entité » ne devine jamais le type d'après le nom : dans cette
  // base, « Association Nevesque » existe deux fois, une fois en entreprise et
  // une fois en association. On choisit dans la recherche, ou on déclare.
  let rechercheEntite = {}
  let suggestions = {}

  async function chercherEntite(champ) {
    const q = (rechercheEntite[champ] || '').trim()
    if (q.length < 2) { suggestions[champ] = []; return }
    const r = await api.search(q, 8)
    suggestions[champ] = r.results || r || []
  }

  function choisirEntite(champ, e) {
    valeurs[champ] = { id: e.id }
    rechercheEntite[champ] = e.name
    suggestions[champ] = []
  }

  function declarerEntite(champ, type) {
    valeurs[champ] = { nom: rechercheEntite[champ], type }
    suggestions[champ] = []
  }

  function reinitialiser() {
    valeurs = {}
    citation = ''
    iaTrace = null
    rechercheEntite = {}
    suggestions = {}
  }

  async function enregistrer() {
    envoi = true; erreur = ''; succes = ''; avertissement = ''
    try {
      if (objet === 'entite' && !rejoint && valeurs.type && (valeurs.name || '').trim()) {
        const r = await api.saisieEntiteExistante(valeurs)
        if (r.fiche) { rejoint = r.fiche; return }
      }
      const source = { citation }
      if (docChoisi) source.raw_document_id = docChoisi.id
      else source.sans_document_motif = sansDocumentMotif
      if (iaTrace) source.ia = iaTrace
      const r = await api.saisieCreer(objet, valeurs, source, confidence)
      if (r.entity_id) {
        // La fiche créée (ou complétée) : on y va, c'est là qu'on la remplit.
        rejoint = null
        // Le SIREN d'un contributeur n'est pas écrit : la fiche le dira à l'arrivée.
        if (r.entite?.siren_a_confirmer) {
          try {
            sessionStorage.setItem('atelier_avis_fiche', `Fiche créée. Le SIREN ${valeurs.siren} `
              + "est gardé dans votre saisie mais pas encore sur la fiche : un validateur doit le "
              + "confirmer, en saisissant de nouveau la fiche avec ce SIREN.")
          } catch {}
        }
        await goto(`/atelier/entite/${r.entity_id}`)
        return
      }
      succes = `Saisie enregistrée (${libelleObjet.toLowerCase()}) : ${r.import?.ecrites ?? 0} ligne(s) écrite(s).`
      // Flux enregistré, mais son lien sur la fiche du tiers refusé par un
      // validateur : le dire, sinon on croit le lien posé.
      if (r.lien && r.lien.ecrit === false) avertissement = r.lien.message
      reinitialiser()
      await chargerSaisies()
    } catch (e) {
      erreur = phraseErreur(e)
    } finally {
      envoi = false
    }
  }

  async function retirer(s) {
    erreur = ''
    try {
      await api.saisieRetirer(s.id)
      await chargerSaisies()
      // Une saisie de fiche a créé une fiche : la retirer ne la supprime pas.
      succes = s.objet === 'entite'
        ? 'Saisie retirée de la liste. La fiche qu\'elle avait créée existe toujours.'
        : 'Saisie retirée : les lignes qu\'elle avait écrites ont disparu.'
    } catch (e) {
      erreur = phraseErreur(e)
    }
  }

  function fmtOctets(n) {
    if (!n) return ''
    return n > 1048576 ? `${(n / 1048576).toFixed(1)} Mo` : `${Math.round(n / 1024)} Ko`
  }

  charger()
</script>

<div class="saisie">
  <header class="top">
    <h1>Saisir une donnée</h1>
    <p class="sub">
      Ce qu'aucun collecteur ne peut atteindre : un budget voté, une dotation
      notifiée, une subvention lue dans un compte rendu. La saisie n'écrase
      jamais une ligne existante : elle en ajoute une, marquée « saisie à la
      main ». Elle est conservée à part : une mise à jour des données ne
      l'efface pas.
    </p>
  </header>

  {#if erreur}<div class="err">{erreur}</div>{/if}
  {#if succes}<div class="ok">{succes}</div>{/if}
  {#if avertissement}<div class="err" role="status">{avertissement}</div>{/if}

  <div class="layout">
    <section class="form">
      <div class="tabs">
        {#each Object.entries(contrat) as [cle, def]}
          <button class:active={objet === cle}
                  on:click={() => { objet = cle; reinitialiser() }}>
            {def._libelle || cle}
          </button>
        {/each}
      </div>

      {#if iaDispo.configuree && objetExtractible}
        <details class="ia">
          <summary>
            Lire un compte rendu avec le modèle
            <em class="modele">
              {iaDispo.modele}
              {#if iaDispo.locale}· sur cette machine, rien ne sort{:else}· service distant{/if}
            </em>
          </summary>

          <p class="hint">
            Le modèle ne saisit rien : il propose des lignes, chacune avec la
            phrase du compte rendu qui la justifie. Le serveur vérifie que cette
            phrase existe vraiment dans le texte — c'est ce qui permet de repérer
            une invention sans rouvrir le PDF.
          </p>

          <div class="acte-choix">
            <input type="text" placeholder="chercher un compte rendu…"
                   bind:value={rechercheActe} />
            <button class="mini" on:click={proposer}
                    disabled={!acteChoisi || iaEnCours}>
              {iaEnCours ? 'Lecture…' : 'Proposer'}
            </button>
          </div>

          {#if acteChoisi}
            <p class="hint choisi">
              ↳ {acteChoisi.date} · {acteChoisi.title || 'sans titre'}
              <button class="mini" on:click={() => (acteChoisi = null)}>changer</button>
            </p>
          {:else}
            <ul class="documents">
              {#each actesFiltres.slice(0, 8) as a}
                <li><button on:click={() => (acteChoisi = a)}>
                  {a.date} — {(a.title || a.excerpt || '').slice(0, 70)}
                </button></li>
              {/each}
            </ul>
          {/if}

          {#if iaEnCours}
            <p class="hint">Un modèle local lit à son rythme — comptez une à
              plusieurs minutes par compte rendu.</p>
          {/if}

          {#if propositions}
            <div class="resultats">
              <p class="bilan">
                {propositions.propositions.length} proposition(s) ·
                <strong class="ok-c">{propositions.citations_verifiees} citation(s) retrouvée(s)</strong>
                {#if propositions.citations_introuvables}{' '}
                  · <strong class="ko-c">{propositions.citations_introuvables} introuvable(s)</strong>
                {' '}{/if}
                {#if propositions.montants_absents}{' '}
                  · <strong class="ko-c">{propositions.montants_absents} montant(s) absent(s) de la citation</strong>
                {' '}{/if}
                {#if propositions.reponses_illisibles}{' '}
                  · {propositions.reponses_illisibles} réponse(s) illisible(s)
                {' '}{/if}
              </p>
              {#if !propositions.propositions.length}
                <p class="hint">Rien trouvé dans ce compte rendu. Une liste vide
                  vaut mieux qu'une ligne incertaine — c'est ce qui est demandé
                  au modèle.</p>
              {/if}
              <ul class="propositions">
                {#each propositions.propositions as p}
                  <li class:doute={!recoupee(p)}>
                    <div class="prop-tete">
                      <span class="marque">{marque(p)}</span>
                      <button class="mini" on:click={() => reprendre(p)}>reprendre</button>
                    </div>
                    <p class="prop-corps">
                      {#each Object.entries(p) as [k, v]}
                        {#if !CONTROLES.includes(k)}{' '}
                          <span><b>{k}</b> {v}</span>
                        {' '}{/if}
                      {/each}
                    </p>
                    <p class="prop-citation">« {p.citation} »</p>
                  </li>
                {/each}
              </ul>
            </div>
          {/if}
        </details>
      {/if}

      {#each champsDuType as [cle, spec]}
        {@const champ = cle.replace(/\*$/, '')}
        {@const genre = spec[0]}
        {@const aide = spec[1]}
        {@const requis = cle.endsWith('*')}
        <label class="field">
          <span>{aide}{#if requis}<em class="req">obligatoire</em>{/if}</span>

          {#if genre === 'entite'}
            <div class="entite">
              <input type="text" placeholder="Chercher dans la base…" aria-label="Chercher une fiche dans la base"
                     bind:value={rechercheEntite[champ]}
                     on:input={() => chercherEntite(champ)} />
              {#if valeurs[champ]?.id}
                <p class="hint choisi">↳ fiche existante n° {valeurs[champ].id}</p>
              {:else if valeurs[champ]?.nom}
                <p class="hint choisi">↳ fiche à créer : {valeurs[champ].nom} ({libelleType(valeurs[champ].type)})</p>
              {/if}
              {#if valeurs[champ]?.type === 'person'}
                <!-- Les aides aux particuliers (façades, ravalement) nomment des
                     personnes physiques. L'atelier les collecte — c'est sa
                     raison d'être — mais le site ne les publiera pas : ni la
                     fiche, ni le flux, ni le nom dans les libellés. Le dire
                     ici, sinon on croit avoir publié ce qu'on vient de saisir. -->
                <p class="hint prive">
                  ⚠ Personne physique : cette ligne restera dans l'atelier. Le
                  site ne publie ni sa fiche, ni le flux qui la nomme — seuls
                  les rôles civiques (élu, candidat, membre de commission)
                  ouvrent la publication.
                </p>
              {/if}
              {#if suggestions[champ]?.length}
                <ul class="suggestions">
                  {#each suggestions[champ] as s}
                    <li><button on:click={() => choisirEntite(champ, s)}>
                      {s.name} <em>{libelleType(s.type)}</em>
                    </button></li>
                  {/each}
                </ul>
              {:else if (rechercheEntite[champ] || '').length > 2 && !valeurs[champ]}
                <p class="hint">
                  Absente de la base ? La créer comme :
                  {#each ['association', 'business', 'person', 'service'] as t}
                    <button class="mini" on:click={() => declarerEntite(champ, t)}>{libelleType(t)}</button>
                  {/each}
                </p>
              {/if}
            </div>

          {:else if genre.startsWith('choix:')}{' '}
            <select bind:value={valeurs[champ]}>
              <option value="">Choisir…</option>
              {#each genre.slice(6).split(',') as opt}
                <option value={opt}>{libelleChoix(opt)}</option>
              {/each}
            </select>

          {' '}{:else if genre === 'montant'}{' '}
            <input type="number" step="0.01" bind:value={valeurs[champ]} />
          {' '}{:else if genre === 'annee'}{' '}
            <input type="number" step="1" bind:value={valeurs[champ]} />
          {' '}{:else if genre === 'date'}{' '}
            <input type="date" bind:value={valeurs[champ]} />
          {' '}{:else if genre === 'texte'}
            <textarea rows="2" bind:value={valeurs[champ]}></textarea>
          {:else}{' '}
            <input type="text" bind:value={valeurs[champ]} />
          {' '}{/if}
        </label>
      {/each}

      <div class="field source" class:manque={!sourceOk}>
        <span>Source <em class="req">obligatoire</em></span>
        <p class="hint">
          Une saisie sans document n'est pas défendable : une page web peut
          disparaître, une copie archivée reste. Si l'acte n'existe plus en
          ligne, dites-le plus bas.
        </p>

        <div class="doc-recherche">
          <input type="text" placeholder="Chercher un document archivé…" aria-label="Chercher un document archivé"
                 bind:value={rechercheDoc} on:input={chercherDocs} />
          <label class="depot">
            {fichierEnCours ? 'Dépôt…' : 'Déposer un fichier'}
            <input type="file" on:change={deposer} hidden />
          </label>
        </div>

        {#if docChoisi}
          <p class="hint choisi">
            ↳ {docChoisi.title || docChoisi.url || `document #${docChoisi.id}`}
            <a href={api.documentUrl(docChoisi.id)}
               on:click|preventDefault={() => api.ouvrirDocument(docChoisi.id)}>↗ relire</a>
            <button class="mini" on:click={() => (docChoisi = null)}>changer</button>
          </p>
        {:else}
          <ul class="documents">
            {#each documents.slice(0, 8) as d}
              <li>
                <button on:click={() => (docChoisi = d)}>
                  {d.title || d.url || `#${d.id}`}
                  <em>{d.doc_type} · {d.source} · {fmtOctets(d.byte_size)}</em>
                </button>
              </li>
            {/each}
          </ul>
          <label class="soupape">
            <span>Aucun document possible ? Dites pourquoi : ce motif sera publié avec la ligne.</span>
            <input type="text" bind:value={sansDocumentMotif}
                   placeholder="Par exemple : registre consulté en mairie, non communiqué" />
          </label>
        {' '}{/if}

        <label class="citation">
          <span>Passage exact de la source (facultatif, mais c'est lui qui rend la ligne vérifiable)</span>
          <textarea rows="2" bind:value={citation}></textarea>
        </label>
      </div>

      <label class="field">
        <span>Fiabilité</span>
        <select bind:value={confidence}>
          {#each choixFiabilite(confiancesPermises) as c}<option value={c.cle}>{c.libelle}</option>{/each}
        </select>
        <p class="hint">
          « Confirmée » est publiée sur le site. « Probable » reste dans
          l'atelier, pour un chiffre dont on doute encore.
        </p>
      </label>

      {#if rejoint}
        <div class="rejoint" role="alert">
          <p>
            Ce nom rejoint une fiche qui existe déjà :
            <a href="/atelier/entite/{rejoint.id}">{rejoint.name}</a>
            ({libelleType(rejoint.type)}{rejoint.commune ? `, ${rejoint.commune}` : ''}){#if rejoint.verdict === 'ecarte'}, <strong>écartée du site</strong>{/if}.
            Enregistrer ne créera pas de nouvelle fiche. Seuls les champs encore
            vides de la fiche existante seront remplis ; rien de ce qui est déjà
            renseigné ne change.
          </p>
          <p class="hint">Si c'est une autre structure, changez le nom ou la commune.</p>
        </div>
      {/if}
      <button class="save" on:click={enregistrer} disabled={envoi || !sourceOk}>
        {envoi ? 'Enregistrement…'
          : objet !== 'entite' ? 'Enregistrer la saisie'
          : rejoint ? `Compléter la fiche « ${rejoint.name} »` : 'Créer la fiche'}
      </button>
      <!-- Répétée sous le bouton : en haut de page, sur téléphone, on ne la
           voyait pas après avoir cliqué. -->
      {#if erreur}<div class="err" role="alert">{erreur}</div>{/if}
      {#if !sourceOk}
        <p class="hint bloque">Choisir un document, en déposer un, ou expliquer son absence.</p>
      {/if}
    </section>

    <aside class="liste">
      <h2>Saisies enregistrées <span class="compte">{saisies.length}</span></h2>
      {#if !saisies.length}
        <p class="muted">Rien encore. Ce qui sera saisi ici survit à une
          reconstruction complète de la base.</p>
      {:else}
        <ul>
          {#each saisies as s (s.id)}
            <li>
              <div class="ligne">
                <strong>{(contrat[s.objet] || {})._libelle || s.objet}</strong>
                <button class="mini danger" on:click={() => retirer(s)}>retirer</button>
              </div>
              <p class="detail">
                {#each Object.entries(s.valeurs || {}).slice(0, 3) as [k, v]}
                  <span>{k} : {typeof v === 'object' ? (v.nom || `#${v.id}`) : v}</span>
                {/each}
              </p>
              <p class="meta">
                {s.saisi_par} · {(s.saisi_le || '').slice(0, 10)}
                {#if s.source?.sans_document_motif}{' '}
                  · <em class="sans-doc">sans document : {s.source.sans_document_motif}</em>
                {' '}{:else if s.source?.raw_document_id}{' '}
                  · <a href={api.documentUrl(s.source.raw_document_id)}
                       on:click|preventDefault={() => api.ouvrirDocument(s.source.raw_document_id)}>source</a>
                {' '}{/if}
              </p>
            </li>
          {/each}
        </ul>
      {/if}
    </aside>
  </div>
</div>

<style>
  .saisie { padding: 1.25rem 1.5rem; height: 100%; overflow-y: auto; color: var(--texte); }
  .top h1 { font-size: 1.4rem; margin: 0; }
  .sub { color: var(--texte-doux); font-size: .85rem; max-width: 78ch; margin: .35rem 0 1rem; line-height: 1.5; }
  code { background: var(--fond); padding: 0 .25rem; border-radius: 3px; font-size: .9em; }

  .err { background: var(--danger-bordure); color: var(--danger-texte); padding: .5rem .75rem; border-radius: 6px; margin-bottom: .75rem; font-size: .85rem; }
  .rejoint { border: 1px solid var(--alerte-bordure); background: var(--alerte-doux); color: var(--alerte-texte);
             padding: .5rem .75rem; border-radius: 6px; margin: .5rem 0; font-size: .85rem; }
  .rejoint a { color: inherit; text-decoration: underline; }
  .ok  { background: var(--succes-bordure); color: var(--succes-texte); padding: .5rem .75rem; border-radius: 6px; margin-bottom: .75rem; font-size: .85rem; }

  .layout { display: grid; grid-template-columns: 1fr 320px; gap: 1.25rem; align-items: start; }
  .form { background: var(--fond); border: 1px solid var(--bordure-douce); border-radius: 8px; padding: 1rem; }

  .tabs { display: flex; gap: .25rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .tabs button { padding: .4rem .8rem; border-radius: 6px; background: var(--surface); color: var(--texte-doux); font-size: .82rem; }
  .tabs button.active { background: var(--accent); color: var(--sur-accent); }

  .field { display: block; margin-bottom: .9rem; }
  .field > span { display: block; font-size: .78rem; color: var(--texte-doux); margin-bottom: .25rem; }
  .req { color: var(--alerte); font-style: normal; font-size: .68rem; text-transform: uppercase;
         letter-spacing: .03em; margin-left: .4rem; }
  input, select, textarea {
    width: 100%; background: var(--fond); border: 1px solid var(--bordure); border-radius: 6px;
    color: var(--texte); padding: .4rem .55rem; font: inherit; font-size: .85rem;
  }
  input::placeholder, textarea::placeholder { color: var(--texte-doux); }

  .hint { color: var(--texte-doux); font-size: .74rem; line-height: 1.45; margin: .3rem 0 0; }
  .hint.choisi { color: var(--succes); }
  .hint.prive { color: var(--alerte); border-left: 2px solid var(--alerte-bordure);
                padding-left: .5rem; margin-top: .35rem; }
  .hint.bloque { color: var(--alerte); text-align: center; }

  .suggestions, .documents { list-style: none; margin: .35rem 0 0; padding: 0;
                             border: 1px solid var(--bordure-douce); border-radius: 6px; overflow: hidden; }
  .suggestions li button, .documents li button {
    display: block; width: 100%; text-align: left; padding: .35rem .55rem;
    background: var(--fond); color: var(--texte-2); font-size: .8rem;
  }
  .suggestions li button:hover, .documents li button:hover { background: var(--surface); }
  .suggestions em, .documents em { color: var(--texte-doux); font-style: normal; font-size: .72rem; margin-left: .4rem; }

  .mini { display: inline-block; width: auto; padding: .1rem .5rem; margin-left: .3rem;
          border-radius: 4px; background: var(--surface); color: var(--texte-2); font-size: .72rem; }
  .mini.danger { background: var(--danger-bordure); color: var(--danger-texte); }

  .ia { border: 1px solid var(--bordure); border-radius: 8px; padding: .6rem .7rem;
        background: var(--fond); margin-bottom: 1rem; }
  .ia summary { cursor: pointer; font-size: .85rem; color: var(--texte-2); }
  .modele { color: var(--texte-doux); font-style: normal; font-size: .72rem; margin-left: .4rem; }
  .acte-choix { display: flex; gap: .4rem; margin-top: .5rem; }
  .acte-choix input { flex: 1; }
  .resultats { margin-top: .7rem; border-top: 1px solid var(--bordure-douce); padding-top: .6rem; }
  .bilan { font-size: .78rem; color: var(--texte-doux); margin: 0 0 .5rem; }
  .ok-c { color: var(--succes); } .ko-c { color: var(--danger); }
  .propositions { list-style: none; margin: 0; padding: 0; }
  .propositions li { border: 1px solid var(--bordure-douce); border-radius: 6px; padding: .45rem .55rem;
                     margin-bottom: .4rem; }
  .propositions li.doute { border-color: var(--danger-bordure); background: var(--danger-doux); }
  .prop-tete { display: flex; justify-content: space-between; align-items: center; }
  .prop-tete .marque { font-size: .72rem; color: var(--succes); }
  .propositions li.doute .marque { color: var(--danger); }
  .prop-corps { margin: .25rem 0; font-size: .78rem; color: var(--texte);
                display: flex; flex-wrap: wrap; gap: .1rem .8rem; }
  .prop-corps b { color: var(--texte-doux); font-weight: 600; font-size: .7rem;
                  text-transform: uppercase; letter-spacing: .03em; margin-right: .2rem; }
  .prop-citation { margin: 0; font-size: .74rem; color: var(--texte-doux); font-style: italic;
                   line-height: 1.4; }

  .source { border: 1px solid var(--bordure); border-radius: 8px; padding: .7rem; background: var(--fond); }
  .source.manque { border-color: var(--alerte-bordure); }
  .doc-recherche { display: flex; gap: .4rem; align-items: center; }
  .depot { flex: 0 0 auto; padding: .4rem .7rem; border-radius: 6px; background: var(--surface);
           color: var(--texte-2); font-size: .78rem; cursor: pointer; white-space: nowrap; }
  .soupape { display: block; margin-top: .5rem; }
  .soupape span { display: block; font-size: .72rem; color: var(--texte-doux); margin-bottom: .2rem; }
  .citation { display: block; margin-top: .6rem; }
  .citation span { display: block; font-size: .72rem; color: var(--texte-doux); margin-bottom: .2rem; }

  .save { width: 100%; padding: .6rem; border-radius: 6px; background: var(--bouton); color: var(--sur-accent);
          font-weight: 600; margin-top: .5rem; }
  .save:disabled { opacity: .45; }

  .liste { background: var(--surface); border: 1px solid var(--bordure); border-radius: 8px; padding: .9rem;
           position: sticky; top: 0; max-height: 88vh; overflow-y: auto; }
  .liste h2 { font-size: .95rem; margin: 0 0 .6rem; }
  .compte { background: var(--surface-2); border-radius: 999px; padding: 0 .45rem; font-size: .72rem; }
  .liste ul { list-style: none; margin: 0; padding: 0; }
  .liste li { border-top: 1px solid var(--bordure); padding: .5rem 0; }
  .ligne { display: flex; justify-content: space-between; align-items: center; font-size: .82rem; }
  .detail { margin: .2rem 0; font-size: .74rem; color: var(--texte-doux); display: flex; flex-direction: column; }
  .meta { margin: 0; font-size: .68rem; color: var(--texte-doux); }
  .sans-doc { color: var(--alerte); font-style: normal; }
  .muted { color: var(--texte-doux); font-size: .8rem; line-height: 1.5; }

  @media (max-width: 950px) { .layout { grid-template-columns: 1fr; } }
</style>
