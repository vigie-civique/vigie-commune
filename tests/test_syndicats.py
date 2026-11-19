"""Les syndicats auxquels l'intercommunalité adhère : leur fiche et leurs comptes.

Deux défauts relevés le 24/09 à Lasalle, et la règle qui les corrige :
  - `banatic` créait le syndicat par son libellé, à côté de la fiche que
    `sirene` avait déjà posée sous son SIREN — deux fiches, et la relation
    d'adhésion sur celle qui n'avait pas d'identifiant ;
  - sa note était réécrite à chaque passe (trois fois par syndicat).
Et les comptes : ce qui compte comme une dépense, une recette, une dette.
"""
from __future__ import annotations

from collectors.banatic import import_adhesions
from collectors.syndicats_comptes import (agreger, importer,
                                          syndicats_du_territoire)


def _banatic(siren="200091197", libelle="SIAEP de Lasalle"):
    return {"groupementsAdherentsSyndicatMixte": [
        {"libelle": libelle, "siren": siren, "codeNatureJuridique": "SMF",
         "populationTotale": 2416}]}


# ── banatic : la fiche du syndicat ───────────────────────────────────────────

def test_le_syndicat_deja_connu_par_son_siren_nest_pas_recree(base, entite):
    epci = entite("CC Causses Aigoual Cévennes", "service")
    connu = entite("SIAEP DE LASALLE", "service", commune="Lasalle")
    base.execute("INSERT INTO businesses (entity_id, siren) VALUES (?,?)",
                 (connu, "200091197"))
    import_adhesions(base, epci, _banatic(), dry_run=False)
    assert base.execute("SELECT count(*) FROM entities WHERE type='service'"
                        " AND name LIKE 'SIAEP%'").fetchone()[0] == 1
    assert base.execute(
        "SELECT to_id FROM relations WHERE relation_type='adhère_à'"
    ).fetchone()[0] == connu


def test_un_syndicat_nouveau_porte_son_siren(base, entite):
    epci = entite("CC Causses Aigoual Cévennes", "service")
    import_adhesions(base, epci, _banatic("253002711", "SM EPTB Gardons"),
                     dry_run=False)
    row = base.execute(
        "SELECT e.name, b.siren FROM entities e JOIN businesses b"
        " ON b.entity_id = e.id").fetchone()
    assert tuple(row) == ("SM EPTB Gardons", "253002711")


def test_rejouer_banatic_necrit_pas_la_note_deux_fois(base, entite):
    epci = entite("CC Causses Aigoual Cévennes", "service")
    for _ in range(3):
        import_adhesions(base, epci, _banatic(), dry_run=False)
    assert base.execute("SELECT count(*) FROM entity_notes"
                        " WHERE source='BANATIC'").fetchone()[0] == 1


# ── Les syndicats du territoire ──────────────────────────────────────────────

def test_le_siren_se_lit_dans_la_note_quand_la_fiche_nen_a_pas(base, entite):
    """Une base où `banatic` n'a pas été rejoué depuis la correction."""
    epci = entite("CC", "service")
    synd = entite("SM EPTB Gardons", "service")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, source)"
                 " VALUES (?,?,'adhère_à','banatic')", (epci, synd))
    base.execute("INSERT INTO entity_notes (entity_id, note, source)"
                 " VALUES (?,?,'BANATIC')",
                 (synd, "SIREN: 253002711 | Nature: SMF | Population: 386290"))
    assert syndicats_du_territoire(base) == [(synd, "253002711", "SM EPTB Gardons")]


def test_un_syndicat_sans_siren_nest_pas_cherche_par_son_nom(base, entite):
    epci = entite("CC", "service")
    synd = entite("Syndicat sans identifiant", "service")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, source)"
                 " VALUES (?,?,'adhère_à','banatic')", (epci, synd))
    assert syndicats_du_territoire(base) == []


# ── Les comptes ──────────────────────────────────────────────────────────────

def _compte(compte, exer="2024", ident="20009119700018", **montants):
    rec = {"compte": compte, "exer": f"{exer}-01-01T00:00:00+00:00",
           "ident": ident, "lbudg": "SIAEP DE LASALLE", "nomen": "M49A",
           "obnetdeb": 0.0, "obnetcre": 0.0, "sd": 0.0, "sc": 0.0}
    rec.update(montants)
    return rec


def test_les_amortissements_ne_sont_pas_une_depense():
    """Une dotation aux amortissements (68) est une écriture d'ordre : au SIAEP
    de Lasalle en 2024, elle pesait 60 k€ sur 109 k€ de classe 6."""
    postes = agreger([_compte("6156", obnetdeb=303.0),
                      _compte("6811", obnetdeb=60000.0)])[(2024, "20009119700018")]["postes"]
    assert postes["depenses_fonctionnement"] == 303.0


