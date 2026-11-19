"""L'identité d'une fiche par son identifiant, avant son nom.

Défaut du 28/10/2026 : la communauté de communes avait deux fiches à Saillans
et à Brassac. Les collecteurs la retrouvent par `epci_nom`, SIRENE par son
propre libellé (« et DU Pays » contre « et DE Pays »), et aucun des deux ne
cherchait par le SIREN. Et quatre fiches fusionnées le 24/09 à Lasalle étaient
revenues : la saisie qui les nomme les recréait par leur nom.

Données fictives : l'instance de test (`tests/instance_test.json`) déclare
« CC des Épreuves Réunies », SIREN 209900019.
"""
from collectors.db import entite_par_siren, pivot_ids, upsert_entity
from collectors.nom_normalise import rectifier, rectifier_nom


def _businesses(base, eid, siren):
    base.execute("INSERT INTO businesses (entity_id, siren) VALUES (?,?)", (eid, siren))


def test_sirene_apres_les_collecteurs_retrouve_la_fiche_epci(base):
    """Ordre de Brassac : la fiche par `epci_nom` existe, SIRENE arrive avec
    un autre libellé et une commune."""
    epci = pivot_ids(base)["epci"]
    eid = upsert_entity(base, type="service", name="CC DES EPREUVES REUNIES DU FICTIF",
                        short_name="209900019", siren="209900019",
                        commune="Testonville")
    assert eid == epci
    assert base.execute("SELECT COUNT(*) FROM entities WHERE type='service'"
                        " AND name LIKE 'CC%'").fetchone()[0] == 1


def test_les_collecteurs_apres_sirene_retrouvent_sa_fiche(base):
    """Ordre de Saillans : SIRENE a écrit la sienne, avec son SIREN ; la fiche
    par `epci_nom` n'est pas créée à côté."""
    sirene = upsert_entity(base, type="service", name="CC DES EPREUVES REUNIES DU FICTIF",
                           siren="209900019", commune="Testonville")
    _businesses(base, sirene, "209900019")
    assert pivot_ids(base)["epci"] == sirene


def test_un_siret_designe_son_siren(base):
    eid = upsert_entity(base, type="business", name="FICTIVE SARL", siren="999000111")
    _businesses(base, eid, "999000111")
    assert entite_par_siren(base, "99900011100017") == eid


def test_un_autre_siren_ne_rattache_rien(base):
    pivot_ids(base)
    assert entite_par_siren(base, "123456789") is None
    assert entite_par_siren(base, "") is None
    assert entite_par_siren(base, "12345") is None


LECTURES = [{"nom": "Caravane Filme", "lecture": "LA CARAVANE FICTIVE"},
            {"nom": "VIVALTEST.", "lecture": "VIV'ALTEST"}]


def test_une_rectification_de_nom_vise_le_nom_entier(monkeypatch):
    monkeypatch.setattr("collectors.config.RECTIFICATIONS", LECTURES)
    assert rectifier_nom("Caravane Filme") == "LA CARAVANE FICTIVE"
    assert rectifier_nom("caravane-filme") == "LA CARAVANE FICTIVE"
    assert rectifier_nom("VIVALTEST") == "VIV'ALTEST"


def test_elle_ne_touche_ni_la_fiche_du_registre_ni_un_nom_voisin(monkeypatch):
    """Le défaut d'une rectification par sous-chaîne, et son contraire."""
    monkeypatch.setattr("collectors.config.RECTIFICATIONS", LECTURES)
    for intact in ("LA CARAVANE FICTIVE", "Caravane Filme et compagnie", None):
        assert rectifier_nom(intact) == intact
    # `rectifier` ignore ces entrées : elles n'ont pas de `source`.
    assert rectifier("Caravane Filme") == "Caravane Filme"


def test_une_saisie_par_nom_rejoint_la_fiche_gardee(base, monkeypatch):
    """Ce qui fait tenir une fusion : la saisie rejouée après coup ne recrée
    pas la fiche absorbée."""
    monkeypatch.setattr("collectors.config.RECTIFICATIONS", LECTURES)
    garde = upsert_entity(base, type="association", name="LA CARAVANE FICTIVE",
                          commune="Testonville")
    assert upsert_entity(base, type="association", name="Caravane Filme") == garde


def test_une_rectification_typee_ne_vaut_que_pour_son_type(monkeypatch):
    monkeypatch.setattr("collectors.config.RECTIFICATIONS",
                        [{"nom": "TRANSMITEST", "lecture": "TRANSEMITEST",
                          "type": "association"}])
    assert rectifier_nom("TRANSMITEST", "association") == "TRANSEMITEST"
    assert rectifier_nom("TRANSMITEST", "business") == "TRANSMITEST"
