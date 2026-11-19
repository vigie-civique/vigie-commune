"""Les six collecteurs data.gouv du 29/10/2026 : ENAF, VigiEau, cantines, ODRÉ,
loyers, RPLS.

Aucun appel réseau. Les extraits sont FICTIFS (communes 99xxx, noms inventés)
et reprennent le format exact des fichiers lus le 29/10/2026. La base est celle
de `scripts/donnees_synthetiques.py`.

Ce que ces essais protègent :
  1. chaque format est lu tel qu'il est publié (motifs de colonnes, virgule
     décimale, JSON en flux coupé n'importe où) ;
  2. aucune donnée qui pourrait désigner une personne n'entre en base : nom
     d'une cantine privée, nom d'une installation électrique, adresse d'un
     logement social ;
  3. chaque chiffre porte sa source (URL) et son millésime ;
  4. une commune absente d'un jeu devient une lacune de `/couverture`, sauf
     quand l'absence est un fait (aucune installation, aucun logement social) ;
  5. un step lancé seul (`--step`) laisse `perimetre` NULL à 0.
"""
from __future__ import annotations

import io
import re
import json
import sqlite3
import urllib.parse
import zipfile

import pytest

from collectors import absences, cantines, enaf, loyers, odre, rpls, vigieau
from collectors import db as vigie_db
from collectors.config import PROFONDEUR_STEP, STEP_META
from scripts.donnees_synthetiques import generer
from scripts.snapshot.couverture import sources_absentes

ICI = "99001"          # la commune de fond de tests/instance_test.json
AILLEURS = "99002"

# ── Extraits fictifs ─────────────────────────────────────────────────────────

ENAF_CSV = (
    "idcom,idcomtxt,naf11art12,art11hab12,art11act12,art11rou12,"
    "naf12art13,art12hab13,art12act13,art12rou13,naf11art13,art11hab13\n"
    f"{ICI},Testonville,1500.0,1000.0,500.0,0.0,300.0,0.0,0.0,300.0,1800.0,1000.0\n"
    f"{AILLEURS},Voisinbourg,10.0,10.0,0.0,0.0,0.0,0.0,0.0,0.0,10.0,10.0\n"
)


def _vigieau_json(annee: int) -> bytes:
    def jours(niveaux):
        return [{"AEP": n, "SOU": n, "SUP": None, "date": f"{annee}-07-{i + 1:02d}"}
                for i, n in enumerate(niveaux)]
    return json.dumps([
        {"commune": {"code": AILLEURS, "nom": "Voisinbourg"},
         "restrictions": jours([None, None])},
        {"commune": {"code": ICI, "nom": "Testonville"},
         "restrictions": jours([None, "vigilance", "alerte", "crise", "crise"])},
    ], separators=(",", ":")).encode()


REGISTRE_CSV = (
    "id,name,siret,city_insee_code,economic_model,management_type,production_type,"
    "central_producer_siret,daily_meal_count,yearly_meal_count,sector_list,"
    "declaration_donnees_2023,declaration_donnees_2024,modification_date\n"
    f"1,École de Testonville,21990001700029,{ICI},public,direct,site,,80,11000,"
    "Ecole primaire (maternelle et élémentaire),False,True,2026-03-01T10:00:00+01:00\n"
    f"2,Aldéric Valmorin EI,99900000100011,{ICI},private,direct,site,99900000200011,12,"
    "2600,Crèche,False,False,2026-02-01T10:00:00+01:00\n"
)
TD_CSV = (
    "id,applicant_id,teledeclaration_mode,year,canteen_id,canteen_siret,canteen_name,"
    "canteen_city_insee_code,canteen_economic_model,canteen_satellite_canteens_count,"
    "canteen_sector_list,teledeclaration_ratio_bio,teledeclaration_ratio_egalim_hors_bio\n"
    f"10,4242,SITE,2024,1,21990001700029,École de Testonville,{ICI},public,,"
    "Ecole primaire,0.18,0.25\n"
    f"11,4243,SITE,2024,2,99900000100011,Aldéric Valmorin EI,{ICI},private,,Crèche,0.4,0.1\n"
)

