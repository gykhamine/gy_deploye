from django.db import migrations, models


class Migration(migrations.Migration):
    """Essai d'une semaine, blocage à l'échéance et réglages de contact WhatsApp (super-administrateur)."""

    dependencies = [("engine", "0005_enregistrement_liste_noire")]

    operations = [
        migrations.AddField(model_name="entreprise", name="acces_jusqua", field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name="entreprise", name="bloquee", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="entreprise", name="en_essai", field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name="ReglagePlateforme",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("whatsapp_numero", models.CharField(blank=True, default="", max_length=30)),
                ("whatsapp_message", models.TextField(blank=True, default="")),
                ("essai_jours", models.PositiveIntegerField(default=7)),
                ("prolongation_jours", models.PositiveIntegerField(default=30)),
            ],
        ),
    ]
