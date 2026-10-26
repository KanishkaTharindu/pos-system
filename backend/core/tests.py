from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from tempfile import TemporaryDirectory
from rest_framework.test import APIClient

from .models import CashRegisterMovement, CashRegisterShift, Customer, EmailLog, EmailTemplate, FinancialTransaction, HeldBill, Invoice, Notification, Payment, PosMessage, Product, ProductDeal, Refund, Sale, SaleItem


class PosApiWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cashier", password="correct-horse-123")
        cashier_group, _ = Group.objects.get_or_create(name="cashier")
        manager_group, _ = Group.objects.get_or_create(name="manager")
        self.user.groups.add(cashier_group)
        self.manager = get_user_model().objects.create_user(username="manager", password="manager-password-123")
        self.manager.groups.add(manager_group)
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
        self.assertEqual(Payment.objects.filter(sale_id=response.data["id"]).count(), 1)
        self.assertEqual(Invoice.objects.get(sale_id=response.data["id"]).invoice_number, response.data["invoice_number"])
        self.assertEqual(FinancialTransaction.objects.get(sale_id=response.data["id"]).amount, Decimal("550.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 6)
        self.assertEqual(response.data["items"][0]["quantity"], 2)

    def test_split_sale_payment_records_tenders_and_rolls_back_mismatched_totals(self):
        response = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 2}], "payments": [
                {"method": "cash", "amount": "200.00"},
                {"method": "card", "amount": "350.00", "reference": "TERM-42"},
            ]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual([(payment["method"], payment["amount"]) for payment in response.data["payments"]], [("cash", "200.00"), ("card", "350.00")])
        self.assertEqual(FinancialTransaction.objects.filter(sale_id=response.data["id"], kind="sale_payment").count(), 2)

        invalid = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 1}], "payments": [{"method": "cash", "amount": "1.00"}]},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(Sale.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 6)

        sale_item = SaleItem.objects.get(sale_id=response.data["id"])
        refund = self.client.post("/api/v1/refunds/", {
            "sale": response.data["id"], "reason": "Split tender refund", "return_to_stock": True, "items": [{"sale_item": sale_item.id, "quantity": 1}],
        }, format="json")
        self.assertEqual(refund.status_code, 201, refund.data)
        self.client.force_authenticate(self.manager)
        approved = self.client.post(f"/api/v1/refunds/{refund.data['id']}/approve/")
        self.assertEqual(approved.status_code, 200, approved.data)
        refund_tenders = FinancialTransaction.objects.filter(sale_id=response.data["id"], kind="refund").order_by("payment_method")
        self.assertEqual([(row.payment_method, row.amount) for row in refund_tenders], [("card", Decimal("-175.00")), ("cash", Decimal("-100.00"))])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)

    def test_invoice_accepts_later_payments_and_tracks_cash_to_current_shift(self):
        sale = Sale.objects.create(invoice_number="INV-LATER-01", total=Decimal("100.00"), cashier=self.user)
        invoice = Invoice.objects.create(sale=sale, invoice_number=sale.invoice_number, status="unpaid")
        opened = self.client.post("/api/v1/cash-register/", {"opening_float": "20.00"}, format="json")
        self.assertEqual(opened.status_code, 201, opened.data)

        partial = self.client.post(f"/api/v1/invoices/{invoice.id}/record_payment/", {"method": "cash", "amount": "30.00"}, format="json")
        self.assertEqual(partial.status_code, 200, partial.data)
        self.assertEqual(partial.data["status"], "partially_paid")
        self.assertEqual(partial.data["amount_paid"], "30.00")
        self.assertEqual(partial.data["balance_due"], "70.00")
        overpayment = self.client.post(f"/api/v1/invoices/{invoice.id}/record_payment/", {"method": "cash", "amount": "71.00"}, format="json")
        self.assertEqual(overpayment.status_code, 400)
        final = self.client.post(f"/api/v1/invoices/{invoice.id}/record_payment/", {"method": "card", "amount": "70.00", "reference": "TERM-INV-02"}, format="json")
        self.assertEqual(final.status_code, 200, final.data)
        self.assertEqual(final.data["status"], "paid")
        self.assertEqual(final.data["balance_due"], "0.00")

        shift_id = opened.data["id"]
        closed = self.client.post(f"/api/v1/cash-register/{shift_id}/close/", {"counted_cash": "50.00"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data["expected_cash"], "50.00")

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

    def test_global_search_returns_grouped_database_matches(self):
        sale = self.client.post(
            "/api/v1/sales/",
            {"customer": self.customer.id, "items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(sale.status_code, 201, sale.data)

        response = self.client.get("/api/v1/search/?q=Riley")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["groups"]["customers"][0]["title"], "Riley Example")
        self.assertEqual(response.data["groups"]["orders"][0]["title"], sale.data["invoice_number"])
        self.assertEqual(response.data["groups"]["invoices"][0]["title"], sale.data["invoice_number"])
        too_short = self.client.get("/api/v1/search/?q=R")
        self.assertEqual(too_short.status_code, 200)
        self.assertFalse(any(too_short.data["groups"].values()))

    def test_dashboard_periods_return_daily_or_monthly_real_aggregates(self):
        daily = self.client.get("/api/v1/dashboard/?days=30")
        annual = self.client.get("/api/v1/dashboard/?days=365")
        invalid = self.client.get("/api/v1/dashboard/?days=2")
        self.assertEqual(daily.status_code, 200)
        self.assertEqual(len(daily.data["daily_sales"]), 30)
        self.assertEqual(annual.status_code, 200)
        self.assertEqual(len(annual.data["daily_sales"]), 12)
        self.assertEqual(invalid.status_code, 400)

    def test_api_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 401)
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(dashboard.status_code, 401)

    def test_product_image_upload_is_validated_and_stored_as_media(self):
        valid_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + (1).to_bytes(4, "big") + (1).to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        )
        self.client.force_authenticate(self.manager)
        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            invalid = self.client.post("/api/v1/products/", {
                "sku": "IMG-BAD", "name": "Bad image", "image": SimpleUploadedFile("bad.png", b"not an image", content_type="image/png"),
            }, format="multipart")
            self.assertEqual(invalid.status_code, 400)

            uploaded = self.client.post("/api/v1/products/", {
                "sku": "IMG-GOOD", "name": "Image product", "image": SimpleUploadedFile("good.png", valid_png, content_type="image/png"),
            }, format="multipart")
            self.assertEqual(uploaded.status_code, 201, uploaded.data)
            self.assertTrue(uploaded.data["image_url"].endswith("/media/product_images/good.png"))
            image_product = Product.objects.get(sku="IMG-GOOD")
            self.assertTrue(image_product.image.storage.exists(image_product.image.name))

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
        self.client.force_authenticate(self.manager)
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
        self.client.force_authenticate(self.manager)
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

        self.client.force_authenticate(self.manager)
        cancelled = self.client.post(f"/api/v1/sales/{sale.data['id']}/cancel/")

        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data["status"], "cancelled")
        self.assertEqual(Invoice.objects.get(sale_id=sale.data["id"]).status, "void")
        self.assertEqual(FinancialTransaction.objects.filter(sale_id=sale.data["id"], kind="reversal").count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(Decimal(dashboard.data["total_revenue"]), Decimal("0.00"))
        second_cancel = self.client.post(f"/api/v1/sales/{sale.data['id']}/cancel/")
        self.assertEqual(second_cancel.status_code, 400)

    def test_catalog_references_link_to_products_and_protect_used_records(self):
        self.client.force_authenticate(self.manager)
        category = self.client.post("/api/v1/categories/", {"name": "Pantry"}, format="json")
        self.assertEqual(category.status_code, 201, category.data)
        self.assertEqual(category.data["slug"], "pantry")
        subcategory = self.client.post(
            "/api/v1/subcategories/",
            {"category": category.data["id"], "name": "Tea"},
            format="json",
        )
        brand = self.client.post("/api/v1/brands/", {"name": "Example Foods"}, format="json")
        self.assertEqual(subcategory.status_code, 201, subcategory.data)
        self.assertEqual(brand.status_code, 201, brand.data)
        product = self.client.post(
            "/api/v1/products/",
            {
                "sku": "TEA-REL-01", "name": "Breakfast Tea", "category": "Pantry",
                "category_ref": category.data["id"], "subcategory": subcategory.data["id"],
                "brand": brand.data["id"], "selling_price": "300.00", "stock_quantity": 4,
            },
            format="json",
        )
        self.assertEqual(product.status_code, 201, product.data)
        self.assertEqual(self.client.get("/api/v1/categories/").data[0]["product_count"], 1)
        self.assertEqual(self.client.get("/api/v1/subcategories/").data[0]["product_count"], 1)
        self.assertEqual(self.client.get("/api/v1/brands/").data[0]["product_count"], 1)
        self.assertEqual(self.client.delete(f"/api/v1/categories/{category.data['id']}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/v1/subcategories/{subcategory.data['id']}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/v1/brands/{brand.data['id']}/").status_code, 400)

    def test_product_rejects_subcategory_from_a_different_category(self):
        self.client.force_authenticate(self.manager)
        first = self.client.post("/api/v1/categories/", {"name": "First"}, format="json")
        second = self.client.post("/api/v1/categories/", {"name": "Second"}, format="json")
        subcategory = self.client.post(
            "/api/v1/subcategories/",
            {"category": first.data["id"], "name": "First subcategory"},
            format="json",
        )
        response = self.client.post(
            "/api/v1/products/",
            {
                "sku": "MISMATCH-01", "name": "Mismatch", "category_ref": second.data["id"],
                "subcategory": subcategory.data["id"], "selling_price": "10.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_user_preferences_are_private_and_persisted(self):
        updated = self.client.patch(
            "/api/v1/preferences/me/",
            {"theme": "dark", "notify_low_stock": False},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["theme"], "dark")
        self.assertFalse(updated.data["notify_low_stock"])
        self.assertEqual(self.client.get("/api/v1/preferences/me/").data["theme"], "dark")
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/preferences/me/").status_code, 401)

    def test_cashier_can_checkout_but_cannot_manage_catalog_or_cancel(self):
        product_edit = self.client.patch(
            f"/api/v1/products/{self.product.id}/",
            {"selling_price": "260.00"},
            format="json",
        )
        sale = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(product_edit.status_code, 403)
        self.assertEqual(sale.status_code, 201, sale.data)
        cancel = self.client.post(f"/api/v1/sales/{sale.data['id']}/cancel/")
        self.assertEqual(cancel.status_code, 403)

    def test_product_endpoint_supports_server_search_filter_and_pagination(self):
        Product.objects.create(
            sku="RICE-02", name="Rice", category="Pantry", selling_price="400.00",
            stock_quantity=2, reorder_level=3,
        )
        Product.objects.create(
            sku="TEA-02", name="Tea", category="Drinks", selling_price="150.00",
            stock_quantity=12, reorder_level=2,
        )
        response = self.client.get("/api/v1/products/?search=rice&page=1&page_size=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["sku"], "RICE-02")
        low_stock = self.client.get("/api/v1/products/?stock=low&page_size=20")
        self.assertEqual([item["sku"] for item in low_stock.data["results"]], ["RICE-02"])

    def test_profile_returns_role_and_only_updates_current_users_profile(self):
        response = self.client.get("/api/v1/profile/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "cashier")
        self.assertEqual(response.data["groups"], ["cashier"])
        updated = self.client.patch("/api/v1/profile/me/", {"first_name": "Taylor", "email": "taylor@example.test"}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Taylor")
        self.assertEqual(self.user.email, "taylor@example.test")

    def test_refund_request_requires_manager_approval_and_updates_money_stock_and_invoice(self):
        sale_response = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 3}]},
            format="json",
        )
        sale_item = SaleItem.objects.get(sale_id=sale_response.data["id"])
        request_body = {
            "sale": sale_response.data["id"],
            "reason": "Customer returned one item",
            "return_to_stock": True,
            "items": [{"sale_item": sale_item.id, "quantity": 1}],
        }
        refund_response = self.client.post("/api/v1/refunds/", request_body, format="json")
        self.assertEqual(refund_response.status_code, 201, refund_response.data)
        self.assertEqual(refund_response.data["status"], "pending")
        self.assertEqual(Notification.objects.filter(recipient=self.manager, kind="refund_request").count(), 1)
        duplicate_reservation = self.client.post(
            "/api/v1/refunds/",
            {**request_body, "items": [{"sale_item": sale_item.id, "quantity": 3}]},
            format="json",
        )
        self.assertEqual(duplicate_reservation.status_code, 400)
        cashier_approval = self.client.post(f"/api/v1/refunds/{refund_response.data['id']}/approve/")
        self.assertEqual(cashier_approval.status_code, 403)
        self.client.force_authenticate(self.manager)
        approved = self.client.post(f"/api/v1/refunds/{refund_response.data['id']}/approve/")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(Notification.objects.filter(recipient=self.user, kind="refund_review", title="Refund approved").count(), 1)
        self.assertEqual(approved.data["total"], "275.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 6)
        self.assertEqual(Invoice.objects.get(sale_id=sale_response.data["id"]).status, "partially_paid")
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(Decimal(dashboard.data["total_revenue"]), Decimal("550.00"))

        self.client.force_authenticate(self.user)
        final_request = self.client.post(
            "/api/v1/refunds/",
            {**request_body, "items": [{"sale_item": sale_item.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(final_request.status_code, 201, final_request.data)
        final_approval = self.client.post(f"/api/v1/refunds/{final_request.data['id']}/approve/")
        self.assertEqual(final_approval.status_code, 403)
        self.client.force_authenticate(self.manager)
        final_approval = self.client.post(f"/api/v1/refunds/{final_request.data['id']}/approve/")
        self.assertEqual(final_approval.status_code, 200, final_approval.data)
        self.assertEqual(final_approval.data["total"], "550.00")
        sale = Sale.objects.get(pk=sale_response.data["id"])
        self.assertEqual(sale.status, "refunded")
        self.assertEqual(sale.invoice.status, "void")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        dashboard = self.client.get("/api/v1/dashboard/")
        self.assertEqual(Decimal(dashboard.data["total_revenue"]), Decimal("0.00"))
        self.assertEqual(Refund.objects.filter(sale=sale, status="approved").count(), 2)

    def test_refund_requester_cannot_review_own_request_and_pending_refund_blocks_cancel(self):
        cashier_sale = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        sale_item = SaleItem.objects.get(sale_id=cashier_sale.data["id"])
        request_body = {
            "sale": cashier_sale.data["id"],
            "reason": "Review separation test",
            "items": [{"sale_item": sale_item.id, "quantity": 1}],
        }
        cashier_request = self.client.post("/api/v1/refunds/", request_body, format="json")
        self.assertEqual(cashier_request.status_code, 201, cashier_request.data)
        self.client.force_authenticate(self.manager)
        blocked_cancel = self.client.post(f"/api/v1/sales/{cashier_sale.data['id']}/cancel/")
        self.assertEqual(blocked_cancel.status_code, 400)
        rejected = self.client.post(f"/api/v1/refunds/{cashier_request.data['id']}/reject/")
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/sales/{cashier_sale.data['id']}/cancel/").status_code, 200)

        manager_sale = self.client.post(
            "/api/v1/sales/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        manager_item = SaleItem.objects.get(sale_id=manager_sale.data["id"])
        manager_request = self.client.post(
            "/api/v1/refunds/",
            {"sale": manager_sale.data["id"], "reason": "Self-review should be blocked", "items": [{"sale_item": manager_item.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(manager_request.status_code, 201, manager_request.data)
        self.assertEqual(self.client.post(f"/api/v1/refunds/{manager_request.data['id']}/approve/").status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/refunds/{manager_request.data['id']}/reject/").status_code, 403)

    def test_cash_register_shift_tracks_sales_movements_and_close_difference(self):
        opened = self.client.post("/api/v1/cash-register/", {"opening_float": "100.00"}, format="json")
        self.assertEqual(opened.status_code, 201, opened.data)
        shift_id = opened.data["id"]
        duplicate = self.client.post("/api/v1/cash-register/", {"opening_float": "0.00"}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        sale = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 1}]}, format="json")
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(Sale.objects.get(pk=sale.data["id"]).cash_register_shift_id, shift_id)
        self.assertEqual(self.client.post(f"/api/v1/cash-register/{shift_id}/movement/", {"kind": "cash_in", "amount": "50.00", "note": "Float top-up"}, format="json").status_code, 201)
        self.assertEqual(self.client.post(f"/api/v1/cash-register/{shift_id}/movement/", {"kind": "cash_out", "amount": "10.00", "note": "Petty cash"}, format="json").status_code, 201)
        closed = self.client.post(f"/api/v1/cash-register/{shift_id}/close/", {"counted_cash": "420.00"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data["expected_cash"], "415.00")
        self.assertEqual(closed.data["difference"], "5.00")
        self.assertEqual(CashRegisterMovement.objects.filter(shift_id=shift_id).count(), 2)
        blocked = self.client.post(f"/api/v1/cash-register/{shift_id}/movement/", {"kind": "cash_in", "amount": "1.00", "note": "Late"}, format="json")
        self.assertEqual(blocked.status_code, 400)

    @override_settings(POS_REQUIRE_OPEN_SHIFT=True)
    def test_open_shift_setting_enforces_shift_for_checkout(self):
        blocked = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 1}]}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.client.post("/api/v1/cash-register/", {"opening_float": "0.00"}, format="json")
        allowed = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 1}]}, format="json")
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_held_bills_are_resumable_and_cashier_scoped(self):
        response = self.client.post("/api/v1/held-bills/", {"customer": self.customer.id, "notes": "Table 4", "items": [{"product": self.product.id, "quantity": 2}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["items"][0]["quantity"], 2)
        self.assertEqual(self.client.get(f"/api/v1/held-bills/{response.data['id']}/resume/").status_code, 200)
        other = get_user_model().objects.create_user(username="other", password="other-password-123")
        other.groups.add(Group.objects.get(name="cashier"))
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(f"/api/v1/held-bills/{response.data['id']}/").status_code, 404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(f"/api/v1/held-bills/{response.data['id']}/").status_code, 204)
        self.assertFalse(HeldBill.objects.filter(pk=response.data["id"]).exists())

    def test_business_settings_control_tax_shift_requirement_and_stock_rules(self):
        initial = self.client.get("/api/v1/business-settings/")
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(initial.data["tax_rate"], "0.1000")
        denied = self.client.patch("/api/v1/business-settings/", {"tax_rate": "0.0800"}, format="json")
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.manager)
        updated = self.client.patch("/api/v1/business-settings/", {"business_name": "Cafe North", "currency_code": "usd", "tax_rate": "0.0800", "require_open_shift": True, "allow_negative_stock": True}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["currency_code"], "USD")
        self.assertEqual(updated.data["tax_rate"], "0.0800")
        self.assertEqual(self.client.get("/api/v1/dashboard/").data["tax_rate"], "0.0800")
        no_shift = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 1}]}, format="json")
        self.assertEqual(no_shift.status_code, 400)
        self.client.force_authenticate(self.user)
        self.client.post("/api/v1/cash-register/", {"opening_float": "0.00"}, format="json")
        sale = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 9}]}, format="json")
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(sale.data["tax_amount"], "180.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, -1)

    def test_notifications_are_user_scoped_and_support_read_actions(self):
        sale = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 7}]}, format="json")
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(Notification.objects.filter(recipient=self.manager).count(), 2)
        self.assertEqual(Notification.objects.filter(recipient=self.user).count(), 0)
        self.client.force_authenticate(self.manager)
        inbox = self.client.get("/api/v1/notifications/")
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(len(inbox.data), 2)
        unread = self.client.get("/api/v1/notifications/unread-count/")
        self.assertEqual(unread.data["count"], 2)
        first_id = inbox.data[0]["id"]
        self.assertEqual(self.client.post(f"/api/v1/notifications/{first_id}/read/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/notifications/unread-count/").data["count"], 1)
        marked = self.client.post("/api/v1/notifications/read-all/")
        self.assertEqual(marked.data["updated"], 1)
        self.assertEqual(self.client.get("/api/v1/notifications/unread-count/").data["count"], 0)

    def test_internal_messages_support_attachments_replies_archive_delete_and_private_inboxes(self):
        temp_media = TemporaryDirectory()
        self.addCleanup(temp_media.cleanup)
        with override_settings(MEDIA_ROOT=temp_media.name):
            attached = SimpleUploadedFile("staff-note.txt", b"Shift handover details", content_type="text/plain")
            sent = self.client.post("/api/v1/messages/", {"recipient": self.manager.id, "subject": "Shift handover", "body": "Please review this note.", "attachment": attached}, format="multipart")
            self.assertEqual(sent.status_code, 201, sent.data)
            self.assertEqual(sent.data["attachment_name"], "staff-note.txt")
            self.assertEqual(Notification.objects.filter(recipient=self.manager, kind="message").count(), 1)

            outsider = get_user_model().objects.create_user(username="outsider", password="outsider-password-123")
            outsider.groups.add(Group.objects.get(name="cashier"))
            self.client.force_authenticate(outsider)
            self.assertEqual(self.client.get(f"/api/v1/messages/{sent.data['id']}/").status_code, 404)

            self.client.force_authenticate(self.manager)
            inbox = self.client.get("/api/v1/messages/?folder=inbox")
            self.assertEqual(len(inbox.data), 1)
            self.assertEqual(self.client.get("/api/v1/messages/unread-count/").data["count"], 1)
            thread = self.client.get(f"/api/v1/messages/{sent.data['id']}/thread/")
            self.assertEqual(thread.status_code, 200, thread.data)
            self.assertEqual(PosMessage.objects.get(pk=sent.data["id"]).read_at is not None, True)
            self.assertEqual(self.client.get("/api/v1/messages/unread-count/").data["count"], 0)
            self.assertEqual(self.client.post(f"/api/v1/messages/{sent.data['id']}/mark-unread/").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/messages/unread-count/").data["count"], 1)
            self.client.force_authenticate(self.user)
            self.assertEqual(self.client.post(f"/api/v1/messages/{sent.data['id']}/mark-unread/").status_code, 403)
            self.client.force_authenticate(self.manager)
            download = self.client.get(f"/api/v1/messages/{sent.data['id']}/attachment/")
            self.assertEqual(download.status_code, 200)
            self.assertEqual(b"".join(download.streaming_content), b"Shift handover details")
            reply = self.client.post(f"/api/v1/messages/{sent.data['id']}/reply/", {"body": "Received, thank you."}, format="json")
            self.assertEqual(reply.status_code, 201, reply.data)
            self.assertEqual(reply.data["parent"], sent.data["id"])
            archived = self.client.post(f"/api/v1/messages/{sent.data['id']}/archive/")
            self.assertEqual(archived.status_code, 200)
            self.assertEqual(len(self.client.get("/api/v1/messages/?folder=archived").data), 1)

            self.client.force_authenticate(self.user)
            sender_inbox = self.client.get("/api/v1/messages/?folder=inbox")
            self.assertEqual(len(sender_inbox.data), 1)
            self.assertEqual(sender_inbox.data[0]["sender_name"], self.manager.username)
            self.assertEqual(self.client.delete(f"/api/v1/messages/{sent.data['id']}/").status_code, 204)
            self.assertEqual(self.client.get("/api/v1/messages/?folder=sent").data, [])

    def test_email_module_logs_missing_smtp_and_retries_with_saved_configuration(self):
        with override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL=""):
            config = self.client.get("/api/v1/emails/templates/")
            self.assertEqual(config.status_code, 200, config.data)
            self.assertFalse(config.data["smtp_configured"])
            self.assertEqual({item["code"] for item in config.data["templates"]}, {"receipt", "invoice", "order_confirmation", "low_stock"})
            failed = self.client.post("/api/v1/emails/", {"template": "manual", "recipient": "customer@example.test", "subject": "Store update", "body": "Please review the order."}, format="json")
            self.assertEqual(failed.status_code, 503)
            log = EmailLog.objects.get(pk=failed.data["email"]["id"])
            self.assertEqual(log.status, "failed")
            self.assertIn("SMTP is not configured", log.error_message)
        with override_settings(EMAIL_HOST="smtp.example.test", DEFAULT_FROM_EMAIL="pos@example.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            retried = self.client.post(f"/api/v1/emails/{log.pk}/retry/")
            self.assertEqual(retried.status_code, 200, retried.data)
            log.refresh_from_db()
            self.assertEqual(log.status, "sent")
            self.assertEqual(log.attempts, 2)
            self.assertEqual(len(mail.outbox), 1)

    def test_receipt_email_template_uses_invoice_and_customer_data(self):
        self.customer.email = "riley@example.test"
        self.customer.save(update_fields=["email"])
        sale_response = self.client.post("/api/v1/sales/", {"customer": self.customer.id, "items": [{"product": self.product.id, "quantity": 2}]}, format="json")
        invoice = Invoice.objects.get(sale_id=sale_response.data["id"])
        with override_settings(EMAIL_HOST="smtp.example.test", DEFAULT_FROM_EMAIL="pos@example.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            sent = self.client.post("/api/v1/emails/", {"template": "receipt", "recipient": self.customer.email, "invoice": invoice.id}, format="json")
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(sent.data["email"]["invoice_number"], invoice.invoice_number)
        self.assertIn(invoice.invoice_number, mail.outbox[-1].subject)
        self.assertIn("Coffee", mail.outbox[-1].body)
        self.assertIn("550.00", mail.outbox[-1].body)

    def test_scheduled_product_deals_are_validated_and_applied_at_checkout(self):
        now = timezone.now()
        self.client.force_authenticate(self.manager)
        bad_price = self.client.post("/api/v1/deals/", {"name": "Invalid price", "product": self.product.id, "deal_price": "250.00", "starts_at": (now - timedelta(minutes=1)).isoformat(), "ends_at": (now + timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(bad_price.status_code, 400)
        invalid_schedule = self.client.post("/api/v1/deals/", {"name": "Invalid schedule", "product": self.product.id, "deal_price": "200.00", "starts_at": (now + timedelta(days=2)).isoformat(), "ends_at": (now + timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(invalid_schedule.status_code, 400)
        deal = self.client.post("/api/v1/deals/", {"name": "Coffee special", "product": self.product.id, "deal_price": "200.00", "starts_at": (now - timedelta(minutes=1)).isoformat(), "ends_at": (now + timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(deal.status_code, 201, deal.data)
        self.client.force_authenticate(self.user)
        sale = self.client.post("/api/v1/sales/", {"items": [{"product": self.product.id, "quantity": 2}]}, format="json")
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(sale.data["subtotal"], "400.00")
        self.assertEqual(sale.data["tax_amount"], "40.00")
        self.assertEqual(sale.data["items"][0]["unit_price"], "200.00")
        self.assertEqual(sale.data["items"][0]["deal_name"], "Coffee special")
        self.assertEqual(ProductDeal.objects.get(pk=deal.data["id"]).sale_items.count(), 1)

