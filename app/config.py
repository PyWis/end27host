import os

from dotenv import load_dotenv

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE, ".env"))

AREAS = {
    "business": "Business",
    "marketing": "Marketing",
    "tecnologia": "Tecnologia",
    "formazione": "Formazione",
}

# Testi e icone delle aree (home e pagine area)
AREA_INFO = {
    "business": ("🏢", "Strategia, processi e consulenza per decidere meglio e crescere."),
    "marketing": ("📣", "Campagne e contenuti con obiettivi misurabili, dal brand alle vendite."),
    "tecnologia": ("💻", "Siti, software e automazioni pronti per lavorare al posto tuo."),
    "formazione": ("🎓", "Corsi pratici per te e il tuo team, tenuti da professionisti."),
}

# Prenotazione call di vendita per pagina monoprodotto / prodotto
CALL_MODES = {
    "none": "Nessuna call",
    "optional": "Call facoltativa (si può pagare subito)",
    "required": "Call obbligatoria (si paga con link dopo la call)",
}


def _bool(name, default="0"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "si", "sì", "on")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE, "instance", "endurance.db")
    UPLOAD_FOLDER = os.path.join(BASE, "app", "static", "uploads")
    BACKUP_FOLDER = os.path.join(BASE, "instance", "backups")
    BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    ADMIN_PATH = "/" + os.getenv("ADMIN_PATH", "/gestione-x7k2").strip("/")
    BASE_USER = os.getenv("BASE_USER", "")
    BASE_PASS = os.getenv("BASE_PASS", "")
    BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
    BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "noreply@example.com")
    BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "Endurance 2027")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PROMO_CODES = _bool("STRIPE_PROMO_CODES", "1")
    CURRENCY = os.getenv("CURRENCY", "eur")
    SITE_URL = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
    SITE_NAME = os.getenv("SITE_NAME", "Endurance 2027")
    SITE_DESCRIPTION = os.getenv("SITE_DESCRIPTION",
                                 "Business, Marketing, Tecnologia e Formazione: prodotti e servizi Endurance 2027.")
    # Google Calendar: link globale + eventuale link per area (GOOGLE_CALENDAR_BOOKING_URL_BUSINESS, ...)
    BOOKING_URL = os.getenv("GOOGLE_CALENDAR_BOOKING_URL", "").strip()
    BOOKING_URLS_BY_AREA = {k: os.getenv(f"GOOGLE_CALENDAR_BOOKING_URL_{k.upper()}", "").strip()
                            for k in AREAS}
    PAYLINK_DAYS = int(os.getenv("PAYLINK_DAYS", "7"))
    # Dati del venditore per privacy / termini di vendita
    COMPANY_NAME = os.getenv("COMPANY_NAME", "")
    COMPANY_VAT = os.getenv("COMPANY_VAT", "")
    COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "")
    COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "")
    COMPANY_PEC = os.getenv("COMPANY_PEC", "")
    TRUST_PROXY = _bool("TRUST_PROXY")  # dietro nginx/caddy: legge X-Forwarded-*
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = SITE_URL.startswith("https://")
