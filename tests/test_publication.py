"""`public_entity` — la fonction qui décide, fiche par fiche.

Elle applique quatre règles dans l'ordre : niveau de confiance, rôle civique
pour les personnes, périmètre, puis pertinence. Chacune a son motif de rejet,
consigné dans les statistiques du snapshot pour que les exclusions se comptent
au lieu de se deviner.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def bps():
    spec = importlib.util.spec_from_file_location(
        "build_public_snapshot", ROOT / "scripts" / "build_public_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fiche(**kw) -> dict:
    base = {"id": 1, "type": "business", "name": "Une entreprise",
            "confidence": "verified", "perimetre": "C1", "commune": "Testonville"}
    base.update(kw)
    return base


# ── Niveau de confiance ──────────────────────────────────────────────────────

@pytest.mark.parametrize("niveau", ["probable", "hypothesis", "unverified", "retracted"])
def test_confidence_privee_rejetee(bps, niveau):
    publiee, motifs = bps.public_entity(fiche(confidence=niveau), [], set())
    assert publiee is None
    assert motifs == ["private_confidence"]


@pytest.mark.parametrize("niveau", ["verified", "confirmed"])
def test_confidence_publique_acceptee(bps, niveau):
    publiee, _ = bps.public_entity(fiche(confidence=niveau), [], set())
    assert publiee is not None


# ── Entrepreneur individuel : nommé seulement si un fait public le désigne ───

def _ei(**kw):
    return fiche(id=7, name="Jeanne Exemple", legal_form_code="1000", **kw)


@pytest.mark.parametrize("statut", ["A", "C", None])
def test_un_entrepreneur_individuel_sans_fait_public_est_ecarte(bps, statut):
    """En activité, cessé ou registre muet : sans fait, pas de nom en ligne."""
    publiee, motifs = bps.public_entity(_ei(biz_status=statut), [], set())
    assert publiee is None and motifs == ["ei_sans_fait_public"]


@pytest.mark.parametrize("statut", ["A", "C"])
def test_un_entrepreneur_individuel_designe_par_un_fait_public_est_publie(bps, statut):
    publiee, _ = bps.public_entity(_ei(biz_status=statut), [], set(), ids_retenus={7})
    assert publiee is not None


def test_un_fait_sur_une_autre_fiche_ne_retient_pas_l_entrepreneur(bps):
    assert bps.public_entity(_ei(biz_status="A"), [], set(), ids_retenus={8})[0] is None


def test_une_societe_reste_publiee_sans_fait(bps):
    for statut in ("A", "C", None):
        assert bps.public_entity(
            fiche(legal_form_code="5499", biz_status=statut), [], set())[0] is not None


def _source_publique():
    from scripts.snapshot.socle import RULES
    return [s for s in sorted(RULES["events"]["public_sources"]) if s != "bodacc"][0]


def _publique(base, entite, nom, forme="7345", **kw):
    """Une partie publique : forme juridique INSEE 7xxx (syndicat de droit public)."""
    eid = entite(nom, **kw)
    base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, ?)",
                 (eid, forme))
    return eid


def _retenus(base, **kw):
    """`ids_retenus_par_un_fait` sans filtre en amont, sauf ce que l'essai passe :
    tous les actes de la base pour publiés, toutes les entités pour extrémités
    publiables, aucun masque. Les paramètres sont obligatoires dans le builder."""
    from scripts.snapshot.fiches import ids_retenus_par_un_fait
    kw.setdefault("ids_actes_publics", {r[0] for r in base.execute("SELECT id FROM events")})
    kw.setdefault("extremites_ok", {r[0] for r in base.execute("SELECT id FROM entities")})
    kw.setdefault("redige", lambda t: t)
    return ids_retenus_par_un_fait(base, **kw)


def _acte(base, source, role, entity_id, type_="deliberation"):
    eid = base.execute("INSERT INTO events (type, source) VALUES (?,?)",
                       (type_, source)).lastrowid
    base.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?,?,?)",
                 (eid, entity_id, role))
    return eid


def _commune(base):
    from collectors.config import COMMUNE_NAME
    from collectors.nom_normalise import normaliser
    nom = f"Commune de {COMMUNE_NAME}"
    return base.execute(
        "INSERT INTO entities (type, name, name_norm, commune, confidence) "
        "VALUES ('service', ?, ?, ?, 'verified')", (nom, normaliser(nom), COMMUNE_NAME)).lastrowid


def test_les_faits_qui_retiennent_une_fiche(bps, base, entite):
    a = _publique(base, entite, "Syndicat des eaux")
    b, c, d, e = (entite(f"Fiche {i}") for i in range(4))
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('subvention', ?, ?, 'verified')", (a, b))
    base.execute("INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom,"
                 " titulaire_id, objet, source, confidence)"
                 " VALUES (?, '1', 'x', ?, 'o', 's', 'confirmed')", (d, c))
    _acte(base, _source_publique(), "cite", e)
    # `a` (la partie publique) n'est pas retenue : l'autre partie, `b`, ne l'est pas.
    # Marché : le titulaire (`c`) seul retient, pas l'acheteur (`d`).
    assert _retenus(base) == {b, c, e}


def test_une_cession_dont_la_commune_est_partie_retient(bps, base, entite):
    commune = _commune(base)
    ei = entite("Fonds vendu à la commune")
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('cession_fonds', ?, ?, 'verified')", (commune, ei))
    assert ei in _retenus(base)


def test_une_annonce_bodacc_ou_un_acte_non_public_ne_retient_pas(bps, base, entite):
    _acte(base, "bodacc", "cite", entite("A"), "annonce")
    _acte(base, "source_inconnue", "cite", entite("B"))
    assert _retenus(base) == set()


def test_seuls_les_actes_publies_retiennent(bps, base, entite):
    e1, e2 = entite("Acte publié"), entite("Acte écarté")
    src = _source_publique()
    publie, ecarte = _acte(base, src, "cite", e1), _acte(base, src, "cite", e2)
    assert _retenus(base, ids_actes_publics={publie}) == {e1}
    assert _retenus(base) == {e1, e2}


def test_un_flux_lu_dans_une_rubrique_de_delegation_ne_retient_pas(bps, base, entite):
    ei = entite("Bénéficiaire d'une décision du maire")
    commune = _commune(base)
    rubrique = base.execute("INSERT INTO events (type, source) VALUES ('deliberation','x')").lastrowid
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, event_id, confidence)"
                 " VALUES ('subvention', ?, ?, ?, 'verified')", (commune, ei, rubrique))
    assert _retenus(base, ids_actes_publics=set(), actes_de_rubrique={rubrique}) == set()
    assert _retenus(base, ids_actes_publics=set()) == {ei}


def test_un_fait_qui_ne_sort_pas_ne_retient_pas(bps, base, entite):
    """Confiance privée, cession entre particuliers, permis (demandeur)."""
    e = [entite(f"E{i}") for i in range(7)]
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('subvention', ?, ?, 'probable')", (e[0], e[1]))
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('cession_fonds', ?, ?, 'verified')", (e[2], e[3]))
    base.execute("INSERT INTO marches_publics (acheteur_id, acheteur_siren, acheteur_nom,"
                 " titulaire_id, objet, source, confidence)"
                 " VALUES (?, '1', 'x', ?, 'o', 's', 'probable')", (e[4], e[5]))
    _acte(base, _source_publique(), "demandeur", e[6])
    assert _retenus(base) == set()


# ── Personnes : jamais sans rôle civique ─────────────────────────────────────

def test_personne_sans_role_civique_rejetee(bps):
    publiee, motifs = bps.public_entity(
        fiche(type="person", name="Un particulier"), [], set())
    assert publiee is None
    assert motifs == ["person_without_public_civic_role"]


def test_personne_avec_role_civique_publiee(bps):
    publiee, _ = bps.public_entity(
        fiche(id=7, type="person", name="Une élue"), [], {7})
    assert publiee is not None


# ── Périmètre ────────────────────────────────────────────────────────────────

def test_entite_non_classee_rejetee(bps):
    publiee, motifs = bps.public_entity(fiche(perimetre=None), [], set())
    assert publiee is None
    assert motifs == ["hors_fiche_perimetre_None"]


def test_commerce_de_commune_voisine_rejete(bps):
    publiee, motifs = bps.public_entity(fiche(perimetre="C2"), [], set())
    assert publiee is None
    assert "perimetre" in motifs[0]


def test_institution_intercommunale_publiee(bps):
    publiee, _ = bps.public_entity(
        fiche(type="service", name="CC des Épreuves Réunies", perimetre="C2"),
        [], set())
    assert publiee is not None


def test_elu_communautaire_publie(bps):
    """Il vote le budget qui s'applique à la commune : le masquer amputerait la
    chaîne de décision de sa moitié intercommunale."""
    publiee, _ = bps.public_entity(
        fiche(id=9, type="person", name="Un délégué", perimetre="C2"),
        [], {9}, ids_conseil_communautaire={9})
    assert publiee is not None


# ── Flux financiers : un montant ne sort pas sans ses deux extrémités ────────
# Les relations avaient cette règle (`endpoint_not_public`) depuis toujours, les
# flux ne l'ont jamais eue : le filtre voisin ne regardait que les personnes
# physiques. Un flux vers une association d'une commune limitrophe sortait donc
# avec un lien vers une fiche que le snapshot n'écrit pas, et le build du site
# s'arrêtait sur `404 /entite/<id> (linked from /finances)` — l'instance entière
# impubliable à cause de trois associations.

def test_flux_vers_une_entite_publiee_passe(bps):
    assert bps.flux_extremites_publiees({"from_id": 1, "to_id": 2}, {1, 2})


def test_flux_vers_une_entite_non_publiee_ecarte(bps):
    assert not bps.flux_extremites_publiees({"from_id": 1, "to_id": 8405}, {1})


def test_flux_depuis_une_entite_non_publiee_ecarte(bps):
    """Les deux sens comptent : une subvention REÇUE d'une entité écartée
    renverrait vers la même fiche absente."""
    assert not bps.flux_extremites_publiees({"from_id": 8405, "to_id": 1}, {1})


def test_une_subvention_a_une_association_voisine_est_deliee(bps):
    """Le budget d'ICI paie une association dont le siège est à côté : c'est un
    fait communal. L'écarter parce que le bénéficiaire n'a pas de fiche cachait
    15 lignes sur 44 à Saillans, 21 à Lasalle."""
    flux = {"from_id": 1, "to_id": 8405, "to_name": "Basket Club de la voisine"}
    assert bps.statut_extremites(flux, {1}, {8405}) == "delie"
    bps.delier_extremites(flux, {1})
    assert flux["to_id"] is None
    assert flux["to_name"] == "Basket Club de la voisine"      # le nom reste


def test_un_flux_vers_une_fiche_privee_reste_ecarte(bps):
    """Une personne physique sans rôle civique, une entité en `probable` : elles
    ne sont pas écartées par le périmètre mais par ce qu'elles sont. Rien n'en
    sort, pas même un nom."""
    assert bps.statut_extremites({"from_id": 1, "to_id": 8405}, {1}, set()) == "ecarte"


def test_un_flux_entre_deux_entites_sans_fiche_est_ecarte(bps):
    """Délier suppose qu'une extrémité au moins soit publiée : sinon le flux ne
    dit rien de la commune."""
    assert bps.statut_extremites({"from_id": 900, "to_id": 8405},
                                 {1}, {900, 8405}) == "ecarte"


def test_un_flux_entre_deux_fiches_publiees_garde_ses_liens(bps):
    assert bps.statut_extremites({"from_id": 1, "to_id": 2}, {1, 2}, set()) == "garde"


def test_deux_beneficiaires_delies_de_meme_montant_restent_deux(bps):
    """RASED et Association Brunouret, 100 € chacune la même année : sans fiche,
    plus d'identifiant pour les distinguer — c'est leur NOM qui le fait."""
    flux = [{"year": 2023, "amount": 100, "type": "subvention", "from_id": 1,
             "to_id": None, "to_name": "RASED"},
            {"year": 2023, "amount": 100, "type": "subvention", "from_id": 1,
             "to_id": None, "to_name": "Association Brunouret"}]
    assert len(bps.dedupliquer_flux(flux)) == 2


def test_un_flux_delie_nest_pas_une_ligne_anonyme(bps):
    """La règle des jumeaux vise ce qu'un collecteur n'a pas su rattacher. Un
    flux délié porte un nom : il n'est pas de ceux-là. Sans cette distinction,
    9 des 15 subventions rendues au public repartaient aussitôt."""
    flux = [{"year": 2022, "amount": 2000, "type": "subvention", "from_id": 1,
             "to_id": 7, "to_name": "Association Brunandin"},
            {"year": 2022, "amount": 2000, "type": "subvention", "from_id": 1,
             "to_id": None, "to_name": "Association Abric"}]
    assert len(bps.dedupliquer_flux(flux)) == 2


def test_un_jumeau_sans_nom_disparait_toujours(bps):
    flux = [{"year": 2022, "amount": 500, "type": "subvention", "from_id": 1,
             "to_id": 7, "to_name": "Le Forum"},
            {"year": 2022, "amount": 500, "type": "subvention", "from_id": 1,
             "to_id": None, "to_name": ""}]
    assert [f["to_name"] for f in bps.dedupliquer_flux(flux)] == ["Le Forum"]


def test_un_doublon_exact_reste_un_doublon(bps):
    ligne = {"year": 2022, "amount": 500, "type": "subvention", "from_id": 1,
             "to_id": 7, "to_name": "Le Forum"}
    assert len(bps.dedupliquer_flux([ligne, dict(ligne)])) == 1


def test_flux_sans_beneficiaire_identifie_reste(bps):
    """Une extrémité vide ne prétend renvoyer nulle part : le flux se publie,
    sans lien. L'écarter effacerait de l'argent public au motif que le
    collecteur n'a pas su nommer qui l'a touché."""
    assert bps.flux_extremites_publiees({"from_id": 1, "to_id": None}, {1})


# ── Marchés : le lien tombe, la pièce reste ──────────────────────────────────
# Un flux est écarté quand son extrémité n'a pas de fiche ; un marché non. La
# pièce décrit un achat de la collectivité, et le nom du titulaire est public —
# seul le renvoi vers une fiche absente est coupé. Saillans, 23/08/2026 : deux
# titulaires C2 d'un marché intercommunal refusaient le snapshot entier.

SANS_EI = {"ei_ids": frozenset(), "sirens_ei_ecartees": frozenset(),
           "noms_ei_ecartees": frozenset(), "ei_nommees": frozenset(), "commune_id": None}


def test_titulaire_sans_fiche_perd_son_lien_pas_son_nom(bps):
    marches = [{"id": 1, "acheteur_id": 5, "titulaire_id": 19017,
                "titulaire_nom": "SOCIÉTÉ CALAUDIN"}]
    assert bps.delier_renvois_morts(marches, {5}, **SANS_EI) == 1
    assert marches[0]["titulaire_id"] is None
    assert marches[0]["acheteur_id"] == 5
    assert marches[0]["titulaire_nom"] == "SOCIÉTÉ CALAUDIN"


def test_acheteur_sans_fiche_perd_son_lien_aussi(bps):
    marches = [{"id": 1, "acheteur_id": 8405, "titulaire_id": 2}]
    assert bps.delier_renvois_morts(marches, {2}, **SANS_EI) == 1
    assert marches[0]["acheteur_id"] is None


def test_marche_entierement_publie_est_laisse_intact(bps):
    marches = [{"id": 1, "acheteur_id": 5, "titulaire_id": 2}]
    assert bps.delier_renvois_morts(marches, {2, 5}, **SANS_EI) == 0
    assert (marches[0]["acheteur_id"], marches[0]["titulaire_id"]) == (5, 2)


def test_un_renvoi_absent_nest_pas_un_renvoi_mort(bps):
    """`NULL` ne prétend renvoyer nulle part : rien à couper, rien à compter —
    sinon le décompte des exclusions gonflerait de marchés que personne n'a
    déliés."""
    marches = [{"id": 1, "acheteur_id": 5, "titulaire_id": None}]
    assert bps.delier_renvois_morts(marches, {5}, **SANS_EI) == 0


# ── L'ordre des règles est lui-même une garantie ─────────────────────────────

def test_une_personne_privee_c1_reste_privee(bps):
    """Le bon périmètre n'ouvre aucun droit : les règles s'additionnent."""
    publiee, _ = bps.public_entity(
        fiche(type="person", name="Un habitant", perimetre="C1"), [], set())
    assert publiee is None


def test_une_piste_c1_reste_privee(bps):
    publiee, _ = bps.public_entity(
        fiche(perimetre="C1", confidence="hypothesis"), [], set())
    assert publiee is None


# ── Le bénéficiaire d'une aide n'est pas un élu ──────────────────────────────
# Ajouté le 21/08/2026, en ouvrant la saisie manuelle et l'extraction assistée.
# Le modèle a sorti d'un vrai PV, correctement, des aides à la rénovation de
# façades NOMINATIVES : « Mme TARIGNAC Céleste », « M. et Mme LORAUDIN », avec le
# numéro de rue. Ce sont des particuliers qui reçoivent de l'argent public — la
# collecte les prend (doctrine de l'atelier), la publication ne doit pas.
#
# Le risque est précis : saisir un flux crée une RELATION vers le bénéficiaire,
# et une relation est justement ce qui rend une personne publiable. Le 19/08,
# 1 229 fiches d'entités écartées dont 80 personnes physiques ont été servies en
# production. Ces trois tests ferment la porte par le code, pas par l'attention.

def test_beneficiaire_dune_aide_nest_pas_publiable(bps):
    """Recevoir de l'argent public n'entre PAS une personne dans l'ensemble
    civique : sa fiche est refusée, et pour ce motif précis."""
    publiee, motifs = bps.public_entity(
        fiche(id=42, type="person", name="Une bénéficiaire", perimetre="C1"),
        [], set())
    assert publiee is None
    assert motifs == ["person_without_public_civic_role"]


def test_seuls_les_roles_civiques_ouvrent_la_publication(bps):
    """Contrôle croisé sur les règles elles-mêmes : si un type de relation
    « économique » entrait un jour dans la liste civique, toute personne payée
    par la commune deviendrait publiable d'un coup, et silencieusement."""
    import json
    regles = json.loads(
        (ROOT / "config" / "publication_rules.exemple.json").read_text(encoding="utf-8"))
    civiques = set(regles["people"]["publish_only_with_relation_types"])
    jamais = {"subventionné", "locataire_commune", "bail", "cession",
              "prestataire", "titulaire", "acheteur"}
    assert not (civiques & jamais), (
        f"rôles non civiques dans la liste de publication : {civiques & jamais}")


