from django.db import migrations, models


class Migration(migrations.Migration):
    """« Online Sheet sécurisé » : jeton d'accès (chiffré) mémorisé avec le lien d'origine, pour que ↻ et la relecture automatique fonctionnent."""

    dependencies = [("engine", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="tabledynamique",
            name="jeton_source",
            field=models.TextField(blank=True, default=""),
        ),
    ]
