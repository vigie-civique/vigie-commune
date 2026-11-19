"""La recherche du site public : des mots, et un ordre qui suit la question.

Relevé du 13/11/2026 : la page cherchait la saisie entière comme une suite de
lettres dans les titres, et rangeait les résultats par catégorie, sans regarder
la requête. D'où du hors sujet (« eau » trouvait « bureau », un acteur passait
toujours devant l'acte) et des absents (« sainte croix » ne trouvait pas
« Sainte-Croix », « écoles » ne trouvait pas « école », un sigle ne trouvait
pas son association).

La recherche est du JavaScript sans dépendance (`public/src/lib/recherche.js`) :
on l'exécute avec `node` quand il est là. Aucune base, aucun réseau ; les
entrées sont fictives, au format de `recherche_index.json`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.snapshot.recherche import write_recherche_index

MODULE = Path(__file__).resolve().parent.parent / "public" / "src" / "lib" / "recherche.js"

INDEX = [
    {"k": "acteur", "t": "Bureau d'études Rousseau", "u": "/entite/1", "c": "Témoinville", "n": 1040},
    {"k": "acteur", "t": "Château de Témoinville", "u": "/entite/2", "n": 1012},
    {"k": "acteur", "t": "Aide à domicile en milieu rural", "s": "ADMR", "u": "/entite/3", "n": 1003},
    {"k": "acteur", "t": "Sainte-Croix-de-Caderle", "u": "/entite/4", "n": 1001},
    {"k": "acte", "t": "Tarifs de l’eau et de l'assainissement 2025", "d": "2025-03-12",
     "c": "temoinville.example", "n": 500},
    {"k": "acte", "t": "École : restauration scolaire, tarifs de la cantine", "d": "2024-06-20",
     "c": "temoinville.example", "n": 500},
    {"k": "acte", "t": "Subvention à l'association des parents d'élèves des écoles", "d": "2023-04-02",
     "m": 15000.0, "c": "temoinville.example", "n": 700},
    {"k": "acte", "t": "Convention de mise à disposition d'un bureau", "d": "2022-01-10",
     "c": "temoinville.example", "n": 500},
    {"k": "marche", "t": "Réfection de la voirie — Entreprise Fictive", "u": "/marches",
     "d": "2024-02-01", "m": 82000, "c": "Département Imaginaire", "n": 600},
    {"k": "dossier", "t": "L'eau à Témoinville", "u": "/dossiers/eau", "n": 2000},
]


def chercher(q: str, index=INDEX, fonction: str = "chercher"):
    if not shutil.which("node"):
        pytest.skip("node absent")
    script = (
        f"import {{ chercher, contientLesMots }} from {json.dumps(MODULE.as_uri())};"
        "const [index, q, f] = JSON.parse(process.argv[1]);"
        "const r = f === 'chercher' ? chercher(index, q) : contientLesMots(index, q);"
        "console.log(JSON.stringify(f === 'chercher'"
        " ? { titres: r.resultats.map((e) => e.t), partiel: r.partiel } : r));"
    )
    r = subprocess.run(["node", "--input-type=module", "-e", script,
                        json.dumps([index, q, fonction])],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_un_mot_ne_se_trouve_pas_au_milieu_d_un_autre():
    titres = chercher("eau")["titres"]
    assert titres, "« eau » doit trouver quelque chose"
    assert not [t for t in titres if "ureau" in t or "hâteau" in t or "ousseau" in t]


def test_le_sujet_passe_devant_l_acteur_qui_n_en_parle_pas():
    titres = chercher("eau")["titres"]
    assert titres[0] == "L'eau à Témoinville"
    assert "Tarifs de l’eau et de l'assainissement 2025" in titres


def test_plusieurs_mots_dans_n_importe_quel_ordre():
    r = chercher("cantine école")
    assert r["titres"] == ["École : restauration scolaire, tarifs de la cantine"]
    assert r["partiel"] is False


def test_tiret_apostrophe_et_accent_ne_cachent_rien():
    assert chercher("sainte croix")["titres"] == ["Sainte-Croix-de-Caderle"]
    assert chercher("caderle")["titres"] == ["Sainte-Croix-de-Caderle"]
    # apostrophe droite tapée, apostrophe typographique dans le titre
    assert chercher("l'eau")["titres"][:2] == [
        "L'eau à Témoinville", "Tarifs de l’eau et de l'assainissement 2025"]
    assert "École : restauration scolaire, tarifs de la cantine" in chercher("ecole")["titres"]


def test_le_pluriel_trouve_le_singulier_et_l_inverse():
    assert "École : restauration scolaire, tarifs de la cantine" in chercher("écoles")["titres"]
    assert ("Subvention à l'association des parents d'élèves des écoles"
            in chercher("école")["titres"])


def test_les_mots_vides_ne_sont_pas_exiges():
    assert chercher("tarifs de la cantine")["titres"] == [
        "École : restauration scolaire, tarifs de la cantine"]


def test_un_sigle_trouve_son_acteur():
    assert chercher("admr")["titres"] == ["Aide à domicile en milieu rural"]


def test_un_montant_tape_avec_son_espace():
    attendu = ["Subvention à l'association des parents d'élèves des écoles"]
    assert chercher("15 000")["titres"] == attendu
    assert chercher("15000 €")["titres"] == attendu


def test_le_payeur_d_un_marche_se_cherche_mais_pas_le_site_source_d_un_acte():
    assert chercher("département")["titres"] == ["Réfection de la voirie — Entreprise Fictive"]
    assert chercher("example")["titres"] == []


def test_a_defaut_de_tout_trouver_on_rend_le_plus_proche_en_le_disant():
    r = chercher("cantine piscine")
    assert r["partiel"] is True
    assert r["titres"] == ["École : restauration scolaire, tarifs de la cantine"]
    assert chercher("zzzz")["titres"] == []


def test_le_filtre_des_listes_suit_les_memes_regles():
    assert chercher("ecole cantine", "École : tarifs de la cantine", "filtre") is True
    assert chercher("eau", "Convention pour un bureau", "filtre") is False
    assert chercher("", "N'importe quoi", "filtre") is True


def test_l_index_porte_le_sigle_des_acteurs(tmp_path):
    """Le sigle n'était que dans `entity_index.json`, que la page abandonne dès
    que l'index transversal est chargé : « ADMR » ne trouvait plus personne."""
    acteurs = [{"id": 3, "name": "Aide à domicile en milieu rural", "short_name": "ADMR"},
               {"id": 4, "name": "Sans sigle", "short_name": None}]
    write_recherche_index(tmp_path, acteurs, [], [], [], {}, {})
    index = json.loads((tmp_path / "recherche_index.json").read_text())["index"]
    par_titre = {r["t"]: r for r in index}
    assert par_titre["Aide à domicile en milieu rural"]["s"] == "ADMR"
    assert "s" not in par_titre["Sans sigle"]