def test_un_elu_reste_publiable(bps):
    """Le contre-test : fermer la porte ne doit pas fermer la maison. Une
    personne entrée dans l'ensemble civique en amont reste publiée."""
    publiee, _ = bps.public_entity(
        fiche(id=7, type="person", name="Une élue", perimetre="C1"), [], {7})
    assert publiee is not None



def _relier(base, de, vers, type_, confiance="confirmed"):
    base.execute("INSERT INTO relations (from_id, to_id, relation_type, confidence)"
                 " VALUES (?,?,?,?)", (de, vers, type_, confiance))
    base.commit()


def test_sieger_en_commission_ne_rend_pas_publique(base, entite):
    """Avis DPO du 14/11/2026 : un membre de commission n'est nommé qu'au titre
    de son mandat. Un habitant lu dans un PV ou sur la page d'une commission
    extramunicipale n'a ni fiche, ni nom en clair dans les textes, même quand
    l'atelier a confirmé son siège."""
    from scripts.snapshot.personnes import etape_personnes_publiques
    commission = entite("Commission extramunicipale d'épreuve", type_="service")
    habitant = entite("Camille Habitante", type_="person")
    _relier(base, habitant, commission, "membre_commission")
    _relier(base, entite("Dominique Voisine", type_="person"), commission,
            "membre_commission", confiance="probable")

    sortie = etape_personnes_publiques(base, "[]")

    assert habitant not in sortie["public_person_ids"]
    assert not {"CAMILLE HABITANTE", "DOMINIQUE VOISINE"} & sortie["noms_publics"]


def test_les_regles_d_une_instance_ne_rouvrent_pas_la_commission(tmp_path):
    """Les `publication_rules.json` des instances ne suivent pas le dépôt :
    `membre_commission` y figure encore dans la liste civique. Le chargement
    l'en retire, pour tout lecteur de `RULES`."""
    import json
    from scripts.snapshot.socle import load_rules
    regles = json.loads(
        (ROOT / "config" / "publication_rules.exemple.json").read_text(encoding="utf-8"))
    regles["people"]["publish_only_with_relation_types"].append("membre_commission")
    chemin = tmp_path / "publication_rules.json"
    chemin.write_text(json.dumps(regles), encoding="utf-8")
    roles = load_rules(chemin)["people"]["publish_only_with_relation_types"]
    assert "membre_commission" not in roles
    assert "élu_cm" in roles


def test_un_elu_membre_de_commission_reste_publie(base, entite):
    """Le contre-test : c'est le mandat qui ouvre la fiche, la commission ne la
    ferme pas."""
    from scripts.snapshot.personnes import etape_personnes_publiques
    mairie = entite("Mairie d'épreuve", type_="service")
    commission = entite("Commission des finances d'épreuve", type_="service")
    elue = entite("Sacha Conseillère", type_="person")
    _relier(base, elue, mairie, "élu_cm", confiance="verified")
    _relier(base, elue, commission, "membre_commission")

    sortie = etape_personnes_publiques(base, "[]")

    assert elue in sortie["public_person_ids"]
    assert "SACHA CONSEILLÈRE" in sortie["noms_publics"]

# ── L'allowlist des sources ──────────────────────────────────────────────────

def test_toute_source_de_marche_est_publiable():
    """Une source qu'on collecte et que l'allowlist ignore est une source morte.

    `events.public_sources` compare à l'IDENTIQUE, et elle a longtemps été
    fabriquée à partir des DOMAINES de l'instance plus quelques mots-clés —
    « BOAMP », « bodacc », « sitadel », « interieur ». Les trois libellés DECP
    n'y figuraient sur aucune instance : 53 marchés collectés sur l'une d'elles,
    aucun publié, et rien ne le signalait. C'est la source la plus sûre du lot,
    la seule qui recoupe par SIREN d'acheteur.

    Ce test porte sur les règles D'EXEMPLE, celles que reçoit toute nouvelle
    instance : c'est là que le défaut naissait.
    """
    import json

    from collectors.marches_publics import SOURCES

    regles = json.loads(
        (ROOT / "config" / "publication_rules.exemple.json").read_text(encoding="utf-8"))
    publiables = set(regles["events"]["public_sources"])

    absentes = [s for s in SOURCES if s not in publiables]
    assert not absentes, (
        f"sources collectées mais non publiables : {absentes}. "
        "Le collecteur écrit ces libellés dans events.source ; l'allowlist les "
        "compare à l'identique.")


