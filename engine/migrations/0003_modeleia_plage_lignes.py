from django.db import migrations, models


class Migration(migrations.Migration):
    """Plage de lignes d'entraînement d'un modèle IA (ex. lignes 10 001 à 20 000 d'une table de 80 000)."""

    dependencies = [("engine", "0002_tabledynamique_jeton_source")]

    operations = [
        migrations.AddField(
            model_name="modeleia",
            name="plage_lignes",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
