#!/usr/bin/env python3
"""Défait les séances qui servent une pièce de l'AUTRE assemblée.

    python3 scripts/detacher_pieces_autre_assemblee.py                # simulation, TSV
    python3 scripts/detacher_pieces_autre_assemblee.py --appliquer    # sauvegarde, puis écrit

À lancer depuis le dossier de l'instance (la base est celle de `collectors.config`),
ou avec `--db`, COLLECTE ARRÊTÉE (minuteur de l'instance). SIMULATION par défaut :
rien n'est écrit en base, la liste part dans un TSV (identifiants, dates, URL de
documents publiés, décomptes ; aucun nom).

Pourquoi elles existent
-----------------------
Le 29/08/2026, `redecouper_pv.deja_en_cache` a relu les procès-verbaux de la CC
en portée COMMUNALE (corrigé par `674f7d8`). `vigie_retirer_doublons_portee.py`
a retiré en septembre les 833 actes nés de ce passage, mais sa requête ne
visait que le type `deliberation` : les SÉANCES du conseil municipal créées par
le même passage sont restées. Puis `fusionner_seances.py` (15/09) a fondu ces
fiches avec la vraie séance communale du même jour quand il y en avait une, et
le PV de la CC, mieux classé qu'un compte rendu, est devenu sa pièce principale.
Relevé sur les sites servis le 07/11/2026 : 43 séances à Lasalle (32 sans
séance communale réelle ce jour-là), 44 à Brassac (toutes).

Ce que fait le script
---------------------
Une URL tenue à la fois par une séance municipale et par une séance
communautaire DU MÊME JOUR (en source ou en pièce) appartient à une seule
assemblée :
  1. celle dont des délibérations citent cette URL, si une seule le fait ;
  2. sinon celle dont le site déclaré (`commune_url`, `epci_url`) porte le
     domaine de l'URL ;
  3. sinon on ne devine pas : `?`, à trancher par un humain AVANT republication.

La séance de l'autre assemblée perd la pièce. Elle est alors :
  - `retirer`  : il ne lui reste ni pièce ni délibération ce jour-là, et rien ne
                 s'y accroche — une séance qui n'a pas eu lieu ; ses liens de
                 présence partent avec elle (cascade) ;
  - `retenu`   : même cas, mais une table la cite, ou des délibérations : la
                 séance reste, la pièce étrangère sort quand même ;
  - `detacher` : il lui reste une pièce à elle ; la pièce étrangère sort, la
                 source passe à la plus probante qui reste.
Pour `retenu` et `detacher`, ce que la séance a pu lire dans le PV étranger est
effacé, faute de savoir quelle pièce l'a écrit (les métadonnées FUSIONNENT) :
présences et pouvoirs, liens `event_entities`, `content`. Ensuite, relire les
pièces restantes : `python3 collectors/run_all.py --step conseils` (la garde de
`conseils.traiter` écarte désormais le PV de l'autre assemblée).

Orphelins (ajouté le 08/11/2026)
--------------------------------
Le même passage du 29/08 a laissé à Saillans des pièces de la CC SANS séance
communautaire qui les tienne : 37 délibérations « commune » et deux séances du
conseil municipal (25/06, 30/06/2026) servant les PDF du portail d'actes de la CC
(`cccps.dematdoc.eu`, référence @ctes au SIREN de la CC). Ni la reprise de
septembre (elle exigeait un jumeau `deliberation_cc` à la même URL) ni le
rapprochement ci-dessus (il exige une URL partagée) ne les voient. Une URL dont
le domaine est celui d'un site DÉCLARÉ de l'autre assemblée seule (`epci_url`,
`commune_url`, `pages.<portée>.portail`) lui appartient donc aussi sans
séance partagée :
  - `retirer_acte` : la délibération sort (ses liens partent en cascade) ;
                     si une table la cite, `?`, à trancher ;
  - la séance perd la pièce comme ci-dessus (`retirer`, `retenu`, `detacher`).
Et `provenance` : une séance dont la pièce étrangère est déjà partie (reprise
de #224) mais dont `source` nomme encore l'autre assemblée (Brassac, séance
du 09/03/2026, `source=ccsvp.fr` pour un PV de brassac.fr). Un détachement
recalcule désormais `source` avec `source_url`.

Ordre : déployer la garde et lancer ce script dans le même geste. Tant qu'une
séance municipale tient un PV de la CC, la garde écarte ce PV côté CC aussi.
Puis : relance de la simulation → 0 ligne ; `scripts/comparer_snapshots.py` ;
republier. Le site ne change pas avant.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import urllib.parse
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import conseils  # noqa: E402
from collectors import config  # noqa: E402
from collectors.sauvegarde import sauvegarder  # noqa: E402

SEANCE = {p: conseils.PORTEES[p]["seance"] for p in conseils.PORTEES}
DELIB = {p: conseils.PORTEES[p]["delib"] for p in conseils.PORTEES}
AUTRE = {"commune": "epci", "epci": "commune"}
TYPES_ANNOTES_NON_EVENEMENT = ("entity", "relation", "flow", "marche")
#: Domaines de portail d'actes qu'un humain a vérifiés (référence @ctes au SIREN de
#: l'autre assemblée sur chaque PDF du plan) : `--portail-verifie`. Un portail peut
#: être mutualisé avec les communes membres ; sans cette vérification, une URL qui
#: n'est étrangère QUE par le portail reste `?` (relecture vigie-securite de #230).
PORTAILS_VERIFIES: set[str] = set()
#: Ce qu'une lecture de pièce écrit dans la séance (`conseils.traiter`).
LU_DANS_LA_PIECE = ("presents", "absents", "pouvoirs", "secretaire", "nb_deliberations",
                    "format", "ocr", "suite_des_actes", "decoupage_delib", "libelle_source",
                    "ecarts_du_decoupage")
#: Colonnes qui citent un acte SANS clé étrangère déclarée.
CITATIONS_SANS_CLE = (("propositions", "object_id"), ("budget_vote", "source_event_id"))


def _domaine(url: str | None) -> str:
    return urllib.parse.urlparse(url or "").netloc.lower().removeprefix("www.")


def _hote(source: str | None) -> str:
    """La colonne `source` porte un domaine nu (`brassac.fr`), pas une URL."""
    s = (source or "").strip().lower()
    return _domaine(s) if "://" in s else s.removeprefix("www.")


def _sites(portee: str) -> set[str]:
    """Domaines où une assemblée publie : son site et son portail d'actes déclarés."""
    site = config.COMMUNE_URL if portee == "commune" else config.EPCI_URL
    portail = ((config.PAGES or {}).get(portee) or {}).get("portail")
    return {d for d in (_domaine(site), _domaine(portail)) if d}