def test_toute_source_d_evenement_est_publiable():
    """Le même défaut, généralisé : ce qui écrit un `event` doit être publiable.

    `ccomptes` a été ajouté aux trois instances le 01/09 et OUBLIÉ dans les
    règles d'exemple : une instance amorcée depuis ce fichier collectait les
    rapports de chambre régionale des comptes et ne les publiait jamais. Le
    défaut ne se voit pas — la collecte réussit, la page reste vide.

    Ce test lit les constantes `SOURCE` des collecteurs qui écrivent dans
    `events`, et exige qu'elles figurent dans l'allowlist d'exemple. Ajouter un
    collecteur d'événements sans l'y déclarer fait donc échouer la suite, au
    lieu de produire une source morte.
    """
    import json

    from collectors import crc, plu

    regles = json.loads(
        (ROOT / "config" / "publication_rules.exemple.json").read_text(encoding="utf-8"))
    publiables = set(regles["events"]["public_sources"])

    # Les collecteurs qui écrivent un `event` avec leur propre libellé de source.
    sources = {"crc": crc.SOURCE, "plu": plu.SOURCE}
    absentes = {nom: s for nom, s in sources.items() if s not in publiables}
    assert not absentes, (
        f"collecteurs dont la source n'est pas publiable : {absentes}. "
        "L'événement sera collecté et jamais publié, sans que rien ne le dise.")


# ── Les fossiles de la base permanente des équipements ───────────────────────

def test_les_lignes_bpe_fossiles_ne_sont_plus_publiees(bps, tmp_path):
    """La BPE a déménagé de `insee_social` vers `equipements`, avec sa
    nomenclature. Cesser de la collecter n'a pas effacé ce qui était déjà en
    base : 502 lignes sur Lasalle, 97 % sans libellé, publiées à côté des
    lignes propres. Les mêmes faits deux fois, dont une illisible.

    L'essai porte sur la REQUÊTE de publication, pas sur son texte : il la joue
    contre une base et compte ce qui sort.
    """
    import sqlite3

    base = tmp_path / "essai.db"
    conn = sqlite3.connect(base)
    conn.execute("""CREATE TABLE insee_indicateurs (
        insee TEXT, commune TEXT, dataset TEXT, indicateur TEXT,
        libelle TEXT, annee TEXT, valeur REAL, dims TEXT)""")
    conn.executemany(
        "INSERT INTO insee_indicateurs VALUES (?,?,?,?,?,?,?,?)", [
            ("30140", "Lasalle", "DS_BPE", "BPE_A104", None, "2025", 3.0, None),
            ("30140", "Lasalle", "DS_BPE", "BPE_A129", None, "2025", 1.0, None),
            ("30140", "Lasalle", "DS_RP_POPULATION", "POP", "Population",
             "2022", 1080.0, None),
        ])
    conn.commit()
    conn.row_factory = sqlite3.Row

    sortis = [dict(r) for r in conn.execute(bps.INSEE_PUBLIABLES)]
    assert [r["indicateur"] for r in sortis] == ["POP"]
    assert all(r["dataset"] != "DS_BPE" for r in sortis)


# ── Le caviardage des textes ──────────────────────────────────────────────────
# Les extraits publient le TEXTE d'une délibération, pas seulement son titre :
# le caviardage y rencontre les listes de présents, les prises de parole et les
# votes — des élus, en exercice ou non.

def _personne(base, entite, prenom: str, nom: str, fiche: str | None = None) -> int:
    pid = entite(fiche or f"{prenom} {nom}", type_="person")
    base.execute("INSERT INTO persons (entity_id, firstname, lastname) VALUES (?,?,?)",
                 (pid, prenom, nom))
    base.commit()
    return pid


def test_qui_a_siege_nest_pas_caviarde_dans_les_actes(bps, base, entite):
    """🔴 Un mandat clos reste un mandat.

    `ids_publics` ne connaît que les élus en exercice. Dans les extraits, les
    conseillers des mandats précédents devenaient « un particulier » à chaque
    prise de parole — l'un d'eux 1 370 fois sur une seule instance.
    """
    ancien = _personne(base, entite, "Alix", "SALESQUE")
    seance = base.execute(
        "INSERT INTO events (type, date, title) VALUES "
        "('conseil_municipal', '2019-04-10', 'Conseil municipal du 10 avril 2019')").lastrowid
    base.execute("INSERT INTO event_entities (event_id, entity_id, role) "
                 "VALUES (?, ?, 'présent')", (seance, ancien))
    _personne(base, entite, "Jordan", "PELAGNOL")
    base.commit()

    redige, _ = bps.compilateur_redaction(base, set())
    assert (redige("M. SALESQUE : je propose la subvention à Jordan PELAGNOL.")
            == "M. SALESQUE : je propose la subvention à un particulier.")


def test_le_nom_dun_elu_partage_avec_un_particulier_ne_masque_pas_lelu(bps, base, entite):
    """« Secrétaire de séance : un particulier » partait en ligne.

    Le maire en exercice a des homonymes privés dans la base, et la forme
    « civilité + patronyme » de l'un d'eux le remplaçait dans chaque liste de
    présents. Une forme partagée avec un élu est ambiguë : elle n'est pas
    masquée. Le nom complet du particulier, lui, l'est toujours.
    """
    maire = _personne(base, entite, "Camille", "GALAVEL")
    _personne(base, entite, "Noa", "GALAVEL")
    _personne(base, entite, "Camille", "GALAVEL", fiche="GALAVEL Camille")  # seconde fiche

    redige, _ = bps.compilateur_redaction(base, {maire})
    assert redige("Secrétaire de séance : M. GALAVEL") == "Secrétaire de séance : M. GALAVEL"
    assert redige("Présents : Camille GALAVEL, maire") == "Présents : Camille GALAVEL, maire"
    assert redige("Gérant : Noa GALAVEL") == "Gérant : un particulier"


def test_un_mandat_tenu_rend_nommable_sans_presence_liee(bps, base, entite):
    """Un ancien maire présidait la séance : « Sous la présidence de Monsieur un
    particulier, Maire ». Sa présence n'était liée à aucune séance, mais la base
    portait son mandat intercommunal."""
    ancien = _personne(base, entite, "Sasha", "NEVOMBE")
    epci = entite("Communauté de communes de Fictiville", type_="service")
    base.execute("INSERT INTO relations (from_id, to_id, relation_type) "
                 "VALUES (?, ?, 'vice_président_cc')", (ancien, epci))
    base.commit()

    redige, _ = bps.compilateur_redaction(base, set())
    texte = "Sous la présidence de Monsieur Sasha NEVOMBE, Maire"
    assert redige(texte) == texte



def test_un_prenom_nest_pas_un_patronyme(bps, base, entite):
    """« M. Jordan LOROMBE » devenait « un particulier GALAVEL » : un particulier
    de la base s'appelle SACHA, et la comparaison ignore la casse."""
    _personne(base, entite, "Lou", "SACHA")

    redige, _ = bps.compilateur_redaction(base, set())
    assert redige("DONNE pouvoir à M. Jordan LOROMBE") == "DONNE pouvoir à M. Jordan LOROMBE"
    assert redige("Aide à M. SACHA.") == "Aide à un particulier."


def test_un_acte_cite_ses_particuliers_mais_masque_naissance_et_domicile(bps):
    """⚖️ Arbitré le 16/09/2026 : un particulier cité dans un acte officiel est
    cité. Ce qui situe ou date une personne ne sort pas — domicile, date et lieu
    de naissance —, et l'âge d'une personne publique reste : il a du sens."""
    from collections import Counter
    publics = {"JORDAN ZANIMET"}
    masque = lambda t, jour="2021-05-20": bps.masquer_donnees_personnelles(t, jour, publics, Counter())

    # Une personne publique : la naissance devient l'âge à la date de l'acte.
    assert masque("Délégué : Jordan ZANIMET né le 05/03/1961 ;") == "Délégué : Jordan ZANIMET âgé de 60 ans ;"
    # Un particulier, un agent : cité, mais sa naissance est masquée.
    assert (masque("Madame Noa CALORLY née le 5 octobre 1961 est nommée agent")
            == "Madame Noa CALORLY née le [date masquée] est nommée agent")
    assert (masque("à Monsieur Maxime QUILIGNAC et né le 05/03/1961 à Testonville PRÉCISE que")
            == "à Monsieur Maxime QUILIGNAC et né le [date et lieu masqués] PRÉCISE que")
    # Le domicile, sous les formes relevées dans les actes.
    assert (masque("courrier de Monsieur VORLANDIN, demeurant au 12 Grand Rue à Testville, dans lequel")
            == "courrier de Monsieur VORLANDIN, demeurant [domicile masqué], dans lequel")
    assert (masque("Mme CALORLY Dominique domiciliée 3, rue\ndes Lilas. Mme CALORLY souhaite")
            == "Mme CALORLY Dominique domiciliée [domicile masqué]. Mme CALORLY souhaite")
    assert (masque("M. Maxime QUILIGNAC, résidant à l'adresse « Domaine des Chênes – 8 Impasse des Merles » "
                   "à Fictiville (99 000) et")
            == "M. Maxime QUILIGNAC, résidant [domicile masqué] et")
    assert (masque("de M. et Mme CALORLY Lou domiciliés à Mas Fictif\ncommune de Fictiville")
            == "de M. et Mme CALORLY Lou domiciliés [domicile masqué]\ncommune de Fictiville")
    assert masque("Référent : r.velirand@fictiville.invalid ;") == "Référent : [courriel masqué] ;"
    assert (masque("Adjoint : Lou CALORLY — paul.dupont@test.fr — 06 39 98 53 25")
            == "Adjoint : Lou CALORLY — [courriel masqué] — [téléphone masqué]")
    assert (masque("M. Maxime QUILIGNAC, résidant à l'adresse « 8 Impasse des Merles » à Fictiville (99\n000) et")
            == "M. Maxime QUILIGNAC, résidant [domicile masqué] et")
    # Ce qui n'est pas une donnée personnelle ne bouge pas — et le nom reste.
    for intact in ("L'association Fictiville Enduro domiciliée à Viane souhaite organiser",
                   "les familles domiciliées sur la commune depuis six mois",
                   "Présidente (contact-04a7f5@fictiville.invalid / 0199003112)",
                   "Tiers-lieu, 12 rue Haute\nContact 01 99 00 31 12 - 01 99 00 12 73",
                   "Aide façade — M. Maxime QUILIGNAC, 300 €",
                   "il me faut la date de naissance des personnes proposées"):
        assert masque(intact) == intact


