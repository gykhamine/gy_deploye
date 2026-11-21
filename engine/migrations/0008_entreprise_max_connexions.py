from django.db import migrations, models


class Migration(migrations.Migration):
    """Plafond de personnes inscrites (agents + clients) par entreprise, 5 par défaut, relevé par le super-administrateur."""

    dependencies = [("engine", "0007_reglage_email_contact")]

    operations = [
        migrations.AddField(model_name="entreprise", name="max_connexions", field=models.PositiveIntegerField(default=5)),
    ]
