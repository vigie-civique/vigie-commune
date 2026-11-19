"""Le prix de l'eau : lecture des classeurs, rattachement, et écriture.

Aucun appel réseau. Les formes éprouvées ici sont celles que les sources rendent
réellement, relevées le 02/09/2026 : quatre conventions d'en-têtes pour un seul
champ, un vide écrit « . », des nombres tantôt nombres tantôt chaînes, et une
API qui rend deux fois la même ligne.

Ce que ces essais protègent : une série de prix ne vaut que si chaque valeur est
bien celle du service qu'on croit, à l'année qu'on croit.
"""
from __future__ import annotations

import pytest

import io
import urllib.error
import zipfile

from collectors import sispea
from collectors.sispea import (INDICATEURS, _ecrire_indicateur, _ecrire_service,
                               _format_reel, _indice, _mode, composition,
                               ensure_tables, nombre, plat)


class TestFormesDeLaSource:
    def test_les_quatre_conventions_den_tete_se_rejoignent(self):
        """Un seul champ, quatre graphies selon la génération de l'export.

        2015-2019 « Id SISPEA du service », 2020-2023 `id_sispea_serv`, 2024
        « Id SISPEA de l'entité de gestion ». Et l'assainissement écrit
        `N_iNSEE_si_commune`, avec une capitale au milieu d'un mot.
        """
        assert plat("N° INSEE si commune") == plat("N_INSEE_si_commune") == plat("n_insee_si_commune")
        assert plat("N_iNSEE_si_commune") == "n_insee_si_commune"
        assert plat("Id SISPEA de l'entité de gestion") == "id_sispea_de_l_entite_de_gestion"
        assert plat("Mode de gestion") == "mode_de_gestion"

    def test_le_vide_secrit_un_point(self):
        """« . » est le vide de ces classeurs. Le prendre pour un nombre
        remplirait la base de valeurs qui n'existent pas."""
        assert nombre(".") is None
        assert nombre("") is None
        assert nombre(None) is None

    def test_un_nombre_est_parfois_une_chaine(self):
        """2023 écrit '4.21', 2024 écrit 4.63, et un poste français peut écrire
        une virgule décimale."""
        assert nombre("4.21") == pytest.approx(4.21)
        assert nombre(4.63) == pytest.approx(4.63)
        assert nombre("2,18") == pytest.approx(2.18)
        assert nombre(4) == 4.0

    def test_le_format_se_lit_aux_octets_pas_au_nom(self, tmp_path):
        """🔴 L'extension MENT : une archive livre un `.xls` qui est un `.xlsx`.
        Le nom ne décide de rien."""
        menteur = tmp_path / "SISPEA_2021_AC.xls"
        menteur.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
        assert _format_reel(menteur) == "xlsx"

        ancien = tmp_path / "SISPEA_2024_AEP.xls"
        ancien.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)
        assert _format_reel(ancien) == "xls"

        etranger = tmp_path / "surprise.xls"
        etranger.write_bytes(b"%PDF" + b"\x00" * 32)
        with pytest.raises(RuntimeError, match="ni xlsx ni xls"):
            _format_reel(etranger)

    def test_une_colonne_absente_arrete_tout_en_nommant_ce_quon_a_vu(self):
        """Un index à demi rempli, écrit en silence, serait pire qu'un échec :
        personne ne saurait que la moitié des prix manque."""
        with pytest.raises(RuntimeError) as arret:
            _indice(["dpt", "nom_coll"], ("id_sispea_serv",), "service", "SISPEA_2019.xlsx")
        assert "service" in str(arret.value)
        assert "nom_coll" in str(arret.value), "le message doit montrer les en-têtes lus"


