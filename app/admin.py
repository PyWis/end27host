import csv
import io
import os
import re
import secrets
import shutil
import string
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect,
                   render_template, request, send_file, session, url_for)
from werkzeug.utils import secure_filename

from .config import AREAS, CALL_MODES
from .backup import make_backup
from .mailer import send_password_email, send_paylink_email
from .models import (HOME_DEFAULTS, Order, Page, PayLink, Product, User, db, fmt_cents,
                     get_item, get_settings, set_setting)

bp = Blueprint("admin", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}
# Anti brute force (in memoria, per processo): max 5 login falliti per IP in 15 minuti
LOGIN_FAILS = defaultdict(deque)
LOGIN_MAX, LOGIN_WINDOW = 5, 15 * 60


def _too_many_fails(ip):
    q, now = LOGIN_FAILS[ip], time.time()
    while q and q[0] < now - LOGIN_WINDOW:
        q.popleft()
    return len(q) >= LOGIN_MAX


# ---------- helpers ----------
def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "pagina"


def random_password(n=14):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


class BadImage(Exception):
    pass


def save_image(file):
    """Salva l'immagine caricata; None se nessun file, BadImage se formato non valido."""
    if not file or not file.filename:
        return None
    fname = secure_filename(file.filename)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in ALLOWED_IMG:
        raise BadImage("Formato immagine non valido (png, jpg, webp, gif).")
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


def delete_image(name):
    if name:
        try:
            os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
        except OSError:
            pass


def parse_price(v):
    try:
        cents = round(float(v.strip().replace(".", "").replace(",", ".")
                            if "," in v else v.strip()) * 100)
    except (ValueError, OverflowError):  # "abc", "nan", "inf"
        return None
    return cents if 50 <= cents <= 99_999_999 else None  # limiti Stripe


def parse_call_mode(v):
    return v if v in CALL_MODES else "none"


def parse_dt(v):
    return datetime.strptime(v, "%Y-%m-%dT%H:%M") if v else None


def login_required(superadmin=False):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            if not g.user:
                return redirect(url_for("admin.login"))
            if g.user.must_change and request.endpoint != "admin.change_credentials":
                return redirect(url_for("admin.change_credentials"))
            if superadmin and not g.user.is_superadmin:
                abort(403)
            return f(*a, **kw)
        return w
    return deco


@bp.before_request
def load_user():
    g.user = db.session.get(User, session["uid"]) if "uid" in session else None


