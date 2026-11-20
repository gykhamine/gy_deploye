"""Argent en Decimal sans casser le code existant (module PUR : aucun import Django ; les champs de modèle sont dans montant_champs.py).

`Montant` est un Decimal qui accepte aussi les float dans + - * / // % ** (un Decimal pur lève TypeError sur « Decimal + float »,
et tout le code wallet mélange encore des float : taux, prix, saisies JSON). Le résultat d'une opération reste un Montant.
`MontantField` (2 décimales) et `TauxField` (6 décimales) lisent la base en Montant. Les floats saisis sont convertis par leur
écriture décimale la plus courte (0.1 -> 0.1, pas 0.1000000000000000055…).
Un Montant est écrit en JSON comme un NOMBRE (json.dumps, JsonResponse, hachage des blocs) : même texte qu'avec un float."""
import json
from decimal import Decimal



def _c(x):
    return Decimal(repr(x)) if isinstance(x, float) else x


def _m(r):
    return Montant(r) if isinstance(r, Decimal) and not isinstance(r, Montant) else r


def _op(nom):
    base = getattr(Decimal, nom)

    def f(self, autre, *reste):
        r = base(self, _c(autre), *reste)
        return r if r is NotImplemented else _m(r)
    f.__name__ = nom
    return f


class Montant(Decimal):
    __slots__ = ()

    def __new__(cls, valeur="0", contexte=None):
        return Decimal.__new__(cls, _c(valeur), contexte)

    for _n in ("__add__", "__radd__", "__sub__", "__rsub__", "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
               "__floordiv__", "__rfloordiv__", "__mod__", "__rmod__", "__pow__", "__rpow__"):
        locals()[_n] = _op(_n)
    del _n

    def __neg__(self):
        return Montant(Decimal.__neg__(self))

    def __pos__(self):
        return Montant(Decimal.__pos__(self))

    def __abs__(self):
        return Montant(Decimal.__abs__(self))

    def __round__(self, n=None):
        return Decimal.__round__(self) if n is None else Montant(Decimal.__round__(self, n))

    def quantize(self, exp, *a, **k):
        return Montant(Decimal.quantize(self, exp, *a, **k))


def saisie(x):
    """Valeur reçue de l'extérieur (JSON, formulaire) -> Montant. Mêmes erreurs que float() (TypeError / ValueError) ; NaN et l'infini
    restent des float pour que les contrôles math.isfinite existants les refusent comme avant."""
    import math
    v = float(x)
    if not math.isfinite(v):
        return v
    if isinstance(x, str):
        try:
            return Montant(x.strip())
        except Exception:
            pass
    return Montant(x if isinstance(x, (int, Decimal)) and not isinstance(x, bool) else v)


def _patcher(classe):
    if classe.__dict__.get("_montant_patche"):   # __dict__ : ne pas hériter du drapeau de la classe mère
        return
    d0 = classe.default

    def default(self, o):
        return float(o) if isinstance(o, Montant) else d0(self, o)
    classe.default = default
    classe._montant_patche = True


_patcher(json.JSONEncoder)


def _adapter_sqlite():
    """sqlite3 n'adapte que le type EXACT Decimal (Django enregistre Decimal -> str) : un Montant (sous-classe) levait
    « Error binding parameter N: type 'Montant' is not supported » à tout INSERT/UPDATE/filtre. On l'enregistre comme Decimal."""
    try:
        import sqlite3
        sqlite3.register_adapter(Montant, str)
    except Exception:
        pass


_adapter_sqlite()