def test_une_annulation_se_deduit():
    postes = agreger([_compte("7011", obnetcre=1000.0, obnetdeb=100.0)]
                     )[(2024, "20009119700018")]["postes"]
    assert postes["recettes_fonctionnement"] == 900.0
    assert postes["ventes_et_redevances"] == 900.0


def test_la_dette_est_un_solde_crediteur():
    postes = agreger([_compte("1641", sc=545454.01, obnetdeb=20000.0)]
                     )[(2024, "20009119700018")]["postes"]
    assert postes["encours_dette"] == 545454.01


def test_la_subvention_dequipement_versee_nest_pas_un_investissement_propre():
    postes = agreger([_compte("2041", obnetdeb=5000.0),
                      _compte("2315", obnetdeb=1000.0)])[(2024, "20009119700018")]["postes"]
    assert postes["depenses_investissement"] == 1000.0
    assert postes["subventions_equipement_versees"] == 5000.0


def test_le_financeur_se_lit_dans_le_compte():
    postes = agreger([_compte("7477", obnetcre=10.0), _compte("1317", obnetcre=20.0),
                      _compte("7478", obnetcre=5.0)])[(2024, "20009119700018")]["postes"]
    assert postes["subventions_europe"] == 30.0
    assert postes["subventions_autres"] == 5.0


def test_deux_budgets_ne_sadditionnent_pas():
    blocs = agreger([_compte("6156", obnetdeb=1.0, ident="A"),
                     _compte("6156", obnetdeb=2.0, ident="B")])
    assert set(blocs) == {(2024, "A"), (2024, "B")}


def test_rejouer_met_a_jour_sans_dupliquer(base, entite):
    synd = entite("SIAEP", "service")
    importer(base, synd, "200091197", [_compte("6156", obnetdeb=303.0)])
    importer(base, synd, "200091197", [_compte("6156", obnetdeb=404.0)])
    rows = base.execute("SELECT montant FROM comptes_syndicats"
                        " WHERE poste='depenses_fonctionnement'").fetchall()
    assert [r[0] for r in rows] == [404.0]


# ── L'encart de la fiche ─────────────────────────────────────────────────────

