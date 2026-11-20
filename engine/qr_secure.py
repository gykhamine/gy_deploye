"""Chiffrement du contenu des QR codes (identifiant seul, jamais les
donnees). Cle propre a chaque entreprise (Entreprise.cle_chiffrement_qr),
derivee en cle Fernet (AES-128-CBC + HMAC-SHA256 authentifie) via SHA-256.
Sans cle configuree, le QR reste en clair (comportement historique)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(cle_brute: str):
    if not cle_brute:
        return None
    empreinte = hashlib.sha256(cle_brute.strip().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(empreinte))


def chiffrer_identifiant(entreprise, texte: str) -> str:
    """Chiffre l'identifiant (matricule) avant generation de l'image QR."""
    f = _fernet(getattr(entreprise, "cle_chiffrement_qr", ""))
    if not f or not texte:
        return texte
    return f.encrypt(texte.encode("utf-8")).decode("ascii")


def dechiffrer_identifiant(entreprise, contenu: str):
    """Dechiffre le contenu lu depuis un QR scanne. Retourne None si le
    contenu est invalide/falsifie ou ne correspond pas a la cle configuree."""
    f = _fernet(getattr(entreprise, "cle_chiffrement_qr", ""))
    if not f:
        return contenu or None
    try:
        return f.decrypt(contenu.strip().encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError, AttributeError):
        return None
