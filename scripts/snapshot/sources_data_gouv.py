"""Les six sources data.gouv du 29/10/2026, prêtes pour les pages.

ENAF, loyers, RPLS et cantines sortent dans `territoire.json` ; VigiEau et ODRÉ
dans `environnement.json`. Chaque export qualifie ce qu'il rend, et applique
les exigences de l'avis DPO du 29/10/2026 :

  - ODRÉ : par filière seulement (nombre, puissance, énergie). Aucun code EIC,
    qui redonne en une requête le nom d'une installation, souvent celui d'un
    exploitant ; aucune ligne individuelle.
  - cantines : seules les cantines publiques sont nommées. Ni SIRET (celui
    d'une cuisine centrale peut être celui d'un entrepreneur individuel), ni
    identifiant ma-cantine ; les cantines privées ne sortent qu'en compte.
  - RPLS : sous trois logements dans la commune, le total seul — les
    répartitions décriraient un logement que tout le village situe.
  - loyers : la mention imposée par le producteur voyage avec le chiffre.

Chaque bloc porte l'URL du fichier lu et la fiche du jeu : un chiffre, une source.
"""
from __future__ import annotations

from collectors import cantines as _cantines
from collectors import enaf as _enaf
from collectors import loyers as _loyers
from collectors import odre as _odre
from collectors import rpls as _rpls
from collectors import vigieau as _vigieau
from scripts.snapshot.socle import rows, table_exists

# Sous ce nombre de logements sociaux dans la commune, seul le total sort.
RPLS_SEUIL_REPARTITION = 3

# Le SDES nomme ensemble un financement de métropole et son équivalent
# d'outre-mer (« PLUS/LLS dans les DOM ») : lu à Saillans, le libellé laissait
# croire à des logements d'outre-mer. Hors des DOM, le nom de métropole seul,
# développé ; dans un DOM, le libellé du répertoire tel quel.
FINANCEMENTS_METROPOLE = {
    "PLA d’intégration/LLTS dans les DOM": "PLAI (prêt locatif aidé d’intégration)",
    "PLUS/LLS dans les DOM": "PLUS (prêt locatif à usage social)",
    "PLS/PPLS/PCLS/PLA CFF": "PLS (prêt locatif social) et prêts voisins",
    "PLI": "PLI (prêt locatif intermédiaire)",
    "HLM/O": "HLM ordinaire (HLM/O)",
    "ILM": "ILM (immeuble à loyer moyen)",
    "ILN": "ILN (immeuble à loyer normal)",
    "PLA Loyer Minoré / PLA Très Social / PLA Insertion":
        "PLA à loyer minoré, très social ou d’insertion",
}
# Les tranches de surface dans l'ordre des surfaces, pas dans celui de l'alphabet.
ORDRE_SURFACES = [libelle for _, libelle in _rpls.TRANCHES_SURFACE] + ["90 m² et plus",
                                                                     "non renseigné"]

# Sous ce nombre d'installations dans une filière, l'énergie injectée n'est pas
# donnée : une ligne d'une ou deux installations désigne un exploitant que le
# village connaît, et l'énergie vendue dit son revenu. Le nombre et la
# puissance, visibles sur place, restent.
ODRE_SEUIL = 3

# Les seuils de la loi EGalim (code rural, art. L230-5-1), « en valeur »,
# applicables aux restaurants collectifs PUBLICS depuis le 1er janvier 2022 (au
# privé depuis 2024) : une déclaration antérieure ne s'y compare pas. Seules les
# cantines publiques sont nommées, donc comparées. Les sous-seuils (60 % viandes
# et poissons depuis 2024, 30 % bio et signes de qualité depuis le 20/08/2026)
# ne se lisent pas dans les fichiers publiés. Vérifié le 29/10/2026 sur le texte
# consolidé et le JORF (vigie-verificateur) ; la part durable est bien
# ratio_bio + ratio_egalim_hors_bio, comme le calcule ma cantine.
EGALIM_DURABLE, EGALIM_BIO, EGALIM_DEPUIS = 0.5, 0.2, 2022


def export_enaf(conn, insee: str) -> dict | None:
    """La consommation d'ENAF année par année, et les deux périodes du ZAN."""
    if not table_exists(conn, "enaf_consommation"):
        return None
    lignes = rows(conn, """
        SELECT annee, destination, m2, millesime, source_url FROM enaf_consommation
         WHERE insee=? ORDER BY annee""", (insee,))
    if not lignes:
        return None
    serie: dict[int, dict] = {}
    for l in lignes:
        serie.setdefault(l["annee"], {"annee": l["annee"]})[l["destination"]] = l["m2"]
    totaux = {a: s.get("total") or 0 for a, s in serie.items()}
    return {
        "serie": list(serie.values()),
        # Du 1er janvier 2011 au 1er janvier 2021 : la période de référence de
        # la loi Climat et résilience ; depuis : ce qui s'impute sur la suivante.
        "decennie_2011_2021_m2": sum(m for a, m in totaux.items() if 2011 <= a <= 2020),
        "depuis_2021_m2": sum(m for a, m in totaux.items() if a >= 2021),
        "millesime": lignes[-1]["millesime"],
        "source_url": lignes[-1]["source_url"],
        "page_jeu": _enaf.PAGE_JEU,
    }


