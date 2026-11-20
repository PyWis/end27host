from datetime import datetime
from urllib.parse import urlparse

from flask import current_app
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def fmt_cents(cents):
    """12345 -> '123,45' (con separatore migliaia: 1.234,50)."""
    s = f"{(cents or 0) / 100:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def embed_url(u):
    """URL incorporabile in iframe (solo link lunghi calendar.google.com/.../appointments/...)."""
    p = urlparse(u or "")
    if p.netloc == "calendar.google.com" and "/appointments/" in p.path:
        return u + ("&" if p.query else "?") + "gv=true"
    return ""


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_superadmin = db.Column(db.Boolean, default=False, nullable=False)
    must_change = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Sellable:
    """Comportamento comune a pagina monoprodotto e prodotto di una raccolta."""

    @property
    def is_active(self):
        return self.enabled and (self.expires_at is None or self.expires_at > datetime.now())

    @property
    def price(self):
        return fmt_cents(self.price_cents)

    @property
    def booking_url(self):
        """Link prenotazione: override sul prodotto > link dell'area > link globale."""
        cfg = current_app.config
        return (self.booking_override or "").strip() \
            or cfg["BOOKING_URLS_BY_AREA"].get(self.area_key, "") or cfg["BOOKING_URL"]

    @property
    def booking_embed_url(self):
        return embed_url(self.booking_url)


class Page(Sellable, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    area = db.Column(db.String(30), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # "mono" | "multi"
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    columns = db.Column(db.Integer, default=3)  # griglia pluriprodotto: 3-5
    # La pagina È il prodotto (monoprodotto): prezzo, immagine, scadenza stanno qui.
    # Per le pluriprodotto servono solo enabled/expires_at (visibilità nel menu).
    price_cents = db.Column(db.Integer, nullable=True)
    image = db.Column(db.String(255))
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    call_mode = db.Column(db.String(10), default="none", nullable=False)  # none|optional|required
    booking_override = db.Column(db.String(500), default="")
    max_quantity = db.Column(db.Integer, default=1, nullable=False)
    featured = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    products = db.relationship("Product", backref="page", cascade="all, delete-orphan",
                               order_by="[Product.position, Product.id]")
    __table_args__ = (db.UniqueConstraint("area", "slug"),)

    item_kind = "page"

    @property
    def name(self):
        return self.title

    @property
    def area_key(self):
        return self.area

    @property
    def active_products(self):
        return [p for p in self.products if p.is_active]

    @property
    def is_visible(self):
        # una raccolta senza prodotti attivi non ha senso nel menu
        return self.is_active and (self.kind == "mono" or bool(self.active_products))


class Product(Sellable, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("page.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    price_cents = db.Column(db.Integer, nullable=False)
    image = db.Column(db.String(255))
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    call_mode = db.Column(db.String(10), default="none", nullable=False)
    booking_override = db.Column(db.String(500), default="")
    max_quantity = db.Column(db.Integer, default=1, nullable=False)
    featured = db.Column(db.Boolean, default=False, nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)

    item_kind = "product"

    @property
    def area_key(self):
        return self.page.area

    @property
    def is_buyable(self):
        return self.is_active and self.page.is_active


def get_item(kind, item_id):
    model = {"page": Page, "product": Product}.get(kind)
    try:
        return db.session.get(model, int(item_id)) if model else None
    except (TypeError, ValueError):
        return None


class PayLink(db.Model):
    """Link di pagamento personale generato dall'admin (es. dopo la call di vendita)."""
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    item_kind = db.Column(db.String(10), nullable=False)
    item_id = db.Column(db.Integer, nullable=False)
    item_name = db.Column(db.String(200), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    customer_email = db.Column(db.String(255), default="")
    note = db.Column(db.Text, default="")
    expires_at = db.Column(db.DateTime, nullable=False)
    created_by = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)
    paid_at = db.Column(db.DateTime, nullable=True)

    @property
    def amount(self):
        return fmt_cents(self.amount_cents)

    @property
    def status(self):
        if self.paid_at:
            return "pagato"
        return "scaduto" if self.expires_at < datetime.now() else "attivo"


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    status = db.Column(db.String(10), default="pending", nullable=False)  # pending|paid|expired
    item_kind = db.Column(db.String(10), nullable=False)
    item_id = db.Column(db.Integer, nullable=False)
    item_name = db.Column(db.String(200), nullable=False)
    unit_cents = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=True)  # totale effettivo (con sconti)
    currency = db.Column(db.String(3), default="eur")
    customer_email = db.Column(db.String(255), default="")
    customer_name = db.Column(db.String(255), default="")
    paylink_id = db.Column(db.Integer, db.ForeignKey("pay_link.id"), nullable=True)
    email_sent = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    paid_at = db.Column(db.DateTime, nullable=True)

    @property
    def amount(self):
        return fmt_cents(self.amount_cents if self.amount_cents is not None
                         else self.unit_cents * self.quantity)


class Setting(db.Model):
    """Impostazioni chiave/valore modificabili da admin (es. SEO e testi della home)."""
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, default="")


HOME_DEFAULTS = {
    "seo_title": "Endurance 2027 · Business, Marketing, Tecnologia e Formazione",
    "seo_description": "Consulenze, servizi e percorsi formativi per far crescere la tua impresa: "
                       "acquista online o prenota una call con il nostro team.",
    "og_image": "",
    "noindex": "0",
    "hero_eyebrow": "Endurance 2027",
    "hero_title": "Strumenti concreti per far crescere il tuo business.",
    "hero_subtitle": "Consulenze, servizi e percorsi formativi in Business, Marketing, Tecnologia e "
                     "Formazione. Scegli cosa ti serve e acquista online, oppure parlane prima con noi.",
}


def get_settings():
    vals = dict(HOME_DEFAULTS)
    vals.update({s.key: s.value for s in Setting.query.all() if s.key in HOME_DEFAULTS})
    return vals


def set_setting(key, value):
    s = db.session.get(Setting, key) or Setting(key=key)
    s.value = value
    db.session.add(s)
