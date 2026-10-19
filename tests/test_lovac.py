"""LOVAC : « s » n'est pas zéro, et la série ne se lit pas d'un bloc.

Aucun appel réseau. L'extrait est FICTIF — deux communes inventées, au format
exact du fichier des communes (point-virgule, guillemets, colonnes suffixées
par le millésime, ISO-8859-1).

Ce que ces essais protègent :
  1. une cellule « s » (moins de 11 logements) reste « s » de la lecture à
     l'export, sans jamais devenir 0 ni disparaître ;
  2. une colonne que le millésime ne publie pas ne fabrique aucune valeur ;
  3. les colonnes sont reconnues par MOTIF : le dépôt suivant changera le
     suffixe du code commune, et `pp_vacant` ne doit pas avaler
     `pp_vacant_plus_2ans` ;
  4. l'export porte les ruptures de méthode avec la série.
"""
from __future__ import annotations

import sqlite3

from collectors.lovac import RUPTURES, SECRET, _colonne, ensure_table, lire
from scripts.snapshot.territoire import export_lovac

ENTETE = ('"CODGEO_26";"LIBGEO_26";"pp_vacant_26";"pp_vacant_plus_2ans_26";'
          '"ff_pp_total_25";"pp_vacant_25";"pp_vacant_plus_2ans_25";'
          '"ff_pp_total_24";"pp_vacant_24";"pp_vacant_plus_2ans_24";'
          '"EPCI_26";"LIB_EPCI_26";"DEP";"LIB_DEP";"REG";"LIB_REG"')
EXTRAIT = "\r\n".join([
    ENTETE,
    '"99001";"Bourg-Fictif";"48";"21";"610";"44";"19";"605";"52";"23";'
    '"999999999";"CC Imaginaire";"99";"Nulle-Part";"99";"Région Fictive"',
    '"99002";"Hameau-Témoin";"s";"s";"95";"12";"s";"94";"s";"s";'
    '"999999999";"CC Imaginaire";"99";"Nulle-Part";"99";"Région Fictive"',
    '"99003";"Hors-Champ";"30";"12";"200";"25";"11";"198";"27";"13";'
    '"999999999";"CC Imaginaire";"99";"Nulle-Part";"99";"Région Fictive"',
]) + "\r\n"


def _par_cle(lignes):
    return {(l[0], l[1], l[2]): (l[3], l[4]) for l in lignes}


class TestColonnes:
    def test_le_prefixe_long_n_est_pas_avale(self):
        assert _colonne("pp_vacant_plus_2ans_26") == ("vacants_plus_2ans", 2026)
        assert _colonne("pp_vacant_26") == ("vacants", 2026)
        assert _colonne("ff_pp_total_25") == ("parc_prive", 2025)

    def test_un_millesime_futur_est_reconnu(self):
        assert _colonne("pp_vacant_27") == ("vacants", 2027)

    def test_les_colonnes_de_zonage_ne_sont_pas_des_series(self):
        for nom in ("CODGEO_26", "LIBGEO_26", "EPCI_26", "LIB_EPCI_26", "DEP", "REG"):
            assert _colonne(nom) is None


class TestLecture:
    def test_seules_les_communes_demandees_sortent(self):
        lignes = lire(EXTRAIT, {"99001", "99002"})
        assert {l[0] for l in lignes} == {"99001", "99002"}

    def test_s_reste_s_et_n_est_pas_zero(self):
        v = _par_cle(lire(EXTRAIT, {"99002"}))
        assert v[("99002", 2026, "vacants")] == (SECRET, None)
        assert v[("99002", 2025, "vacants_plus_2ans")] == (SECRET, None)
        assert v[("99002", 2025, "vacants")] == ("12", 12)

    def test_une_colonne_non_publiee_ne_fait_aucune_valeur(self):
        v = _par_cle(lire(EXTRAIT, {"99001"}))
        assert ("99001", 2026, "parc_prive") not in v
        assert v[("99001", 2025, "parc_prive")] == ("610", 610)

    def test_le_code_commune_change_de_suffixe(self):
        texte = EXTRAIT.replace("CODGEO_26", "CODGEO_27")
        assert {l[0] for l in lire(texte, {"99001"})} == {"99001"}


class TestExport:
    def _base(self, tmp_path) -> sqlite3.Connection:
        conn = sqlite3.connect(tmp_path / "lovac.db")
        conn.row_factory = sqlite3.Row
        ensure_table(conn)
        conn.executemany(
            "INSERT INTO lovac_vacance (insee, millesime, indicateur, brut, valeur, source_url)"
            " VALUES (?,?,?,?,?,?)",
            [(*l, "https://exemple.invalid/lovac.csv")
             for l in lire(EXTRAIT, {"99001", "99002"})])
        return conn

    def test_s_voyage_jusqu_a_l_export(self, tmp_path):
        export = export_lovac(self._base(tmp_path), "99002")
        dernier = export["serie"][-1]
        assert dernier["millesime"] == 2026
        assert dernier["vacants"] == SECRET
        assert "parc_prive" not in dernier
        assert export["serie"][0]["vacants"] == SECRET  # 2024

    def test_la_serie_est_dans_l_ordre_et_porte_ses_ruptures(self, tmp_path):
        export = export_lovac(self._base(tmp_path), "99001")
        assert [m["millesime"] for m in export["serie"]] == [2024, 2025, 2026]
        assert [r["millesime"] for r in export["ruptures"]] == sorted(RUPTURES)
        assert {2023, 2025} <= set(RUPTURES)

    def test_une_commune_absente_n_exporte_rien(self, tmp_path):
        assert export_lovac(self._base(tmp_path), "99003") is None
