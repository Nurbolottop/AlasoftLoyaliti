"""Адаптер push-провайдера (FCM HTTP v1)."""

import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger('alasoft.push')


class PushSendError(Exception):
    def __init__(self, message, invalid_token=False):
        super().__init__(message)
        self.invalid_token = invalid_token


class BasePushProvider:
    name = 'base'

    def send(self, token: str, title: str, body: str, data: dict, *, silent=False,
             high_priority=False, channel='default', badge=None) -> str:
        raise NotImplementedError


class ConsolePushProvider(BasePushProvider):
    name = 'console'

    def send(self, token: str, title: str, body: str, data: dict, *, silent=False,
             high_priority=False, channel='default', badge=None) -> str:
        logger.info('PUSH → %s | %s | %s | %s | silent=%s high=%s', token[:12], title, body, data,
                    silent, high_priority)
        return 'console'


class FcmPushProvider(BasePushProvider):
    """FCM HTTP v1. Требует service-account JSON и google-auth в окружении."""

    name = 'fcm'

    def _access_token(self):
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GoogleRequest
        except ImportError as exc:
            raise PushSendError('google-auth не установлен: pip install google-auth') from exc

        credentials = service_account.Credentials.from_service_account_file(
            settings.FCM_CREDENTIALS_FILE,
            scopes=['https://www.googleapis.com/auth/firebase.messaging'],
        )
        credentials.refresh(GoogleRequest())
        return credentials.token

    def send(self, token: str, title: str, body: str, data: dict, *, silent=False,
             high_priority=False, channel='default', badge=None) -> str:
        if not settings.FCM_PROJECT_ID or not settings.FCM_CREDENTIALS_FILE:
            raise PushSendError('FCM не настроен (FCM_PROJECT_ID/FCM_CREDENTIALS_FILE)')

        url = f'https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send'
        message = {
            'token': token,
            'data': {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                     for k, v in (data or {}).items()},
        }
        if silent:
            # Тихое событие: только data, iOS будит приложение в фоне
            message['android'] = {'priority': 'high' if high_priority else 'normal'}
            message['apns'] = {
                'headers': {'apns-push-type': 'background', 'apns-priority': '5'},
                'payload': {'aps': {'content-available': 1}},
            }
        else:
            message['notification'] = {'title': title, 'body': body}
            message['android'] = {
                'priority': 'high' if high_priority else 'normal',
                'notification': {'channel_id': channel},
            }
            aps = {'sound': 'default'}
            if badge is not None:
                aps['badge'] = int(badge)
            message['apns'] = {
                'headers': {'apns-priority': '10' if high_priority else '5'},
                'payload': {'aps': aps},
            }
        payload = {'message': message}
        try:
            response = requests.post(
                url,
                headers={'Authorization': f'Bearer {self._access_token()}'},
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise PushSendError(str(exc)) from exc

        if response.status_code in (400, 404):
            # UNREGISTERED / INVALID_ARGUMENT — токен устройства пора удалять.
            raise PushSendError(response.text[:200], invalid_token=True)
        if response.status_code >= 400:
            raise PushSendError(response.text[:200])

        try:
            return response.json().get('name', '')
        except ValueError:
            return ''


_PROVIDERS = {
    'console': ConsolePushProvider,
    'fcm': FcmPushProvider,
}


def get_push_provider() -> BasePushProvider:
    provider_class = _PROVIDERS.get(settings.PUSH_PROVIDER, ConsolePushProvider)
    return provider_class()