LOYERS_ENTETE = ('"id_zone";"INSEE_C";"LIBGEO";"EPCI";"DEP";"REG";"loypredm2";"lwr.IPm2";'
                 '"upr.IPm2";"TYPPRED";"nbobs_com";"nbobs_mail";"R2_adj"')


def _loyers_csv(loyer: str) -> bytes:
    return "\r\n".join([
        LOYERS_ENTETE,
        f'"1";"{ICI}";"Testonville";"209900019";"99";"99";{loyer};7,1;10,5;"maille";16;1369;0,69',
    ]).encode("latin-1")


RPLS_CSV = (
    '"DEPCOM";"NBPIECE";"SURFHAB";"CONSTRUCT";"FINAN_CODE";"FINAN_LIBELLE";"DPEENERGIE"\n'
    f'"{ICI}";2;45;"1975";"52";"HLM/O";"D"\n'
    f'"{ICI}";4;72;"1975";"52";"HLM/O";"E"\n'
    f'"{ICI}";6;110;"2012";"13";"PLUS";""\n'
)


# ── Lecture ──────────────────────────────────────────────────────────────────

class TestEnaf:
    def test_seules_les_annees_simples_sont_lues(self):
        assert enaf._colonne("naf11art12") == ("total", 2011)
        assert enaf._colonne("art14hab15") == ("habitat", 2014)
        assert enaf._colonne("naf11art25") is None      # cumul
        assert enaf._colonne("art11hab25") is None
        assert enaf._colonne("naf11hab12") is None      # total mal formé
        assert enaf._colonne("artpop1622") is None

    def test_le_millesime_est_le_premier_janvier_qui_clot_la_serie(self):
        lignes, millesime = enaf.lire(ENAF_CSV, {ICI})
        assert millesime == 2013
        total = sum(l[3] for l in lignes if l[2] == "total")
        assert total == 1800.0                           # = le cumul naf11art13
        assert {l[0] for l in lignes} == {ICI}


class TestVigieau:
    @pytest.mark.parametrize("bloc", [7, 64, 1 << 20])
    def test_le_flux_se_coupe_n_importe_ou(self, bloc):
        objets = list(vigieau.objets(io.BytesIO(_vigieau_json(2025)), {ICI}, bloc))
        assert [o["commune"]["code"] for o in objets] == [ICI]

    def test_un_jour_sans_restriction_est_compte(self):
        objet = next(vigieau.objets(io.BytesIO(_vigieau_json(2025)), {ICI}))
        compte, dernier = vigieau.compter(objet)[2025]
        assert compte[("AEP", "crise")] == 2
        assert compte[("AEP", "aucune")] == 1
        assert sum(n for (z, _), n in compte.items() if z == "SUP") == 5
        assert dernier == "2025-07-05"


class TestCantines:
    def test_une_cantine_privee_n_est_pas_nommee(self):
        privee = next(c for c in cantines.lire_registre(REGISTRE_CSV, {ICI})
                      if c["modele"] == "private")
        assert privee["nom"] is None and privee["siret"] is None
        assert privee["cuisine_centrale_siret"] is None
        assert privee["repas_an"] == 2600

    def test_le_declarant_n_est_jamais_lu(self):
        lignes = cantines.lire_teledeclarations(TD_CSV, {ICI})
        assert all("4242" not in map(str, l.values()) for l in lignes)
        publique = next(l for l in lignes if l["modele"] == "public")
        assert (publique["part_bio"], publique["part_egalim_hors_bio"]) == (0.18, 0.25)


class TestOdre:
    def test_le_nom_n_est_ni_demande_ni_garde(self):
        assert "nominstallation" not in odre.CHAMPS
        params = urllib.parse.parse_qs(urllib.parse.urlparse(odre._url([ICI], False, 0)).query)
        assert "nominstallation" not in params["select"][0]

    def test_date_du_registre(self):
        assert odre.date_du_registre(
            "Registre national … (au 31/08/2026)") == "2026-08-31"


