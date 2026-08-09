from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.common'
    verbose_name = 'Общее'

    def ready(self):
        # Регистрирует OpenAPI-расширения (securityScheme для JWT).
        from apps.common import schema  # noqa: F401
