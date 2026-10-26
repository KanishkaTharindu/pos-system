from django.db import migrations


def create_pos_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ("admin", "manager", "cashier"):
        Group.objects.get_or_create(name=name)


def remove_pos_roles(apps, schema_editor):
    # Role groups may already be assigned to users; preserve that user data on rollback.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0004_userpreference"),
    ]

    operations = [migrations.RunPython(create_pos_roles, remove_pos_roles)]