# ---------- auth ----------
@bp.route("/", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _too_many_fails(ip):
            flash("Troppi tentativi falliti. Riprova tra qualche minuto.", "error")
            return render_template("admin/login.html"), 429
        u = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if u and u.check_password(request.form.get("password", "")):
            LOGIN_FAILS.pop(ip, None)
            session.clear()
            session["uid"] = u.id
            return redirect(url_for("admin.dashboard"))
        LOGIN_FAILS[ip].append(time.time())
        flash("Credenziali non valide.", "error")
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@bp.route("/credenziali", methods=["GET", "POST"])
@login_required()
def change_credentials():
    u = g.user
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        email = request.form.get("email", u.email).strip().lower() if u.is_superadmin else u.email
        err = None
        if not EMAIL_RE.match(email):
            err = "Email non valida."
        elif u.must_change and u.is_superadmin and email == u.email:
            err = "Al primo accesso devi impostare una nuova email."
        elif email != u.email and User.query.filter_by(email=email).first():
            err = "Email già in uso."
        elif len(pw) < 10:
            err = "La password deve avere almeno 10 caratteri."
        elif pw != pw2:
            err = "Le password non coincidono."
        elif u.check_password(pw):
            err = "La nuova password deve essere diversa da quella attuale."
        if err:
            flash(err, "error")
        else:
            u.email, u.must_change = email, False
            u.set_password(pw)
            db.session.commit()
            flash("Credenziali aggiornate.", "ok")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/credentials.html")


# ---------- dashboard / admins ----------
@bp.route("/dashboard")
@login_required()
def dashboard():
    pages = Page.query.order_by(Page.area, Page.title).all()
    return render_template("admin/dashboard.html", pages=pages)


@bp.route("/admins", methods=["GET", "POST"])
@login_required(superadmin=True)
def admins():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Email non valida.", "error")
        elif User.query.filter_by(email=email).first():
            flash("Email già presente.", "error")
        else:
            pw = random_password()
            u = User(email=email, is_superadmin=False, must_change=True)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            url = current_app.config["SITE_URL"] + url_for("admin.login")
            if send_password_email(email, pw, url):
                flash(f"Admin creato. Password inviata a {email}.", "ok")
            else:
                flash("Admin creato ma l'email NON è stata inviata (controlla BREVO_API_KEY). "
                      "Usa «Reinvia password» dopo aver configurato Brevo.", "error")
        return redirect(url_for("admin.admins"))
    return render_template("admin/admins.html", users=User.query.order_by(User.id).all())


@bp.route("/admins/<int:uid>/reset", methods=["POST"])
@login_required(superadmin=True)
def admin_reset(uid):
    u = db.get_or_404(User, uid)
    if u.is_superadmin:
        abort(403)
    pw = random_password()
    u.set_password(pw)
    u.must_change = True
    db.session.commit()
    url = current_app.config["SITE_URL"] + url_for("admin.login")
    ok = send_password_email(u.email, pw, url)
    flash("Nuova password inviata." if ok else "Password rigenerata ma email NON inviata.",
          "ok" if ok else "error")
    return redirect(url_for("admin.admins"))


@bp.route("/admins/<int:uid>/delete", methods=["POST"])
@login_required(superadmin=True)
def admin_delete(uid):
    u = db.get_or_404(User, uid)
    if u.is_superadmin:
        abort(403)
    db.session.delete(u)
    db.session.commit()
    flash("Admin eliminato.", "ok")
    return redirect(url_for("admin.admins"))


# ---------- pagine ----------
def fill_sell_fields(obj, f):
    """Campi comuni di vendita (pagina mono / prodotto). Ritorna messaggio d'errore o None."""
    url = f.get("booking_override", "").strip()
    if url and not url.startswith("https://"):
        return "Il link di prenotazione deve iniziare con https://"
    try:
        qty = int(f.get("max_quantity") or 1)
    except ValueError:
        return "Quantità massima non valida."
    obj.call_mode = parse_call_mode(f.get("call_mode"))
    obj.booking_override = url
    obj.max_quantity = min(999, max(1, qty))
    obj.featured = f.get("featured") == "on"
    return None


def fill_page(p):
    f = request.form
    title = f.get("title", "").strip()
    area, kind = f.get("area"), f.get("kind")
    if p.id is not None:
        kind = p.kind  # il tipo non si cambia dopo la creazione
    if not title or area not in AREAS or kind not in ("mono", "multi"):
        flash("Titolo, area e tipo sono obbligatori.", "error")
        return False
    slug = slugify(f.get("slug") or title)
    dup = Page.query.filter_by(area=area, slug=slug).first()
    if dup and dup.id != p.id:
        flash("Esiste già una pagina con questo indirizzo in quest'area.", "error")
        return False
    try:
        exp = parse_dt(f.get("expires_at", ""))
    except ValueError:
        flash("Scadenza non valida.", "error")
        return False
    cents = parse_price(f.get("price", "")) if f.get("price", "").strip() else None
    if kind == "mono" and cents is None:
        flash("Per la pagina monoprodotto serve un prezzo valido (min 0,50).", "error")
        return False
    p.title, p.area, p.slug, p.kind = title, area, slug, kind
    p.description = f.get("description", "").strip()
    p.expires_at = exp
    p.enabled = f.get("enabled") == "on"
    p.price_cents = cents if kind == "mono" else None
    if kind == "mono":
        err = fill_sell_fields(p, f)
        if err:
            flash(err, "error")
            return False
    else:
        p.call_mode, p.featured, p.max_quantity = "none", False, 1
    try:
        img = save_image(request.files.get("image"))
    except BadImage as e:
        flash(str(e), "error")
        return False
    if img:
        delete_image(p.image)
        p.image = img
    try:
        p.columns = min(5, max(3, int(f.get("columns", 3))))
    except ValueError:
        p.columns = 3
    return True


@bp.route("/pagine/nuova", methods=["GET", "POST"])
@login_required()
def page_new():
    if request.method == "POST":
        p = Page()
        if fill_page(p):
            db.session.add(p)
            db.session.commit()
            flash("Pagina creata e già disponibile nel menu." if p.is_visible else "Pagina creata (non ancora nel menu).", "ok")
            return redirect(url_for("admin.page_edit", pid=p.id))
    return render_template("admin/page_form.html", page=None)


@bp.route("/pagine/<int:pid>", methods=["GET", "POST"])
@login_required()
def page_edit(pid):
    p = db.get_or_404(Page, pid)
    if request.method == "POST":
        if fill_page(p):
            db.session.commit()
            flash("Pagina salvata.", "ok")
            return redirect(url_for("admin.page_edit", pid=p.id))
    return render_template("admin/page_form.html", page=p)


def copy_image(name):
    if not name:
        return None
    folder = current_app.config["UPLOAD_FOLDER"]
    new = f"{uuid.uuid4().hex}.{name.rsplit('.', 1)[-1]}"
    try:
        shutil.copyfile(os.path.join(folder, name), os.path.join(folder, new))
    except OSError:
        return None
    return new


@bp.route("/pagine/<int:pid>/duplica", methods=["POST"])
@login_required()
def page_duplicate(pid):
    src = db.get_or_404(Page, pid)
    base, n = src.slug + "-copia", 1
    slug = base
    while Page.query.filter_by(area=src.area, slug=slug).first():
        n += 1
        slug = f"{base}-{n}"
    cols = ("area", "kind", "description", "columns", "price_cents", "expires_at", "call_mode",
            "booking_override", "max_quantity")
    p = Page(title=src.title + " (copia)", slug=slug, enabled=False, featured=False,
             image=copy_image(src.image), **{c: getattr(src, c) for c in cols})
    for pr in src.products:
        p.products.append(Product(
            image=copy_image(pr.image), featured=False,
            **{c: getattr(pr, c) for c in ("name", "description", "price_cents", "enabled", "expires_at",
                                           "call_mode", "booking_override", "max_quantity", "position")}))
    db.session.add(p)
    db.session.commit()
    flash("Pagina duplicata (disattivata: controllala e attivala).", "ok")
    return redirect(url_for("admin.page_edit", pid=p.id))


@bp.route("/pagine/<int:pid>/delete", methods=["POST"])
@login_required()
def page_delete(pid):
    p = db.get_or_404(Page, pid)
    for pr in p.products:
        delete_image(pr.image)
    delete_image(p.image)
    db.session.delete(p)
    db.session.commit()
    flash("Pagina eliminata.", "ok")
    return redirect(url_for("admin.dashboard"))


# ---------- prodotti ----------
@bp.route("/pagine/<int:pid>/prodotti/nuovo", methods=["GET", "POST"])
@bp.route("/prodotti/<int:prid>", methods=["GET", "POST"])
@login_required()
def product_edit(pid=None, prid=None):
    prod = db.get_or_404(Product, prid) if prid else None
    page = prod.page if prod else db.get_or_404(Page, pid)
    if page.kind != "multi":
        flash("Nella pagina monoprodotto la pagina stessa è il prodotto.", "error")
        return redirect(url_for("admin.page_edit", pid=page.id))
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        cents = parse_price(f.get("price", ""))
        err = None
        try:
            exp = parse_dt(f.get("expires_at", ""))
        except ValueError:
            exp, err = None, "Scadenza non valida."
        if not name or cents is None:
            err = "Nome e prezzo valido (min 0,50) sono richiesti."
        target = prod or Product(page_id=page.id)
        err = err or fill_sell_fields(target, f)
        img = None
        if not err:
            try:
                img = save_image(request.files.get("image"))
            except BadImage as e:
                err = str(e)
        if err:
            db.session.rollback()
            flash(err, "error")
        else:
            if not prod:
                prod = target
                prod.position = max([x.position for x in page.products] or [0]) + 1
                db.session.add(prod)
            prod.name, prod.price_cents, prod.expires_at = name, cents, exp
            prod.description = f.get("description", "").strip()
            prod.enabled = f.get("enabled") == "on"
            if img:
                delete_image(prod.image)
                prod.image = img
            db.session.commit()
            flash("Prodotto salvato.", "ok")
            return redirect(url_for("admin.page_edit", pid=page.id))
    return render_template("admin/product_form.html", page=page, prod=prod)


@bp.route("/prodotti/<int:prid>/sposta/<direction>", methods=["POST"])
@login_required()
def product_move(prid, direction):
    prod = db.get_or_404(Product, prid)
    items = list(prod.page.products)
    i = items.index(prod)
    j = i - 1 if direction == "su" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
    for n, it in enumerate(items):
        it.position = n
    db.session.commit()
    return redirect(url_for("admin.page_edit", pid=prod.page_id) + "#prodotti")


@bp.route("/prodotti/<int:prid>/delete", methods=["POST"])
@login_required()
def product_delete(prid):
    prod = db.get_or_404(Product, prid)
    pid = prod.page_id
    delete_image(prod.image)
    db.session.delete(prod)
    db.session.commit()
    flash("Prodotto eliminato.", "ok")
    return redirect(url_for("admin.page_edit", pid=pid))


# ---------- ordini ----------
@bp.route("/ordini")
@login_required()
def orders():
    status = request.args.get("stato", "paid")
    q = Order.query.order_by(Order.created_at.desc())
    if status in ("paid", "pending", "expired"):
        q = q.filter_by(status=status)
    rows = q.limit(500).all()
    paid = Order.query.filter_by(status="paid").all()
    total = fmt_cents(sum(o.amount_cents or 0 for o in paid))
    return render_template("admin/orders.html", orders=rows, status=status, total=total, n_paid=len(paid))


@bp.route("/ordini.csv")
@login_required()
def orders_csv():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["n", "data", "stato", "prodotto", "quantita", "totale_eur", "email", "nome", "da_link", "stripe_session"])
    for o in Order.query.order_by(Order.created_at).all():
        w.writerow([o.id, o.created_at.strftime("%Y-%m-%d %H:%M"), o.status, o.item_name, o.quantity,
                    o.amount, o.customer_email, o.customer_name, "si" if o.paylink_id else "",
                    o.stripe_session_id])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ordini.csv"})


