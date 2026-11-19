#!/usr/bin/env python3
"""
collect_loop.py — relance les sources PÉRIMÉES, et rien d'autre.

Le moteur savait déjà trois choses sur sa propre cadence, et il lui manquait la
quatrième :

  1. le RYTHME de chaque source — `STEP_META` (TTL, priorité, table témoin) ;
  2. le JOURNAL de ce qui a tourné — `collector_runs`, écrit par
     `run_all.run_step` ;
  3. le CONTRÔLE — `sante()` signale une source muette, en panne, figée ou
     qui change de forme (`sante.json`, lu par `vigie_etat.sh --ecarts`) ;
  4. **l'ACTEUR** — personne ne relançait ce qui était périmé. C'est ce
     fichier. Il ne décide d'aucun rythme : il ne fait qu'exécuter celui que
     `STEP_META` déclare déjà.

Le vérificateur de cette boucle est donc la FRAÎCHEUR, pas le succès : un
collecteur qui rend zéro ligne n'est pas en échec (c'est le cas ordinaire d'une
petite commune), il est simplement à jour. Ce qui doit alerter, c'est le
silence — et il se lit dans `collector_runs`, pas dans les compteurs.

Usage :
    venv/bin/python3 scripts/collect_loop.py                 # scan seul (lecture)
    venv/bin/python3 scripts/collect_loop.py --run           # une passe
    venv/bin/python3 scripts/collect_loop.py --loop          # jusqu'à stable
    venv/bin/python3 scripts/collect_loop.py --only events --run
    venv/bin/python3 scripts/collect_loop.py --force --run   # ignore les TTL

Code de sortie : 0 si rien n'a échoué, 1 sinon — pour qu'un ordonnanceur le
sache sans lire la sortie.

⚠️ Deux gestes que cette boucle fait À VOTRE PLACE, et qu'il ne faut pas
retirer :

  - **les dérivés suivent leur source.** `cm_flux`, `commissions`,
    `approbations` et `budgets_votes` relisent le TEXTE des séances : les
    rejouer après `cm` n'est pas une précaution, c'est la correction du défaut
    du 14/08/2026 — `cm_flux` avait tourné sur 309 PV, `cm` en avait ajouté 636
    une demi-heure plus tard, et le site a publié 44 subventions au lieu de 123.
  - **`origine` et `perimetre` ferment toute passe** qui a collecté quelque
    chose. Un step lancé seul laisse ses entités NON CLASSÉES : le 03/09/2026,
    `--step osm` a créé 121 lieux avec `perimetre` NULL et le snapshot suivant a
    publié 130 objets de moins sans rien dire.
    Cf. [[feedback-step-isole-non-classe]].
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collectors import index_sens, mesure                  # noqa: E402
from collectors.config import COLLECTE_SUSPENDUS, DB_PATH, STEP_META  # noqa: E402
from collectors.db import get_conn, init_db               # noqa: E402
from collectors.sauvegarde import sauvegarder, verifier   # noqa: E402
from collectors.run_all import STEPS, run_step            # noqa: E402

# Ce que la boucle peut relancer : l'intersection du rythme déclaré et des steps
# qui existent. Une liste recopiée ici dériverait — c'est ce qui, en v1, ne
# suivait que 10 collecteurs sur 15, et rendait le silence des cinq autres
# indétectable.
SUIVIS = {nom: meta for nom, meta in STEP_META.items() if nom in STEPS}

# Les dérivés, par la source dont ils relisent le texte. Leur rythme propre
# reste dans `STEP_META` : ce tableau ne dit pas QUAND les jouer, il dit
# qu'après cette source-là ils sont périmés quoi qu'en dise leur date.
DERIVES: dict[str, tuple[str, ...]] = {
    "cm":         ("cm_flux", "commissions", "approbations", "budgets_votes"),
    "cm_archive": ("cm_flux", "commissions", "approbations", "budgets_votes"),
    "cc_epci":    ("cm_flux",),
    "ofgl":       ("subventions",),
    "budget":     ("subventions",),
}

# Les deux classements qui ferment une collecte. Ils ne vont chercher rien.
CLOTURE = ("origine", "perimetre")

TEMPS_MAX_DEFAUT = 900   # secondes, par collecteur

# Copies de la base gardées par instance. Rien ne les purgeait : 26 copies de
# 138 Mo sur le VPS et 128 (12 Go) sur le Mac le 25/10/2026. Un retour arrière
# se fait sur la veille, pas sur celle d'il y a trois semaines. Trois depuis le
# 31/10/2026 (Julien) : le déploiement garde les siennes, et la copie durable
# est tirée sur le Mac (`vigie_atelier_sauvegarde.sh`).
GARDER_SAUVEGARDES = 3


class TempsDepasse(Exception):
    """Levée dans le collecteur lui-même, donc journalisée par `run_step`."""


def _age_jours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return None


def dernier_passage(conn, nom: str):
    """Dernier passage ABOUTI. Un `error` ou un `timeout` ne rajeunit rien.

    Sans cette clause, une source en panne paraîtrait fraîche pour la seule
    raison qu'on a essayé — et la boucle cesserait de la retenter le lendemain.
    """
    return conn.execute(
        "SELECT id, finished_at, status, items_before, items_after FROM collector_runs"
        " WHERE collector=? AND status IN ('ok','empty') AND finished_at IS NOT NULL"
        " ORDER BY finished_at DESC LIMIT 1", (nom,)).fetchone()


# Une suspension qui dure se relit comme un oubli : au-delà, elle redevient
# une alerte de `sante.json`, sans que la source soit relancée pour autant.
SUSPENSION_ALERTE_JOURS = 30


def perimes(conn, force: bool = False,
            suspendus: dict[str, dict] = COLLECTE_SUSPENDUS) -> list[str]:
    """Les steps à rejouer, du plus prioritaire au moins.

    Une source suspendue n'y est jamais, même sous `force` : `--force` ignore les
    TTL, pas une décision de l'instance. Seul `--only <step> --force` la joue.
    """
    retard = []
    for nom in sorted(SUIVIS, key=lambda n: SUIVIS[n][1]):
        if nom in suspendus:
            continue
        ttl = SUIVIS[nom][0]
        ligne = dernier_passage(conn, nom)
        age = _age_jours(ligne["finished_at"]) if ligne else None
        if force or age is None or age > ttl:
            retard.append(nom)
    return retard


def scan(conn, force: bool = False) -> list[str]:
    retard = set(perimes(conn, force))
    print(f"\n{'collecteur':14} {'dernier passage':18} {'âge':>7} {'TTL':>6}  état")
    print("─" * 62)
    for nom in sorted(SUIVIS, key=lambda n: SUIVIS[n][1]):
        ligne = dernier_passage(conn, nom)
        age = _age_jours(ligne["finished_at"]) if ligne else None
        quand = ligne["finished_at"][:16] if ligne else "jamais"
        print(f"{nom:14} {quand:18} {(f'{age:.1f}j' if age is not None else '—'):>7}"
              f" {SUIVIS[nom][0]:>5}j  "
              f"{'suspendu' if nom in COLLECTE_SUSPENDUS else 'PÉRIMÉ' if nom in retard else 'frais'}")
    print("─" * 62)
    ordre = [n for n in sorted(SUIVIS, key=lambda n: SUIVIS[n][1]) if n in retard]
    print(f"{len(ordre)} source(s) à rafraîchir : {', '.join(ordre) if ordre else '—'}")
    return ordre


# Au-delà, une source n'est plus « à rafraîchir » mais en panne : deux TTL sans
# passage abouti, ou trois échecs de suite (une API qui refuse trois matins
# n'est pas de passage).
RETARD_ALERTE = 2
ECHECS_ALERTE = 3

# Part des lignes de sa table témoin qu'une passe ABOUTIE peut faire perdre
# sans alerte. Onze collecteurs vident puis réécrivent ce qu'ils relèvent
# (odre, hatvp, guichets, rpls, cantines, eau…) : chez eux, une table qui
# fond veut dire que la SOURCE a rendu moins qu'avant, et le passage restait
# `ok`. Le même seuil vaut pour ce que la source a RENDU (`mesure.alertes`,
# lot 1 du chantier « sentinelle de forme des sources ») : chute en % et
# passage à zéro, décidé le 06/11/2026. Celui-ci reste, le temps de juger son
# bruit sur les tables.
BAISSE_ALERTE = 0.2

# Le texte d'une erreur de collecte vient souvent du SERVEUR interrogé : la
# phrase d'un `HTTP/1.1 503 <phrase>`, l'URL complète (requête et jeton
# compris) qu'y ajoute requests. `sante.json` entre dans le contexte d'ouverture
# de session d'un assistant : il n'en garde qu'un motif d'une liste fermée
# (audit du 06/11/2026). Le texte brut reste dans `collector_runs.error`.
# Liste fermée de référence, commune aux trois copies (ici, et côté
# exploitation) : mêmes règles, dans le même ordre, tout en ASCII. Un chiffre
# arabe-indien ou une lettre d'un autre alphabet ne ressort jamais.
_A = re.ASCII
_SORTIE = re.compile(r"SystemExit\b(?::\s*(\d{1,3})\s*$)?", _A)
_HTTP = re.compile(r"HTTP Error (\d{3})\b|\b(\d{3}) (?:Client|Server) Error\b"
                   r"|(?:Client|Server) error '(\d{3})\b", _A)
_CURL = re.compile(r"\bcurl\D{0,8}?(\d{1,3})\b", _A)
_MOTIFS = (
    ("certificat", re.compile(r"SSLError|SSL:|CERTIFICATE_VERIFY_FAILED|certificate verify", _A),
     {"60"}),
    ("DNS", re.compile(r"gaierror|NameResolutionError|Name or service not known"
                       r"|nodename nor servname|Temporary failure in name resolution", _A),
     {"6"}),
    ("délai", re.compile(r"Timeout|timed out|TempsDepasse", _A), {"28"}),
    ("connexion", re.compile(r"ConnectionError|ConnectionRefused|RemoteDisconnected"
                             r"|IncompleteRead|ChunkedEncodingError|ProtocolError", _A),
     {"7", "52", "56"}),
)
_CLASSE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}", _A)


def forme_erreur(texte: str | None) -> str:
    """Ramène une erreur à `SystemExit n`, `HTTP nnn`, `certificat`, `DNS`,
    `délai`, `connexion`, `curl n`, un nom de classe Python, sinon `autre`."""
    texte = texte or ""
    if m := _SORTIE.match(texte):
        return f"SystemExit {int(m.group(1))}" if m.group(1) else "SystemExit"
    if m := _HTTP.search(texte):
        return f"HTTP {m.group(1) or m.group(2) or m.group(3)}"
    m = _CURL.search(texte)
    curl = str(int(m.group(1))) if m else None
    for motif, rx, codes in _MOTIFS:
        if rx.search(texte) or curl in codes:
            return motif
    if curl:
        return f"curl {curl}"
    classe = texte.split(":", 1)[0].strip()
    return classe if _CLASSE.fullmatch(classe) else "autre"


# Une source qui a déjà livré et n'ajoute plus rien depuis un an : jeu figé
# alors que l'API répond (decp_augmente, 577 jours sans un signal). Repris de
# `qa_loop.check_silent_source` le 06/11/2026, mais compté en JOURS : sa règle
# « 3 passes sans apport » criait sur le cas ordinaire, `cm` (TTL 7 j, un PV par
# mois), `bodacc`, `marches`, `events` d'une petite commune. Seuil grossier
# exprès, à affiner sur les `dernier_apport` relevés.
SANS_APPORT_ALERTE_JOURS = 365
SANS_APPORT_PASSES = 3


def sante(conn, suspendus: dict[str, dict] = COLLECTE_SUSPENDUS) -> dict:
    """L'état de chaque source, lisible SANS ouvrir la base : `sante.json`.

    `vigie_etat.sh --ecarts` le lit sur chaque instance. Il ne porte que des
    noms de steps, des dates, le motif d'erreur (`forme_erreur`), pour une baisse deux
    nombres de lignes, et pour une source qui change les hôtes d'API, types de
    contenu, nombres d'éléments mesurés à l'appel et noms de colonnes gagnées
    ou perdues, filtrés sur leur forme. Les sites déclarés des acteurs, eux,
    sont des valeurs de la base : leurs alertes ne donnent qu'un décompte
    (`mesure.SITES_D_ACTEURS`). Le 26/10/2026, deux collecteurs du portail
    échouaient à chaque génération et personne ne l'a su avant de chercher.

    Les deux contrôles de source de `qa_loop` (`check_silent_source`,
    `stale_source`), que rien ne lançait, sont fondus ici (lot 2, 06/11/2026) :
    le silence au-delà de 1,5 TTL est celui de `RETARD_ALERTE` (2 TTL, un seul
    seuil) ; s'y ajoutent le step jamais passé, celui qui n'a jamais abouti, et
    la source figée (`SANS_APPORT_ALERTE_JOURS`).
    """
    etat: dict = {"genere_le": datetime.now().isoformat(timespec="seconds"),
                  "steps": {}, "alertes": [], "suspendus": {}}
    # Un nom qui ne désigne aucun step ne suspend rien : la source qu'on croyait
    # arrêtée continue de tourner. Le dire, sans arrêter la collecte pour autant.
    for nom in sorted(set(suspendus) - set(SUIVIS)):
        etat["alertes"].append(f"{nom} : suspendu dans instance.json, mais aucun step de ce nom")
    for nom in sorted(SUIVIS):
        ttl = SUIVIS[nom][0]
        ligne = dernier_passage(conn, nom)
        age = _age_jours(ligne["finished_at"]) if ligne else None
        echecs, derniere, passages = 0, None, 0
        for run in conn.execute(
                "SELECT status, error FROM collector_runs WHERE collector=?"
                " AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 10", (nom,)):
            passages += 1
            if run["status"] in ("ok", "empty"):
                break
            echecs += 1
            derniere = derniere or (forme_erreur(run["error"]) if run["error"]
                                    else run["status"] or "?")
        apport, depuis_apport = dernier_apport(conn, nom)
        etat["steps"][nom] = {
            "dernier_succes": ligne["finished_at"] if ligne else None,
            "age_jours": round(age, 1) if age is not None else None, "ttl_jours": ttl,
            "echecs_consecutifs": echecs, "derniere_erreur": derniere,
            "dernier_apport": apport,
            "mesure": resume_mesure(conn, ligne["id"] if ligne else None),
        }
        if nom in suspendus:
            # Ni ses échecs passés ni son âge ne sont une panne : on a choisi de
            # ne plus la relancer. Seule la durée de la suspension compte.
            s = suspendus[nom]
            etat["steps"][nom]["suspendu"] = s
            etat["suspendus"][nom] = s["motif"]
            depuis = _age_jours(s["depuis"])
            if depuis is not None and depuis > SUSPENSION_ALERTE_JOURS:
                etat["alertes"].append(
                    f"{nom} : suspendu depuis {depuis:.0f} j — {s['motif']}")
            continue
        sans_apport = _age_jours(apport)
        if echecs >= ECHECS_ALERTE:
            etat["alertes"].append(f"{nom} : {echecs} échecs de suite — {derniere}")
        elif not passages:
            # Tout step suivi passe à chaque nuit où il est périmé : jamais
            # passé veut dire que la boucle ne l'atteint pas.
            etat["alertes"].append(f"{nom} : aucun passage au journal")
        elif ligne is None:
            etat["alertes"].append(
                f"{nom} : aucun passage abouti ({echecs} échec(s)) — {derniere}")
        elif age is not None and age > RETARD_ALERTE * ttl:
            etat["alertes"].append(f"{nom} : {age:.0f} j sans passage abouti (TTL {ttl} j)")
        elif sans_apport is not None and sans_apport > SANS_APPORT_ALERTE_JOURS \
                and depuis_apport >= SANS_APPORT_PASSES:
            etat["alertes"].append(
                f"{nom} : rien ajouté depuis {sans_apport:.0f} j ({depuis_apport} passes "
                "abouties) alors qu'elle a déjà livré — le jeu source est-il encore "
                "alimenté ?")
        avant = ligne["items_before"] if ligne else None
        apres = ligne["items_after"] if ligne else None
        if avant and apres is not None and apres < avant * (1 - BAISSE_ALERTE):
            # La date de la passe jugée : l'alerte se répète jusqu'à la passe
            # aboutie suivante (TTL), et sans date elle se relisait chaque
            # matin comme une panne du jour (revue du 07/11/2026, C5).
            etat["alertes"].append(
                f"{nom} : la passe aboutie du {str(ligne['finished_at'])[:10]} a fait "
                f"tomber sa table de {avant} à {apres} lignes "
                f"({(avant - apres) / avant:.0%}) — la source rend-elle moins qu'avant ?")
        # Ce que la SOURCE a rendu, face à la passe précédente : HTML à la
        # place de données, renvoi vers un autre hôte, source complète qui
        # tombe à zéro ou chute (`collectors/mesure.py`).
        etat["alertes"] += mesure.alertes(conn, nom)
    # L'index de recherche par sens, sur une instance qui l'entretient
    # (`scripts/build_rag_index.py --etat`) : son dernier essai, ses mesures, et
    # ses échecs dans les mêmes formes d'alerte qu'un step.
    index = index_sens.lire_etat(Path(DB_PATH).parent)
    if index is not None:
        etat["index_sens"] = index
        etat["alertes"] += index_sens.alertes(index)
    return etat


def dernier_apport(conn, nom: str) -> tuple[str | None, int]:
    """Date de la dernière passe aboutie qui a AJOUTÉ quelque chose, et nombre
    de passes abouties depuis. (None, 0) pour une source qui n'a jamais rien
    ajouté : sur `crc`, ne rien trouver est le résultat juste et durable."""
    ligne = conn.execute(
        "SELECT MAX(finished_at) FROM collector_runs WHERE collector=?"
        " AND status IN ('ok','empty') AND items_added > 0", (nom,)).fetchone()
    quand = ligne[0] if ligne else None
    if quand is None:
        return None, 0
    depuis = conn.execute(
        "SELECT COUNT(*) FROM collector_runs WHERE collector=?"
        " AND status IN ('ok','empty') AND finished_at > ?", (nom, quand)).fetchone()[0]
    return quand, int(depuis)


def resume_mesure(conn, run_id: int | None) -> dict | None:
    """Ce que la mesure à l'appel a noté pour une passe, en DÉCOMPTES : de
    quoi vérifier qu'elle se remplit sans ouvrir la base. Ni hôte, ni nom de
    source, ni forme : `sante.json` entre dans le contexte de l'assistant
    ([[feedback-texte-de-serveur-dans-le-contexte-de-l-assistant]]).
    `lignes: 0` sur une passe récente : le step n'est pas mesuré."""
    if run_id is None:
        return None
    try:
        lignes, hotes, sources, completes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(nature='reponses'), 0),"
            " COALESCE(SUM(nature='elements'), 0),"
            " COALESCE(SUM(nature='elements' AND complete=1), 0)"
            " FROM collector_mesures WHERE run_id=?", (run_id,)).fetchone()
    except Exception:
        return None                   # base antérieure au lot 1
    return {"lignes": int(lignes), "hotes": int(hotes), "sources": int(sources),
            "completes": int(completes)}


