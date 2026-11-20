from datetime import datetime

import stripe
from flask import (Blueprint, Response, abort, current_app, redirect,
                   render_template, request, session, url_for)

from .config import AREAS
from .models import Page, PayLink, Product, User, db, embed_url, get_item, get_settings
from .orders import record_session, retrieve_session, start_checkout

bp = Blueprint("public", __name__)
LEGAL = {"privacy": "Privacy policy", "cookie": "Cookie policy", "termini": "Termini e condizioni di vendita"}


def is_admin():
    return "uid" in session and db.session.get(User, session["uid"]) is not None


def featured_items():
    pages = [p for p in Page.query.filter_by(featured=True, kind="mono") if p.is_visible]
    prods = [p for p in Product.query.filter_by(featured=True) if p.is_active and p.page.is_visible]
    return pages + prods


def item_url(item):
    if item.item_kind == "page":
        return url_for("public.page", area=item.area, slug=item.slug)
    return url_for("public.page", area=item.page.area, slug=item.page.slug) + f"#prod-{item.id}"


@bp.app_context_processor
def helpers():
    return {"item_url": item_url}


@bp.route("/")
def home():
    return render_template("home.html", featured=featured_items(), home=get_settings())


@bp.route("/area/<area>")
def area(area):
    if area not in AREAS:
        abort(404)
    pages = [p for p in Page.query.filter_by(area=area).order_by(Page.title) if p.is_visible]
    return render_template("area.html", area=area, pages=pages)


@bp.route("/area/<area>/<slug>")
def page(area, slug):
    p = Page.query.filter_by(area=area, slug=slug).first_or_404()
    preview = False
    if not p.is_visible:
        if not is_admin():
            abort(404)
        preview = True  # gli admin vedono l'anteprima delle pagine non attive
    products = p.products if preview else p.active_products
    return render_template("page_mono.html" if p.kind == "mono" else "page_multi.html",
                           page=p, products=products, preview=preview)


# ---------- acquisto ----------
def _buyable(kind, item_id):
    item = get_item(kind, item_id)
    if not item:
        abort(404)
    ok = (item.kind == "mono" and item.is_active and item.price_cents) if kind == "page" else item.is_buyable
    if not ok:
        abort(410)
    return item


@bp.route("/checkout/pagina/<int:page_id>", methods=["POST"])
def checkout_page(page_id):
    return _checkout(_buyable("page", page_id))


@bp.route("/checkout/<int:product_id>", methods=["POST"])
def checkout(product_id):
    return _checkout(_buyable("product", product_id))


def _checkout(item):
    if item.call_mode == "required":
        abort(403, "Per questo prodotto è necessario prenotare prima una call.")
    return redirect(start_checkout(item, current_app.config["SITE_URL"] + item_url(item)), code=303)


@bp.route("/prenota")
def book_general():
    url = current_app.config["BOOKING_URL"]
    if not url:
        abort(404)
    return render_template("book.html", item=None, booking_url=url, embed=embed_url(url))


@bp.route("/prenota/<kind>/<int:item_id>")
def book(kind, item_id):
    item = get_item(kind, item_id)
    if not item or item.call_mode == "none" or not item.booking_url:
        abort(404)
    if not (item.is_buyable if kind == "product" else item.is_active):
        abort(410)
    return render_template("book.html", item=item, booking_url=item.booking_url, embed=item.booking_embed_url)


@bp.route("/paga/<token>", methods=["GET", "POST"])
def paylink(token):
    link = PayLink.query.filter_by(token=token).first_or_404()
    item = get_item(link.item_kind, link.item_id)
    usable = link.status == "attivo" and item is not None
    if request.method == "POST":
        if not usable:
            abort(410)
        cancel = current_app.config["SITE_URL"] + url_for("public.paylink", token=token)
        return redirect(start_checkout(item, cancel, amount_cents=link.amount_cents,
                                       paylink=link, quantity_max=1), code=303)
    return render_template("paylink.html", link=link, usable=usable)


@bp.route("/grazie")
def success():
    order = None
    s = retrieve_session(request.args.get("session_id", ""))
    if s is not None:
        order = record_session(s)
    item = get_item(order.item_kind, order.item_id) if order and order.status == "paid" else None
    show_call = bool(item and item.call_mode == "optional" and not order.paylink_id and item.booking_url)
    return render_template("success.html", order=order if order and order.status == "paid" else None,
                           item=item, show_call=show_call)


@bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    secret = current_app.config["STRIPE_WEBHOOK_SECRET"]
    if not secret:
        abort(404)
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""), secret)
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                         "checkout.session.expired"):
        record_session(event["data"]["object"])
    return {"ok": True}


# ---------- pagine legali e SEO ----------
@bp.route("/<any(privacy, cookie, termini):doc>")
def legal(doc):
    c = current_app.config
    return render_template(f"legal/{doc}.html", doc_title=LEGAL[doc],
                           seller=c["COMPANY_NAME"] or "[RAGIONE SOCIALE]", vat=c["COMPANY_VAT"] or "[P.IVA]",
                           addr=c["COMPANY_ADDRESS"] or "[SEDE LEGALE]", mail=c["COMPANY_EMAIL"] or "[EMAIL]")


@bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nAllow: /\nSitemap: {current_app.config['SITE_URL']}{url_for('public.sitemap')}\n"
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    base = current_app.config["SITE_URL"]
    urls = [url_for("public.home")] + [url_for("public.area", area=a) for a in AREAS]
    urls += [url_for("public.page", area=p.area, slug=p.slug) for p in Page.query.all() if p.is_visible]
    urls += [url_for("public.legal", doc=d) for d in LEGAL]
    xml = render_template("sitemap.xml", urls=[base + u for u in urls], today=datetime.now().date())
    return Response(xml, mimetype="application/xml")
