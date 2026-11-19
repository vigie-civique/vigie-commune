"""Commune nouvelle : SIREN, anciens noms, codes postaux, anciens codes INSEE.

Relevé national de Vallouise-Pelvoux (05101), 01/11/2026. La commune est née
en 2017 de Vallouise (05175) et de Pelvoux, dont elle a gardé le code. Le
relevé avait :

  - le SIREN de Pelvoux, cessée, au lieu du sien (200064657) : OFGL, balances
    DGFiP et marchés de la commune vides ;
  - aucun monument historique : ceux de Vallouise sont sous 05175 ;
  - aucune association : le JO écrit « Vallouise » sous 05290 et « Pelvoux »
    sous 05340, et seul 05290 était interrogé ;
  - une lacune « 1 014 actes sur 1 014 mènent au compte rendu de la séance »
    sans une seule délibération.
"""
import importlib.util
import json
from pathlib import Path

from test_profondeur import charger_config, instance  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def _init_instance():
    spec = importlib.util.spec_from_file_location(
        "_init_instance", ROOT / "scripts" / "init_instance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Réponse réelle du répertoire des entreprises, réduite aux champs lus.
REPERTOIRE_05101 = {"results": [
    {"siren": "200064657", "nom_complet": "COMMUNE DE VALLOUISE-PELVOUX",
     "etat_administratif": "A", "siege": {"siret": "20006465700017", "commune": "05101"}},
    {"siren": "240500462", "nom_complet": "COMMUNAUTE DE COMMUNES DU PAYS DES ECRINS",
     "etat_administratif": "A"},
    {"siren": "210501755", "nom_complet": "COMMUNE DE VALLOUISE",
     "etat_administratif": "C"},
    {"siren": "210501011", "nom_complet": "COMMUNE DE PELVOUX",
     "etat_administratif": "C"},
]}


def test_une_commune_cessee_n_est_jamais_retenue(monkeypatch):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: REPERTOIRE_05101)
    siren, siret, anciens, sirens_anciens, absorbees = ii._sirens(
        "05101", "Vallouise-Pelvoux", ["05175"])
    assert siren == "200064657"
    assert siret == "20006465700017"
    assert anciens == {"05101": "Pelvoux", "05175": "Vallouise"}
    # Pelvoux a le même code : même collectivité continuée. Vallouise non :
    # absorbée, relevée à part sous son SIREN.
    assert sirens_anciens == ["210501011"]
    assert absorbees == {"05175": "210501755"}


def test_le_siren_attendu_reste_prioritaire(monkeypatch):
    """Deux « COMMUNE DE X » actives : la règle de calcul tranche toujours."""
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: {"results": [
        {"siren": "200099999", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
        {"siren": "212602890", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
    ]})
    assert ii._sirens("26289", "Saillans")[0] == "212602890"


def test_seules_des_cessees_ne_donnent_pas_de_siren(monkeypatch):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: {"results": [
        {"siren": "210501011", "nom_complet": "COMMUNE DE PELVOUX", "etat_administratif": "C"},
    ]})
    assert ii._sirens("05101", "Vallouise-Pelvoux")[0] == ""


def test_nom_lu_au_repertoire():
    ii = _init_instance()
    assert ii._nom_commune("COMMUNE DE SAINT-MARTIN-DE-LA-PLAINE") == "Saint-Martin-de-la-Plaine"
    assert ii._nom_commune("COMMUNE DE L'ARGENTIERE-LA-BESSEE") == "L'Argentiere-la-Bessee"
    assert ii._nom_commune("COMMUNE DE VALLOUISE") == "Vallouise"


def test_tous_les_codes_postaux_sont_interroges(instance):  # noqa: F811
    data = json.loads((Path(__file__).parent / "instance_test.json").read_text(encoding="utf-8"))
    communes = data["communes"]
    communes["99001"] = {**communes["99001"], "codes_postaux": ["99000", "99020"]}
    cfg = charger_config(instance(communes=communes))
    assert cfg.cp_du_step("rna") == ["99000", "99020"]
    assert cfg.COMMUNES["99001"]["cp"] == "99000"   # le premier, pour le reste


