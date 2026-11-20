"""Vérifie que le fichier Parquet de chaque table dynamique (EAV) correspond à l'EAV, qui reste la source de vérité absolue ; sinon le
reconstruit depuis l'EAV.

    python manage.py verifier_parquet                 # un passage sur toutes les tables, répare les écarts
    python manage.py verifier_parquet --sans-reparer  # constate seulement (code retour 2 s'il y a un écart)
    python manage.py verifier_parquet --entreprise 3  # une seule entreprise
    python manage.py verifier_parquet --boucle 3600   # en service : un passage toutes les heures
"""
import time

from django.core.management.base import BaseCommand

from engine import parquet_store
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import t as _gt  # noqa: E402


class Command(BaseCommand):
    @property
    def help(self):
        return _gt("rg6.msg.027")

    def add_arguments(self, p):
        p.add_argument("--boucle", type=int, default=0, help=_gt("rg6.msg.028"))
        p.add_argument("--sans-reparer", action="store_true", help=_gt("rg6.msg.029"))
        p.add_argument("--entreprise", type=int, default=None, help=_gt("rg6.msg.030"))

    def handle(self, *args, **o):
        from engine.models import Entreprise
        entreprise = Entreprise.objects.filter(pk=o["entreprise"]).first() if o["entreprise"] else None
        code = 0
        try:
            while True:
                rapports = parquet_store.verifier_toutes(entreprise, reparer=not o["sans_reparer"])
                ecarts = [r for r in rapports if r["etat"] in ("reparee", "divergente", "absent", "erreur")]
                for r in ecarts:
                    self.stdout.write(_gtf("rg6.msg.031", v1=r['table'], v2=r['nom'], v3=r['etat'], v4=r['colonnes'] or '-', v5=r['lignes_eav'], v6=r['lignes_fichier']))
                for r in rapports:
                    if r.get("non_convertibles") or r.get("colonnes_mal_typees"):
                        self.stdout.write(_gtf("rg6.msg.032", v1=r['table'], v2=r['nom'], v3=r['non_convertibles'], v4=r['colonnes_mal_typees'] or '-', v5=r['exemples_non_convertibles'][:3]))
                self.stdout.write(_gtf("rg5.msg.097", v1=len(rapports), v2=len(ecarts)))
                code = 2 if any(r["etat"] in ("divergente", "absent", "erreur") for r in rapports) else 0
                if not o["boucle"]:
                    break
                time.sleep(max(o["boucle"], 60))
        except KeyboardInterrupt:
            pass
        if code:
            raise SystemExit(code)
