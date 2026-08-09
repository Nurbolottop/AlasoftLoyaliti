"""Маршруты AlaSoft. Публичный контракт — /api/v1 (ТЗ backend §2)."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.director.urls import urlpatterns as director_urlpatterns
from apps.loyalty.urls import admin_urlpatterns as loyalty_admin_urlpatterns
from apps.loyalty.urls import user_urlpatterns as loyalty_user_urlpatterns
from apps.organizations.urls import urlpatterns as organization_urlpatterns
from apps.users.urls import auth_urlpatterns, me_urlpatterns


def healthcheck(_request):
    return JsonResponse({'success': True, 'data': {'status': 'ok'}})


api_v1 = [
    path('auth/', include((auth_urlpatterns, 'auth'))),
    path('', include((me_urlpatterns, 'me'))),
    path('', include((organization_urlpatterns, 'organizations'))),
    path('', include((loyalty_user_urlpatterns, 'loyalty-user'))),
    path('admin/', include((loyalty_admin_urlpatterns, 'loyalty-admin'))),
    path('director/', include((director_urlpatterns, 'director'))),
]

urlpatterns = [
    path('health', healthcheck, name='health'),
    path('api/v1/', include((api_v1, 'v1'))),

    # OpenAPI / Swagger (ТЗ backend §32)
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Служебная админка Django — только для операторов платформы.
    path('django-admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
