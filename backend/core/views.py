from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Brand, BusinessSettings, CashRegisterMovement, CashRegisterShift, Category, Customer, EmailLog, EmailTemplate, FinancialTransaction, Invoice, LoginActivity, Notification, Payment, PosMessage, Product, ProductDeal, Refund, RefundItem, Sale, SaleItem, StaffProfile, SubCategory, UserPreference, HeldBill
from .email_service import attempt_email, ensure_email_templates, render_email_template, smtp_is_configured
from .configuration import get_business_settings
from .notifications import notify_user
from .permissions import PosCashierPermission, PosManagerPermission, PosRolePermission, PosSalesPermission
from .serializers import BrandSerializer, BusinessSettingsSerializer, CashMovementWriteSerializer, CashRegisterCloseSerializer, CashRegisterMovementSerializer, CashRegisterOpenSerializer, CashRegisterShiftSerializer, CategorySerializer, CustomerSerializer, DashboardSummarySerializer, EmailLogSerializer, EmailSendSerializer, EmailTemplateSerializer, FinancialTransactionSerializer, HeldBillSerializer, HeldBillWriteSerializer, InvoicePaymentSerializer, InvoiceSerializer, NotificationSerializer, PasswordChangeSerializer, PosMessageReplySerializer, PosMessageSerializer, PosMessageWriteSerializer, ProductDealSerializer, ProductSerializer, RefundRequestSerializer, RefundSerializer, SaleSerializer, SaleWriteSerializer, SubCategorySerializer, UserPreferenceSerializer, UserProfileSerializer
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken


def allocate_payment_amount(sale, amount):
    payments = list(sale.payments.all())
    if not payments or not sale.total:
        return [("cash", amount)]
    allocations = []
    remaining = amount
    for payment in payments[:-1]:
        share = min((amount * payment.amount / sale.total).quantize(Decimal("0.01")), remaining)
        if share > 0:
            allocations.append((payment.method, share))
            remaining -= share
    if remaining > 0:
        allocations.append((payments[-1].method, remaining))
    return allocations


class PosTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        request = self.context.get("request")
        ip_address = request.META.get("REMOTE_ADDR") if request else None
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""
        LoginActivity.objects.create(user=self.user, ip_address=ip_address or None, user_agent=user_agent)
        return data


class PosTokenObtainPairView(TokenObtainPairView):
    serializer_class = PosTokenObtainPairSerializer


