"""Surveillance SIEM : scellement du journal, ancrage hors machine, détection, vérification d'intégrité.

    python manage.py siem_surveiller                      # un passage
    python manage.py siem_surveiller --boucle 30          # en service (le collecteur « surveillance » bat à chaque tour)
    python manage.py siem_surveiller --verifier           # vérification d'intégrité complète, code retour 2 si anomalie
    python manage.py siem_surveiller --rebaser            # nouvelle référence du code (après un déploiement voulu)
    python manage.py siem_surveiller --incident           # état de l'incident en cours (calme / suspect / attaque)
    python manage.py siem_surveiller --signer-purge 2026-01-01T00:00:00+01:00   # signature à fournir à --purger
    python manage.py siem_surveiller --purger 2026-01-01T00:00:00+01:00 --signature <sig>   # purge de rétention signée
    python manage.py siem_surveiller --cle-ingestion      # secret d'ingestion à donner à un collecteur d'une autre machine
    python manage.py siem_surveiller --sql-durcissement   # SQL PostgreSQL : rôle sans UPDATE/DELETE sur la table d'audit

Sur PythonAnywhere : « Tasks » → tâche planifiée `python manage.py siem_surveiller` (un passage), ou tâche « toujours active » avec `--boucle 30`.
"""
import sys
import time

from django.core.management.base import BaseCommand

from engine import siem_noyau as siem
from engine import siem_regles
from engine.i18n import tf as _siem_tf  # noqa: E402
from engine.i18n import tl as _siem_tl  # noqa: E402

SQL_DURCISSEMENT = _siem_tl("rg4.siem.msg.211")


class Command(BaseCommand):
    @property
    def help(self):
        return _siem_tf("rg4.siem.msg.209")

    def add_arguments(self, p):
        p.add_argument("--boucle", type=int, default=0, help=_siem_tf("rg4.siem.msg.071"))
        p.add_argument("--verifier", action="store_true")
        p.add_argument("--rebaser", action="store_true")
        p.add_argument("--incident", action="store_true")
        p.add_argument("--signer-purge", dest="signer_purge", default="")
        p.add_argument("--purger", default="")
        p.add_argument("--signature", default="")
        p.add_argument("--cle-ingestion", dest="cle_ingestion", action="store_true")
        p.add_argument("--sql-durcissement", dest="sql", nargs="?", const="gyplatform_app", default="")

    def handle(self, *args, **o):
        if o["sql"]:
            self.stdout.write(SQL_DURCISSEMENT.format(role=o["sql"]))
            return
        if o["cle_ingestion"]:
            from engine.views_siem import cle_ingestion
            cle_id, _ = siem.cle_courante()
            self.stdout.write(_siem_tf("rg4.siem.msg.213", v1=cle_id, v2=cle_ingestion(cle_id).decode()))
            return
        if o["signer_purge"]:
            self.stdout.write(siem.signature_purge(o["signer_purge"]))
            return
        if o["purger"]:
            n = siem.purger(o["purger"], o["signature"])
            self.stdout.write(_siem_tf("rg4.siem.msg.072", n=n))
            return
        if o["rebaser"]:
            n = len(siem_regles.poser_reference_code())
            self.stdout.write(_siem_tf("rg4.siem.msg.073", n=n))
            return
        if o["incident"]:
            from engine import siem_incident
            e = siem_incident.etat()
            self.stdout.write(_siem_tf("rg4.siem.msg.075", v1=e['etat']))
            for s in (e["incident"] or {}).get("signaux", [])[:15]:
                self.stdout.write(f"  [{s['gravite']}/{s['couche']}] {s['regle']} — {s['message']}")
            return
        if o["verifier"]:
            ok, anomalies = siem.verifier_chaines()
            for a in anomalies:
                self.stdout.write(_siem_tf("rg4.siem.msg.212", v1=a.get('source'), v2=a.get('motif')))
            self.stdout.write(_siem_tf("rg4.siem.msg.076") if ok else _siem_tf("rg4.siem.msg.077", v1=len(anomalies)))
            sys.exit(0 if ok else 2)
        try:
            from django.core.management import call_command
            call_command("createcachetable", verbosity=0)   # cache dédié du SIEM (idempotent)
        except Exception as e:
            self.stderr.write(_siem_tf("rg4.siem.msg.078", e=e))
        if o["boucle"]:
            siem_regles.demarrer_pulsation()   # le battement ne dépend plus de la durée des étapes ; une étape bloquée relance le processus
        try:
            while True:
                try:
                    fait = siem_regles.cycle()
                except Exception:   # un passage raté (base indisponible...) ne tue jamais la surveillance en service
                    if not o["boucle"]:
                        raise
                    import logging
                    logging.getLogger("engine.siem").exception(_siem_tf("rg4.siem.msg.216"))
                    fait = {}
                if fait or not o["boucle"]:
                    self.stdout.write(_siem_tf("rg4.siem.msg.079") + (", ".join(f"{k}={'ok' if v else _siem_tf('rg4.siem.msg.214')}" for k, v in fait.items()) or _siem_tf("rg4.siem.msg.215")))
                if not o["boucle"]:
                    break
                time.sleep(max(o["boucle"], 5))
        except KeyboardInterrupt:
            pass
