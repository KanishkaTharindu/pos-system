from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from uuid import uuid4
from rest_framework import serializers
from django.utils.text import slugify
from django.utils import timezone
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field

from .models import Brand, BusinessSettings, CashRegisterMovement, CashRegisterShift, Category, Customer, EmailLog, EmailTemplate, FinancialTransaction, HeldBill, HeldBillItem, Invoice, LoginActivity, Notification, Payment, PosMessage, Product, ProductDeal, Refund, RefundItem, Sale, SaleItem, StaffProfile, SubCategory, UserPreference
from .configuration import get_business_settings
from .notifications import notify_roles


class ProductSerializer(serializers.ModelSerializer):
    category_ref = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=False, allow_null=True)
    subcategory = serializers.PrimaryKeyRelatedField(queryset=SubCategory.objects.all(), required=False, allow_null=True)
    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), required=False, allow_null=True)
    image = serializers.FileField(required=False, allow_null=True, write_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "image",
            "image_url",
            "category",
            "category_ref",
            "subcategory",
            "brand",
            "description",
            "barcode",
            "cost_price",
            "selling_price",
            "stock_quantity",
            "reorder_level",
            "is_active",
        ]
        extra_kwargs = {
            "cost_price": {"min_value": Decimal("0.00")},
            "selling_price": {"min_value": Decimal("0.00")},
            "stock_quantity": {"min_value": 0},
            "reorder_level": {"min_value": 0},
        }

    def validate(self, attrs):
        upload = attrs.get("image")
        if upload:
            if upload.size > 5 * 1024 * 1024:
                raise serializers.ValidationError({"image": "Product images must be 5 MB or smaller."})
            extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
            signature = upload.read(16)
            if extension == "png" and signature.startswith(b"\x89PNG\r\n\x1a\n") and signature[12:16] == b"IHDR":
                dimensions = upload.read(8)
                width, height = int.from_bytes(dimensions[:4], "big"), int.from_bytes(dimensions[4:], "big")
                valid = 0 < width <= 10000 and 0 < height <= 10000
            elif extension in {"jpg", "jpeg"} and signature.startswith(b"\xff\xd8\xff") and upload.size >= 4:
                upload.seek(-2, 2)
                valid = upload.read(2) == b"\xff\xd9"
            elif extension == "webp" and signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
                valid = int.from_bytes(signature[4:8], "little") + 8 == upload.size
            else:
                valid = False
            upload.seek(0)
            if not valid:
                raise serializers.ValidationError({"image": "Upload a valid PNG, JPEG, or WebP image."})
        category = attrs.get("category_ref", getattr(self.instance, "category_ref", None))
        subcategory = attrs.get("subcategory", getattr(self.instance, "subcategory", None))
        if subcategory and "category" in attrs and "category_ref" not in attrs:
            if subcategory.category.name != attrs["category"]:
                raise serializers.ValidationError({"subcategory": "Choose a subcategory from the selected category."})
        if subcategory and category and subcategory.category_id != category.id:
            raise serializers.ValidationError({"subcategory": "Choose a subcategory from the selected category."})
        if subcategory and not category:
            attrs["category_ref"] = subcategory.category
        return attrs

    def get_image_url(self, obj):
        if not obj.image:
            return ""
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url

    def create(self, validated_data):
        category_ref = validated_data.get("category_ref")
        if category_ref:
            validated_data["category"] = category_ref.name
        if not category_ref and validated_data.get("category"):
            category_ref, _ = Category.objects.get_or_create(
                name=validated_data["category"],
                defaults={"slug": slugify(validated_data["category"]) or "general"},
            )
            validated_data["category_ref"] = category_ref
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "category" in validated_data and "category_ref" not in validated_data:
            category_ref, _ = Category.objects.get_or_create(
                name=validated_data["category"],
                defaults={"slug": slugify(validated_data["category"]) or "general"},
            )
            validated_data["category_ref"] = category_ref
        if "category_ref" in validated_data and validated_data["category_ref"]:
            validated_data["category"] = validated_data["category_ref"].name
        return super().update(instance, validated_data)


class ProductDealSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    original_price = serializers.DecimalField(source="product.selling_price", max_digits=10, decimal_places=2, read_only=True)
    discount_percent = serializers.SerializerMethodField()

    class Meta:
        model = ProductDeal
        fields = ["id", "name", "product", "product_name", "original_price", "deal_price", "discount_percent", "starts_at", "ends_at", "is_active", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {"deal_price": {"min_value": Decimal("0.01")}}

    def get_discount_percent(self, obj):
        original = obj.product.selling_price
        if not original:
            return "0.00"
        return str(((original - obj.deal_price) * 100 / original).quantize(Decimal("0.01")))

    def validate(self, attrs):
        product = attrs.get("product", getattr(self.instance, "product", None))
        price = attrs.get("deal_price", getattr(self.instance, "deal_price", None))
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if product and price is not None and price >= product.selling_price:
            raise serializers.ValidationError({"deal_price": "A deal price must be below the product's regular selling price."})
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "The deal must end after it starts."})
        return attrs


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)
    slug = serializers.SlugField(required=False)

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "description", "is_active", "display_order", "product_count", "created_at"]
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        if not attrs.get("slug") and attrs.get("name"):
            attrs["slug"] = slugify(attrs["name"])
        return attrs


class SubCategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)
    slug = serializers.SlugField(required=False)

    class Meta:
        model = SubCategory
        fields = ["id", "category", "name", "slug", "description", "is_active", "display_order", "product_count", "created_at"]
        read_only_fields = ["created_at"]
        validators = []

    def validate(self, attrs):
        if not attrs.get("slug") and attrs.get("name"):
            attrs["slug"] = slugify(attrs["name"])
        category = attrs.get("category", getattr(self.instance, "category", None))
        slug = attrs.get("slug", getattr(self.instance, "slug", ""))
        existing = SubCategory.objects.filter(category=category, slug=slug)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if slug and existing.exists():
            raise serializers.ValidationError({"slug": "This slug is already used in the selected category."})
        return attrs


class BrandSerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Brand
        fields = ["id", "name", "description", "logo_url", "is_active", "product_count", "created_at"]
        read_only_fields = ["created_at"]


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = ["theme", "notify_low_stock", "notify_sales", "updated_at"]
        read_only_fields = ["updated_at"]


class BusinessSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessSettings
        fields = ["business_name", "address", "phone", "email", "currency_code", "tax_rate", "require_open_shift", "allow_negative_stock", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_currency_code(self, value):
        return value.upper()

    def validate_tax_rate(self, value):
        if value < 0 or value > 1:
            raise serializers.ValidationError("Tax rate must be between 0% and 100%.")
        return value


class UserProfileSerializer(serializers.ModelSerializer):
    groups = serializers.SerializerMethodField()
    phone = serializers.CharField(source="staff_profile.phone", required=False, allow_blank=True, max_length=30)
    avatar = serializers.FileField(source="staff_profile.avatar", required=False, write_only=True)
    avatar_url = serializers.SerializerMethodField()
    recent_activity = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name", "last_name", "phone", "avatar", "avatar_url", "recent_activity", "is_superuser", "groups"]
        read_only_fields = ["id", "username", "is_superuser", "groups", "avatar_url", "recent_activity"]

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("staff_profile", {})
        instance = super().update(instance, validated_data)
        if profile_data:
            profile, _ = StaffProfile.objects.get_or_create(user=instance)
            for field, value in profile_data.items():
                setattr(profile, field, value)
            profile.save()
        return instance

    def get_groups(self, obj) -> list[str]:
        return list(obj.groups.order_by("name").values_list("name", flat=True))

    def get_avatar_url(self, obj):
        profile = getattr(obj, "staff_profile", None)
        if not profile or not profile.avatar:
            return ""
        request = self.context.get("request")
        return request.build_absolute_uri(profile.avatar.url) if request else profile.avatar.url

    def get_recent_activity(self, obj):
        return LoginActivitySerializer(obj.login_activity.all()[:10], many=True).data


class LoginActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = LoginActivity
        fields = ["ip_address", "user_agent", "created_at"]
        read_only_fields = fields


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=12, max_length=128)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        new_password = attrs["new_password"]
        if attrs["confirm_password"] != new_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        if not any(char.islower() for char in new_password) or not any(char.isupper() for char in new_password) or not any(char.isdigit() for char in new_password) or not any(not char.isalnum() for char in new_password):
            raise serializers.ValidationError({"new_password": "Use at least one lowercase letter, uppercase letter, number, and symbol."})
        if user.check_password(new_password):
            raise serializers.ValidationError({"new_password": "Choose a password different from your current password."})
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "target", "is_read", "created_at", "read_at"]
        read_only_fields = fields


class PosMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    recipient_name = serializers.SerializerMethodField()
    attachment_name = serializers.SerializerMethodField()

    class Meta:
        model = PosMessage
        fields = ["id", "sender", "sender_name", "recipient", "recipient_name", "parent", "subject", "body", "attachment_name", "read_at", "sender_archived_at", "recipient_archived_at", "sent_at"]
        read_only_fields = fields

    def get_sender_name(self, obj):
        return obj.sender.get_full_name() or obj.sender.get_username()

    def get_recipient_name(self, obj):
        return obj.recipient.get_full_name() or obj.recipient.get_username()

    def get_attachment_name(self, obj):
        return obj.attachment.name.rsplit("/", 1)[-1] if obj.attachment else ""


class PosMessageWriteSerializer(serializers.ModelSerializer):
    recipient = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.filter(is_active=True))

    class Meta:
        model = PosMessage
        fields = ["recipient", "subject", "body", "attachment"]

    def validate(self, attrs):
        if attrs["recipient"].pk == self.context["request"].user.pk:
            raise serializers.ValidationError({"recipient": "Choose another staff member."})
        if not attrs.get("body", "").strip() and not attrs.get("attachment"):
            raise serializers.ValidationError({"body": "A message needs text or an attachment."})
        attachment = attrs.get("attachment")
        if attachment:
            allowed = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".csv", ".docx"}
            extension = "." + attachment.name.rsplit(".", 1)[-1].lower() if "." in attachment.name else ""
            if extension not in allowed:
                raise serializers.ValidationError({"attachment": "Allowed attachments: PDF, PNG, JPG, TXT, CSV, DOCX."})
            if attachment.size > 5 * 1024 * 1024:
                raise serializers.ValidationError({"attachment": "Attachments must be 5 MB or smaller."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        message = PosMessage.objects.create(sender=self.context["request"].user, **validated_data)
        from .notifications import notify_user
        notify_user(message.recipient, kind="message", title=f"New message: {message.subject}", body=f"From {message.sender.get_full_name() or message.sender.get_username()}", target="Message")
        return message


class PosMessageReplySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10000, allow_blank=False, trim_whitespace=True)


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = ["id", "code", "name", "subject_template", "updated_at"]


class EmailLogSerializer(serializers.ModelSerializer):
    initiated_by_name = serializers.CharField(source="initiated_by.username", read_only=True, allow_null=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True, allow_null=True)

    class Meta:
        model = EmailLog
        fields = ["id", "recipient", "subject", "template_code", "status", "error_message", "attempts", "initiated_by_name", "invoice_number", "created_at", "last_attempt_at", "sent_at"]
        read_only_fields = fields


class EmailSendSerializer(serializers.Serializer):
    TEMPLATE_CHOICES = [(code, code.replace("_", " ").title()) for code in ["manual", "receipt", "invoice", "order_confirmation", "low_stock"]]
    template = serializers.ChoiceField(choices=TEMPLATE_CHOICES, default="manual")
    recipient = serializers.EmailField()
    subject = serializers.CharField(max_length=250, required=False, allow_blank=True)
    body = serializers.CharField(max_length=50000, required=False, allow_blank=True)
    invoice = serializers.PrimaryKeyRelatedField(queryset=Invoice.objects.select_related("sale", "sale__customer"), required=False, allow_null=True)

    def validate(self, attrs):
        template = attrs["template"]
        if template == "manual" and (not attrs.get("subject", "").strip() or not attrs.get("body", "").strip()):
            raise serializers.ValidationError({"body": "Manual emails need both a subject and message."})
        if template in {"receipt", "invoice", "order_confirmation"} and not attrs.get("invoice"):
            raise serializers.ValidationError({"invoice": "Choose an invoice for this template."})
        return attrs


class CashRegisterMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashRegisterMovement
        fields = ["id", "kind", "amount", "note", "created_at"]
        read_only_fields = ["id", "created_at"]


class CashRegisterShiftSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    movements = CashRegisterMovementSerializer(many=True, read_only=True)

    class Meta:
        model = CashRegisterShift
        fields = [
            "id", "cashier", "cashier_name", "status", "opening_float", "expected_cash",
            "counted_cash", "difference", "opened_at", "closed_at", "movements",
        ]
        read_only_fields = ["cashier", "status", "expected_cash", "counted_cash", "difference", "opened_at", "closed_at"]


class CashRegisterOpenSerializer(serializers.Serializer):
    opening_float = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))


