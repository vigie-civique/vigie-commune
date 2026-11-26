"""L'urbanisme : autorisations, document d'urbanisme, et les parcelles qui croisent DVF.

Les autorisations d'urbanisme sont un registre public, mais le demandeur
d'un permis est souvent un particulier : son adresse ne sort pas, et un
renvoi vers une fiche non publiée est retiré. Une entreprise individuelle
(forme 1000) est une personne physique : le pétitionnaire n'est pas un fait
public qui la retient (règle du 24/11/2026, avis vigie-dpo) ; sa forme
inconnue vaut EI.
"""
from __future__ import annotations

from scripts.snapshot.socle import rows, table_exists, write_json


def _formes_par_siren(conn) -> dict[str, str]:
    if not table_exists(conn, "businesses"):
        return {}
    return {r["siren"]: str(r["legal_form_code"] or "") for r in rows(
        conn, "SELECT siren, legal_form_code FROM businesses"
              " WHERE siren IS NOT NULL AND siren <> ''")}


def demandeur_est_ei(cj, siren, entity_id, formes, ei_ids) -> bool:
    """Le demandeur d'un permis est-il à traiter comme une EI ?

    Oui si sa fiche est une EI (`ei_ids`), si `CJ_DEM` OU la forme de la fiche du
    SIREN est une personne physique (1xxx, `forme_personne_physique`) : l'une ne
    masque pas l'autre. Oui aussi si AUCUNE des deux n'est un code à quatre
    chiffres : forme inconnue, donc EI (avis vigie-dpo du 24/11/2026). Seule une
    forme connue hors 1xxx publie le demandeur."""
    # Import local : `fiches` importe `actes`, qui importe ce module.
    from scripts.snapshot.fiches import forme_personne_physique
    formes_lues = [f for f in ((cj or "").strip(), formes.get(siren or "", "").strip()) if f]
    connue = any(len(f) == 4 and f.isdigit() for f in formes_lues)
    return (entity_id in ei_ids or not connue
            or any(forme_personne_physique(f) for f in formes_lues))


def evenements_demandeur_retire(conn, ei_ids) -> set[int]:
    """Les événements `autorisation_urbanisme` dont le demandeur ne sort pas.

    Pour que la fiche d'une entité ne rétablisse pas, par le lien « demandeur »,
    le nom que `urbanisme.json` retire (`etape_liens_actes`, qui tourne avant
    l'étape urbanisme). Par autorisation : `CJ_DEM` est propre à chaque permis."""
    if not table_exists(conn, "urbanisme_autorisations"):
        return set()
    avec_cj = "demandeur_cj" in {r[1] for r in conn.execute(
        "PRAGMA table_info(urbanisme_autorisations)")}
    formes = _formes_par_siren(conn)
    return {r["event_id"] for r in rows(conn, f"""
        SELECT event_id, demandeur_siren, demandeur_entity_id,
               {"demandeur_cj" if avec_cj else "NULL AS demandeur_cj"}
          FROM urbanisme_autorisations WHERE event_id IS NOT NULL""")
            if demandeur_est_ei(r["demandeur_cj"], r["demandeur_siren"],
                                r["demandeur_entity_id"], formes, ei_ids)}