def test_lacune_sans_deliberation():
    from scripts.snapshot.couverture import lacune_documents
    assert "Aucune délibération" in lacune_documents(0, 1014, 0)
    assert lacune_documents(0, 1014, 12).startswith("Pour 1\u202f014 actes sur 1\u202f014")
    # Un stats.json sans le compteur garde l'ancienne lecture.
    assert lacune_documents(3, 10).startswith("Pour 7 actes sur 10")


def test_un_monument_sous_l_ancien_code_va_a_la_commune_actuelle(monkeypatch):
    from collectors import pop_culture
    monkeypatch.setattr(pop_culture, "COMMUNES", {"99002": {"nom": "Voisinbourg"}})
    monkeypatch.setattr(pop_culture, "COMMUNES_ADRESSE", {
        "99002": {"nom": "Voisinbourg"},
        "99004": {"nom": "Ancienville", "commune": "99002"}})
    assert pop_culture._commune_de("99004") == "Voisinbourg"
    assert pop_culture._commune_de("99002") == "Voisinbourg"


# ── Plusieurs SIREN pour une même commune (Saillans, 01/01/2025) ─────────────

def test_saillans_garde_son_ancien_siren(monkeypatch):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: {"results": [
        {"siren": "938438454", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
        {"siren": "212602890", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "C"},
    ]})
    siren, _, _, anciens, _ = ii._sirens("26289", "Saillans", ["26371"])
    assert (siren, anciens) == ("938438454", ["212602890"])


def test_un_exercice_se_lit_sous_un_seul_siren(instance):  # noqa: F811
    cfg = charger_config(instance(commune_siren="938438454",
                                  commune_sirens_anciens=["212602890", "938438454"]))
    assert cfg.COMMUNE_SIRENS == ["938438454", "212602890"]
    lus = []

    def lire(siren):
        lus.append(siren)
        return {"938438454": [], "212602890": [{"exer": 2024}]}[siren]
    assert cfg.premier_releve(lire) == [{"exer": 2024}]
    assert lus == ["938438454", "212602890"]
    # Un None (non publié, lecture incomplète) ne passe pas à l'ancien SIREN.
    assert cfg.premier_releve(lambda s: None) is None


def test_un_marche_de_l_ancien_siren_est_de_la_commune(monkeypatch):
    from collectors import marches_extraits
    monkeypatch.setattr(marches_extraits, "COMMUNE_SIRENS", ["938438454", "212602890"])
    assert marches_extraits.portee_de("21260289000017", "") == "commune"
    assert marches_extraits.portee_de("938438454", "") == "commune"


# ── Revue sécurité de la PR #195 ─────────────────────────────────────────────

def test_une_seule_active_mais_d_une_autre_commune(monkeypatch):
    """Une autre collectivité, présente par un établissement, n'est pas retenue."""
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: {"results": [
        {"siren": "200011111", "nom_complet": "COMMUNE DE VOISINBOURG",
         "etat_administratif": "A", "siege": {"commune": "99002"}},
        {"siren": "219900019", "nom_complet": "COMMUNE DE TESTONVILLE",
         "etat_administratif": "C", "siege": {"commune": "99001"}},
    ]})
    assert ii._sirens("99001", "Testonville")[0] == ""


def test_un_siren_ancien_mal_saisi_arrete_le_chargement(instance):  # noqa: F811
    import pytest
    with pytest.raises(SystemExit, match="doit être une liste"):
        charger_config(instance(commune_sirens_anciens="212602890"))
    with pytest.raises(SystemExit, match="SIREN invalide"):
        charger_config(instance(commune_sirens_anciens=["2126"]))
    with pytest.raises(SystemExit, match="SIREN invalide"):
        charger_config(instance(commune_siren="21260289"))


def test_une_erreur_de_lecture_ne_passe_pas_a_l_ancien_siren(monkeypatch):
    from collectors import config, ofgl
    monkeypatch.setattr(config, "COMMUNE_SIRENS", ["938438454", "212602890"])
    lus = []

    def lecture(year, siren):
        lus.append(siren)
        return None if siren == "938438454" else [{"agregat": "x"}]
    monkeypatch.setattr(ofgl, "_fetch_year", lecture)
    assert ofgl.fetch_year(2025) == []
    assert lus == ["938438454"]


