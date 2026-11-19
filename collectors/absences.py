"""
absences.py — Les communes qu'un jeu national ne couvre pas, relevées par le
collecteur qui l'a lu.

Un collecteur qui ne trouve pas sa commune dans le fichier n'écrit rien. Sans
trace, la page ne distingue pas « ce jeu ne parle pas de la commune » de « nous
n'avons rien collecté » : la première est un fait sur la source, la seconde un
défaut de la chaîne. Décision du 29/10/2026 : une absence n'arrête pas un
collecteur, elle devient une lacune affichée (`/couverture`, via
`scripts/snapshot/couverture.py`).

À ne pas confondre avec `lacunes.py`, qui relève ce que les DOSSIERS ignorent.

Chaque passe REMPLACE les absences de son step : une commune que le millésime
suivant ajoute cesse d'être une lacune sans qu'on y touche.
"""


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sources_absentes (
            step        TEXT NOT NULL,
            insee       TEXT NOT NULL,
            jeu         TEXT NOT NULL,   -- libellé lisible du jeu
            page_jeu    TEXT,            -- la fiche data.gouv (ou équivalent)
            source_url  TEXT,            -- le fichier lu
            millesime   TEXT,            -- celui du fichier lu
            releve_le   TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (step, insee)
        )
    """)


def noter(conn, step: str, demandees, trouvees, *, jeu: str, page_jeu: str,
          source_url: str, millesime) -> list[str]:
    """Remplace les absences du step ; rend les codes absents, triés."""
    ensure_table(conn)
    absentes = sorted(set(demandees) - set(trouvees))
    conn.execute("DELETE FROM sources_absentes WHERE step=?", (step,))
    conn.executemany(
        "INSERT INTO sources_absentes"
        " (step, insee, jeu, page_jeu, source_url, millesime) VALUES (?,?,?,?,?,?)",
        [(step, i, jeu, page_jeu, source_url, str(millesime)) for i in absentes])
    for i in absentes:
        print(f"  [{step}] {i} absente du fichier : lacune notée")
    return absentes