def ecrire_sante(conn) -> Path:
    chemin = Path(DB_PATH).parent / "sante.json"
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(json.dumps(sante(conn), ensure_ascii=False, indent=1))
    temporaire.replace(chemin)
    return chemin


def jouer(nom: str, temps_max: int) -> tuple[str, str | None]:
    """Joue un step sous garde de temps. `run_step` journalise, pas nous.

    L'alarme lève DANS le collecteur : l'exception remonte à `run_step`, qui la
    consigne en `error` avec son message. Une garde posée à l'extérieur aurait
    laissé la ligne de journal ouverte pour toujours — un run sans fin est
    indiscernable d'un run en cours.
    """
    def _alarme(signum, frame):
        raise TempsDepasse(f"dépassé {temps_max}s")

    precedent = signal.signal(signal.SIGALRM, _alarme)
    signal.alarm(temps_max)
    try:
        return run_step(nom)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, precedent)


def items(conn, nom: str) -> int | None:
    """Ce que le dernier run a ajouté, pour savoir si un dérivé doit suivre."""
    ligne = conn.execute(
        "SELECT items_added FROM collector_runs WHERE collector=?"
        " ORDER BY id DESC LIMIT 1", (nom,)).fetchone()
    return ligne["items_added"] if ligne else None


def sauvegarde() -> Path:
    """Copie de la base avant d'écrire. Même convention que `qa_loop`."""
    racine = Path(os.environ.get("VIGIE_BACKUPS") or Path.home() / "Claude" / ".backups")
    dest = racine / f"vigie-collecte-{datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    copie = sauvegarder(DB_PATH, dest / DB_PATH.name)
    # On ne purge que derrière une copie saine. Le répertoire est partagé par les
    # instances du Mac : seules les copies de CETTE base sont candidates.
    if not verifier(copie):
        for vieille in sorted(racine.glob(f"vigie-collecte-*/{DB_PATH.name}"))[:-GARDER_SAUVEGARDES]:
            vieille.unlink()
            try:
                vieille.parent.rmdir()
            except OSError:
                pass
    return copie