class CashRegisterCloseSerializer(serializers.Serializer):
    counted_cash = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))


class CashMovementWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=CashRegisterMovement.KIND_CHOICES)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    note = serializers.CharField(max_length=250)


class HeldBillLineWriteSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)


class HeldBillLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    unit_price = serializers.DecimalField(source="product.selling_price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = HeldBillItem
        fields = ["id", "product", "product_name", "product_sku", "quantity", "unit_price"]


class HeldBillSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, allow_null=True)
    items = HeldBillLineSerializer(many=True, read_only=True)

    class Meta:
        model = HeldBill
        fields = ["id", "cashier", "cashier_name", "customer", "customer_name", "notes", "created_at", "updated_at", "items"]
        read_only_fields = ["cashier", "created_at", "updated_at"]


class HeldBillWriteSerializer(serializers.Serializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(is_active=True), required=False, allow_null=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
    items = HeldBillLineWriteSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        products = [item["product"].id for item in items]
        if len(products) != len(set(products)):
            raise serializers.ValidationError("Each product can appear only once in a held bill.")
        return items

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        bill = HeldBill.objects.create(cashier=self.context["request"].user, **validated_data)
        HeldBillItem.objects.bulk_create([
            HeldBillItem(bill=bill, product=item["product"], quantity=item["quantity"])
            for item in items
        ])
        return bill


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "loyalty_points",
            "is_active",
        ]


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    deal_name = serializers.CharField(source="deal.name", read_only=True, allow_null=True)

    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "deal_name",
            "quantity",
            "unit_price",
            "line_total",
        ]


class SaleSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(is_active=True), required=False, allow_null=True)
    items = SaleItemSerializer(many=True, read_only=True)
    customer_name = serializers.SerializerMethodField(read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    payments = serializers.SerializerMethodField(read_only=True)
    cash_register_shift = serializers.IntegerField(source="cash_register_shift_id", read_only=True, allow_null=True)

    class Meta:
        model = Sale
        fields = [
            "id",
            "invoice_number",
            "customer",
            "customer_name",
            "sold_at",
            "subtotal",
            "tax_amount",
            "total",
            "status",
            "items",
            "payments",
            "cash_register_shift",
        ]
        read_only_fields = ["invoice_number", "sold_at", "subtotal", "tax_amount", "total", "status"]

    def get_customer_name(self, obj) -> str:
        return obj.customer.name if obj.customer else "Walk-in"

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_payments(self, obj) -> list[dict[str, object]]:
        return PaymentSerializer(obj.payments.all(), many=True).data


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "method", "amount", "reference", "created_at"]


class InvoiceSerializer(serializers.ModelSerializer):
    sale_id = serializers.IntegerField(source="sale.id", read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer_email = serializers.SerializerMethodField()
    total = serializers.DecimalField(source="sale.total", max_digits=12, decimal_places=2, read_only=True)
    amount_paid = serializers.SerializerMethodField()
    balance_due = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "invoice_number", "sale_id", "customer_name", "customer_email", "total", "amount_paid", "balance_due", "status", "created_at"]

    def get_customer_name(self, obj) -> str:
        return obj.sale.customer.name if obj.sale.customer else "Walk-in"

    def get_customer_email(self, obj) -> str:
        return obj.sale.customer.email if obj.sale.customer else ""

    def _paid(self, obj) -> Decimal:
        return sum((payment.amount for payment in obj.sale.payments.all()), Decimal("0.00"))

    def get_amount_paid(self, obj) -> str:
        return f"{self._paid(obj):.2f}"

    def get_balance_due(self, obj) -> str:
        return f"{max(Decimal('0.00'), obj.sale.total - self._paid(obj)):.2f}"


class InvoicePaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.METHOD_CHOICES)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reference = serializers.CharField(max_length=80, required=False, allow_blank=True)

class FinancialTransactionSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="sale.invoice_number", read_only=True, allow_null=True)
    cashier_name = serializers.CharField(source="cashier.get_username", read_only=True, allow_null=True)

    class Meta:
        model = FinancialTransaction
        fields = ["id", "reference", "kind", "amount", "payment_method", "invoice_number", "cashier_name", "created_at"]


class DailySalesSummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)


class BestSellingProductSerializer(serializers.Serializer):
    items__product__id = serializers.IntegerField()
    items__product__name = serializers.CharField()
    quantity_sold = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)


class DashboardSummarySerializer(serializers.Serializer):
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    today_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=4)
    total_products = serializers.IntegerField()
    active_customers = serializers.IntegerField()
    low_stock_alerts = serializers.IntegerField()
    sales_count = serializers.IntegerField()
    today_sales_count = serializers.IntegerField()
    best_selling_product = BestSellingProductSerializer(allow_null=True)
    daily_sales = DailySalesSummarySerializer(many=True)
    recent_sales = SaleSerializer(many=True)


class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)


class SalePaymentInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.METHOD_CHOICES)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reference = serializers.CharField(max_length=80, required=False, allow_blank=True)


class SaleWriteSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(is_active=True), required=False, allow_null=True)
    items = SaleItemInputSerializer(many=True, allow_empty=False, write_only=True)
    payment_method = serializers.ChoiceField(choices=Payment.METHOD_CHOICES, default="cash", write_only=True, required=False)
    payments = SalePaymentInputSerializer(many=True, allow_empty=False, required=False, write_only=True)

    class Meta:
        model = Sale
        fields = ["customer", "items", "payment_method", "payments"]

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        payment_method = validated_data.pop("payment_method", "cash")
        payment_lines = validated_data.pop("payments", None)
        customer = validated_data.get("customer")
        request = self.context.get("request")
        cashier = request.user if request and request.user.is_authenticated else None
        business_settings = get_business_settings()
        shift = CashRegisterShift.objects.select_for_update().filter(cashier=cashier, status="open").first() if cashier else None
        if business_settings.require_open_shift and not shift:
            raise serializers.ValidationError({"detail": "Open a cash register shift before completing a sale."})
        sale = Sale.objects.create(
            customer=customer,
            invoice_number="TEMP",
            status="completed",
            cashier=cashier,
            cash_register_shift=shift,
        )
        sale.invoice_number = f"INV-{sale.pk:08d}"
        sale.save(update_fields=["invoice_number"])

        subtotal = Decimal("0.00")
        low_stock_crossings = []
        for item_data in items_data:
            product = Product.objects.select_for_update().get(pk=item_data["product"].pk)
            quantity = item_data["quantity"]
            if not product.is_active:
                raise serializers.ValidationError({"items": f"{product.name} is inactive."})
            if product.stock_quantity < quantity and not business_settings.allow_negative_stock:
                raise serializers.ValidationError({"items": f"Insufficient stock for {product.name}."})

            active_deal = ProductDeal.objects.select_for_update().filter(
                product=product,
                is_active=True,
                starts_at__lte=timezone.now(),
                ends_at__gt=timezone.now(),
                deal_price__lt=product.selling_price,
            ).order_by("deal_price", "id").first()
            unit_price = active_deal.deal_price if active_deal else product.selling_price
            line_total = unit_price * quantity
            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                deal=active_deal,
            )
            previous_stock = product.stock_quantity
            product.stock_quantity -= quantity
            product.save(update_fields=["stock_quantity"])
            if previous_stock > product.reorder_level >= product.stock_quantity:
                low_stock_crossings.append(product)
            subtotal += line_total

        sale.subtotal = subtotal
        sale.tax_amount = (subtotal * business_settings.tax_rate).quantize(Decimal("0.01"))
        sale.total = sale.subtotal + sale.tax_amount
        sale.save(update_fields=["subtotal", "tax_amount", "total"])
        if payment_lines is None:
            payment_lines = [{"method": payment_method, "amount": sale.total, "reference": ""}]
        payment_total = sum((line["amount"] for line in payment_lines), Decimal("0.00"))
        if payment_total != sale.total:
            raise serializers.ValidationError({"payments": f"Payment amounts must equal the sale total of {sale.total}."})
        for line in payment_lines:
            Payment.objects.create(sale=sale, method=line["method"], amount=line["amount"], reference=line.get("reference", ""))
            FinancialTransaction.objects.create(
                reference=f"TXN-{uuid4().hex.upper()}",
                kind="sale_payment",
                amount=line["amount"],
                payment_method=line["method"],
                sale=sale,
                cash_register_shift=shift,
                cashier=sale.cashier,
            )
        Invoice.objects.create(sale=sale, invoice_number=sale.invoice_number, status="paid")
        notify_roles(
            roles={"admin", "manager"},
            kind="sale",
            title="Sale completed",
            body=f"{sale.invoice_number} was completed for {sale.total}.",
            target="Sales",
            preference="notify_sales",
        )
        for product in low_stock_crossings:
            notify_roles(
                roles={"admin", "manager"},
                kind="low_stock",
                title="Low stock alert",
                body=f"{product.name} has {product.stock_quantity} units remaining (threshold {product.reorder_level}).",
                target="Products",
                preference="notify_low_stock",
            )
        return sale