def etape_urbanisme(conn, out, public_ids, ei_ids) -> dict:
    avec_cj = table_exists(conn, "urbanisme_autorisations") and "demandeur_cj" in {
        r[1] for r in conn.execute("PRAGMA table_info(urbanisme_autorisations)")}
    formes = _formes_par_siren(conn)
    urbanisme_rows = rows(conn, f"""
        SELECT num_dau, insee, commune, categorie, type_dau, type_label,
               date_depot, date_autorisation, date_achevement,
               demandeur_nom, demandeur_siren, demandeur_entity_id,
               {"demandeur_cj" if avec_cj else "NULL AS demandeur_cj"},
               adresse, lieu_dit, cadastre_ref, superficie_terrain,
               nb_logements, surface_hab_creee, surface_loc_creee, residence
        FROM urbanisme_autorisations ORDER BY date_depot DESC, commune
    """) if table_exists(conn, "urbanisme_autorisations") else []
    urbanisme_public = []
    adresses_retirees = 0
    renvois_retires = 0
    demandeurs_ei_retires = 0
    for u in urbanisme_rows:
        u = dict(u)
        # Le demandeur n'est publié que si sa forme est CONNUE et hors 1xxx.
        # Cela vaut même pour une EI publiée par ailleurs.
        if demandeur_est_ei(u.pop("demandeur_cj", None), u.get("demandeur_siren"),
                            u.get("demandeur_entity_id"), formes, ei_ids):
            if u.get("demandeur_nom") or u.get("demandeur_siren"):
                demandeurs_ei_retires += 1
            u["demandeur_nom"] = u["demandeur_siren"] = u["demandeur_entity_id"] = None
        # Prudence : sans personne morale nommée (ou pour une EI), le demandeur est un
        # particulier. On garde le lieu-dit et la parcelle (le croisement DVF
        # et la lecture territoriale sont préservés) mais pas la voie exacte.
        if not u.get("demandeur_nom"):
            if u.get("adresse"):
                adresses_retirees += 1
            u["adresse"] = None
        # Un identifiant d'entité PROMET une fiche. Le demandeur d'un permis
        # est très souvent hors périmètre publiable — 66 lignes sur Lasalle,
        # 199 sur Brassac, 168 sur Saillans pointaient vers une fiche
        # absente. Aucune page du site ne lit ce champ ; il ne sert donc
        # qu'au lecteur du JSON, à qui il ment. On le retire plutôt que de
        # le laisser désigner un 404.
        if u.get("demandeur_entity_id") and u["demandeur_entity_id"] not in public_ids:
            u["demandeur_entity_id"] = None
            renvois_retires += 1
        u["date_precision"] = "annee"   # cf. contrôle de divulgation SDES
        urbanisme_public.append(u)
    # Le document d'urbanisme au GPU, et ce qu'il fait du territoire. Les
    # parts ne sortent QUE si la couverture a été vérifiée — le collecteur
    # les laisse à NULL sinon, et le JSON transporte le contrôle avec elles.
    plu_documents = rows(conn, """
        SELECT insee, partition, titre, du_type, portee, date_appro,
               gpu_status, gpu_maj
          FROM urbanisme_documents WHERE couvre = 1 ORDER BY insee, date_appro
    """) if table_exists(conn, "urbanisme_documents") else []
    plu_zonage = rows(conn, """
        SELECT insee, typezone, famille, zones, aire_m2, part_pct, couverture
          FROM urbanisme_zonage ORDER BY insee, part_pct DESC
    """) if table_exists(conn, "urbanisme_zonage") else []
    plu_statut = rows(conn, """
        SELECT insee, nom, rnu, aire_km2, documents, releve_le
          FROM urbanisme_statut ORDER BY insee
    """) if table_exists(conn, "urbanisme_statut") else []

    write_json(out / "urbanisme.json", {
        "documents": plu_documents,
        "zonage": plu_zonage,
        "statut": plu_statut,
        "autorisations": urbanisme_public,
        "total": len(urbanisme_public),
        "note_dates": "Dates ramenées à l'année pour les petites communes "
                      "(contrôle de divulgation statistique du SDES).",
        "renvois_demandeur_retires": renvois_retires,
        "demandeurs_ei_retires": demandeurs_ei_retires,
    })

    return {"urbanisme_public": urbanisme_public, "adresses_retirees": adresses_retirees}


# `croisement_foncier.json` (parcelles portant une mutation DVF et une
# autorisation) n'est plus écrit depuis le 04/10/2026 : aucune page ne le
# lisait, ni le lien de réutilisation, ni llms.txt — et il portait le nom du
# demandeur de chaque autorisation (refonte du contenu, décision 12).