# ---------- link di pagamento (dopo la call) ----------
def sellable_items():
    """Tutto ciò che si può vendere con un link: pagine mono e prodotti (anche non pubblici)."""
    out = [(f"page:{p.id}", f"{AREAS[p.area]} · {p.title}", p.price) for p in
           Page.query.filter_by(kind="mono").order_by(Page.area, Page.title)]
    for pg in Page.query.filter_by(kind="multi").order_by(Page.area, Page.title):
        out += [(f"product:{pr.id}", f"{AREAS[pg.area]} · {pg.title} · {pr.name}", pr.price) for pr in pg.products]
    return out


@bp.route("/link-pagamento", methods=["GET", "POST"])
@login_required()
def paylinks():
    created = None
    if request.method == "POST":
        f = request.form
        kind, _, iid = f.get("item", "").partition(":")
        item = get_item(kind, iid)
        email = f.get("customer_email", "").strip().lower()
        cents = parse_price(f.get("price", "")) if f.get("price", "").strip() else (item.price_cents if item else None)
        try:
            days = min(60, max(1, int(f.get("days") or current_app.config["PAYLINK_DAYS"])))
        except ValueError:
            days = current_app.config["PAYLINK_DAYS"]
        if not item or (kind == "page" and item.kind != "mono"):
            flash("Scegli un prodotto.", "error")
        elif email and not EMAIL_RE.match(email):
            flash("Email cliente non valida.", "error")
        elif not cents:
            flash("Prezzo non valido (min 0,50).", "error")
        else:
            created = PayLink(token=secrets.token_urlsafe(24), item_kind=kind, item_id=item.id,
                              item_name=item.name, amount_cents=cents, customer_email=email,
                              note=f.get("note", "").strip(), created_by=g.user.email,
                              expires_at=datetime.now() + timedelta(days=days))
            db.session.add(created)
            db.session.commit()
            url = current_app.config["SITE_URL"] + url_for("public.paylink", token=created.token)
            if email and f.get("send") == "on":
                ok = send_paylink_email(created, url)
                flash(f"Link creato e inviato a {email}." if ok else "Link creato ma email NON inviata (Brevo).",
                      "ok" if ok else "error")
            else:
                flash("Link creato: copialo e invialo al cliente.", "ok")
    links = PayLink.query.order_by(PayLink.created_at.desc()).limit(200).all()
    return render_template("admin/paylinks.html", items=sellable_items(), links=links, created=created)


