from django.contrib import admin

from .models import (
    ArchiveCycle, ChampDynamique, CleAPI, ConnexionExterne, CoucheOntologie, CouleurOntologie, DemandeAgent, Entreprise, Enregistrement, FormuleCalcul,
    ConteneurTables, GroupeTables, JournalAudit, LigneCalcul, MembreEntreprise, RapportGenere,
    SignatureDocument, TableCalcul, TableDynamique, ValeurChamp, Bloc,
    CellulePartagee, GroupePartage, GroupePartageCellule, GroupePartageLigne, LignePartagee,)

admin.site.register(Entreprise)
admin.site.register(ArchiveCycle)
admin.site.register(MembreEntreprise)
admin.site.register(TableDynamique)
admin.site.register(ChampDynamique)
admin.site.register(Enregistrement)
admin.site.register(ValeurChamp)
admin.site.register(DemandeAgent)
admin.site.register(RapportGenere)
admin.site.register(JournalAudit)
admin.site.register(SignatureDocument)


@admin.register(CleAPI)
class CleAPIAdmin(admin.ModelAdmin):
    list_display = ("nom", "prefixe", "entreprise", "membre", "actif", "cree_le", "derniere_utilisation")
    list_filter = ("actif", "entreprise")
    readonly_fields = ("prefixe", "cle_hachee", "cree_le", "cree_par")


@admin.register(GroupeTables)
class GroupeTablesAdmin(admin.ModelAdmin):
    list_display = ("nom", "entreprise", "nb_tables", "nb_lignes_total", "cree_automatiquement", "cree_le")
    list_filter = ("entreprise", "cree_automatiquement")
    search_fields = ("nom",)


@admin.register(ConteneurTables)
class ConteneurTablesAdmin(admin.ModelAdmin):
    list_display = ("nom", "entreprise", "cree_le")
    list_filter = ("entreprise",)
    search_fields = ("nom",)


@admin.register(ConnexionExterne)
class ConnexionExterneAdmin(admin.ModelAdmin):
    list_display = ("nom", "entreprise", "moteur", "hote", "port", "nom_base", "actif", "derniere_synchro_statut", "derniere_synchro_le")
    list_filter = ("entreprise", "moteur", "actif")
    search_fields = ("nom", "hote", "nom_base")
    # Le mot de passe ne doit jamais s'afficher en clair dans la liste
    # d'admin ; il reste éditable via le formulaire de détail uniquement.
    exclude = ()


@admin.register(Bloc)
class BlocAdmin(admin.ModelAdmin):
    list_display = ("entreprise", "index", "hash", "previous_hash", "cree_le")
    list_filter = ("entreprise",)
    readonly_fields = [f.name for f in Bloc._meta.fields]
admin.site.register(FormuleCalcul)
admin.site.register(TableCalcul)
admin.site.register(LigneCalcul)
admin.site.register(CoucheOntologie)
admin.site.register(CouleurOntologie)


# ── Fonctions fusionnées depuis gscode ──

class LignePartageeInline(admin.TabularInline):
    model = LignePartagee
    extra = 0
    autocomplete_fields = []
    raw_id_fields = ["enregistrement"]



class CellulePartageeInline(admin.TabularInline):
    model = CellulePartagee
    extra = 0
    raw_id_fields = ["enregistrement", "champ"]


@admin.register(GroupePartage)

class GroupePartageAdmin(admin.ModelAdmin):
    """Partage de COLONNE (un même champ synchronisé, ligne cible auto)."""
    list_display = ("id", "nom", "entreprise", "cree_le")
    list_filter = ("entreprise",)
    search_fields = ("nom",)


@admin.register(GroupePartageLigne)

class GroupePartageLigneAdmin(admin.ModelAdmin):
    """Partage de LIGNE (des lignes précises reliées, colonnes synchronisées par slug)."""
    list_display = ("id", "nom", "entreprise", "cree_le")
    list_filter = ("entreprise",)
    search_fields = ("nom",)
    inlines = [LignePartageeInline]


@admin.register(GroupePartageCellule)

class GroupePartageCelluleAdmin(admin.ModelAdmin):
    """Partage de CELLULE (des cellules précises reliées, sans contrainte de slug)."""
    list_display = ("id", "nom", "entreprise", "cree_le")
    list_filter = ("entreprise",)
    search_fields = ("nom",)
    inlines = [CellulePartageeInline]


from .models import EvaluationSUS, MesureUsage  # test d'utilisabilité (engine/usabilite.py)
admin.site.register(EvaluationSUS)
admin.site.register(MesureUsage)
