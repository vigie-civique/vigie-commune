"""Les subventions FDVA accordées, lues dans les résultats publiés par la DRAJES.

Ce que ces essais figent, d'après l'avis DPO du 07/11/2026 : un refus n'entre
jamais en base ; seule une association du périmètre, rattachée par son
identifiant, reçoit ; l'état est « engagé », pas « payé » ; un intitulé qui
semble nommer une personne attend l'atelier. Les cas viennent du PDF réel de la
campagne 2026 (Occitanie), réduits à des données fictives.
"""
from __future__ import annotations

import json

import pytest

from collectors import fdva_resultats as fdva
from collectors.etat_flux import etat_du_flux


def _ligne(siret, accorde, intitule="Fonctionnement global", dossier="26-040108",
           demande="2 000 €"):
    return dict(zip(fdva.ENTETE, ("1050", "DD30", siret, dossier, "Amicale Fictive", intitule,
                                  "Une longue description du projet.", "140 070 €",
                                  demande, accorde)))


@pytest.fixture
def association(base, entite):
    eid = entite("Amicale Fictive", "association")
    base.execute("INSERT INTO associations (entity_id, siren) VALUES (?, ?)", (eid, "900000001"))
    base.commit()
    return eid


@pytest.fixture
def etat(entite):
    return entite("État français", "service")


def _flux(base):
    return [dict(r) for r in base.execute(
        "SELECT year, amount, to_id, description, source, confidence, statut, type"
        " FROM financial_flows ORDER BY id")]


def test_un_montant_accorde_se_lit_et_un_refus_vaut_zero():
    assert fdva.montant("2 000 €") == 2000
    assert fdva.montant("1 500 €") == 1500
    assert fdva.montant("- €") == 0


def test_l_accorde_entre_engage_et_le_refus_n_entre_pas(base, association, etat):
    stats = fdva.importer(base, [_ligne("900000001 00029", "2 000 €"),
                                 _ligne("900000001 00029", "- €", "Second volet")],
                          2026, etat)
    assert stats["refusees"] == 1 and stats["inserees"] == 1
    (flux,) = _flux(base)
    assert flux["amount"] == 2000 and flux["year"] == 2026 and flux["to_id"] == association
    assert flux["statut"] == "engage" and flux["confidence"] == "probable"
    assert etat_du_flux(flux["type"], flux["source"], flux["statut"]) == "engage"
    # Ni la description longue, ni le demandé, ni le coût du projet.
    assert "description" not in flux["description"].lower()
    assert "140" not in flux["description"]


def test_hors_perimetre_et_sans_identifiant_ne_rattachent_rien(base, association, etat):
    stats = fdva.importer(base, [_ligne("410532584 00019", "2 000 €"),
                                 _ligne("NR", "1 000 €")], 2026, etat)
    assert (stats["hors_perimetre"], stats["sans_identifiant"], stats["inserees"]) == (1, 1, 0)
    assert _flux(base) == []


def test_une_entreprise_du_perimetre_n_est_pas_une_association(base, entite, etat):
    eid = entite("Jean Dupont EI", "business")
    base.execute("INSERT INTO businesses (entity_id, siren, siret_siege, raw_data) VALUES (?,?,?,?)",
                 (eid, "812345678", "81234567800011", json.dumps({
                     "nature_juridique": "1000",
                     "siege": {"siret": "81234567800011"},
                     "matching_etablissements": [{"siret": "81234567800011"}]})))
    base.commit()
    stats = fdva.importer(base, [_ligne("812345678 00011", "1 000 €")], 2026, etat)
    assert stats["pas_une_association"] == 1 and _flux(base) == []


def test_un_intitule_qui_nomme_une_personne_attend_l_atelier(base, association, etat):
    fdva.importer(base, [_ligne("900000001 00029", "650 €", "Résidence de Mme Martin")], 2026, etat)
    (flux,) = _flux(base)
    assert flux["confidence"] == "probable"
    assert not base.execute("SELECT 1 FROM relations").fetchone()


def test_deux_volets_d_un_meme_dossier_font_deux_flux_et_la_relecture_aucun_de_plus(
        base, association, etat):
    lignes = [_ligne("900000001 00029", "650 €", "Balades contées"),
              _ligne("900000001 00029", "1 000 €", "Ateliers artistiques")]
    assert fdva.importer(base, lignes, 2026, etat)["inserees"] == 2
    assert fdva.importer(base, lignes, 2026, etat)["deja"] == 2
    assert len(_flux(base)) == 2


