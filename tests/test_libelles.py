"""Les formes grammaticales du nom de commune.

« de Lasalle » ne se dérive pas mécaniquement en « de Alès », ni « à Le Bez ».
Une chaîne « de {COMMUNE} » écrite à la main finit toujours par produire une
faute sur la commune suivante — d'où un module généré plutôt que des libellés
dans le code.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def gl():
    spec = importlib.util.spec_from_file_location(
        "generer_libelles", ROOT / "scripts" / "generer_libelles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("nom, de, a", [
    ("Brassac",        "de Brassac",        "à Brassac"),
    ("Alès",           "d'Alès",            "à Alès"),
    ("Le Bez",         "du Bez",            "au Bez"),
    ("Les Plantiers",  "des Plantiers",     "aux Plantiers"),
    ("La Salvetat",    "de La Salvetat",    "à La Salvetat"),
    ("Uzès",           "d'Uzès",            "à Uzès"),
])
def test_formes_grammaticales(gl, nom, de, a):
    f = gl.formes(nom)
    assert f["de"] == de
    assert f["a"] == a


def test_les_deux_applications_recoivent_le_module(gl):
    """Le site public ET l'atelier : l'atelier a été oublié pendant des mois,
    et nommait donc la commune d'origine dans ses 21 pages."""
    cibles = {c.parent.parent.parent.name for c in gl.CIBLES}
    assert cibles == {"public", "dashboard"}
    assert all(c.name == "instance.js" for c in gl.CIBLES)


def test_le_module_genere_nest_pas_versionne():
    """Il décrit une commune : il n'a rien à faire dans un dépôt public."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "public/src/lib/instance.js" in ignore
    assert "dashboard/src/lib/instance.js" in ignore


def test_les_destinataires_sortent_en_liste_de_paragraphes(gl, tmp_path):
    """Une liste doit arriver en tableau JS, pas en chaîne « ['…'] » : la page
    les parcourt un par un. Les paragraphes vides ne sortent pas."""
    cible = tmp_path / "instance.js"
    gl.ecrire({"DESTINATAIRES": ["Hébergé en France.", "Code chez GitHub."]}, [cible])
    texte = cible.read_text(encoding="utf-8")
    assert 'export const DESTINATAIRES = ["Hébergé en France.", "Code chez GitHub."]' in texte


# ── Les mots de l'atelier : un seul endroit, en français ─────────────────────
# Audit de l'atelier du 15/11/2026 (constats 12 et 13) : `person`, `verified`,
# « Périm. », « Rel. », « 500 » bruts à l'écran, trois tables de libellés de
# type, un message qui parlait d'une commande à relancer. `lib/libelles.js` est
# désormais le seul endroit ; ces contrôles empêchent le retour des mots.

def _sources_atelier():
    racine = ROOT / "dashboard" / "src"
    return [f for f in sorted(racine.rglob("*")) if f.suffix in (".svelte", ".js")
            and "node_modules" not in f.parts and f.name != "instance.js"]


def _code(f):
    """Le fichier sans ses commentaires de ligne : l'histoire d'un défaut peut
    nommer une commune ou citer le message qu'on a retiré."""
    import re
    texte = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(l for l in texte.splitlines()
                     if not l.lstrip().startswith(("//", "/*", "*")))


INTERDITS_A_L_ECRAN = [
    ("Vous proposez ; un validateur tranche", "faux : un contributeur écrit directement sur une fiche non publiée"),
    ("run_all", "commande d'exploitation, pas un message pour un bénévole"),
    ("Enregistrer ce ${", "accord fautif (« ce entité »)"),
    ("la ligne a disparu de la base", "faux quand la saisie avait créé une fiche"),
    ("Périm.", "abréviation"),
    (">Rel.<", "abréviation"),
    ("Erreur : {", "code brut devant l'utilisateur : une phrase (libelles.js)"),
    ("throw new Error(`${res.status}`)", "code brut : erreurHttp(res)"),
    ("<option value=\"verified\">verified</option>", "fiabilité en anglais"),
    ("<option value=\"hypothesis\">hypothesis</option>", "fiabilité en anglais"),
    ("Websites candidats", "anglais"),
]


@pytest.mark.parametrize("motif, pourquoi", INTERDITS_A_L_ECRAN)
def test_aucun_mot_interdit_a_l_ecran(motif, pourquoi):
    fautes = [str(f.relative_to(ROOT)) for f in _sources_atelier()
              if motif in _code(f)]
    assert not fautes, f"« {motif} » ({pourquoi}) dans : {fautes}"


def test_les_libelles_de_type_n_ont_qu_une_table():
    """`person: 'personne'` s'écrit dans lib/libelles.js, nulle part ailleurs."""
    ailleurs = [str(f.relative_to(ROOT)) for f in _sources_atelier()
                if "person: 'personne'" in _code(f)
                and f.name != "libelles.js"]
    assert not ailleurs, f"une seconde table de libellés de type : {ailleurs}"


def test_pas_de_commune_nommee_dans_l_atelier():
    """Le moteur ne nomme aucune commune : le texte propre à une instance vient
    de `instance.js` (généré), jamais d'un fichier versionné."""
    fautes = [str(f.relative_to(ROOT)) for f in _sources_atelier()
              if "Lasalle" in _code(f)]
    assert not fautes, fautes


def test_phrases_d_erreur_sans_code_et_sans_jargon():
    """Chaque code courant a une phrase, et aucune ne montre le code lui-même."""
    import re
    source = (ROOT / "dashboard" / "src" / "lib" / "libelles.js").read_text(encoding="utf-8")
    bloc = source[source.index("export function phraseStatut"):source.index("export function phraseErreur")]
    for code in ("401", "403", "404", "409", "422", "429"):
        assert f"s === {code}" in bloc, f"pas de phrase pour {code}"
    phrases = re.findall(r"return ['\"]([^'\"]+)['\"]", bloc)
    assert phrases and all(not re.search(r"\b[1-5]\d\d\b", p) for p in phrases)
