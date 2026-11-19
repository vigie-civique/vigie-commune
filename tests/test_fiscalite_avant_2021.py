"""Le foncier bâti d'avant 2021 : taux communal 2015-2020 et taux départemental 2020.

Aucun appel réseau. Les réponses ci-dessous sont des extraits des réponses de
data.economie.gouv.fr relevées le 09/11/2026 (champs `select`-és par le
collecteur, valeurs inchangées) ; la base est une vraie base.

Ce que ces essais protègent :
  1. le taux voté 2020 plus le taux du département 2020 font le taux voté 2021
     de la commune (`e12vote`) : c'est tout le transfert, et la preuve que les
     deux sources parlent du même taux ;
  2. le taux départemental a sa propre portée, jamais `commune` ;
  3. un département absent ou ambigu ne donne ni zéro ni taux choisi au hasard ;
  4. l'API coupe à 100 lignes sans le dire : on suit `total_count` ;
  5. les années d'avant 2021 ne sortent pas dans `taux`, que lisent l'accueil
     et /impots : elles y feraient lire le transfert comme un relèvement.
"""
from __future__ import annotations

import json

import pytest

from collectors import fiscalite

CI = "comptes-individuels-des-communes-fichier-global-"

# `an,dep,icom,inom,tfb`, where an in (…) and (dep, icom) des trois instances.
COMMUNES = {
    CI + "2011-2015": [
        {"an": "2015", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2015", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.09},
        {"an": "2015", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
    ],
    CI + "2016": [
        {"an": "2016", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2016", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.24},
        {"an": "2016", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
    ],
    CI + "2017": [
        {"an": "2017", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2017", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.39},
        {"an": "2017", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
    ],
    CI + "2018": [
        {"an": "2018", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2018", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.54},
        {"an": "2018", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
    ],
    CI + "2019-2020": [
        {"an": "2019", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2020", "dep": "026", "icom": "289", "inom": "SAILLANS", "tfb": 14.78},
        {"an": "2019", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.7},
        {"an": "2020", "dep": "081", "icom": "037", "inom": "BRASSAC", "tfb": 15.7},
        {"an": "2019", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
        {"an": "2020", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 18.22},
    ],
}
# `exer,dep,lbudg,tfb`, where exer='2020' and dep in ('026','030','081').
DEPARTEMENTS = [
    {"exer": "2020", "dep": "026", "lbudg": "DEP DROME", "tfb": 15.51},
    {"exer": "2020", "dep": "030", "lbudg": "DEP GARD", "tfb": 24.65},
    {"exer": "2020", "dep": "081", "lbudg": "DEP TARN", "tfb": 29.91},
]
# fiscalite-locale-des-particuliers, `exercice,insee_com,e12vote`, exercice 2021.
E12VOTE_2021 = {"26289": 30.29, "30140": 42.87, "81037": 45.61}
TROIS = ["30140", "26289", "81037"]


def _par(lignes):
    return {(l[0], l[2], l[3]): l for l in lignes}


def test_le_taux_2021_est_le_taux_communal_2020_plus_celui_du_departement():
    lignes = _par(fiscalite.lignes_avant_2021(COMMUNES, DEPARTEMENTS, TROIS))
    for insee, taux_2021 in E12VOTE_2021.items():
        commune = lignes[(insee, 2020, "TFB_VOTE")][6]
        departement = lignes[(insee, 2020, "TFB_DEP")][6]
        assert round(commune + departement, 2) == taux_2021, insee


def test_six_annees_par_commune_et_un_taux_departemental():
    lignes = fiscalite.lignes_avant_2021(COMMUNES, DEPARTEMENTS, TROIS)
    for insee in TROIS:
        votes = sorted(l[2] for l in lignes if l[0] == insee and l[3] == "TFB_VOTE")
        assert votes == [2015, 2016, 2017, 2018, 2019, 2020], insee
        assert [l[2] for l in lignes if l[0] == insee and l[3] == "TFB_DEP"] == [2020]
    lasalle = _par(lignes)
    assert lasalle[("30140", 2015, "TFB_VOTE")][7] == CI + "2011-2015"
    assert lasalle[("81037", 2017, "TFB_VOTE")][6] == 15.39


def test_le_taux_departemental_n_est_jamais_attribue_a_la_commune():
    lignes = fiscalite.lignes_avant_2021(COMMUNES, DEPARTEMENTS, TROIS)
    dep = [l for l in lignes if l[3] == "TFB_DEP"]
    assert {l[5] for l in dep} == {"departement"}
    assert all("département" in l[4] and "commune" not in l[4] for l in dep)
    assert {l[7] for l in dep} == {fiscalite.COMPTES_DEPARTEMENTS}
    assert {l[5] for l in lignes if l[3] == "TFB_VOTE"} == {"commune"}


def test_une_annee_2021_ou_hors_commune_ne_passe_pas():
    jeu = {CI + "2019-2020": COMMUNES[CI + "2019-2020"] + [
        {"an": "2021", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": 42.87},
        {"an": "2020", "dep": "030", "icom": "141", "inom": "VOISINE", "tfb": 9.99},
        {"an": "2020", "dep": "030", "icom": "140", "inom": "LASALLE", "tfb": None},
    ]}
    lignes = fiscalite.lignes_avant_2021(jeu, [], ["30140"])
    assert sorted((l[2], l[6]) for l in lignes) == [(2019, 18.22), (2020, 18.22)]


def test_departement_absent_ou_ambigu():
    sans_gard = [d for d in DEPARTEMENTS if d["dep"] != "030"]
    lignes = fiscalite.lignes_avant_2021({}, sans_gard, TROIS)
    assert {l[0] for l in lignes} == {"26289", "81037"}, "pas de zéro pour le Gard"

    double = DEPARTEMENTS + [{"exer": "2020", "dep": "030", "lbudg": "AUTRE", "tfb": 11.0}]
    with pytest.raises(ValueError, match="030"):
        fiscalite.lignes_avant_2021({}, double, TROIS)


def test_pas_de_taux_departemental_en_corse():
    # Relevé du 09/11/2026 : la Collectivité de Corse publie 12,56 ; Ajaccio
    # 2020 18,40 → 2021 30,65 (12,25 transférés), Bastia 23,68 → 36,58 (12,90).
    jeu = {CI + "2019-2020": [
        {"an": "2020", "dep": "02A", "icom": "004", "inom": "AJACCIO", "tfb": 18.4},
        {"an": "2020", "dep": "02B", "icom": "033", "inom": "BASTIA", "tfb": 23.68},
    ]}
    cdc = [{"exer": "2020", "dep": "02A", "lbudg": "COLLECTIVITE DE CORSE", "tfb": 12.56}]
    lignes = fiscalite.lignes_avant_2021(jeu, cdc, ["2A004", "2B033"])
    assert sorted((l[0], l[3]) for l in lignes) == [("2A004", "TFB_VOTE"), ("2B033", "TFB_VOTE")]


def test_code_dgfip():
    assert fiscalite.code_dgfip("30140") == ("030", "140")
    assert fiscalite.code_dgfip("2A004") == ("02A", "004")
    assert fiscalite.code_dgfip("97101") is None
    assert fiscalite.code_dgfip("3014") is None


def test_la_requete_vise_les_annees_et_les_communes(monkeypatch):
    demandes = []

    def records(dataset, select, where):
        demandes.append((dataset, where))
        return []
    monkeypatch.setattr(fiscalite, "_records", records)

    fiscalite.fetch_avant_2021(TROIS + ["97101"])

    jeux = dict(demandes)
    assert set(jeux) == set(fiscalite.COMPTES_COMMUNES) | {fiscalite.COMPTES_DEPARTEMENTS}
    assert jeux[CI + "2011-2015"].startswith("an in ('2015') and ")
    assert jeux[CI + "2019-2020"].startswith("an in ('2019','2020') and ")
    assert "(dep='030' and icom='140')" in jeux[CI + "2016"]
    assert "101" not in jeux[CI + "2016"], "outre-mer : ne pas deviner son code"
    assert jeux[fiscalite.COMPTES_DEPARTEMENTS] == "exer='2020' and dep in ('026','030','081')"


def test_les_pages_suivent_total_count(monkeypatch):
    offsets = []

    def fetch(url, **_):
        offset = int(url.split("offset=")[1].split("&")[0])
        offsets.append(offset)
        n = min(100, 230 - offset)
        return {"total_count": 230, "results": [{"i": offset + k} for k in range(n)]}
    monkeypatch.setattr(fiscalite, "fetch_json", fetch)

    lignes = fiscalite._records("jeu", "a", "b")

    assert offsets == [0, 100, 200]
    assert [r["i"] for r in lignes] == list(range(230))


def test_fiscalite_json_garde_avant_2021_hors_de_taux(base, tmp_path):
    from scripts.snapshot.territoire import etape_fiscalite

    fiscalite.ensure_table(base)
    etape_fiscalite(base, tmp_path)
    vide = json.loads((tmp_path / "fiscalite.json").read_text())
    assert "avant_2021" not in vide, "sans lignes, la sortie ne change pas"

    fiscalite.import_avant_2021(base, fiscalite.lignes_avant_2021(COMMUNES, DEPARTEMENTS, TROIS))
    base.execute("INSERT INTO fiscalite_taux (insee, annee, indicateur, portee, taux)"
                 " VALUES ('30140', 2021, 'TFB_VOTE', 'commune', 42.87)")
    base.commit()
    etape_fiscalite(base, tmp_path)
    d = json.loads((tmp_path / "fiscalite.json").read_text())

    assert [(t["annee"], t["taux"]) for t in d["taux"]] == [(2021, 42.87)]
    assert d["total"] == 1
    assert len(d["avant_2021"]) == 3 * 6 + 3
    gard = next(t for t in d["avant_2021"] if t["insee"] == "30140" and t["indicateur"] == "TFB_DEP")
    assert (gard["annee"], gard["taux"], gard["portee"]) == (2020, 24.65, "departement")
    assert gard["source"] == ("https://data.economie.gouv.fr/explore/dataset/"
                              + fiscalite.COMPTES_DEPARTEMENTS + "/")


def test_un_taux_a_zero_est_absent_et_retire_de_la_base(base):
    # Fontrieu (81062), commune nouvelle du 01/01/2016 : 0 en 2016 entre deux 13,06.
    jeu = {CI + "2016": [{"an": "2016", "dep": "081", "icom": "062", "inom": "FONTRIEU", "tfb": 0.0}],
           CI + "2017": [{"an": "2017", "dep": "081", "icom": "062", "inom": "FONTRIEU", "tfb": 13.06}]}
    lignes = fiscalite.lignes_avant_2021(jeu, [{"exer": "2020", "dep": "081", "tfb": 0}], ["81062"])
    assert [(l[2], l[6]) for l in lignes] == [(2017, 13.06)], "ni zéro communal ni départemental"

    fiscalite.ensure_table(base)
    base.execute("INSERT INTO fiscalite_taux (insee, annee, indicateur, portee, taux)"
                 " VALUES ('81062', 2016, 'TFB_VOTE', 'commune', 0.0)")
    base.commit()
    fiscalite.import_avant_2021(base, lignes, fiscalite.zeros_avant_2021(jeu, ["81062"]))
    assert [tuple(r) for r in base.execute("SELECT annee, taux FROM fiscalite_taux WHERE insee='81062'"
                        " ORDER BY annee")] == [(2017, 13.06)]
