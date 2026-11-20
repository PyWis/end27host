import logging
from html import escape

import requests
from flask import current_app

log = logging.getLogger(__name__)


def send_email(to_email, subject, html):
    """Invia un'email transazionale via Brevo. Ritorna True se inviata."""
    cfg = current_app.config
    if not cfg["BREVO_API_KEY"]:
        log.warning("BREVO_API_KEY mancante: email «%s» NON inviata a %s", subject, to_email)
        return False
    try:
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": cfg["BREVO_API_KEY"], "accept": "application/json"},
            json={
                "sender": {"email": cfg["BREVO_SENDER_EMAIL"], "name": cfg["BREVO_SENDER_NAME"]},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html,
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Errore Brevo: %s", e)
        return False


def send_password_email(to_email, password, admin_url):
    to_e, pw_e, url_e = escape(to_email), escape(password), escape(admin_url)
    html = (f"<p>Ciao,</p><p>sei stato aggiunto come amministratore.</p>"
            f"<p>Accedi a <a href='{url_e}'>{url_e}</a><br>"
            f"Email: <b>{to_e}</b><br>Password temporanea: <b>{pw_e}</b></p>"
            f"<p>Al primo accesso ti verrà chiesto di cambiarla.</p>")
    return send_email(to_email, "Accesso amministratore", html)


def send_order_confirmation(order, booking_url=""):
    cfg = current_app.config
    name = escape(order.customer_name or "")
    html = (f"<p>Ciao {name},</p><p>grazie per il tuo acquisto su {escape(cfg['SITE_NAME'])}.</p>"
            f"<p><b>{escape(order.item_name)}</b> × {order.quantity}<br>"
            f"Totale pagato: <b>€ {order.amount}</b><br>Ordine n. {order.id}</p>")
    if booking_url:
        u = escape(booking_url)
        html += (f"<p>Puoi prenotare la tua call con il nostro team qui:<br>"
                 f"<a href='{u}'>{u}</a></p>")
    html += "<p>La ricevuta del pagamento ti arriva separatamente da Stripe.</p>"
    return send_email(order.customer_email, f"Conferma ordine n. {order.id}", html)


def send_paylink_email(link, url):
    u = escape(url)
    html = (f"<p>Ciao,</p><p>come concordato, ecco il link per completare l'acquisto di "
            f"<b>{escape(link.item_name)}</b> (€ {link.amount}).</p>"
            f"<p><a href='{u}'>{u}</a></p>"
            f"<p>Il link è valido fino al {link.expires_at.strftime('%d/%m/%Y')}.</p>")
    if link.note:
        html += f"<p>{escape(link.note)}</p>"
    return send_email(link.customer_email, f"Link di pagamento: {link.item_name}", html)