def export_loyers(conn, insee: str) -> dict | None:
    if not table_exists(conn, "loyers_annonces"):
        return None
    lignes = rows(conn, """
        SELECT millesime, type_bien, loyer_m2, bas_m2, haut_m2, echelle,
               annonces_commune, annonces_maille, source_url
          FROM loyers_annonces WHERE insee=? AND millesime=(
               SELECT MAX(millesime) FROM loyers_annonces WHERE insee=?)
         ORDER BY type_bien""", (insee, insee))
    if not lignes:
        return None
    millesime = lignes[0]["millesime"]
    _, slug = _loyers.JEUX.get(millesime, (None, ""))
    return {
        "millesime": millesime,
        "biens": lignes,
        "mention": _loyers.MENTION,
        "page_jeu": f"https://www.data.gouv.fr/datasets/{slug}" if slug else None,
    }


def export_rpls(conn, insee: str) -> dict | None:
    if not table_exists(conn, "rpls_communes"):
        return None
    lignes = rows(conn, """
        SELECT millesime, dimension, modalite, logements, source_url FROM rpls_communes
         WHERE insee=? AND millesime=(SELECT MAX(millesime) FROM rpls_communes WHERE insee=?)
    """, (insee, insee))
    if not lignes:
        return None
    total = next((l["logements"] for l in lignes if l["dimension"] == "total"), 0)
    repartitions: dict[str, list] = {}
    outre_mer = insee.startswith("97")

    def rang(l):
        if l["dimension"] == "surface" and l["modalite"] in ORDRE_SURFACES:
            return (l["dimension"], ORDRE_SURFACES.index(l["modalite"]), "")
        return (l["dimension"], 0, l["modalite"])

    if total >= RPLS_SEUIL_REPARTITION:
        for l in sorted(lignes, key=rang):
            if l["dimension"] == "total":
                continue
            modalite = l["modalite"]
            if l["dimension"] == "financement" and not outre_mer:
                modalite = FINANCEMENTS_METROPOLE.get(modalite, modalite)
            repartitions.setdefault(l["dimension"], []).append(
                {"modalite": modalite, "logements": l["logements"]})
    return {
        "millesime": lignes[0]["millesime"],      # « 2025-01 » : au 1er janvier 2025
        "total": total,
        "repartitions": repartitions,
        "repartitions_masquees": 0 < total < RPLS_SEUIL_REPARTITION,
        "source_url": lignes[0]["source_url"],
        "page_jeu": _rpls.PAGE_JEU,
    }


def export_cantines(conn, insee: str) -> dict | None:
    """Les cantines publiques nommées, leurs taux déclarés ; les privées comptées."""
    if not table_exists(conn, "cantines_registre"):
        return None
    registre = rows(conn, """
        SELECT id, nom, modele, gestion, production, secteurs, repas_an,
               annees_declarees, source_url
          FROM cantines_registre WHERE insee=? ORDER BY nom""", (insee,))
    avec_td = table_exists(conn, "cantines_teledeclarations")
    declarations = rows(conn, """
        SELECT annee, cantine_id, nom, mode, satellites, part_bio, part_egalim_hors_bio
          FROM cantines_teledeclarations
         WHERE insee=? AND modele='public' ORDER BY annee, nom
    """, (insee,)) if avec_td else []
    # La dernière campagne publiée, toutes communes : une cantine déclarée pour
    # une année plus récente attend la publication, elle n'a pas « disparu ».
    derniere = (rows(conn, "SELECT MAX(annee) a FROM cantines_teledeclarations")[0]["a"]
                if avec_td else None)
    if not registre and not declarations:
        return None
    publiques = [c for c in registre if c["modele"] == "public"]
    privees = [c for c in registre if c["modele"] != "public"]
    # Rapprochées par l'identifiant ma-cantine, jamais par SIRET : plusieurs
    # cantines partagent celui de la mairie, et un SIRET vide les mêlerait.
    # L'identifiant sert ici, il ne sort pas.
    par_cantine: dict[int, list] = {}
    for d in declarations:
        egalim = (None if d["part_bio"] is None or d["part_egalim_hors_bio"] is None
                  else round(d["part_bio"] + d["part_egalim_hors_bio"], 4))
        par_cantine.setdefault(d["cantine_id"], []).append({
            "annee": d["annee"], "nom": d["nom"], "mode": d["mode"],
            "satellites": d["satellites"], "part_bio": d["part_bio"],
            "part_durable": egalim,
            "comparable_aux_seuils": d["annee"] >= EGALIM_DEPUIS,
        })
    ids_du_registre = {c["id"] for c in publiques}
    return {
        "cantines": [{
            "nom": c["nom"], "secteurs": c["secteurs"], "repas_an": c["repas_an"],
            "gestion": c["gestion"], "production": c["production"],
            "livree": c["production"] == "site_cooked_elsewhere",
            "annees_declarees": [int(a) for a in (c["annees_declarees"] or "").split(",") if a],
            "declarations": par_cantine.get(c["id"], []),
        } for c in publiques],
        # Une cuisine centrale de la commune qui déclare pour ses satellites
        # sans être elle-même au registre de la commune.
        "autres_declarations": [d for i, ds in par_cantine.items()
                                if i not in ids_du_registre for d in ds],
        # Le nombre seul : avec une cantine privée, ses repas la désigneraient.
        "privees": {"nombre": len(privees)},
        "derniere_campagne_publiee": derniere,
        "seuils": {"durable": EGALIM_DURABLE, "bio": EGALIM_BIO, "depuis": EGALIM_DEPUIS},
        "source_url": registre[0]["source_url"] if registre else None,
        "page_registre": _cantines.PAGE_REGISTRE,
        "page_teledeclarations": _cantines.PAGE_TD,
    }