# L'en-tête réel de la composition communale (2015-2025), réduit aux colonnes
# lues plus une qui ne doit PAS entrer : la population de la commune.
ENTETE_COMPOSITION = (
    "Code INSEE de la commune adhérente",
    "Identifiant SISPEA de l'entité de gestion à laquelle la commune adhère",
    "Nom de l'entité de gestion à laquelle la commune adhère",
    "Nom de la collectivité de l'entité de gestion à laquelle la commune adhère",
    "Type de collectivité", "N° SIREN", "Type du mode de gestion", "Nom de l'opérateur",
    "Statut de l'opérateur", "Date de début du mode de gestion",
    "Date de fin du mode de gestion", "Population de la commune",
)


def _classeur_zippe(lignes) -> bytes:
    import openpyxl
    cahier = openpyxl.Workbook()
    feuille = cahier.active
    feuille.append(ENTETE_COMPOSITION)
    for ligne in lignes:
        feuille.append(ligne)
    xlsx = io.BytesIO()
    cahier.save(xlsx)
    zippe = io.BytesIO()
    with zipfile.ZipFile(zippe, "w") as archive:
        archive.writestr("CompositionCommunaleServices_AEP_2024.xlsx", xlsx.getvalue())
    return zippe.getvalue()


class _Reponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TestRattachement:
    """La composition communale de l'Observatoire, qui remplace Hub'Eau
    (API décommissionnée le 10/09/2026 : 410 Gone, et zéro service collecté
    sans que le step échoue)."""

    @pytest.fixture
    def magasin(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sispea, "CACHE", tmp_path)
        return tmp_path

    def test_une_annee_se_lit_commune_par_commune(self, magasin, monkeypatch):
        """Le classeur écrit 1001.0 pour 01001 ; une commune peut dépendre de
        deux services (deux secteurs) ; un service dessert plusieurs communes."""
        pytest.importorskip("openpyxl", reason="job « tests-deps »")
        from datetime import datetime
        debut, fin = datetime(2022, 4, 1, 12), datetime(2031, 3, 31, 12)
        brut = _classeur_zippe([
            (1001.0, 182741.0, "eau potable : Veyle", "SYNDICAT BRESSE", "Syndicat Mixte",
             200086353.0, "Délégation", "SAUR", "Entreprise privée", debut, fin, 848),
            (1002.0, 182741.0, "eau potable : Veyle", "SYNDICAT BRESSE", "Syndicat Mixte",
             200086353.0, "Délégation", "SAUR", "Entreprise privée", debut, fin, 273),
            (1002.0, 182248.0, "eau potable", "L'Abergement", "Commune",
             210100020.0, "Régie à autonomie financière", None, "Régie à autonomie financière",
             None, None, 273),
        ])
        monkeypatch.setattr(sispea.urllib.request, "urlopen",
                            lambda *_a, **_k: _Reponse(brut))
        annee = composition("AEP", 2024)

        assert annee["communes"] == {"01001": ["182741"], "01002": ["182741", "182248"]}
        syndicat = annee["services"]["182741"]
        assert syndicat["communes"] == "01001,01002"
        assert syndicat["siren"] == "200086353"
        assert annee["services"]["182248"]["mode_gestion"] == "Régie"
        assert (syndicat["operateur"], syndicat["statut_operateur"]) == ("SAUR", "Entreprise privée")
        assert (syndicat["debut_gestion"], syndicat["fin_gestion"]) == ("2022-04-01", "2031-03-31")
        assert syndicat["annee_operateur"] == "2024"
        assert annee["services"]["182248"]["operateur"] is None
        assert "848" not in str(annee), "une colonne hors liste blanche est entrée"
        assert (magasin / "composition" / "AEP-2024.json").is_file()

    def test_une_source_retiree_fait_echouer(self, magasin, monkeypatch):
        """🔴 Hub'Eau rendait 410 et le collecteur rendait `[]` : step « réussi »,
        dossiers sans eau. Un 410 est une panne, il doit se voir."""
        def _gone(requete, **_):
            raise urllib.error.HTTPError(requete.full_url, 410, "Gone", {}, None)
        monkeypatch.setattr(sispea.urllib.request, "urlopen", _gone)
        with pytest.raises(urllib.error.HTTPError):
            composition("AEP", 2024)

    def test_une_commune_sans_eau_potable_fait_echouer(self, monkeypatch):
        """Toute commune a un service d'eau potable. N'en trouver aucun dit que
        la source a changé, pas que la commune n'a pas d'eau."""
        monkeypatch.setattr(sispea, "communes_du_step", lambda _s: ["30140", "99999"])
        monkeypatch.setattr(sispea, "composition", lambda comp, annee: {
            "communes": {"30140": ["1"]},
            "services": {"1": {"libelle": comp}}})
        with pytest.raises(RuntimeError, match="99999"):
            sispea._moisson_composition()

    def test_le_mode_se_ramene_au_mot_que_l_ofb_ecrit(self):
        """La composition détaille (« Régie à autonomie financière »), l'OFB écrit
        « Regie ». Sans ce retour au mot, la graphie sans accent l'emportait."""
        assert _mode("Régie à autonomie financière et dotée de la personne morale") == "Régie"
        assert _mode("Délégation") == "Délégation"
        assert _mode("Gérance") == "Gérance"
        assert _mode(None) is None