def _etrangere(url: str | None, portee: str) -> bool:
    """L'URL est publiée par l'AUTRE assemblée, et par elle seule."""
    dom = _domaine(url)
    return bool(dom) and dom in _sites(AUTRE[portee]) and dom not in _sites(portee)


def _par_portail_seul(url: str | None, portee: str) -> bool:
    """Étrangère seulement parce que son domaine est un portail d'actes de l'autre
    assemblée (pas son site), et ce portail n'a pas été vérifié."""
    dom = _domaine(url)
    autre = AUTRE[portee]
    site = _domaine(config.COMMUNE_URL if autre == "commune" else config.EPCI_URL)
    return dom != site and dom not in PORTAILS_VERIFIES


def _source_de(portee: str, url: str | None) -> str:
    """`source` d'une séance d'après sa pièce. Une capture d'archive reste publiée
    par l'assemblée (cf. `redecouper_pv.deja_en_cache`) : son site, pas l'archive."""
    dom = _domaine(url)
    if dom and dom != "web.archive.org":
        return dom
    return _domaine(config.COMMUNE_URL if portee == "commune" else config.EPCI_URL)


def _seances(conn, portee: str) -> dict[str, list[dict]]:
    """{date: [{id, source_url, pieces, urls}]} pour une assemblée."""
    out: dict[str, list[dict]] = {}
    for eid, d, src, source, meta in conn.execute(
            "SELECT id, date, source_url, source, metadata FROM events WHERE type=?",
            (SEANCE[portee],)):
        try:
            m = json.loads(meta or "{}")
        except json.JSONDecodeError:
            m = {}
        pieces = m.get("pieces") or []
        urls = ({p.get("url") for p in pieces} | {src}) - {None}
        out.setdefault(d, []).append({"id": eid, "source_url": src, "source": source,
                                      "pieces": pieces, "urls": urls})
    return out


