from django.conf import settings
from django.core.mail import EmailMessage
from django.template import Context, Engine
from django.db.models import F
from django.utils import timezone

from .configuration import get_business_settings
from .models import EmailLog, EmailTemplate, Product


DEFAULT_TEMPLATES = {
    "receipt": {
        "name": "Sale receipt",
        "subject_template": "{{ business_name }} receipt {{ invoice_number }}",
        "body_template": "Hello {{ customer_name }},\n\nThank you for your purchase from {{ business_name }}.\n\nReceipt {{ invoice_number }}\n{{ line_items }}\nSubtotal: {{ subtotal }}\nTax: {{ tax }}\nTotal: {{ total }}\nPayment status: {{ invoice_status }}\n\n{{ business_address }}\n{{ business_phone }}",
    },
    "invoice": {
        "name": "Invoice",
        "subject_template": "{{ business_name }} invoice {{ invoice_number }}",
        "body_template": "Hello {{ customer_name }},\n\nPlease find your invoice details below.\n\nInvoice {{ invoice_number }}\n{{ line_items }}\nSubtotal: {{ subtotal }}\nTax: {{ tax }}\nTotal due: {{ total }}\nStatus: {{ invoice_status }}\n\n{{ business_name }}\n{{ business_address }}\n{{ business_phone }}",
    },
    "order_confirmation": {
        "name": "Order confirmation",
        "subject_template": "{{ business_name }} order {{ invoice_number }} confirmed",
        "body_template": "Hello {{ customer_name }},\n\nYour order {{ invoice_number }} is confirmed.\n{{ line_items }}\nTotal: {{ total }}\n\nThank you,\n{{ business_name }}",
    },
    "low_stock": {
        "name": "Low stock alert",
        "subject_template": "{{ business_name }} low stock alert",
        "body_template": "The following active products are at or below their reorder levels:\n\n{{ low_stock_items }}\n\n{{ business_name }}",
    },
}


def smtp_is_configured():
    return bool(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL)


def ensure_email_templates():
    for code, values in DEFAULT_TEMPLATES.items():
        EmailTemplate.objects.get_or_create(code=code, defaults=values)
    return EmailTemplate.objects.filter(is_active=True)


def render_email_template(code, invoice=None):
    template = EmailTemplate.objects.get(code=code, is_active=True)
    business = get_business_settings()
    context = {
        "business_name": business.business_name,
        "business_address": business.address,
        "business_phone": business.phone,
    }
    if code == "low_stock":
        context["low_stock_items"] = "\n".join(
            f"{product.name}: {product.stock_quantity} in stock (reorder at {product.reorder_level})"
            for product in Product.objects.filter(is_active=True, stock_quantity__lte=F("reorder_level")).order_by("name")
        ) or "No products currently need reordering."
    elif invoice:
        sale = invoice.sale
        context.update({
            "invoice_number": invoice.invoice_number,
            "customer_name": sale.customer.name if sale.customer else "Customer",
            "line_items": "\n".join(f"{item.quantity} x {item.product.name} — {item.line_total}" for item in sale.items.select_related("product")),
            "subtotal": str(sale.subtotal),
            "tax": str(sale.tax_amount),
            "total": str(sale.total),
            "invoice_status": invoice.status.replace("_", " ").title(),
        })
    engine = Engine(autoescape=False)
    subject = engine.from_string(template.subject_template).render(Context(context)).strip()
    body = engine.from_string(template.body_template).render(Context(context)).strip()
    return subject, body


def attempt_email(log):
    log.attempts += 1
    log.last_attempt_at = timezone.now()
    try:
        if not smtp_is_configured():
            raise RuntimeError("SMTP is not configured. Set EMAIL_HOST and DEFAULT_FROM_EMAIL in backend/.env.")
        sent = EmailMessage(log.subject, log.body, settings.DEFAULT_FROM_EMAIL, [log.recipient]).send(fail_silently=False)
        if sent != 1:
            raise RuntimeError("The email backend did not accept the message.")
        log.status = "sent"
        log.error_message = ""
        log.sent_at = timezone.now()
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)[:1000]
    log.save(update_fields=["attempts", "last_attempt_at", "status", "error_message", "sent_at"])
    return log.status == "sent"
