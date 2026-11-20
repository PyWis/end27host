# Pubblicazione su Hostinger

## 1. Compatibilità: quale piano serve

Endurance 2027 è un'app **Python/Flask** con database SQLite, servita da gunicorn.

| Piano Hostinger | Compatibile? | Perché |
|---|---|---|
| Web Hosting (Single / Premium / Business) | ❌ No | Solo PHP/Node.js, niente Python né accesso root |
| Cloud Hosting (Startup / Professional / Enterprise) | ❌ No | Stessi limiti del web hosting condiviso |
| Hostinger Website Builder / Horizons | ❌ No | Non eseguono codice personalizzato |
| **VPS (KVM 1, 2, 4, 8)** | ✅ **Sì** | Hostinger supporta Python/Flask **solo su VPS** (root, gestito da te) |

**Consiglio:** VPS **KVM 1** (1 vCPU, 4 GB RAM) basta e avanza. Sistema operativo: **Ubuntu 24.04 LTS** (template "Plain OS", senza pannello).

Verifica fatta sul codice:

- Python ≥ 3.9 (Ubuntu 24.04 ha 3.12) ✅
- Dipendenze tutte da `pip`, nessuna libreria di sistema da compilare ✅
- SQLite incluso in Python, nessun database esterno da installare ✅
- Avvio con gunicorn + backup `flask --app run backup` provati ✅
- Servizi esterni (Stripe, Brevo, Google Calendar) via HTTPS in uscita: nessun blocco sui VPS ✅
- Il limite di 5 login falliti è in memoria **per processo**: si usa **1 worker gunicorn con più thread** (vedi §6) ✅
- Gli orari (ordini, scadenze link) usano l'ora locale del server: va impostato il fuso **Europe/Rome** (§3), altrimenti risultano in UTC ⚠️

---

## 2. Acquisto VPS e dominio

1. Acquista **VPS KVM 1** → scegli **Ubuntu 24.04** e una password di root (o meglio una chiave SSH).
2. In hPanel → **VPS → Panoramica** annota l'**indirizzo IP** del server.
3. In hPanel → **Domini → DNS / Nameserver** del tuo dominio crea/modifica:

| Tipo | Nome | Valore | TTL |
|---|---|---|---|
| A | `@` | IP del VPS | 3600 |
| A | `www` | IP del VPS | 3600 |

Elimina eventuali altri record A/AAAA su `@` e `www` (es. quelli del parcheggio Hostinger). La propagazione può richiedere da pochi minuti a qualche ora.

Negli esempi sotto il dominio è `tuodominio.it`: sostituiscilo ovunque.

---

## 3. Primo accesso e preparazione del server

Dal tuo computer (o dal **Terminale del browser** in hPanel → VPS):

```bash
ssh root@IP_DEL_VPS
```

Aggiorna il sistema, imposta il fuso orario e installa i pacchetti:

```bash
apt update && apt upgrade -y
timedatectl set-timezone Europe/Rome
apt install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx sqlite3 ufw
```