@bp.route("/link-pagamento/<int:lid>/annulla", methods=["POST"])
@login_required()
def paylink_cancel(lid):
    link = db.get_or_404(PayLink, lid)
    if not link.paid_at:
        link.expires_at = datetime.now()
        db.session.commit()
        flash("Link annullato.", "ok")
    return redirect(url_for("admin.paylinks"))


# ---------- home & SEO ----------
@bp.route("/home", methods=["GET", "POST"])
@login_required()
def home_settings():
    if request.method == "POST":
        f = request.form
        old = get_settings()
        try:
            img = save_image(request.files.get("og_image"))
        except BadImage as e:
            flash(str(e), "error")
            return redirect(url_for("admin.home_settings"))
        for key in HOME_DEFAULTS:
            if key in ("og_image", "noindex"):
                continue
            # vuoto = torna al testo predefinito
            set_setting(key, " ".join(f.get(key, "").split()) or HOME_DEFAULTS[key])
        set_setting("noindex", "1" if f.get("noindex") == "on" else "0")
        if img or f.get("og_remove") == "on":
            delete_image(old["og_image"])
            set_setting("og_image", img or "")
        db.session.commit()
        flash("Home aggiornata.", "ok")
        return redirect(url_for("admin.home_settings"))
    return render_template("admin/home.html", home=get_settings(), defaults=HOME_DEFAULTS)


# ---------- backup ----------
@bp.route("/backup", methods=["POST"])
@login_required(superadmin=True)
def backup():
    path = make_backup()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))
