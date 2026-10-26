from django.conf import settings
from django.db import migrations


def assign_unconfigured_users(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    cashier, _ = Group.objects.using(schema_editor.connection.alias).get_or_create(name="cashier")
    ungrouped_users = User.objects.using(schema_editor.connection.alias).filter(
        is_superuser=False,
        groups__isnull=True,
    ).distinct()
    for user in ungrouped_users.iterator():
        user.groups.add(cashier)


class Migration(migrations.Migration):
    dependencies = [("core", "0006_backfill_product_categories")]
    operations = [migrations.RunPython(assign_unconfigured_users, migrations.RunPython.noop)]
