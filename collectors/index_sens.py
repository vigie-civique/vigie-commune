"""L'identité et la santé de l'index de recherche par sens (table `embeddings`).

Un vecteur n'a de sens que face à une question passée par le MÊME modèle : deux
modèles, ou deux versions d'un même nom (`ollama pull` remplace en silence),
rangent les textes dans des espaces différents. Comparer une question à un index
construit par un autre modèle ne lève aucune erreur, il rend des voisins au
hasard avec des scores plausibles. Chaque vecteur porte donc le nom et
l'empreinte du modèle qui l'a calculé, et la recherche refuse un index qui ne
correspond pas au modèle servi.

Le module sert à trois endroits qui doivent dire la même chose : l'API de
l'atelier (refus d'une recherche), `scripts/build_rag_index.py` (refus de
compléter un index d'un autre modèle) et `scripts/collect_loop.py` (alertes de
`sante.json`).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

# `127.0.0.1` et non `localhost` : Ollama n'écoute qu'en IPv4, et sur macOS
# `localhost` résout d'abord en IPv6 (cf. api.py).
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODELE = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# L'état de la dernière indexation, à côté de la base, comme `sante.json`.
ETAT = "index_sens.json"

# Au-delà de deux jours sans indexation aboutie, l'index d'une instance qui
# l'entretient chaque nuit a manqué au moins une passe.
TTL_JOURS = 1
RETARD_ALERTE = 2

_EMPREINTE = re.compile(r"[0-9a-f]{12}")
_NOM = re.compile(r"[\w.:/-]{1,80}", re.ASCII)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", re.ASCII)
_MESURES = ("extraits", "ajoutes", "mis_a_jour", "inchanges", "retires",
            "extraits_en_echec", "duree_s", "octets_index", "octets_base")
# La liste fermée des motifs (`collect_loop.forme_erreur`, et sa copie dans
# `vigie_etat.sh` côté exploitation).
_MOTIF = re.compile(r"SystemExit \d{1,3}|HTTP \d{3}|certificat|DNS|délai|connexion"
                    r"|curl \d{1,3}|[A-Za-z_][A-Za-z0-9_]{0,39}", re.ASCII)


def nom_complet(nom: str) -> str:
    """`nomic-embed-text` est annoncé `nomic-embed-text:latest`."""
    return nom if ":" in nom else f"{nom}:latest"


def empreinte(requests_mod, url: str = OLLAMA_URL, nom: str = MODELE,
              timeout: float = 3) -> str | None:
    """Les 12 premiers caractères de l'empreinte que sert Ollama pour `nom`
    (l'« ID » de `ollama list`). `None` si le modèle n'est pas installé.

    Lève l'exception de `requests` si Ollama ne répond pas : « pas de modèle »
    et « pas de serveur » ne se corrigent pas de la même façon.
    """
    r = requests_mod.get(f"{url}/api/tags", timeout=timeout)
    r.raise_for_status()
    noms = {nom, nom_complet(nom)}
    for m in r.json().get("models") or []:
        if m.get("name") in noms or m.get("model") in noms:
            e = (m.get("digest") or "")[:12]
            return e if _EMPREINTE.fullmatch(e) else None
    return None


def modeles_de_l_index(conn) -> list[tuple[str | None, str | None, int]]:
    """(modèle, empreinte, nombre d'extraits) pour chaque modèle présent."""
    return [tuple(r) for r in conn.execute(
        "SELECT modele, modele_empreinte, COUNT(*) FROM embeddings"
        " GROUP BY 1, 2 ORDER BY 3 DESC")]


def incompatibilite(conn, nom: str, emp: str | None) -> str | None:
    """Pourquoi l'index ne peut pas être interrogé avec ce modèle ; `None` s'il
    le peut (ou s'il est vide). La comparaison porte sur l'empreinte : deux
    noms pour une même empreinte sont un même modèle."""
    if emp is None:
        return (f"Le modèle d'embeddings « {nom} » n'est pas installé dans Ollama "
                f"sur cette machine : `ollama pull {nom}` sur un poste, "
                "`vps/ollama/poser-ollama.sh` (dépôt d'exploitation) sur le serveur.")
    presents = modeles_de_l_index(conn)
    if not presents or all(e == emp for _, e, _ in presents):
        return None
    vus = ", ".join(f"{m or 'modèle inconnu'} ({e or 'sans empreinte'}) : {n}"
                    for m, e, n in presents)
    return (f"L'index a été construit par un autre modèle que celui que sert "
            f"Ollama, {nom_complet(nom)} ({emp}) — extraits par modèle : {vus}. "
            "Leurs vecteurs ne se comparent pas. Reconstruire l'index : "
            "`python3 scripts/build_rag_index.py --reset`.")


# ─── L'état de la dernière indexation ─────────────────────────────────────────
# Il entre dans `sante.json`, lu au début de chaque session d'un assistant : il
# ne porte que des dates, des nombres, un motif d'une liste fermée
# (`collect_loop.forme_erreur`), et le nom et l'empreinte du modèle filtrés sur
# leur forme. Jamais le texte d'une erreur, qui peut venir d'un serveur.

def chemin_etat(dossier_base: Path) -> Path:
    return Path(dossier_base) / ETAT


def lire_etat(dossier_base: Path) -> dict | None:
    """Relu FILTRÉ : ne repart vers `sante.json` que ce qu'`ecrire_etat`
    aurait pu écrire, même si le fichier a été écrit par autre chose."""
    try:
        brut = json.loads(chemin_etat(dossier_base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(brut, dict):
        return None
    etat = {}
    for cle in ("dernier_essai", "dernier_succes"):
        val = brut.get(cle)
        etat[cle] = val if isinstance(val, str) and _DATE.fullmatch(val) else None
    echecs = brut.get("echecs_consecutifs")
    etat["echecs_consecutifs"] = echecs if isinstance(echecs, int) and echecs >= 0 else 0
    motif = brut.get("derniere_erreur")
    etat["derniere_erreur"] = None if motif is None else (
        motif if isinstance(motif, str) and _MOTIF.fullmatch(motif) else "autre")
    for cle in _MESURES:
        val = brut.get(cle)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            etat[cle] = val
    if isinstance(brut.get("modele"), str) and _NOM.fullmatch(brut["modele"]):
        etat["modele"] = brut["modele"]
    if isinstance(brut.get("empreinte"), str) and _EMPREINTE.fullmatch(brut["empreinte"]):
        etat["empreinte"] = brut["empreinte"]
    return etat


def ecrire_etat(dossier_base: Path, *, reussi: bool, motif: str | None = None,
                mesures: dict | None = None) -> dict:
    """Consigne un essai. Un échec garde la dernière réussite et compte les
    échecs de suite ; une réussite les remet à zéro."""
    avant = lire_etat(dossier_base) or {}
    maintenant = datetime.now().isoformat(timespec="seconds")
    etat = {
        "dernier_essai": maintenant,
        "dernier_succes": maintenant if reussi else avant.get("dernier_succes"),
        "echecs_consecutifs": 0 if reussi else int(avant.get("echecs_consecutifs") or 0) + 1,
        "derniere_erreur": None if reussi else (
            motif if motif and _MOTIF.fullmatch(motif) else "autre"),
    }
    for cle, val in (mesures or {}).items():
        if cle in _MESURES and isinstance(val, (int, float)) and not isinstance(val, bool):
            etat[cle] = val
        elif cle == "modele" and isinstance(val, str) and _NOM.fullmatch(val):
            etat[cle] = val
        elif cle == "empreinte" and isinstance(val, str) and _EMPREINTE.fullmatch(val):
            etat[cle] = val
    chemin = chemin_etat(dossier_base)
    provisoire = chemin.with_suffix(".tmp")
    provisoire.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    provisoire.replace(chemin)
    return etat


def alertes(etat: dict | None, maintenant: datetime | None = None) -> list[str]:
    """Les alertes de `sante.json`, dans des formes que `vigie_etat.sh` connaît
    déjà (celles d'un step de collecte). Aucun état : l'instance n'entretient
    pas d'index, il n'y a rien à dire."""
    if not etat:
        return []
    sortie = []
    echecs = etat.get("echecs_consecutifs") or 0
    if isinstance(echecs, int) and echecs > 0:
        sortie.append(f"index_sens : {echecs} échecs de suite — "
                      f"{etat.get('derniere_erreur') or '?'}")
    try:
        succes = datetime.fromisoformat(etat.get("dernier_succes") or "")
    except (TypeError, ValueError):
        return sortie
    age = ((maintenant or datetime.now()) - succes).total_seconds() / 86400
    if age > RETARD_ALERTE * TTL_JOURS:
        sortie.append(f"index_sens : {age:.0f} j sans passage abouti (TTL {TTL_JOURS} j)")
    return sortie
