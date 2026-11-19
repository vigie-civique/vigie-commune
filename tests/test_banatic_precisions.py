"""BANATIC : une compétence se lit avec sa précision, un événement avec sa date.

Relevé du 07/11/2026 : la compétence « Service public de défense extérieure
contre l'incendie » d'une intercommunalité portait la précision « Entretien des
pistes DFCI. », que le collecteur ne lisait pas ; lue seule, elle a fait écrire
que les bornes incendie relevaient de l'intercommunalité. Et la fiche annonçait
encore un transfert de compétences par un arrêté annulé depuis.
Données fictives, à la forme de `__NEXT_DATA__`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from collectors.banatic import import_arretes, import_competences
from collectors.config import EPCI_SIREN
from scripts.snapshot.democratie import etape_intercommunalite


def _fiche(**champs):
    return {"dateDerniereModification": "2026-09-09 00:00:00", **champs}


def _competence(code="7525", precision="Entretien des pistes DFCI.", ic=None):
    return {"code": code, "libelle": "Service public de défense extérieure contre l'incendie",
            "description": "", "categorieCompetence": {"code": "15", "libelle": "Autres"},
            "isObligatoire": False, "isInteretCommunautaire": False,
            "specificiteInteretCompetence": precision,
            "specificiteInteretCommunautaire": ic}


def _evenement(id_="ev-1", doc="doc-1"):
    return {"id": id_, "description": "Transfert des compétences eau et assainissement au 1/01/2021",
            "document": {"id": doc, "titre": "Arrêté de modification"},
            "dateCreation": "2019-12-12 00:00:00", "dateEffet": "2021-01-01 00:00:00",
            "isCreationGroupement": False, "isAffichable": True}


def test_la_precision_est_collectee(base):
    import_competences(base, 0, _fiche(competencesGroupement=[_competence()]), dry_run=False)
    row = base.execute("SELECT libelle, precision_competence, precision_interet_communautaire"
                       " FROM epci_competences").fetchone()
    assert row["precision_competence"] == "Entretien des pistes DFCI."
    assert row["precision_interet_communautaire"] is None


def test_une_precision_retiree_a_la_source_disparait(base):
    import_competences(base, 0, _fiche(competencesGroupement=[_competence()]), dry_run=False)
    import_competences(base, 0, _fiche(competencesGroupement=[_competence(precision="  ")]),
                       dry_run=False)
    assert base.execute("SELECT precision_competence FROM epci_competences"
                        ).fetchone()[0] is None


def test_la_precision_sort_dans_le_snapshot(base, tmp_path):
    import_competences(base, 0, _fiche(competencesGroupement=[_competence(
        code="3030", precision=None, ic="Résidences non médicalisées.")]), dry_run=False)
    base.commit()
    etape_intercommunalite(base, tmp_path, datetime.now(timezone.utc), {})
    c = json.loads((tmp_path / "intercommunalite.json").read_text())["competences"][0]
    assert c["precision_interet_communautaire"] == "Résidences non médicalisées."
    assert c["precision_competence"] is None


def test_le_snapshot_lit_une_base_sans_les_colonnes(base, tmp_path):
    base.execute("DROP TABLE epci_competences")
    base.execute("CREATE TABLE epci_competences (code TEXT, libelle TEXT, categorie TEXT,"
                 " obligatoire INTEGER, interet_communautaire INTEGER)")
    base.execute("INSERT INTO epci_competences VALUES ('7525', 'DECI', 'Autres', 0, 0)")
    etape_intercommunalite(base, tmp_path, datetime.now(timezone.utc), {})
    c = json.loads((tmp_path / "intercommunalite.json").read_text())["competences"][0]
    assert c["precision_competence"] is None


def test_un_evenement_dit_sa_source_et_sa_date(base):
    import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    ev = base.execute("SELECT title, content, metadata FROM events").fetchone()
    assert "selon BANATIC, fiche à jour du 09/09/2026" in ev["title"]
    assert "retiré, annulé ou remplacé" in ev["content"]
    meta = json.loads(ev["metadata"])
    assert (meta["banatic_id"], meta["date_effet"], meta["etat_au"], meta["revisable"]) == (
        "ev-1", "2021-01-01", "2026-09-09", True)
    assert meta["siren"] == EPCI_SIREN


def test_rejouer_ne_duplique_pas_et_reecrit(base):
    for _ in range(3):
        import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    assert base.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    import_arretes(base, {"dateDerniereModification": "2026-11-01", "evenements": [_evenement()]},
                   dry_run=False)
    assert "01/11/2026" in base.execute("SELECT title FROM events").fetchone()[0]


def test_une_ligne_davant_la_regle_est_reprise_par_son_document(base):
    """Une base collectée avant : ni identifiant BANATIC ni date d'état."""
    import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    base.execute("UPDATE events SET title='ancien', metadata='{}'")
    import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    rows = base.execute("SELECT title FROM events").fetchall()
    assert len(rows) == 1 and "selon BANATIC" in rows[0][0]


def test_deux_evenements_qui_partagent_un_document_restent_deux(base):
    """Relecture vigie-securite du 08/11 : la reprise par l'URL faisait écraser
    le second événement d'une fiche par le premier quand ils citent le même arrêté."""
    evs = [_evenement("ev-1", "doc-1"), _evenement("ev-2", "doc-1")]
    evs[1]["description"] = "Adhésion au syndicat mixte"
    for _ in range(2):
        import_arretes(base, _fiche(evenements=evs), dry_run=False)
    ids = sorted(json.loads(r[0])["banatic_id"]
                 for r in base.execute("SELECT metadata FROM events"))
    assert ids == ["ev-1", "ev-2"]


def test_une_cle_posee_apres_coup_survit_a_la_passe(base):
    import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    base.execute("UPDATE events SET metadata = json_patch(metadata, '{\"note\": \"x\"}')")
    import_arretes(base, _fiche(evenements=[_evenement()]), dry_run=False)
    assert json.loads(base.execute("SELECT metadata FROM events").fetchone()[0])["note"] == "x"


def test_une_ligne_davant_la_regle_et_deux_evenements_du_meme_document(base):
    """Le cas de migration réel : une ligne sans identifiant, puis une fiche qui
    porte deux événements citant le même arrêté."""
    import_arretes(base, _fiche(evenements=[_evenement("ev-1", "doc-1")]), dry_run=False)
    base.execute("UPDATE events SET metadata='{}'")
    evs = [_evenement("ev-1", "doc-1"), _evenement("ev-2", "doc-1")]
    for _ in range(2):
        import_arretes(base, _fiche(evenements=evs), dry_run=False)
    ids = sorted(json.loads(r[0]).get("banatic_id")
                 for r in base.execute("SELECT metadata FROM events"))
    assert ids == ["ev-1", "ev-2"]