Firewall (lascia aperti solo SSH e web):

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
```

Utente dedicato senza privilegi per l'app:

```bash
adduser --system --group --home /srv/endurance2027 endurance
```

---

## 4. Caricare il codice

**Opzione A – da GitHub (consigliata, aggiornamenti con `git pull`)**

Il repository è privato, quindi serve una *deploy key*:

```bash
sudo -u endurance mkdir -p -m 700 /srv/endurance2027/.ssh
sudo -u endurance ssh-keygen -t ed25519 -N "" -f /srv/endurance2027/.ssh/id_ed25519
cat /srv/endurance2027/.ssh/id_ed25519.pub
```

Copia la chiave stampata in GitHub → repository → **Settings → Deploy keys → Add deploy key** (solo lettura). Poi:

```bash
cd /srv/endurance2027
sudo -u endurance git clone git@github.com:PyWis/end27host.git app_src
```

**Opzione B – upload manuale**: carica la cartella del progetto via SFTP (FileZilla, porta 22, utente root) in `/srv/endurance2027/app_src`, poi:

```bash
chown -R endurance:endurance /srv/endurance2027/app_src
```

Ambiente Python e dipendenze:

```bash
cd /srv/endurance2027/app_src
sudo -u endurance python3 -m venv .venv
sudo -u endurance .venv/bin/pip install -r requirements.txt
```

---

## 5. File `.env` di produzione

```bash
cd /srv/endurance2027/app_src
sudo -u endurance cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
sudo -u endurance nano .env
```

Valori da cambiare **obbligatoriamente**:

| Variabile | Valore in produzione |
|---|---|
| `SECRET_KEY` | la stringa casuale generata sopra |
| `ADMIN_PATH` | un percorso tuo, difficile da indovinare (es. `/gestione-q8w3z`) |
| `TRUST_PROXY` | `1` (il sito è dietro nginx) |
| `BASE_USER` / `BASE_PASS` | email e password provvisorie del superadmin (al primo accesso vanno cambiate) |
| `SITE_URL` | `https://tuodominio.it` (con **https**, senza `/` finale: attiva i cookie `Secure`) |
| `COMPANY_*` | dati reali dell'azienda (footer, privacy, termini) |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` | chiave Brevo e mittente di un **dominio verificato** su Brevo |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` | chiavi **live** (`sk_live_…`, `pk_live_…`) quando vai online davvero |
| `STRIPE_WEBHOOK_SECRET` | lo ottieni al §9 |
| `GOOGLE_CALENDAR_BOOKING_URL` | link della tua pagina di prenotazione |

Proteggi il file:

```bash
chmod 600 .env
```

Prova veloce di avvio (Ctrl+C per uscire):

```bash
sudo -u endurance .venv/bin/gunicorn -w 1 -b 127.0.0.1:8000 run:app
```

---

## 6. Servizio systemd (avvio automatico)

```bash
nano /etc/systemd/system/endurance.service
```

```ini
[Unit]
Description=Endurance 2027 (gunicorn)
After=network.target

[Service]
User=endurance
Group=endurance
WorkingDirectory=/srv/endurance2027/app_src
ExecStart=/srv/endurance2027/app_src/.venv/bin/gunicorn --workers 1 --threads 4 --timeout 60 -b 127.0.0.1:8000 run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now endurance
systemctl status endurance
```

> **1 worker + 4 thread**: tiene un solo processo (il limite tentativi di login resta corretto) ma gestisce più richieste in contemporanea. Per questo sito è più che sufficiente.

---

## 7. nginx (sito pubblico)

```bash
nano /etc/nginx/sites-available/endurance
```

```nginx
server {
    listen 80;
    server_name tuodominio.it www.tuodominio.it;

    # immagini caricate dall'admin: max 5 MB (lascia un po' di margine)
    client_max_body_size 6M;

    # file statici serviti direttamente da nginx
    location /static/ {
        alias /srv/endurance2027/app_src/app/static/;
        expires 30d;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 60s;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/endurance /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

nginx deve poter leggere i file statici:

```bash
chmod 755 /srv/endurance2027 /srv/endurance2027/app_src
```

---

## 8. HTTPS gratuito (Let's Encrypt)

Quando il dominio punta già al VPS (`ping tuodominio.it` risponde con l'IP del VPS):

```bash
certbot --nginx -d tuodominio.it -d www.tuodominio.it --redirect -m TUA_EMAIL --agree-tos -n
```

Il rinnovo è automatico. Verifica con `certbot renew --dry-run`.

Apri `https://tuodominio.it` → deve comparire la home. Poi `https://tuodominio.it/<ADMIN_PATH>` → login con `BASE_USER` / `BASE_PASS` e cambio credenziali.

---

## 9. Stripe e Brevo

**Webhook Stripe** (Dashboard Stripe → **Sviluppatori → Webhook → Aggiungi endpoint**):

