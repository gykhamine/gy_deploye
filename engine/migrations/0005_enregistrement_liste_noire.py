from django.db import migrations, models


class Migration(migrations.Migration):
    """Liste noire de lignes : écartées de toutes les analyses sans être supprimées."""

    dependencies = [("engine", "0004_tabledynamique_jeton_a_reconfigurer")]

    operations = [
        migrations.AddField(
            model_name="enregistrement",
            name="liste_noire",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
