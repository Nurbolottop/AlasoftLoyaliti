"""Регистрация, PIN-вход, ротация токенов (ТЗ backend §5, критерии приёмки §21)."""

import pytest

from apps.tests.conftest import post
from apps.users.models import OtpChallenge, OtpStatus, User

pytestmark = pytest.mark.django_db

OTP_REQUEST = '/api/v1/auth/otp/request'
OTP_VERIFY = '/api/v1/auth/otp/verify'
REGISTER = '/api/v1/auth/register/complete'
PIN_LOGIN = '/api/v1/auth/pin/login'
PIN_RESET = '/api/v1/auth/pin/reset'
REFRESH = '/api/v1/auth/refresh'


def _otp(api, phone, purpose='REGISTER'):
    response = post(api, OTP_REQUEST, {'phone': phone, 'purpose': purpose})
    assert response.status_code == 200, response.data
    data = response.data['data']
    return data['challenge_id'], data['debug_code']


def test_full_registration_flow(api):
    """SMS → 6-значный код + QR → PIN → повторный PIN-вход."""
    phone = '+996555112233'
    challenge_id, code = _otp(api, phone)

    verify = post(api, OTP_VERIFY, {'challenge_id': challenge_id, 'phone': phone, 'code': code})
    assert verify.status_code == 200, verify.data
    token = verify.data['data']['verification_token']

    register = post(api, REGISTER, {
        'phone': phone, 'verification_token': token, 'pin': '1111',
        'first_name': 'Азамат', 'language': 'ky',
        'device': {'device_id': 'dev-1', 'platform': 'android', 'fcm_token': 'tok'},
    })
    assert register.status_code == 201, register.data
    user_data = register.data['data']['user']
    assert len(user_data['public_code']) == 6
    assert user_data['qr_payload'].startswith('alasoft://u/')
    # QR не должен раскрывать телефон или имя
    assert phone not in user_data['qr_payload']
    assert 'Азамат' not in user_data['qr_payload']

    login = post(api, PIN_LOGIN, {'phone': phone, 'pin': '1111', 'device': {'device_id': 'dev-1'}})
    assert login.status_code == 200, login.data
    assert login.data['data']['tokens']['access']


def test_public_code_is_globally_unique(api, client_user, other_client_user):
    assert client_user.public_code != other_client_user.public_code
    assert client_user.qr_token != other_client_user.qr_token


def test_otp_code_is_not_stored_in_plaintext(api):
    phone = '+996555112244'
    _, code = _otp(api, phone)
    challenge = OtpChallenge.objects.filter(phone=phone).first()
    assert code not in challenge.code_hash
    assert challenge.code_hash != code


def test_wrong_otp_code_rejected(api):
    phone = '+996555112255'
    challenge_id, code = _otp(api, phone)
    wrong = '000000' if code != '000000' else '111111'

    response = post(api, OTP_VERIFY, {'challenge_id': challenge_id, 'phone': phone, 'code': wrong})
    assert response.status_code == 422
    assert response.data['error']['code'] == 'OTP_INVALID'


def test_verification_token_is_single_use(api):
    phone = '+996555112266'
    challenge_id, code = _otp(api, phone)
    token = post(api, OTP_VERIFY, {
        'challenge_id': challenge_id, 'phone': phone, 'code': code
    }).data['data']['verification_token']

    payload = {'phone': phone, 'verification_token': token, 'pin': '2222'}
    assert post(api, REGISTER, payload).status_code == 201
    repeat = post(api, REGISTER, payload)
    assert repeat.status_code == 403
    assert repeat.data['error']['code'] == 'OTP_VERIFICATION_REQUIRED'


def test_pin_login_locks_after_max_attempts(api, client_user, settings):
    for _ in range(settings.PIN_MAX_ATTEMPTS):
        response = post(api, PIN_LOGIN, {'phone': client_user.phone, 'pin': '9999'})
        assert response.status_code == 401

    locked = post(api, PIN_LOGIN, {'phone': client_user.phone, 'pin': '1234'})
    assert locked.status_code == 429
    assert locked.data['error']['code'] == 'PIN_LOCKED'


def test_pin_is_hashed(client_user):
    assert client_user.pin_hash
    assert '1234' not in client_user.pin_hash


def test_pin_reset_requires_otp(api, client_user):
    challenge_id, code = _otp(api, client_user.phone, purpose='PIN_RESET')
    token = post(api, OTP_VERIFY, {
        'challenge_id': challenge_id, 'phone': client_user.phone, 'code': code
    }).data['data']['verification_token']

    response = post(api, PIN_RESET, {
        'phone': client_user.phone, 'verification_token': token, 'pin': '4321'
    })
    assert response.status_code == 200
    assert post(api, PIN_LOGIN, {'phone': client_user.phone, 'pin': '4321'}).status_code == 200


def test_pin_reset_without_verification_is_rejected(api, client_user):
    response = post(api, PIN_RESET, {
        'phone': client_user.phone, 'verification_token': 'fake-token', 'pin': '4321'
    })
    assert response.status_code == 403


def test_refresh_rotates_and_blacklists_old_token(api, client_user):
    login = post(api, PIN_LOGIN, {'phone': client_user.phone, 'pin': '1234'})
    refresh = login.data['data']['tokens']['refresh']

    first = post(api, REFRESH, {'refresh': refresh})
    assert first.status_code == 200
    assert first.data['data']['refresh'] != refresh

    reused = post(api, REFRESH, {'refresh': refresh})
    assert reused.status_code == 401
    assert reused.data['error']['code'] == 'TOKEN_INVALID'


def test_login_for_unknown_phone_does_not_leak_existence(api):
    response = post(api, PIN_LOGIN, {'phone': '+996555999888', 'pin': '1234'})
    assert response.status_code == 401
    assert response.data['error']['code'] == 'INVALID_CREDENTIALS'


def test_director_login_with_password(api, director):
    response = post(api, '/api/v1/auth/director/login', {
        'phone': director.phone, 'password': 'DirectorPass1!'
    })
    assert response.status_code == 200
    assert response.data['data']['user']['role'] == 'DIRECTOR'


def test_phone_is_normalized_to_e164(api):
    challenge_id, code = _otp(api, '0555 11-22-77')
    challenge = OtpChallenge.objects.get(pk=challenge_id)
    assert challenge.phone == '+996555112277'


def test_me_requires_authentication(api):
    assert api.get('/api/v1/me').status_code == 401
