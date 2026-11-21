from django.db import migrations, models


class Migration(migrations.Migration):
    """Plafonds absolus de lignes (100 000) et de colonnes (1 000) d'une table, par entreprise, réglés par le super-administrateur."""

    dependencies = [("engine", "0008_entreprise_max_connexions")]

    operations = [
        migrations.AddField(model_name="entreprise", name="plafond_lignes_max", field=models.PositiveIntegerField(default=100000)),
        migrations.AddField(model_name="entreprise", name="plafond_colonnes_max", field=models.PositiveIntegerField(default=1000)),
    ]