class _Page:
    def __init__(self, numero, texte, tables):
        self.page_number, self._texte, self._tables = numero, texte, tables

    def extract_text(self):
        return self._texte

    def extract_tables(self):
        return self._tables

    def close(self):
        self.ferme = True


class _Pdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _faux_pdf(monkeypatch, pages):
    pdfplumber = pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")
    monkeypatch.setattr(pdfplumber, "open", lambda chemin: _Pdf(pages))


def test_la_campagne_se_lit_dans_le_document_et_les_entetes_sont_retires(monkeypatch, tmp_path):
    ligne = list(_ligne("900000001 00029", "2 000 €").values())
    _faux_pdf(monkeypatch, [
        _Page(1, "COMMISSION REGIONALE CONSULTATIVE\nRésultats de la campagne 2026",
              [[list(fdva.ENTETE), ligne]]),
        _Page(2, "", [[list(fdva.ENTETE), ligne]]),
    ])
    campagne, lignes = fdva.lire_pdf(tmp_path / "resultats-2025.pdf")
    assert campagne == 2026 and len(lignes) == 2
    assert lignes[0]["Montant accordé"] == "2 000 €"


def test_une_ligne_de_forme_inattendue_arrete_la_lecture(monkeypatch, tmp_path):
    _faux_pdf(monkeypatch, [_Page(1, "Résultats de la campagne 2026",
                                  [[list(fdva.ENTETE), ["1050", "DD30", "900000001"]]])])
    with pytest.raises(ValueError, match="forme inattendue"):
        fdva.lire_pdf(tmp_path / "r.pdf")


def test_sans_campagne_le_document_est_refuse(monkeypatch, tmp_path):
    _faux_pdf(monkeypatch, [_Page(1, "Un autre document", [[list(fdva.ENTETE)]])])
    with pytest.raises(ValueError, match="campagne introuvable"):
        fdva.lire_pdf(tmp_path / "r.pdf")


def test_magasin_vide_n_est_pas_une_panne(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fdva, "CACHE", tmp_path / "fdva")
    fdva.run()
    assert "aucun résultat FDVA déposé" in capsys.readouterr().out


def test_la_simulation_n_ecrit_rien_dans_la_base(monkeypatch, tmp_path, base, association):
    """`--dry-run` ouvre la base en lecture seule : ni table, ni entité, ni flux."""
    import sqlite3
    chemin_base = base.execute("PRAGMA database_list").fetchone()[2]
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF fictif")
    monkeypatch.setattr(fdva, "DB_PATH", chemin_base)
    monkeypatch.setattr(fdva, "lire_pdf",
                        lambda chemin: (2026, [_ligne("900000001 00029", "2 000 €")]))
    avant = sqlite3.connect(chemin_base).execute(
        "SELECT (SELECT COUNT(*) FROM sqlite_master), (SELECT COUNT(*) FROM entities)").fetchone()
    fdva.run(dry_run=True, fichiers=[pdf])
    apres = sqlite3.connect(chemin_base).execute(
        "SELECT (SELECT COUNT(*) FROM sqlite_master), (SELECT COUNT(*) FROM entities)").fetchone()
    assert apres == avant


@pytest.mark.parametrize("intitule", ["Atelier animé par Jean Dupont", "Hommage à Marie Curie",
                                      "ATELIER ANIME PAR JEAN DUPONT", "Fonctionnement global"])
def test_tout_flux_attend_l_atelier_et_ne_pose_aucune_relation(base, association, etat, intitule):
    """Relecture vigie-securite du 08/11 : le repérage des personnes laissait
    passer des noms en casse mixte, publiés en `verified`."""
    fdva.importer(base, [_ligne("900000001 00029", "650 €", intitule)], 2026, etat)
    (flux,) = _flux(base)
    assert flux["confidence"] == "probable"
    assert not base.execute("SELECT 1 FROM relations").fetchone()


def test_un_intitule_long_garde_son_numero_de_dossier(base, association, etat):
    long = "Projet " + "x" * 300
    lignes = [_ligne("900000001 00029", "650 €", long, dossier="26-000001"),
              _ligne("900000001 00029", "700 €", long, dossier="26-000002")]
    assert fdva.importer(base, lignes, 2026, etat)["inserees"] == 2
    assert all("dossier 26-00000" in f["description"] for f in _flux(base))