# ── Avant la fusion (02/11/2026) ─────────────────────────────────────────────
# Un exercice lu sous l'ancien SIREN sortait sous le nom actuel ; une commune
# absorbée d'un autre code (Véronne dans Saillans) n'était pas relevée. Tranché :
# toutes les absorbées, chacune à part, jamais additionnées à la commune.

def test_le_releve_dit_sous_quel_siren_il_a_ete_lu(instance):  # noqa: F811
    cfg = charger_config(instance(commune_siren="938438454",
                                  commune_sirens_anciens=["212602890"]))
    assert cfg.releve_et_siren(
        lambda s: {"938438454": [], "212602890": [{"exer": 2024}]}[s]
    ) == ("212602890", [{"exer": 2024}])
    assert cfg.releve_et_siren(lambda s: []) == (None, [])
    assert cfg.releve_et_siren(lambda s: None) == (None, None)


def _avec_veronne(instance, **veronne):  # noqa: F811
    return charger_config(instance(
        commune_siren="938438454", commune_sirens_anciens=["212602890"],
        anciennes_communes={
            "99371": {"nom": "Véronne", "cp": "99000", "population": 0,
                      "commune": "99001", **veronne},
            # Une ancienne commune d'une AUTRE commune du périmètre : jamais
            # une absorbée de la commune de collecte, SIREN ou pas.
            "99372": {"nom": "Ailleurs", "cp": "99000", "population": 0,
                      "commune": "99002", "siren": "219903723"},
        }))


def test_une_absorbee_se_declare_par_son_siren(instance):  # noqa: F811
    cfg = _avec_veronne(instance, siren="212603716")
    assert cfg.COMMUNES_ABSORBEES == {"212603716": {"nom": "Véronne", "insee": "99371"}}
    assert cfg.SIRENS_AVANT_FUSION == {"212602890": "Testonville", "212603716": "Véronne"}
    # La série de la commune ne la lit jamais : elle n'est pas un de ses SIREN.
    assert "212603716" not in cfg.COMMUNE_SIRENS
    # Sans `siren`, l'entrée reste un code d'adresse (Brassac, Lasalle).
    assert _avec_veronne(instance).COMMUNES_ABSORBEES == {}


def test_un_siren_d_absorbee_mal_saisi_arrete_le_chargement(instance):  # noqa: F811
    import pytest
    with pytest.raises(SystemExit, match="SIREN invalide"):
        _avec_veronne(instance, siren="2126037")


def test_une_ligne_d_avant_la_fusion_est_marquee():
    from scripts.snapshot.argent import marquer_avant_fusion
    lignes = [{"year": 2025, "siren": "938438454"}, {"year": 2024, "siren": "212602890"},
              {"year": 2018, "siren": None}]
    assert marquer_avant_fusion(lignes, {"212602890": "Saillans"}) == 1
    assert lignes == [{"year": 2025}, {"year": 2024, "avant_fusion": "Saillans",
                                       "siren": "212602890"}, {"year": 2018}]


def test_une_absorbee_va_dans_sa_table_jamais_dans_celle_de_la_commune(monkeypatch):
    """Base réelle (SQLite en mémoire), lectures OFGL simulées."""
    import sqlite3
    from collectors import config, ofgl
    monkeypatch.setattr(config, "COMMUNE_SIRENS", ["938438454", "212602890"])
    monkeypatch.setattr(ofgl, "COMMUNES_ABSORBEES", {"212603716": {"nom": "Véronne"}})
    lus = {"938438454": [], "212602890": [{"type_de_budget": ofgl.TYPE_PRINCIPAL,
                                           "agregat": "Encours de dette", "montant": 100.0}],
           "212603716": [{"type_de_budget": ofgl.TYPE_PRINCIPAL,
                          "agregat": "Encours de dette", "montant": 7.0}]}
    monkeypatch.setattr(ofgl, "_fetch_year", lambda year, siren: lus[siren])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ofgl.ensure_table(conn)
    ofgl.import_year(conn, 2024)
    ofgl.import_absorbees(conn, 2024)
    assert [tuple(r) for r in conn.execute(
        "SELECT year, agregat, montant, siren FROM ofgl_agregats")] == [
        (2024, "Encours de dette", 100.0, "212602890")]
    assert [tuple(r) for r in conn.execute(
        "SELECT siren, year, montant FROM ofgl_agregats_absorbees")] == [
        ("212603716", 2024, 7.0)]


