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


_PROVIDERS = {
    'console': ConsoleSmsProvider,
    'paysoft': PaySoftSmsProvider,
}


def get_sms_provider() -> BaseSmsProvider:
    provider_class = _PROVIDERS.get(settings.SMS_PROVIDER, ConsoleSmsProvider)
    return provider_class()
