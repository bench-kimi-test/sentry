import django.db.models.deletion
import sentry.db.models.fields.foreignkey
from django.db import migrations

from sentry.new_migrations.migrations import CheckedMigration
from sentry.new_migrations.monkey.models import SafeDeleteModel
from sentry.new_migrations.monkey.state import DeletionAction


class Migration(CheckedMigration):
    is_post_deployment = False

    dependencies = [
        ("sentry", "1080_backfill_deprecated_dashboard_widget_display_types"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dashboardlastvisited",
            name="dashboard",
            field=sentry.db.models.fields.foreignkey.FlexibleForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="sentry.dashboard",
            ),
        ),
        migrations.AlterField(
            model_name="dashboardlastvisited",
            name="member",
            field=sentry.db.models.fields.foreignkey.FlexibleForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="sentry.organizationmember",
            ),
        ),
        SafeDeleteModel(
            name="DashboardLastVisited",
            deletion_action=DeletionAction.MOVE_TO_PENDING,
        ),
    ]
