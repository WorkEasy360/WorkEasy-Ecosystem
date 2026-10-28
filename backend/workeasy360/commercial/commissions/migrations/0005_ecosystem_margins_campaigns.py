"""ADR-053: margins, campaigns, typed referral links and the referral funnel.

Additive only: new columns carry database defaults (the ledger is append-only,
so no existing row is rewritten - ``ADD COLUMN ... DEFAULT`` is metadata), and
two new tables. A margin rule is a version, frozen like a commission rule:
only ``retired_at`` may move.
"""

import django.db.models.deletion
import django.db.models.functions.text
import uuid
from django.conf import settings
from django.db import migrations, models

from workeasy360.platform.tenancy.migrations_helpers import freeze_columns


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0004_rule_recurring'),
        ('partners', '0010_program_fee'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='commissionaccrual',
            name='cost_minor',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionaccrual',
            name='earning',
            field=models.CharField(blank=True, db_default='commission', default='commission', max_length=16),
        ),
        migrations.AddField(
            model_name='commissionaccrual',
            name='plan_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='commissionaccrual',
            name='product_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
        migrations.AddField(
            model_name='referral',
            name='registered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='referral',
            name='visitor_hash',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='referralcode',
            name='plan_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='referralcode',
            name='product_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
        migrations.CreateModel(
            name='MarginRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.SlugField(max_length=64)),
                ('version', models.PositiveIntegerField()),
                ('experience', models.CharField(blank=True, default='', max_length=16)),
                ('product_code', models.CharField(blank=True, default='', max_length=32)),
                ('discount_bp', models.PositiveIntegerField()),
                ('recurring', models.BooleanField(default=True)),
                ('effective_from', models.DateTimeField()),
                ('retired_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='margin_rules', to='partners.partneraccount')),
            ],
            options={
                'db_table': 'commissions_margin_rule',
            },
        ),
        migrations.AddField(
            model_name='commissionaccrual',
            name='margin_rule',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='accruals', to='commissions.marginrule'),
        ),
        migrations.CreateModel(
            name='ReferralCampaign',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('active', 'Active'), ('revoked', 'Revoked')], default='active', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='campaigns', to='partners.partneraccount')),
            ],
            options={
                'db_table': 'commissions_referral_campaign',
            },
        ),
        migrations.AddField(
            model_name='referralcode',
            name='campaign',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='codes', to='commissions.referralcampaign'),
        ),
        migrations.AddIndex(
            model_name='marginrule',
            index=models.Index(fields=['partner', 'product_code', 'effective_from'], name='ix_margin_selection'),
        ),
        migrations.AddConstraint(
            model_name='marginrule',
            constraint=models.UniqueConstraint(fields=('code', 'version'), name='uq_margin_code_version'),
        ),
        migrations.AddConstraint(
            model_name='marginrule',
            constraint=models.UniqueConstraint(condition=models.Q(('retired_at__isnull', True)), fields=('code',), name='uq_margin_one_live_version'),
        ),
        migrations.AddConstraint(
            model_name='marginrule',
            constraint=models.CheckConstraint(condition=models.Q(('discount_bp__lte', 10000)), name='ck_margin_discount_at_most_100pct'),
        ),
        migrations.AddConstraint(
            model_name='marginrule',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('experience__in', ['reseller', 'white_label']), ('partner__isnull', True)), models.Q(('experience', ''), ('partner__isnull', False)), _connector='OR'), name='ck_margin_default_or_override'),
        ),
        migrations.AddConstraint(
            model_name='referralcampaign',
            constraint=models.UniqueConstraint(models.F('partner'), django.db.models.functions.text.Lower('name'), name='uq_campaign_partner_name'),
        ),
        freeze_columns(
            "commissions_margin_rule",
            columns=[
                "code",
                "version",
                "experience",
                "partner_id",
                "product_code",
                "discount_bp",
                "recurring",
                "effective_from",
                "reason",
                "created_by_id",
                "created_at",
            ],
        ),
    ]
