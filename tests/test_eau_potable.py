"""L'eau du robinet : réseaux desservis et contrôle sanitaire (Hub'Eau).

Aucun appel réseau. Les lignes reproduites ont la forme exacte que Hub'Eau a
rendue le 30/09/2026 sur `communes_udi` et `resultats_dis` pour Lasalle.

Ce que ces essais protègent :
  1. un prélèvement demandé au titre d'un réseau reste rattaché à ce réseau,
     même si la réponse ne le liste pas ;
  1 bis. la reprise d'un réseau ne se règle que sur SA requête : un prélèvement
     partagé, arrivé par un voisin, lui faisait sauter 2016-2020 (30/09/2026) ;
  2. un prélèvement commun à deux réseaux vaut pour les deux ;
  3. « <0,005 » n'est pas zéro : Hub'Eau publie 0.0 en numérique, le texte
     publié doit survivre ;
  4. rejouer une page n'écrit rien deux fois ;
  5. trois fractions d'un même paramètre (microcystine totale, dissoute, dans
     la biomasse) sont trois résultats, pas un ;
  6. interrogés par lot, les réseaux gardent chacun le suivi de SA requête ; une
     ligne qui ne cite aucun réseau du lot fait relire le lot réseau par réseau ;
  7. une pagination plus courte que le `count` annoncé fait échouer le lot ;
  8. un réseau qui ne répond pas fait échouer le step (Mascaras, 27/10/2026).
"""
from __future__ import annotations

import pytest

from collectors import eau_potable
from collectors.eau_potable import (LotAmbigu, avancer_suivi, depart, enregistrer_resultats,
                                    ensure_tables, fetch_resultats, ligne_udi, lire_lot)


def _resultat(prel="03000199114", param="7010", reseaux=("030000476",),
              date="2026-07-13T13:14:00Z", alpha="<0,005", num=0.0):
    return {
        "code_prelevement": prel, "code_parametre": param,
        "libelle_parametre": "Chlordane alpha", "code_type_parametre": "N",
        "code_lieu_analyse": "L", "resultat_alphanumerique": alpha,
        "resultat_numerique": num, "libelle_unite": "µg/L",
        "limite_qualite_parametre": "<=0,1 µg/L", "reference_qualite_parametre": None,
        "code_commune": "30329", "nom_commune": "THOIRAS-CORBÈS",
        "nom_uge": "SYNDICAT DE LASALLE", "nom_distributeur": "VEOLIA GARD - LOZÈRE",
        "nom_moa": "SYNDICAT DE LASALLE", "date_prelevement": date,
        "conclusion_conformite_prelevement": "Eau d'alimentation conforme.",
        "conformite_limites_bact_prelevement": "C",
        "conformite_limites_pc_prelevement": "C",
        "conformite_references_bact_prelevement": "C",
        "conformite_references_pc_prelevement": "N",
        "reseaux": [{"code": c, "nom": "—"} for c in reseaux],
    }


def _servir(module, pages, count=None):
    """Remplace Hub'Eau : rend `pages` dans l'ordre, chaînées par `next`."""
    total = sum(len(p) for p in pages) if count is None else count
    reponses = [{"count": total, "data": p, "next": f"page{i + 2}" if i + 1 < len(pages) else None}
                for i, p in enumerate(pages)]
    appels = []

    def lire(url, *a, **k):
        appels.append(url)
        return reponses[len(appels) - 1] if len(appels) <= len(reponses) else reponses[-1]
    module._get_json = lire
    return appels


@pytest.fixture(autouse=True)
def _hubeau_d_origine():
    origine = eau_potable._get_json
    yield
    eau_potable._get_json = origine


def _reseaux_de(base, prel):
    return {r[0] for r in base.execute(
        "SELECT code_reseau FROM eau_potable_prelevement_reseaux"
        " WHERE code_prelevement=?", (prel,))}


def test_un_quartier_absent_ne_casse_pas_la_cle():
    u = {"code_commune": "30140", "nom_quartier": None, "code_reseau": "030000473",
         "nom_reseau": "LASALLE", "debut_alim": "2010-12-30", "annee": "2026"}
    assert ligne_udi(u)[2] == ""


def test_le_reseau_interroge_est_toujours_rattache(base):
    ensure_tables(base)
    enregistrer_resultats(base, [_resultat(reseaux=())], "030000476")
    assert _reseaux_de(base, "03000199114") == {"030000476"}


