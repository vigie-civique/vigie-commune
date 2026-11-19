"""Les propositions : ce qu'un contributeur écrit sur un objet PUBLIÉ.

Arbitré par Julien le 02/10/2026, avant l'arrivée des premiers bénévoles. Les
rôles disaient « le contributeur propose sans trancher », mais l'API ne
réservait au validateur que le verdict et la fiabilité : le nom d'une fiche
publiée, le type d'une relation, le montant corrigé d'un acte s'écrivaient
directement — et la passe quotidienne les mettait en ligne sans que personne
les ait relus.

Une proposition garde ce que le contributeur voulait écrire (`charge`), ce
qu'il avait sous les yeux (`avant`), et attend. Un validateur l'accepte — elle
est alors REJOUÉE par le même écrivain que s'il l'avait saisie lui-même, il n'y
a pas de second chemin d'écriture — ou la refuse, avec un motif.

Ce module ne porte que la table. Les écrivains vivent dans `api.py`, à côté de
ce qu'ils écrivent.

Depuis le 04/10/2026, une proposition peut aussi porter une ligne EXTRAITE
d'un texte et qui n'est encore nulle part en base : un marché lu dans un
procès-verbal (`collectors/marches_extraits.py`). Elle ne vise alors pas un
objet publié mais l'acte d'où elle a été lue (`object_type = 'deliberation'`),
et sa `cle` — l'empreinte de la ligne — est ce qui empêche un rapport rejoué
d'empiler deux fois la même relecture.

Depuis le 15/11/2026, un contributeur peut aussi SIGNALER une fiche (décision
de Julien après l'audit du même jour) : à retirer, doublon d'une autre fiche,
à revoir, avec un texte court. Pas de circuit neuf : c'est une proposition de
nature `signalement`, qui entre dans la même file. Le validateur la tranche
avec les gestes qu'il a déjà (écarter la fiche, la marquer « à revoir ») ou
la refuse ; le contributeur, lui, n'écarte toujours rien.
"""
from __future__ import annotations

import json
from typing import Optional

#: Ce qu'une proposition peut porter, et l'objet qu'elle vise.
NATURES = ("fiche", "coords", "relation", "correction", "marche", "flux", "signalement")

#: Ce qu'un signalement peut demander. Le doublon nomme l'autre fiche ; la
#: fusion reste un geste à la main, que le signalement demande sans le faire.
MOTIFS_SIGNALEMENT = {
    "retirer": "À retirer du site",
    "doublon": "Doublon d'une autre fiche",
    "revoir":  "À revoir",
}
#: Ce que devient un signalement ACCEPTÉ. Les deux premiers sont les verdicts
#: que le validateur pose déjà sur une fiche. Le troisième (décision de
#: Julien, 16/11/2026) ne touche pas la fiche : le doublon est reconnu, et la
#: fusion, demandée, reste à faire à la main. Offert au seul motif `doublon`.
ISSUES_SIGNALEMENT = ("ecarte", "a_revoir", "fusion_a_faire")
FUSION_A_FAIRE = "fusion_a_faire"
#: Signalements qu'un même compte peut avoir en attente (avis de
#: vigie-securite) : au-delà, 409 jusqu'à la relecture des siens.
PLAFOND_SIGNALEMENTS = 20
ETATS = ("en_attente", "acceptee", "refusee", "retiree")

SCHEMA = """CREATE TABLE IF NOT EXISTS propositions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nature       TEXT NOT NULL,
    object_type  TEXT NOT NULL,
    object_id    INTEGER NOT NULL,
    entity_id    INTEGER,
    charge       TEXT NOT NULL,
    avant        TEXT,
    propose_par  INTEGER REFERENCES users(id),
    propose_le   TEXT DEFAULT (datetime('now')),
    etat         TEXT NOT NULL DEFAULT 'en_attente',
    tranche_par  INTEGER REFERENCES users(id),
    tranche_le   TEXT,
    motif        TEXT,
    cle          TEXT,
    issue        TEXT
)"""

