from decimal import Decimal

from django.conf import settings
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="subcategories")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category__display_order", "display_order", "name"]
        constraints = [models.UniqueConstraint(fields=["category", "slug"], name="unique_subcategory_slug_per_category")]

    def __str__(self):
        return f"{self.category}: {self.name}"


class Brand(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    logo_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserPreference(models.Model):
    THEME_CHOICES = [("light", "Light"), ("dark", "Dark")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pos_preferences")
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="light")
    notify_low_stock = models.BooleanField(default=True)
    notify_sales = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class StaffProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    phone = models.CharField(max_length=30, blank=True)
    avatar = models.FileField(upload_to="staff_avatars/", blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class LoginActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_activity")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class BusinessSettings(models.Model):
    """Store-wide settings that directly affect checkout and generated records."""
    business_name = models.CharField(max_length=160, default="PoshPointHub")
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    currency_code = models.CharField(max_length=3, default="LKR")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.1000"))
    require_open_shift = models.BooleanField(default=False)
    allow_negative_stock = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "business settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Business settings cannot be deleted.")


class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    image = models.FileField(upload_to="product_images/", blank=True)
    category = models.CharField(max_length=100, default="General")
    category_ref = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    subcategory = models.ForeignKey(SubCategory, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    description = models.TextField(blank=True)
    barcode = models.CharField(max_length=100, blank=True, db_index=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    stock_quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=5)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.sku})"


class ProductDeal(models.Model):
    name = models.CharField(max_length=120)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="deals")
    deal_price = models.DecimalField(max_digits=10, decimal_places=2)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "name"]
        indexes = [models.Index(fields=["is_active", "starts_at", "ends_at"], name="product_deal_schedule_idx")]

    def __str__(self):
        return f"{self.name} — {self.product.name}"


class Customer(models.Model):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    loyalty_points = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CashRegisterShift(models.Model):
    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_register_shifts")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    opening_float = models.DecimalField(max_digits=12, decimal_places=2)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(fields=["cashier"], condition=models.Q(status="open"), name="one_open_shift_per_cashier"),
            models.CheckConstraint(condition=models.Q(opening_float__gte=0), name="cash_shift_opening_float_nonnegative"),
        ]


class CashRegisterMovement(models.Model):
    KIND_CHOICES = [("cash_in", "Cash in"), ("cash_out", "Cash out")]
    shift = models.ForeignKey(CashRegisterShift, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=250)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_register_movements")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="cash_movement_amount_positive")]


class Sale(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]

    invoice_number = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    sold_at = models.DateTimeField(auto_now_add=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sales")
    cash_register_shift = models.ForeignKey("CashRegisterShift", on_delete=models.PROTECT, null=True, blank=True, related_name="sales")

    class Meta:
        ordering = ["-sold_at"]

    def __str__(self):
        return self.invoice_number


class Payment(models.Model):
    METHOD_CHOICES = [("cash", "Cash"), ("card", "Card"), ("transfer", "Bank transfer"), ("other", "Other")]
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="cash")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Invoice(models.Model):
    STATUS_CHOICES = [("paid", "Paid"), ("partially_paid", "Partially paid"), ("unpaid", "Unpaid"), ("void", "Void")]
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="invoice")
    invoice_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="paid")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class FinancialTransaction(models.Model):
    TYPE_CHOICES = [("sale_payment", "Sale payment"), ("refund", "Refund"), ("cash_in", "Cash in"), ("cash_out", "Cash out"), ("reversal", "Reversal")]
    reference = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=Payment.METHOD_CHOICES, default="cash")
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, null=True, blank=True, related_name="financial_transactions")
    cash_register_shift = models.ForeignKey(CashRegisterShift, on_delete=models.PROTECT, null=True, blank=True, related_name="financial_transactions")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_transactions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Financial transactions are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Financial transactions are append-only.")


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    deal = models.ForeignKey(ProductDeal, on_delete=models.SET_NULL, null=True, blank=True, related_name="sale_items")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class Refund(models.Model):
    STATUS_CHOICES = [("pending", "Pending approval"), ("approved", "Approved"), ("rejected", "Rejected")]
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="refunds")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    reason = models.TextField()
    return_to_stock = models.BooleanField(default=False)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_pos_refunds")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_pos_refunds")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class RefundItem(models.Model):
    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="refund_items")
    quantity = models.PositiveIntegerField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["refund", "sale_item"], name="unique_refund_line_per_sale_item"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="refund_item_quantity_positive"),
        ]


class HeldBill(models.Model):
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="held_pos_bills")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="held_bills")
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class HeldBillItem(models.Model):
    bill = models.ForeignKey(HeldBill, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="held_bill_items")
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bill", "product"], name="unique_product_per_held_bill"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="held_bill_quantity_positive"),
        ]


class Notification(models.Model):
    TYPE_CHOICES = [
        ("sale", "Sale"),
        ("low_stock", "Low stock"),
        ("refund_request", "Refund request"),
        ("refund_review", "Refund review"),
        ("message", "Message"),
    ]
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pos_notifications")
    kind = models.CharField(max_length=24, choices=TYPE_CHOICES)
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=500)
    target = models.CharField(max_length=250, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"], name="notification_inbox_idx")]

    @property
    def is_read(self):
        return self.read_at is not None


class PosMessage(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_pos_messages")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="received_pos_messages")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    subject = models.CharField(max_length=200)
    body = models.TextField(max_length=10000)
    attachment = models.FileField(upload_to="messages/%Y/%m/", blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    sender_archived_at = models.DateTimeField(null=True, blank=True)
    recipient_archived_at = models.DateTimeField(null=True, blank=True)
    sender_deleted_at = models.DateTimeField(null=True, blank=True)
    recipient_deleted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "read_at", "-sent_at"], name="pos_msg_inbox_idx"),
            models.Index(fields=["sender", "-sent_at"], name="pos_msg_sent_idx"),
        ]

    def __str__(self):
        return self.subject


class EmailTemplate(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    subject_template = models.CharField(max_length=250)
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class EmailLog(models.Model):
    STATUS_CHOICES = [("sent", "Sent"), ("failed", "Failed")]
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="pos_email_logs")
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_logs")
    recipient = models.EmailField()
    subject = models.CharField(max_length=250)
    body = models.TextField()
    template_code = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="failed")
    error_message = models.CharField(max_length=1000, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "-created_at"], name="email_log_status_idx")]
