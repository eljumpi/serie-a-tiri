# Classifica Serie A per tiri in porta

Una classifica alternativa della Serie A: i punti non dipendono dai gol, ma dai **tiri in porta**.
La pagina mostra tre tabelle (Totale, Casa, Trasferta), il confronto con la classifica reale
(posizione e punti veri, più il Delta di posizioni), la forma recente, le partite giocate e quelle
in programma. Un click su una squadra o su una partita apre il grafico dei punti cumulati.

Sito: https://eljumpi.github.io/serie-a-tiri/

## Regola dei punti

In ogni partita:

- più tiri in porta dell'avversario: **3 punti**
- stesso numero di tiri in porta: **1 punto** a testa
- meno tiri in porta: **0 punti**

A parità di punti si ordina per differenza tiri in porta, poi per tiri in porta fatti.
Delta = posizione nella classifica reale meno posizione nella classifica a tiri in porta
(positivo = la squadra guadagna posizioni con questa regola).

Le partite concluse per cui ESPN non pubblica i tiri in porta sono escluse dal calcolo
e elencate in fondo alla pagina.

## Fonte dati

API pubbliche di ESPN, senza chiave. Se ESPN cambia formato o non risponde, lo script lo segnala
invece di inventare dati. Serve solo Python 3 (libreria standard).

## Aggiornare in locale

```bash
python3 build.py            # aggiornamento incrementale, rigenera index.html
python3 build.py --full     # riscarica tutta la stagione (~2 minuti)
python3 build.py --offline  # ricostruisce dalla cache, senza rete
```

Poi apri `index.html` in Firefox. Per pubblicare:

```bash
git add data index.html && git commit -m "Aggiornamento" && git push
```

## Aggiornamento automatico

Il workflow `.github/workflows/update.yml` gira ogni giorno alle 06:00 UTC, esegue `build.py`
e committa `data/` e `index.html` solo se sono cambiati.

### Lanciarlo a mano

- Dal sito: scheda **Actions** del repo, workflow **Aggiorna classifica**, **Run workflow**.
- Da terminale: `gh workflow run update.yml` (e `gh run watch` per seguirlo).

### Se GitHub lo disattiva

GitHub disabilita i workflow schedulati dopo 60 giorni senza attività nel repo. Per riattivarlo:

- scheda **Actions**, workflow **Aggiorna classifica**, pulsante **Enable workflow**; oppure
- `gh workflow enable update.yml`.

Poi lancialo a mano una volta per verificare. I commit automatici del workflow contano come
attività solo finché il workflow è attivo.
