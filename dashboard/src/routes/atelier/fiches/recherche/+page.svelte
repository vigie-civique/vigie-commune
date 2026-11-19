<script>
  // Résultats de la recherche par nom du bandeau, groupés par nature.
  // Trois appels, indépendants : l'un qui échoue ne cache pas les autres.
  import { COMMUNE } from '#lib/instance.js'
  import { api } from '#lib/api.js'
  import { pageStore } from '#lib/etat-app'
  import { lienSur } from '#lib/liens.js'
  import { adresseFiche } from '../liens-retour.js'
  import { LIBELLE_TYPE, phraseStatut } from '#lib/libelles.js'

  const page = pageStore()
  const LIMITE = 50

  const euro = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })

  $: q = ($page.url.searchParams.get('q') ?? '').trim()
  $: ici = $page.url.pathname + $page.url.search
  $: if (q.length >= 2) lancer(q); else raz()

  let fiches = { etat: 'attente', items: [] }
  let actes = { etat: 'attente', items: [] }
  let marches = { etat: 'attente', items: [] }
  let jeton = 0

  function raz() { jeton++; fiches = actes = marches = { etat: 'attente', items: [] } }

  const enCours = () => ({ etat: 'cours', items: [] })

  async function groupe(promesse, moi, cible) {
    let r
    try { r = { etat: 'ok', items: await promesse } } catch (e) { r = { etat: 'echec', items: [], statut: e?.status } }
    if (moi === jeton) cible(r)
  }

  function lancer(t) {
    const moi = ++jeton
    fiches = actes = marches = enCours()
    groupe(api.search(t, LIMITE), moi, r => fiches = r)
    groupe(api.eventSearch(t, LIMITE), moi, r => actes = r)
    // `/api/marches` filtre chaque critère à part : le nom du titulaire, celui de
    // l'acheteur, un mot de l'objet du marché. Trois appels, fusionnés sans doublon.
    groupe(
      Promise.all([api.marches({ titulaire: t, limit: LIMITE }), api.marches({ acheteur: t, limit: LIMITE }),
                   api.marches({ objet: t, limit: LIMITE })])
        .then(lots => {
          const vus = new Set()
          return lots.flat().filter(m => !vus.has(m.id) && vus.add(m.id)).slice(0, LIMITE)
        }),
      moi, r => marches = r)
  }

  // L'extrait de l'API entoure le mot trouvé de caractères de contrôle (\x02 \x03) :
  // on les coupe en segments, sans jamais injecter de HTML.
  function segments(extrait) {
    if (!extrait) return []
    return extrait.split('\u0002').flatMap((bout, i) => {
      if (i === 0) return bout ? [{ t: bout, m: false }] : []
      const [trouve, reste = ''] = bout.split('\u0003')
      return [{ t: trouve, m: true }, ...(reste ? [{ t: reste, m: false }] : [])]
    })
  }

  const dateFr = d => d ? d.slice(0, 10).split('-').reverse().join('/') : ''
  const messageEchec = g => g.statut ? `Cette recherche a échoué. ${phraseStatut(g.statut)} Les autres résultats restent valables.` : 'Cette recherche a échoué. Les autres résultats restent valables.'
</script>

<svelte:head><title>« {q} » — Recherche — Atelier {COMMUNE}</title></svelte:head>

