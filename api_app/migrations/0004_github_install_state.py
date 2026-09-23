from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api_app", "0003_auditlog_githubconnection_projecttoken_waiver_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubInstallState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "state_hash",
                    models.CharField(
                        db_index=True,
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                (
                    "consumed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="github_install_states",
                        to="api_app.project",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="githubinstallstate",
            index=models.Index(
                fields=["project", "expires_at"],
                name="api_app_git_project_5f77cf_idx",
            ),
        ),
        migrations.AlterField(
            model_name="githubconnection",
            name="repository_full_name",
            field=models.CharField(blank=True, max_length=300),
        ),
    ]
