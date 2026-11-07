# Classifiche alternative per tiri in porta

Classifiche alternative dei campionati (oggi **Serie A** e **Premier League**): i punti non dipendono
dai gol, ma dai **tiri in porta**. Ogni pagina mostra tre tabelle (Totale, Casa, Trasferta), il confronto
con la classifica reale (posizione e punti veri, più il Delta di posizioni), la forma recente, le partite
in programma e, in una sezione comprimibile, le partite giocate. Un click su una squadra o su una partita
apre il grafico dei punti cumulati.

Sito: https://eljumpi.github.io/serie-a-tiri/

- Home: una card per ogni campionato/statistica
- Serie A: https://eljumpi.github.io/serie-a-tiri/serie-a/tiri-in-porta.html
- Premier League: https://eljumpi.github.io/serie-a-tiri/premier-league/tiri-in-porta.html

Il menù in alto ha due livelli (Campionato, Statistica). Le statistiche non ancora implementate
compaiono come "prossimamente".

## Regola dei punti

In ogni partita:

- più tiri in porta dell'avversario: **3 punti**
- stesso numero di tiri in porta: **1 punto** a testa
- meno tiri in porta: **0 punti**

A parità di punti si ordina per differenza tiri in porta, poi per tiri in porta fatti, poi per nome.
Delta = posizione nella classifica reale meno posizione nella classifica a tiri in porta
(positivo = la squadra guadagna posizioni con questa regola).

Contano solo le partite concluse. Quelle concluse per cui ESPN non pubblica i tiri in porta sono escluse
dal calcolo ed elencate in fondo alla pagina. Una partita con calcio d'inizio passato da oltre 4 ore ma non
conclusa per ESPN non viene scartata in silenzio: compare nel riquadro **Dati da verificare** della pagina e
nell'output di `build.py`, e resta fuori dalle classifiche finché non è conclusa.

## Fonte dati

API pubbliche di ESPN, senza chiave (scoreboard, summary, standings). Se ESPN cambia formato o non risponde,
lo script lo segnala invece di inventare dati. Serve solo Python 3 (libreria standard).

## Aggiungere un campionato o una statistica

In `build.py`:

- campionato: una riga in `LEAGUES` (codice ESPN, nome, slug). Esempio: `{"code": "esp.1", "name": "LaLiga", "slug": "laliga"}`
- statistica: una riga in `STATS` (nome ESPN in `boxscore.teams[].statistics`, nome, slug, `implemented`)

Le pagine `/<league-slug>/<stat-slug>.html` e le card della home vengono generate da sole.
Le cache stanno in `data/raw/<league-slug>/`.

## Aggiornare in locale

```bash
python3 build.py                    # tutti i campionati, aggiornamento incrementale
python3 build.py --league eng.1     # un solo campionato (codice ESPN o slug)
python3 build.py --full             # riscarica tutta la stagione (qualche minuto per campionato)
python3 build.py --offline          # ricostruisce dalla cache, senza rete
```

Poi apri `index.html` in Firefox. Codice di uscita: 0 tutto ok, 1 almeno un campionato fallito o con
dati parziali (gli altri sono comunque generati), 2 controlli interni in anomalia. Per pubblicare:

```bash
git add data index.html ./*/*.html && git commit -m "Aggiornamento" && git push
```

## Aggiornamento automatico

Il workflow `.github/workflows/update.yml` gira ogni giorno alle 06:00 UTC, esegue `build.py` per tutti i
campionati e committa `data/` e le pagine HTML solo se sono cambiati. Se un campionato fallisce, gli altri
vengono comunque pubblicati e il job risulta fallito (rosso) con un messaggio di errore esplicito.

### Lanciarlo a mano

- Dal sito: scheda **Actions** del repo, workflow **Aggiorna classifiche**, **Run workflow**.
- Da terminale: `gh workflow run update.yml` (e `gh run watch` per seguirlo).

### Se GitHub lo disattiva

GitHub disabilita i workflow schedulati dopo 60 giorni senza attività nel repo. Per riattivarlo:

- scheda **Actions**, workflow **Aggiorna classifiche**, pulsante **Enable workflow**; oppure
- `gh workflow enable update.yml`.

Poi lancialo a mano una volta per verificare. I commit automatici del workflow contano come
attività solo finché il workflow è attivo.