# ── Le texte des actes : des mots courants, jamais un nom (13/11/2026) ───────
#
# Le texte d'un acte garde les noms qu'il cite. L'index de ses mots ne doit en
# porter aucun : seuls les mots du lexique, et seulement pour un acte où ils ne
# s'écrivent qu'en minuscules, hors de toute civilité (avis du DPO).

import importlib.util

from scripts.snapshot.recherche import (cle_de_recherche, lire_lexique, mots_indexables,
                                        write_texte_index)

LEXIQUE = frozenset("boucher boucherie cantine école écoles tarif tarifs travaux voirie "
                    "maire petit petite rose cœur assainissement marché".split())


def test_un_nom_propre_n_entre_pas_meme_s_il_est_aussi_un_mot_courant():
    texte = ("Monsieur le maire expose. M. Boucher demande un dégrèvement ; le boucher "
             "du village aussi. Mme PETIT Rose s'y oppose. La petite cantine reste ouverte.")
    cles = mots_indexables(texte, LEXIQUE)
    # « boucher », « petit », « rose », « maire » : écrits ici comme des noms ou
    # derrière une civilité, ils sortent de CET acte, même en minuscules ailleurs.
    assert not cles & {"boucher", "petit", "rose", "maire"}
    assert {"cantine", "petite"} <= cles


