"""Системные проверки безопасности аутентификации."""

from django.conf import settings
from django.core.checks import Warning, register


@register('security')
def check_otp_static_code(app_configs, **kwargs):
    """Фиксированный OTP виден в `manage.py check` и в deploy-чеклисте."""
    if not settings.OTP_STATIC_CODE:
        return []
    return [
        Warning(
            'OTP_STATIC_CODE задан: подтверждение телефона проходит по '
            'фиксированному коду, а не по SMS.',
            hint='Временная мера до подключения SMS-провайдера. Убери '
                 'OTP_STATIC_CODE из .env сразу после настройки SMS_PROVIDER.',
            id='users.W001',
        )
    ]