class TestEcriture:
    def test_zero_nest_pas_un_prix_mais_reste_un_taux(self, base):
        """Des services déclarent 0 €/m³ : une case laissée vide, pas une eau
        gratuite. Le zéro reste légitime ailleurs — un service peut n'avoir
        renouvelé aucune canalisation dans l'année."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"nom": "Régie d'essai"})
        assert _ecrire_indicateur(base, "1", 2024, "AEP", "P107.2", 0.0, "ofb")
        prix_ecrit = base.execute(
            "SELECT COUNT(*) FROM sispea_indicateurs WHERE code='D102.0'").fetchone()[0]
        assert prix_ecrit == 0, "aucun prix n'a été écrit, et c'est bien le sujet"
        assert base.execute(
            "SELECT valeur FROM sispea_indicateurs WHERE code='P107.2'").fetchone()[0] == 0.0

    def test_le_fichier_du_producteur_fait_foi_sur_l_api_qui_le_republie(self, base):
        """Les deux sources se recouvrent sur 2015-2019. À valeur discordante,
        c'est l'extraction de l'OFB qui l'emporte, quel que soit l'ordre de
        passage — sans quoi le résultat dépendrait de qui a couru le premier."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"nom": "Régie d'essai"})

        _ecrire_indicateur(base, "1", 2019, "AEP", "D102.0", 2.04, "hubeau")
        _ecrire_indicateur(base, "1", 2019, "AEP", "D102.0", 2.11, "ofb")
        ligne = base.execute(
            "SELECT valeur, origine FROM sispea_indicateurs WHERE annee=2019").fetchone()
        assert (ligne["valeur"], ligne["origine"]) == (2.11, "ofb")

        # Et dans l'autre sens : Hub'Eau ne doit pas reprendre la main.
        _ecrire_indicateur(base, "1", 2019, "AEP", "D102.0", 2.04, "hubeau")
        ligne = base.execute(
            "SELECT valeur, origine FROM sispea_indicateurs WHERE annee=2019").fetchone()
        assert (ligne["valeur"], ligne["origine"]) == (2.11, "ofb")

    def test_une_source_complete_l_autre_sans_l_effacer(self, base):
        """Hub'Eau donne le SIREN et les communes desservies, l'OFB un nom de
        collectivité plus complet. Un `INSERT OR REPLACE` aurait fait perdre à
        chaque passage ce que l'autre source venait d'apporter."""
        ensure_tables(base)
        _ecrire_service(base, "173349", "AEP", {
            "nom": None, "siren": "253000426", "communes": "30140,30236",
            "type_collectivite": "Syndicat Intercommunal à Vocation Unique"})
        _ecrire_service(base, "173349", "AEP", {
            "nom": "SYNDICAT AEP DE LA REGION", "mode_gestion": "Délégation"})

        ligne = base.execute("SELECT * FROM sispea_services").fetchone()
        assert ligne["nom"] == "SYNDICAT AEP DE LA REGION"
        assert ligne["mode_gestion"] == "Délégation"
        assert ligne["siren"] == "253000426", "le SIREN de la composition a été effacé"
        assert ligne["communes"] == "30140,30236"

    def test_un_retour_en_regie_efface_l_ancien_delegataire(self, base):
        """L'opérateur n'a qu'une source : quand elle le dit vide, il l'est. Et
        l'OFB, qui ne le porte pas, ne l'efface pas."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Délégation", "operateur": "SAUR",
                                           "fin_gestion": "2024-12-31"})
        _ecrire_service(base, "1", "AEP", {"nom": "SYNDICAT"})          # passage de l'OFB
        assert base.execute("SELECT operateur FROM sispea_services").fetchone()[0] == "SAUR"
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Régie", "operateur": None,
                                           "statut_operateur": "Régie simple",
                                           "debut_gestion": None, "fin_gestion": None})
        ligne = base.execute("SELECT operateur, fin_gestion FROM sispea_services").fetchone()
        assert (ligne["operateur"], ligne["fin_gestion"]) == (None, None)

    def test_une_base_ancienne_recoit_les_colonnes_de_l_operateur(self, base):
        """Les instances ont créé `sispea_services` avant le 26/10/2026 :
        `CREATE TABLE IF NOT EXISTS` ne leur ajoute rien."""
        base.execute("CREATE TABLE sispea_services (code_service TEXT PRIMARY KEY,"
                     " competence TEXT NOT NULL, nom TEXT, libelle TEXT, type_collectivite TEXT,"
                     " mode_gestion TEXT, siren TEXT, communes TEXT)")
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"operateur": "VEOLIA"})
        assert base.execute("SELECT operateur FROM sispea_services").fetchone()[0] == "VEOLIA"

    def test_chaque_mesure_porte_son_libelle_et_son_unite(self, base):
        """Une valeur nue ne se lit pas : 72,5 est un pourcentage, 2,04 des
        euros par mètre cube. Les deux viennent de la même table, écrite une
        fois pour toutes."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {})
        _ecrire_indicateur(base, "1", 2024, "AEP", "D102.0", 2.18, "ofb")
        ligne = base.execute("SELECT libelle, unite FROM sispea_indicateurs").fetchone()
        assert ligne["unite"] == "€/m³"
        assert "120 m³" in ligne["libelle"]


