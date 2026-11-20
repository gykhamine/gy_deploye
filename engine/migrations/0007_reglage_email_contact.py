from django.db import migrations, models


class Migration(migrations.Migration):
    """Contact e-mail configurable (en plus de WhatsApp) pour le pop-up « fonctionnalité bloquée »."""

    dependencies = [("engine", "0006_essai_blocage_entreprise")]

    operations = [
        migrations.AddField(model_name="reglageplateforme", name="email_contact", field=models.CharField(blank=True, default="", max_length=254)),
    ]