def test_un_mot_hors_lexique_n_entre_jamais():
    cles = mots_indexables("m. dupond et la famille zanimet contestent les tarifs", LEXIQUE)
    assert cles == {"tarif"}


def test_le_meme_mot_en_minuscules_seules_entre():
    assert "boucher" in mots_indexables("le boucher livre la cantine", LEXIQUE)


def test_un_mot_en_capitales_sort_de_l_acte():
    cles = mots_indexables("OBJET : VOIRIE. Les travaux de voirie sont votés.", LEXIQUE)
    assert cles == {"travau"}


def test_sans_lexique_pas_d_index_et_l_ancien_disparait(tmp_path):
    (tmp_path / "recherche_texte.json").write_text("{}")
    assert write_texte_index(tmp_path, {1: "la cantine"}, None) == 0
    assert not (tmp_path / "recherche_texte.json").exists()
    assert lire_lexique(tmp_path / "absent.txt") is None


def test_l_index_range_les_actes_par_mot(tmp_path):
    textes = {7: "les tarifs de la cantine", 9: "M. Boucher et la cantine de l'école",
              11: "le cœur du village"}
    assert write_texte_index(tmp_path, textes, LEXIQUE) == 4
    d = json.loads((tmp_path / "recherche_texte.json").read_text())
    assert d["mots"] == {"cantine": [7, 9], "coeur": [11], "ecole": [9], "tarif": [7]}
    assert d["actes"] == 3


def test_la_cle_de_python_est_celle_de_la_page():
    """L'index est écrit en Python et lu en JavaScript : une clé pliée
    autrement d'un côté serait un mot introuvable, sans erreur nulle part."""
    if not shutil.which("node"):
        pytest.skip("node absent")
    mots = ["Écoles", "travaux", "cœur", "ASSAINISSEMENT", "été", "prix", "bus", "œuvres", "àâäéèêëîïôöùûüç"]
    script = (f"import {{ motsDeRequete }} from {json.dumps(MODULE.as_uri())};"
              "console.log(JSON.stringify(JSON.parse(process.argv[1]).map((m) => motsDeRequete(m)[0])));")
    r = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(mots)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [cle_de_recherche(m) for m in mots]