def proprietaire(conn, url: str) -> str:
    cites = {p for p in DELIB if conn.execute(
        "SELECT 1 FROM events WHERE type=? AND source_url=? LIMIT 1",
        (DELIB[p], url)).fetchone()}
    if len(cites) == 1:
        return cites.pop()
    dom = _domaine(url)
    par_site = {p for p in ("commune", "epci") if dom in _sites(p)}
    return par_site.pop() if len(par_site) == 1 else "?"


def accroches(conn, eid: int) -> int:
    """Combien de lignes citent la séance et ne partiraient pas avec elle."""
    n = 0
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for nom in tables:
        for fk in conn.execute(f'PRAGMA foreign_key_list("{nom}")'):
            # (id, seq, table, from, to, on_update, on_delete, match)
            if fk[2] == "events" and fk[6].upper() != "CASCADE":
                n += conn.execute(f'SELECT COUNT(*) FROM "{nom}" WHERE "{fk[3]}"=?',
                                  (eid,)).fetchone()[0]
    for nom, col in CITATIONS_SANS_CLE:
        if nom in tables and any(c[1] == col for c in conn.execute(f'PRAGMA table_info("{nom}")')):
            n += conn.execute(f'SELECT COUNT(*) FROM "{nom}" WHERE "{col}"=?', (eid,)).fetchone()[0]
    if "annotations" in tables:
        marque = ",".join("?" * len(TYPES_ANNOTES_NON_EVENEMENT))
        n += conn.execute(f"SELECT COUNT(*) FROM annotations WHERE object_id=?"
                          f" AND object_type NOT IN ({marque})",
                          (eid, *TYPES_ANNOTES_NON_EVENEMENT)).fetchone()[0]
    return n


def plan(conn) -> list[dict]:
    seances = {p: _seances(conn, p) for p in SEANCE}
    lignes = []
    for d, municipales in seances["commune"].items():
        communautaires = seances["epci"].get(d) or []
        for cm in municipales:
            for cc in communautaires:
                for url in sorted(cm["urls"] & cc["urls"]):
                    lignes.append(_ligne(conn, d, url, cm, cc))
    couvertes = {(l["date"], l["url"]) for l in lignes}
    for portee in SEANCE:
        for eid, d, url in conn.execute(
                "SELECT id, date, source_url FROM events WHERE type=? AND source_url IS NOT NULL",
                (DELIB[portee],)).fetchall():
            if _etrangere(url, portee):
                nacc = accroches(conn, eid)
                douteux = nacc or _par_portail_seul(url, portee)
                lignes.append({"action": "?" if douteux else "retirer_acte", "type": DELIB[portee],
                               "id": eid, "date": d, "url": url,
                               "proprietaire": DELIB[AUTRE[portee]], "accroches": nacc})
        for d, fiches in seances[portee].items():
            for f in fiches:
                etrangeres = sorted(u for u in f["urls"] if _etrangere(u, portee)
                                    and (d, u) not in couvertes)
                for url in etrangeres:
                    if _par_portail_seul(url, portee):
                        lignes.append({"action": "?", "type": SEANCE[portee], "id": f["id"],
                                       "date": d, "url": url,
                                       "proprietaire": SEANCE[AUTRE[portee]]})
                    else:
                        lignes.append(_orpheline(conn, d, url, f, portee))
                if (not etrangeres and _hote(f["source"]) in _sites(AUTRE[portee])
                        and _hote(f["source"]) not in _sites(portee)
                        and not _etrangere(f["source_url"], portee)):
                    lignes.append({"action": "provenance", "type": SEANCE[portee],
                                   "id": f["id"], "date": d, "url": f["source_url"],
                                   "proprietaire": SEANCE[portee]})
    return lignes


def _orpheline(conn, d, url, fiche, portee) -> dict:
    """Pièce de l'autre assemblée sans séance de l'autre assemblée qui la tienne."""
    restantes = [p for p in fiche["pieces"] if not _etrangere(p.get("url"), portee)]
    # Les actes du jour qui partent avec la reprise ne retiennent pas la séance.
    delibs = sum(1 for (u,) in conn.execute(
        "SELECT source_url FROM events WHERE type=? AND date=?", (DELIB[portee], d))
        if not _etrangere(u, portee))
    nacc = 0
    if restantes:
        action = "detacher"
    else:
        nacc = accroches(conn, fiche["id"])
        action = "retenu" if nacc or delibs else "retirer"
    return {"action": action, "type": SEANCE[portee], "id": fiche["id"], "date": d,
            "url": url, "proprietaire": SEANCE[AUTRE[portee]],
            "pieces_restantes": " ".join(p.get("url", "") for p in restantes),
            "delibs_du_jour": delibs, "accroches": nacc,
            "presences_liees": conn.execute(
                "SELECT COUNT(*) FROM event_entities WHERE event_id=?",
                (fiche["id"],)).fetchone()[0]}


