"""Checkout Stripe, registrazione ordini e conferme."""
from datetime import datetime

import stripe
from flask import abort, current_app, url_for

from .mailer import send_order_confirmation
from .models import Order, PayLink, db, get_item


def _stripe():
    key = current_app.config["STRIPE_SECRET_KEY"]
    if not key:
        abort(503, "Pagamenti non configurati")
    stripe.api_key = key
    return stripe


def start_checkout(item, cancel_url, amount_cents=None, paylink=None, quantity_max=None):
    """Crea la sessione Stripe Checkout e un ordine «pending». Ritorna l'URL Stripe."""
    cfg = current_app.config
    s_api = _stripe()
    cents = amount_cents if amount_cents is not None else item.price_cents
    product_data = {"name": item.name}
    if item.image:
        product_data["images"] = [cfg["SITE_URL"] + url_for("static", filename="uploads/" + item.image)]
    line = {"quantity": 1, "price_data": {"currency": cfg["CURRENCY"], "unit_amount": cents,
                                          "product_data": product_data}}
    qmax = quantity_max if quantity_max is not None else (item.max_quantity or 1)
    if qmax > 1:
        line["adjustable_quantity"] = {"enabled": True, "minimum": 1, "maximum": min(qmax, 999)}
    params = dict(
        mode="payment",
        line_items=[line],
        metadata={"item_kind": item.item_kind, "item_id": str(item.id),
                  "paylink_id": str(paylink.id) if paylink else ""},
        success_url=cfg["SITE_URL"] + url_for("public.success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        locale="it",
    )
    if cfg["STRIPE_PROMO_CODES"] and not paylink:
        params["allow_promotion_codes"] = True
    if paylink and paylink.customer_email:
        params["customer_email"] = paylink.customer_email
    try:
        s = s_api.checkout.Session.create(**params)
    except stripe.StripeError as e:
        current_app.logger.error("Stripe: %s", e)
        abort(502, "Errore nel sistema di pagamento")
    db.session.add(Order(stripe_session_id=s.id, item_kind=item.item_kind, item_id=item.id,
                         item_name=item.name, unit_cents=cents, currency=cfg["CURRENCY"],
                         paylink_id=paylink.id if paylink else None))
    db.session.commit()
    return s.url


def _get(obj, key, default=None):
    try:
        v = obj[key]
    except (KeyError, TypeError, AttributeError):
        v = getattr(obj, key, default)
    return default if v is None else v


def record_session(s):
    """Aggiorna l'ordine a partire da una sessione Stripe (webhook o pagina «grazie»).
    Idempotente: si può chiamare più volte. Ritorna l'ordine o None."""
    sid = _get(s, "id")
    order = Order.query.filter_by(stripe_session_id=sid).first() if sid else None
    if not order:
        return None
    if _get(s, "status") == "expired" and order.status == "pending":
        order.status = "expired"
        db.session.commit()
        return order
    if _get(s, "payment_status") != "paid" or order.status == "paid":
        return order
    details = _get(s, "customer_details", {}) or {}
    order.status, order.paid_at = "paid", datetime.now()
    order.amount_cents = _get(s, "amount_total", order.unit_cents)
    order.customer_email = _get(details, "email", "") or _get(s, "customer_email", "") or ""
    order.customer_name = _get(details, "name", "") or ""
    if order.unit_cents:
        sub = _get(s, "amount_subtotal", order.unit_cents)
        order.quantity = max(1, round(sub / order.unit_cents))
    if order.paylink_id:
        link = db.session.get(PayLink, order.paylink_id)
        if link and not link.paid_at:
            link.paid_at = order.paid_at
    db.session.commit()
    send_confirmation(order)
    return order


def send_confirmation(order):
    if order.email_sent or not order.customer_email:
        return
    item = get_item(order.item_kind, order.item_id)
    booking = ""
    # call proposta solo se prevista e non già fatta (gli acquisti da link arrivano dopo la call)
    if item and item.call_mode == "optional" and not order.paylink_id:
        booking = item.booking_url
    if send_order_confirmation(order, booking):
        order.email_sent = True
        db.session.commit()


def retrieve_session(session_id):
    if not session_id or not current_app.config["STRIPE_SECRET_KEY"]:
        return None
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    try:
        return stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError:
        return None
