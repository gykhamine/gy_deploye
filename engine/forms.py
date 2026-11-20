"""Le moteur genere automatiquement un formulaire depuis la definition
d'une TableDynamique, sans jamais ecrire de formulaire en dur."""
from engine.i18n import tl
from engine.i18n import t
from django import forms

from .models import ChampDynamique, TableDynamique


class TrimEtValiderMixin:
    """Ajouté à un formulaire : nettoie les espaces de chaque champ texte et
    refuse un champ obligatoire rempli uniquement d'espaces (case "vide").
    Les mots de passe (PasswordInput) ne sont jamais modifiés."""

    def clean(self):
        cleaned = super().clean()
        for nom, champ in self.fields.items():
            if isinstance(champ.widget, forms.PasswordInput):
                continue
            valeur = cleaned.get(nom)
            if isinstance(valeur, str):
                valeur_nettoyee = valeur.strip()
                cleaned[nom] = valeur_nettoyee
                if champ.required and valeur_nettoyee == "":
                    self.add_error(nom, t("engine/forms.py:g:24:41"))
        return cleaned


INDICATIFS_PAYS = [
    ("+242", "🇨🇬 Congo (+242)"),
    ("+243", "🇨🇩 RD Congo (+243)"),
    ("+237", "🇨🇲 Cameroun (+237)"),
    ("+241", "🇬🇦 Gabon (+241)"),
    ("+236", "🇨🇫 Centrafrique (+236)"),
    ("+240", tl("engine/forms.py:g:34:14")),
    ("+235", "🇹🇩 Tchad (+235)"),
    ("+221", tl("engine/forms.py:g:36:14")),
    ("+225", tl("engine/forms.py:g:37:14")),
    ("+223", "🇲🇱 Mali (+223)"),
    ("+226", "🇧🇫 Burkina Faso (+226)"),
    ("+227", "🇳🇪 Niger (+227)"),
    ("+228", "🇹🇬 Togo (+228)"),
    ("+229", tl("engine/forms.py:g:42:14")),
    ("+224", tl("engine/forms.py:g:43:14")),
    ("+245", tl("engine/forms.py:g:44:14")),
    ("+238", "🇨🇻 Cap-Vert (+238)"),
    ("+232", "🇸🇱 Sierra Leone (+232)"),
    ("+231", "🇱🇷 Liberia (+231)"),
    ("+220", "🇬🇲 Gambie (+220)"),
    ("+222", "🇲🇷 Mauritanie (+222)"),
    ("+233", "🇬🇭 Ghana (+233)"),
    ("+234", "🇳🇬 Nigeria (+234)"),
    ("+251", tl("engine/forms.py:g:52:14")),
    ("+254", "🇰🇪 Kenya (+254)"),
    ("+255", "🇹🇿 Tanzanie (+255)"),
    ("+256", "🇺🇬 Ouganda (+256)"),
    ("+250", "🇷🇼 Rwanda (+250)"),
    ("+257", "🇧🇮 Burundi (+257)"),
    ("+216", "🇹🇳 Tunisie (+216)"),
    ("+212", "🇲🇦 Maroc (+212)"),
    ("+213", tl("engine/forms.py:g:60:14")),
    ("+20", tl("engine/forms.py:g:61:13")),
    ("+33", "🇫🇷 France (+33)"),
    ("+32", "🇧🇪 Belgique (+32)"),
    ("+41", "🇨🇭 Suisse (+41)"),
    ("+1", tl("engine/forms.py:g:65:12")),
    ("+44", "🇬🇧 Royaume-Uni (+44)"),
    ("+49", "🇩🇪 Allemagne (+49)"),
    ("+34", "🇪🇸 Espagne (+34)"),
    ("+351", "🇵🇹 Portugal (+351)"),
    ("+55", tl("engine/forms.py:g:70:13")),
    ("+86", "🇨🇳 Chine (+86)"),
    ("+91", "🇮🇳 Inde (+91)"),
    ("+261", "🇲🇬 Madagascar (+261)"),
    ("+258", "🇲🇿 Mozambique (+258)"),
    ("+260", "🇿🇲 Zambie (+260)"),
    ("+263", "🇿🇼 Zimbabwe (+263)"),
    ("+267", "🇧🇼 Botswana (+267)"),
    ("+264", "🇳🇦 Namibie (+264)"),
    ("+27", tl("engine/forms.py:g:79:13")),
    ("+268", "🇸🇿 Eswatini (+268)"),
    ("+266", "🇱🇸 Lesotho (+266)"),
    ("+265", "🇲🇼 Malawi (+265)"),
    ("+269", "🇰🇲 Comores (+269)"),
    ("+230", "🇲🇺 Maurice (+230)"),
    ("+248", "🇸🇨 Seychelles (+248)"),
    ("+253", "🇩🇯 Djibouti (+253)"),
    ("+252", "🇸🇴 Somalie (+252)"),
    ("+249", "🇸🇩 Soudan (+249)"),
    ("+211", tl("engine/forms.py:g:89:14")),
    ("+218", "🇱🇾 Libye (+218)"),
    ("+31", "🇳🇱 Pays-Bas (+31)"),
    ("+39", "🇮🇹 Italie (+39)"),
    ("+352", "🇱🇺 Luxembourg (+352)"),
    ("+377", "🇲🇨 Monaco (+377)"),
    ("+43", "🇦🇹 Autriche (+43)"),
    ("+45", "🇩🇰 Danemark (+45)"),
    ("+46", tl("engine/forms.py:g:97:13")),
    ("+47", tl("engine/forms.py:g:98:13")),
    ("+358", "🇫🇮 Finlande (+358)"),
    ("+353", "🇮🇪 Irlande (+353)"),
    ("+30", tl("engine/forms.py:g:101:13")),
    ("+48", "🇵🇱 Pologne (+48)"),
    ("+420", tl("engine/forms.py:g:103:14")),
    ("+421", "🇸🇰 Slovaquie (+421)"),
    ("+36", "🇭🇺 Hongrie (+36)"),
    ("+40", "🇷🇴 Roumanie (+40)"),
    ("+359", "🇧🇬 Bulgarie (+359)"),
    ("+385", "🇭🇷 Croatie (+385)"),
    ("+90", "🇹🇷 Turquie (+90)"),
    ("+7", "🇷🇺 Russie (+7)"),
    ("+380", "🇺🇦 Ukraine (+380)"),
    ("+961", "🇱🇧 Liban (+961)"),
    ("+962", "🇯🇴 Jordanie (+962)"),
    ("+963", "🇸🇾 Syrie (+963)"),
    ("+964", "🇮🇶 Irak (+964)"),
    ("+966", "🇸🇦 Arabie Saoudite (+966)"),
    ("+971", tl("engine/forms.py:g:117:14")),
    ("+974", "🇶🇦 Qatar (+974)"),
    ("+965", tl("engine/forms.py:g:119:14")),
    ("+973", tl("engine/forms.py:g:120:14")),
    ("+968", "🇴🇲 Oman (+968)"),
    ("+972", tl("engine/forms.py:g:122:14")),
    ("+970", "🇵🇸 Palestine (+970)"),
    ("+98", "🇮🇷 Iran (+98)"),
    ("+92", "🇵🇰 Pakistan (+92)"),
    ("+880", "🇧🇩 Bangladesh (+880)"),
    ("+94", "🇱🇰 Sri Lanka (+94)"),
    ("+977", tl("engine/forms.py:g:128:14")),
    ("+95", "🇲🇲 Birmanie (+95)"),
    ("+66", tl("engine/forms.py:g:130:13")),
    ("+84", "🇻🇳 Vietnam (+84)"),
    ("+855", "🇰🇭 Cambodge (+855)"),
    ("+856", "🇱🇦 Laos (+856)"),
    ("+62", tl("engine/forms.py:g:134:13")),
    ("+60", "🇲🇾 Malaisie (+60)"),
    ("+65", "🇸🇬 Singapour (+65)"),
    ("+63", "🇵🇭 Philippines (+63)"),
    ("+81", "🇯🇵 Japon (+81)"),
    ("+82", tl("engine/forms.py:g:139:13")),
    ("+852", "🇭🇰 Hong Kong (+852)"),
    ("+886", tl("engine/forms.py:g:141:14")),
    ("+61", "🇦🇺 Australie (+61)"),
    ("+64", tl("engine/forms.py:g:143:13")),
    ("+52", "🇲🇽 Mexique (+52)"),
    ("+54", "🇦🇷 Argentine (+54)"),
    ("+56", "🇨🇱 Chili (+56)"),
    ("+57", "🇨🇴 Colombie (+57)"),
    ("+51", tl("engine/forms.py:g:148:13")),
    ("+58", "🇻🇪 Venezuela (+58)"),
    ("+593", tl("engine/forms.py:g:150:14")),
    ("+591", "🇧🇴 Bolivie (+591)"),
    ("+595", "🇵🇾 Paraguay (+595)"),
    ("+598", "🇺🇾 Uruguay (+598)"),
    ("+509", tl("engine/forms.py:g:154:14")),
    ("+590", "🇬🇵 Guadeloupe (+590)"),
    ("+596", "🇲🇶 Martinique (+596)"),
    ("+594", "🇬🇫 Guyane (+594)"),
    ("+262", tl("engine/forms.py:g:158:14")),
]


