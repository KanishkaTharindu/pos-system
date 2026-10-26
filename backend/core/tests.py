from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Customer, Product, Sale


class PosApiWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cashier", password="correct-horse-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(
            sku="COFFEE-01", name="Coffee", category="Drinks", cost_price="100.00",
            selling_price="250.00", stock_quantity=8, reorder_level=2,
        )
        self.customer = Customer.objects.create(name="Riley Example", phone="0771234567")

    def test_sale_records_items_totals_and_decrements_inventory(self):
        response = self.client.post(
            "/api/v1/sales/",
            {"customer": self.customer.id, "items": [{"product": self.product.id, "quantity": 2}]},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["customer_name"], "Riley Example")
        self.assertEqual(response.data["subtotal"], "500.00")
        self.assertEqual(response.data["tax_amount"], "50.00")
        self.assertEqual(response.data["total"], "550.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 6)
        self.assertEqual(response.data["items"][0]["quantity"], 2)

    def test_failed_sale_rolls_back_all_lines_and_stock_changes(self):
        empty_stock = Product.objects.create(sku="TEA-01", name="Tea", selling_price="50.00", stock_quantity=0)

        response = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 3}, {"product": empty_stock.id, "quantity": 1}]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(Sale.objects.count(), 0)

    def test_dashboard_reports_database_values(self):
        sale = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(sale.status_code, 201, sale.data)

        response = self.client.get("/api/v1/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["today_revenue"]), Decimal("275.00"))
        self.assertEqual(response.data["today_sales_count"], 1)
        self.assertEqual(response.data["total_products"], 1)
        self.assertEqual(response.data["best_selling_product"]["items__product__name"], "Coffee")
        self.assertEqual(len(response.data["daily_sales"]), 7)

    def test_api_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 401)
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(dashboard.status_code, 401)

    def test_cashier_can_obtain_jwt_and_use_protected_api(self):
        self.client.force_authenticate(user=None)
        token = self.client.post(
            "/api/v1/auth/token/",
            {"username": "cashier", "password": "correct-horse-123"},
            format="json",
        )
        self.assertEqual(token.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.data['access']}")
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_product_and_customer_records_can_be_updated(self):
        product_response = self.client.patch(
            f"/api/v1/products/{self.product.id}/",
            {"selling_price": "275.00", "stock_quantity": 12},
            format="json",
        )
        customer_response = self.client.patch(
            f"/api/v1/customers/{self.customer.id}/",
            {"phone": "0777654321", "is_active": False},
            format="json",
        )
        self.assertEqual(product_response.status_code, 200, product_response.data)
        self.assertEqual(product_response.data["selling_price"], "275.00")
        self.assertEqual(product_response.data["stock_quantity"], 12)
        self.assertEqual(customer_response.status_code, 200, customer_response.data)
        self.assertEqual(customer_response.data["phone"], "0777654321")
        self.assertFalse(customer_response.data["is_active"])

    def test_health_endpoints_are_public_and_do_not_disclose_database_details(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok", "database": "available"})

    def test_negative_stock_and_price_are_rejected(self):
        response = self.client.post(
            "/api/v1/products/",
            {"sku": "BAD-01", "name": "Invalid", "cost_price": "-1.00", "selling_price": "1.00", "stock_quantity": -1, "reorder_level": 0},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cancelling_sale_restores_stock_and_removes_it_from_revenue(self):
        sale = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 3}]},
            format="json",
        )
        self.assertEqual(sale.status_code, 201, sale.data)

        cancelled = self.client.post(f"/api/v1/sales/{sale.data['id']}/cancel/")

        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data["status"], "cancelled")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(Decimal(dashboard.data["total_revenue"]), Decimal("0.00"))
        second_cancel = self.client.post(f"/api/v1/sales/{sale.data['id']}/cancel/")
        self.assertEqual(second_cancel.status_code, 400)

