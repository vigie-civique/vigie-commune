"""La tuile « Prix de l'eau » : les services d'AUJOURD'HUI, et des courbes sans pente inventée.

Relevé du 09/11/2026 : l'accueil de Lasalle affichait « 2,43 à 4,63 €, 2022
et 2024, 3 services ». La régie communale (n° 173063, 2,43 € en 2022) n'est
plus dans la composition communale SISPEA depuis 2023 : la communauté de
communes l'a reprise (n° 324955). Même défaut à Saillans, dont la régie
(n° 71693, 2,04 € en 2019) a cédé la place au SMPAS.

Les fixtures reprennent les compositions réelles (CompositionCommunaleServices
_AEP_2015 à 2025, colonnes « code INSEE de la commune adhérente » et
« identifiant SISPEA de l'entité de gestion », relues le 09/11/2026) et les
prix publiés dans les `environnement.json` des trois instances.

Aucun réseau, aucune base d'instance. La tuile et le mini-graphique sont du
JavaScript : on les exécute avec `node` quand il est là (le mini-graphique
demande en plus `public/node_modules`, installé par le job de build).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from collectors import sispea
from collectors.sispea import _ecrire_service, ensure_tables

PUBLIC = Path(__file__).resolve().parent.parent / "public"

# ── Les compositions communales réelles, réduites aux trois communes ─────────
# {insee: [(service, libellé, années)]}
RATTACHEMENTS = {
    "30140": [  # Lasalle
        ("173063", "eau potable", range(2015, 2023)),                       # régie
        ("173349", "eau potable : SIAEP de Lasalle", range(2015, 2026)),
        ("324955", "eau potable : Lasalle", range(2023, 2026)),             # la CC
    ],
    "26289": [  # Saillans, commune nouvelle avec Véronne en 2025
        ("71693", "eau potable", range(2015, 2020)),                        # régie
        ("71850", "eau potable : SMPAS SI DES EAUX", range(2020, 2026)),
        ("71891", "eau potable (Véronne)", range(2025, 2026)),
    ],
    "81037": [  # Brassac
        ("140062", "eau potable", range(2015, 2026)),
    ],
}

# Le champ `type_collectivite` publié (environnement.json de Lasalle, 09/11/2026).
TYPES = {"173063": "Commune", "173349": "Syndicat Intercommunal à Vocation Unique",
         "324955": "Communauté de communes"}

# Les prix D102.0 publiés (environnement.json des instances, 09/11/2026).
PRIX = {
    "173063": {2009: 1.598, 2016: 2.28, 2017: 2.24, 2018: 2.34, 2019: 2.27, 2020: 2.43,
               2021: 2.43, 2022: 2.43},
    "173349": {2009: 2.619, 2010: 2.653, 2011: 2.74, 2012: 2.847, 2013: 2.887, 2015: 2.883,
               2018: 3.46, 2019: 3.51, 2020: 3.7, 2021: 3.81, 2022: 4.0, 2023: 4.21, 2024: 4.63},
    "324955": {2023: 2.54, 2024: 2.75},
    "71693": {2014: 1.975, 2016: 1.91, 2017: 2.07, 2018: 2.04, 2019: 2.04},
    "71850": {2016: 2.24, 2017: 2.38, 2018: 2.38, 2019: 2.28, 2020: 2.38, 2021: 2.38,
              2022: 2.4, 2023: 2.4, 2024: 2.79},
    "71891": {},  # « en attente de saisie »
    "140062": {2008: 1.258, 2009: 1.336, 2010: 1.468, 2011: 1.556, 2014: 1.781,
               2021: 2.23, 2024: 2.18},
}


def _composition(competence: str, annee: int) -> dict:
    if competence != "AEP":
        return {"communes": {}, "services": {}}
    communes: dict[str, list[str]] = {}
    services: dict[str, dict] = {}
    for insee, liste in RATTACHEMENTS.items():
        for code, libelle, annees in liste:
            if annee in annees:
                communes.setdefault(insee, []).append(code)
                services[code] = {"libelle": libelle, "communes": insee,
                                  "annee_operateur": str(annee)}
    return {"communes": communes, "services": services}


def _moisson(monkeypatch, insee: str) -> dict:
    monkeypatch.setattr(sispea, "communes_du_step", lambda _s: [insee])
    monkeypatch.setattr(sispea, "composition", _composition)
    return sispea._moisson_composition()


class TestDerniereComposition:
    """Le collecteur dit, service par service, la dernière année où il dessert
    la commune : c'est ce qui sépare le service d'aujourd'hui de l'historique."""

    def test_lasalle_la_regie_s_arrete_quand_la_cc_la_reprend(self, monkeypatch):
        moisson = _moisson(monkeypatch, "30140")
        annees = {code: identite["derniere_composition"] for code, (_c, identite) in moisson.items()}
        assert annees == {"173063": 2022, "173349": 2025, "324955": 2025}

    def test_saillans_la_regie_disparait_veronne_arrive(self, monkeypatch):
        moisson = _moisson(monkeypatch, "26289")
        annees = {code: identite["derniere_composition"] for code, (_c, identite) in moisson.items()}
        assert annees == {"71693": 2019, "71850": 2025, "71891": 2025}

    def test_le_passage_de_l_ofb_ne_l_efface_pas(self, base, monkeypatch):
        """Les extractions de l'OFB repassent sur chaque service sans connaître
        les compositions : l'année écrite par la composition doit survivre."""
        ensure_tables(base)
        for code, (competence, identite) in _moisson(monkeypatch, "30140").items():
            _ecrire_service(base, code, competence, identite)
        _ecrire_service(base, "173063", "AEP", {"nom": "LASALLE", "mode_gestion": "Regie",
                                                "indicateurs": {"D102.0": 2.43}})
        lues = dict(base.execute(
            "SELECT code_service, derniere_composition FROM sispea_services").fetchall())
        assert lues == {"173063": 2022, "173349": 2025, "324955": 2025}

    def test_une_base_ancienne_recoit_la_colonne(self, base):
        base.execute("CREATE TABLE sispea_services (code_service TEXT PRIMARY KEY,"
                     " competence TEXT NOT NULL, nom TEXT, libelle TEXT, type_collectivite TEXT,"
                     " mode_gestion TEXT, siren TEXT, communes TEXT)")
        ensure_tables(base)
        colonnes = {r[1] for r in base.execute("PRAGMA table_info(sispea_services)")}
        assert "derniere_composition" in colonnes