<div class="page">
  <h1>Résultats pour « {q} »</h1>
  <p class="aide">Les fiches sont cherchées par leur nom, dans toute la base, quel que soit leur verdict ou leur territoire. Les marchés publics sont trouvés par le nom de l'acheteur ou du titulaire, ou par un mot de leur objet.</p>

  {#if q.length < 2}
    <p class="vide">Tapez au moins deux lettres dans le champ du bandeau.</p>
  {:else}
    <section aria-labelledby="h-fiches">
      <h2 id="h-fiches">Fiches {#if fiches.etat === 'ok'}<span class="sr-only">, </span><span class="n">{fiches.items.length}{fiches.items.length === LIMITE ? '+' : ''}</span>{/if}</h2>
      {#if fiches.etat === 'cours'}<p class="vide">Recherche…</p>
      {:else if fiches.etat === 'echec'}<p class="erreur" role="alert">{messageEchec(fiches)}</p>
      {:else if fiches.items.length === 0}<p class="vide">Aucune fiche à ce nom.</p>
      {:else}
        <ul class="liste">
          {#each fiches.items as f (f.id)}
            <li>
              <a href={adresseFiche(f.id, ici)}>{f.name}</a>
              <span class="meta">{LIBELLE_TYPE[f.type] ?? f.type}{f.address ? ` · ${f.address}` : ''}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section aria-labelledby="h-actes">
      <h2 id="h-actes">Actes et événements {#if actes.etat === 'ok'}<span class="sr-only">, </span><span class="n">{actes.items.length}{actes.items.length === LIMITE ? '+' : ''}</span>{/if}</h2>
      {#if actes.etat === 'cours'}<p class="vide">Recherche…</p>
      {:else if actes.etat === 'echec'}<p class="erreur" role="alert">{messageEchec(actes)}</p>
      {:else if actes.items.length === 0}<p class="vide">Aucun acte ni événement avec ce mot.</p>
      {:else}
        <ul class="liste">
          {#each actes.items as ev (ev.id)}
            <li>
              <span class="titre">{ev.title}</span>
              <span class="meta">{ev.type}{ev.date ? ` · ${dateFr(ev.date)}` : ''}{ev.source ? ` · ${ev.source}` : ''}</span>
              {#if ev.snippet}<span class="extrait">{#each segments(ev.snippet) as s}{#if s.m}<mark>{s.t}</mark>{:else}{s.t}{/if}{/each}</span>{/if}
              {#if ev.source_url}<a class="src" href={lienSur(ev.source_url)} target="_blank" rel="noopener" aria-label="Voir la source : {ev.title} (nouvel onglet)">Voir la source</a>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section aria-labelledby="h-marches">
      <h2 id="h-marches">Marchés publics (acheteur, titulaire ou objet) {#if marches.etat === 'ok'}<span class="sr-only">, </span><span class="n">{marches.items.length}{marches.items.length === LIMITE ? '+' : ''}</span>{/if}</h2>
      {#if marches.etat === 'cours'}<p class="vide">Recherche…</p>
      {:else if marches.etat === 'echec'}<p class="erreur" role="alert">{messageEchec(marches)}</p>
      {:else if marches.items.length === 0}<p class="vide">Aucun marché dont l'acheteur, le titulaire ou l'objet contient ce mot.</p>
      {:else}
        <ul class="liste">
          {#each marches.items as m (m.id)}
            <li>
              <span class="titre">{m.objet || 'Objet non renseigné'}</span>
              <span class="meta">
                {m.montant != null ? euro.format(m.montant) : 'montant inconnu'}{m.date_notif ? ` · notifié le ${dateFr(m.date_notif)}` : ''}
              </span>
              <span class="meta">
                Titulaire :
                {#if m.titulaire_entity_id}<a href={adresseFiche(m.titulaire_entity_id, ici)}>{m.titulaire_nom}</a>{:else}{m.titulaire_nom || 'inconnu'}{/if}
                · Acheteur :
                {#if m.acheteur_entity_id}<a href={adresseFiche(m.acheteur_entity_id, ici)}>{m.acheteur_nom}</a>{:else}{m.acheteur_nom || 'inconnu'}{/if}
              </span>
              {#if m.source_url}<a class="src" href={lienSur(m.source_url)} target="_blank" rel="noopener" aria-label="Voir la source : {m.objet || 'marché'} (nouvel onglet)">Voir la source</a>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
</div>

<style>
  .page { padding: .9rem 1.1rem 2rem; max-width: 900px; display: flex; flex-direction: column; gap: .9rem; }
  h1 { font-size: 1.15rem; }
  h2 { font-size: .95rem; margin-bottom: .3rem; display: flex; align-items: center; gap: .5rem; }
  .n { font-size: .72rem; background: var(--surface-2); color: var(--texte-doux); border-radius: 999px; padding: 1px 8px; font-weight: 600; }
  .aide, .meta, .vide { color: var(--texte-doux); font-size: .78rem; }
  .erreur { color: var(--danger); font-size: .82rem; }
  .liste { list-style: none; padding: 0; margin: 0; border: 1px solid var(--bordure); border-radius: 6px; background: var(--surface); }
  li { display: flex; flex-direction: column; gap: 2px; padding: .55rem .8rem; border-bottom: 1px solid var(--bordure); font-size: .85rem; }
  li:last-child { border-bottom: none; }
  li > a:first-child { color: var(--info); font-weight: 600; }
  .titre { font-weight: 600; }
  .extrait { font-size: .8rem; color: var(--texte); }
  mark { background: var(--surface-2); color: var(--texte); font-weight: 700; padding: 0 1px; }
  .src { font-size: .75rem; color: var(--info); }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  a:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
</style>
