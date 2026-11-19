<script>
  import { heureLocale } from '#lib/heure.js'
  import { libelleType } from '#lib/libelles.js'
  import { VERDICTS, VERDICT, FIABILITE, FIABILITE_AIDE, ORIGINES, ORIGINE_AIDE } from '#lib/axes.js'

  export let form
  export let entity
  export let modifies = []
  export let tranche = false
  // Verdict choisi, pas encore enregistré (null : inchangé).
  export let verdict = null

  const CONFIDENCES = ['verified','confirmed','probable','hypothesis']

  $: m = k => modifies.includes(k)
</script>

<section class="card">
  <h2>Identité</h2>
  <div class="grid2">
    <label class:modifie={m('name')}>
      Nom complet
      <input bind:value={form.name} />
    </label>
    <label class:modifie={m('short_name')}>
      Nom court ou abrégé
      <input bind:value={form.short_name} placeholder="Facultatif" />
    </label>
    <label>
      Type
      <!-- Le type ne se change pas ici : l'enregistrement ne l'envoie pas. -->
      <input value={libelleType(form.type)} disabled title="Le type d'une fiche ne se change pas depuis l'éditeur" />
    </label>
    <label class:modifie={m('responsible')}>
      Responsable
      <input bind:value={form.responsible} placeholder="Nom du dirigeant ou de la présidente" />
    </label>
    <label class:modifie={m('confidence')}>
      Fiabilité de la source
      <select bind:value={form.confidence} disabled={!tranche}
              title={tranche ? 'Ce que la machine sait de la fiche — pas un jugement humain'
                             : 'Réservé au validateur'}>
        {#each CONFIDENCES as c}
          <option value={c} title={FIABILITE_AIDE[c]}>{FIABILITE[c]} — {FIABILITE_AIDE[c]}</option>
        {/each}
      </select>
    </label>
    <div class="axe">
      <span class="axe-titre">Origine</span>
      <span class="axe-valeur" title={ORIGINE_AIDE[entity.origine] ?? "Aucune preuve en base de la façon dont cette fiche est entrée."}>
        {ORIGINES[entity.origine] ?? 'Non établie'}
      </span>
    </div>
    <div class="axe col2" class:modifie={verdict}>
      <span class="axe-titre">Verdict de l'atelier</span>
      <span class="verdict v-{entity.verdict}" class:remplace={verdict}>{VERDICT[entity.verdict]?.libelle ?? entity.verdict}</span>
      {#if verdict}{' '}
        <span>→</span>
        <span class="verdict v-{verdict}">{VERDICT[verdict]?.libelle ?? verdict}</span>
        <span class="tag-attente">à enregistrer</span>
        <button type="button" class="lien-annuler" on:click={() => verdict = null}>annuler</button>
      {' '}{/if}
      {#if entity.verdict_par}{' '}
        <span class="muted">— {entity.verdict_par}, {heureLocale(entity.verdict_le)}</span>
      {' '}{/if}
      <p class="axe-effet">{VERDICT[verdict ?? entity.verdict]?.effet ?? ''}</p>
      {#if entity.verdict_note}<p class="axe-effet">« {entity.verdict_note} »</p>{/if}
      {#if tranche && entity.ecrit_par_contributeurs?.length}
        <!-- Avant de retenir : ce que des contributeurs ont écrit directement
             depuis le dernier verdict. Une information, pas un blocage. -->
        <div class="ecrit-contrib" role="note">
          <p><strong>Modifié par des contributeurs depuis le dernier verdict</strong>
            — à relire avant de retenir :</p>
          <ul>
            {#each entity.ecrit_par_contributeurs as c}
              <li>{c.champ} : « {c.avant ?? '—'} » → « {c.apres ?? '—'} »
                <span class="muted">— {c.par}, {heureLocale(c.le)}</span></li>
            {/each}
          </ul>
        </div>
      {/if}
      {#if tranche}
        <div class="verdict-gestes">
          {#each VERDICTS.filter(v => v.cle !== (verdict ?? entity.verdict)) as v}
            <button type="button" class="vg vg-{v.cle}" title={v.effet}
                    on:click={() => verdict = v.cle === entity.verdict ? null : v.cle}>{v.geste}</button>
          {/each}
        </div>
      {:else}
        <p class="axe-effet">Retenir ou écarter revient au validateur.</p>
      {/if}
    </div>
  </div>
</section>

<style>
  /* Les trois axes d'une fiche (lib/axes.js) : origine, fiabilité, verdict. */
  .axe { display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem;
         font-size: .82rem; }
  .axe-titre { color: var(--texte-doux); width: 100%; }
  .axe-valeur { color: var(--texte); }
  .axe-effet { width: 100%; margin: .1rem 0; color: var(--texte-doux); font-size: .78rem; }
  .verdict { padding: .1rem .5rem; border-radius: 4px; font-weight: 600; background: var(--surface); color: var(--texte-2); }
  .verdict.remplace { text-decoration: line-through; opacity: .6; }
  .v-retenu { background: var(--succes-doux); color: var(--succes); }
  .v-a_revoir { background: var(--info-doux); color: var(--info); }
  .v-ecarte { background: var(--danger-doux); color: var(--danger); }
  .ecrit-contrib { width: 100%; margin: .4rem 0; padding: .5rem .7rem; border-left: 3px solid var(--info);
                   background: var(--surface); font-size: .9rem; }
  .ecrit-contrib ul { margin: .3rem 0 0 1rem; padding: 0; }
  .verdict-gestes { display: flex; gap: .35rem; flex-wrap: wrap; width: 100%; margin-top: .2rem; }
  .vg { border: 1px solid var(--bordure); border-radius: 4px; padding: .25rem .6rem;
        font-size: .78rem; cursor: pointer; background: var(--surface); color: var(--texte); }
  .vg-retenu { color: var(--succes); } .vg-a_revoir { color: var(--info); } .vg-ecarte { color: var(--danger); }
  .vg:hover { border-color: var(--bordure-forte); }
</style>
