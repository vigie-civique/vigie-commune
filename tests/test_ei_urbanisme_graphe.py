"""Une entreprise individuelle (forme 1000) ne sort ni par le pétitionnaire d'un
permis (`urbanisme.json`), ni par `personnes_morales.json` (24/11/2026, avis
vigie-dpo : le pétitionnaire n'est pas un fait qui retient ; la fédération
n'indexe que des structures ; forme inconnue = EI)."""
from __future__ import annotations

import json

from collectors.graphe import personnes_morales_par_acte
from collectors.urbanisme_sitadel import ensure_table
from scripts.snapshot.urbanisme import etape_urbanisme



def _entreprise(base, nom, siren, forme):
    eid = base.execute("INSERT INTO entities(type, name) VALUES('business', ?)", (nom,)).lastrowid
    base.execute("INSERT INTO businesses(entity_id, siren, legal_form_code) VALUES(?,?,?)",
                 (eid, siren, forme))
    return eid


def _permis(base, num, nom, siren, eid=None, cj=None):
    ensure_table(base)
    base.execute(
        "INSERT INTO urbanisme_autorisations (num_dau, insee, commune, categorie, type_dau,"
        " date_depot, demandeur_nom, demandeur_siren, demandeur_cj, demandeur_entity_id,"
        " adresse, lieu_dit, cadastre_ref, superficie_terrain)"
        " VALUES (?, '99001', 'Testonville', 'logements', 'PC', '2026-01-01', ?, ?, ?, ?,"
        " '3 rue des Lilas', 'Les Prés', 'AC0167', 420)", (num, nom, siren, cj, eid))


def _sortie(base, tmp_path, public_ids, ei_ids):
    etape_urbanisme(base, tmp_path, public_ids, ei_ids)
    donnees = json.loads((tmp_path / "urbanisme.json").read_text())
    return {a["num_dau"]: a for a in donnees["autorisations"]}, donnees


def test_une_ei_petitionnaire_sort_sans_nom_ni_siren_meme_publiee(base, tmp_path):
    ei = _entreprise(base, "Martin Dupont", "999000011", "1000")
    _permis(base, "EI", "Martin Dupont", "999000011", ei)
    sortie, donnees = _sortie(base, tmp_path, public_ids={ei}, ei_ids={ei})
    a = sortie["EI"]
    assert a["demandeur_nom"] is None and a["demandeur_siren"] is None
    assert a["demandeur_entity_id"] is None and a["adresse"] is None
    assert (a["lieu_dit"], a["cadastre_ref"], a["superficie_terrain"]) == ("Les Prés", "AC0167", 420)
    assert donnees["demandeurs_ei_retires"] == 1
    assert "999000011" not in json.dumps(donnees) and "Dupont" not in json.dumps(donnees)


def test_une_ei_dont_la_forme_vient_de_cj_dem_est_retiree_sans_fiche(base, tmp_path):
    _permis(base, "CJ", "Julie Durand", "999000022", cj="1000")
    sortie, _ = _sortie(base, tmp_path, set(), set())
    assert sortie["CJ"]["demandeur_nom"] is None and sortie["CJ"]["demandeur_siren"] is None


def test_une_forme_inconnue_vaut_ei(base, tmp_path):
    _permis(base, "INCONNU", "Quelqu'un", "999000033")
    sortie, _ = _sortie(base, tmp_path, set(), set())
    assert sortie["INCONNU"]["demandeur_nom"] is None and sortie["INCONNU"]["adresse"] is None


def test_une_societe_petitionnaire_est_inchangee(base, tmp_path):
    soc = _entreprise(base, "Bâti SAS", "999000044", "5710")
    _permis(base, "SOC", "Bâti SAS", "999000044", soc)
    _permis(base, "SOC_CJ", "Autre SARL", "999000045", cj="5499")
    sortie, donnees = _sortie(base, tmp_path, public_ids={soc}, ei_ids=set())
    assert sortie["SOC"]["demandeur_nom"] == "Bâti SAS"
    assert sortie["SOC"]["demandeur_siren"] == "999000044"
    assert sortie["SOC"]["demandeur_entity_id"] == soc
    assert sortie["SOC"]["adresse"] == "3 rue des Lilas"
    assert sortie["SOC_CJ"]["demandeur_nom"] == "Autre SARL"
    assert donnees["demandeurs_ei_retires"] == 0


