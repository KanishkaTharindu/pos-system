from decimal import Decimal

from django.db import DatabaseError, connection
from django.db.models import F, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Customer, Product, Sale
from .serializers import CustomerSerializer, ProductSerializer, SaleSerializer, SaleWriteSerializer


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


@api_view(["GET"])
@permission_classes([AllowAny])
def health_live(request):
    return Response({"status": "ok", "service": "pos-backend"})


@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1, current_database(), current_user, inet_server_port(), version()"
            )
            _, database, current_user, port, version = cursor.fetchone()
        return Response(
            {
                "status": "ok",
                "database": database,
                "role": current_user,
                "host": connection.settings_dict["HOST"],
                "port": port,
                "version": version,
                "configured_db": connection.settings_dict["NAME"],
            }
        )
    except DatabaseError:
        return Response({"status": "error", "database": "unavailable"}, status=503)


@api_view(["GET"])
@permission_classes([AllowAny])
def dashboard_summary(request):
    products = Product.objects.all()
    total_revenue = Sale.objects.filter(status="completed").aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    active_customers = Customer.objects.filter(is_active=True).count()
    low_stock = products.filter(stock_quantity__lte=F("reorder_level")).count()
    monthly_sales = Sale.objects.filter(status="completed").count()
    return Response(
        {
            "total_revenue": float(total_revenue),
            "total_products": products.count(),
            "active_customers": active_customers,
            "low_stock_alerts": low_stock,
            "sales_count": monthly_sales,
            "recent_sales": SaleSerializer(Sale.objects.select_related("customer").order_by("-sold_at")[:5], many=True).data,
        }
    )