def test_le_titre_dun_acte_designe_un_particulier_par_son_role(bps):
    """⚖️ Règle du 27/10/2026 : le TITRE d'un acte ne nomme qu'une personne
    publique. Un particulier y devient « un particulier », un agent « un agent » ;
    la décision, le montant et le reste du titre demeurent. Un titre réduit à un
    nom n'est pas un acte."""
    from collections import Counter
    publics = {"JORDAN ZANIMET", "ZANIMET JORDAN"}
    titre = lambda t: bps.titre_d_acte(t, "2026-04-20", publics, Counter())

    assert titre("Aide façade — M. Maxime QUILIGNAC, 300 €") == ("Aide façade — un particulier, 300 €", None)
    assert (titre("Avis conforme changement de destination PC 030 140 25 H0005 de Mme CALORLY Noa")
            == ("Avis conforme changement de destination [numéro retiré] d'un particulier", None))
    assert (titre("Avenant à la mise à disposition de Mme Noa CALORLY")
            == ("Avenant à la mise à disposition d'un agent", None))
    assert titre("Vente d'un terrain à M. et Mme VORLANDIN") == ("Vente d'un terrain à un particulier", None)
    # Une personne publique reste nommée, et sur un doute aussi.
    for intact in ("Désignation de M. Jordan ZANIMET au SIVOM",
                   "Délégation à M. ZANIMET",
                   "Indemnités de Monsieur le Maire",
                   "Motion présentée par Madame la Présidente"):
        assert titre(intact) == (intact, None)
    # Ce qui n'est pas un titre d'acte.
    assert titre("| M. Maxime QUILIGNAC") == (None, "titre_fragment")
    assert titre("M. Maxime QUILIGNAC") == (None, "titre_reduit_a_un_nom")
    assert titre("AR CONTROLE DE LEGALITE 030-213001407") == (None, "titre_fragment")
    # Le domicile est masqué dans un titre comme dans un texte.
    assert titre("Aide à M. VORLANDIN, demeurant au 12 Grand Rue à Testville")[0] == \
        "Aide à un particulier, demeurant [domicile masqué]"


def test_une_adresse_sans_verbe_et_un_portable_nominatif_sont_masques(bps):
    """Relevé le 12/10/2026 sur les extraits publiés d'une instance : des aides
    façade écrivent « Mme FENAUDIN Lou, 14 rue des Tilleuls à Fictiville » — sans
    « domicilié » —, et une fiche de contacts donnait portable et courriel d'agents
    et d'une conseillère. Le nom reste (arbitré le 16/09) ; ce qui situe ou joint
    la personne ne sort pas."""
    from collections import Counter
    masque = lambda t: bps.masquer_donnees_personnelles(t, "2021-05-20", set(), Counter())

    assert (masque("accorder à Mme FENAUDIN Lou, 14 rue des Tilleuls à Fictiville, la somme de 350 €")
            == "accorder à Mme FENAUDIN Lou, [domicile masqué], la somme de 350 €")
    assert (masque("Monsieur de MARELLAC Sacha, 41 rue du Puits de Fictiville à FICTIVILLE, qui sollicite")
            == "Monsieur de MARELLAC Sacha, [domicile masqué], qui sollicite")
    assert (masque("Mme TARIGNAC Céleste – 27 Rue des Écoles : Monsieur le Maire fait part")
            == "Mme TARIGNAC Céleste, [domicile masqué] : Monsieur le Maire fait part")
    # Une initiale suffit pour un courriel nominatif ; un portable qui suit un nom
    # est celui de la personne, et son courriel avec lui.
    assert (masque("- Mme Ariel NEVANDIN, Cheffe de projet 06 39 98 01 43 d.galombere@fictiville.invalid = M.")
            == "- Mme Ariel NEVANDIN, Cheffe de projet [téléphone masqué] [courriel masqué] = M.")
    assert (masque("Alix FENANQUET, Conseillère municipale 06.39.98.65.10 a.fenanquet@fictiville.invalid Pour")
            == "Alix FENANQUET, Conseillère municipale [téléphone masqué] [courriel masqué] Pour")
    # Ce qui est un service ne bouge pas.
    for intact in ("Fax : 01 99 00 58 41 contact-c09804@fictiville.invalid Fax : 01 99 00 12 93",
                   "Tiers-lieu, 12 rue Haute\nContact 01 99 00 31 12 - 01 99 00 12 73",
                   "La Cure » 15 rue Nevorly 30460 Lasalle Contact 01 99 00 57 93 - 01 99 00 06 60",
                   "Aide façade — M. Maxime QUILIGNAC, 300 €"):
        assert masque(intact) == intact


def _pieges() -> list[tuple[str, list[str]]]:
    lignes = (ROOT / "tests" / "fixtures" / "pieges_textes.txt").read_text(encoding="utf-8").splitlines()
    pieges = []
    for ligne in lignes:
        if not ligne.strip() or ligne.startswith("#"):
            continue
        phrase, interdits = ligne.split(" | ")
        pieges.append((phrase.replace("\\n", "\n"), [f.strip() for f in interdits.split(";")]))
    return pieges


@pytest.mark.parametrize("phrase,interdits", _pieges())
def test_aucun_piege_ne_sort(bps, phrase, interdits):
    """🔴 tests/fixtures/pieges_textes.txt : chaque phrase est masquée ou refusée.

    Relevé par une revue externe le 05/10/2026 : le masque ne connaissait que
    « né le DATE à LIEU », s'arrêtait à la première virgule d'une adresse, et le
    filet cherchait les mêmes formes que lui."""
    from collections import Counter
    sortie, refus = bps.texte_publiable(phrase, "2021-05-20", set(), Counter())
    assert refus or not [f for f in interdits if f in sortie], sortie


def test_les_formes_elargies_masquent_sans_refuser_ni_deborder(bps):
    """Le masque fait le travail, le filet n'est qu'un filet : les formes
    courantes sortent masquées, et ce qui n'est la naissance ou le domicile de
    personne ne bouge pas."""
    from collections import Counter
    publiable = lambda t, publics=frozenset(): bps.texte_publiable(t, "2021-05-20", set(publics), Counter())

    assert publiable("M. Lou CALORLY, né à Testonville le 5 mars 1961, a demandé") == (
        "M. Lou CALORLY, né le [date et lieu masqués], a demandé", None)
    assert publiable("Délégué : Jordan ZANIMET né à Testonville le 05/03/1961 ;", {"JORDAN ZANIMET"}) == (
        "Délégué : Jordan ZANIMET âgé de 60 ans ;", None)
    assert publiable("Date de naissance : 05/03/1961") == ("Date de naissance : [date masquée]", None)
    assert publiable("Monsieur Lou CALORLY, né en 1961, retraité") == (
        "Monsieur Lou CALORLY, né en [année masquée], retraité", None)
    assert publiable("Mme Lou SALOMBE, demeurant à Testonville, 5 rue des Lilas, sollicite") == (
        "Mme Lou SALOMBE, demeurant [domicile masqué], sollicite", None)
    assert publiable("M. Lou CALORLY, domicilié au lieu-dit Mas de la Combe, demande") == (
        "M. Lou CALORLY, domicilié [domicile masqué], demande", None)
    assert publiable("Madame Noa CALORLY née le 5 octobre 1961 est nommée agent à compter du 1er janvier 2022") == (
        "Madame Noa CALORLY née le [date masquée] est nommée agent à compter du 1er janvier 2022", None)

    for intact in ("Le projet est né à Lasalle d'une rencontre",
                   "La communauté de communes, née en 2017 de la fusion de trois intercommunalités",
                   "L'association domiciliée au lieu-dit Mas de la Combe organise",
                   "La commune ne le fera qu'après le 12 mars 2022",
                   "Monsieur le Maire propose d'acquérir la parcelle située 12 rue Haute",
                   "les familles domiciliées sur la commune depuis six mois"):
        assert publiable(intact) == (intact, None)


def test_ladresse_du_bien_aide_est_masquee_le_reste_du_texte_reste(bps):
    """🔴 Audit du 14/11/2026 : « pour le 220 route vieille », « façades du 18 rue
    de la Fontfictive » sortaient en clair, sans verbe de domicile ni civilité
    devant. Le nom, la décision et le montant restent (arbitrage du 14/11)."""
    from collections import Counter
    publiable = lambda t: bps.texte_publiable(t, "2025-01-20", set(), Counter())

    assert publiable(
        "demande de M. et Mme Lou SALOMBE, qui sollicitent une aide à la rénovation de façades "
        "pour le 220 route vieille dont le montant pourrait s'élever à 479 €, en vertu de la "
        "délibération du 10/04/2019.\n- DECIDE d'octroyer la somme de 479 € dans le cadre de "
        "l'aide à la rénovation des façades du 220 route vieille, aide régie par délibération du "
        "10 avril 2019.\n- DIT que cette somme sera inscrite à l'article 65741 du Budget Primitif 2025") == (
        "demande de M. et Mme Lou SALOMBE, qui sollicitent une aide à la rénovation de façades "
        "pour le [adresse masquée] dont le montant pourrait s'élever à 479 €, en vertu de la "
        "délibération du 10/04/2019.\n- DECIDE d'octroyer la somme de 479 € dans le cadre de "
        "l'aide à la rénovation des façades du [adresse masquée], aide régie par délibération du "
        "10 avril 2019.\n- DIT que cette somme sera inscrite à l'article 65741 du Budget Primitif 2025", None)
    # L'océrisation coupe l'adresse ; un mois en capitale n'est pas une voie.
    assert publiable(
        "aide financière du 15 Novembre 2023 pour la rénovation de la façade de Mme Lou SALOMBE, "
        "18 rue de la\nFontfictive.\nfacture de 1 560 € HT") == (
        "aide financière du 15 Novembre 2023 pour la rénovation de la façade de Mme Lou SALOMBE, "
        "[adresse masquée].\nfacture de 1 560 € HT", None)
    # Le déclencheur ne dépend ni du mot « aide », ni de la phrase, ni de la ligne
    # (relecture sécurité du 14/11) ; et l'adresse s'écrit de bien des façons.
    for phrase, interdits in (
        ("Subvention pour le ravalement de l'immeuble du 12 rue des Lilas, propriété de M. Paul DUPONT", ["12 rue", "Lilas"]),
        ("Subvention de 2 000 € à M. Paul DUPONT pour la réfection de la toiture du 12 rue des Lilas", ["12 rue", "Lilas"]),
        ("Opération façades : participation communale de 1 500 € pour le 12 rue des Lilas", ["12 rue", "Lilas"]),
        ("Attribution d'une aide\npour la façade du 12 rue des Lilas", ["12 rue", "Lilas"]),
        ("Aide communale. M. Paul DUPONT a déposé un dossier pour sa façade, 12 rue des Lilas", ["12 rue", "Lilas"]),
        ("façade du 12 bd des Lilas", ["12 bd", "Lilas"]),
        ("façade du 12 av. Jean Jaurès", ["12 av", "Jaurès"]),
        ("façade du 12B rue des Lilas", ["12B", "Lilas"]),
        ("façade de l'immeuble sis au n°12 de la rue des Lilas", ["n°12", "Lilas"]),
        ("façade de l'immeuble rue des Lilas n° 12", ["Lilas"]),
        ("façade de la maison de M. Paul DUPONT au lieu-dit Les Fontanilles", ["Fontanilles"]),
        ("façade de l'immeuble cadastré section AB n° 123", ["AB n° 123"]),
        ("façade du 12 rue de l'Abbaye", ["12 rue", "Abbaye"]),
        ("façade du 3 rue du 8 Mai 1945, propriété de M. Paul DUPONT", ["3 rue", "8 Mai"]),
    ):
        sortie, refus = publiable(phrase)
        assert refus or not [f for f in interdits if f in sortie], sortie
    # Ce qui n'est pas une adresse ne bouge pas, même dans un texte de façade.
    assert publiable("aide à la rénovation des façades du 7 chemin des Essais dont le montant est de 300 €") == (
        "aide à la rénovation des façades du [adresse masquée] dont le montant est de 300 €", None)
    # 🔴 Le masque ne touche jamais au nombre qui suit : un montant cité reste juste.
    assert publiable("Façade de l'immeuble cadastré section AB n° 123, 1 500 € accordés")[0] == \
        "Façade de l'immeuble cadastré [adresse masquée], 1 500 € accordés"
    for avant, apres in (
        ("Façade : DUPONT Paul – 9 rue des Essais 1 200 €", "Façade : DUPONT Paul – [adresse masquée] 1 200 €"),
        ("Façade : 4 impasse Fictive 2 500 €", "Façade : [adresse masquée] 2 500 €"),
        ("Façade : 12 rue de la Gare du 12 mars 2024", "Façade : [adresse masquée] du 12 mars 2024"),
        ("Façade du 3 rue du 19 Mars 1962 pour 1 500 €", "Façade du [adresse masquée] pour 1 500 €"),
    ):
        assert publiable(avant) == (apres, None), avant
    for intact in ("Façades : travaux rue de la Gare, 3 lots attribués",
                   "Façades : réfection du chemin de Ronde, 15 voix pour",
                   "Aide aux façades. Aménagement de la 2 route départementale 999",
                   "Aide façade plafonnée au 30 Pour cent du montant, soit 1 500 Euros, votée par 12 voix",
                   "Opération façades et assainissement du hameau de Valfictif : 12 000 € HT",
                   "Aide façade : séance du 12 Conseil municipal, le 15 Novembre 2023, article 65741"):
        assert publiable(intact) == (intact, None)
    # Hors d'une aide au bien, l'adresse d'une parcelle ou d'un bâtiment reste.
    for intact in ("Monsieur le Maire propose d'acquérir la parcelle située 12 rue Haute",
                   "Travaux de toiture de l'école, 3 place de la Mairie, pour 12 000 € HT"):
        assert publiable(intact) == (intact, None)


