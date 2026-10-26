from django.conf import settings
from django.db import transaction
from decimal import Decimal
from uuid import uuid4
from rest_framework import serializers

from .models import Customer, Product, Sale, SaleItem


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "category",
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

    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "quantity",
            "unit_price",
            "line_total",
        ]


class SaleSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(is_active=True), required=False, allow_null=True)
    items = SaleItemSerializer(many=True, read_only=True)
    customer_name = serializers.SerializerMethodField(read_only=True)
    invoice_number = serializers.CharField(read_only=True)

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
        ]
        read_only_fields = ["invoice_number", "sold_at", "subtotal", "tax_amount", "total", "status"]

    def get_customer_name(self, obj) -> str:
        return obj.customer.name if obj.customer else "Walk-in"


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
    tax_rate = serializers.DecimalField(max_digits=4, decimal_places=2)
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


class SaleWriteSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(is_active=True), required=False, allow_null=True)
    items = SaleItemInputSerializer(many=True, allow_empty=False, write_only=True)

    class Meta:
        model = Sale
        fields = ["customer", "items"]

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        customer = validated_data.get("customer")
        sale = Sale.objects.create(
            customer=customer,
            invoice_number=f"INV-{uuid4().hex.upper()}",
            status="completed",
        )

        subtotal = Decimal("0.00")
        for item_data in items_data:
            product = Product.objects.select_for_update().get(pk=item_data["product"].pk)
            quantity = item_data["quantity"]
            if not product.is_active:
                raise serializers.ValidationError({"items": f"{product.name} is inactive."})
            if product.stock_quantity < quantity:
                raise serializers.ValidationError({"items": f"Insufficient stock for {product.name}."})

            unit_price = product.selling_price
            line_total = unit_price * quantity
            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
            )
            product.stock_quantity -= quantity
            product.save(update_fields=["stock_quantity"])
            subtotal += line_total

        sale.subtotal = subtotal
        sale.tax_amount = (subtotal * settings.POS_TAX_RATE).quantize(Decimal("0.01"))
        sale.total = sale.subtotal + sale.tax_amount
        sale.save(update_fields=["subtotal", "tax_amount", "total"])
        return sale