def main() -> int:
    ap = argparse.ArgumentParser(description="Relance les sources périmées")
    ap.add_argument("--run", action="store_true", help="une passe")
    ap.add_argument("--loop", action="store_true", help="scan → run → rescan jusqu'à stable")
    ap.add_argument("--only", help="ne traiter que ce step")
    ap.add_argument("--force", action="store_true", help="ignorer les TTL")
    ap.add_argument("--max-iter", type=int, default=3)
    ap.add_argument("--temps-max", type=int, default=TEMPS_MAX_DEFAUT,
                    help="garde de temps par collecteur, en secondes")
    ap.add_argument("--sante", action="store_true",
                    help="réécrire sante.json seulement (après l'indexation de la passe)")
    args = ap.parse_args()

    if args.sante:
        conn = get_conn()
        try:
            print(f"Santé des sources : {ecrire_sante(conn)}")
        finally:
            conn.close()
        return 0

    # Seconde porte d'entrée de la collecte : même garde que `run_all.main`.
    from collectors import reseau
    reseau.garder()

    if args.only and args.only not in SUIVIS:
        raise SystemExit(f"step inconnu ou sans rythme déclaré : {args.only}\n"
                         f"  connus : {', '.join(sorted(SUIVIS))}")
    if args.only in COLLECTE_SUSPENDUS and not args.force:
        print(f"{args.only} est suspendu ({COLLECTE_SUSPENDUS[args.only]['motif']}) :"
              f" --force pour le jouer quand même.")

    agir = args.run or args.loop
    if agir:
        print(f"Sauvegarde avant collecte : {sauvegarde()}")
        # Le schéma d'abord. `run_all` le met à jour avant tout step ; la boucle
        # appelait `run_step` directement et ne le faisait jamais. Une table
        # ajoutée au schéma (`comptes_syndicats`, 24/09/2026) n'existait donc
        # sur aucune instance : le step qui l'écrit aurait échoué chaque matin,
        # et comme un `error` ne rajeunit rien, il aurait été retenté en vain
        # tous les jours. `init_db` est idempotent — IF NOT EXISTS, colonnes et
        # index rattrapés.
        init_db()
    conn = get_conn()
    joues: list[str] = []
    echecs: dict[str, str] = {}
    try:
        iteration = 0
        while True:
            iteration += 1
            retard = scan(conn, args.force)
            if args.only:
                retard = [args.only] if (args.only in retard or args.force) else []
            # Un échec ne se retente pas dans le même cycle : une API qui refuse
            # à 9 h refusera à 9 h 01, et insister ressemble à du martèlement.
            todo = [n for n in retard if n not in echecs]
            if not agir or not todo:
                break

            print(f"\n— Passe {iteration} : {len(todo)} step(s) —")
            for nom in todo:
                statut, err = jouer(nom, args.temps_max)
                joues.append(nom)
                if statut not in ("ok", "empty"):
                    echecs[nom] = err or "?"
                    continue
                # Le dérivé suit la source qui l'alimente, et seulement si elle
                # a ajouté quelque chose : rejouer une lecture de PV quand aucun
                # PV n'est arrivé ne coûte rien d'utile.
                if (items(conn, nom) or 0) > 0:
                    for derive in DERIVES.get(nom, ()):
                        if derive in STEPS and derive not in todo and derive not in COLLECTE_SUSPENDUS:
                            statut_d, err_d = jouer(derive, args.temps_max)
                            joues.append(derive)
                            if statut_d not in ("ok", "empty"):
                                echecs[derive] = err_d or "?"

            if not args.loop or iteration >= args.max_iter:
                break

        # Le classement ferme la passe, TOUJOURS — même si un collecteur a
        # échoué : ce qui a été écrit avant l'échec doit être classé, sinon il
        # reste invisible au snapshot.
        if joues:
            print(f"\n— Clôture : {', '.join(CLOTURE)} —")
            for nom in CLOTURE:
                statut, err = jouer(nom, args.temps_max)
                if statut not in ("ok", "empty"):
                    echecs[nom] = err or "?"

        if agir:
            print(f"Santé des sources : {ecrire_sante(conn)}")
        print(f"\n{len(joues)} step(s) joué(s) : {', '.join(joues) if joues else '—'}")
        if echecs:
            print(f"{len(echecs)} en échec :")
            for nom, err in echecs.items():
                print(f"  ✗ {nom:14} {err}")
        return 1 if echecs else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
