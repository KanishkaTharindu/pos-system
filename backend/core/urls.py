from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomerViewSet, ProductViewSet, SaleViewSet, dashboard_summary, health_live, health_ready

router = DefaultRouter()
router.register(r"products", ProductViewSet, basename="products")
router.register(r"customers", CustomerViewSet, basename="customers")
router.register(r"sales", SaleViewSet, basename="sales")

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", dashboard_summary, name="dashboard_summary"),
    path("health/live/", health_live, name="health_live"),
    path("health/ready/", health_ready, name="health_ready"),
]