- URL: `https://tuodominio.it/stripe/webhook`
- Eventi: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`
- Copia il **Signing secret** (`whsec_…`) in `.env` → `STRIPE_WEBHOOK_SECRET`, poi `systemctl restart endurance`.
- Fai il webhook sia in modalità **test** sia **live**: hanno secret diversi.

**Brevo**: in *Mittenti e domini* verifica il dominio (record DKIM/SPF/DMARC da aggiungere nel DNS di Hostinger, come al §2). Senza verifica le email finiscono in spam o vengono rifiutate.

> Se usi anche la posta Hostinger sullo stesso dominio, **unisci** gli SPF in un solo record TXT, es. `v=spf1 include:_spf.mail.hostinger.com include:spf.brevo.com ~all`.

---

## 10. Backup automatici

```bash
crontab -u endurance -e
```

```cron
15 3 * * * cd /srv/endurance2027/app_src && .venv/bin/flask --app run backup >/dev/null 2>&1
```

I backup finiscono in `instance/backups/` (ultimi `BACKUP_KEEP`). Copia periodicamente **fuori dal server** anche:

- `instance/backups/` (database)
- `app/static/uploads/` (immagini dei prodotti)
- `.env`

Esempio dal tuo computer:

```bash
scp -r root@IP_DEL_VPS:/srv/endurance2027/app_src/instance/backups ./backup-endurance
```

Attiva anche gli **snapshot/backup settimanali del VPS** in hPanel (VPS → Backup e monitoraggio).

---

## 11. Aggiornare il sito

```bash
cd /srv/endurance2027/app_src
sudo -u endurance .venv/bin/flask --app run backup
sudo -u endurance git pull
sudo -u endurance .venv/bin/pip install -r requirements.txt
systemctl restart endurance
```

Le nuove colonne del database vengono aggiunte da sole all'avvio (`ensure_columns`).

---

## 12. Problemi comuni

| Sintomo | Causa / soluzione |
|---|---|
| **502 Bad Gateway** | gunicorn fermo: `journalctl -u endurance -n 50` |
| Login admin che "non resta" | `SITE_URL` con https ma sito aperto in http, o `TRUST_PROXY` ≠ 1 |
| «Richiesta non valida» sui form | cookie bloccato: stesso motivo sopra; ricarica la pagina |
| Immagine non caricata (413) | file > 5 MB, o `client_max_body_size` mancante in nginx |
| Ordini restano «in attesa» | webhook Stripe sbagliato: controlla URL e `STRIPE_WEBHOOK_SECRET` (log in Stripe → Webhook) |
| Email non arrivano | `BREVO_API_KEY` errata o dominio mittente non verificato su Brevo |
| Orari sbagliati di 1–2 ore | fuso del server: `timedatectl set-timezone Europe/Rome` e `systemctl restart endurance` |
| CSS/immagini mancanti | permessi: `chmod 755 /srv/endurance2027 /srv/endurance2027/app_src` |

Log utili:

```bash
journalctl -u endurance -f
tail -f /var/log/nginx/error.log
```

---

## Checklist prima di andare online

- [ ] VPS Ubuntu 24.04, fuso Europe/Rome
- [ ] DNS `@` e `www` → IP del VPS
- [ ] `.env` con `SECRET_KEY` casuale, `ADMIN_PATH` personale, `TRUST_PROXY=1`, `SITE_URL=https://…`
- [ ] Servizio `endurance` attivo, nginx + HTTPS funzionanti
- [ ] Superadmin: email e password cambiate al primo accesso
- [ ] Webhook Stripe live configurato e testato con un pagamento reale di prova (poi rimborsato)
- [ ] Dominio verificato su Brevo, email di prova ricevuta
- [ ] Pagine `/termini`, `/privacy`, `/cookie` verificate da un consulente
- [ ] *Admin → Home & SEO*: *noindex* di test disattivato (se l'avevi attivato)
- [ ] Backup notturno in cron + copia esterna
