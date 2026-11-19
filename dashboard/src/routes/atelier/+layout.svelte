<script>
  import ChoixTheme from '#lib/components/ChoixTheme.svelte'
  import RechercheBandeau from './fiches/RechercheBandeau.svelte'
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { pageStore } from '#lib/etat-app'
  const page = pageStore()
  import { currentUser, logout, rafraichir, authFetch } from '#lib/stores/auth.js'
  import { LIBELLE_ROLE, auMoins } from '#lib/roles.js'
  import { menuPour } from '#lib/menu.js'

  let ready = false

  // Pages ouvertes sans session : la connexion, et l'invitation — où l'invité
  // n'a pas encore de compte.
  $: isLogin = ['/atelier/login', '/atelier/invitation'].includes($page.url.pathname)

  onMount(async () => {
    if (isLogin) { ready = true; return }

    // Vérifier le token d'accès courant
    const access = sessionStorage.getItem('atelier_access')
    if (access) {
      const res = await fetch('/api/auth/me', {
        headers: { 'X-Atelier-Session': access },
      })
      if (res.ok) {
        currentUser.set(await res.json())
        ready = true
        return
      }
    }

    // Tenter le rafraîchissement (cookie HttpOnly, cf. stores/auth.js)
    if (await rafraichir()) {
      const me = await fetch('/api/auth/me', {
        headers: { 'X-Atelier-Session': sessionStorage.getItem('atelier_access') },
      })
      if (me.ok) {
        currentUser.set(await me.json())
        ready = true
        return
      }
    }

    // Non authentifié
    sessionStorage.removeItem('atelier_access')
    localStorage.removeItem('atelier_refresh')
    goto('/atelier/login')
  })

  async function handleLogout() {
    await logout()
    goto('/atelier/login')
  }

  // Le menu (groupes, rôle minimal, entrées éteintes) vit dans lib/menu.js : l'accueil
  // s'en sert aussi pour ne pas envoyer quelqu'un vers une page fermée.
  // La recherche par sens s'éteint tant qu'aucun modèle n'est branché ;
  // `null` = on ne sait pas encore, et on n'éteint pas sur une ignorance.
  let iaBranchee = null
  $: if (ready && $currentUser && iaBranchee === null) lireIA()
  async function lireIA() {
    iaBranchee = undefined
    try {
      const r = await authFetch('/rag/config')
      if (r.ok) iaBranchee = (await r.json()).enabled === true
    } catch { /* API muette : l'entrée reste ouverte, la page dira pourquoi */ }
  }
  $: groupes = menuPour($currentUser, iaBranchee)

  // Sur téléphone le menu se replie : un bouton l'ouvre, un clic sur une entrée
  // (ou un changement de page) le referme.
  let ouvert = false
  $: $page.url.pathname, ouvert = false
</script>