def export_vigieau(conn, insee: str) -> dict | None:
    if not table_exists(conn, "vigieau_jours"):
        return None
    lignes = rows(conn, """
        SELECT annee, zone, niveau, jours, dernier_jour, source_url FROM vigieau_jours
         WHERE insee=? ORDER BY annee""", (insee,))
    if not lignes:
        return None
    annees: dict[int, dict] = {}
    for l in lignes:
        a = annees.setdefault(l["annee"], {"annee": l["annee"], "dernier_jour": l["dernier_jour"],
                                           "zones": {}})
        a["zones"].setdefault(l["zone"], {})[l["niveau"]] = l["jours"]
        a["dernier_jour"] = max(a["dernier_jour"], l["dernier_jour"])
    for a in annees.values():
        a["jours_releves"] = max(sum(z.values()) for z in a["zones"].values())
        # La ressource la plus restreinte de l'année, et elle seule pour le
        # titre. Lire l'eau potable seule faisait un zéro d'une absence : les
        # zones « eau potable » n'existent dans le fichier que depuis peu, et
        # Saillans affichait 0 jour en 2022, rivières et nappes en crise.
        restreints = {z: sum(n for niv, n in c.items() if niv != "aucune")
                      for z, c in a["zones"].items()}
        plus = max(restreints, key=lambda z: (restreints[z], a["zones"][z].get("crise", 0)))
        a["plus_touchee"] = {"zone": plus, "jours": restreints[plus],
                             "crise": a["zones"][plus].get("crise", 0)}
    return {
        "annees": list(annees.values()),
        "zones": _vigieau.ZONES,
        "source_url": lignes[-1]["source_url"],
        "page_jeu": _vigieau.PAGE_JEU,
    }


def export_odre(conn, insee: str) -> dict | None:
    """Par filière : nombre d'installations, puissance, énergie. Rien d'individuel."""
    if not table_exists(conn, "odre_installations"):
        return None
    filieres = rows(conn, """
        SELECT COALESCE(filiere, 'Non précisée') AS filiere,
               SUM(COALESCE(nb_installations, 1)) AS installations,
               ROUND(SUM(puissance_kw), 1) AS puissance_kw,
               ROUND(SUM(energie_injectee_kwh)) AS energie_injectee_kwh,
               MAX(date_registre) AS date_registre
          FROM odre_installations
         WHERE COALESCE(NULLIF(insee_implantation, ''), insee) = ?
         GROUP BY 1 ORDER BY puissance_kw DESC""", (insee,))
    date = (rows(conn, "SELECT MAX(date_registre) d FROM odre_installations") or [{}])[0].get("d")
    if not date:
        return None
    for f in filieres:
        f.pop("date_registre", None)
        f["energie_masquee"] = f["installations"] < ODRE_SEUIL
        if f["energie_masquee"]:
            f["energie_injectee_kwh"] = None
    # Une commune absente d'un registre exhaustif n'a aucune installation : une
    # liste vide sort, avec la date du registre lu, et la page le dit.
    return {"filieres": filieres, "date_registre": date, "page_jeu": _odre.PAGE_JEU}