def test_un_prelevement_commun_vaut_pour_les_deux_reseaux(base):
    ensure_tables(base)
    enregistrer_resultats(base, [_resultat(reseaux=("030000473", "030008363"))],
                          "030000473")
    assert _reseaux_de(base, "03000199114") == {"030000473", "030008363"}


def test_sous_le_seuil_n_est_pas_zero(base):
    ensure_tables(base)
    enregistrer_resultats(base, [_resultat()], "030000476")
    alpha, num = base.execute(
        "SELECT resultat_alpha, resultat_numerique FROM eau_potable_resultats").fetchone()
    assert (alpha, num) == ("<0,005", 0.0)


def test_rejouer_une_page_n_ecrit_rien_deux_fois(base):
    ensure_tables(base)
    page = [_resultat(param="7010"), _resultat(param="1340")]
    assert enregistrer_resultats(base, page, "030000476") == 2
    assert enregistrer_resultats(base, page, "030000476") == 0
    assert base.execute("SELECT COUNT(*) FROM eau_potable_prelevements").fetchone()[0] == 1


def test_la_reprise_part_du_dernier_prelevement_lu_pour_le_reseau(base):
    ensure_tables(base)
    page = [_resultat(prel="A", date="2024-03-01T08:00:00Z"),
            _resultat(prel="B", date="2026-07-13T13:14:00Z")]
    _servir(eau_potable, [page])
    fetch_resultats(base, ["030000476"], "2016-01-01")
    assert depart(base, "030000476", None) == "2026-07-13"
    assert depart(base, "030000476", "2020-01-01") == "2020-01-01"


def test_un_rattachement_venu_d_un_voisin_ne_fait_pas_sauter_l_histoire(base):
    """Le bourg (473) rapporte un prélèvement de 2026 partagé avec Thoiras
    (476) : Thoiras n'a encore rien lu lui-même, il repart de 2016."""
    ensure_tables(base)
    _servir(eau_potable, [[_resultat(reseaux=("030000473", "030000476"))]])
    fetch_resultats(base, ["030000473"], "2016-01-01")
    assert "030000476" in _reseaux_de(base, "03000199114")
    assert depart(base, "030000476", None) == "2016-01-01"


def test_le_suivi_ne_recule_jamais(base):
    ensure_tables(base)
    avancer_suivi(base, "030000476", "2025-06-01T08:00:00Z")
    avancer_suivi(base, "030000476", "2019-01-01T08:00:00Z")
    avancer_suivi(base, "030000476", None)
    assert depart(base, "030000476", None) == "2025-06-01"


def test_trois_fractions_d_un_meme_parametre_sont_trois_resultats(base):
    ensure_tables(base)
    page = [dict(_resultat(param="5489"), libelle_parametre=f"Microcystine-YR {f}")
            for f in ("totale", "dissoute", "dans la biomasse")]
    assert enregistrer_resultats(base, page, "030000476") == 3


# ── La publication (`export_eau_potable`) ────────────────────────────────────
# Ce que ces tests protègent : un réseau voisin, arrivé par un prélèvement
# partagé, publié comme desservant la commune ; un réseau sans prélèvement
# présenté comme conforme.

def _desserte(base, annee, commune, reseau, quartier="le village"):
    base.execute("INSERT INTO eau_potable_udi (annee, code_commune, nom_quartier, code_reseau,"
                 " nom_reseau) VALUES (?,?,?,?,?)", (annee, commune, quartier, reseau, f"Réseau {reseau}"))


def test_la_publication_ne_retient_que_les_reseaux_de_la_derniere_desserte(base):
    from scripts.build_public_snapshot import export_eau_potable
    ensure_tables(base)
    _desserte(base, "2025", "99001", "ANCIEN")
    _desserte(base, "2026", "99001", "BOURG")
    _desserte(base, "2026", "99001", "MUET", quartier="-")
    _desserte(base, "2026", "99002", "VOISIN")
    for prel, date, bact in (("P1", "2025-03-01T10:00:00Z", "C"), ("P2", "2026-06-25T10:00:00Z", "N")):
        r = _resultat(prel=prel, reseaux=("BOURG", "VOISIN"), date=date)
        r["conformite_limites_bact_prelevement"] = bact
        enregistrer_resultats(base, [r], "BOURG")
    base.commit()

    e = export_eau_potable(base, "99001")

    assert e["annee_desserte"] == "2026"
    bourg, muet = e["reseaux"]
    assert (bourg["code"], bourg["quartiers"], bourg["prelevements"]) == ("BOURG", ["le village"], 2)
    assert (bourg["bacteriologie"], bourg["chimie"]) == (1, 0)
    assert bourg["hors_limites"] == [{"date": "2026-06-25", "bacteriologie": 1, "chimie": 0}]
    assert bourg["maitre_ouvrage"] == "SYNDICAT DE LASALLE"
    assert [a["annee"] for a in bourg["par_annee"]] == ["2025", "2026"]
    # Desservi, jamais prélevé : zéro prélèvement, et surtout pas « conforme ».
    assert (muet["prelevements"], muet["quartiers"], muet["hors_limites"]) == (0, [], [])
    assert muet["du"] is None