class TestPublication:
    """De la base à `environnement.json` : sans le champ publié, la tuile ne
    peut rien filtrer."""

    def _publie(self, base, tmp_path) -> dict:
        from scripts.snapshot.territoire import etape_environnement
        etape_environnement(base, tmp_path)
        return {s["code_service"]: s for s in json.loads(
            (tmp_path / "environnement.json").read_text())["sispea_services"]}

    def test_le_snapshot_publie_la_derniere_composition(self, base, tmp_path, monkeypatch):
        ensure_tables(base)
        for code, (competence, identite) in _moisson(monkeypatch, "30140").items():
            _ecrire_service(base, code, competence, identite)
        base.commit()
        publies = self._publie(base, tmp_path)
        assert {c: s["derniere_composition"] for c, s in publies.items()} == {
            "173063": 2022, "173349": 2025, "324955": 2025}

    def test_une_base_pas_encore_recollectee_se_publie_quand_meme(self, base, tmp_path):
        base.execute("CREATE TABLE sispea_services (code_service TEXT PRIMARY KEY,"
                     " competence TEXT NOT NULL, nom TEXT, libelle TEXT, type_collectivite TEXT,"
                     " mode_gestion TEXT, siren TEXT, communes TEXT)")
        base.execute("INSERT INTO sispea_services (code_service, competence) VALUES ('1', 'AEP')")
        base.commit()
        assert "derniere_composition" not in self._publie(base, tmp_path)["1"]


# ── La tuile, exécutée par node ──────────────────────────────────────────────