def test_un_acte_sur_la_situation_dune_personne_ne_publie_pas_son_texte(bps):
    """Arbitrage du 14/11/2026 : créance effacée, secours, logement d'habitation,
    mesure individuelle d'un agent, litige avec un particulier. Le titre, le
    montant et le lien restent ; le texte ne sort pas."""
    motif = lambda titre, texte, publics=frozenset(): bps.motif_sans_extrait(titre, texte, set(publics))

    # Toujours : une liste de débiteurs n'a pas de civilité.
    assert motif("Admission en non-valeur", "titres irrécouvrables : SALOMBE Lou 45,00 €") == \
        "sans_extrait_creance_ou_impaye"
    assert motif("Aide", "Mme Lou SALOMBE, placée sous curatelle renforcée") == "sans_extrait_personne_protegee"
    # 🔴 Seconde relecture sécurité du 14/11 : un nom écrit sans civilité ne
    # déclenchait plus rien, et ces textes sortaient avec le nom.
    for texte in ("DUPONT Paul, adjoint technique, est placé en congé de longue maladie.",
                  "Sanction disciplinaire : Paul Dupont, agent de maîtrise, exclusion temporaire de 3 jours.",
                  "Bail d'habitation du logement de la poste consenti à Paul Dupont.",
                  "Expulsion de Paul Dupont du logement communal.",
                  "Aide sociale facultative : DUPONT Paul 150 € ; SALOMBE Lou 200 €.",
                  "Situation de M. DUPONT Paul\n" + "Le conseil rappelle le cadre applicable. " * 12
                  + "Il prononce le licenciement pour inaptitude."):
        assert motif("Point", texte), texte
    assert motif("Stage", "convention entre le centre de formation, le tuteur et la structure d'accueil") is None
    assert motif("Parc", "un établissement public sous tutelle du ministère chargé de l'Écologie") is None
    # Dès qu'une personne qui n'est pas publique y est nommée.
    for titre, texte, attendu in (
        ("Secours", "DECIDE d'accorder un secours exceptionnel de 200 € à Mme Lou SALOMBE", "aide_sociale"),
        ("Aide", "Une aide financière exceptionnelle de 500 € est accordée à M. Paul DUPONT, "
                 "dont la situation familiale est difficile", "aide_sociale"),
        ("Cantine", "Impayés de cantine : la famille Dupontel doit 450 €", "creance_ou_impaye"),
        ("Eau", "Dégrèvement de la facture d'eau de M. Paul DUPONT suite à une fuite", "creance_ou_impaye"),
        ("Bail", "bail d'habitation consenti à Mme Lou SALOMBE pour 450 € par mois", "logement_d_habitation"),
        ("Logement", "M. Paul DUPONT, occupant du logement communal, présente un retard de loyers",
         "creance_ou_impaye"),
        ("Bail", "signer un bail avec Mme Lou SALOMBE pour l'appartement au-dessus de la poste",
         "logement_d_habitation"),
        ("Personnel", "prononce une sanction disciplinaire à l'encontre de M. Paul DUPONT",
         "mesure_individuelle_agent"),
        ("Personnel", "Mise en disponibilité de Mme Lou SALOMBE", "mesure_individuelle_agent"),
        ("Personnel", "Reclassement de M. Paul DUPONT pour raison de santé, avis du comité médical",
         "mesure_individuelle_agent"),
        ("Autorisation d'ester en justice", "dans le litige qui oppose la commune à M. Paul DUPONT",
         "litige_avec_un_particulier"),
        ("Contentieux", "Le Tribunal administratif a été saisi par M. Paul DUPONT",
         "litige_avec_un_particulier"),
        ("Recours", "Recours gracieux de Mme Lou SALOMBE contre le permis", "litige_avec_un_particulier"),
    ):
        assert motif(titre, texte) == f"sans_extrait_{attendu}", texte
    # Ce qui ne vise personne se lit : un règlement, une subvention au CCAS, la
    # protection de l'élu, un litige avec une société ; et la mention des voies
    # de recours, au pied de presque chaque délibération, n'est pas un litige.
    for titre, texte, publics in (
        ("Règlement intérieur", "Le licenciement et la rupture conventionnelle suivent la procédure", ()),
        ("RIFSEEP", "Le régime est maintenu pendant un congé de longue maladie", ()),
        ("Subvention", "Subvention au CCAS pour l'aide sociale facultative : 5 000 €", ()),
        ("Délégations", "Révocation de la délégation de signature accordée au 2e adjoint", ()),
        ("Protection fonctionnelle", "accordée à M. Jordan ZANIMET, maire", ("JORDAN ZANIMET",)),
        ("Marché", "Remise gracieuse des pénalités à l'entreprise Routex SARL", ()),
        ("Autorisation d'ester en justice", "dans le litige qui oppose la commune à la société X", ()),
        ("Délégations", "M. Jordan ZANIMET, maire, peut ester en justice", ("JORDAN ZANIMET",)),
        ("Tarifs", "peut faire l'objet d'un recours pour excès de pouvoir devant le Tribunal "
                   "Administratif. Demande de M. Paul DUPONT.", ()),
        ("Tarifs de la cantine", "Les tarifs 2026 sont fixés à 3,50 € le repas", ()),
    ):
        assert motif(titre, texte, publics) is None, texte


def test_dans_un_titre_le_doute_profite_au_role(bps):
    """🔴 « AIDE A LA RENOVATION DES FACADES – M. ET MME NOM » sortait dans le
    titre, la recherche et les fichiers : un élu portait ce patronyme, et le
    doute gardait le nom. Il profite désormais au rôle (avis DPO du 14/11/2026),
    sauf dans un titre qui parle d'un mandat."""
    from collections import Counter
    publics = {"JORDAN ZANIMET"}
    titre = lambda t: bps.titre_d_acte(t, "2025-01-20", set(publics), Counter())[0]

    assert "ZANIMET" not in titre("AIDE A LA RENOVATION DES FACADES – M. ET MME ZANIMET")
    assert "ZANIMET" not in titre("Aide à la rénovation des façades de M. ZANIMET, 479 €")
    assert titre("Délégation de signature à M. ZANIMET") == "Délégation de signature à M. ZANIMET"
    # Un mot courant ne fait pas un mandat (relecture sécurité du 14/11).
    for fuite in ("Aide aux façades sur avis de la commission – M. ZANIMET",
                  "Aide à l'installation d'un chauffe-eau solaire – M. ZANIMET",
                  "Permis de construire de M. ZANIMET, titulaire",
                  "Vente d'un terrain à M. ZANIMET, représentant l'indivision",
                  "Subvention façade – M. ZANIMET (dossier présenté par le maire)"):
        assert "ZANIMET" not in titre(fuite), fuite
    # Les autres façons d'écrire une personne devant son nom.
    for forme in ("Aide façade – Mr DUPONTEL", "Aide façade – Mr. Dupontel", "Aide façade – Melle MARTINOT",
                  "Aide façade – Vve MARTINOT", "Aide façade – MONSIEUR MARTINOT", "Aide façade – M.MARTINOT",
                  "Vente d'un terrain aux Consorts MARTINOT", "Vente d'un terrain aux époux Martinot",
                  "Vente d'un terrain à M. et Mme Martinot", "Aide façade de Monsieur Martinot Paul"):
        sortie = titre(forme)
        assert sortie is None or "MARTINOT" not in sortie.upper() and "DUPONTEL" not in sortie.upper(), forme
    assert titre("Indemnités de Monsieur le Maire") == "Indemnités de Monsieur le Maire"
    assert titre("Indemnités de Madame La Présidente") == "Indemnités de Madame La Présidente"
    assert titre("Subvention façade de M. Jordan ZANIMET, 479 €") == "Subvention façade de M. Jordan ZANIMET, 479 €"


def test_un_point_de_lordre_du_jour_ne_nomme_pas_un_particulier(bps, base, entite):
    """🔴 L'ordre du jour d'une convocation publiait « Demande de M. X » en clair :
    il ne passait que par le masque des naissances et des domiciles, alors que
    rien n'y est encore délibéré. Retiré une première fois le 21/08 pour cette
    raison, revenu avec les convocations le 01/10."""
    from collections import Counter
    _personne(base, entite, "Gaston", "PIEGEPART")
    _personne(base, entite, "Lou", "SALESQUE")
    elu = _personne(base, entite, "Jordan", "PELAGNOL")
    redige, _ = bps.compilateur_redaction(base, {elu})

    c = bps.convocation_publique(
        {"ordre_du_jour": ["Demande de M. PIEGEPART", "Vente à Gaston PIEGEPART",
                           "Élection de Jordan PELAGNOL", "FPIC 2026"]},
        "2026-09-23", set(), Counter(), redige)
    assert c["ordre_du_jour"] == ["Demande de un particulier", "Vente à un particulier",
                                  "Élection de Jordan PELAGNOL", "FPIC 2026"]


def test_le_tableau_des_elus_garde_les_noms_et_les_fonctions(bps):
    """L'océrisation mêle les colonnes « Date de naissance Adresse CP Ville » sur
    plusieurs lignes : on ne garde de chaque ligne que l'élu et sa fonction."""
    from collections import Counter
    texte = ("Liste des conseillers municipaux\n"
             "élus : Date de naissance Adresse CP Ville Titre\n"
             "05/03/1961 12 Chemin de la\n"
             "Monsieur Jordan ZANIMET à Testonville (78) Fontaine 99000 FICTIVILLE Maire\n"
             "Madame Andréa PELANDIN à Exempleville (26) 4, Allée des Merles 99000 FICTIVILLE 1ère adjointe\n"
             "Le Conseil Municipal, après en avoir délibéré, APPROUVE")
    sortie = bps.masquer_donnees_personnelles(texte, "2019-02-01", set(), Counter())
    assert "Monsieur Jordan ZANIMET Maire" in sortie
    assert "Madame Andréa PELANDIN 1ère adjointe" in sortie
    for fuite in ("Testonville", "Exempleville", "Chemin", "Allée", "99000", "05/03/1961"):
        assert fuite not in sortie, fuite
    assert sortie.endswith("Le Conseil Municipal, après en avoir délibéré, APPROUVE")


