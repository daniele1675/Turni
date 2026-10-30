# Turni

Legge i tuoi turni da **Amazon Moment** e li rende disponibili in tre modi:

- **App sul telefono** (PWA): vista settimanale, prossimo turno, pause e pranzo, ore totali, avviso se un turno cambia.
- **Calendario in abbonamento** (iPhone, Google Calendar, Outlook…): un evento per turno, aggiornato da solo.
- **Home Assistant**: lo stesso calendario come entità `calendar.*`, utile per le automazioni, più una notifica quando cambia qualcosa.

Gira come container Docker sul server di casa (ZimaOS). Ogni 4 ore (di notte no) apre il portale in un browser senza schermo, fa il login con le tue credenziali, legge la settimana corrente, le 3 successive e quella precedente, e salva tutto in `data/turni.json`.

---

## 1. Installazione su ZimaOS

Ti serve l'accesso SSH a ZimaOS (o il terminale dalla dashboard).

```bash
# copia la cartella "turni" sul server, ad esempio in /DATA/AppData/turni, poi:
cd /DATA/AppData/turni
cp .env.example .env
nano .env          # scrivi PORTAL_USERNAME e PORTAL_PASSWORD, salva
chmod 600 .env     # leggibile solo da root
docker compose up -d --build
```

La prima build scarica Chromium e richiede qualche minuto (immagine di circa 1 GB).

Se la password contiene spazi, `#` o virgolette, scrivila tra apici singoli: `PORTAL_PASSWORD='la mia#password'`.

### In alternativa: dalla schermata "Installa app personalizzata"

Quella schermata non sa costruire immagini, quindi l'immagine va costruita prima su GitHub:

1. Crea un repository su GitHub (ad esempio `turni`) e carica tutto il contenuto di questa cartella, compresa `.github/`. Al primo caricamento GitHub costruisce l'immagine da solo (scheda *Actions*, circa 5 minuti).
2. Su GitHub apri il tuo profilo → *Packages* → `turni` → *Package settings* → *Change visibility* → **Public**. Così ZimaOS la scarica senza login. L'immagine non contiene dati tuoi: le credenziali le metti solo su ZimaOS.
3. In ZimaOS: *Installa app personalizzata* → **YAML** → incolla il contenuto di `zimaos-app.yml` (usa già l'immagine `ghcr.io/daniele1675/turni`).
4. Passa a **Modulo** e, in *Variabili*, compila `PORTAL_USERNAME` e `PORTAL_PASSWORD`. Poi premi *Installa*.

Per aggiornare in futuro basta modificare i file su GitHub e, in ZimaOS, aggiornare l'app (o ricrearla): scarica la nuova immagine.

La prima prova del login, in questo caso, la fai dal terminale con `docker exec -it turni python -m app.scraper`, oppure guardando i log del container.

### Prima prova

Prima di lasciarlo andare da solo, verifica che il login funzioni:

```bash
docker compose run --rm turni python -m app.scraper
```

Deve stampare qualcosa come:

```
Lette 5 settimane (metodo: fullcalendar)
2026-11-01  11:30–20:00  (8 h)
2026-11-03  07:00–15:30  (8 h)
...
```

Se qualcosa non va, nella cartella `data/debug/` trovi uno screenshot della pagina in cui si è fermato (`ultima_pagina.png`), il suo HTML e il motivo. Mandameli (oscurando i dati personali) e sistemiamo il riconoscimento del login.

Poi guarda il token di accesso:

```bash
docker logs turni | grep Token
# oppure
cat data/access_token.txt
```

---

## 2. App sul telefono

Apri nel browser del telefono `http://IP-DI-ZIMAOS:8090/?token=IL-TUO-TOKEN` (il token viene salvato e tolto dall'indirizzo).

- **iPhone**: Safari → Condividi → *Aggiungi alla schermata Home*. Alla prima apertura dall'icona incolla di nuovo il token: le app sulla Home hanno uno spazio separato da Safari.
- **Android**: Chrome → menu ⋮ → *Installa app*.

Cosa trovi: il prossimo turno (o quello in corso, con quanto manca alla fine), la settimana da lunedì a domenica con una barra che mostra lavoro, pause, pranzo e attività (riunioni, formazione…), le ore della settimana. Tocca un giorno per vedere tutti i blocchi; scorri a destra/sinistra o usa le frecce per cambiare settimana. *Aggiorna* rilegge subito il portale (al massimo una volta ogni 5 minuti).

I giorni letti senza turni compaiono come **Riposo**; quelli che il portale non ha ancora pubblicato come **Non ancora pubblicato**.

**Fuori casa**: aggiungi un hostname al tunnel Cloudflare che usi già per Home Assistant (ad esempio `turni.tuodominio.it` → `http://IP-DI-ZIMAOS:8090`). Con HTTPS l'app funziona anche offline, mostrando gli ultimi turni scaricati.

---

## 3. Calendario del telefono

Nell'app tocca l'ingranaggio: c'è l'indirizzo del calendario, con le opzioni per includere pause e pranzo come eventi separati e un promemoria un'ora prima.

- **iPhone**: *Apri in Calendario (iPhone)*, oppure Impostazioni → Calendario → Account → Aggiungi account → Altro → *Aggiungi calendario sottoscritto*.
- **Google Calendar**: dal computer, calendar.google.com → *Altri calendari* → **+** → *Da URL*. Google scarica il calendario dai suoi server, quindi l'indirizzo deve essere quello pubblico del tunnel, non l'IP di casa. Google lo aggiorna con i suoi tempi (anche più di 12 ore): per vedere subito un cambio usa l'app o Home Assistant.

Ogni turno è un evento tipo *Turno 07:00–15:30 · Riunione*; nella descrizione ci sono le ore di lavoro (pranzo escluso) e tutti i blocchi.

---

## 4. Home Assistant

### Calendario

Impostazioni → Dispositivi e servizi → Aggiungi integrazione → **Remote Calendar**:

- Nome: `Turni di lavoro`
- URL: `http://IP-DI-ZIMAOS:8090/calendar.ics?token=IL-TUO-TOKEN`

Remote Calendar di suo rilegge il calendario solo ogni 24 ore. Per averlo aggiornato ogni ora, crea questa automazione (Impostazioni → Automazioni → Crea → menu ⋮ → Modifica in YAML):

```yaml
alias: Aggiorna calendario turni
triggers:
  - trigger: time_pattern
    minutes: 5
actions:
  - action: homeassistant.update_entity
    target:
      entity_id: calendar.turni_di_lavoro
mode: single
```

Il calendario si può usare come trigger: ad esempio "60 minuti prima dell'inizio di un evento di `calendar.turni_di_lavoro`" per accendere il clima o una sveglia.

### Notifica quando un turno cambia

1. Crea un'automazione con trigger webhook:

```yaml
alias: Turni cambiati
triggers:
  - trigger: webhook
    webhook_id: turni-cambiati-SCEGLI-UN-CODICE
    allowed_methods: [POST]
    local_only: true
actions:
  - action: notify.mobile_app_IL_TUO_TELEFONO
    data:
      title: "{{ trigger.json.title }}"
      message: "{{ trigger.json.message }}"
mode: queued
```

2. Nel file `.env` imposta
   `NOTIFY_WEBHOOK_URL=http://IP-DI-HOME-ASSISTANT:8123/api/webhook/turni-cambiati-SCEGLI-UN-CODICE`
   e riavvia: `docker compose up -d`.

Riceverai messaggi tipo *mar 3/11: 07:00–15:30 → 08:00–16:30*.

C'è anche `GET /api/next?token=…`, che restituisce il prossimo turno in JSON, se vuoi un sensore REST.

---

## Due persone (turni inseriti a mano)

L'app può essere usata anche da una seconda persona che non ha il portale e inserisce i turni a mano.

**Aggiungerla.** Dal tuo telefono: ingranaggio → *Persone* → scrivi il nome → *Aggiungi*. Poi *Copia link app* e mandale il link: aprendolo sul suo telefono entra con il suo profilo (sul telefono va aggiunta alla schermata Home come per te).

**Cosa vede ognuno.** In alto c'è la scelta della persona: i tuoi turni, i suoi, e *Insieme*, che mette i due turni uno sotto l'altro e segnala i giorni in cui siete **liberi insieme** (con il prossimo in evidenza). Ognuno vede i turni dell'altro, ma modifica solo i propri.

**Inserire i turni.** Tocca un giorno: si apre una scheda con i **modelli** salvati (es. "Mattina 06:00–14:00, pausa 30'") da usare con un tocco, oppure gli orari da scrivere a mano: inizio, fine, pausa e, se vuoi, l'ora della pausa e una nota. Un turno che finisce dopo mezzanotte (es. 22:00–06:00) va bene. Spuntando *Salva questi orari anche come modello* gli orari diventano un modello per le volte successive. I modelli si gestiscono anche da ingranaggio → *I tuoi modelli di turno*.

**Calendario e Home Assistant.** Anche la seconda persona ha il suo calendario: lo trova nelle sue impostazioni, oppure lo copi tu da *Persone* → *Copia link calendario*. In Home Assistant si aggiunge come un secondo **Remote Calendar** (ad esempio `Turni di Giulia`).

**Sicurezza.** Se un link finisce nelle mani sbagliate, *Nuovo link* lo sostituisce e quello vecchio smette di funzionare. *Rimuovi* cancella la persona con i suoi turni e modelli. I dati stanno in `data/persone.json`.

---

## Sicurezza

- Username e password stanno solo nel file `.env` sul tuo server e vengono usati solo per fare il login al portale. Non finiscono nei log, né nei file di debug.
- L'app, l'API e il calendario richiedono il token. Chi ha il link del calendario vede i tuoi orari: se lo condividi per sbaglio, cambia `ACCESS_TOKEN` nel `.env` e riavvia.
- Dopo un login rifiutato le letture automatiche si fermano per 12 ore, per non rischiare il blocco dell'account. Puoi sempre riprovare a mano con *Aggiorna*.
- Il programma legge solo la pagina *Your schedule*: non tocca richieste di ferie, extra time o altro.
- È un accesso automatico al portale aziendale con il tuo account: se il regolamento interno lo vieta, tienine conto.

## Note e limiti

- I turni si leggono direttamente da FullCalendar (la libreria che disegna il calendario del portale). Se non fosse possibile, il programma li ricava dalla posizione dei blocchi sulla griglia, con precisione di 5 minuti. Il metodo usato compare in `docker compose run --rm turni python -m app.scraper` e nell'app (Impostazioni).
- Le ore di lavoro escludono solo il **pranzo**: le pause da 15 minuti sono contate come lavoro.
- **Extra time**: nella pagina che mi hai mandato non c'erano slot di extra time, quindi il riconoscimento è a intuito (blocchi di sfondo o con "extra"/"VTO" nel nome). Gli slot riconosciuti compaiono sotto la settimana come *Extra time disponibile* e non vengono contati come turni.
- La pagina del portale avvisa di una migrazione ad **A to Z**: se un giorno ti spostano lì, la parte di lettura andrà riscritta per il nuovo sito.

## Aggiornare il programma

```bash
cd /DATA/AppData/turni
docker compose up -d --build
```

I dati (`data/`) e la configurazione (`.env`) restano.

## File

```
app/scraper.py   login e lettura delle settimane (Playwright + Chromium)
app/extract.js   lettura dei turni dentro la pagina del portale
app/model.py     dai blocchi ai turni, archivio, rilevamento modifiche
app/ics.py       feed calendario
app/main.py      server web, aggiornamento periodico, notifiche
app/static/      app per il telefono
data/            dati e sessione del browser (creata al primo avvio)
```