#: Une ligne extraite ne se propose qu'une fois, quel que soit son sort : une
#: ligne écartée qu'un rapport rejoué ramènerait en attente ferait relire deux
#: fois ce qu'un validateur a déjà tranché.
INDEX_CLE = ("CREATE UNIQUE INDEX IF NOT EXISTS idx_propositions_cle "
             "ON propositions(nature, cle) WHERE cle IS NOT NULL")


def assurer_schema(conn) -> None:
    """L'API ne passe pas par `init_db()` : une base d'avant le 02/10/2026 n'a
    pas la table, et la première proposition y échouerait. Une base d'avant le
    04/10/2026 l'a sans `cle`."""
    conn.execute(SCHEMA)
    colonnes = {r[1] for r in conn.execute("PRAGMA table_info(propositions)")}
    if "cle" not in colonnes:
        conn.execute("ALTER TABLE propositions ADD COLUMN cle TEXT")
    # Ce que le validateur a fait d'un signalement accepté (`ISSUES_SIGNALEMENT`).
    if "issue" not in colonnes:
        conn.execute("ALTER TABLE propositions ADD COLUMN issue TEXT")
    conn.execute(INDEX_CLE)


def proposer(conn, nature: str, object_type: str, object_id: int,
             entity_id: Optional[int], charge: dict, avant: dict, user: dict,
             cle: Optional[str] = None, rendre_reprises: bool = False):
    """Enregistre la proposition et la journalise. Une proposition encore en
    attente du même auteur, sur le même objet et de même nature, est REMPLACÉE :
    corriger sa propre proposition ne doit pas en empiler deux à relire.

    Remplacée, mais pas perdue : ses champs que la nouvelle ne touche pas y
    sont REPRIS. Deux onglets ouverts sur la même fiche, l'un proposant le
    responsable, l'autre le nom, retiraient en silence la première proposition
    (audit du 15/11/2026). Un même champ : la plus récente l'emporte, c'est le
    même auteur qui se corrige.

    Sauf quand elle porte une `cle` : plusieurs marchés se lisent dans le même
    acte, et le second ne remplace pas le premier. C'est alors la clé qui
    dédoublonne (cf. `INDEX_CLE`).

    Rend l'identifiant ; avec `rendre_reprises`, le couple (identifiant,
    identifiants des propositions reprises)."""
    reprises: list[int] = []
    if cle is None:
        # Lire les anciennes puis les retirer forme un seul geste : sous la
        # transaction de l'appelant s'il en tient une, sinon sous la nôtre,
        # que l'appelant valide (il commite après). Sans elle, deux envois
        # simultanés liraient la même ancienne et la reprendraient deux fois.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        anciennes = conn.execute(
            "SELECT id, charge, avant FROM propositions WHERE etat='en_attente' "
            "AND nature=? AND object_type=? AND object_id=? AND propose_par=? ORDER BY id",
            (nature, object_type, object_id, user["id"])).fetchall()
        for a in anciennes:
            ancienne = _ligne({"charge": a[1], "avant": a[2]})
            charge = {**ancienne["charge"], **charge}
            avant = {**ancienne["avant"], **avant}
            reprises.append(a[0])
        conn.execute(
            "UPDATE propositions SET etat='retiree', tranche_le=datetime('now') "
            "WHERE etat='en_attente' AND nature=? AND object_type=? AND object_id=? "
            "AND propose_par=?", (nature, object_type, object_id, user["id"]))
    pid = conn.execute(
        "INSERT INTO propositions(nature, object_type, object_id, entity_id, charge, "
        "avant, propose_par, cle) VALUES(?,?,?,?,?,?,?,?)",
        (nature, object_type, object_id, entity_id,
         json.dumps(charge, ensure_ascii=False), json.dumps(avant, ensure_ascii=False),
         user["id"], cle)).lastrowid
    conn.execute(
        "INSERT INTO audit_log(user_id, entity_id, table_name, action, field, new_value) "
        "VALUES(?,?,?,?,?,?)",
        (user["id"], entity_id, "propositions", "proposer", f"proposition/{pid}",
         json.dumps(charge, ensure_ascii=False)[:4000]))
    return (pid, reprises) if rendre_reprises else pid