class LoginForm(TrimEtValiderMixin, forms.Form):
    """Formulaire de connexion."""
    username = forms.CharField(
        label=tl("engine/forms.py:164:16"),
        widget=forms.TextInput(attrs={
            'class': 'champ-formulaire',
            'placeholder': tl("engine/forms.py:g:169:28"),
            'autofocus': True,
        })
    )
    password = forms.CharField(
        label=tl("engine/forms.py:172:16"),
        widget=forms.PasswordInput(attrs={
            'class': 'champ-formulaire',
            'placeholder': tl("engine/forms.py:g:177:28"),
        })
    )


class ProfileForm(TrimEtValiderMixin, forms.Form):
    """Formulaire de modification du profil utilisateur."""
    entreprise_nom = forms.CharField(
        label=tl("engine/forms.py:183:16"),
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire'})
    )
    first_name = forms.CharField(
        label=tl("engine/forms.py:189:16"),
        required=False,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire'})
    )
    last_name = forms.CharField(
        label=tl("engine/forms.py:194:16"),
        required=False,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire'})
    )
    indicatif_pays = forms.ChoiceField(
        label=tl("engine/forms.py:199:16"),
        choices=INDICATIFS_PAYS,
        required=False,
        widget=forms.Select(attrs={'class': 'champ-formulaire'})
    )
    telephone = forms.CharField(
        label=tl("engine/forms.py:205:16"),
        required=False,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:209:83")})
    )


