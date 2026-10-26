from django.db import migrations
from django.utils.text import slugify


def link_legacy_categories(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    Product = apps.get_model("core", "Product")
    database = schema_editor.connection.alias

    names = Product.objects.using(database).values_list("category", flat=True).distinct()
    for name in names:
        normalized = (name or "General").strip() or "General"
        category = Category.objects.using(database).filter(name=normalized).first()
        if category is None:
            base_slug = slugify(normalized) or "general"
            slug = base_slug
            suffix = 2
            while Category.objects.using(database).filter(slug=slug).exists():
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            category = Category.objects.using(database).create(name=normalized, slug=slug)
        Product.objects.using(database).filter(category=normalized, category_ref__isnull=True).update(category_ref_id=category.pk)


class Migration(migrations.Migration):
    dependencies = [("core", "0005_pos_roles")]
    operations = [migrations.RunPython(link_legacy_categories, migrations.RunPython.noop)]