def chercher_avec_texte(q: str, index, texte):
    if not shutil.which("node"):
        pytest.skip("node absent")
    script = (f"import {{ chercher }} from {json.dumps(MODULE.as_uri())};"
              "const [index, q, texte] = JSON.parse(process.argv[1]);"
              "const r = chercher(index, q, () => true, texte);"
              "console.log(JSON.stringify({ titres: r.resultats.map((e) => e.t),"
              " texte: r.resultats.filter((e) => r.dansLeTexte.has(e)).map((e) => e.t),"
              " partiel: r.partiel }));")
    r = subprocess.run(["node", "--input-type=module", "-e", script,
                        json.dumps([index, q, texte])],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_un_acte_se_trouve_par_son_texte_et_la_page_le_dit():
    index = [
        {"k": "acte", "t": "Délibération n° 140", "i": 7, "n": 500},
        {"k": "acte", "t": "Tarifs de la cantine", "i": 9, "n": 500},
        {"k": "acte", "t": "Délibération n° 141", "n": 500},          # sans texte publié
    ]
    texte = {"cantine": [7, 9], "tarif": [7]}
    r = chercher_avec_texte("cantine", index, texte)
    # Le titre qui dit le sujet passe devant l'acte qui le mentionne.
    assert r["titres"] == ["Tarifs de la cantine", "Délibération n° 140"]
    assert r["texte"] == ["Délibération n° 140"]
    assert chercher_avec_texte("cantine", index, None)["titres"] == ["Tarifs de la cantine"]
    # Deux mots : l'un dans le titre, l'autre dans le texte.
    assert chercher_avec_texte("tarifs cantines", index, texte)["partiel"] is False


def _site(dossier: Path, textes: dict[int, str], mots: dict[str, list[int]]) -> Path:
    (dossier / "extrait").mkdir(parents=True)
    for i, t in textes.items():
        (dossier / "extrait" / f"{i}.json").write_text(
            json.dumps({"id": i, "texte": t}, ensure_ascii=False), encoding="utf-8")
    (dossier / "recherche_texte.json").write_text(
        json.dumps({"mots": mots}, ensure_ascii=False), encoding="utf-8")
    return dossier


@pytest.fixture(scope="module")
def vs():
    chemin = Path(__file__).resolve().parent.parent / "scripts" / "verify_snapshot.py"
    spec = importlib.util.spec_from_file_location("verify_snapshot_recherche", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_le_controle_laisse_passer_un_index_propre(vs, tmp_path):
    textes = {7: "M. Boucher conteste les tarifs de la cantine."}
    write_texte_index(tmp_path, textes, LEXIQUE)
    _site(tmp_path / "site", textes,
          json.loads((tmp_path / "recherche_texte.json").read_text())["mots"])
    rep = vs.Report()
    vs.check_index_du_texte(tmp_path / "site", rep)
    assert not rep.errors, rep.errors


@pytest.mark.parametrize("texte", [
    "M. Boucher conteste.",                      # derrière une civilité
    "BOUCHER Jean conteste, le boucher aussi.",  # en capitales
    "la famille boucher conteste.",              # civilité, tout en minuscules
])
def test_le_controle_bloque_un_mot_ecrit_comme_un_nom(vs, tmp_path, texte):
    rep = vs.Report()
    vs.check_index_du_texte(_site(tmp_path, {7: texte}, {"boucher": [7]}), rep)
    assert list(rep.errors) == ["index du texte portant un mot écrit comme un nom dans l'acte"]
    # Le rapport ne recopie pas le mot : il désigne l'acte.
    assert "boucher" not in str(rep.errors).lower()


def test_le_controle_bloque_un_acte_sans_texte_publie(vs, tmp_path):
    rep = vs.Report()
    vs.check_index_du_texte(_site(tmp_path, {7: "la cantine"}, {"cantine": [7, 8]}), rep)
    assert list(rep.errors) == ["index du texte citant un acte sans texte publié"]


def test_deux_civilites_qui_se_suivent_comptent_toutes_les_deux(vs, tmp_path):
    """Relevé sur 300 textes publiés (13/11/2026) : dans « M. X, adjoint, et
    Madame Y est… », la suite de la première civilité avalait la seconde, et le
    contrôle refusait un mot que le constructeur avait laissé passer. Le
    constructeur doit toujours écarter au moins ce que le contrôle vérifie."""
    lexique = LEXIQUE | {"responsable", "compétente", "est"}
    textes = {7: "M. Zanimet adjoint et Madame Temoin est compétente. "
                 "Mme ZANCHI, responsable du service, approuve les tarifs."}
    write_texte_index(tmp_path, textes, lexique)
    mots = json.loads((tmp_path / "recherche_texte.json").read_text())["mots"]
    assert not set(mots) & {"est", "responsable"}
    assert "tarif" in mots
    rep = vs.Report()
    vs.check_index_du_texte(_site(tmp_path / "site", textes, mots), rep)
    assert not rep.errors, rep.errors


def test_le_lexique_embarque_est_en_minuscules_et_porte_sa_licence():
    """Morphalou 3.1 (ATILF, LGPL-LR) : la liste ne va pas sans sa notice ni sa
    licence, et elle ne contient que des mots d'un seul tenant en minuscules."""
    from scripts.snapshot.recherche import LEXIQUE
    lexique = lire_lexique()
    assert lexique and len(lexique) > 100_000
    assert all(m == m.lower() and m.isalpha() and len(m) >= 3 for m in lexique)
    assert {"cantine", "assainissement", "voirie", "école"} <= lexique
    assert (LEXIQUE.parent / "NOTICE").is_file()
    assert "Linguistic Resources" in (LEXIQUE.parent / "LICENCE-LGPLLR.txt").read_text()
