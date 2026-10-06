"""Регрессии аудита безопасности: обход лимитов и перебор PIN/кода."""

from django.test import RequestFactory
from django.utils import timezone

from apps.common.net import client_ip
from apps.tests.conftest import auth_client, post

OTP_REQUEST = '/api/v1/auth/otp/request'
OTP_VERIFY = '/api/v1/auth/otp/verify'
PIN_LOGIN = '/api/v1/auth/pin/login'
PHONE_CHANGE = '/api/v1/me/phone/request'

# Один номер в разных записях — все нормализуются в +996555123456.
PHONE_FORMATS = [
    '+996555123456', '996555123456', '0555123456', '+996 555 123 456',
    '+996-555-123-456', '(0555) 123456', '996 555 123456', '+996555 123456',
    '0 555 123 456', '+996 (555) 123-456', '0555-123-456', '996-555-123456',
]


def test_otp_verify_limit_ignores_phone_format(api, db):
    challenge = post(api, OTP_REQUEST, {'phone': '+996555123456'}).data['data']['challenge_id']

    statuses = [
        post(api, OTP_VERIFY, {'challenge_id': challenge, 'phone': fmt, 'code': '0000'}).status_code
        for fmt in PHONE_FORMATS
    ]

    # Лимит 10/час на номер: смена формата записи не даёт новых попыток.
    assert statuses[-1] == 429
    assert statuses.count(429) == len(PHONE_FORMATS) - 10


def test_pin_hard_lock_after_repeated_failures(api, client_user, settings):
    client_user.failed_pin_attempts = settings.PIN_HARD_LOCK_ATTEMPTS - 1
    client_user.save(update_fields=['failed_pin_attempts'])

    r = post(api, PIN_LOGIN, {'phone': client_user.phone, 'pin': '9999'})
    assert r.data['error']['code'] == 'PIN_INVALID'

    client_user.refresh_from_db()
    assert client_user.is_pin_locked
    assert (client_user.pin_locked_until - timezone.now()).days > 365, \
        'жёсткая блокировка снимается только сбросом PIN, а не временем'


def test_pin_counter_survives_short_lockout(client_user, settings):
    for _ in range(settings.PIN_MAX_ATTEMPTS):
        client_user.register_failed_pin()
    assert client_user.is_pin_locked
    assert client_user.failed_pin_attempts == settings.PIN_MAX_ATTEMPTS


def test_client_ip_ignores_forwarded_for_without_proxy(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, 'NUM_PROXIES': None}
    request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='6.6.6.6', REMOTE_ADDR='10.0.0.1')
    assert client_ip(request) == '10.0.0.1'


def test_client_ip_takes_hop_added_by_nginx(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, 'NUM_PROXIES': 1}
    # Клиент прислал поддельный 6.6.6.6, nginx дописал реальный 203.0.113.7.
    request = RequestFactory().get(
        '/', HTTP_X_FORWARDED_FOR='6.6.6.6, 203.0.113.7', REMOTE_ADDR='172.18.0.1',
    )
    assert client_ip(request) == '203.0.113.7'


def test_phone_change_requests_are_rate_limited(client_user, db):
    client = auth_client(client_user)
    statuses = [
        post(client, PHONE_CHANGE, {'new_phone': f'+99655590{i:04d}'}).status_code
        for i in range(7)
    ]
    # Иначе с одного аккаунта можно слать коды на любые номера без ограничений.
    assert statuses[-1] == 429
