"""Системные проверки безопасности аутентификации."""

from django.conf import settings
from django.core.checks import Error, Warning, register


@register('security')
def check_otp_static_code(app_configs, **kwargs):
    """Фиксированный OTP = любой подтверждает чужой номер. В dev это
    предупреждение, на боевых настройках — ошибка: migrate в entrypoint
    падает, и сервер с такой настройкой просто не поднимется."""
    if not settings.OTP_STATIC_CODE:
        return []
    if settings.DEBUG:
        return [
            Warning(
                'OTP_STATIC_CODE задан: подтверждение телефона проходит по '
                'фиксированному коду, а не по SMS.',
                hint='Допустимо только в dev.',
                id='users.W001',
            )
        ]
    return [
        Error(
            'OTP_STATIC_CODE задан на боевых настройках: любой может '
            'подтвердить чужой номер.',
            hint='Очистите OTP_STATIC_CODE в .env. Для ревьюеров магазинов '
                 'есть OTP_TEST_ACCOUNTS.',
            id='users.E001',
        )
    ]
