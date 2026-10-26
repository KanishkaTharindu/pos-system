from django.conf import settings

from .models import BusinessSettings


def get_business_settings():
    """Return the singleton settings row, seeded from environment defaults once."""
    record, _ = BusinessSettings.objects.get_or_create(
        pk=1,
        defaults={
            "tax_rate": settings.POS_TAX_RATE,
            "require_open_shift": settings.POS_REQUIRE_OPEN_SHIFT,
        },
    )
    return record