class ChangePasswordForm(TrimEtValiderMixin, forms.Form):
    """Formulaire de changement de mot de passe."""
    ancien_mot_de_passe = forms.CharField(
        label=tl("engine/forms.py:214:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire'})
    )
    nouveau_mot_de_passe = forms.CharField(
        label=tl("engine/forms.py:218:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire'}),
        min_length=8
    )
    confirmation_mot_de_passe = forms.CharField(
        label=tl("engine/forms.py:223:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire'})
    )

    def clean(self):
        cleaned = super().clean()
        nouveau = cleaned.get('nouveau_mot_de_passe')
        confirmation = cleaned.get('confirmation_mot_de_passe')
        if nouveau and confirmation and nouveau != confirmation:
            self.add_error('confirmation_mot_de_passe', t("engine/forms.py:g:233:57"))
        return cleaned

class InscriptionForm(TrimEtValiderMixin, forms.Form):
    """Formulaire d'inscription : cree un compte + une entreprise en une fois,
    OU rejoint une entreprise existante (mode client) si `rejoindre_entreprise`
    est passe au constructeur — dans ce cas `entreprise_nom` n'est pas requis."""
    username = forms.CharField(
        label=tl("engine/forms.py:240:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': 'ex: jean.dupont', 'autofocus': True})
    )
    password = forms.CharField(
        label=tl("engine/forms.py:244:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:247:87")}),
        min_length=6,
    )
    password_confirmation = forms.CharField(
        label=tl("engine/forms.py:249:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:252:87")}),
    )
    first_name = forms.CharField(
        label=tl("engine/forms.py:253:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:256:83")})
    )
    last_name = forms.CharField(
        label=tl("engine/forms.py:257:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:260:83")})
    )
    indicatif_pays = forms.ChoiceField(
        label=tl("engine/forms.py:261:16"),
        choices=INDICATIFS_PAYS,
        initial="+242",
        widget=forms.Select(attrs={'class': 'champ-formulaire'})
    )
    telephone = forms.CharField(
        label=tl("engine/forms.py:267:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:270:83")})
    )
    entreprise_nom = forms.CharField(
        label=tl("engine/forms.py:271:16"),
        required=False, max_length=200,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:275:83")})
    )

    def __init__(self, *args, rejoindre_entreprise=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._rejoindre_entreprise = rejoindre_entreprise

    def clean_username(self):
        nom = self.cleaned_data['username']
        from django.contrib.auth import get_user_model
        if get_user_model().objects.filter(username=nom).exists():
            raise forms.ValidationError(t("engine/forms.py:284:42"))
        return nom

    def clean(self):
        cleaned = super().clean()
        mdp = cleaned.get('password')
        conf = cleaned.get('password_confirmation')
        if mdp and conf and mdp != conf:
            self.add_error('password_confirmation', t("engine/forms.py:g:293:53"))
        # entreprise_nom n'est obligatoire que pour la creation d'une nouvelle
        # entreprise (pas quand un client rejoint une entreprise existante).
        if not self._rejoindre_entreprise and not (cleaned.get('entreprise_nom') or '').strip():
            self.add_error('entreprise_nom', t("engine/forms.py:g:297:46"))
        nom_entreprise = (cleaned.get('entreprise_nom') or '').strip()
        if not self._rejoindre_entreprise and nom_entreprise:
            from .models import Entreprise
            if Entreprise.objects.filter(nom__iexact=nom_entreprise).exists():
                self.add_error('entreprise_nom', t("engine/forms.py:entreprise_deja_prise"))
        return cleaned


class CreerEmployeForm(TrimEtValiderMixin, forms.Form):
    """Formulaire pour ajouter un employe a l'entreprise."""
    username = forms.CharField(
        label=tl("engine/forms.py:303:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:306:83"), 'autofocus': True})
    )
    password = forms.CharField(
        label=tl("engine/forms.py:307:16"),
        widget=forms.PasswordInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:310:87")}),
        min_length=6,
    )
    first_name = forms.CharField(
        label=tl("engine/forms.py:312:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': 'Prenom'})
    )
    last_name = forms.CharField(
        label=tl("engine/forms.py:316:16"),
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:319:83")})
    )
    indicatif_pays = forms.ChoiceField(
        label=tl("engine/forms.py:320:16"),
        choices=INDICATIFS_PAYS,
        required=False,
        widget=forms.Select(attrs={'class': 'champ-formulaire'})
    )
    telephone = forms.CharField(
        label=tl("engine/forms.py:326:16"),
        required=False,
        widget=forms.TextInput(attrs={'class': 'champ-formulaire', 'placeholder': tl("engine/forms.py:g:330:83")})
    )
    role = forms.ChoiceField(
        label=tl("engine/forms.py:331:16"),
        choices=[("admin", "Administrateur"), ("agent", tl("engine/forms.py:g:334:57")), ("lecteur", tl("engine/forms.py:g:334:79"))],
        initial="agent",
        required=False,
        widget=forms.Select(attrs={'class': 'champ-formulaire'})
    )

    def clean_username(self):
        nom = self.cleaned_data['username']
        from django.contrib.auth import get_user_model
        if get_user_model().objects.filter(username=nom).exists():
            raise forms.ValidationError(t("engine/forms.py:342:42"))
        return nom


WIDGETS_PAR_TYPE = {
    "texte": forms.CharField,
    "texte_long": lambda **kw: forms.CharField(widget=forms.Textarea, **kw),
    "nombre": forms.IntegerField,
    "decimal": forms.FloatField,
    "monnaie": forms.DecimalField,
    "pourcentage": forms.FloatField,
    "note": lambda **kw: forms.IntegerField(min_value=1, max_value=5, **kw),
    "date": forms.DateField,
    "heure": forms.TimeField,
    "datetime": forms.DateTimeField,
    "horodatage": forms.DateTimeField,
    "duree": forms.DurationField,
    "booleen": forms.BooleanField,
    # `switch` est rendu comme un BooleanField standard côté Django (la
    # distinction visuelle — toggle plutôt que checkbox — est faite en JS).
    "switch": forms.BooleanField,
    # `barre_progression` : entier borné 0..100. required=False par défaut
    # (l'utilisateur a souvent une valeur par défaut de 0 qu'on remplit
    # côté UI).
    "barre_progression": lambda **kw: forms.IntegerField(min_value=0, max_value=100, **kw),
    "email": forms.EmailField,
    "url": forms.URLField,
    "telephone": forms.CharField,
    "couleur": lambda **kw: forms.CharField(widget=forms.TextInput(attrs={"type": "color"}), **kw),
    "fichier": forms.FileField,
    "image": forms.ImageField,
    "son": forms.FileField,
    "video": forms.FileField,
    # `vecteur`/`matrice` retombent sur CharField (defaut) : la valeur brute
    # est du JSON texte, validee/interpretee plus loin (ia_integration.py),
    # pas au niveau de ce formulaire generique. `json` suit le meme principe
    # — document JSON libre, saisi comme texte, juste avec un Textarea pour
    # le confort de saisie (comme texte_long).
    "json": lambda **kw: forms.CharField(widget=forms.Textarea, **kw),
}


def construire_formulaire_dynamique(table: TableDynamique):
    """Retourne une classe Form generee dynamiquement pour cette table."""
    champs = {}
    for champ in table.champs.all():
        if champ.type_champ == "relation" and champ.relation_vers:
            queryset = champ.relation_vers.enregistrements.all()
            champs[champ.slug] = forms.ModelChoiceField(
                queryset=queryset, required=champ.obligatoire, label=champ.nom
            )
        elif champ.type_champ in ChampDynamique.TYPES_CHOIX:
            options = [(o, o) for o in champ.liste_options]
            classe_champ = forms.MultipleChoiceField if champ.type_champ == "choix_multiple" else forms.ChoiceField
            champs[champ.slug] = classe_champ(choices=options, required=champ.obligatoire, label=champ.nom)
        else:
            fabrique = WIDGETS_PAR_TYPE.get(champ.type_champ, forms.CharField)
            champs[champ.slug] = fabrique(required=champ.obligatoire, label=champ.nom)

    return type(f"Formulaire{table.slug.capitalize()}", (TrimEtValiderMixin, forms.Form), champs)


class DemandeAgentForm(forms.Form):
    """Formulaire unique de l'interface : une seule zone de conversation."""
    table_slug = forms.CharField(label=tl("engine/forms.py:405:41"))
    texte = forms.CharField(widget=forms.Textarea, label=tl("engine/forms.py:406:59"))
