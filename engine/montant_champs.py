"""Champs de modèle Django pour l'argent (Decimal) : MontantField (2 décimales) et TauxField (6 décimales). Relus en `Montant` (engine/montant.py)."""
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from engine.montant import Montant, _patcher


class MontantField(models.DecimalField):
    """Montant FCFA : 20 chiffres dont 2 décimales. Relu depuis la base en `Montant`."""
    def __init__(self, *args, max_digits=20, decimal_places=2, **kwargs):
        super().__init__(*args, max_digits=max_digits, decimal_places=decimal_places, **kwargs)

    def from_db_value(self, valeur, expression, connection):
        return None if valeur is None else Montant(valeur)

    def get_db_prep_value(self, value, connection, prepared=False):
        # Les pilotes (sqlite3, psycopg) reçoivent un Decimal pur, jamais la sous-classe Montant.
        r = super().get_db_prep_value(value, connection, prepared)
        return Decimal(r) if isinstance(r, Decimal) and type(r) is not Decimal else r

    def to_python(self, valeur):
        r = super().to_python(valeur)
        return Montant(r) if isinstance(r, Decimal) else r


class TauxField(MontantField):
    """Taux / pourcentage : 20 chiffres dont 6 décimales."""
    def __init__(self, *args, decimal_places=6, **kwargs):
        super().__init__(*args, decimal_places=decimal_places, **kwargs)


_patcher(DjangoJSONEncoder)   # un Montant sort en JSON comme un nombre, pas comme une chaîne
