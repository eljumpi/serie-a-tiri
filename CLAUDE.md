# serie-a-tiri
Classifiche alternative per campionato. Campionati: Serie A, Premier League. Statistica attuale: tiri in porta.
Più tiri in porta in una partita = 3 punti, pari = 1 a testa. Solo partite concluse.
Pari punti: diff TP, poi TP fatti, poi alfabetico.
Fonte: API pubbliche ESPN (scoreboard + summary, boxscore.teams[].statistics, name="shotsOnTarget").
Solo libreria standard Python 3. Le pagine HTML sono generate da build.py: non modificarle a mano.
Mai inventare dati: se una fonte cambia o non risponde, segnalalo.
Workflow giornaliero 06:00 UTC; commit solo se i dati cambiano.
Ruoli: la chat Claude scrive/rivede il codice e prepara i prompt; tu esegui, testi in Firefox, gestisci git/GitHub.
Chiudi ogni lavoro con un riepilogo breve, con numeri verificabili, pensato per essere incollato in chat.

## Multi-campionato
- `build.py` ha due registri: `LEAGUES` (codice ESPN, nome, slug) e `STATS` (id ESPN, nome, slug, abbr, unit, implemented).
  Aggiungere un campionato = una riga in `LEAGUES`. Aggiungere una statistica = una riga in `STATS`
  (`implemented: False` la mostra come "prossimamente" nel menù, non cliccabile).
- Cache separata per campionato: `data/raw/<league-slug>/events.json` (partite) e `data/raw/<league-slug>/<stat-slug>.json` (statistica).
- Output: `index.html` (home con una card per campionato/statistica) e `/<league-slug>/<stat-slug>.html`; dati incorporati in ogni pagina.
  Tabelle in `data/processed/<league-slug>/<stat-slug>/tables.json` (+ `meta.json` per le card della home).
- Le chiavi `tpf`/`tps`/`sf`/`sa` nei dati sono storiche: valgono per qualunque statistica (fatta/subita).
- `python3 build.py` elabora tutti i campionati; `--league ita.1|serie-a` ne elabora uno; `--offline` usa solo la cache; `--full` riscarica tutto.
- Se un campionato fallisce (nessun dato, errore, richieste ESPN fallite) gli altri vengono comunque generati e
  `build.py` esce con codice 1; il workflow pubblica ciò che è riuscito e poi rende il job rosso.

## Dati da verificare
Partita con calcio d'inizio passato da oltre 4 ore e non conclusa per ESPN (o conclusa senza risultato leggibile):
non si scarta in silenzio. Compare nel riquadro "Dati da verificare" della pagina e viene stampata a terminale.
Non entra nelle classifiche finché non è conclusa e non compare fra le partite da giocare.

## Controlli
- Interni (a ogni build): somma punti = 3*V + N, presenze = 2 * partite, V+N+P = G, casa + trasferta = totale.
- G per squadra confrontate con l'endpoint standings ESPN (`/apis/v2/sports/soccer/<codice>/standings`), solo online, a ogni build.
- Regressione: dopo un refactor, con la stessa cache (`--offline`) `data/processed/serie-a/tiri-in-porta/tables.json`
  deve restare identico (sha256) a prima del refactor.