{#if isLogin}
  <slot />
{:else if ready && $currentUser}
  <div class="atelier-shell">
    <aside class="atelier-sidebar">
      <div class="sidebar-header">
        <span class="sidebar-title">Atelier</span>
        <span class="role-badge" class:admin={$currentUser.role === 'admin'}>{LIBELLE_ROLE[$currentUser.role] ?? $currentUser.role}</span>
      </div>

      <button class="menu-bouton" aria-expanded={ouvert} aria-controls="menu-atelier"
              on:click={() => ouvert = !ouvert}>
        {ouvert ? 'Fermer le menu' : 'Menu'}
      </button>

      <div class="menu" id="menu-atelier" class:ouvert>
        <nav class="sidebar-nav" aria-label="Atelier">
          {#each groupes as g}
            <div class="groupe">
              {#if g.titre}<p class="groupe-titre">{g.titre}</p>{/if}
              {#each g.entrees as n}
                {#if n.raison}{' '}
                  <span class="eteint" aria-disabled="true">
                    <span class="eteint-nom">{n.label}</span>
                    <span class="eteint-raison">{n.raison}</span>
                  </span>
                {' '}{:else}{' '}
                  <a href={n.href} class:active={$page.url.pathname === n.href}
                     aria-current={$page.url.pathname === n.href ? 'page' : undefined}>{n.label}</a>
                {' '}{/if}
              {/each}
            </div>
          {/each}
        </nav>

        <div class="sidebar-footer">
          <a class="user-email" href="/atelier/mon-compte" title="Mon compte">{$currentUser.email}</a>
          <ChoixTheme />
          <button class="logout-btn" on:click={handleLogout}>Déconnexion</button>
        </div>
      </div>
    </aside>

    <div class="atelier-content">
      <div class="bandeau"><RechercheBandeau /></div>
      <slot />
    </div>
  </div>
{/if}

<style>
  .atelier-shell {
    display: flex;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }

  .atelier-sidebar {
    width: 200px;
    flex-shrink: 0;
    background: var(--surface);
    border-right: 1px solid var(--bordure);
    display: flex;
    flex-direction: column;
    padding: .75rem 0;
  }

  .sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 .9rem .6rem;
    border-bottom: 1px solid var(--bordure);
    margin-bottom: .4rem;
  }

  .sidebar-title {
    font-weight: 700;
    font-size: .85rem;
    color: var(--texte);
  }

  .role-badge {
    font-size: .65rem;
    padding: 1px 6px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--texte-doux);
    text-transform: uppercase;
    letter-spacing: .04em;
  }
  .role-badge.admin { background: var(--accent-fort); color: var(--sur-accent); }

  .menu { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow-y: auto; }
  .menu-bouton { display: none; }

  .sidebar-nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: .25rem .5rem;
    gap: .55rem;
  }
  .groupe { display: flex; flex-direction: column; gap: 2px; }
  .groupe-titre {
    font-size: .66rem; letter-spacing: .06em; text-transform: uppercase;
    color: var(--texte-doux); padding: 0 .65rem; margin: .1rem 0 .1rem;
  }

  .sidebar-nav a, .sidebar-nav .eteint {
    padding: .4rem .65rem;
    border-radius: 5px;
    font-size: .8rem;
    color: var(--texte-doux);
    transition: background .12s;
  }
  /* Une entrée éteinte garde sa raison à l'écran : un survol n'existe pas au toucher. */
  .sidebar-nav .eteint { display: flex; flex-direction: column; cursor: not-allowed; }
  .eteint-nom { opacity: .55; }
  .eteint-raison { font-size: .68rem; color: var(--texte-doux); line-height: 1.3; }
  .sidebar-nav a.active { background: var(--accent); color: var(--sur-accent); }
  .sidebar-nav a:hover:not(.active) { background: var(--fond); color: var(--texte); }

  .sidebar-footer {
    padding: .6rem .9rem 0;
    border-top: 1px solid var(--bordure);
    display: flex;
    flex-direction: column;
    gap: .4rem;
  }

  .user-email {
    font-size: .72rem;
    color: var(--texte-doux);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .logout-btn {
    font-size: .75rem;
    color: var(--danger);
    text-align: left;
    padding: 0;
    cursor: pointer;
    background: none;
    border: none;
  }
  .logout-btn:hover { text-decoration: underline; }

  .atelier-content {
    flex: 1;
    overflow-y: auto;
    background: var(--fond);
    scroll-padding-top: 3.5rem;   /* le bandeau collant ne masque pas ce qui prend le focus */
  }

  /* Le champ de recherche reste sous les yeux quand la page défile. */
  .bandeau {
    position: sticky; top: 0; z-index: 40;
    padding: .45rem 1.1rem;
    background: var(--fond);
    border-bottom: 1px solid var(--bordure);
  }

  /* Sur un portable ou un téléphone (≤ 900 px) le menu latéral mangeait la
     largeur : il devient une barre d'une ligne, et le menu s'ouvre dessous, en
     colonne, à la demande. Chaque entrée fait au moins 44 px de haut. */
  @media (max-width: 900px) {
    .atelier-shell { flex-direction: column; }
    .atelier-sidebar {
      width: 100%; flex-direction: row; flex-wrap: wrap; align-items: center;
      padding: .25rem .6rem; border-right: none; border-bottom: 1px solid var(--bordure);
      gap: .5rem; position: relative; flex: none;
    }
    .sidebar-header { border-bottom: none; margin: 0; padding: 0; gap: .5rem; justify-content: flex-start; }
    .menu-bouton {
      display: inline-flex; align-items: center; margin-left: auto;
      min-height: 44px; padding: 0 .9rem; border-radius: 6px;
      border: 1px solid var(--bordure-forte); color: var(--texte); font-size: .85rem;
    }
    .menu {
      display: none; flex: 0 0 100%; max-height: calc(100vh - 7rem);
      border-top: 1px solid var(--bordure); padding-top: .3rem;
    }
    .menu.ouvert { display: flex; }
    .sidebar-nav { padding: 0; }
    .sidebar-nav a { display: flex; align-items: center; min-height: 44px; font-size: .9rem; }
    .sidebar-nav .eteint { min-height: 44px; justify-content: center; }
    .sidebar-footer { flex-direction: column; align-items: stretch; padding: .5rem .4rem; gap: .3rem; }
    .user-email { display: flex; align-items: center; min-height: 44px; font-size: .8rem; }
    .logout-btn { min-height: 44px; text-align: left; font-size: .9rem; }
    .bandeau { padding: .35rem .6rem; }
  }
</style>