class RefundLineInputSerializer(serializers.Serializer):
    sale_item = serializers.PrimaryKeyRelatedField(queryset=SaleItem.objects.all())
    quantity = serializers.IntegerField(min_value=1)


class RefundRequestSerializer(serializers.Serializer):
    sale = serializers.PrimaryKeyRelatedField(queryset=Sale.objects.all())
    reason = serializers.CharField(max_length=1000)
    return_to_stock = serializers.BooleanField(default=False)
    items = RefundLineInputSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        sale = attrs["sale"]
        lines = attrs["items"]
        if sale.status != "completed":
            raise serializers.ValidationError({"sale": "Only completed sales can be refunded."})
        seen = set()
        for line in lines:
            sale_item = line["sale_item"]
            if sale_item.sale_id != sale.id:
                raise serializers.ValidationError({"items": "Each refund line must belong to the selected sale."})
            if sale_item.id in seen:
                raise serializers.ValidationError({"items": "A sale line can appear only once per refund request."})
            seen.add(sale_item.id)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        sale = Sale.objects.select_for_update().get(pk=validated_data["sale"].pk)
        if sale.status != "completed":
            raise serializers.ValidationError({"sale": "Only completed sales can be refunded."})
        selected = validated_data["items"]
        refund_subtotal = Decimal("0.00")
        for line in selected:
            sale_item = SaleItem.objects.select_for_update().get(pk=line["sale_item"].pk, sale=sale)
            already_reserved = RefundItem.objects.filter(
                sale_item=sale_item,
                refund__status__in=["pending", "approved"],
            ).aggregate(quantity=Sum("quantity"))["quantity"] or 0
            if already_reserved + line["quantity"] > sale_item.quantity:
                raise serializers.ValidationError({
                    "items": f"Requested quantity exceeds the unrefunded quantity for {sale_item.product.name}."
                })
            refund_subtotal += sale_item.unit_price * line["quantity"]

        refund = Refund.objects.create(
            sale=sale,
            reason=validated_data["reason"],
            return_to_stock=validated_data["return_to_stock"],
            subtotal=refund_subtotal,
            requested_by=self.context["request"].user,
        )
        RefundItem.objects.bulk_create([
            RefundItem(refund=refund, sale_item=line["sale_item"], quantity=line["quantity"],
                       subtotal=line["sale_item"].unit_price * line["quantity"])
            for line in selected
        ])
        notify_roles(
            roles={"admin", "manager"},
            kind="refund_request",
            title="Refund approval needed",
            body=f"Refund requested for {sale.invoice_number}: {refund.reason[:120]}",
            target="Refunds",
            exclude_user=self.context["request"].user,
        )
        return refund


class RefundItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="sale_item.product.name", read_only=True)
    product_sku = serializers.CharField(source="sale_item.product.sku", read_only=True)

    class Meta:
        model = RefundItem
        fields = ["id", "sale_item", "product_name", "product_sku", "quantity", "subtotal"]


class RefundSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="sale.invoice_number", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True, allow_null=True)
    items = RefundItemSerializer(many=True, read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id", "sale", "invoice_number", "status", "reason", "return_to_stock",
            "subtotal", "tax_amount", "total", "requested_by", "requested_by_name", "reviewed_by_name",
            "created_at", "reviewed_at", "items",
        ]
        read_only_fields = ["requested_by"]