class TestLoyers:
    def test_le_type_de_bien_se_lit_dans_le_nom_du_fichier(self):
        assert loyers._type_de_bien("x/pred-app12-mef-dhup.csv") == "appartement_1_2_pieces"
        assert loyers._type_de_bien("x/pred-app3-mef-dhup.csv") == "appartement_3_pieces_et_plus"
        assert loyers._type_de_bien("x/pred-app-mef-dhup.csv") == "appartement"
        assert loyers._type_de_bien("x/pred-mai-mef-dhup.csv") == "maison"

    def test_virgule_decimale_et_echelle(self):
        (l,) = loyers.lire(_loyers_csv("8,7").decode("latin-1"), {ICI})
        assert l["loyer_m2"] == 8.7 and l["echelle"] == "maille"
        assert (l["annonces_commune"], l["annonces_maille"]) == (16, 1369)


class TestRpls:
    def test_aucune_colonne_ne_situe_un_logement(self):
        for colonne in ("NUMVOIE", "NOMVOIE", "ETAGE", "X", "Y", "LIEUDIT", "COMPLGEO"):
            assert colonne not in rpls.COLONNES

    def test_comptes(self):
        c = rpls.compter(RPLS_CSV, "DEPCOM")[ICI]
        assert c[("total", "")] == 3
        assert c[("pieces", "5 et plus")] == 1
        assert c[("construction", "1970")] == 2
        assert c[("dpe", "non renseigné")] == 1
        assert c[("surface", "90 m² et plus")] == 1


# ── Déclaration des steps ────────────────────────────────────────────────────

STEPS = ("enaf", "vigieau", "cantines", "odre", "loyers", "rpls")


@pytest.mark.parametrize("step", STEPS)
def test_le_step_est_declare(step):
    assert step in STEP_META and PROFONDEUR_STEP[step] == "fond"


# ── Absences ─────────────────────────────────────────────────────────────────

def test_une_absence_se_remplace_a_chaque_passe(base):
    absences.noter(base, "x", {ICI, AILLEURS}, {ICI}, jeu="Jeu", page_jeu="p",
                   source_url="u", millesime=2025)
    absences.noter(base, "x", {ICI, AILLEURS}, {ICI, AILLEURS}, jeu="Jeu", page_jeu="p",
                   source_url="u", millesime=2026)
    assert base.execute("SELECT COUNT(*) FROM sources_absentes").fetchone()[0] == 0


def test_une_absence_devient_une_lacune_de_couverture(base):
    absences.noter(base, "loyers", {ICI, AILLEURS}, {ICI}, jeu="Carte des loyers",
                   page_jeu="https://www.data.gouv.fr/datasets/x", source_url="u",
                   millesime=2025)
    (lacune,) = sources_absentes(base)
    assert lacune["etat"] == "absent" and lacune["insee"] == AILLEURS
    assert "2025" in lacune["detail"] and lacune["page_jeu"]


# ── Passe complète sur la base synthétique ───────────────────────────────────

@pytest.fixture
def synthetique(tmp_path, monkeypatch):
    chemin = tmp_path / "synthetique.db"
    generer(chemin, graine=1, taille=20)
    monkeypatch.setattr(vigie_db, "DB_PATH", chemin)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    assert _perimetre_nul(conn) == 0
    yield conn
    conn.close()


