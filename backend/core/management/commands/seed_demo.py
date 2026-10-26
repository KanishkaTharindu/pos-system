import os
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from core.models import Brand, Category, Product, SubCategory


PRODUCTS = [
    ("RICE-001", "Basmati Rice 1kg", "Pantry", "Grains", "Harvest", "850.00", "640.00", 38, 8),
    ("TEA-001", "Ceylon Black Tea 100g", "Beverages", "Tea", "Highland", "620.00", "410.00", 24, 6),
    ("COFFEE-001", "Ground Coffee 250g", "Beverages", "Coffee", "Highland", "1450.00", "1050.00", 15, 4),
    ("OIL-001", "Coconut Oil 750ml", "Pantry", "Cooking oils", "Island Pantry", "980.00", "720.00", 18, 5),
    ("MILK-001", "Fresh Milk 1L", "Dairy", "Fresh milk", "Daily Farm", "520.00", "390.00", 14, 6),
    ("BREAD-001", "Wholegrain Bread", "Bakery", "Bread", "Daily Farm", "480.00", "320.00", 11, 4),
    ("SOAP-001", "Herbal Hand Soap", "Household", "Personal care", "Green Leaf", "390.00", "220.00", 27, 7),
    ("LENTIL-001", "Red Lentils 500g", "Pantry", "Grains", "Harvest", "560.00", "390.00", 21, 6),
    ("JUICE-001", "Mango Juice 1L", "Beverages", "Juice", "Island Pantry", "690.00", "455.00", 3, 5),
]

DEMO_USERS = [
    ("pos_admin", "POS_SEED_ADMIN_PASSWORD", "admin"),
    ("manager", "POS_SEED_MANAGER_PASSWORD", "manager"),
    ("cashier", "POS_SEED_CASHIER_PASSWORD", "cashier"),
]


class Command(BaseCommand):
    help = "Create development-only sample catalog records and optional role accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo seeding is disabled unless DJANGO_DEBUG=True.")

        roles = {name: Group.objects.get_or_create(name=name)[0] for name in ("admin", "manager", "cashier")}
        for name, password_variable, role_name in DEMO_USERS:
            password = os.environ.get(password_variable)
            if not password:
                continue
            user, created = get_user_model().objects.get_or_create(username=name, defaults={"is_staff": role_name == "admin"})
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            user.groups.add(roles[role_name])

        for sku, name, category_name, subcategory_name, brand_name, price, cost, stock, reorder in PRODUCTS:
            category, _ = Category.objects.get_or_create(
                name=category_name,
                defaults={"slug": slugify(category_name)},
            )
            subcategory, _ = SubCategory.objects.get_or_create(
                category=category,
                name=subcategory_name,
                defaults={"slug": slugify(subcategory_name)},
            )
            brand, _ = Brand.objects.get_or_create(name=brand_name)
            Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "category": category.name,
                    "category_ref": category,
                    "subcategory": subcategory,
                    "brand": brand,
                    "description": "",
                    "barcode": "",
                    "selling_price": Decimal(price),
                    "cost_price": Decimal(cost),
                    "stock_quantity": stock,
                    "reorder_level": reorder,
                    "is_active": True,
                },
            )

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(PRODUCTS)} development catalog products."))
        if any(os.environ.get(env_name) for _, env_name, _ in DEMO_USERS):
            self.stdout.write(self.style.SUCCESS("Configured role accounts from supplied environment passwords."))
        else:
            self.stdout.write("No demo accounts created; set POS_SEED_*_PASSWORD variables to create them.")