class TestDeclaration:
    def test_les_deux_competences_declarent_leur_prix(self):
        """L'eau potable et l'assainissement se paient sur la même facture :
        n'en collecter qu'une la ferait lire pour le tout."""
        assert "D102.0" in INDICATEURS["AEP"]
        assert "D204.0" in INDICATEURS["AC"]

    def test_le_step_est_declare_dans_la_configuration(self):
        """Un collecteur qui n'est cité par personne ne tourne jamais — le
        défaut qui avait laissé une base sans budget alors que le site en
        publiait les pages. Ces deux tables-là se lisent sans les collecteurs."""
        from collectors.config import PROFONDEUR_STEP, STEP_META
        assert "sispea" in STEP_META
        assert PROFONDEUR_STEP["sispea"] == "fond"

    def test_le_step_est_appelable(self):
        """`run_all` importe les quarante collecteurs, donc la lecture des PDF.
        Le job de tests léger ne les installe pas — la suite ne doit dépendre ni
        des collecteurs ni de l'API —, `tests-deps` les joue. Sans ce garde-fou,
        ce test a fait rougir la CI sur `pdfplumber`, exactement comme le test
        de cohérence des steps le 26/08."""
        pytest.importorskip("pdfplumber", reason="job « tests-deps »")
        from collectors.run_all import STEPS
        assert "sispea" in STEPS