def test_une_commune_sans_desserte_connue_ne_publie_rien(base):
    from scripts.build_public_snapshot import export_eau_potable
    assert export_eau_potable(base, "99001") is None, "table absente"
    ensure_tables(base)
    assert export_eau_potable(base, "99001") is None


def test_un_lot_garde_le_suivi_de_chaque_reseau(base):
    """Lot (473, 476) : 476 n'est cité que par un prélèvement de 2018, 473 par
    un de 2026. Chacun repart de ce que SA requête aurait rendu."""
    ensure_tables(base)
    appels = _servir(eau_potable, [[_resultat(prel="A", reseaux=("030000476",),
                                              date="2018-05-02T08:00:00Z")],
                                   [_resultat(prel="B", reseaux=("030000473",))]])
    fetch_resultats(base, ["030000473", "030000476"], "2016-01-01")
    assert "code_reseau=030000473%2C030000476" in appels[0]
    assert len(appels) == 2, "une requête paginée pour le lot, pas une par réseau et par année"
    assert depart(base, "030000473", None) == "2026-07-13"
    assert depart(base, "030000476", None) == "2018-05-02"
    assert _reseaux_de(base, "A") == {"030000476"}


def test_un_reseau_du_lot_sans_ligne_garde_sa_reprise(base):
    ensure_tables(base)
    _servir(eau_potable, [[_resultat(reseaux=("030000473",))]])
    fetch_resultats(base, ["030000473", "030000476"], "2016-01-01")
    assert depart(base, "030000476", None) == "2016-01-01"


def test_une_ligne_sans_reseau_du_lot_fait_relire_reseau_par_reseau(base):
    ensure_tables(base)
    ligne = _resultat(reseaux=())
    with pytest.raises(LotAmbigu):
        enregistrer_resultats(base, [ligne], ["030000473", "030000476"])
    assert base.execute("SELECT COUNT(*) FROM eau_potable_prelevements").fetchone()[0] == 0
    appels = []
    eau_potable._get_json = lambda url, *a, **k: (appels.append(url)
                                                  or {"count": 1, "data": [ligne], "next": None})
    lire_lot(base, ["030000473", "030000476"], "2016-01-01")
    assert len(appels) == 3
    assert "code_reseau=030000476&" in appels[2]
    assert _reseaux_de(base, "03000199114") == {"030000473", "030000476"}


def test_une_pagination_coupee_fait_echouer_le_lot(base):
    ensure_tables(base)
    _servir(eau_potable, [[_resultat()]], count=20001)
    with pytest.raises(RuntimeError, match="20001"):
        fetch_resultats(base, ["030000476"], "2016-01-01")
    assert depart(base, "030000476", None) == "2016-01-01", "le suivi n'avance pas"


def test_un_reseau_muet_fait_echouer_le_step(base, monkeypatch):
    ensure_tables(base)
    monkeypatch.setattr(eau_potable, "get_conn", lambda: _SansFermer(base))
    monkeypatch.setattr(eau_potable, "communes_du_step", lambda step: ["65303"])
    udi = {"code_commune": "65303", "nom_quartier": None, "code_reseau": "065000767",
           "nom_reseau": "ARROS-LUC", "debut_alim": None, "annee": "2026"}

    def lire(url, *a, **k):
        if "communes_udi" in url:
            return {"count": 1, "data": [udi], "next": None}
        raise OSError("HTTP Error 503: Service Unavailable")
    eau_potable._get_json = lire
    with pytest.raises(RuntimeError, match="065000767"):
        eau_potable.run()


class _SansFermer:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nom):
        return getattr(self._conn, nom)

    def close(self):
        pass