def test_chaque_page_est_fermee(monkeypatch, tmp_path):
    ligne = list(_ligne("900000001 00029", "2 000 €").values())
    pages = [_Page(1, "Résultats de la campagne 2026", [[ligne]]), _Page(2, "", [[ligne]])]
    _faux_pdf(monkeypatch, pages)
    fdva.lire_pdf(tmp_path / "r.pdf")
    assert all(getattr(p, "ferme", False) for p in pages)


def test_trop_de_pages_est_refuse(monkeypatch, tmp_path):
    _faux_pdf(monkeypatch, [_Page(i, "campagne 2026", []) for i in range(fdva.PAGES_MAX + 1)])
    with pytest.raises(ValueError, match="pages"):
        fdva.lire_pdf(tmp_path / "r.pdf")


def test_un_fichier_refuse_n_est_pas_relu_et_n_arrete_pas_les_autres(
        monkeypatch, tmp_path, base, association, etat):
    chemin_base = base.execute("PRAGMA database_list").fetchone()[2]
    mauvais, bon = tmp_path / "a.pdf", tmp_path / "b.pdf"
    mauvais.write_bytes(b"%PDF mauvais")
    bon.write_bytes(b"%PDF bon")
    lus = []

    def lire(chemin):
        lus.append(chemin.name)
        if chemin == mauvais:
            raise fdva.RefusFdva("forme inattendue")
        return 2026, [_ligne("900000001 00029", "2 000 €")]

    monkeypatch.setattr(fdva, "DB_PATH", chemin_base)
    monkeypatch.setattr(fdva, "lire_pdf", lire)
    monkeypatch.setattr(fdva, "pivot_ids", lambda conn: {"etat": etat})
    fdva.run(fichiers=[mauvais, bon])
    fdva.run(fichiers=[mauvais, bon])
    assert lus == ["a.pdf", "b.pdf"]
    import sqlite3
    c = sqlite3.connect(chemin_base)
    assert c.execute("SELECT fichier FROM fdva_refus").fetchall() == [("a.pdf",)]
    assert c.execute("SELECT COUNT(*) FROM financial_flows").fetchone()[0] == 1


def test_une_panne_n_est_pas_un_refus_et_ne_recopie_rien(monkeypatch, tmp_path, base, etat, capsys):
    """Une erreur qui n'est pas un refus du collecteur (pdfminer, droits,
    mémoire) se retente à la passe suivante, et son message n'est ni noté ni
    affiché : il pourrait citer le PDF."""
    chemin_base = base.execute("PRAGMA database_list").fetchone()[2]
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    lus = []

    def lire(chemin):
        lus.append(chemin.name)
        raise ValueError("invalid literal for int(): 'TEXTE DU PDF'")

    monkeypatch.setattr(fdva, "DB_PATH", chemin_base)
    monkeypatch.setattr(fdva, "lire_pdf", lire)
    monkeypatch.setattr(fdva, "pivot_ids", lambda conn: {"etat": etat})
    fdva.run(fichiers=[pdf])
    fdva.run(fichiers=[pdf])
    assert lus == ["a.pdf", "a.pdf"]
    assert "TEXTE DU PDF" not in capsys.readouterr().out
    import sqlite3
    assert sqlite3.connect(chemin_base).execute("SELECT COUNT(*) FROM fdva_refus").fetchone()[0] == 0


def test_un_fichier_trop_lourd_est_refuse_sans_etre_lu(monkeypatch, tmp_path, base, etat):
    chemin_base = base.execute("PRAGMA database_list").fetchone()[2]
    pdf = tmp_path / "gros.pdf"
    pdf.write_bytes(b"%PDF" + b"0" * 64)
    monkeypatch.setattr(fdva, "OCTETS_MAX", 10)
    monkeypatch.setattr(fdva, "DB_PATH", chemin_base)
    monkeypatch.setattr(fdva, "lire_pdf", lambda chemin: pytest.fail("lu malgré la taille"))
    monkeypatch.setattr(fdva, "pivot_ids", lambda conn: {"etat": etat})
    fdva.run(fichiers=[pdf])
    import sqlite3
    (motif,) = sqlite3.connect(chemin_base).execute("SELECT motif FROM fdva_refus").fetchone()
    assert "octets" in motif