def _perimetre_nul(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM entities WHERE perimetre IS NULL").fetchone()[0]


def _fiche(*ressources):
    return {"resources": [{"format": f, "title": t, "url": u,
                           "last_modified": "2026-10-29T01:01:08"} for f, t, u in ressources]}


def _faux_deposer(contenus: dict[str, bytes]):
    def deposer(url, fichier, source, jours, facteur_perime=2):
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(contenus[url])
        return fichier
    return deposer


def _brancher(module, monkeypatch, tmp_path, fiches: dict, contenus: dict):
    monkeypatch.setattr(module, "fetch_json",
                        lambda url, **_: fiches[next(k for k in fiches if k in url)])
    if hasattr(module, "deposer"):
        monkeypatch.setattr(module, "deposer", _faux_deposer(contenus))
    if hasattr(module, "CACHE"):
        monkeypatch.setattr(module, "CACHE", tmp_path / "magasin")
    if hasattr(module, "FICHIER"):
        monkeypatch.setattr(module, "FICHIER", tmp_path / "magasin" / module.FICHIER.name)


def _vigieau_zip() -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("restrictions_communes_2025.json", _vigieau_json(2025))
    return tampon.getvalue()


def _preparer(step, monkeypatch, tmp_path):
    if step == "enaf":
        u = "https://static.exemple.invalid/conso-com.csv"
        _brancher(enaf, monkeypatch, tmp_path,
                  {"datasets": _fiche(("csv", "conso_com.csv", u))}, {u: ENAF_CSV.encode()})
        return enaf.run, "enaf_consommation"
    if step == "vigieau":
        u = "https://static.exemple.invalid/restrictions-communes-2025.zip"
        _brancher(vigieau, monkeypatch, tmp_path,
                  {"datasets": _fiche(("zip", "Communes en restrictions - 2025", u))},
                  {u: _vigieau_zip()})
        monkeypatch.setattr(vigieau, "MIN_COMMUNES", 2)
        return vigieau.run, "vigieau_jours"
    if step == "cantines":
        ur, ut = "https://cellar.exemple.invalid/registre.csv", "https://cellar.exemple.invalid/td.csv"
        _brancher(cantines, monkeypatch, tmp_path, {
            cantines.DATASET_REGISTRE: _fiche(("csv", "Registre National des Cantines", ur)),
            cantines.DATASET_TD: _fiche(("csv", "Résultats … (données 2024)", ut)),
        }, {ur: REGISTRE_CSV.encode(), ut: TD_CSV.encode()})
        return cantines.run, "cantines_registre"
    if step == "odre":
        def fetch_json(url, **_):
            if "/records?" not in url:
                return {"metas": {"default": {"title": "Registre (au 31/08/2026)"}}}
            agregat = "not+startswith" not in url
            ligne = {"codeinseecommune": ICI, "filiere": "Solaire",
                     "puismaxinstallee": 120.0 if agregat else 90.0,
                     "nbinstallations": 15 if agregat else 1}
            return {"results": [ligne], "total_count": 1}
        monkeypatch.setattr(odre, "fetch_json", fetch_json)
        return odre.run, "odre_installations"
    if step == "loyers":
        noms = ("pred-app-mef-dhup.csv", "pred-app12-mef-dhup.csv",
                "pred-app3-mef-dhup.csv", "pred-mai-mef-dhup.csv")
        urls = [f"https://static.exemple.invalid/{n}" for n in noms]
        _brancher(loyers, monkeypatch, tmp_path,
                  {"datasets": _fiche(*(("csv", n, u) for n, u in zip(noms, urls)))},
                  {u: _loyers_csv("9,1") for u in urls})
        return loyers.run, "loyers_annonces"
    if step == "rpls":
        monkeypatch.setattr(rpls, "fetch_json", lambda url, **_: {
            "millesime": "2025-01",
            "columns": [{"name": n} for n in ("DEPCOM", *rpls.COLONNES)]})

        class Reponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        monkeypatch.setattr(rpls.urllib.request, "urlopen",
                            lambda req, timeout=None: Reponse(RPLS_CSV.encode()))
        return rpls.run, "rpls_communes"
    raise AssertionError(step)


@pytest.mark.parametrize("step", STEPS)
def test_un_step_seul_laisse_le_perimetre_classe(step, synthetique, monkeypatch, tmp_path):
    run, table = _preparer(step, monkeypatch, tmp_path)
    assert run() > 0
    assert _perimetre_nul(synthetique) == 0
    sans_source = synthetique.execute(
        f"SELECT COUNT(*) FROM {table} WHERE source_url IS NULL OR source_url = ''").fetchone()[0]
    assert sans_source == 0


def test_aucun_nom_de_personne_n_entre(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("cantines", monkeypatch, tmp_path)
    run()
    for table in ("cantines_registre", "cantines_teledeclarations"):
        for ligne in synthetique.execute(f"SELECT * FROM {table}"):
            assert not any("Valmorin" in str(v) for v in tuple(ligne)), table


def test_une_commune_absente_du_jeu_est_notee(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("enaf", monkeypatch, tmp_path)
    monkeypatch.setattr(enaf, "communes_du_step", lambda step: [ICI, "99003"])
    run()
    absentes = synthetique.execute(
        "SELECT step, insee, millesime FROM sources_absentes").fetchall()
    assert [tuple(r) for r in absentes] == [("enaf", "99003", "2013")]


def test_sans_logement_social_le_total_est_zero(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("rpls", monkeypatch, tmp_path)
    monkeypatch.setattr(rpls, "communes_du_step", lambda step: [ICI, "99003"])
    run()
    total = synthetique.execute(
        "SELECT logements FROM rpls_communes WHERE insee='99003' AND dimension='total'"
    ).fetchone()[0]
    assert total == 0
    assert not synthetique.execute(
        "SELECT name FROM sqlite_master WHERE name='sources_absentes'").fetchone()



def test_aucune_commune_au_repertoire_n_est_pas_une_panne(synthetique, monkeypatch, tmp_path):
    """DiDo répond 400 « Le fichier est vide. » quand aucune commune demandée n'a
    de logement social : le dossier 27222 + 27334 tombait en échec (31/10)."""
    run, _ = _preparer("rpls", monkeypatch, tmp_path)
    monkeypatch.setattr(rpls, "communes_du_step", lambda step: ["99003"])

    def vide(req, timeout=None):
        raise rpls.urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO('{"code": 400, "message": "Le fichier est vide."}'.encode()))
    monkeypatch.setattr(rpls.urllib.request, "urlopen", vide)
    assert run() == 0
    total = synthetique.execute(
        "SELECT logements FROM rpls_communes WHERE insee='99003' AND dimension='total'"
    ).fetchone()[0]
    assert total == 0


def test_un_autre_400_reste_une_panne(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("rpls", monkeypatch, tmp_path)

    def refus(req, timeout=None):
        raise rpls.urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"message": "millesime inconnu"}'))
    monkeypatch.setattr(rpls.urllib.request, "urlopen", refus)
    with pytest.raises(rpls.urllib.error.HTTPError):
        run()

# ── Corrections de l'avis DPO du 29/10/2026 ──────────────────────────────────

def test_une_absence_porte_le_millesime_du_fichier_meme_sans_cantine(
        synthetique, monkeypatch, tmp_path):
    """Le millésime venait des lignes trouvées : vide quand la commune n'a aucune
    cantine, et `/couverture` écrivait « millésime ». Il vient du fichier."""
    run, _ = _preparer("cantines", monkeypatch, tmp_path)
    monkeypatch.setattr(cantines, "communes_du_step", lambda step: ["99003"])
    run()
    (millesime,) = synthetique.execute(
        "SELECT millesime FROM sources_absentes WHERE step='cantines'").fetchone()
    assert millesime == "2026-10-29"


@pytest.mark.parametrize("lire, texte", [
    (lambda t: cantines.lire_registre(t, {ICI}),
     REGISTRE_CSV.replace("city_insee_code", "code_commune")),
    (lambda t: cantines.lire_teledeclarations(t, {ICI}),
     TD_CSV.replace("canteen_economic_model", "modele")),
    (lambda t: loyers.lire(t, {ICI}),
     _loyers_csv("8,7").decode("latin-1").replace('"INSEE_C"', '"CODGEO"')),
])
def test_un_format_change_leve_au_lieu_de_tout_dire_absent(lire, texte):
    with pytest.raises(RuntimeError, match="colonne"):
        lire(texte)


@pytest.mark.parametrize("step", ["cantines", "loyers", "vigieau"])
def test_une_lecture_ratee_n_ecrit_aucune_absence(step, synthetique, monkeypatch, tmp_path):
    run, table = _preparer(step, monkeypatch, tmp_path)
    if step == "cantines":
        monkeypatch.setattr(cantines, "deposer", _faux_deposer(
            {u: b"id,nom\n1,x\n" for u in ("https://cellar.exemple.invalid/registre.csv",
                                            "https://cellar.exemple.invalid/td.csv")}))
    elif step == "loyers":
        monkeypatch.setattr(loyers, "deposer", lambda url, fichier, *a: (
            fichier.parent.mkdir(parents=True, exist_ok=True),
            fichier.write_bytes(b'"CODGEO";"x"\r\n'), fichier)[-1])
    else:
        monkeypatch.setattr(vigieau, "MIN_COMMUNES", 3)     # le fichier n'en a que 2
    with pytest.raises(RuntimeError):
        run()
    for nom in ("sources_absentes", table):
        if synthetique.execute("SELECT 1 FROM sqlite_master WHERE name=?", (nom,)).fetchone():
            assert synthetique.execute(f"SELECT COUNT(*) FROM {nom}").fetchone()[0] == 0, nom


def test_le_flux_compte_toutes_les_communes_vues():
    vus = [0]
    list(vigieau.objets(io.BytesIO(_vigieau_json(2025)), {ICI}, 7, vus=vus))
    assert vus == [2]


def test_la_mention_imposee_des_loyers_est_exacte():
    assert loyers.MENTION == ("Estimations ANIL, à partir des données du Groupe SeLoger "
                              "et de leboncoin")


# ── Exports vers les pages : les exigences de l'avis DPO du 29/10/2026 ───────

from scripts.snapshot import sources_data_gouv as exports  # noqa: E402


def _cles(objet) -> set[str]:
    """Toutes les clés d'un export, à toute profondeur."""
    if isinstance(objet, dict):
        return set(objet) | {k for v in objet.values() for k in _cles(v)}
    if isinstance(objet, list):
        return {k for v in objet for k in _cles(v)}
    return set()


def test_l_electricite_sort_par_filiere_sans_code_eic(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("odre", monkeypatch, tmp_path)
    run()
    synthetique.execute("UPDATE odre_installations SET code_eic='17W000000000000X'"
                        " WHERE agregat_moins_36kw=0")
    synthetique.commit()
    sortie = exports.export_odre(synthetique, ICI)
    assert "code_eic" not in _cles(sortie)
    assert "17W" not in json.dumps(sortie)
    (solaire,) = sortie["filieres"]
    assert (solaire["installations"], solaire["puissance_kw"]) == (16, 210.0)
    assert solaire["energie_masquee"] is False


def test_une_commune_sans_installation_a_une_liste_vide_datee(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("odre", monkeypatch, tmp_path)
    run()
    sortie = exports.export_odre(synthetique, "99003")
    assert sortie["filieres"] == [] and sortie["date_registre"] == "2026-08-31"


def test_les_cantines_privees_ne_sortent_qu_en_compte(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("cantines", monkeypatch, tmp_path)
    run()
    sortie = exports.export_cantines(synthetique, ICI)
    texte = json.dumps(sortie, ensure_ascii=False)
    assert "Valmorin" not in texte
    assert not {"siret", "id", "cantine_id", "cuisine_centrale_siret"} & _cles(sortie)
    assert not re.search(r"\b\d{14}\b", texte)                 # aucun SIRET
    assert sortie["privees"] == {"nombre": 1}
    (ecole,) = sortie["cantines"]
    assert ecole["declarations"][0]["part_durable"] == 0.43     # 0,18 bio + 0,25


def test_sous_trois_logements_sociaux_le_total_seul(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("rpls", monkeypatch, tmp_path)
    run()
    assert exports.export_rpls(synthetique, ICI)["repartitions"]       # 3 logements
    synthetique.execute("DELETE FROM rpls_communes WHERE dimension != 'total'"
                        " OR insee != ?", (ICI,))
    synthetique.execute("UPDATE rpls_communes SET logements=2")
    synthetique.execute("INSERT INTO rpls_communes (insee, millesime, dimension, modalite,"
                        " logements) VALUES (?, '2025-01', 'pieces', '6', 1)", (ICI,))
    synthetique.commit()
    sortie = exports.export_rpls(synthetique, ICI)
    assert sortie["total"] == 2 and sortie["repartitions"] == {}
    assert sortie["repartitions_masquees"] is True


def test_la_mention_des_loyers_voyage_avec_le_chiffre(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("loyers", monkeypatch, tmp_path)
    run()
    sortie = exports.export_loyers(synthetique, ICI)
    assert sortie["mention"] == loyers.MENTION and len(sortie["biens"]) == 4


def test_enaf_separe_les_deux_periodes_du_zan(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("enaf", monkeypatch, tmp_path)
    run()
    synthetique.execute("INSERT INTO enaf_consommation (insee, annee, destination, m2,"
                        " millesime) VALUES (?, 2022, 'total', 700, 2013)", (ICI,))
    synthetique.commit()
    sortie = exports.export_enaf(synthetique, ICI)
    assert (sortie["decennie_2011_2021_m2"], sortie["depuis_2021_m2"]) == (1800.0, 700.0)


def test_vigieau_compte_les_jours_releves(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("vigieau", monkeypatch, tmp_path)
    run()
    (annee,) = exports.export_vigieau(synthetique, ICI)["annees"]
    assert annee["jours_releves"] == 5 and annee["zones"]["AEP"]["crise"] == 2


# ── Relecture sécurité du 29/10/2026 ─────────────────────────────────────────

def test_deposer_refuse_une_url_qui_n_est_pas_https(tmp_path):
    from collectors.national_store import deposer
    with pytest.raises(RuntimeError, match="https"):
        deposer("file:///etc/passwd", tmp_path / "x.csv", "essai", None)


def test_un_code_qui_n_est_pas_insee_n_entre_pas_dans_la_requete():
    with pytest.raises(ValueError):
        odre._url(['99001") or ("1"="1'], False, 0)
    assert odre._url(["2A004", "99001"], False, 0)


def test_un_flux_sans_marqueur_ne_grossit_pas_sans_fin(monkeypatch):
    monkeypatch.setattr(vigieau, "TAMPON_MAX", 1000)
    with pytest.raises(RuntimeError, match="64 Mo"):
        list(vigieau.objets(io.BytesIO(b"[" + b"x" * 5000), {ICI}, 100))


# ── Seconde relecture DPO du 29/10/2026 ──────────────────────────────────────

def test_sous_trois_installations_l_energie_est_masquee(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("odre", monkeypatch, tmp_path)
    run()
    synthetique.execute("UPDATE odre_installations SET nb_installations=1,"
                        " energie_injectee_kwh=90000")
    synthetique.commit()
    (f,) = exports.export_odre(synthetique, ICI)["filieres"]
    assert f["installations"] == 2 and f["energie_masquee"] is True
    assert f["energie_injectee_kwh"] is None


def test_les_declarations_se_rapprochent_par_identifiant_pas_par_siret(
        synthetique, monkeypatch, tmp_path):
    """Deux cantines au SIRET de la mairie : chacune garde ses taux."""
    run, _ = _preparer("cantines", monkeypatch, tmp_path)
    run()
    synthetique.execute("INSERT INTO cantines_registre (id, insee, nom, siret, modele)"
                        " VALUES (3, ?, 'Cantine du Haut', '21990001700029', 'public')", (ICI,))
    synthetique.commit()
    sortie = exports.export_cantines(synthetique, ICI)
    par_nom = {c["nom"]: c["declarations"] for c in sortie["cantines"]}
    assert len(par_nom["École de Testonville"]) == 1 and par_nom["Cantine du Haut"] == []


def test_verify_snapshot_releve_ce_que_l_export_ne_doit_pas_sortir(tmp_path):
    from scripts.verify_snapshot import Report, check_sources_data_gouv
    (tmp_path / "territoire.json").write_text(json.dumps({
        "cantines": {"cantines": [{"nom": "x", "siret": "21990001700029"}],
                     "privees": {"nombre": 1, "repas_an": 2600}},
        "rpls": {"total": 2, "repartitions": {"pieces": [{"modalite": "6", "logements": 1}]}}}))
    (tmp_path / "environnement.json").write_text(json.dumps({
        "electricite": {"filieres": [{"filiere": "Solaire", "installations": 1,
                                      "energie_injectee_kwh": 90000, "code_eic": "17W"}]}}))
    rep = Report()
    check_sources_data_gouv(tmp_path, rep)
    assert sum(len(v) for v in rep.errors.values()) == 6, rep.errors


# ── Suites du 29/10/2026 ─────────────────────────────────────────────────────

def test_la_premiere_passe_d_un_step_compte_ce_qu_elle_cree(base):
    """`items_added` était vide à la première passe : `collecte.sh` ne publiait pas."""
    # `run_all` importe tous les collecteurs (pdfplumber…) : joué par `tests-deps`.
    pytest.importorskip("pdfplumber")
    from collectors.run_all import count_items
    assert count_items(base, "enaf") == 0
    enaf.ensure_table(base)
    base.execute("INSERT INTO enaf_consommation (insee, annee, destination, m2, millesime)"
                 " VALUES ('99001', 2011, 'total', 1.0, 2025)")
    assert count_items(base, "enaf") == 1


def test_les_financements_rpls_disent_leur_nom_de_metropole(synthetique, monkeypatch, tmp_path):
    run, _ = _preparer("rpls", monkeypatch, tmp_path)
    run()
    synthetique.execute("UPDATE rpls_communes SET modalite='PLUS/LLS dans les DOM'"
                        " WHERE dimension='financement' AND modalite='PLUS'")
    synthetique.commit()
    sortie = exports.export_rpls(synthetique, ICI)
    financements = {f["modalite"] for f in sortie["repartitions"]["financement"]}
    assert "PLUS (prêt locatif à usage social)" in financements
    assert not any("DOM" in f for f in financements)
    surfaces = [s["modalite"] for s in sortie["repartitions"]["surface"]]
    assert surfaces == ["30 à 49 m²", "70 à 89 m²", "90 m² et plus"]


def test_une_annee_se_lit_sur_la_ressource_la_plus_touchee(synthetique, monkeypatch, tmp_path):
    """Saillans affichait 0 jour en 2022 : le fichier n'a pas de zone « eau potable »
    avant les années récentes. Un zéro d'absence ne doit pas faire le titre."""
    run, _ = _preparer("vigieau", monkeypatch, tmp_path)
    run()
    synthetique.execute("DELETE FROM vigieau_jours")
    synthetique.executemany(
        "INSERT INTO vigieau_jours (insee, annee, zone, niveau, jours, dernier_jour)"
        " VALUES (?, 2022, ?, ?, ?, '2022-12-31')",
        [(ICI, "AEP", "aucune", 365), (ICI, "SOU", "aucune", 146), (ICI, "SOU", "crise", 219),
         (ICI, "SUP", "aucune", 300), (ICI, "SUP", "alerte", 65)])
    synthetique.commit()
    (annee,) = exports.export_vigieau(synthetique, ICI)["annees"]
    assert annee["plus_touchee"] == {"zone": "SOU", "jours": 219, "crise": 219}
