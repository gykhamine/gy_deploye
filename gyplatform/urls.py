"""
URL configuration for gyplatform project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path, re_path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from engine.securite import servir_media
from engine.ratelimit import limiter

# L'écran de connexion de l'administration Django subit la même limite que la connexion principale.
admin.site.login = limiter("admin_login", max_appels=10, periode_secondes=60, methodes={"POST"})(admin.site.login)

urlpatterns = [
    path('admin/', admin.site.urls),
    # Médias derrière contrôle d'accès (engine/securite.py) — plus jamais publics, et servis aussi sans DEBUG.
    re_path(r'^media/(?P<chemin>.+)$', servir_media, name='media'),
    path('', include('engine.urls')),
]

# Pages d'erreur personnalisées (identité de l'entreprise, thème, langue) : engine/views_erreur.py
handler400 = "engine.views_erreur.erreur_400"
handler403 = "engine.views_erreur.erreur_403"
handler404 = "engine.views_erreur.erreur_404"
handler500 = "engine.views_erreur.erreur_500"