def _ligne(conn, d, url, cm, cc) -> dict:
    maitre = proprietaire(conn, url)
    if maitre == "?":
        return {"action": "?", "type": "", "id": "", "date": d, "url": url, "proprietaire": "?"}
    fiche, tenante = (cm, cc) if maitre == "epci" else (cc, cm)
    portee = AUTRE[maitre]
    restantes = [p for p in fiche["pieces"] if p.get("url") not in tenante["urls"]]
    delibs = conn.execute("SELECT COUNT(*) FROM events WHERE type=? AND date=?",
                          (DELIB[portee], d)).fetchone()[0]
    presences = conn.execute("SELECT COUNT(*) FROM event_entities WHERE event_id=?",
                             (fiche["id"],)).fetchone()[0]
    nacc = 0
    if restantes:
        action = "detacher"
    else:
        nacc = accroches(conn, fiche["id"])
        action = "retenu" if nacc or delibs else "retirer"
    return {"action": action, "type": SEANCE[portee], "id": fiche["id"], "date": d,
            "url": url, "proprietaire": SEANCE[maitre],
            "pieces_restantes": " ".join(p.get("url", "") for p in restantes),
            "delibs_du_jour": delibs, "accroches": nacc, "presences_liees": presences}


def appliquer(conn, lignes: list[dict]) -> tuple[int, int]:
    """Rend (séances retirées, séances détachées). `?` n'est jamais touché.
    Les actes retirés et les provenances corrigées se comptent dans le plan."""
    portee_de = {SEANCE[p]: p for p in SEANCE}
    retirees, detachees = set(), set()
    for l in lignes:
        if l["action"] == "retirer_acte":
            conn.execute("DELETE FROM events WHERE id=?", (l["id"],))
            _oublier_embeddings(conn, l["id"])
        elif l["action"] == "provenance":
            conn.execute("UPDATE events SET source=? WHERE id=?",
                         (_source_de(portee_de[l["type"]], l["url"]), l["id"]))
        elif l["action"] == "retirer":
            conn.execute("DELETE FROM events WHERE id=?", (l["id"],))
            _oublier_embeddings(conn, l["id"])
            retirees.add(l["id"])
        elif l["action"] in ("detacher", "retenu"):
            meta = json.loads(conn.execute("SELECT metadata FROM events WHERE id=?",
                                           (l["id"],)).fetchone()[0] or "{}")
            pieces = [p for p in meta.get("pieces") or [] if p.get("url") != l["url"]]
            meta = {k: v for k, v in meta.items() if k not in LU_DANS_LA_PIECE}
            meta["pieces"] = pieces
            conn.execute(
                "UPDATE events SET metadata=?, content=NULL, source_url=CASE"
                " WHEN source_url=? THEN ? ELSE source_url END WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), l["url"],
                 conseils._url_la_plus_probante(pieces, None), l["id"]))
            # `source` suit `source_url` : sinon la séance garde le nom de l'autre
            # assemblée (Brassac, 09/03/2026, après la reprise du 08/11).
            portee = portee_de[l["type"]]
            src, source = conn.execute("SELECT source_url, source FROM events WHERE id=?",
                                       (l["id"],)).fetchone()
            if _hote(source) in _sites(AUTRE[portee]) - _sites(portee):
                conn.execute("UPDATE events SET source=? WHERE id=?",
                             (_source_de(portee, src), l["id"]))
            conn.execute("DELETE FROM event_entities WHERE event_id=?", (l["id"],))
            detachees.add(l["id"])
    return len(retirees), len(detachees)


