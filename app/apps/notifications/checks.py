"""Проверки конфигурации доставки сообщений."""

from django.conf import settings
from django.core.checks import Warning, register


@register('security')
def check_sms_provider(app_configs, **kwargs):
    """Ловит две частые ошибки настройки провайдера до того, как они дойдут
    до живых пользователей."""
    issues = []

    if settings.SMS_PROVIDER == 'paysoft' and not settings.SMS_PAYSOFT_URL:
        issues.append(Warning(
            'SMS_PROVIDER=paysoft, но SMS_PAYSOFT_URL пуст — коды не уйдут.',
            hint='Заполните SMS_PAYSOFT_URL/LOGIN/PASSWORD в .env.',
            id='notifications.W001',
        ))

    if settings.SMS_PROVIDER == 'greenapi' and not (
        settings.GREENAPI_API_URL and settings.GREENAPI_ID_INSTANCE and settings.GREENAPI_API_TOKEN
    ):
        issues.append(Warning(
            'SMS_PROVIDER=greenapi, но креды GREEN-API заполнены не все — коды не уйдут.',
            hint='Заполните GREENAPI_API_URL/ID_INSTANCE/API_TOKEN в .env.',
            id='notifications.W003',
        ))

    if settings.SMS_PROVIDER == 'console' and not settings.DEBUG:
        issues.append(Warning(
            'SMS_PROVIDER=console на боевых настройках: коды подтверждения '
            'пишутся в лог и никому не отправляются.',
            hint='Подключите реального провайдера перед выпуском в магазины.',
            id='notifications.W002',
        ))

    return issues
