import os
import secrets
from datetime import datetime

from flask import Flask, abort, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from .config import AREA_INFO, AREAS, CALL_MODES, Config
from .models import Page, User, db


def create_app():
    app = Flask(__name__, instance_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance"))
    app.config.from_object(Config)
    if app.config["SECRET_KEY"] == "dev-only-change-me":
        app.logger.warning("SECRET_KEY non impostata: usa un valore lungo e casuale in .env")
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    db.init_app(app)
    if app.config["TRUST_PROXY"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from .admin import bp as admin_bp
    from .public import bp as public_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix=app.config["ADMIN_PATH"])

    # --- CSRF minimale per tutti i POST ---
    @app.before_request
    def csrf_protect():
        if request.method == "POST" and request.endpoint != "public.stripe_webhook":
            token = session.get("_csrf")
            if not token or not secrets.compare_digest(token, request.form.get("_csrf", "")):
                abort(400, "CSRF token non valido")

    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    img_dir = os.path.join(app.static_folder, "img")

    def site_img(name):
        """URL di app/static/img/<name>.(svg|png|jpg|jpeg|webp) se esiste, altrimenti None."""
        for ext in ("svg", "png", "jpg", "jpeg", "webp"):
            if os.path.exists(os.path.join(img_dir, f"{name}.{ext}")):
                return url_for("static", filename=f"img/{name}.{ext}")
        return None

    @app.context_processor
    def inject():
        menu = {}
        for key, label in AREAS.items():
            pages = Page.query.filter_by(area=key).order_by(Page.title).all()
            menu[key] = {"label": label, "pages": [p for p in pages if p.is_visible]}
        return {"menu": menu, "csrf_token": csrf_token, "AREAS": AREAS,
                "CALL_MODES": CALL_MODES, "cfg": app.config, "AREA_INFO": AREA_INFO, "site_img": site_img, "now": datetime.now(),
                "ANY_BOOKING": bool(app.config["BOOKING_URL"] or any(app.config["BOOKING_URLS_BY_AREA"].values()))}

    @app.cli.command("backup")
    def backup_cmd():
        """Backup del database in instance/backups (es. da cron ogni notte)."""
        from .backup import make_backup
        print(make_backup())

    # --- pagine di errore leggibili in italiano ---
    @app.errorhandler(HTTPException)
    def http_error(e):
        msgs = {404: "Pagina non trovata.", 410: "Prodotto non più disponibile.",
                413: "File troppo grande (max 5 MB).", 400: "Richiesta non valida: ricarica la pagina e riprova."}
        return render_template("error.html", code=e.code,
                               message=msgs.get(e.code) or e.description), e.code

    @app.after_request
    def headers(resp):
        if request.path.startswith(app.config["ADMIN_PATH"]):
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    with app.app_context():
        db.create_all()
        ensure_columns()
        seed_superadmin(app)
    return app


def seed_superadmin(app):
    if User.query.filter_by(is_superadmin=True).first():
        return
    email, pw = app.config["BASE_USER"], app.config["BASE_PASS"]
    if not email or not pw:
        app.logger.warning("BASE_USER/BASE_PASS non impostati: nessun superadmin creato")
        return
    u = User(email=email.lower(), is_superadmin=True, must_change=True)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()


def ensure_columns():
    """Aggiunge ai DB SQLite già esistenti le colonne nuove (migrazione leggera)."""
    from sqlalchemy import text
    call = ("call_mode", "VARCHAR(10) NOT NULL DEFAULT 'none'")
    common = [call, ("booking_override", "VARCHAR(500) DEFAULT ''"),
              ("max_quantity", "INTEGER NOT NULL DEFAULT 1"), ("featured", "BOOLEAN NOT NULL DEFAULT 0")]
    wanted = {
        "page": [("price_cents", "INTEGER"), ("image", "VARCHAR(255)"),
                 ("enabled", "BOOLEAN NOT NULL DEFAULT 1"), ("expires_at", "DATETIME")] + common,
        "product": common + [("position", "INTEGER NOT NULL DEFAULT 0")],
    }
    for table, cols in wanted.items():
        have = {r[1] for r in db.session.execute(text(f"PRAGMA table_info({table})"))}
        for col, ddl in cols:
            if col not in have:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    db.session.commit()