class TestDegradation:
    def test_l_absence_des_millesimes_ne_perd_pas_la_serie(self, base, monkeypatch, capsys):
        """Les exercices demandent un téléchargement national, `bsdtar` et
        parfois `xlrd`. Leur absence n'efface rien de ce qui est en base, et se
        DIT (`run_sispea` en fait ensuite un échec du step).
        """
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"nom": "Régie d'essai"})
        _ecrire_indicateur(base, "1", 2019, "AEP", "D102.0", 2.04, "hubeau")

        def _absent(_competence):
            raise RuntimeError("bsdtar est introuvable")
        monkeypatch.setattr(sispea, "_archives", _absent)

        releve = {"services": 1, "mesures": 1, "millesimes": 0, "annees": set()}
        motif = sispea.completer_par_millesimes(base, {"1": "AEP"}, releve)

        assert motif and "bsdtar" in motif
        assert releve["millesimes_erreur"]
        # La série déjà en base est intacte : c'est tout l'objet.
        assert base.execute("SELECT COUNT(*) FROM sispea_indicateurs").fetchone()[0] == 1
        # Et la dégradation se DIT : une souplesse muette est un défaut.
        assert "exercices récents indisponibles" in capsys.readouterr().out


class TestGraphies:
    def test_les_accents_survivent_au_passage_de_l_ofb(self, base):
        """🔴 Hub'Eau écrit « Régie », les extractions de l'OFB « Regie ». La
        source qui passe en dernier l'emportant, la base se remplissait de mots
        français sans leurs accents, et deux services d'une même commune
        s'affichaient sous deux graphies du même mot."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Régie"})
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Regie"})
        assert base.execute("SELECT mode_gestion FROM sispea_services").fetchone()[0] == "Régie"

        # Et dans l'ordre inverse : la graphie accentuée gagne quand elle arrive.
        _ecrire_service(base, "2", "AEP", {"mode_gestion": "Delegation"})
        _ecrire_service(base, "2", "AEP", {"mode_gestion": "Délégation"})
        assert base.execute(
            "SELECT mode_gestion FROM sispea_services WHERE code_service='2'").fetchone()[0] == "Délégation"

    def test_une_valeur_qui_change_vraiment_l_emporte(self, base):
        """La règle ne fige rien : elle départage deux GRAPHIES. Un service qui
        passe de la régie à la délégation doit se voir — c'est même l'un des
        faits les plus intéressants qu'on puisse lire ici."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Régie"})
        _ecrire_service(base, "1", "AEP", {"mode_gestion": "Délégation"})
        assert base.execute("SELECT mode_gestion FROM sispea_services").fetchone()[0] == "Délégation"

    def test_le_nom_du_service_et_celui_de_la_collectivite_ne_se_melangent_pas(self, base):
        """Hub'Eau nomme le SERVICE (« eau potable »), l'OFB la COLLECTIVITÉ.
        Rangés dans la même colonne, l'un chassait l'autre selon la source
        arrivée en dernier : deux services voisins s'affichaient, l'un sous le
        nom de sa commune, l'autre sous le mot « eau potable »."""
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {"libelle": "eau potable"})
        _ecrire_service(base, "1", "AEP", {"nom": "SYNDICAT AEP DE LA REGION"})
        ligne = base.execute("SELECT nom, libelle FROM sispea_services").fetchone()
        assert ligne["libelle"] == "eau potable"
        assert ligne["nom"] == "SYNDICAT AEP DE LA REGION"


