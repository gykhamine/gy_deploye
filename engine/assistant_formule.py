"""
Assistant visuel (pas-à-pas) pour les champs calculés (ChampDynamique avec
type_champ='formule') — voir ChampDynamique.formule_etapes_json.

Permet à un utilisateur non technicien de construire une formule qui combine
plusieurs champs de la MÊME ligne (ex: bulletin scolaire = note1 + note2,
puis résultat × coefficient), en choisissant à la volée les champs à inclure
et l'opération à chaque étape, sans écrire de code.

`generer_code_formule(etapes)` transforme cette liste d'étapes en un script
`def formule(ligne): ...` classique, exécuté ensuite exactement comme une
formule écrite à la main (voir calculs.recalculer_champ_formule ->
executor.executer_formule_securisee). C'est la SEULE fonction qui fait le
lien entre l'assistant visuel et le moteur d'exécution existant : aucun
changement n'est nécessaire côté calculs.py/executor.py.

Format d'une étape :
    {
        "operation": "addition" | "soustraction" | "multiplication"
                     | "division" | "moyenne",
        "termes": [
            {"type": "champ", "slug": "note1"},
            {"type": "constante", "valeur": 0.2},   # nombre fixe (ex. taux de TVA)
            {"type": "precedent"},   # résultat de l'étape précédente
            ...
        ],
    }
`{"type": "precedent"}` est ignoré à l'étape 1 (rien à référencer).
"""
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t, tf, tl

OPERATIONS_LABELS = {
    "addition": "Addition (+)",
    "soustraction": "Soustraction (−)",
    "multiplication": "Multiplication (×)",
    "division": "Division (÷)",
    "moyenne": tl("engine/assistant_formule.py:g:36:16"),
}


def _constante(terme):
    """Valeur numérique finie d'un terme {"type": "constante", "valeur": ...} (ex. 0.2 pour une TVA à 20 %),
    ou None si absente/invalide. Toujours convertie en float : jamais de texte libre dans le code généré."""
    import math
    try:
        v = float(terme.get("valeur"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _valider_etapes(etapes):
    if not isinstance(etapes, list) or not etapes:
        raise ValueError(_i18n_t("engine/assistant_formule.py:g:53:26"))
    for i, etape in enumerate(etapes):
        if etape.get("operation") not in OPERATIONS_LABELS:
            raise ValueError(tf("engine/assistant_formule.py:g:56:30", v1=i + 1))
        termes = etape.get("termes") or []
        termes_valides = [
            t for t in termes
            if (t.get("type") == "champ" and t.get("slug"))
            or (t.get("type") == "precedent" and i > 0)
            or (t.get("type") == "constante" and _constante(t) is not None)
        ]
        if not termes_valides:
            raise ValueError(tf("engine/assistant_formule.py:g:65:30", v1=i + 1))


def generer_code_formule(etapes):
    """Retourne le code source `def formule(ligne): ...` correspondant à la
    liste d'étapes. Lève ValueError si les étapes sont invalides (aucune
    étape, aucun terme sélectionné, opération inconnue)."""
    _valider_etapes(etapes)

    lignes = [
        _gt("engine/assistant_formule.py:77:9"),
        td("engine/assistant_formule.py:g:77:9"),
        td("engine/assistant_formule.py:g:78:9"),
        td("engine/assistant_formule.py:g:79:9"),
        "    def _v(slug):",
        td("engine/assistant_formule.py:g:81:9"),
        "        try:",
        "            return float(val) if val not in (None, '') else 0.0",
        "        except (TypeError, ValueError):",
        "            return 0.0",
        "",
    ]

    var_precedent = None
    for i, etape in enumerate(etapes, start=1):
        operandes = []
        for terme in etape.get("termes") or []:
            if terme.get("type") == "precedent":
                if var_precedent is not None:
                    operandes.append(var_precedent)
            elif terme.get("type") == "champ" and terme.get("slug"):
                operandes.append(f"_v({terme['slug']!r})")
            elif terme.get("type") == "constante" and _constante(terme) is not None:
                operandes.append(repr(_constante(terme)))
        if not operandes:
            operandes = ["0.0"]

        operation = etape.get("operation")
        if operation == "moyenne":
            expression = "(" + " + ".join(operandes) + f") / {len(operandes)}"
        elif operation == "division":
            expression = " / ".join(operandes)
        elif operation == "soustraction":
            expression = " - ".join(operandes)
        elif operation == "multiplication":
            expression = " * ".join(operandes)
        else:  # "addition"
            expression = " + ".join(operandes)

        variable = f"r{i}"
        lignes.append(f"    {variable} = {expression}")
        var_precedent = variable

    lignes.append(f"    return {var_precedent}")
    return "\n".join(lignes) + "\n"