def test_un_marche_d_une_absorbee_est_releve(monkeypatch):
    from collectors import marches_publics as marches
    monkeypatch.setattr(marches, "COMMUNE_SIRENS", ["938438454", "212602890"])
    monkeypatch.setattr(marches, "COMMUNES_ABSORBEES", {"212603716": {"nom": "Véronne"}})
    assert marches.dans_le_perimetre("21260371600016", "")
    assert not marches.dans_le_perimetre("21260371700015", "")


def test_un_marche_d_avant_la_fusion_reste_marque_apres_le_rattachement():
    """Le marquage lisait `acheteur_siren` APRÈS `rattacher_acheteurs`, qui le
    retire : aucun marché n'était marqué (Saillans en ligne, 02/11/2026). On
    joue les deux dans l'ordre de `etape_finances`."""
    from scripts.snapshot.argent import marquer_marches_avant_fusion, rattacher_acheteurs
    marches = [{"acheteur_id": None, "acheteur_siren": "21260289000017"},
               {"acheteur_id": None, "acheteur_siren": "21260371600016"},
               {"acheteur_id": None, "acheteur_siren": "93843845400010"}]
    avant = {"212602890": "Saillans", "212603716": "Véronne"}
    assert marquer_marches_avant_fusion(marches, avant) == 2
    rattacher_acheteurs(marches, {"212602890": 1, "938438454": 1})
    assert [m.get("avant_fusion") for m in marches] == ["Saillans", "Véronne", None]
    # Le SIREN ne sort toujours pas ; l'ancienne commune garde sa fiche.
    assert all("acheteur_siren" not in m for m in marches)
    assert marches[0]["acheteur_id"] == 1 and marches[1]["acheteur_id"] is None


# ── Le SIREN de geo.api fait foi avant le calcul (chantier du 02/11/2026) ────

def _repertoire(par_requete):
    """`_get` factice : la réponse dépend du `q` de la requête."""
    import urllib.parse

    def get(url, **k):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
        return par_requete.get(q, {"results": []})
    return get


def test_le_siren_de_geo_api_bat_le_calcul(monkeypatch, capsys):
    """Saillans : le calcul vise 212602890 ; si les deux étaient actifs au
    répertoire, c'est celui de geo.api (938438454) qui doit être retenu."""
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", _repertoire({"commune de Saillans": {"results": [
        {"siren": "212602890", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
        {"siren": "938438454", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
    ]}}))
    assert ii._sirens("26289", "Saillans", siren_geo="938438454")[0] == "938438454"
    assert "⚠" not in capsys.readouterr().out


def test_le_siren_de_geo_api_se_cherche_par_lui_meme(monkeypatch):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", _repertoire({
        "commune de Vallouise-Pelvoux": {"results": REPERTOIRE_05101["results"][2:]},
        "200064657": {"results": REPERTOIRE_05101["results"][:1]},
    }))
    siren, siret, *_ = ii._sirens("05101", "Vallouise-Pelvoux", ["05175"], "200064657")
    assert (siren, siret) == ("200064657", "20006465700017")


def test_un_siren_de_geo_api_cesse_est_signale_et_ecarte(monkeypatch, capsys):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", _repertoire({
        "commune de Saillans": {"results": [
            {"siren": "212602890", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"}]},
        "938438454": {"results": [
            {"siren": "938438454", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "C"}]},
    }))
    assert ii._sirens("26289", "Saillans", siren_geo="938438454")[0] == "212602890"
    sortie = capsys.readouterr().out
    assert "cessé au répertoire" in sortie and "à vérifier à la main" in sortie


