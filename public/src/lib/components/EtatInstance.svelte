<script>
  // Bandeau d'état — ce que ce site EST, dit sur toutes ses pages.
  //
  // Trois sites servis par le même moteur n'ont pas le même statut, et rien ne
  // le disait : quelqu'un arrivant sur un portage de démonstration depuis un
  // moteur de recherche n'avait aucun moyen de savoir que personne, sur place,
  // ne tenait ce site. Il lisait ses manques comme la qualité du dispositif.
  //
  // Le bandeau NE SE REFERME PAS. Un bandeau qu'on peut fermer n'est pas une
  // mention, c'est une réclame : il disparaît au premier clic et le lecteur
  // suivant ne le voit plus. Il est discret et permanent, jamais modal — pas
  // de `role="alert"`, qui interromprait un lecteur d'écran à chaque page pour
  // une information qui ne change pas.
  //
  // Les libellés viennent de `instance.js`, donc de `collectors/statut.py` :
  // le même texte est publié dans le snapshot. Deux textes qui se ressemblent
  // finissent par diverger.
  //
  // Une ligne depuis le 09/11/2026 (conseil design, décision de Julien) : le
  // paragraphe de statut passait avant le titre de chaque page, cinq lignes
  // sur un téléphone. Il ne disparaît pas, il change de place : il se lit en
  // entier en tête de /couverture, où mène le lien. Ici restent l'étiquette,
  // la date de collecte et ce lien.
  import { STATUT_TYPE, STATUT_LIBELLE,
           ANNONCE_TEXTE, ANNONCE_JUSQU_AU, ANNONCE_LIEN } from '#lib/instance.js'

  // L'annonce de l'instance (config/instance.json, clé « annonce ») : une
  // réunion, une permanence. Passé `jusqu_au`, elle ne s'affiche plus, que le
  // site ait été republié ou non.
  const annonce = ANNONCE_TEXTE
    && (!ANNONCE_JUSQU_AU || new Date().toLocaleDateString('sv-SE') <= ANNONCE_JUSQU_AU)

  // La date de dernière COLLECTE — jamais celle de publication. Une
  // republication ne recollecte rien, et ferait passer un site figé pour un
  // site vivant. Absente, on n'invente pas : la ligne disparaît.
  export let derniereCollecte = ''

  const fmt = (d) => {
    if (!d) return ''
    try {
      return new Date(d.slice(0, 10) + 'T00:00:00')
        .toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })
    } catch { return d }
  }

  // Plein contre creux (l'anneau, en CSS), en plus de la couleur : le
  // pastillage seul ne se lit ni en noir et blanc, ni par un daltonien. Le
  // libellé, lui, dit le statut en toutes lettres.
</script>

<!-- Style de la maquette v2 (09/11/2026) : une ligne dès 640 px, sous le
     filet de l'en-tête ; en dessous, statut, date et lien passent à la ligne.
     La date n'est jamais masquée. -->
<aside class="etat {STATUT_TYPE}" aria-label="Statut de ce site">
  <div class="gouttiere">
    {#if annonce}
      <p class="annonce">
        <strong>{ANNONCE_TEXTE}</strong>
        {#if ANNONCE_LIEN}<a href={ANNONCE_LIEN}>En savoir plus <span aria-hidden="true">→</span></a>{/if}
      </p>
    {/if}
    <p class="entete">
      <span class="etiquette"><span class="anneau" aria-hidden="true"></span><b>{STATUT_LIBELLE}</b></span>
      {#if derniereCollecte}{' '}
        <span class="date">dernière collecte : {fmt(derniereCollecte)}</span>
      {' '}{/if}
      <a href="/couverture">Ce que le site couvre, et ce qui lui manque <span aria-hidden="true">→</span></a>
    </p>
  </div>
</aside>

<style>
  /* Aucun fond de couleur : un filet sous la ligne, comme l'en-tête au-dessus.
     Le statut se lit à son libellé et à son anneau : plein quand quelqu'un
     tient le site sur place, creux et ambre pour une démonstration (l'ambre
     est déjà celui des mises en garde). */
  .etat { margin: 0; padding-block: 8px; font-size: 15px; line-height: 1.4; border-bottom: 1px solid var(--filet); }
  /* La gouttière du layout est locale à son composant : la même, ici. */
  .gouttiere {
    max-width: var(--large); margin: 0 auto;
    padding-left: max(16px, env(safe-area-inset-left, 0px));
    padding-right: max(16px, env(safe-area-inset-right, 0px));
  }
  @media (min-width: 700px) {
    .gouttiere {
      padding-left: max(32px, env(safe-area-inset-left, 0px));
      padding-right: max(32px, env(safe-area-inset-right, 0px));
    }
  }

  .annonce { margin: 0 0 6px; padding-bottom: 6px; border-bottom: 1px solid var(--filet); font-size: 15px; line-height: 1.45; }
  .entete { margin: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 14px; }
  .etiquette b { font-weight: 600; }
  .anneau {
    display: inline-block; width: 10px; height: 10px; margin-right: 6px;
    border-radius: 50%; background: var(--encre); vertical-align: 0;
  }
  .demonstration .anneau { background: transparent; box-shadow: inset 0 0 0 2px var(--ambre); }
  .date { color: var(--gris); font-family: var(--mono); font-size: 13px; font-variant-numeric: tabular-nums; }
  /* Cible de 44 px au moins (avis UX du 09/11/2026). */
  .entete a, .annonce a { display: inline-flex; align-items: center; gap: .3em; min-height: 44px; color: var(--encre); }
  .entete a { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
  .entete a:hover { text-decoration-thickness: 2px; }
  /* Sous 640 px, la date prend sa propre ligne : rien n'est masqué, le
     bandeau fait trois lignes courtes à 390 px (accepté par l'UX). */
  @media (max-width: 639.98px) {
    .date { flex-basis: 100%; }
  }
  @media (min-width: 640px) { .entete a { margin-left: auto; } }

  /* Une page imprimée circule sans son contexte : le bandeau reste. */
  @media print {
    .etat { border: 1px solid #999; }
  }
</style>
