from django.db import migrations, models


class Migration(migrations.Migration):
    """« Online Sheet sécurisé » : drapeau « jeton à reconfigurer » (le serveur distant ne reconnaît plus le jeton mémorisé)."""

    dependencies = [("engine", "0003_modeleia_plage_lignes")]

    operations = [
        migrations.AddField(
            model_name="tabledynamique",
            name="jeton_a_reconfigurer",
            field=models.BooleanField(default=False),
        ),
    ]