def _snapshot():
    import importlib.util
    import sys
    from pathlib import Path
    racine = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(racine))
    spec = importlib.util.spec_from_file_location(
        "snapshot_sous_test", racine / "scripts" / "build_public_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_la_fiche_recoit_ses_comptes_par_exercice_et_par_budget(base, entite):
    synd = entite("SIAEP", "service")
    importer(base, synd, "200093797", [
        _compte("6156", exer="2024", ident="PRINCIPAL", obnetdeb=100.0),
        _compte("6156", exer="2024", ident="ANNEXE", obnetdeb=7.0),
        _compte("6156", exer="2023", ident="PRINCIPAL", obnetdeb=90.0),
    ])
    comptes = _snapshot().comptes_syndicats_par_entite(base)[synd]
    assert [c["year"] for c in comptes] == [2024, 2023]
    montants = sorted(b["postes"]["depenses_fonctionnement"]
                      for b in comptes[0]["budgets"])
    assert montants == [7, 100], "deux budgets ne s'additionnent pas"


def test_sans_table_la_fiche_na_pas_dencart(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "ancienne.db")
    conn.row_factory = sqlite3.Row
    assert _snapshot().comptes_syndicats_par_entite(conn) == {}


# ── L'intercommunalité elle-même (23/10/2026) ────────────────────────────────

def test_les_associations_se_lisent_a_part_des_communes():
    """6574x : personnes de droit privé ; 6573x : communes, CCAS, budgets annexes."""
    postes = agreger([_compte("65748", obnetdeb=287000.0),
                      _compte("657341", obnetdeb=12000.0)])[(2024, "20009119700018")]["postes"]
    assert postes["subventions_versees"] == 299000.0
    assert postes["subventions_personnes_privees"] == 287000.0


def test_l_epci_se_trouve_par_sa_configuration(base, entite, monkeypatch):
    import collectors.config as config
    from collectors.syndicats_comptes import epci_du_territoire
    monkeypatch.setattr(config, "EPCI_NOM", "CC des Épreuves")
    monkeypatch.setattr(config, "EPCI_SIREN", "209900019")
    from collectors.nom_normalise import normaliser
    eid = entite("CC des Épreuves", "service")
    base.execute("UPDATE entities SET name_norm=? WHERE id=?", (normaliser("CC des Épreuves"), eid))
    assert epci_du_territoire(base) == (eid, "209900019", "CC des Épreuves")
    monkeypatch.setattr(config, "EPCI_SIREN", "")
    assert epci_du_territoire(base) is None


def test_l_epci_ecrit_par_sirene_se_trouve_par_son_siren(base, entite, monkeypatch):
    # Dossier national du 31/10/2026 : la seule fiche de la communauté portait
    # le libellé du registre, et le nom de la configuration ne la trouvait pas.
    import collectors.config as config
    from collectors.syndicats_comptes import epci_du_territoire
    monkeypatch.setattr(config, "EPCI_NOM", "CC des Épreuves")
    monkeypatch.setattr(config, "EPCI_SIREN", "209900019")
    eid = entite("COMMUNAUTE DE COMMUNES DES EPREUVES", "service")
    base.execute("INSERT INTO businesses (entity_id, siren) VALUES (?, '209900019')", (eid,))
    assert epci_du_territoire(base) == (eid, "209900019", "CC des Épreuves")


def test_la_fiche_de_l_epci_dit_que_ce_sont_ses_comptes(base, entite):
    epci = entite("CC", "service")
    synd = entite("SIAEP", "service")
    importer(base, epci, "209900019", [_compte("65748", obnetdeb=5.0)], source="dgfip-gfp")
    importer(base, synd, "200091197", [_compte("6156", obnetdeb=1.0)])
    comptes = _snapshot().comptes_syndicats_par_entite(base)
    assert comptes[epci][0]["de"] == "epci"
    assert comptes[synd][0]["de"] == "syndicat"


def test_une_balance_non_lue_met_le_step_en_echec(tmp_path, schema_sql, monkeypatch):
    import sqlite3
    import pytest
    from collectors import syndicats_comptes as sc
    chemin = tmp_path / "b.db"
    c = sqlite3.connect(chemin)
    c.executescript(schema_sql)
    c.close()
    monkeypatch.setattr(sc, "DB_PATH", chemin)
    monkeypatch.setattr(sc, "epci_du_territoire", lambda conn: (None, "209900019", "CC"))

    def panne(*a, **k):
        raise OSError("réseau")
    monkeypatch.setattr(sc, "telecharger", panne)
    with pytest.raises(RuntimeError, match="209900019"):
        sc.run()


def test_une_intercommunalite_sans_fiche_ne_se_lit_pas_en_silence(tmp_path, schema_sql, monkeypatch):
    """Écrite sans fiche, la balance aurait `entity_id` NULL, que le snapshot
    écarte : le journal aurait compté des postes que personne ne verrait."""
    import sqlite3
    import pytest
    from collectors import syndicats_comptes as sc
    chemin = tmp_path / "b.db"
    c = sqlite3.connect(chemin)
    c.executescript(schema_sql)
    c.close()
    monkeypatch.setattr(sc, "DB_PATH", chemin)
    monkeypatch.setattr(sc, "epci_du_territoire", lambda conn: (None, "209900019", "CC"))
    monkeypatch.setattr(sc, "telecharger", lambda *a, **k: [_compte("65748", obnetdeb=5.0)])
    with pytest.raises(RuntimeError, match="209900019"):
        sc.run()
    c = sqlite3.connect(chemin)
    assert c.execute("SELECT COUNT(*) FROM comptes_syndicats").fetchone()[0] == 0



def test_un_delai_depasse_se_lit_comme_une_panne_passagere(tmp_path, schema_sql, monkeypatch, capsys):
    """Le 31/10, l'export DGFiP est resté muet un quart d'heure : chaque syndicat
    tombait en délai dépassé, et le service local classait le step en défaut
    faute de reconnaître le message. Les lignes suivent la forme qu'il lit
    (`portail-local/local_service/server.py`, DETAIL et CROIX)."""
    import re
    import socket
    import sqlite3
    import pytest
    from collectors import syndicats_comptes as sc
    chemin = tmp_path / "b.db"
    c = sqlite3.connect(chemin)
    c.executescript(schema_sql)
    c.close()
    monkeypatch.setattr(sc, "DB_PATH", chemin)
    monkeypatch.setattr(sc, "epci_du_territoire", lambda conn: (1, "209900019", "CC"))
    monkeypatch.setattr(sc, "syndicats_du_territoire",
                        lambda conn: [(2, "252703863", "Syndicat de prévention, collecte")])

    def muet(*a, **k):
        raise socket.timeout("The read operation timed out")
    monkeypatch.setattr(sc, "telecharger", muet)
    with pytest.raises(RuntimeError) as e:
        sc.run()
    assert str(e.value) == "relevés en échec : 209900019, 252703863"
    details = re.findall(r"\[(\w+)\] (?:\d[\dAB]\d{3} )?(.+?) : échec → (.*)", capsys.readouterr().out)
    assert {(step, cle) for step, cle, _ in details} == {
        ("syndicats", "209900019"), ("syndicats", "252703863")}
    assert all("timed out" in motif for _, _, motif in details)

# ── Trois règles, relevées sur la balance 2025 de la CC (24/10/2026) ─────────
# Lignes recopiées du jeu DGFiP `balances-comptables-des-groupements-a-
# fiscalite-propre-depuis-2010`, SIREN 200034601 : la page servait −900 653 €
# de dette, −7 339 € de personnel, et des ventes au-dessus des recettes.

def _cc(compte, ident, **montants):
    return _compte(compte, exer="2025", ident=ident, **{"oobdeb": 0.0, "oobcre": 0.0, **montants})


def test_une_ecriture_dordre_ne_compte_ni_en_recette_ni_en_depense():
    """OBNET comprend l'ordre ; la variation de stocks (7135) et la sortie d'un
    bien (21321 crédité par ordre) ne sont pas des opérations réelles."""
    bois = "20003460100043"
    postes = agreger([
        _cc("701", bois, obnetcre=9558.0),
        _cc("7135", bois, obnetdeb=6089.2, obnetcre=3350.0, oobdeb=6089.2, oobcre=3350.0),
    ])[(2025, bois)]["postes"]
    assert postes["recettes_fonctionnement"] == postes["ventes_et_redevances"] == 9558.0

    principal = "20003460100019"
    postes = agreger([
        _cc("2111", principal),
        _cc("21321", principal, obnetcre=220444.45, oobcre=220444.45),
    ])[(2025, principal)]["postes"]
    assert postes["depenses_investissement"] == 0.0             # et non −220 444,45


def test_le_personnel_est_le_chapitre_012():
    """Le budget Déchets paie son personnel en 6215 ; son 64198 (remboursements
    sur rémunérations) est une atténuation, pas du personnel en moins."""
    dechets = "20003460100092"
    postes = agreger([
        _cc("6215", dechets, obnetdeb=586397.83),
        _cc("6475", dechets, obnetdeb=111.6),
        _cc("64198", dechets, obnetcre=7451.04),
    ])[(2025, dechets)]["postes"]
    assert round(postes["personnel"], 2) == 586509.43


def test_un_encours_negatif_nest_pas_une_dette():
    """Le 1641 du budget eau n'a jamais été crédité : 900 653 € de capital
    remboursé, pas une dette de −900 653 €. Le budget Déchets, lui, a un vrai
    encours."""
    eau, dechets = "20003460100100", "20003460100092"
    comptes = agreger([
        _cc("1641", eau, bedeb=640530.01, obnetdeb=260123.13, sd=900653.14),
        _cc("1641", dechets, becre=553109.52, obnetdeb=43388.44, sc=509721.08),
    ])
    assert "encours_dette" not in comptes[(2025, eau)]["postes"]
    assert comptes[(2025, eau)]["postes"]["encours_dette_non_lisible"] == 900653.14
    assert comptes[(2025, dechets)]["postes"]["encours_dette"] == 509721.08


def test_un_poste_que_la_regle_ne_produit_plus_disparait_au_rejeu(base, entite):
    eau = "20003460100100"
    cc = entite("CC", "service")
    importer(base, cc, "200034601", [_cc("1641", eau, sc=1000.0)], source="dgfip-gfp")
    importer(base, cc, "200034601", [_cc("1641", eau, sd=900653.14)], source="dgfip-gfp")
    postes = {r[0] for r in base.execute("SELECT poste FROM comptes_syndicats")}
    assert postes == {"encours_dette_non_lisible"}


def test_les_remboursements_de_la_m57_developpee_ne_sont_pas_du_personnel():
    """6439 et 6449 sont au chapitre 013 en M57 développée (plans de comptes
    normatifs 2023-2026) : des recettes, comme 6419."""
    m57 = "20003460100019"
    postes = agreger([
        _compte("64111", exer="2025", ident=m57, obnetdeb=1000.0),
        _compte("6439", exer="2025", ident=m57, obnetcre=100.0),
        _compte("6449", exer="2025", ident=m57, obnetcre=50.0),
    ])[(2025, m57)]["postes"]
    assert postes["personnel"] == 1000.0
