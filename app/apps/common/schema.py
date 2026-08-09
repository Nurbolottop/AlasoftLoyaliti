"""Расширения drf-spectacular для корректной генерации OpenAPI."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AlaSoftJWTScheme(OpenApiAuthenticationExtension):
    """Описывает Bearer-токен AlaSoft как securityScheme в схеме."""

    target_class = 'apps.users.authentication.AlaSoftJWTAuthentication'
    name = 'BearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': 'Access-токен из /api/v1/auth/*. В claim role — USER / '
                           'ORGANIZATION_ADMIN / DIRECTOR.',
        }