def _node(script: str, entree: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node absent")
    fini = subprocess.run(["node", "--input-type=module", "-e", script], cwd=PUBLIC,
                          input=json.dumps(entree), capture_output=True, text=True,
                          timeout=120)
    assert fini.returncode == 0, fini.stderr
    return json.loads(fini.stdout)


TUILE = """
import { tableauDeBord } from '#lib/tableau.server.js'
let entree = ''
for await (const morceau of process.stdin) entree += morceau
const { env, insee } = JSON.parse(entree)
const lire = (nom, defaut) => (nom === 'environnement.json' ? env : defaut)
const tuiles = tableauDeBord({ lire, insee, epciCourt: 'CC', aujourdhui: '2026-11-09',
                               chiffres: {}, interco: null, sourceMarches: null,
                               extraitsMarches: [] })
console.log(JSON.stringify(tuiles.find((t) => t.cle === 'eau') || null))
"""


def _environnement(insee: str, avec_composition: bool = True) -> dict:
    derniere = {code: max(annees) for code, _l, annees in RATTACHEMENTS[insee]}
    services = [{"code_service": code, "competence": "AEP", "libelle": libelle,
                 "communes": insee,
                 **({"type_collectivite": TYPES[code]} if code in TYPES else {}),
                 **({"derniere_composition": derniere[code]} if avec_composition else {})}
                for code, libelle, _a in RATTACHEMENTS[insee]]
    # Un service d'assainissement ne doit jamais entrer dans la tuile.
    services.append({"code_service": "999", "competence": "AC", "derniere_composition": 2025})
    indicateurs = [{"code_service": code, "annee": annee, "code": "D102.0", "valeur": valeur}
                   for code, _l, _a in RATTACHEMENTS[insee]
                   for annee, valeur in PRIX[code].items()]
    indicateurs.append({"code_service": "999", "annee": 2024, "code": "D204.0", "valeur": 9.0})
    return {"sispea_services": services, "sispea_indicateurs": indicateurs}


def _tuile(insee: str, avec_composition: bool = True) -> dict:
    return _node(TUILE, {"env": _environnement(insee, avec_composition), "insee": insee})


class TestTuile:
    def test_lasalle_deux_services_d_aujourd_hui(self):
        t = _tuile("30140")
        assert t["valeur"] == "2,75 à 4,63 €"
        # Deux prix, une courbe : la note dit de quel service (vérificateur, 09/11).
        assert t["note"] == ("le m³ toutes taxes comprises, 2024, 2 services,"
                             " courbe\u00a0: le syndicat intercommunal")
        # Le service de la CC n'a que deux points (2023, 2024) : pas de courbe.
        assert [len(s["points"]) for s in t["graphe"]["series"]] == [len(PRIX["173349"])]
        assert t["graphe"]["annuel"] is True

    def test_sans_type_publie_la_note_se_tait_sur_la_courbe(self):
        env = _environnement("30140")
        for s in env["sispea_services"]:
            s.pop("type_collectivite", None)
        t = _node(TUILE, {"env": env, "insee": "30140"})
        assert t["note"] == "le m³ toutes taxes comprises, 2024, 2 services"

    def test_deux_services_de_meme_nature_la_note_se_tait(self):
        env = _environnement("30140")
        for s in env["sispea_services"]:
            if s["code_service"] == "324955":
                s["type_collectivite"] = "Syndicat Intercommunal à Vocation Multiple"
        t = _node(TUILE, {"env": env, "insee": "30140"})
        assert t["note"] == "le m³ toutes taxes comprises, 2024, 2 services"

    def test_saillans_le_smpas_et_veronne_sans_prix(self):
        t = _tuile("26289")
        assert t["valeur"] == "2,79 €"
        assert t["note"] == "le m³ toutes taxes comprises, 2024, un autre service sans prix publié"
        assert [len(s["points"]) for s in t["graphe"]["series"]] == [len(PRIX["71850"])]

    def test_brassac_inchange(self):
        t = _tuile("81037")
        assert (t["valeur"], t["note"]) == ("2,18 €", "le m³ toutes taxes comprises, 2024")
        assert len(t["graphe"]["series"]) == 1

    def test_un_fichier_publie_sans_le_champ_garde_tous_les_services(self):
        """Les environnement.json en ligne au 09/11/2026 n'ont pas le champ :
        la tuile ne devine pas, elle rend ce qu'elle rendait."""
        t = _tuile("30140", avec_composition=False)
        assert (t["valeur"], t["note"]) == ("2,43 à 4,63 €", "le m³ toutes taxes comprises, 2022 et 2024, 3 services")
        # Sans la composition, un service sans prix peut être mort : on n'en dit rien.
        t = _tuile("26289", avec_composition=False)
        assert (t["valeur"], t["note"]) == ("2,04 à 2,79 €", "le m³ toutes taxes comprises, 2019 et 2024, 2 services")

    def test_aucun_service_a_trois_points_pas_de_courbe(self):
        env = _environnement("30140")
        env["sispea_services"] = [s for s in env["sispea_services"] if s["code_service"] == "324955"]
        t = _node(TUILE, {"env": env, "insee": "30140"})
        assert t["valeur"] == "2,75 €" and t["graphe"] is False


# ── Le mini-graphique : une année absente coupe le trait ────────────────────

# Le `<script>` de Mini.svelte, exécuté tel quel par node, SANS le compilateur
# Svelte : le job « tests-deps » a node mais pas `public/node_modules`, et y
# refuse tout test sauté. Ses déclarations réactives (`$: courbes = …`) sont du
# JavaScript valide (une étiquette `$`) ; seul `export let graphe` devient le
# paramètre d'une fonction. Le gabarit (`<path d={c.d}>`) n'est pas éprouvé
# ici : la compilation et le prérendu le sont par `npm run build` (job site).
MINI = """
import { readFileSync } from 'node:fs'
let entree = ''
for await (const morceau of process.stdin) entree += morceau
const source = readFileSync('src/lib/components/Mini.svelte', 'utf8')
const script = source.match(/<script>([\\s\\S]*?)<\\/script>/)[1]
for (const attendu of ['export let graphe', '$: courbes =']) {
  if (!script.includes(attendu)) throw new Error(`Mini.svelte a changé de forme : « ${attendu} » absent`)
}
const corps = 'let barres, courbes, parts\\n' + script.replace('export let graphe', '') + '\\nreturn courbes'
const courbes = new Function('graphe', corps)(JSON.parse(entree))
console.log(JSON.stringify(courbes.map((c) => c.d)))
"""


def _traces(graphe: dict) -> list[str]:
    return _node(MINI, graphe)


def _courbe(prix: dict, annuel: bool) -> dict:
    return {"type": "courbe", "depuisZero": True, "annuel": annuel,
            "series": [{"ton": "ardoise",
                        "points": [{"x": a, "y": v} for a, v in sorted(prix.items())]}]}


class TestMini:
    def test_brassac_quatre_morceaux_dont_trois_points_isoles(self):
        """2008-2011, puis 2014, 2021, 2024 seules : aucun trait entre elles."""
        (d,) = _traces(_courbe(PRIX["140062"], annuel=True))
        morceaux = [m.strip() for m in d.split("M") if m.strip()]
        assert len(morceaux) == 4
        assert morceaux[0].count("L") == 3                   # 2008 → 2011
        for isole in morceaux[1:]:                           # segment nul : un point
            debut, fin = isole.split(" L")
            assert debut == fin

    def test_le_siaep_coupe_en_2014_et_de_2016_a_2017(self):
        (d,) = _traces(_courbe(PRIX["173349"], annuel=True))
        assert d.count("M") == 3

    def test_une_serie_continue_ne_change_pas(self):
        (d,) = _traces(_courbe(PRIX["71850"], annuel=True))
        assert d.count("M") == 1 and d.count("L") == len(PRIX["71850"]) - 1

    def test_sans_annuel_le_trace_est_celui_d_avant(self):
        """La population (recensements espacés) et les autres courbes : un trait."""
        (d,) = _traces(_courbe(PRIX["140062"], annuel=False))
        assert d.count("M") == 1 and d.count("L") == len(PRIX["140062"]) - 1


# ── Les autres tuiles à série : taxe foncière en marches, axe dit ────────────
# Décision de Julien du 09/11/2026 : un graphique dans toute tuile dont la
# série publiée a au moins trois points, taxe foncière comprise ; un axe qui ne
# part pas de zéro dit ses bornes. Les valeurs sont celles des fichiers publiés
# de Lasalle et de Brassac (fiscalite.json, ofgl.json, lus le 09/11/2026).

TUILES = """
import { tableauDeBord } from '#lib/tableau.server.js'
let entree = ''
for await (const morceau of process.stdin) entree += morceau
const { fichiers, insee, cle } = JSON.parse(entree)
const lire = (nom, defaut) => fichiers[nom] ?? defaut
const tuiles = tableauDeBord({ lire, insee, aujourdhui: '2026-11-09', chiffres: {},
                               interco: null, sourceMarches: null, extraitsMarches: [] })
console.log(JSON.stringify(tuiles.find((t) => t.cle === cle) || null))
"""

TAUX = {
    "30140": {2021: 42.87, 2022: 42.87, 2023: 42.87, 2024: 44.16, 2025: 44.16},
    "81037": {2021: 45.61, 2022: 45.61, 2023: 45.61, 2024: 45.61, 2025: 45.61},
}


def _foncier(insee: str, taux: dict) -> dict:
    lignes = [{"insee": insee, "indicateur": "TFB_VOTE", "annee": a, "taux": v}
              for a, v in taux.items()]
    return _node(TUILES, {"fichiers": {"fiscalite.json": {"taux": lignes}},
                          "insee": insee, "cle": "foncier-bati"})


class TestTaxeFonciere:
    def test_lasalle_des_marches_et_la_note_de_242_inchangee(self):
        t = _foncier("30140", TAUX["30140"])
        assert (t["valeur"], t["note"]) == (
            "44,16 %", "taux voté par la commune en 2025, relevé en 2024 (+1,29 point)")
        g = t["graphe"]
        assert g["escalier"] is True and g["annuel"] is True
        assert g["bornes"] == [42, 45]
        assert g["echelle"] == "échelle de 42 à 45 %"

    def test_brassac_un_taux_inchange_reste_un_palier(self):
        t = _foncier("81037", TAUX["81037"])
        assert t["note"] == "taux voté par la commune en 2025, inchangé de 2021 à 2025"
        assert t["graphe"]["bornes"] == [45, 46]

    def test_deux_annees_seulement_pas_de_graphique(self):
        t = _foncier("30140", {2024: 44.16, 2025: 44.16})
        assert t["valeur"] == "44,16 %" and not t["graphe"]


class TestRecettes:
    def test_l_axe_des_recettes_dit_ses_bornes(self):
        recettes = {2018: 1.28e6, 2019: 1.337e6, 2020: 1.365e6, 2021: 1.312e6,
                    2022: 1.601e6, 2023: 1.84e6, 2024: 1.834e6, 2025: 1.843e6}
        depenses = {2018: 1.138e6, 2019: 1.209e6, 2020: 1.158e6, 2021: 1.257e6,
                    2022: 1.487e6, 2023: 1.709e6, 2024: 1.671e6, 2025: 1.669e6}
        lignes = ([{"agregat": "Recettes de fonctionnement", "year": a, "montant": v}
                   for a, v in recettes.items()]
                  + [{"agregat": "Dépenses de fonctionnement", "year": a, "montant": v}
                     for a, v in depenses.items()])
        t = _node(TUILES, {"fichiers": {"ofgl.json": {"ofgl": lignes}},
                           "insee": "30140", "cle": "budget"})
        # Les libellés vérifiés en #242 ne bougent pas.
        assert t["valeur"] == "1,84 M€"
        assert t["note"] == "2025, comptes publics (OFGL), entre 1,28 M€ (2018) et 1,84 M€ (2025)"
        assert t["graphe"]["bornes"] == [1.1e6, 1.9e6]
        assert t["graphe"]["echelle"] == "échelle de 1,1 à 1,9 M€"
        assert [s["ton"] for s in t["graphe"]["series"]] == ["recette", "depense"]


class TestMarches:
    def _escalier(self, taux: dict) -> str:
        (d,) = _traces({"type": "courbe", "escalier": True, "annuel": True, "bornes": [42, 45],
                        "series": [{"ton": "ardoise",
                                    "points": [{"x": a, "y": v} for a, v in sorted(taux.items())]}]})
        return d

    def test_un_palier_par_annee_une_contremarche_au_changement(self):
        d = self._escalier(TAUX["30140"])
        assert d.count("M") == 1 and "L" not in d
        assert d.count("H") == 5                    # cinq années, cinq paliers
        assert d.endswith("H120.0")                 # la dernière année a sa largeur
        hauteurs = {float(v.split()[0]) for v in d.split("V")[1:]}
        assert len(hauteurs) == 2                   # 42,87 puis 44,16 : deux niveaux

    def test_une_annee_absente_coupe_le_trait(self):
        d = self._escalier({2021: 42.87, 2022: 42.87, 2024: 44.16, 2025: 44.16})
        assert d.count("M") == 2