class TestPublicationDeLOperateur:
    """`gestionnaire_publiable` (snapshot) : l'avis vigie-dpo du 26/10/2026 en code."""

    @pytest.fixture
    def regle(self):
        from scripts.snapshot.territoire import gestionnaire_publiable
        return gestionnaire_publiable

    def test_un_delegataire_sort_avec_sa_fin_de_contrat(self, regle):
        g = regle({"operateur": "VEOLIA", "statut_operateur": "Entreprise privée",
                   "mode_gestion": "Délégation", "fin_gestion": "2028-12-31",
                   "annee_operateur": "2025"})
        assert g == {"role": "delegataire", "nom": "VEOLIA", "fin": "2028-12-31", "annee": "2025"}

    def test_un_statut_hors_liste_ne_sort_pas(self, regle):
        """Liste FERMÉE : un statut nouveau du producteur — une personne physique,
        demain — tombe dehors par défaut."""
        assert regle({"operateur": "DUPONT Jean", "statut_operateur": "Exploitant individuel"}) is None
        assert regle({"operateur": "VEOLIA", "statut_operateur": None}) is None

    @pytest.mark.parametrize("nom", ["?", "Inconnu", "procédure en cours", "gestion en regie",
                                     "régie interne", "Communes", "différents délégataires"])
    def test_un_faux_nom_ne_sort_pas(self, regle, nom):
        assert regle({"operateur": nom, "statut_operateur": "Entreprise privée"}) is None

    def test_une_regie_personnalisee_garde_son_nom(self, regle):
        """« Régie » n'est pas un faux nom : c'est la phrase « gestion en régie » qui l'est."""
        g = regle({"operateur": "Régie de l'Eau Bordeaux Métropole", "mode_gestion": "Régie",
                   "statut_operateur": "Régie à autonomie financière et dotée de la personne morale"})
        assert g["nom"] == "Régie de l'Eau Bordeaux Métropole"

    def test_l_adresse_collee_au_nom_est_coupee(self, regle):
        g = regle({"operateur": "SAUR - 71100 Chalons sur Saone",
                   "statut_operateur": "Entreprise privée"})
        assert g["nom"] == "SAUR"

    def test_en_regie_c_est_un_prestataire(self, regle):
        """202 services en régie déclarent un opérateur : un marché de prestation,
        pas une délégation. « Délégation à X » serait faux."""
        g = regle({"operateur": "SAUR", "statut_operateur": "Entreprise privée",
                   "mode_gestion": "Régie"})
        assert g["role"] == "prestataire"

    def test_une_fin_de_contrat_depassee_ne_sort_pas(self, regle):
        g = regle({"operateur": "SUEZ", "statut_operateur": "Entreprise privée",
                   "mode_gestion": "Délégation", "fin_gestion": "2014-06-30",
                   "annee_operateur": "2015"})
        assert g["fin"] is None and g["nom"] == "SUEZ"

    def test_le_snapshot_ne_sort_que_le_gestionnaire_filtre(self, base, tmp_path):
        """De la base au fichier publié : l'opérateur sort par la règle, jamais en
        colonnes brutes (statut, dates, nom refusé)."""
        import json
        from scripts.snapshot.territoire import etape_environnement
        ensure_tables(base)
        _ecrire_service(base, "1", "AEP", {
            "nom": "SYNDICAT", "mode_gestion": "Délégation", "operateur": "SAUR - 71100 Chalon",
            "statut_operateur": "Entreprise privée", "debut_gestion": "2022-01-01",
            "fin_gestion": "2031-03-31", "annee_operateur": "2025"})
        _ecrire_service(base, "2", "AC", {"nom": "COMMUNE", "mode_gestion": "Délégation",
                                          "operateur": "procédure en cours",
                                          "statut_operateur": "Entreprise privée"})
        base.commit()
        etape_environnement(base, tmp_path)
        services = {s["code_service"]: s for s in
                    json.loads((tmp_path / "environnement.json").read_text())["sispea_services"]}
        assert services["1"]["gestionnaire"] == {"role": "delegataire", "nom": "SAUR",
                                                 "fin": "2031-03-31", "annee": "2025"}
        assert services["2"]["gestionnaire"] is None
        texte = (tmp_path / "environnement.json").read_text()
        assert "71100" not in texte and "procédure" not in texte and "statut_operateur" not in texte
