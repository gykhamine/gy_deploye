"""Utilitaires purs hérités de l'ancien module cluster : signature HMAC de messages et journal hors ligne de la blockchain système.

  • `signer` / `verifier` : signature HMAC-SHA256 horodatée (ancrage SIEM signé, rappels sécurisés) ;
  • `journaliser_hors_ligne` / `reprendre_journal` : quand la base est injoignable, l'événement super-administrateur attend
    dans un fichier (une ligne JSON par événement) puis est rejoué, dans l'ordre, à la prochaine écriture réussie.
"""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

TOLERANCE_HORLOGE_S = 120  # écart maximal accepté pour un message signé


def _chemin_journal(base_dir=None):
    if base_dir is None:
        from django.conf import settings
        base_dir = settings.BASE_DIR
    return Path(base_dir) / "journal_systeme_hors_ligne.jsonl"


def journaliser_hors_ligne(tx, base_dir=None):
    """Base injoignable : l'événement super-admin attend dans un fichier (une ligne JSON par événement)."""
    try:
        with open(_chemin_journal(base_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(tx, ensure_ascii=False) + "\n")
        os.chmod(_chemin_journal(base_dir), 0o600)
    except OSError:
        pass


def reprendre_journal(base_dir=None):
    """Événements en attente, dans l'ordre ; le fichier est supprimé (renommage atomique : un seul rejoueur)."""
    p = _chemin_journal(base_dir)
    pris = p.with_suffix(".pris")
    try:
        os.replace(p, pris)
    except OSError:
        return []
    evenements = []
    try:
        with open(pris, encoding="utf-8") as f:
            for ligne in f:
                try:
                    evenements.append(json.loads(ligne))
                except ValueError:
                    pass
        os.unlink(pris)
    except OSError:
        pass
    return evenements


def signer(secret, corps: bytes, ts=None):
    ts = int(ts or time.time())
    mac = hmac.new(secret.encode(), str(ts).encode() + b"." + corps, hashlib.sha256).hexdigest()
    return ts, mac


def verifier(secret, corps: bytes, ts, mac):
    if not secret or not mac:
        return False
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > TOLERANCE_HORLOGE_S:
        return False  # rejeu d'un vieux message
    attendu = hmac.new(secret.encode(), str(ts).encode() + b"." + corps, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, str(mac))