def test_ce_qui_na_pas_pu_etre_masque_ne_sort_pas(bps):
    """Le filet : un tableau dont la fin ne se trouve pas n'est pas masqué, et
    l'extrait est refusé plutôt que publié tel quel."""
    from collections import Counter
    texte = "élus : Date de naissance Adresse CP Ville\n" + "Madame Andréa PELANDIN 4, Allée des Merles\n" * 120
    assert bps.texte_publiable(texte, "2019-02-01", set(), Counter()) == (None, "donnee_personnelle_non_masquee")


def _ligne(ident, titre, document, numero=None, date="2026-05-28", type_="deliberation"):
    import json
    return {"id": ident, "type": type_, "date": date, "title": titre,
            "source_url": "https://exemple.fr/" + document,
            "metadata": json.dumps({"numero_acte": numero} if numero else {})}


def test_le_recueil_fait_foi_sur_les_fragments_du_proces_verbal():
    """Le conseil du 28/05/2026 : 28 actes au recueil, 35 fragments tirés du
    procès-verbal, dont des lignes de tableau avec le vote du voisin."""
    from scripts.snapshot.actes import fragments_de_pv
    lignes = [_ligne(1, "SUBVENTION 2026 — USPOP", "recueil.pdf", "260513"),
              _ligne(2, "Attribuer à l'Amicale Fictive une subvention de 500 €", "recueil.pdf"),
              _ligne(3, "TRANSMISSION", "pv.pdf"),
              _ligne(4, "DUPONT Paul", "pv.pdf"),
              # Une séance connue par son seul procès-verbal n'est pas touchée.
              _ligne(5, "DOTATION 2026 – C.C.A.S", "pv-fevrier.pdf", date="2026-02-05"),
              # Ni l'intercommunalité : ses doublons attendent une relecture.
              _ligne(6, "FONDS DE CONCOURS", "pv-cc.pdf", "12", type_="deliberation_cc"),
              _ligne(7, "TABLEAU DES EFFECTIFS", "recueil-cc.pdf", type_="deliberation_cc")]
    assert fragments_de_pv(lignes) == {3: "https://exemple.fr/pv.pdf", 4: "https://exemple.fr/pv.pdf"}


def test_un_flux_lu_dans_un_fragment_renvoie_au_proces_verbal():
    from scripts.snapshot.argent import renvoyer_aux_pieces
    flux = [{"id": 1, "event_id": 40, "raw_document_id": None, "document_url": None},
            {"id": 2, "event_id": 41, "raw_document_id": None, "document_url": None}]
    sans = renvoyer_aux_pieces(flux, {41}, {40: "https://exemple.fr/pv.pdf"})
    assert sans == 0
    assert flux[0] == {"id": 1, "acte_id": None, "document": "https://exemple.fr/pv.pdf"}
    assert flux[1] == {"id": 2, "acte_id": 41, "document": None}


def test_un_acte_sans_numero_du_recueil_lui_meme_reste():
    """Le cachet illisible d'un acte du recueil lui laisse une clé par titre :
    il vient du document qui fait foi, il sort."""
    from scripts.snapshot.actes import fragments_de_pv
    assert fragments_de_pv([_ligne(1, "TARIFS 2026", "recueil.pdf", "260601"),
                            _ligne(2, "CREATION D'EMPLOI", "recueil.pdf")]) == {}


def test_un_acte_numerote_sans_document_ne_fait_pas_un_recueil():
    """Relecture sécurité du 14/11/2026 : `None` entrait dans les documents
    numérotés, et tous les actes par titre de la séance étaient écartés."""
    from scripts.snapshot.actes import fragments_de_pv
    numerote = {**_ligne(1, "TARIFS 2026", "x", "260601"), "source_url": None}
    assert fragments_de_pv([numerote, _ligne(2, "CREATION D'EMPLOI", "pv.pdf")]) == {}


def test_un_fragment_sans_piece_reste():
    from scripts.snapshot.actes import fragments_de_pv
    sans_piece = {**_ligne(2, "DUPONT Paul", "x"), "source_url": "file:///srv/pv.pdf"}
    assert fragments_de_pv([_ligne(1, "TARIFS 2026", "recueil.pdf", "260601"), sans_piece]) == {}


def test_l_agenda_ne_passe_pas_par_le_filtre_des_titres_d_actes():
    """« Sur les traces de Monsieur Castor » était devenu « … d'un particulier »."""
    from collections import Counter
    from scripts.snapshot.actes import titre_de_l_evenement
    masquages = Counter()
    titre = "Sur les traces de Monsieur Castor"
    assert titre_de_l_evenement("local_event", titre, "2026-06-01", set(), masquages) == (titre, None)
    assert not masquages
    # Le même mot dans un titre d'ACTE reste désigné par son rôle.
    acte, refus = titre_de_l_evenement("deliberation", "Vente d'un terrain à Monsieur Castor",
                                       "2026-06-01", set(), Counter())
    assert refus is None and "Castor" not in acte


def _faire_ei(base, entite, nom):
    eid = entite(nom, perimetre="C1")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code, status)"
                 " VALUES (?, '1000', 'A')", (eid,))
    return eid


def test_flux_et_marche_de_bout_en_bout(bps, base, entite, monkeypatch):
    """`etape_fiches` puis `etape_flux` / `etape_finances`, sur le vrai schéma.

    L'EI n'est retenue que si le flux se publie LIÉ à une partie publique
    publiée. L'autre extrémité :
    - personne physique, personne C2 hors conseil, confiance privée, ou autre
      partie qui n'est pas publique (une association) : l'EI n'est pas retenue ;
    - partie publique rejetée à l'atelier ou d'une commune voisine (flux DÉLIÉ) :
      l'EI n'est pas retenue (le contrôleur refusait le snapshot, avis
      vigie-securite du 25/11/2026) ; le flux sort délié des deux côtés.
    Marché : confiance publique retient, `probable` non, et `marches.json`
    dit la même chose que la fiche."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_finances, etape_flux
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    particulier = entite("Un particulier", "person", perimetre="C1")
    probable = _publique(base, entite, "Syndicat douteux", perimetre="C1", confidence="probable")
    elue_c2 = entite("Une élue voisine", "person", perimetre="C2")
    rejetee = _publique(base, entite, "Syndicat rejeté", perimetre="C1")
    voisine = _publique(base, entite, "Syndicat voisin", perimetre="C2")
    association = entite("Une association", "association", perimetre="C1")
    autres = [particulier, probable, elue_c2, rejetee, voisine, association]
    eis = [_faire_ei(base, entite, f"EI {i}") for i in range(len(autres))]
    for autre, ei in zip(autres, eis):
        base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                     " VALUES ('subvention', ?, ?, 'verified')", (autre, ei))
    marche_ok, marche_prive = _faire_ei(base, entite, "EI marché"), _faire_ei(base, entite, "EI marché probable")
    for ei, conf in ((marche_ok, "verified"), (marche_prive, "probable")):
        base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                     " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', ?)", (ei, conf))
    base.commit()

    revue = {"entity": {rejetee: {"statut": "ecarte"}}}
    civiques = {elue_c2}
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, revue, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, revue, civiques, set(), exclusions,
                            actes["public_events"], actes["actes_de_rubrique"],
                            redige=lambda t: t)
    publies = {e["id"] for e in f["public_entities"]}
    attendu = {marche_ok}
    assert publies & (set(eis) | {marche_ok, marche_prive}) == attendu

    flux = etape_flux(base, revue, f["entity_rows"], civiques, f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    # Les flux des deux parties publiques déliées sortent, déliés des deux côtés,
    # sans nom d'EI ; l'EI de l'association est écartée faute de fait public, et
    # l'association n'est pas une partie publique : son flux est ÉCARTÉ (brief
    # `ei-flux-marches`, il sortait délié avant).
    assert len(flux) == 2
    assert {fl["to_name"] for fl in flux} == {None}
    assert {fl["from_name"] for fl in flux} == {"Syndicat rejeté", "Syndicat voisin"}
    assert all(fl["from_id"] is None and fl["to_id"] is None for fl in flux)

    finances = etape_finances(base, revue, f["public_ids"], f["public_entities"],
                              {e["id"]: e.get("perimetre") for e in f["public_entities"]},
                              set(), exclusions, ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])
    assert {m["titulaire_id"] for m in finances["marches_data"]} == {marche_ok}


def test_une_ei_citee_seulement_dans_un_acte_ecarte_n_est_pas_publiee(bps, base, entite, monkeypatch):
    """De bout en bout, avec le vrai branchement : `etape_actes` puis
    `etape_fiches(public_events=...)`. Un acte de type exclu n'est pas publié ;
    l'EI qu'il cite ressort avec `ei_sans_fait_public`. Témoin : citée dans un
    acte publié, elle est publiée."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.socle import RULES
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    ecartee, temoin = entite("EI écartée", perimetre="C1"), entite("EI témoin", perimetre="C1")
    for eid in (ecartee, temoin):
        base.execute("INSERT INTO businesses (entity_id, legal_form_code, status)"
                     " VALUES (?, '1000', 'A')", (eid,))
    source = _source_publique()
    type_exclu = sorted(RULES["events"]["exclude_types"])[0]
    _acte(base, source, "cite", ecartee, type_exclu)
    _acte(base, source, "cite", temoin, "deliberation")
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    sortie = fiches.etape_fiches(base, {}, set(), set(), exclusions,
                                 actes["public_events"], actes["actes_de_rubrique"],
                            redige=lambda t: t)
    publies = {e["id"] for e in sortie["public_entities"]}
    assert temoin in publies and ecartee not in publies
    motifs = {x["id"]: x["reasons"] for x in sortie["entity_exclusions"]}
    assert motifs[ecartee] == ["ei_sans_fait_public"]


def test_un_flux_n_est_retenant_que_de_la_part_d_une_partie_publique(bps, base, entite, monkeypatch):
    """Retiennent : la commune, l'intercommunalité, un syndicat en forme 7xxx, un EPIC en forme 4110.
    Ne retiennent pas : une association, une société, une pharmacie FINESS (une
    fiche `type = 'service'` sans forme publique)."""
    from collectors import config
    from collectors.nom_normalise import normaliser
    monkeypatch.setattr(config, "EPCI_NOM", "Communauté de communes de Test")
    ei = {n: entite(f"EI {n}") for n in
          ("asso", "societe", "pharmacie", "commune", "interco", "syndicat", "epic")}
    asso = entite("Une association", "association")
    societe = entite("Une société")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code) VALUES (?, '5499')", (societe,))
    pharmacie = entite("Pharmacie FINESS", "service")
    commune = _commune(base)
    interco = base.execute(
        "INSERT INTO entities (type, name, name_norm, confidence)"
        " VALUES ('service', 'Communauté de communes de Test', ?, 'verified')",
        (normaliser("Communauté de communes de Test"),)).lastrowid
    syndicat = _publique(base, entite, "Syndicat de droit public")
    epic = _publique(base, entite, "Agence publique (EPIC)", forme="4110")
    for de, cle in ((asso, "asso"), (societe, "societe"), (pharmacie, "pharmacie"),
                    (commune, "commune"), (interco, "interco"), (syndicat, "syndicat"), (epic, "epic")):
        base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                     " VALUES ('subvention', ?, ?, 'verified')", (de, ei[cle]))
    assert _retenus(base) == {ei["commune"], ei["interco"], ei["syndicat"],
                                             ei["epic"]}


