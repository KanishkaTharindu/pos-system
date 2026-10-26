from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BrandViewSet, BusinessSettingsView, CashRegisterViewSet, CategoryViewSet, CustomerViewSet, EmailViewSet, FinancialTransactionViewSet, GlobalSearchView, HeldBillViewSet, InvoiceViewSet, MyPasswordChangeView, MyPreferencesView, MyProfileView, NotificationViewSet, PosMessageViewSet, ProductDealViewSet, ProductViewSet, RefundViewSet, SaleViewSet, SubCategoryViewSet, dashboard_summary, health_live, health_ready

router = DefaultRouter()
router.register(r"products", ProductViewSet, basename="products")
router.register(r"deals", ProductDealViewSet, basename="deals")
router.register(r"categories", CategoryViewSet, basename="categories")
router.register(r"subcategories", SubCategoryViewSet, basename="subcategories")
router.register(r"brands", BrandViewSet, basename="brands")
router.register(r"invoices", InvoiceViewSet, basename="invoices")
router.register(r"transactions", FinancialTransactionViewSet, basename="transactions")
router.register(r"refunds", RefundViewSet, basename="refunds")
router.register(r"customers", CustomerViewSet, basename="customers")
router.register(r"sales", SaleViewSet, basename="sales")
router.register(r"cash-register", CashRegisterViewSet, basename="cash-register")
router.register(r"held-bills", HeldBillViewSet, basename="held-bills")
router.register(r"notifications", NotificationViewSet, basename="notifications")
router.register(r"messages", PosMessageViewSet, basename="messages")
router.register(r"emails", EmailViewSet, basename="emails")

urlpatterns = [
    path("search/", GlobalSearchView.as_view(), name="global_search"),
    path("", include(router.urls)),
    path("dashboard/", dashboard_summary, name="dashboard_summary"),
    path("preferences/me/", MyPreferencesView.as_view(), name="my_preferences"),
    path("profile/me/", MyProfileView.as_view(), name="my_profile"),
    path("profile/change-password/", MyPasswordChangeView.as_view(), name="profile_change_password"),
    path("business-settings/", BusinessSettingsView.as_view(), name="business_settings"),
    path("health/live/", health_live, name="health_live"),
    path("health/ready/", health_ready, name="health_ready"),
]