def test_sans_siren_de_geo_api_rien_ne_change(monkeypatch, capsys):
    """geo.api passe par des 504 : un échec de lecture n'est pas un écart."""
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", lambda url, **k: REPERTOIRE_05101)
    assert ii._sirens("05101", "Vallouise-Pelvoux", ["05175"], "")[0] == "200064657"
    assert "geo.api" not in capsys.readouterr().out


# ── Une absorbée cherchée sous son propre nom (point 5, 02/11/2026) ──────────

COG_26289 = [{"code": "26289", "intitule": "Saillans"},
             {"code": "26371", "intitule": "Véronne"}]


def _get_veronne(cessees_veronne):
    import urllib.parse

    def get(url, **k):
        if "/precedents" in url:
            return COG_26289
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
        if q == "commune de Saillans":
            return {"results": [
                {"siren": "938438454", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "A"},
                {"siren": "212602890", "nom_complet": "COMMUNE DE SAILLANS", "etat_administratif": "C"}]}
        if q == "commune de Véronne":
            return {"results": cessees_veronne}
        return {"results": []}
    return get


def test_veronne_se_trouve_sous_son_ancien_nom(monkeypatch):
    ii = _init_instance()
    monkeypatch.setattr(ii, "_get", _get_veronne([
        {"siren": "212603716", "nom_complet": "COMMUNE DE VERONNE", "etat_administratif": "C"},
        # Homonyme d'un autre département : préfixe différent, écarté.
        {"siren": "212106678", "nom_complet": "COMMUNE DE VERONNES", "etat_administratif": "C"},
    ]))
    _, _, noms, anciens, absorbees = ii._sirens("26289", "Saillans", ["26371"], "938438454")
    assert absorbees == {"26371": "212603716"}
    assert noms["26371"] == "Véronne"
    assert anciens == ["212602890"]


def test_une_absorbee_active_ou_ambigue_n_est_jamais_retenue(monkeypatch):
    ii = _init_instance()
    active = [{"siren": "212603716", "nom_complet": "COMMUNE DE VERONNE", "etat_administratif": "A"}]
    monkeypatch.setattr(ii, "_get", _get_veronne(active))
    assert ii._sirens("26289", "Saillans", ["26371"], "938438454")[4] == {}
    deux = [{"siren": "212603716", "nom_complet": "COMMUNE DE VERONNE", "etat_administratif": "C"},
            {"siren": "212603717", "nom_complet": "COMMUNE DE VERONNE", "etat_administratif": "C"}]
    monkeypatch.setattr(ii, "_get", _get_veronne(deux))
    assert ii._sirens("26289", "Saillans", ["26371"], "938438454")[4] == {}


def test_sans_cog_rien_n_est_devine(monkeypatch):
    ii = _init_instance()
    get = _get_veronne([{"siren": "212603716", "nom_complet": "COMMUNE DE VERONNE",
                         "etat_administratif": "C"}])
    monkeypatch.setattr(ii, "_get", lambda url, **k: None if "/precedents" in url else get(url))
    assert ii._sirens("26289", "Saillans", ["26371"], "938438454")[4] == {}


def test_communes_de_l_epci_illisibles_arretent_l_amorcage(monkeypatch):
    """Magasin du 16/11/2026 : geo.api muet sur l'EPCI, configuration à 0
    commune écrite en code 0, puis seize steps refusés par config.py."""
    import pytest

    ii = _init_instance()
    commune = {"nom": "Testonville", "code": "99001", "codesPostaux": ["99000"],
               "population": 900, "epci": {"code": "209900019", "nom": "CC d'épreuve"}}

    def lecture(url, **k):
        if "/epcis/" in url:
            return lecture.epci
        if "/departements/" in url:
            return {"nom": "Épreuve"}
        return commune

    monkeypatch.setattr(ii, "_get", lecture)
    for reponse in (None, [], [{"nom": "Voisinbourg", "code": "99002", "codesPostaux": ["99000"]}]):
        lecture.epci = reponse
        with pytest.raises(SystemExit, match="configuration non écrite"):
            ii.construire("99001")