@pytest.mark.parametrize("source,type_", [
    ("ccomptes", "rapport_ccomptes"), ("sitadel", "permis"), ("gpu", "document_urbanisme"),
    ("interieur", "resultat_electoral"), ("BOAMP", "marche_public"),
    ("DECP data.gouv.fr", "marche_public")])
def test_seul_un_acte_de_la_collectivite_retient(bps, base, entite, source, type_):
    """Les registres et publications d'autres autorités ne sont pas des actes de la
    collectivité. BOAMP et DECP comptent comme marchés (table `marches_publics`)."""
    ei = entite("EI citée par un registre")
    _acte(base, source, "cite", ei, type_)
    assert _retenus(base) == set()
    _acte(base, _source_publique(), "cite", ei, "deliberation")
    assert _retenus(base) == {ei}


def _verdict(statut, confidence=None):
    return {"statut": statut, "corrections": {}, "confidence": confidence, "note": None}


def test_le_verdict_d_atelier_passe_avant_la_confiance(bps, base, entite):
    """Flux ou marché rejeté à l'atelier : ne retient pas. Flux `probable` validé
    à l'atelier (confiance relevée) : retient. Même ordre qu'`etape_flux`."""
    commune = _commune(base)
    ei_rejete, ei_valide, ei_marche_rejete, ei_marche = (entite(f"EI {i}") for i in range(4))
    flux_rejete = base.execute(
        "INSERT INTO financial_flows (type, from_id, to_id, confidence)"
        " VALUES ('subvention', ?, ?, 'verified')", (commune, ei_rejete)).lastrowid
    flux_valide = base.execute(
        "INSERT INTO financial_flows (type, from_id, to_id, confidence)"
        " VALUES ('subvention', ?, ?, 'probable')", (commune, ei_valide)).lastrowid
    marche_rejete, marche = (base.execute(
        "INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id, objet,"
        " source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (ei,)).lastrowid
        for ei in (ei_marche_rejete, ei_marche))
    revue = {"flow": {flux_rejete: _verdict("ecarte"),
                      flux_valide: _verdict("retenu", "verified")},
             "marche": {marche_rejete: _verdict("ecarte")}}
    assert _retenus(base, revue=revue) == {ei_valide, ei_marche}
    # Sans revue : le flux `probable` ne retient pas, le rejeté retient.
    assert _retenus(base) == {ei_rejete, ei_marche_rejete, ei_marche}


def test_un_flux_d_agregat_ne_retient_pas(bps, base, entite):
    """`write_entity_bundles` retire les agrégats de la fiche : ils ne désignent
    personne."""
    commune, ei, ei_detail = _commune(base), entite("EI agrégat"), entite("EI détail")
    for cible, perimetre in ((ei, "agregat"), (ei_detail, "detail")):
        base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence, perimetre)"
                     " VALUES ('subvention', ?, ?, 'verified', ?)", (commune, cible, perimetre))
    assert _retenus(base) == {ei_detail}


def test_un_flux_que_l_ei_verse_a_la_commune_ne_retient_pas(bps, base, entite):
    """Loyer, redevance, prix d'achat : dans le sens EI → partie publique, pas de
    retenue (avis vigie-dpo du 24/11/2026). Dans l'autre sens, oui."""
    commune = _commune(base)
    locataire, beneficiaire = entite("EI locataire"), entite("EI bénéficiaire")
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('bail', ?, ?, 'verified')", (locataire, commune))
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('subvention', ?, ?, 'verified')", (commune, beneficiaire))
    assert _retenus(base) == {beneficiaire}


def test_un_acte_au_titre_masque_ne_retient_pas_et_n_est_pas_lie(bps, base, entite, monkeypatch):
    """De bout en bout : une délibération dont le titre publié dit « un
    particulier » ne retient pas l'EI qu'elle lie ; et si l'EI est retenue par un
    marché, sa fiche ne liste ni cet acte ni un acte où elle est `demandeur`."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes, etape_liens_actes
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    masquee, marche = (_faire_ei(base, entite, n) for n in ("EI masquée", "EI marché"))
    src = _source_publique()
    titre = base.execute("INSERT INTO events (type, source, title, date) VALUES"
                         " ('deliberation', ?, 'Vente d''un terrain à un particulier', '2026-06-01')",
                         (src,)).lastrowid
    neutre = base.execute("INSERT INTO events (type, source, title, date) VALUES"
                          " ('deliberation', ?, 'Subvention annuelle', '2026-06-02')",
                          (src,)).lastrowid
    for eid, ei, role in ((titre, masquee, "cite"), (titre, marche, "cite"),
                          (neutre, marche, "demandeur")):
        base.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?,?,?)",
                     (eid, ei, role))
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (marche,))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions,
                            actes["public_events"], actes["actes_de_rubrique"],
                            redige=lambda t: t)
    publies = {e["id"] for e in f["public_entities"]}
    assert marche in publies and masquee not in publies
    liens = etape_liens_actes(base, f["public_ids"], f["public_entities"],
                              actes["public_events"], exclusions, f["ei_ids"])["public_links"]
    assert [l for l in liens if l["entity_id"] == marche] == []


def test_une_ei_en_naf_68_est_traitee_comme_une_ei(bps, base, entite, monkeypatch):
    """Une EI en NAF 68 est `individuelle` (la forme 1000 l'emporte) ; le repérage
    des liens se fait par la forme juridique 1000 (`ei_ids`)."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes, etape_liens_actes
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    ei = _faire_ei(base, entite, "EI loueuse")
    base.execute("UPDATE businesses SET naf_code = '6820A' WHERE entity_id = ?", (ei,))
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (ei,))
    acte = base.execute("INSERT INTO events (type, source, title, date) VALUES"
                        " ('deliberation', ?, 'Vente à un particulier', '2026-06-01')",
                        (_source_publique(),)).lastrowid
    base.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?,?,'cite')",
                 (acte, ei))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions,
                            actes["public_events"], actes["actes_de_rubrique"],
                            redige=lambda t: t)
    fiche = next(e for e in f["public_entities"] if e["id"] == ei)
    assert fiche["nature"] == "individuelle"
    liens = etape_liens_actes(base, f["public_ids"], f["public_entities"],
                              actes["public_events"], exclusions, f["ei_ids"])["public_links"]
    assert [l for l in liens if l["entity_id"] == ei] == []