def _oublier_embeddings(conn, eid: int) -> None:
    """Les vecteurs d'un événement retiré : sans clé étrangère, ils resteraient."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embeddings'"
                    ).fetchone():
        conn.execute("DELETE FROM embeddings WHERE source_table='events' AND source_id=?",
                     (eid,))


def _cles(lignes) -> set[tuple[str, str, str]]:
    """Ce qu'on applique : `?` n'est jamais appliqué, il n'entre pas dans la comparaison."""
    return {(l["action"], str(l["id"]), l["url"]) for l in lignes if l["action"] != "?"}


def _lire_tsv(chemin: Path) -> list[dict]:
    with chemin.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _ecrire_tsv(chemin: Path, lignes: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    champs = ["action", "type", "id", "date", "url", "proprietaire", "pieces_restantes",
              "delibs_du_jour", "accroches", "presences_liees"]
    with chemin.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, champs, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)


def _resume(lignes: list[dict]) -> None:
    compte: dict = {}
    for l in lignes:
        compte[(l["action"], l["type"])] = compte.get((l["action"], l["type"]), 0) + 1
    for (action, type_), n in sorted(compte.items()):
        print(f"  {action:<9} {type_ or '-':<22} {n}")


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--db", type=Path, default=Path(config.DB_PATH))
    a.add_argument("--tsv", type=Path,
                   default=Path(f"audits/pieces-autre-assemblee-{date.today()}.tsv"))
    a.add_argument("--appliquer", action="store_true",
                   help="sauvegarde la base, puis retire et détache (jamais `?`)")
    a.add_argument("--plan-relu", type=Path,
                   help="avec --appliquer, OBLIGATOIRE : le TSV de la simulation relue ; "
                        "rien n'est écrit si le plan recalculé en diffère")
    a.add_argument("--portail-verifie", action="append", default=[], metavar="DOMAINE",
                   help="portail d'actes dont chaque PDF du plan a été vérifié (SIREN @ctes)")
    args = a.parse_args()
    PORTAILS_VERIFIES.update(d.lower().removeprefix("www.") for d in args.portail_verifie)

    if args.appliquer and not (args.plan_relu and args.plan_relu.is_file()):
        print("--appliquer exige --plan-relu <TSV de la simulation relue>. Rien d'écrit.")
        return 2
    if args.appliquer and args.plan_relu.resolve() == args.tsv.resolve():
        print("--tsv et --plan-relu désignent le même fichier : le plan relu serait écrasé.")
        return 2
    if not args.db.is_file():
        print(f"base introuvable : {args.db}")
        return 2
    # Simulation : base en lecture seule.
    conn = (sqlite3.connect(args.db) if args.appliquer
            else sqlite3.connect(f"file:{args.db}?mode=ro", uri=True))
    conn.execute("PRAGMA foreign_keys = ON")
    lignes = plan(conn)
    _ecrire_tsv(args.tsv, lignes)
    print(f"{args.db.name} : {len(lignes)} rattachement(s) à une pièce de l'autre assemblée")
    _resume(lignes)
    print(f"  → {args.tsv}")
    if not args.appliquer:
        print("SIMULATION — rien n'a été écrit en base.")
        return 0
    relu = _cles(_lire_tsv(args.plan_relu))
    if _cles(lignes) != relu:
        print(f"Le plan diffère de la simulation relue ({len(relu)} → {len(_cles(lignes))} "
              f"ligne(s) à appliquer). Rien d'écrit : resimuler et relire.")
        return 1
    conn.close()

    garde = args.db.with_name(f"{args.db.stem}.avant-autre-assemblee-"
                              f"{datetime.now():%Y%m%d-%H%M%S}.db")
    sauvegarder(args.db, garde)
    print(f"base sauvegardée → {garde.name}")
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        # Recalculé APRÈS la sauvegarde, dans la transaction qui écrit : une
        # collecte passée entre-temps ne décale pas ce qu'on applique.
        a_faire = plan(conn)
        if _cles(a_faire) != relu:
            # `with conn` annule : rien n'est écrit (seule la sauvegarde reste).
            raise SystemExit("Le plan a changé pendant la sauvegarde. Rien d'écrit : "
                             "resimuler et relire.")
        retirees, detachees = appliquer(conn, a_faire)
    reste = plan(conn)
    print(f"{retirees} séance(s) retirée(s), {detachees} détachée(s) ou retenue(s). "
          f"Restant : {len(reste)}" + (" (des `?` à trancher)" if reste else ""))
    print("Relire les pièces restantes (--step conseils), puis comparer et republier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
