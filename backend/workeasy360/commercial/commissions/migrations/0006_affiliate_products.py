"""ADR-054: product-aware affiliate links, the programme policy, the trial
step and the paid transaction behind each earning.

Additive: new columns carry database defaults (the ledger is append-only, so
no existing row is rewritten - ``ADD COLUMN ... DEFAULT`` is metadata). Two
data steps run before the new check constraints:

- every existing link that names a product becomes a ``product`` link (it
  already opened the product page - nothing a visitor sees changes);
- every partner that holds an active product link is given the promotion
  grant for that product, so links made before ADR-054 keep working under the
  default ``stop`` policy. No grant is invented for a product a partner never
  linked to.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def product_links_and_their_grants(apps, schema_editor):
    from django.utils import timezone

    ReferralCode = apps.get_model("commissions", "ReferralCode")
    Product = apps.get_model("catalog", "Product")
    Grant = apps.get_model("partners", "PartnerPromotionGrant")

    ReferralCode.objects.exclude(product_code="").update(link_kind="product")
    products = dict(Product.objects.values_list("code", "id"))
    seen = set()
    for code in (
        ReferralCode.objects.filter(status="active")
        .exclude(product_code="")
        .order_by("created_at")
    ):
        key = (code.partner_id, code.product_code)
        if key in seen or code.product_code not in products:
            continue
        seen.add(key)
        Grant.objects.create(
            partner_id=code.partner_id,
            product_id=products[code.product_code],
            status="active",
            granted_by_id=code.created_by_id,
            granted_at=timezone.now(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_product_updates'),
        ('commissions', '0005_ecosystem_margins_campaigns'),
        ('partners', '0011_promotion_grants'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReferralPolicy',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ('attribution_days', models.PositiveSmallIntegerField()),
                ('revoked_links', models.CharField(choices=[('stop', 'Stop'), ('continue', 'Continue')], default='stop', max_length=16)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'commissions_referral_policy',
            },
        ),
        migrations.AddField(
            model_name='commissionaccrual',
            name='payment_ref',
            field=models.CharField(blank=True, db_default='', default='', max_length=80),
        ),
        migrations.AddField(
            model_name='referral',
            name='trial_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='referralcode',
            name='link_kind',
            field=models.CharField(choices=[('general', 'General'), ('product', 'Product'), ('pricing', 'Pricing'), ('feature', 'Feature'), ('update', 'Update')], db_default='general', default='general', max_length=16),
        ),
        migrations.AddField(
            model_name='referralcode',
            name='update',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='catalog.productupdate'),
        ),
        migrations.RunPython(product_links_and_their_grants, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='referralcode',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('link_kind', 'general'), ('product_code', '')), models.Q(models.Q(('link_kind', 'general'), _negated=True), models.Q(('product_code', ''), _negated=True)), _connector='OR'), name='ck_refcode_kind_matches_product'),
        ),
        migrations.AddConstraint(
            model_name='referralcode',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('link_kind__in', ['feature', 'update']), ('update__isnull', False)), models.Q(models.Q(('link_kind__in', ['feature', 'update']), _negated=True), ('update__isnull', True)), _connector='OR'), name='ck_refcode_kind_matches_update'),
        ),
        migrations.AddField(
            model_name='referralpolicy',
            name='updated_by',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='referralpolicy',
            constraint=models.CheckConstraint(condition=models.Q(('id', 1)), name='ck_referral_policy_singleton'),
        ),
        migrations.AddConstraint(
            model_name='referralpolicy',
            constraint=models.CheckConstraint(condition=models.Q(('attribution_days__gte', 1), ('attribution_days__lte', 365)), name='ck_referral_policy_window'),
        ),
    ]
