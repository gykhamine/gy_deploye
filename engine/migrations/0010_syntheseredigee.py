import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Synthèses rédigées : analyse écrite à la main sur une table (blocs de texte + cartes, tableaux et graphiques déjà calculés), même cycle que les
    documents administratifs (brouillon -> finalisée, PDF figé avec son empreinte SHA-256)."""

    dependencies = [("engine", "0009_entreprise_plafonds_tables")]

    operations = [
        migrations.CreateModel(
            name="SyntheseRedigee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titre", models.CharField(blank=True, default="", max_length=200)),
                ("corps", models.JSONField(blank=True, default=list)),
                ("statut", models.CharField(choices=[("brouillon", "Brouillon"), ("finalisee", "Finalisée")], db_index=True, default="brouillon", max_length=10)),
                ("finalisee_le", models.DateTimeField(blank=True, null=True)),
                ("pdf_fige", models.FileField(blank=True, null=True, upload_to="documents/syntheses/%Y/")),
                ("empreinte_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("archive", models.BooleanField(db_index=True, default=False)),
                ("archive_le", models.DateTimeField(blank=True, null=True)),
                ("cree_le", models.DateTimeField(auto_now_add=True)),
                ("modifie_le", models.DateTimeField(auto_now=True)),
                ("auteur", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="syntheses_redigees", to="engine.membreentreprise")),
                ("entreprise", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="syntheses_redigees", to="engine.entreprise")),
                ("finalisee_par", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="syntheses_finalisees", to="engine.membreentreprise")),
                ("modele_entete", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="syntheses", to="engine.modeleentete")),
                ("table", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="syntheses_redigees", to="engine.tabledynamique")),
            ],
            options={
                "ordering": ["-modifie_le", "-id"],
            },
        ),
    ]
