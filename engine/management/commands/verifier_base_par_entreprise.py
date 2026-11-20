"""Contrôle, AVANT le passage en « un fichier par entreprise », ce qui peut casser.

    python manage.py verifier_base_par_entreprise

1. Liste les modèles d'entreprise (fichier de l'entreprise) et les modèles système.
2. Signale les liens d'un modèle d'entreprise vers un modèle SYSTÈME (hors entreprise, membres, comptes copiés dans chaque
   fichier) : une jointure (select_related, filtre « lien__champ ») sur ces liens échouerait.
3. Cherche dans le code ces jointures précises (fichier:ligne) pour les corriger avant la mise en service.
Code de sortie ≠ 0 si une jointure risquée est trouvée.
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import ForeignKey, OneToOneField

from engine import base_par_entreprise as bpe
from engine.management.commands._textes import m

COPIES_OK = set(bpe.COPIES)


class Command(BaseCommand):
    @property
    def help(self):
        return m("commandes.verifier.help")

    def handle(self, *args, **o):
        loc = bpe.modeles_locataires()
        loc_set = set(loc)
        user = settings.AUTH_USER_MODEL.lower()
        self.stdout.write(m("commandes.verifier.nb_modeles", n=len(loc)))
        risques = {}   # nom du champ -> [(modèle, cible)]
        for m in loc:
            for f in m._meta.get_fields():
                if isinstance(f, (ForeignKey, OneToOneField)) and f.concrete and f.related_model is not None:
                    cible = f.related_model
                    if cible in loc_set or cible.__name__ in COPIES_OK or cible._meta.label_lower == user:
                        continue
                    risques.setdefault(f.name, []).append((m.__name__, cible.__name__))
        if not risques:
            self.stdout.write(self.style.SUCCESS(m("commandes.verifier.aucun_lien")))
            return
        self.stdout.write(m("commandes.verifier.liens_titre"))
        for nom, liste in sorted(risques.items()):
            self.stdout.write(f"  {nom}: " + ", ".join(f"{a}→{b}" for a, b in liste))
        motifs = [re.compile(r"select_related\([^)]*['\"]%s(?:__[a-z_]+)?['\"]" % re.escape(n)) for n in risques]
        motifs += [re.compile(r"['\"]%s__[a-z_]+['\"]" % re.escape(n)) for n in risques]
        trouves = []
        for fichier in sorted(Path(settings.BASE_DIR, "engine").rglob("*.py")):
            if fichier.name.startswith("tests"):
                continue
            for i, ligne in enumerate(fichier.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if any(p.search(ligne) for p in motifs):
                    trouves.append(f"{fichier.relative_to(settings.BASE_DIR)}:{i}: {ligne.strip()[:110]}")
        if trouves:
            self.stdout.write(m("commandes.verifier.nb_jointures", n=len(trouves)))
            for t in trouves:
                self.stdout.write("  " + t)
            raise CommandError(m("commandes.verifier.jointures_erreur"))
        self.stdout.write(self.style.SUCCESS(m("commandes.verifier.aucune_jointure")))