def test_personnes_morales_sans_ei_publiee_ni_forme_inconnue(base):
    soc = _entreprise(base, "Régie SA", "999000051", "5710")
    ei_publiee = _entreprise(base, "Pierre Martin", "999000052", "1000")
    ei_non_publiee = _entreprise(base, "Paul Petit", "999000053", "1000")
    inconnue = _entreprise(base, "Forme Inconnue", "999000054", None)
    asso = base.execute("INSERT INTO entities(type, name) VALUES('association', 'Club')").lastrowid
    base.execute("INSERT INTO associations(entity_id, siren) VALUES(?, '999000055')", (asso,))
    ancienne = _entreprise(base, "Artisan Ancien", "999000056", "1100")
    texte = _entreprise(base, "Forme Texte", "999000057", "ABC")
    publiques = [{"id": i, "type": t, "name": n} for i, t, n in [
        (soc, "business", "Régie SA"), (ei_publiee, "business", "Pierre Martin"),
        (inconnue, "business", "Forme Inconnue"), (asso, "association", "Club"),
        (ancienne, "business", "Artisan Ancien"), (texte, "business", "Forme Texte")]]
    liens = [{"event_id": 1, "entity_id": i}
             for i in (soc, ei_publiee, ei_non_publiee, inconnue, asso, ancienne, texte)]
    sortie = personnes_morales_par_acte(
        base, [{"id": 1, "cle": "c-1", "ancre": "c-1"}], liens, publiques)
    assert {l["siren"] for l in sortie["c-1"]} == {"999000051", "999000055"}


def test_une_societe_hors_public_ids_garde_son_nom_mais_perd_le_renvoi(base, tmp_path):
    soc = _entreprise(base, "Société Voisine SAS", "999000061", "5710")
    _permis(base, "VOISIN", "Société Voisine SAS", "999000061", soc)
    sortie, donnees = _sortie(base, tmp_path, public_ids=set(), ei_ids=set())
    a = sortie["VOISIN"]
    assert a["demandeur_nom"] == "Société Voisine SAS" and a["demandeur_siren"] == "999000061"
    assert a["demandeur_entity_id"] is None
    assert donnees["renvois_demandeur_retires"] == 1


def test_cj_dem_5499_ne_sauve_pas_une_fiche_de_forme_1000(base, tmp_path):
    ei = _entreprise(base, "Léa BRANCHEFICTIVE", "999000071", "1000")
    _permis(base, "BRANCHE", "Léa BRANCHEFICTIVE", "999000071", ei, cj="5499")
    sortie, _ = _sortie(base, tmp_path, public_ids={ei}, ei_ids={ei})
    assert sortie["BRANCHE"]["demandeur_nom"] is None
    assert sortie["BRANCHE"]["demandeur_entity_id"] is None


def test_toute_forme_1xxx_est_une_ei_et_une_valeur_non_numerique_est_inconnue(base, tmp_path):
    for num, cj in (("F1100", "1100"), ("F1900", "1900"), ("FABC", "ABC"),
                    ("FCOURT", "54"), ("FLONG", "54999")):
        _permis(base, num, "Nom " + num, "99900008" + str(len(num)), cj=cj)
    _permis(base, "F5499", "Société 5499", "999000089", cj="5499")
    sortie, _ = _sortie(base, tmp_path, set(), set())
    for num in ("F1100", "F1900", "FABC", "FCOURT", "FLONG"):
        assert sortie[num]["demandeur_nom"] is None, num
    assert sortie["F5499"]["demandeur_nom"] == "Société 5499"


def test_le_collecteur_remplit_la_forme_d_une_ligne_deja_inseree(base):
    from collectors.urbanisme_sitadel import import_rows
    ensure_table(base)
    base.execute(
        "INSERT INTO urbanisme_autorisations (num_dau, insee, categorie, demandeur_nom)"
        " VALUES ('ANCIEN', '99001', 'logements', 'Société Ancienne')")
    import_rows(base, "logements", [{"COMM": "99001", "NUM_DAU": "ANCIEN", "TYPE_DAU": "PC",
                                     "DENOM_DEM": "Société Ancienne", "CJ_DEM": "5499"}])
    lignes = base.execute("SELECT demandeur_cj FROM urbanisme_autorisations"
                          " WHERE num_dau = 'ANCIEN'").fetchall()
    assert [l[0] for l in lignes] == ["5499"]


