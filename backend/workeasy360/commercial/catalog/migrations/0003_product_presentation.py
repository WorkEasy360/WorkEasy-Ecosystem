"""Phase 9: how a product is presented on the public site.

``slug`` is added nullable, back-filled deterministically from ``code``
(``whatsapp_marketing`` -> ``whatsapp-marketing``; a collision - impossible for
today's codes, which are unique and hyphen-free - gets a numeric suffix), then
made unique and required. ``listing`` defaults to ``listed``, so every existing
product keeps exactly the visibility it had. Additive; no existing column
changes. Reversible (the reverse drops the new columns).
"""

from __future__ import annotations

from django.db import migrations, models


def backfill_slugs(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    taken: set[str] = set()
    for product in Product.objects.order_by("created_at", "id"):
        base = "-".join(
            part for part in product.code.lower().replace("_", "-").split("-") if part
        )
        slug, n = base, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        Product.objects.filter(pk=product.pk).update(slug=slug)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_plan_builder")]

    operations = [
        # A plain column first: SlugField indexes itself, and the unique
        # SlugField below would then try to create the same pattern index.
        migrations.AddField(
            model_name="product",
            name="slug",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(backfill_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="slug",
            field=models.SlugField(max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name="product",
            name="tagline",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="product",
            name="marketing_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="product",
            name="listing",
            field=models.CharField(
                choices=[
                    ("listed", "Listed"),
                    ("coming_soon", "Coming Soon"),
                    ("hidden", "Hidden"),
                ],
                default="listed",
                max_length=16,
            ),
        ),
    ]