def test_du_builder_au_controle_trois_ei_retenues_sans_erreur(bps, base, entite, monkeypatch, tmp_path):
    """Le builder et le contrôleur tiennent la même règle : trois EI retenues (flux
    de la commune, marché, délibération), les étapes jusqu'à `write_entity_bundles`,
    puis `check_ei_sans_fait` : aucune erreur."""
    import importlib.util
    import json
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes, etape_liens_actes
    from scripts.snapshot.argent import etape_finances, etape_flux
    from scripts.snapshot.socle import ROOT
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    # Le payeur doit avoir sa fiche pour retenir (avis vigie-securite du 25/11).
    base.execute("UPDATE entities SET perimetre = 'C1' WHERE id = ?", (commune,))
    par_flux, par_marche, par_acte = (_faire_ei(base, entite, n)
                                      for n in ("EI flux", "EI marché", "EI acte"))
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence)"
                 " VALUES ('subvention', ?, ?, 'verified')", (commune, par_flux))
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (par_marche,))
    acte = base.execute("INSERT INTO events (type, source, title, date) VALUES"
                        " ('deliberation', ?, 'Subvention annuelle', '2026-06-01')",
                        (_source_publique(),)).lastrowid
    base.execute("INSERT INTO event_entities (event_id, entity_id, role) VALUES (?,?,'cite')",
                 (acte, par_acte))
    base.commit()

    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions,
                            actes["public_events"], actes["actes_de_rubrique"],
                            redige=lambda t: t)
    assert {par_flux, par_marche, par_acte} <= f["public_ids"]
    liens = etape_liens_actes(base, f["public_ids"], f["public_entities"],
                              actes["public_events"], exclusions, f["ei_ids"])["public_links"]
    flux = etape_flux(base, {}, f["entity_rows"], set(), f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    marches = etape_finances(base, {}, f["public_ids"], f["public_entities"], {}, set(),
                             exclusions, ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["marches_data"]
    fiches.write_entity_bundles(tmp_path, f["public_entities"], [], actes["public_events"],
                                liens, flux, marches)
    (tmp_path / "entities.json").write_text(json.dumps({"entities": f["public_entities"]}))

    spec = importlib.util.spec_from_file_location(
        "verify_snapshot", ROOT / "scripts" / "verify_snapshot.py")
    vs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vs)
    rep = vs.Report()
    vs.check_ei_sans_fait(tmp_path, rep)
    assert rep.errors == {}
    assert sum(1 for e in f["public_entities"] if e.get("nature") == "individuelle") == 3


def test_le_lien_beneficiaire_d_un_flux_non_publiable_ne_retient_pas(bps, base, entite):
    """Les collecteurs posent un lien `bénéficiaire` pour tout flux lu dans un acte,
    même `probable` : il ne doit pas rouvrir ce que le filtre des flux ferme."""
    ei = entite("EI bénéficiaire d'un flux probable")
    _acte(base, _source_publique(), "bénéficiaire", ei)
    base.execute("INSERT INTO financial_flows (type, to_id, confidence)"
                 " VALUES ('subvention', ?, 'probable')", (ei,))
    assert _retenus(base) == set()


def test_un_flux_vers_une_ei_au_titre_masque_est_delie(bps, base, entite, monkeypatch):
    """La fiche d'une EI ne ré-identifie pas « un particulier » par ses flux."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    ei = _faire_ei(base, entite, "EI aidée")
    acte = base.execute("INSERT INTO events (type, source, title, date) VALUES"
                        " ('deliberation', ?, 'Aide à un particulier', '2026-06-01')",
                        (_source_publique(),)).lastrowid
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, event_id, confidence,"
                 " description) VALUES ('subvention', ?, ?, ?, 'verified', 'Aide à un particulier')",
                 (commune, ei, acte))
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (ei,))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions, actes["public_events"], redige=lambda t: t)
    flux = etape_flux(base, {}, f["entity_rows"], set(), f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    # Délié : ni l'EI (id, nom) ni la description ; le montant reste attaché à l'acte.
    assert len(flux) == 1
    assert flux[0]["to_id"] is None and flux[0]["to_name"] is None
    assert flux[0]["description"] is None and flux[0]["acte_id"] == acte


def test_un_flux_lu_dans_un_acte_masque_ne_retient_pas(bps, base, entite):
    commune = _commune(base)
    masque, decrit, neutre = (entite(n) for n in ("EI acte masqué", "EI description", "EI neutre"))
    acte = base.execute("INSERT INTO events (type, source) VALUES ('deliberation', 'x')").lastrowid
    for ei, event, desc in ((masque, acte, None), (decrit, None, "Aide à un particulier"),
                            (neutre, None, "Subvention")):
        base.execute("INSERT INTO financial_flows (type, from_id, to_id, event_id, confidence,"
                     " description) VALUES ('subvention', ?, ?, ?, 'verified', ?)",
                     (commune, ei, event, desc))
    assert _retenus(base, ids_actes_publics=set(), actes_masques={acte}) == {neutre}


def test_la_description_est_redigee_avant_de_retenir(bps, base, entite, monkeypatch):
    """Une subvention à « Jeanne EXEMPLE », EI dont la dirigeante non publique
    porte le même nom : avec le VRAI `redige`, la description devient « un
    particulier », le flux est délié et ne retient pas l'EI (sinon le snapshot
    serait refusé par `check_ei_sans_fait`)."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    from scripts.snapshot.personnes import etape_personnes_publiques
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    dirigeante = entite("Jeanne Exemple", type_="person")
    base.execute("UPDATE entities SET name = 'Jeanne EXEMPLE' WHERE id = ?", (dirigeante,))
    base.execute("INSERT INTO persons (entity_id, firstname, lastname) VALUES (?, 'Jeanne', 'EXEMPLE')",
                 (dirigeante,))
    ei = _faire_ei(base, entite, "Jeanne EXEMPLE")
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence, description)"
                 " VALUES ('subvention', ?, ?, 'verified', 'Subvention à Jeanne EXEMPLE')",
                 (commune, ei))
    base.commit()
    pers = etape_personnes_publiques(base, "[]")
    redige = pers["redige"]
    assert "Jeanne EXEMPLE" not in (redige("Subvention à Jeanne EXEMPLE") or "")
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, pers["noms_publics"], redige, exclusions)
    f = fiches.etape_fiches(base, {}, pers["public_person_ids"], set(), exclusions,
                            actes["public_events"], redige=redige)
    assert ei not in f["public_ids"]
    flux = etape_flux(base, {}, f["entity_rows"], pers["public_person_ids"], f["public_ids"],
                      f["ecartees_du_perimetre"], redige, exclusions, actes["public_events"],
                      ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    assert all(fl.get("to_name") != "Jeanne EXEMPLE" for fl in flux)


def test_une_ei_hors_perimetre_perd_son_nom_avec_un_titre_masque(bps, base, entite, monkeypatch):
    """EI d'une commune voisine (C2) : écartée pour le périmètre, son flux est
    déjà délié (nom gardé) avant le masque ; il perd quand même nom et description
    si la description dit « un particulier » (avis vigie-securite, tour 9)."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    base.execute("UPDATE entities SET perimetre = 'C1' WHERE id = ?", (commune,))
    ei = entite("Jeanne Exemple", perimetre="C2")
    base.execute("INSERT INTO businesses (entity_id, legal_form_code, status)"
                 " VALUES (?, '1000', 'A')", (ei,))
    base.execute("INSERT INTO financial_flows (type, from_id, to_id, confidence, description)"
                 " VALUES ('subvention', ?, ?, 'verified', 'Subvention à un particulier')",
                 (commune, ei))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions, actes["public_events"],
                            redige=lambda t: t)
    flux = etape_flux(base, {}, f["entity_rows"], set(), f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    assert len(flux) == 1
    assert flux[0]["to_id"] is None and flux[0]["to_name"] is None
    assert flux[0]["description"] is None


def test_les_copies_du_controleur_sont_celles_du_builder(bps):
    """`verify_snapshot.py` n'importe pas le builder (filet indépendant) et recopie
    ses listes : cet essai les garde identiques."""
    import importlib.util
    from scripts.snapshot.actes import _DESIGNE_UNE_PERSONNE
    from scripts.snapshot.fiches import (ETATS_SANS_VERSEMENT, ROLES_QUI_NE_RETIENNENT_PAS,
                                         TYPES_ACTES_DE_LA_COLLECTIVITE)
    from scripts.snapshot.socle import ROOT
    spec = importlib.util.spec_from_file_location(
        "verify_snapshot", ROOT / "scripts" / "verify_snapshot.py")
    vs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vs)
    assert tuple(vs.TYPES_ACTES_COLLECTIVITE) == tuple(TYPES_ACTES_DE_LA_COLLECTIVITE)
    assert vs.DESIGNE_UNE_PERSONNE.pattern == _DESIGNE_UNE_PERSONNE.pattern
    assert vs.DESIGNE_UNE_PERSONNE.flags == _DESIGNE_UNE_PERSONNE.flags
    assert tuple(vs.ROLES_SANS_FAIT) == tuple(ROLES_QUI_NE_RETIENNENT_PAS)
    assert tuple(vs.ETATS_SANS_VERSEMENT) == tuple(ETATS_SANS_VERSEMENT)


@pytest.mark.parametrize("masque_en_premier", [False, True])
def test_deux_flux_jumeaux_vers_une_ei_le_masque_reste(bps, base, entite, monkeypatch, masque_en_premier):
    """Deux flux jumeaux (même année, montant, type, émetteur, EI), l'un neutre,
    l'autre à la description « un particulier » : il en sort UN seul, délié, sans
    `to_name`, quel que soit l'ordre d'entrée. Jamais le neutre avec le nom."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    base.execute("UPDATE entities SET perimetre = 'C1' WHERE id = ?", (commune,))
    ei = _faire_ei(base, entite, "EI jumelle")
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (ei,))
    descriptions = ["Aide à un particulier", "Aide annuelle"]
    for d in (descriptions if masque_en_premier else descriptions[::-1]):
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                     " description) VALUES ('subvention', 2025, 500, ?, ?, 'verified', ?)",
                     (commune, ei, d))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions, actes["public_events"],
                            redige=lambda t: t)
    flux = etape_flux(base, {}, f["entity_rows"], set(), f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    assert len(flux) == 1
    assert flux[0]["to_id"] is None and flux[0]["to_name"] is None
    assert flux[0]["description"] is None


def test_des_jumeaux_dont_l_un_est_masque_ne_retiennent_pas(bps, base, entite):
    """Sans autre fait, ni le neutre ni le masqué ne retiennent l'EI : sinon sa
    fiche sortirait sans fait (le contrôle refuserait le snapshot)."""
    commune = _commune(base)
    ei = entite("EI jumelle sans autre fait")
    for d in ("Aide annuelle", "Aide à un particulier"):
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                     " description) VALUES ('subvention', 2025, 500, ?, ?, 'verified', ?)",
                     (commune, ei, d))
    assert _retenus(base) == set()


def test_dedupliquer_flux_garde_le_masque(bps):
    neutre = {"year": 2025, "amount": 500, "type": "s", "from_id": 1, "to_id": 9,
              "description": "Aide annuelle"}
    masque = {**neutre, "description": "Aide à un particulier"}
    est = lambda f: "particulier" in (f.get("description") or "")
    assert bps.dedupliquer_flux([neutre, masque], est) == [masque]
    assert bps.dedupliquer_flux([masque, neutre], est) == [masque]
    assert bps.dedupliquer_flux([neutre, masque]) == [neutre]  # sans `masque` : comme avant


@pytest.mark.parametrize("masque_en_premier", [False, True])
def test_des_jumeaux_dont_l_un_nomme_une_personne_non_publique(bps, base, entite, monkeypatch, masque_en_premier):
    """Le masque se lit APRÈS `redige` : « Versement à Jeanne EXEMPLE » (nom d'une
    personne non publique) devient « un particulier ». Entre ce jumeau et un flux
    « Travaux », c'est lui qui reste, délié, dans les deux ordres d'entrée."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.argent import etape_flux
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.personnes import etape_personnes_publiques
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    base.execute("UPDATE entities SET perimetre = 'C1' WHERE id = ?", (commune,))
    dirigeante = entite("Jeanne EXEMPLE", type_="person")
    base.execute("INSERT INTO persons (entity_id, firstname, lastname) VALUES (?, 'Jeanne', 'EXEMPLE')",
                 (dirigeante,))
    ei = _faire_ei(base, entite, "Jeanne EXEMPLE")
    base.execute("INSERT INTO marches_publics (acheteur_siren, acheteur_nom, titulaire_id,"
                 " objet, source, confidence) VALUES ('1', 'x', ?, 'o', 's', 'verified')", (ei,))
    descriptions = ["Versement à Jeanne EXEMPLE", "Travaux"]
    for d in (descriptions if masque_en_premier else descriptions[::-1]):
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                     " description) VALUES ('subvention', 2025, 500, ?, ?, 'verified', ?)",
                     (commune, ei, d))
    base.commit()
    pers = etape_personnes_publiques(base, "[]")
    redige = pers["redige"]
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, pers["noms_publics"], redige, exclusions)
    f = fiches.etape_fiches(base, {}, pers["public_person_ids"], set(), exclusions,
                            actes["public_events"], redige=redige)
    flux = etape_flux(base, {}, f["entity_rows"], pers["public_person_ids"], f["public_ids"],
                      f["ecartees_du_perimetre"], redige, exclusions, actes["public_events"],
                      ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    assert len(flux) == 1
    assert flux[0]["to_id"] is None and flux[0]["to_name"] is None
    assert flux[0]["description"] is None


@pytest.mark.parametrize("cas", ["subvention_a_un_particulier", "loyer_de_l_ei"])
def test_un_flux_d_une_ei_sans_autre_fait_est_delie_et_garde_son_montant(bps, base, entite, monkeypatch, cas):
    """EI écartée faute de fait public : son flux ne disparaît pas des totaux. Un
    seul flux, délié, montant présent, aucun nom ni identifiant d'EI, aucune
    description ; et l'EI n'est pas publiée. (a) subvention de la commune
    « à un particulier » ; (b) loyer versé par l'EI à la commune."""
    from collections import Counter, defaultdict
    from scripts.snapshot import fiches
    from scripts.snapshot.actes import etape_actes
    from scripts.snapshot.argent import etape_flux
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    commune = _commune(base)
    base.execute("UPDATE entities SET perimetre = 'C1' WHERE id = ?", (commune,))
    ei = _faire_ei(base, entite, "Jeanne Exemple")
    if cas == "subvention_a_un_particulier":
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                     " description) VALUES ('subvention', 2025, 840, ?, ?, 'verified',"
                     " 'Aide à un particulier')", (commune, ei))
    else:
        base.execute("INSERT INTO financial_flows (type, year, amount, from_id, to_id, confidence,"
                     " description) VALUES ('bail', 2025, 840, ?, ?, 'verified',"
                     " 'Loyer du local')", (ei, commune))
    base.commit()
    exclusions = defaultdict(Counter)
    actes = etape_actes(base, {}, set(), lambda t: t, exclusions)
    f = fiches.etape_fiches(base, {}, set(), set(), exclusions, actes["public_events"],
                            redige=lambda t: t)
    assert ei not in f["public_ids"]
    flux = etape_flux(base, {}, f["entity_rows"], set(), f["public_ids"],
                      f["ecartees_du_perimetre"], lambda t: t, exclusions,
                      actes["public_events"], ei_ids=f["ei_ids"], ei_nommees=f["ei_nommees"])["public_flows"]
    assert len(flux) == 1
    assert flux[0]["amount"] == 840
    assert ei not in (flux[0]["from_id"], flux[0]["to_id"])
    assert flux[0]["from_name"] != "Jeanne Exemple" and flux[0]["to_name"] != "Jeanne Exemple"
    import json
    assert "Jeanne Exemple" not in json.dumps(flux)
    assert flux[0]["description"] is None