def test_le_lien_demandeur_d_un_permis_sans_demandeur_ne_revient_pas_par_la_fiche(base):
    """Forme vide, ni CJ_DEM ni homonyme : hors `ei_ids`, mais le demandeur est
    retiré de `urbanisme.json` ; le lien « demandeur » de la fiche part aussi."""
    from scripts.snapshot.actes import etape_liens_actes
    inconnue = _entreprise(base, "Forme Vide", "999000091", None)
    societe = _entreprise(base, "Société 5499", "999000092", "5710")
    ids = {}
    for nom, eid in (("A", inconnue), ("B", societe)):
        ev = base.execute("INSERT INTO events(type, date, title) VALUES"
                          "('autorisation_urbanisme', '2026-01-01', ?)", (f"Permis {nom}",)).lastrowid
        base.execute("INSERT INTO event_entities(event_id, entity_id, role) VALUES(?,?,'demandeur')",
                     (ev, eid))
        ids[nom] = ev
        _permis(base, nom, "x", {"A": "999000091", "B": "999000092"}[nom], eid)
        base.execute("UPDATE urbanisme_autorisations SET event_id = ? WHERE num_dau = ?", (ev, nom))
    exclusions = {"event_links": {}}
    r = etape_liens_actes(base, {inconnue, societe}, [], [{"id": i, "title": f"Permis {n}", "type": "autorisation_urbanisme"}
                          for n, i in ids.items()], exclusions, set())
    assert [(l["event_id"], l["entity_id"]) for l in r["public_links"]] == [(ids["B"], societe)]
    assert exclusions["event_links"]["ei_acte_masque_ou_demandeur"] == 1


def test_cj_dem_5499_ne_masque_pas_une_fiche_de_forme_1000_sans_entity_id(base, tmp_path):
    _entreprise(base, "Hugo Pons", "999000095", "1000")
    _permis(base, "SANS_ID", "Hugo Pons", "999000095", None, cj="5499")
    sortie, _ = _sortie(base, tmp_path, set(), set())
    assert sortie["SANS_ID"]["demandeur_nom"] is None
    assert sortie["SANS_ID"]["demandeur_siren"] is None


def test_une_base_ancienne_sans_demandeur_cj_passe_le_premier_snapshot(base, tmp_path):
    """Le chemin de toutes les instances au premier snapshot après fusion : la
    table existe SANS `demandeur_cj`. Aucune erreur SQL, le nom d'une société
    à fiche en 5710 reste ; puis la collecte ajoute et remplit la colonne."""
    from collectors.urbanisme_sitadel import import_rows
    soc = _entreprise(base, "Ancienne SAS", "999000101", "5710")
    ensure_table(base)
    base.execute("ALTER TABLE urbanisme_autorisations DROP COLUMN demandeur_cj")
    base.execute(
        "INSERT INTO urbanisme_autorisations (num_dau, insee, commune, categorie, type_dau,"
        " date_depot, demandeur_nom, demandeur_siren, demandeur_entity_id, adresse)"
        " VALUES ('OLD', '99001', 'Testonville', 'logements', 'PC', '2026-01-01',"
        " 'Ancienne SAS', '999000101', ?, '3 rue des Lilas')", (soc,))
    sortie, _ = _sortie(base, tmp_path, public_ids={soc}, ei_ids=set())
    assert sortie["OLD"]["demandeur_nom"] == "Ancienne SAS"
    ensure_table(base)
    import_rows(base, "logements", [{"COMM": "99001", "NUM_DAU": "OLD", "TYPE_DAU": "PC",
                                     "DENOM_DEM": "Ancienne SAS", "SIREN_DEM": "999000101",
                                     "CJ_DEM": "5710"}])
    assert [r[0] for r in base.execute(
        "SELECT demandeur_cj FROM urbanisme_autorisations")] == ["5710"]


def test_import_rows_prend_en_compte_une_correction_de_cj_dem(base):
    from collectors.urbanisme_sitadel import import_rows
    ensure_table(base)
    ligne = {"COMM": "99001", "NUM_DAU": "CORR", "TYPE_DAU": "PC", "DENOM_DEM": "Quelqu'un"}
    import_rows(base, "logements", [{**ligne, "CJ_DEM": "5499"}])
    import_rows(base, "logements", [{**ligne, "CJ_DEM": "1000"}])
    assert [r[0] for r in base.execute(
        "SELECT demandeur_cj FROM urbanisme_autorisations")] == ["1000"]


def test_une_correction_de_cj_dem_d_un_autre_demandeur_n_est_pas_appliquee(base):
    """Le CJ d'un nouveau demandeur ne s'applique pas au nom de l'ancien."""
    from collectors.urbanisme_sitadel import import_rows
    ensure_table(base)
    ligne = {"COMM": "99001", "NUM_DAU": "TRANSFERT", "TYPE_DAU": "PC", "DENOM_DEM": "Ancien"}
    import_rows(base, "logements", [{**ligne, "SIREN_DEM": "999000111", "CJ_DEM": "5499"}])
    import_rows(base, "logements", [{**ligne, "DENOM_DEM": "Nouveau",
                                     "SIREN_DEM": "999000222", "CJ_DEM": "1000"}])
    assert [tuple(r) for r in base.execute(
        "SELECT demandeur_nom, demandeur_siren, demandeur_cj FROM urbanisme_autorisations")] == [
        ("Ancien", "999000111", "5499")]