class MyPreferencesView(APIView):
    serializer_class = UserPreferenceSerializer

    def get(self, request):
        preference, _ = UserPreference.objects.get_or_create(user=request.user)
        return Response(UserPreferenceSerializer(preference).data)

    def patch(self, request):
        preference, _ = UserPreference.objects.get_or_create(user=request.user)
        serializer = UserPreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MyProfileView(APIView):
    serializer_class = UserProfileSerializer

    def get(self, request):
        StaffProfile.objects.get_or_create(user=request.user)
        return Response(UserProfileSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        StaffProfile.objects.get_or_create(user=request.user)
        serializer = UserProfileSerializer(request.user, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MyPasswordChangeView(APIView):
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        refresh = RefreshToken.for_user(request.user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})


class BusinessSettingsView(APIView):
    """Store-wide configuration; managers can edit, cashiers can read."""
    permission_classes = [PosManagerPermission]
    serializer_class = BusinessSettingsSerializer

    def get(self, request):
        return Response(BusinessSettingsSerializer(get_business_settings()).data)

    def patch(self, request):
        instance = get_business_settings()
        serializer = BusinessSettingsSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class NotificationViewSet(viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [PosRolePermission]
    queryset = Notification.objects.all()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return self.queryset.filter(recipient=self.request.user)

    def list(self, request, *args, **kwargs):
        return Response(NotificationSerializer(self.get_queryset()[:100], many=True).data)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": self.get_queryset().filter(read_at__isnull=True).count()})

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": updated})


class PosMessageViewSet(viewsets.GenericViewSet):
    permission_classes = [PosSalesPermission]
    serializer_class = PosMessageSerializer
    queryset = PosMessage.objects.select_related("sender", "recipient", "parent").all()
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        return self.queryset.filter(
            Q(sender=user, sender_deleted_at__isnull=True)
            | Q(recipient=user, recipient_deleted_at__isnull=True)
        )

    def get_serializer_class(self):
        if self.action == "create":
            return PosMessageWriteSerializer
        if self.action == "reply":
            return PosMessageReplySerializer
        return PosMessageSerializer

    def list(self, request, *args, **kwargs):
        user = request.user
        folder = request.query_params.get("folder", "inbox")
        if folder == "inbox":
            messages = self.get_queryset().filter(recipient=user, recipient_archived_at__isnull=True)
        elif folder == "sent":
            messages = self.get_queryset().filter(sender=user, sender_archived_at__isnull=True)
        elif folder == "archived":
            messages = self.get_queryset().filter(
                Q(sender=user, sender_archived_at__isnull=False)
                | Q(recipient=user, recipient_archived_at__isnull=False)
            )
        else:
            raise serializers.ValidationError({"folder": "Choose inbox, sent, or archived."})
        search = request.query_params.get("search", "").strip()
        if search:
            messages = messages.filter(Q(subject__icontains=search) | Q(body__icontains=search) | Q(sender__username__icontains=search) | Q(recipient__username__icontains=search))
        messages = messages.order_by("-sent_at", "-id")[:100]
        return Response(PosMessageSerializer(messages, many=True).data)

    def retrieve(self, request, pk=None):
        message = self.get_object()
        if message.recipient_id == request.user.id and message.read_at is None:
            message.read_at = timezone.now()
            message.save(update_fields=["read_at"])
        return Response(PosMessageSerializer(message).data)

    @action(detail=True, methods=["get"])
    def thread(self, request, pk=None):
        message = self.get_object()
        while message.parent_id:
            message = self.get_queryset().get(pk=message.parent_id)
        thread = [message]
        seen = {message.pk}
        frontier = [message.pk]
        while frontier:
            replies = list(self.get_queryset().filter(parent_id__in=frontier).order_by("sent_at", "id"))
            frontier = [reply.pk for reply in replies if reply.pk not in seen]
            seen.update(frontier)
            thread.extend(reply for reply in replies if reply.pk in frontier)
        thread.sort(key=lambda item: (item.sent_at, item.pk))
        now = timezone.now()
        unread_ids = [item.pk for item in thread if item.recipient_id == request.user.id and item.read_at is None]
        if unread_ids:
            PosMessage.objects.filter(pk__in=unread_ids).update(read_at=now)
            for item in thread:
                if item.pk in unread_ids:
                    item.read_at = now
        return Response(PosMessageSerializer(thread, many=True).data)

    def create(self, request, *args, **kwargs):
        serializer = PosMessageWriteSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        message = serializer.save()
        return Response(PosMessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="recipients")
    def recipients(self, request):
        User = get_user_model()
        users = User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(groups__name__in={"admin", "manager", "cashier"})).exclude(pk=request.user.pk).distinct().order_by("username")
        return Response([{"id": user.pk, "username": user.get_username(), "name": user.get_full_name() or user.get_username()} for user in users])

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self.get_queryset().filter(recipient=request.user, read_at__isnull=True, recipient_archived_at__isnull=True).count()
        return Response({"count": count})

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        count = self.get_queryset().filter(recipient=request.user, read_at__isnull=True, recipient_archived_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": count})

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        message = self.get_object()
        field = "sender_archived_at" if message.sender_id == request.user.id else "recipient_archived_at"
        if getattr(message, field) is None:
            setattr(message, field, timezone.now())
            message.save(update_fields=[field])
        return Response(PosMessageSerializer(message).data)

    @action(detail=True, methods=["post"], url_path="mark-unread")
    def mark_unread(self, request, pk=None):
        message = self.get_object()
        if message.recipient_id != request.user.id:
            return Response({"detail": "Only the recipient can mark a message unread."}, status=status.HTTP_403_FORBIDDEN)
        if message.read_at is not None:
            message.read_at = None
            message.save(update_fields=["read_at"])
        return Response(PosMessageSerializer(message).data)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        parent = self.get_object()
        if request.user.id == parent.sender_id:
            recipient = parent.recipient
        else:
            recipient = parent.sender
        serializer = PosMessageReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = PosMessage.objects.create(
            sender=request.user,
            recipient=recipient,
            parent=parent,
            subject=parent.subject if parent.subject.lower().startswith("re:") else f"Re: {parent.subject}",
            body=serializer.validated_data["body"],
        )
        from .notifications import notify_user
        notify_user(recipient, kind="message", title=f"New message: {message.subject}", body=f"From {request.user.get_full_name() or request.user.get_username()}", target="Message")
        return Response(PosMessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        message = self.get_object()
        if not message.attachment:
            return Response({"detail": "This message has no attachment."}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(message.attachment.open("rb"), as_attachment=True, filename=Path(message.attachment.name).name)

    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        field = "sender_deleted_at" if message.sender_id == request.user.id else "recipient_deleted_at"
        setattr(message, field, timezone.now())
        message.save(update_fields=[field])
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailViewSet(viewsets.GenericViewSet):
    permission_classes = [PosSalesPermission]
    serializer_class = EmailLogSerializer
    queryset = EmailLog.objects.select_related("initiated_by", "invoice").all()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.groups.filter(name__in={"admin", "manager"}).exists():
            return self.queryset
        return self.queryset.filter(initiated_by=user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        if request.query_params.get("status") in {"sent", "failed"}:
            queryset = queryset.filter(status=request.query_params["status"])
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(recipient__icontains=search) | Q(subject__icontains=search) | Q(template_code__icontains=search))
        return Response(EmailLogSerializer(queryset[:100], many=True).data)

    def create(self, request, *args, **kwargs):
        serializer = EmailSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template_code = data["template"]
        invoice = data.get("invoice")
        if template_code == "manual":
            subject, body = data["subject"].strip(), data["body"].strip()
        else:
            ensure_email_templates()
            subject, body = render_email_template(template_code, invoice=invoice)
        log = EmailLog.objects.create(
            initiated_by=request.user,
            invoice=invoice,
            recipient=data["recipient"],
            subject=subject,
            body=body,
            template_code=template_code,
        )
        sent = attempt_email(log)
        result = {"email": EmailLogSerializer(log).data}
        if sent:
            result["detail"] = "Email sent."
            return Response(result, status=status.HTTP_201_CREATED)
        result["detail"] = log.error_message or "Email delivery failed. You can retry this from the sent log."
        return Response(result, status=status.HTTP_503_SERVICE_UNAVAILABLE if not smtp_is_configured() else status.HTTP_502_BAD_GATEWAY)

    @action(detail=False, methods=["get"], url_path="templates")
    def templates(self, request):
        templates = ensure_email_templates()
        return Response({"smtp_configured": smtp_is_configured(), "templates": EmailTemplateSerializer(templates, many=True).data})

    @action(detail=False, methods=["post"], url_path="test")
    def test_email(self, request):
        if not request.user.email:
            raise serializers.ValidationError({"email": "Add an email address to your profile before sending a test email."})
        log = EmailLog.objects.create(
            initiated_by=request.user,
            recipient=request.user.email,
            subject="POS SMTP configuration test",
            body="This message confirms that the PoshPointHub email service can send mail.",
            template_code="test",
        )
        sent = attempt_email(log)
        result = {"email": EmailLogSerializer(log).data, "detail": "Test email sent." if sent else log.error_message}
        return Response(result, status=status.HTTP_201_CREATED if sent else status.HTTP_503_SERVICE_UNAVAILABLE)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        log = self.get_object()
        if log.status != "failed":
            return Response({"detail": "Only failed emails can be retried."}, status=status.HTTP_400_BAD_REQUEST)
        sent = attempt_email(log)
        result = {"email": EmailLogSerializer(log).data, "detail": "Email sent." if sent else log.error_message}
        return Response(result, status=status.HTTP_200_OK if sent else status.HTTP_503_SERVICE_UNAVAILABLE)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.annotate(product_count=Count("products")).all()
    serializer_class = CategorySerializer
    permission_classes = [PosManagerPermission]

    def perform_destroy(self, instance):
        if instance.products.exists() or instance.subcategories.exists():
            raise serializers.ValidationError({"detail": "This category still has products or subcategories. Reassign them before deleting it."})
        instance.delete()


class SubCategoryViewSet(viewsets.ModelViewSet):
    queryset = SubCategory.objects.select_related("category").annotate(product_count=Count("products")).all()
    serializer_class = SubCategorySerializer
    permission_classes = [PosManagerPermission]

    def perform_destroy(self, instance):
        if instance.products.exists():
            raise serializers.ValidationError({"detail": "This subcategory still has products. Reassign them before deleting it."})
        instance.delete()


class BrandViewSet(viewsets.ModelViewSet):
    queryset = Brand.objects.annotate(product_count=Count("products")).all()
    serializer_class = BrandSerializer
    permission_classes = [PosManagerPermission]

    def perform_destroy(self, instance):
        if instance.products.exists():
            raise serializers.ValidationError({"detail": "This brand still has products. Reassign them before deleting it."})
        instance.delete()


class ProductPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by("name")
    serializer_class = ProductSerializer
    permission_classes = [PosManagerPermission]
    pagination_class = ProductPagination

    def get_queryset(self):
        queryset = super().get_queryset().select_related("category_ref", "subcategory", "brand")
        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(sku__icontains=search) | Q(barcode__icontains=search)
                | Q(category__icontains=search) | Q(brand__name__icontains=search)
                | Q(category_ref__name__icontains=search) | Q(subcategory__name__icontains=search)
            )
        if params.get("category"):
            category = params["category"]
            category_filter = Q(category__iexact=category) | Q(category_ref__name__iexact=category) | Q(category_ref__slug=category)
            if category.isdecimal():
                category_filter |= Q(category_ref_id=int(category))
            queryset = queryset.filter(category_filter)
        if params.get("subcategory"):
            queryset = queryset.filter(subcategory_id=params["subcategory"])
        if params.get("brand"):
            queryset = queryset.filter(brand_id=params["brand"])
        if params.get("status") in {"active", "inactive"}:
            queryset = queryset.filter(is_active=params["status"] == "active")
        if params.get("stock") == "low":
            queryset = queryset.filter(is_active=True, stock_quantity__lte=F("reorder_level"))
        elif params.get("stock") == "available":
            queryset = queryset.filter(is_active=True, stock_quantity__gt=F("reorder_level"))
        elif params.get("stock") == "out":
            queryset = queryset.filter(is_active=True, stock_quantity=0)
        ordering = params.get("ordering", "name")
        allowed_ordering = {"name", "sku", "selling_price", "stock_quantity", "created_at", "-name", "-sku", "-selling_price", "-stock_quantity", "-created_at"}
        if ordering in allowed_ordering:
            queryset = queryset.order_by(ordering)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        if "page" not in request.query_params and "page_size" not in request.query_params:
            return Response(self.get_serializer(queryset, many=True).data)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


class ProductDealViewSet(viewsets.ModelViewSet):
    queryset = ProductDeal.objects.select_related("product").all()
    serializer_class = ProductDealSerializer
    permission_classes = [PosManagerPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("active") == "true":
            now = timezone.now()
            queryset = queryset.filter(is_active=True, starts_at__lte=now, ends_at__gt=now)
        elif self.request.query_params.get("active") == "false":
            queryset = queryset.filter(is_active=False)
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(product__name__icontains=search) | Q(product__sku__icontains=search))
        return queryset


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by("name")
    serializer_class = CustomerSerializer
    permission_classes = [PosCashierPermission]


class GlobalSearchView(APIView):
    permission_classes = [PosCashierPermission]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Response({"query": query, "groups": {"products": [], "orders": [], "customers": [], "invoices": []}})

        products = Product.objects.filter(
            Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)
        ).order_by("name")[:5]
        sales = Sale.objects.select_related("customer").filter(
            Q(invoice_number__icontains=query) | Q(customer__name__icontains=query)
        ).order_by("-sold_at")[:5]
        customers = Customer.objects.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
        ).order_by("name")[:5]
        invoices = Invoice.objects.select_related("sale", "sale__customer").filter(
            Q(invoice_number__icontains=query) | Q(sale__customer__name__icontains=query)
        ).order_by("-created_at")[:5]
        return Response({
            "query": query,
            "groups": {
                "products": [{"id": row.id, "title": row.name, "detail": f"{row.sku} · {row.category}", "target": "Products"} for row in products],
                "orders": [{"id": row.id, "title": row.invoice_number, "detail": f"{row.customer.name if row.customer else 'Walk-in customer'} · {row.status} · {row.total}", "target": "Order"} for row in sales],
                "customers": [{"id": row.id, "title": row.name, "detail": row.phone or row.email or "Customer", "target": "Dashboard"} for row in customers],
                "invoices": [{"id": row.id, "title": row.invoice_number, "detail": f"{row.sale.customer.name if row.sale.customer else 'Walk-in customer'} · {row.status}", "target": "Invoices"} for row in invoices],
            },
        })


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.select_related("customer", "cashier").prefetch_related("items__product", "items__deal", "payments").order_by("-sold_at")
    serializer_class = SaleSerializer
    permission_classes = [PosSalesPermission]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return SaleWriteSerializer
        return SaleSerializer

    def get_permissions(self):
        if self.action == "cancel":
            return [PosManagerPermission()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = serializer.save()
        read_serializer = SaleSerializer(sale)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        with transaction.atomic():
            sale = Sale.objects.select_for_update().get(pk=self.get_object().pk)
            if sale.status != "completed":
                return Response({"detail": "Only completed sales can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
            if Refund.objects.filter(sale=sale).exclude(status="rejected").exists():
                return Response({"detail": "Sales with active refund requests cannot be cancelled. Resolve the refund workflow first."}, status=status.HTTP_400_BAD_REQUEST)
            for item in sale.items.all():
                product = Product.objects.select_for_update().get(pk=item.product_id)
                product.stock_quantity = F("stock_quantity") + item.quantity
                product.save(update_fields=["stock_quantity"])
            sale.status = "cancelled"
            sale.save(update_fields=["status"])
            if hasattr(sale, "invoice"):
                sale.invoice.status = "void"
                sale.invoice.save(update_fields=["status"])
            for method, amount in allocate_payment_amount(sale, sale.total):
                register_shift = CashRegisterShift.objects.filter(cashier=request.user, status="open").first() or sale.cash_register_shift
                FinancialTransaction.objects.create(
                    reference=f"TXN-{uuid4().hex.upper()}",
                    kind="reversal",
                    amount=-amount,
                    payment_method=method,
                    sale=sale,
                    cash_register_shift=register_shift,
                    cashier=request.user,
                )
        return Response(SaleSerializer(self.get_queryset().get(pk=sale.pk)).data)


class RefundViewSet(viewsets.ModelViewSet):
    queryset = Refund.objects.select_related("sale", "requested_by", "reviewed_by").prefetch_related(
        "items__sale_item__product"
    ).all()
    serializer_class = RefundSerializer
    permission_classes = [PosSalesPermission]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return RefundRequestSerializer
        return RefundSerializer

    def get_permissions(self):
        if self.action in {"approve", "reject"}:
            return [PosManagerPermission()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        serializer = RefundRequestSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        refund = serializer.save()
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        with transaction.atomic():
            refund = Refund.objects.select_for_update().select_related("sale").get(pk=self.get_object().pk)
            if refund.status != "pending":
                return Response({"detail": "Only pending refund requests can be approved."}, status=status.HTTP_400_BAD_REQUEST)
            if refund.requested_by_id == request.user.id:
                return Response({"detail": "A refund request must be reviewed by a different user."}, status=status.HTTP_403_FORBIDDEN)
            sale = Sale.objects.select_for_update().get(pk=refund.sale_id)
            if sale.status != "completed":
                return Response({"detail": "Only active completed sales can be refunded."}, status=status.HTTP_400_BAD_REQUEST)

            refund_items = list(refund.items.select_related("sale_item").all())
            all_sale_items = list(SaleItem.objects.select_for_update().filter(sale=sale))
            current_quantities = {item.sale_item_id: item.quantity for item in refund_items}
            is_full_refund = True
            for sale_item in all_sale_items:
                previously_refunded = RefundItem.objects.filter(
                    sale_item=sale_item, refund__sale=sale, refund__status="approved"
                ).aggregate(quantity=Sum("quantity"))["quantity"] or 0
                if previously_refunded + current_quantities.get(sale_item.id, 0) < sale_item.quantity:
                    is_full_refund = False
                    break

            refund.tax_amount = (
                sale.tax_amount - (Refund.objects.filter(sale=sale, status="approved").aggregate(total=Sum("tax_amount"))["total"] or Decimal("0.00"))
                if is_full_refund
                else (sale.tax_amount * refund.subtotal / sale.subtotal).quantize(Decimal("0.01")) if sale.subtotal else Decimal("0.00")
            )
            refund.total = refund.subtotal + refund.tax_amount
            refund.status = "approved"
            refund.reviewed_by = request.user
            refund.reviewed_at = timezone.now()
            refund.save(update_fields=["tax_amount", "total", "status", "reviewed_by", "reviewed_at"])

            for refund_item in refund_items:
                if refund.return_to_stock:
                    product = Product.objects.select_for_update().get(pk=refund_item.sale_item.product_id)
                    product.stock_quantity = F("stock_quantity") + refund_item.quantity
                    product.save(update_fields=["stock_quantity"])

            previously_refunded_total = Refund.objects.filter(sale=sale, status="approved").exclude(pk=refund.pk).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
            refund_total = previously_refunded_total + refund.total
            try:
                invoice = sale.invoice
            except Invoice.DoesNotExist:
                invoice = Invoice.objects.create(invoice_number=sale.invoice_number, sale=sale, status="paid")
            invoice.status = "void" if refund_total >= sale.total else "partially_paid"
            invoice.save(update_fields=["status"])
            if refund_total >= sale.total:
                sale.status = "refunded"
                sale.save(update_fields=["status"])

            for method, amount in allocate_payment_amount(sale, refund.total):
                register_shift = CashRegisterShift.objects.filter(cashier=request.user, status="open").first() or sale.cash_register_shift
                FinancialTransaction.objects.create(
                    reference=f"TXN-{uuid4().hex.upper()}",
                    kind="refund",
                    amount=-amount,
                    payment_method=method,
                    sale=sale,
                    cash_register_shift=register_shift,
                    cashier=request.user,
                )
            notify_user(
                refund.requested_by,
                kind="refund_review",
                title="Refund approved",
                body=f"Refund for {sale.invoice_number} was approved for {refund.total}.",
                target="Refunds",
            )
        return Response(RefundSerializer(self.get_queryset().get(pk=refund.pk)).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        with transaction.atomic():
            refund = Refund.objects.select_for_update().get(pk=self.get_object().pk)
            if refund.status != "pending":
                return Response({"detail": "Only pending refund requests can be rejected."}, status=status.HTTP_400_BAD_REQUEST)
            if refund.requested_by_id == request.user.id:
                return Response({"detail": "A refund request must be reviewed by a different user."}, status=status.HTTP_403_FORBIDDEN)
            refund.status = "rejected"
            refund.reviewed_by = request.user
            refund.reviewed_at = timezone.now()
            refund.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            notify_user(
                refund.requested_by,
                kind="refund_review",
                title="Refund rejected",
                body=f"Refund request for {refund.sale.invoice_number} was rejected.",
                target="Refunds",
            )
        return Response(RefundSerializer(self.get_queryset().get(pk=refund.pk)).data)


class CashRegisterViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CashRegisterShiftSerializer
    permission_classes = [PosCashierPermission]
    queryset = CashRegisterShift.objects.select_related("cashier").prefetch_related("movements").all()

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.groups.filter(name__in={"admin", "manager"}).exists():
            return self.queryset
        return self.queryset.filter(cashier=user)

    def get_serializer_class(self):
        if self.action == "create":
            return CashRegisterOpenSerializer
        if self.action == "close":
            return CashRegisterCloseSerializer
        if self.action == "movement":
            return CashMovementWriteSerializer
        return CashRegisterShiftSerializer

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        if page is not None:
            return self.get_paginated_response(CashRegisterShiftSerializer(page, many=True).data)
        return Response(CashRegisterShiftSerializer(self.get_queryset(), many=True).data)

    @action(detail=False, methods=["get"])
    def current(self, request):
        shift = self.get_queryset().filter(cashier=request.user, status="open").first()
        return Response(CashRegisterShiftSerializer(shift).data if shift else None)

    def create(self, request, *args, **kwargs):
        serializer = CashRegisterOpenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if CashRegisterShift.objects.filter(cashier=request.user, status="open").exists():
                raise serializers.ValidationError({"detail": "You already have an open cash register shift."})
            shift = CashRegisterShift.objects.create(cashier=request.user, opening_float=serializer.validated_data["opening_float"])
        return Response(CashRegisterShiftSerializer(shift).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        serializer = CashRegisterCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            shift = CashRegisterShift.objects.select_for_update().get(pk=self.get_object().pk)
            if shift.status != "open":
                return Response({"detail": "This register shift is already closed."}, status=status.HTTP_400_BAD_REQUEST)
            cash_transactions = FinancialTransaction.objects.filter(
                Q(cash_register_shift=shift) | Q(cash_register_shift__isnull=True, sale__cash_register_shift=shift),
                payment_method="cash",
                kind__in=["sale_payment", "refund", "reversal"],
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            movements = shift.movements.aggregate(
                cash_in=Sum("amount", filter=Q(kind="cash_in")),
                cash_out=Sum("amount", filter=Q(kind="cash_out")),
            )
            expected = shift.opening_float + cash_transactions + (movements["cash_in"] or Decimal("0.00")) - (movements["cash_out"] or Decimal("0.00"))
            counted = serializer.validated_data["counted_cash"]
            shift.status = "closed"
            shift.expected_cash = expected
            shift.counted_cash = counted
            shift.difference = counted - expected
            shift.closed_at = timezone.now()
            shift.save(update_fields=["status", "expected_cash", "counted_cash", "difference", "closed_at"])
        return Response(CashRegisterShiftSerializer(self.get_queryset().get(pk=shift.pk)).data)

    @action(detail=True, methods=["post"])
    def movement(self, request, pk=None):
        serializer = CashMovementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            shift = CashRegisterShift.objects.select_for_update().get(pk=self.get_object().pk)
            if shift.status != "open":
                return Response({"detail": "Cash movements require an open shift."}, status=status.HTTP_400_BAD_REQUEST)
            movement = CashRegisterMovement.objects.create(
                shift=shift,
                recorded_by=request.user,
                **serializer.validated_data,
            )
            amount = movement.amount if movement.kind == "cash_in" else -movement.amount
            FinancialTransaction.objects.create(
                reference=f"TXN-{uuid4().hex.upper()}",
                kind=movement.kind,
                amount=amount,
                payment_method="cash",
                cash_register_shift=shift,
                cashier=request.user,
            )
        return Response(CashRegisterShiftSerializer(self.get_queryset().get(pk=shift.pk)).data, status=status.HTTP_201_CREATED)


class HeldBillViewSet(viewsets.ModelViewSet):
    serializer_class = HeldBillSerializer
    permission_classes = [PosSalesPermission]
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = HeldBill.objects.select_related("cashier", "customer").prefetch_related("items__product").all()

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.groups.filter(name__in={"admin", "manager"}).exists():
            return self.queryset
        return self.queryset.filter(cashier=user)

    def get_serializer_class(self):
        return HeldBillWriteSerializer if self.action == "create" else HeldBillSerializer

    def create(self, request, *args, **kwargs):
        serializer = HeldBillWriteSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        bill = serializer.save()
        return Response(HeldBillSerializer(bill).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def resume(self, request, pk=None):
        return Response(HeldBillSerializer(self.get_object()).data)


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.select_related("sale", "sale__customer").prefetch_related("sale__payments").all()
    serializer_class = InvoiceSerializer
    permission_classes = [PosSalesPermission]

    @action(detail=True, methods=["post"])
    def record_payment(self, request, pk=None):
        serializer = InvoicePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().select_related("sale").get(pk=self.get_object().pk)
            sale = Sale.objects.select_for_update().get(pk=invoice.sale_id)
            if invoice.status == "void":
                return Response({"detail": "A void invoice cannot accept payments."}, status=status.HTTP_400_BAD_REQUEST)
            paid = Payment.objects.filter(sale=sale).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            amount = serializer.validated_data["amount"]
            balance = sale.total - paid
            if amount > balance:
                raise serializers.ValidationError({"amount": f"Payment cannot exceed the outstanding balance of {balance:.2f}."})
            register_shift = CashRegisterShift.objects.select_for_update().filter(cashier=request.user, status="open").first()
            if get_business_settings().require_open_shift and not register_shift:
                raise serializers.ValidationError({"detail": "Open a cash register shift before recording an invoice payment."})
            Payment.objects.create(
                sale=sale,
                method=serializer.validated_data["method"],
                amount=amount,
                reference=serializer.validated_data.get("reference", ""),
            )
            FinancialTransaction.objects.create(
                reference=f"TXN-{uuid4().hex.upper()}",
                kind="sale_payment",
                amount=amount,
                payment_method=serializer.validated_data["method"],
                sale=sale,
                cash_register_shift=register_shift,
                cashier=request.user,
            )
            invoice.status = "paid" if amount == balance else "partially_paid"
            invoice.save(update_fields=["status"])
        return Response(InvoiceSerializer(self.get_queryset().get(pk=invoice.pk)).data)


class FinancialTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FinancialTransaction.objects.select_related("sale", "cashier").all()
    serializer_class = FinancialTransactionSerializer
    permission_classes = [PosRolePermission]


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_live(request):
    return Response({"status": "ok", "service": "pos-backend"})


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response(
            {
                "status": "ok",
                "database": "available",
            }
        )
    except DatabaseError:
        return Response({"status": "error", "database": "unavailable"}, status=503)


@extend_schema(responses=DashboardSummarySerializer)
@api_view(["GET"])
@permission_classes([PosRolePermission])
def dashboard_summary(request):
    products = Product.objects.filter(is_active=True)
    reportable_sales = Sale.objects.filter(status__in=["completed", "refunded"])
    refund_transactions = FinancialTransaction.objects.filter(kind="refund")
    today = timezone.localdate()
    try:
        days = int(request.query_params.get("days", "7"))
    except ValueError as exc:
        raise serializers.ValidationError({"days": "Choose a whole number from 7 to 365."}) from exc
    if days < 7 or days > 365:
        raise serializers.ValidationError({"days": "Choose a whole number from 7 to 365."})
    today_sales = reportable_sales.filter(sold_at__date=today)
    gross_revenue = reportable_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    total_refunds = refund_transactions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_revenue = gross_revenue + total_refunds
    today_gross_revenue = today_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    today_refunds = refund_transactions.filter(created_at__date=today).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    today_revenue = today_gross_revenue + today_refunds
    active_customers = Customer.objects.filter(is_active=True).count()
    low_stock = products.filter(stock_quantity__lte=F("reorder_level")).count()
    best_product = (
        Sale.objects.filter(status__in=["completed", "refunded"], items__product__is_active=True)
        .values("items__product__id", "items__product__name")
        .annotate(quantity_sold=Sum("items__quantity"), revenue=Sum("items__line_total"))
        .order_by("-revenue")
        .first()
    )
    daily_sales = []
    if days > 60:
        month_count = min(12, (days + 29) // 30)
        last_month = today.year * 12 + today.month - 1
        first_month = last_month - month_count + 1
        first_year, first_month_index = divmod(first_month, 12)
        first_day = date(first_year, first_month_index + 1, 1)
        totals_by_day = dict(
            reportable_sales.filter(sold_at__date__gte=first_day, sold_at__date__lte=today)
            .annotate(day=TruncDate("sold_at"))
            .values_list("day")
            .annotate(total=Sum("total"))
            .values_list("day", "total")
        )
        sales_by_month = {}
        for day, total in totals_by_day.items():
            month = date(day.year, day.month, 1)
            sales_by_month[month] = sales_by_month.get(month, Decimal("0.00")) + (total or Decimal("0.00"))
        refunds_by_day = dict(
            refund_transactions.filter(created_at__date__gte=first_day, created_at__date__lte=today)
            .annotate(day=TruncDate("created_at"))
            .values_list("day")
            .annotate(total=Sum("amount"))
            .values_list("day", "total")
        )
        for day, amount in refunds_by_day.items():
            month = date(day.year, day.month, 1)
            sales_by_month[month] = sales_by_month.get(month, Decimal("0.00")) + (amount or Decimal("0.00"))
        for offset in range(month_count - 1, -1, -1):
            year, month_index = divmod(last_month - offset, 12)
            month = date(year, month_index + 1, 1)
            total = sales_by_month.get(month, Decimal("0.00")) or Decimal("0.00")
            daily_sales.append({"date": month.isoformat(), "revenue": str(total)})
    else:
        sales_by_day = dict(
            reportable_sales.filter(sold_at__date__gte=today - timedelta(days=days - 1), sold_at__date__lte=today)
            .annotate(day=TruncDate("sold_at"))
            .values_list("day")
            .annotate(total=Sum("total"))
            .values_list("day", "total")
        )
        refunds_by_day = dict(
            refund_transactions.filter(created_at__date__gte=today - timedelta(days=days - 1), created_at__date__lte=today)
            .annotate(day=TruncDate("created_at"))
            .values_list("day")
            .annotate(total=Sum("amount"))
            .values_list("day", "total")
        )
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            total = (sales_by_day.get(day, Decimal("0.00")) or Decimal("0.00")) + (refunds_by_day.get(day, Decimal("0.00")) or Decimal("0.00"))
            daily_sales.append({"date": day.isoformat(), "revenue": str(total)})
    return Response(
        {
            "total_revenue": str(total_revenue),
            "today_revenue": str(today_revenue),
            "total_products": products.count(),
            "active_customers": active_customers,
            "low_stock_alerts": low_stock,
            "sales_count": reportable_sales.count(),
            "today_sales_count": today_sales.count(),
            "tax_rate": str(get_business_settings().tax_rate),
            "best_selling_product": best_product,
            "daily_sales": daily_sales,
            "recent_sales": SaleSerializer(Sale.objects.select_related("customer").prefetch_related("items__product", "items__deal").order_by("-sold_at")[:10], many=True).data,
        }
    )
