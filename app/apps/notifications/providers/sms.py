"""Адаптер SMS-провайдера: PaySoft заменяется без правок бизнес-логики."""

import logging

import requests
from django.conf import settings

logger = logging.getLogger('alasoft.sms')


class SmsSendError(Exception):
    pass


class BaseSmsProvider:
    name = 'base'

    def send(self, phone: str, text: str) -> str:
        raise NotImplementedError


class ConsoleSmsProvider(BaseSmsProvider):
    """Dev-режим: код печатается в лог, внешних вызовов нет."""

    name = 'console'

    def send(self, phone: str, text: str) -> str:
        logger.info('SMS → %s: %s', phone, text)
        return 'console'


class PaySoftSmsProvider(BaseSmsProvider):
    name = 'paysoft'

    def __init__(self):
        self.url = settings.SMS_PAYSOFT_URL
        self.login = settings.SMS_PAYSOFT_LOGIN
        self.password = settings.SMS_PAYSOFT_PASSWORD
        self.sender = settings.SMS_PAYSOFT_SENDER

    def send(self, phone: str, text: str) -> str:
        if not self.url:
            raise SmsSendError('SMS_PAYSOFT_URL не настроен')
        try:
            response = requests.post(
                self.url,
                json={
                    'login': self.login,
                    'password': self.password,
                    'sender': self.sender,
                    'phone': phone,
                    'text': text,
                },
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SmsSendError(str(exc)) from exc

        try:
            return str(response.json().get('message_id', ''))
        except ValueError:
            return ''


class GreenApiSmsProvider(BaseSmsProvider):
    """Доставка через WhatsApp (GREEN-API). Канал другой, интерфейс тот же:
    OTP-логика не знает, SMS это или мессенджер."""

    name = 'greenapi'

    def __init__(self):
        self.api_url = settings.GREENAPI_API_URL.rstrip('/')
        self.id_instance = settings.GREENAPI_ID_INSTANCE
        self.api_token = settings.GREENAPI_API_TOKEN

    def send(self, phone: str, text: str) -> str:
        if not (self.api_url and self.id_instance and self.api_token):
            raise SmsSendError('GREENAPI_API_URL/ID_INSTANCE/API_TOKEN не настроены')
        url = f'{self.api_url}/waInstance{self.id_instance}/sendMessage/{self.api_token}'
        try:
            response = requests.post(
                url,
                json={
                    # E.164 без плюса: +996555123456 → 996555123456@c.us
                    'chatId': f'{phone.lstrip("+")}@c.us',
                    'message': text,
                    'linkPreview': False,
                },
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # В URL лежит токен — в текст ошибки (и в БД) его не пускаем.
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            raise SmsSendError(f'GREEN-API: HTTP {status}' if status else 'GREEN-API: сеть недоступна') from None

        try:
            return str(response.json().get('idMessage', ''))
        except ValueError:
            return ''


_PROVIDERS = {
    'console': ConsoleSmsProvider,
    'paysoft': PaySoftSmsProvider,
    'greenapi': GreenApiSmsProvider,
}


def get_sms_provider() -> BaseSmsProvider:
    provider_class = _PROVIDERS.get(settings.SMS_PROVIDER, ConsoleSmsProvider)
    return provider_class()
