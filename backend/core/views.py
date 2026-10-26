from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Customer, Product, Sale
from .serializers import CustomerSerializer, DashboardSummarySerializer, ProductSerializer, SaleSerializer, SaleWriteSerializer


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by("name")
    serializer_class = ProductSerializer


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by("name")
    serializer_class = CustomerSerializer


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.select_related("customer").prefetch_related("items__product").order_by("-sold_at")
    serializer_class = SaleSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return SaleWriteSerializer
        return SaleSerializer

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
            for item in sale.items.all():
                product = Product.objects.select_for_update().get(pk=item.product_id)
                product.stock_quantity = F("stock_quantity") + item.quantity
                product.save(update_fields=["stock_quantity"])
            sale.status = "cancelled"
            sale.save(update_fields=["status"])
        return Response(SaleSerializer(self.get_queryset().get(pk=sale.pk)).data)


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
def dashboard_summary(request):
    products = Product.objects.filter(is_active=True)
    completed_sales = Sale.objects.filter(status="completed")
    today = timezone.localdate()
    today_sales = completed_sales.filter(sold_at__date=today)
    total_revenue = completed_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    today_revenue = today_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    active_customers = Customer.objects.filter(is_active=True).count()
    low_stock = products.filter(stock_quantity__lte=F("reorder_level")).count()
    best_product = (
        Sale.objects.filter(status="completed", items__product__is_active=True)
        .values("items__product__id", "items__product__name")
        .annotate(quantity_sold=Sum("items__quantity"), revenue=Sum("items__line_total"))
        .order_by("-revenue")
        .first()
    )
    sales_by_day = dict(
        completed_sales.filter(sold_at__date__gte=today - timedelta(days=6), sold_at__date__lte=today)
        .annotate(day=TruncDate("sold_at"))
        .values_list("day")
        .annotate(total=Sum("total"))
        .values_list("day", "total")
    )
    daily_sales = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        total = sales_by_day.get(day, Decimal("0.00")) or Decimal("0.00")
        daily_sales.append({"date": day.isoformat(), "revenue": str(total)})
    return Response(
        {
            "total_revenue": str(total_revenue),
            "today_revenue": str(today_revenue),
            "total_products": products.count(),
            "active_customers": active_customers,
            "low_stock_alerts": low_stock,
            "sales_count": completed_sales.count(),
            "today_sales_count": today_sales.count(),
            "tax_rate": str(settings.POS_TAX_RATE),
            "best_selling_product": best_product,
            "daily_sales": daily_sales,
            "recent_sales": SaleSerializer(Sale.objects.select_related("customer").prefetch_related("items__product").order_by("-sold_at")[:10], many=True).data,
        }
    )
