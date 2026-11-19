#!/usr/bin/env python3
"""
banc_essai_ia.py — quel modèle sait lire un procès-verbal ?

Compare plusieurs modèles sur de VRAIS comptes rendus de l'instance, avec
exactement le prompt de l'atelier (importé de `collectors/extraction.py`, jamais
recopié). Rien n'est écrit en base : le banc lit, mesure, et affiche.

CE QU'IL MESURE, ET POURQUOI

  propositions          combien de lignes le modèle propose. Beaucoup n'est pas
                        bon signe en soi — un modèle bavard invente.
  citations vérifiées   la part des propositions dont la phrase citée figure
                        VRAIMENT dans le texte. C'est la mesure qui compte :
                        elle ne demande aucune confiance envers le modèle, et
                        elle attrape l'invention sans relecture humaine.
  réponses illisibles   tranches où le modèle n'a pas rendu de JSON exploitable.
  durée                 sur une machine de collecte, un modèle qui met dix
                        minutes par PV ne sera pas utilisé.

Le taux de citations vérifiées est un plancher, pas une note : une citation
retrouvée prouve que la phrase existe, pas que la ligne l'interprète bien. Le
banc dit quel modèle ne ment pas ; il ne dit pas lequel comprend.

MODÈLES DISTANTS (API Anthropic)

  Préfixe `anthropic:` : `--modeles gemma4:26b,anthropic:claude-opus-5-5`.
  Le texte des PV quitte alors le Mac : le banc refuse sans `--envoi-distant`,
  tant que les préalables D2 (DPA du canal API, registre, mentions légales)
  ne sont pas levés, c'est un geste délibéré, pas un réglage par défaut.
  Le SDK n'est PAS une dépendance du moteur (l'IA ne tourne jamais sur le
  VPS) : `venv/bin/pip install anthropic` sur le Mac seulement. Clé :
  `ANTHROPIC_API_KEY` ou profil `ant auth login`, jamais dans le dépôt.
  Ni `temperature` (refusée par l'API) ni repli sur un autre modèle en cas de
  refus : un banc qui change de modèle en silence ne mesure plus rien.

Usage :
  venv/bin/python scripts/banc_essai_ia.py
  venv/bin/python scripts/banc_essai_ia.py --modeles qwen2.5:14b,gemma4:26b --actes 3
  venv/bin/python scripts/banc_essai_ia.py --objet budget_vote --json rapport.json
  venv/bin/python scripts/banc_essai_ia.py --envoi-distant \\
      --modeles gemma4:26b,anthropic:claude-opus-5-5 --effort medium --passes 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from collectors import extraction  # noqa: E402
from collectors.db import get_conn  # noqa: E402

# `127.0.0.1` et non `localhost` : sur ce Mac, `localhost` résout en IPv6 alors
# qu'Ollama n'écoute qu'en IPv4 — une demi-journée perdue le 20/08/2026 sur un
# « connection refused » qui ne concernait pas le service qu'on croyait.
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

MODELES_PAR_DEFAUT = ["qwen2.5:14b", "gemma4:26b"]

PREFIXE_ANTHROPIC = "anthropic:"
# Les modèles Claude récents réfléchissent toujours, et la réflexion se paie sur
# `max_tokens` : le budget de l'atelier (4 096) les laisserait muets, le piège
# exact de `ReponseSansContenu`. Plancher propre au canal distant.
MAX_TOKENS_ANTHROPIC = 16_000

_client_anthropic = None


def est_distant(modele: str) -> bool:
    return modele.startswith(PREFIXE_ANTHROPIC)


def appel_anthropic(modele: str, effort: str, conso: dict):
    """Même contrat que `appel_pour`, par l'API Messages d'Anthropic.

    `conso` accumule les tokens et le modèle réellement servi : un banc
    distant qui ne dit pas ce qu'il a coûté ne permet pas de trancher.
    """
    global _client_anthropic
    import anthropic  # import tardif : hors des dépendances du moteur
    if _client_anthropic is None:
        _client_anthropic = anthropic.Anthropic()
    nom = modele[len(PREFIXE_ANTHROPIC):]

    def appel(message, system="", max_tokens=2048, json_strict=False):
        # `json_strict` n'a pas d'équivalent sans schéma : les gabarits de
        # l'atelier décrivent la forme en texte, et `json_du_modele` lit la
        # réponse comme pour Ollama. Mêmes règles pour tous.
        r = _client_anthropic.messages.create(
            model=nom,
            max_tokens=max(max_tokens, MAX_TOKENS_ANTHROPIC),
            # Le système (règles de l'atelier) est identique à chaque tranche :
            # mis en cache, il n'est payé plein tarif qu'une fois.
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": message}],
            output_config={"effort": effort},
        )
        u = r.usage
        conso["entree"] += u.input_tokens
        conso["sortie"] += u.output_tokens
        conso["cache_lu"] += u.cache_read_input_tokens or 0
        conso["cache_ecrit"] += u.cache_creation_input_tokens or 0
        conso["servi_par"].add(r.model)

        if r.stop_reason == "refusal":
            cat = getattr(r.stop_details, "category", None) if r.stop_details else None
            raise extraction.ReponseSansContenu(
                f"refus du modèle (catégorie : {cat or 'non précisée'}).")
        texte = "".join(b.text for b in r.content if b.type == "text").strip()
        if texte:
            return texte
        if r.stop_reason == "max_tokens":
            raise extraction.ReponseSansContenu(
                "réponse coupée par la limite de tokens — budget parti dans la "
                "réflexion ; baisser --effort ou relever MAX_TOKENS_ANTHROPIC.")
        raise extraction.ReponseSansContenu("le modèle a répondu sans aucun texte.")
    return appel


def appel_pour(modele: str):
    """Fabrique la fonction d'appel attendue par `extraction.extraire`.

    Format « chat completions », celui que parlent aussi bien Ollama que les
    passerelles distantes : le banc mesure donc le modèle, pas un dialecte.
    """
    def appel(message, system="", max_tokens=2048, json_strict=False):
        charge = {
            "model": modele,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": message}],
        }
        if json_strict:
            charge["response_format"] = {"type": "json_object"}
            charge["temperature"] = 0
        r = requests.post(f"{OLLAMA}/v1/chat/completions", json=charge, timeout=900)
        r.raise_for_status()
        # Le même lecteur que l'atelier : un modèle à raisonnement rend un
        # contenu vide avec un HTTP 200 quand son budget de tokens est épuisé,
        # et le banc doit dire ça plutôt que « 0 proposition ».
        return extraction.contenu_openai(r.json())
    return appel


def actes_dessai(conn, combien: int) -> list[dict]:
    """Les procès-verbaux les plus consistants de la base.

    Choisis par longueur de texte, et non au hasard : un compte rendu de trois
    lignes ne départage aucun modèle. Ce sont de vrais documents de l'instance,
    avec leurs césures de PDF et leurs en-têtes répétés — les extraire
    proprement est précisément ce qu'on veut éprouver.
    """
    return [dict(r) for r in conn.execute("""
        SELECT id, date, title, content, LENGTH(content) AS taille
        FROM events
        WHERE type IN ('deliberation','conseil_municipal','délibérations_cc','pv_cc')
          AND content IS NOT NULL AND LENGTH(content) > 4000
        ORDER BY LENGTH(content) DESC
        LIMIT ?""", (combien,))]


def modeles_disponibles() -> set[str]:
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=5)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return set()


def fondee(p: dict) -> bool:
    """Phrase retrouvée ET montant écrit dans la phrase (quand il y en a un)."""
    return bool(p.get("citation_verifiee")) and p.get("montant_dans_citation") is not False


def essayer(modele: str, actes: list[dict], objet: str, effort: str) -> dict:
    resultats, propositions_vues = [], []
    conso = {"entree": 0, "sortie": 0, "cache_lu": 0, "cache_ecrit": 0,
             "servi_par": set()}
    appel = (appel_anthropic(modele, effort, conso) if est_distant(modele)
             else appel_pour(modele))
    debut_total = time.time()
    for acte in actes:
        debut = time.time()
        try:
            r = extraction.extraire(acte["content"], objet, appel)
        except Exception as e:
            print(f"    ✗ {acte['date']} : {type(e).__name__}")
            resultats.append({"acte": acte["id"], "erreur": str(e)})
            continue
        duree = time.time() - debut
        for p in r["propositions"]:
            p["acte"] = acte["id"]
        n = len(r["propositions"])
        part = f"{r['citations_verifiees']}/{n}" if n else "—"
        sans_montant = sum(1 for p in r["propositions"]
                           if p["montant_dans_citation"] is False)
        print(f"    {acte['date']}  {r['tranches']} tranche(s)  "
              f"{n:3} propositions  citations {part:>7}  {duree:6.1f}s"
              + (f"  ✗ {sans_montant} montant(s) absent(s) de la citation"
                 if sans_montant else "")
              + (f"  ⚠ {r['reponses_illisibles']} sans réponse exploitable"
                 if r["reponses_illisibles"] else ""))
        # Le motif, pas seulement le compte : « 5 réponses illisibles » n'a
        # rien appris pendant deux essais, « le modèle n'est pas arrivé à la
        # réponse » se corrige en une variable d'environnement.
        for motif in r.get("motifs_echec", []):
            print(f"        ↳ {motif}")
        resultats.append({"acte": acte["id"], "date": acte["date"],
                          "duree": round(duree, 1), **{k: v for k, v in r.items()
                                                       if k != "propositions"}})
        propositions_vues.extend(r["propositions"])

    n = len(propositions_vues)
    verifiees = sum(1 for p in propositions_vues if p.get("citation_verifiee"))
    montants_absents = sum(1 for p in propositions_vues
                           if p.get("montant_dans_citation") is False)
    if est_distant(modele):
        print(f"    tokens : {conso['entree']} entrée, {conso['sortie']} sortie, "
              f"cache {conso['cache_lu']} lus / {conso['cache_ecrit']} écrits — "
              f"servi par {', '.join(sorted(conso['servi_par'])) or '?'}")
    return {
        "modele": modele,
        "effort": effort if est_distant(modele) else None,
        "tokens": ({k: (sorted(v) if isinstance(v, set) else v)
                    for k, v in conso.items()} if est_distant(modele) else None),
        "duree_totale": round(time.time() - debut_total, 1),
        "propositions": n,
        "citations_verifiees": verifiees,
        "montants_absents": montants_absents,
        "fondees": sum(1 for p in propositions_vues if fondee(p)),
        "illisibles": sum(r.get("reponses_illisibles", 0) for r in resultats),
        "detail": resultats,
        # TOUTES les propositions : un échantillon de cinq ne permet ni de
        # relire les citations introuvables, ni de mesurer le rappel.
        "liste": propositions_vues,
    }


def cle(p: dict, objet: str):
    """Ce qui fait qu'une proposition « revient » d'un passage à l'autre.

    Le montant quand il y en a un (la citation varie d'une virgule entre deux
    passages), sinon le début de la citation.
    """
    champ = extraction.CHAMP_MONTANT.get(objet)
    try:
        return (p.get("acte"), round(float(p.get(champ)), 2))
    except (TypeError, ValueError):
        return (p.get("acte"), extraction._aplatir(p.get("citation", ""))[:60])


def synthese(passes: list[dict], objet: str) -> dict:
    """Moyennes sur les passages, et ce qui revient à CHAQUE passage.

    Un modèle sans `temperature` ne rend pas deux fois la même chose : au banc du
    31/10/2026, Opus a proposé 6 puis 2 flux sur le même PV. Une proposition
    qui revient à chaque passage n'est pas plus vraie, mais un écart entre
    deux passages dit qu'un seul ne suffit pas à juger.
    """
    total = sum(p["propositions"] for p in passes)
    ensembles = [{cle(x, objet) for x in p["liste"]} for p in passes]
    union = set().union(*ensembles) if ensembles else set()
    stables = set.intersection(*ensembles) if ensembles else set()
    pc = (lambda k: round(100 * sum(p[k] for p in passes) / total) if total else 0)
    return {
        "passes": len(passes),
        "propositions_par_passe": round(total / len(passes), 1),
        "citations_pc": pc("citations_verifiees"),
        "montants_absents": sum(p["montants_absents"] for p in passes),
        "fondees_pc": pc("fondees"),
        "stables": len(stables),
        "union": len(union),
        "duree_par_passe": round(sum(p["duree_totale"] for p in passes) / len(passes)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare des modèles sur de vrais PV")
    ap.add_argument("--modeles", default=",".join(MODELES_PAR_DEFAUT),
                    help="liste séparée par des virgules ; préfixe "
                         f"« {PREFIXE_ANTHROPIC} » pour l'API Anthropic")
    ap.add_argument("--objet", default="flux", choices=list(extraction.GABARITS))
    ap.add_argument("--actes", type=int, default=2)
    # Le terminal ne montre que des mesures : un assistant qui lance le banc
    # lit sa sortie, et le texte d'un PV ou d'une réponse du modèle n'y a pas
    # sa place (audit du 06/11/2026). Propositions et citations : ce fichier.
    ap.add_argument("--json", default=str(Path(__file__).resolve().parent.parent
                                          / "audits" / "banc_essai_ia.json"),
                    help="rapport complet, propositions et citations comprises "
                         "(défaut : audits/banc_essai_ia.json, hors dépôt)")
    ap.add_argument("--envoi-distant", action="store_true",
                    help="autorise l'envoi du texte des PV à l'API Anthropic")
    ap.add_argument("--effort", default="medium",
                    choices=["low", "medium", "high", "xhigh", "max"],
                    help="effort des modèles distants (défaut : medium)")
    ap.add_argument("--passes", type=int, default=1,
                    help="passages par modèle (3 pour juger de la stabilité)")
    args = ap.parse_args()
    if args.passes < 1:
        ap.error("--passes : au moins 1")

    demandes = [m.strip() for m in args.modeles.split(",") if m.strip()]
    distants = [m for m in demandes if est_distant(m)]
    locaux = [m for m in demandes if not est_distant(m)]

    if distants:
        if not args.envoi_distant:
            print(f"Modèles distants demandés ({', '.join(distants)}) : le texte "
                  "des PV quitterait le Mac.\nAjouter --envoi-distant une fois "
                  "les préalables D2 levés (DPA du canal API, registre).")
            sys.exit(1)
        try:
            import anthropic  # noqa: F401
        except ImportError:
            print("SDK absent : venv/bin/pip install anthropic (sur le Mac, "
                  "jamais dans requirements.txt).")
            sys.exit(1)

    if locaux:
        dispo = modeles_disponibles()
        if not dispo:
            print(f"Aucun modèle joignable sur {OLLAMA} — Ollama tourne-t-il ?")
            sys.exit(1)
        absents = [m for m in locaux if m not in dispo]
        if absents:
            print(f"Absents d'Ollama, ignorés : {', '.join(absents)}")
            print(f"Disponibles : {', '.join(sorted(dispo))}")
        demandes = [m for m in demandes if est_distant(m) or m in dispo]
    if not demandes:
        sys.exit(1)

    conn = get_conn(read_only=True)
    actes = actes_dessai(conn, args.actes)
    conn.close()
    if not actes:
        print("Aucun procès-verbal assez consistant dans cette base.")
        sys.exit(1)

    print(f"\nBanc d'essai — objet « {args.objet} », {len(actes)} acte(s), "
          f"{sum(a['taille'] for a in actes) // 1000} k caractères")
    for a in actes:
        print(f"  · acte {a['id']}  {a['date']}  {a['taille']:6} car.")

    rapports = []
    for modele in demandes:
        passes = []
        for i in range(args.passes):
            print(f"\n  {modele}" + (f" — passage {i + 1}/{args.passes}"
                                     if args.passes > 1 else ""))
            passes.append(essayer(modele, actes, args.objet, args.effort))
        rapports.append({"modele": modele, "synthese": synthese(passes, args.objet),
                         "passes": passes})

    print("\n" + "─" * 96)
    print(f"{'modèle':32} {'prop./passe':>11} {'citation OK':>11} "
          f"{'montant ✗':>9} {'fondées':>8} {'stables':>9} {'durée/passe':>12}")
    for r in rapports:
        s = r["synthese"]
        # Guillemets non imbriqués : la CI joue aussi sur Python 3.11, où une
        # f-string ne peut pas contenir le même guillemet qu'elle.
        stables = "{}/{}".format(s["stables"], s["union"])
        print(f"{r['modele']:32} {s['propositions_par_passe']:>11} "
              f"{s['citations_pc']:>9} % {s['montants_absents']:>9} "
              f"{s['fondees_pc']:>6} % {stables:>9} {s['duree_par_passe']:>11}s")
    print("─" * 96)
    print("« fondées » = phrase retrouvée dans le PV ET montant écrit dans la phrase.\n"
          "C'est un PLANCHER : il ne dit pas que la ligne interprète bien, ni ce "
          "qui manque.\n« stables » = propositions revenues à chaque passage, sur "
          "toutes celles vues.")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"objet": args.objet,
                        "actes": [{k: v for k, v in a.items() if k != "content"}
                                  for a in actes],
                        "rapports": rapports}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\nPropositions et citations, à relire soi-même → {args.json}")


if __name__ == "__main__":
    main()