def _ligne(r) -> dict:
    d = dict(r)
    for cle in ("charge", "avant"):
        try:
            d[cle] = json.loads(d[cle]) if d[cle] else {}
        except json.JSONDecodeError:
            d[cle] = {}
    return d


_SELECTION = """
    SELECT p.id, p.nature, p.object_type, p.object_id, p.entity_id, p.charge, p.avant,
           p.propose_par AS propose_par_id, up.email AS propose_par, p.propose_le,
           p.etat, ut.email AS tranche_par, p.tranche_le, p.motif,
           p.cle, p.issue, e.name AS fiche
    FROM propositions p
    LEFT JOIN users up   ON up.id = p.propose_par
    LEFT JOIN users ut   ON ut.id = p.tranche_par
    LEFT JOIN entities e ON e.id = p.entity_id
"""


def lire(conn, proposition_id: int) -> Optional[dict]:
    r = conn.execute(_SELECTION + " WHERE p.id=?", (proposition_id,)).fetchone()
    return _ligne(r) if r else None


def _filtre(etat, auteur_id, nature, issue):
    sql, params = " WHERE p.etat=?", [etat]
    if auteur_id is not None:
        sql += " AND p.propose_par=?"
        params.append(auteur_id)
    if nature is not None:
        sql += " AND p.nature=?"
        params.append(nature)
    if issue is not None:
        sql += " AND p.issue=?"
        params.append(issue)
    return sql, params


def compter(conn, etat: str = "en_attente", auteur_id: Optional[int] = None,
            nature: Optional[str] = None, issue: Optional[str] = None) -> int:
    sql, params = _filtre(etat, auteur_id, nature, issue)
    return conn.execute("SELECT COUNT(*) FROM propositions p" + sql, params).fetchone()[0]


def lister(conn, etat: str = "en_attente", auteur_id: Optional[int] = None,
           limit: int = 200, nature: Optional[str] = None,
           issue: Optional[str] = None, offset: int = 0,
           natures_lues: tuple[str, ...] = ()) -> list[dict]:
    """Une page de la file. `auteur_id` restreint à celles d'un compte — un
    contributeur ne voit que les siennes.

    Ordre : les propositions des contributeurs d'abord, les plus anciennes en
    tête (quelqu'un attend) ; puis les lignes LUES dans un acte (`natures_lues`),
    l'acte le plus récent d'abord — un rapport couvre dix ans de procès-verbaux
    et la file s'ouvrait sur 2016 — puis l'identifiant. Le tri est fait en SQL
    pour qu'une page n'oblige pas à construire toute la file.
    """
    sql, params = _filtre(etat, auteur_id, nature, issue)
    lues = ",".join("?" * len(natures_lues)) or "NULL"
    ordre = (f" ORDER BY (p.nature IN ({lues})), "
             "CASE WHEN p.nature IN (" + lues + ") THEN ev.date END DESC, p.id")
    return [_ligne(r) for r in conn.execute(
        _SELECTION + " LEFT JOIN events ev ON ev.id = p.object_id" + sql + ordre
        + " LIMIT ? OFFSET ?",
        params + [*natures_lues, *natures_lues, limit, offset])]


def clore(conn, proposition_id: int, etat: str, user: dict, motif: str = "") -> None:
    conn.execute(
        "UPDATE propositions SET etat=?, tranche_par=?, tranche_le=datetime('now'), motif=? "
        "WHERE id=?", (etat, user["id"], (motif or "").strip()[:1000] or None, proposition_id))
    conn.execute(
        "INSERT INTO audit_log(user_id, entity_id, table_name, action, field, new_value) "
        "SELECT ?, entity_id, 'propositions', ?, 'proposition/' || id, ? "
        "FROM propositions WHERE id=?",
        (user["id"], etat, (motif or "").strip()[:1000] or None, proposition_id))
