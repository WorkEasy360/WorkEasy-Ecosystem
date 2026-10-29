# ADR-051: the Product Manager. Additive only - eight columns on the product
# (every one with a default, so existing rows keep their meaning: the four
# channel flags default to True, which is where products sold before them)
# and one new table for store-listing images.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_product_category_icon_trial_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='store_title',
            field=models.CharField(blank=True, db_default='', default='', max_length=128),
        ),
        migrations.AddField(
            model_name='product',
            name='store_summary',
            field=models.CharField(blank=True, db_default='', default='', max_length=200),
        ),
        migrations.AddField(
            model_name='product',
            name='application_url',
            field=models.URLField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='product',
            name='documentation_url',
            field=models.URLField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='product',
            name='support_url',
            field=models.URLField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='product',
            name='customer_store_enabled',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='reseller_enabled',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='affiliate_enabled',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='white_label_enabled',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.CreateModel(
            name='ProductMedia',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('logo', 'Logo'), ('banner', 'Banner'), ('screenshot', 'Screenshot')], max_length=16)),
                ('file_id', models.UUIDField()),
                ('width', models.PositiveIntegerField()),
                ('height', models.PositiveIntegerField()),
                ('content_type', models.CharField(max_length=32)),
                ('size_bytes', models.PositiveIntegerField()),
                ('caption', models.CharField(blank=True, default='', max_length=140)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('uploaded_by_id', models.UUIDField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media', to='catalog.product')),
            ],
            options={
                'db_table': 'catalog_product_media',
                'ordering': ['product', 'kind', 'sort_order', 'created_at'],
                'indexes': [models.Index(fields=['product', 'kind'], name='ix_productmedia_kind')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('kind', 'screenshot'), _negated=True), fields=('product', 'kind'), name='uq_product_media_single_kind')],
            },
        ),
    ]
