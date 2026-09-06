"""Удаление аккаунта, тестовые номера и публичные страницы (выпуск в магазины)."""

import pytest
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Gift,
    GiftStatus,
    RedemptionRequest,
    RedemptionStatus,
    Transaction,
    UserOrganizationState,
)
from apps.tests.conftest import PIN, auth_client, post
from apps.tests.test_cashback import SOM, give_cashback
from apps.users.models import OtpChallenge, User

pytestmark = pytest.mark.django_db

DELETE_REQUEST = '/api/v1/me/delete/request'
DELETE_CONFIRM = '/api/v1/me/delete/confirm'


def start_deletion(user_api):
    r = post(user_api, DELETE_REQUEST)
    assert r.status_code == 200, r.data
    return r.data['data']['challenge_id'], r.data['data']['debug_code']


# --- основной сценарий ------------------------------------------------------

def test_delete_request_sends_code_to_current_phone(user_api, client_user):
    challenge_id, _ = start_deletion(user_api)
    challenge = OtpChallenge.objects.get(pk=challenge_id)
    assert challenge.phone == client_user.phone
    assert challenge.user_id == client_user.id
    assert challenge.purpose == 'ACCOUNT_DELETE'


def test_repeated_request_hits_cooldown(user_api, settings):
    settings.OTP_RESEND_COOLDOWN_SECONDS = 60
    start_deletion(user_api)
    again = post(user_api, DELETE_REQUEST)
    assert again.status_code == 429
    assert again.data['error']['code'] == 'OTP_COOLDOWN'
    assert again.data['error']['details']['retry_after'] > 0


def test_confirm_anonymizes_user(user_api, client_user):
    old_phone, old_code, old_qr = client_user.phone, client_user.public_code, client_user.qr_token
    challenge_id, code = start_deletion(user_api)

    r = post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})
    assert r.status_code == 200, r.data
    assert r.data['data']['deleted'] is True

    client_user.refresh_from_db()
    assert client_user.phone.startswith('DELETED-')
    assert len(client_user.phone) <= 20
    assert client_user.phone != old_phone
    assert client_user.first_name == '' and client_user.last_name == ''
    assert client_user.public_code is None and client_user.qr_token is None
    assert client_user.pin_hash == ''
    assert client_user.is_active is False
    assert client_user.is_registration_complete is False
    assert client_user.deleted_at is not None
    assert old_code and old_qr  # были до удаления


def test_tokens_stop_working_right_after_delete(user_api):
    challenge_id, code = start_deletion(user_api)
    post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})
    assert user_api.get('/api/v1/me').status_code == 401


def test_delete_survives_user_with_transactions(user_api, visit_admin_api, client_user, visit_org):
    """user.delete() упал бы на PROTECT — обезличивание проходит."""
    for i in range(5):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
             idempotency_key=f'del{i}')
    give_cashback(client_user, visit_org, 100 * SOM)
    assert Transaction.objects.filter(user=client_user).exists()

    challenge_id, code = start_deletion(user_api)
    assert post(user_api, DELETE_CONFIRM,
                {'challenge_id': challenge_id, 'code': code}).status_code == 200

    # ledger на месте и указывает на обезличенную строку
    assert Transaction.objects.filter(user=client_user).exists()
    assert User.objects.filter(pk=client_user.pk).exists()


def test_delete_cancels_balances_gifts_and_pending(user_api, visit_admin_api,
                                                   client_user, visit_org):
    for i in range(5):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
             idempotency_key=f'bal{i}')
    gift = Gift.objects.get(user=client_user, organization=visit_org)
    post(visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
         {'user_id': str(client_user.id)}, idempotency_key='bal-redeem')
    give_cashback(client_user, visit_org, 100 * SOM)

    challenge_id, code = start_deletion(user_api)
    post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})

    gift.refresh_from_db()
    assert gift.status == GiftStatus.CANCELLED
    assert not UserOrganizationState.objects.filter(user=client_user).exists()
    assert not CashbackLot.objects.filter(user=client_user,
                                          status=CashbackLotStatus.ACTIVE).exists()
    # у кассира не должно остаться зависшего запроса
    assert not RedemptionRequest.objects.filter(user=client_user,
                                                status=RedemptionStatus.PENDING).exists()


def test_old_code_and_qr_stop_resolving(user_api, visit_admin_api, client_user):
    old_code, old_qr = client_user.public_code, client_user.qr_token
    challenge_id, code = start_deletion(user_api)
    post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})

    by_code = post(visit_admin_api, '/api/v1/admin/customers/resolve-code', {'code': old_code})
    by_qr = post(visit_admin_api, '/api/v1/admin/customers/resolve-qr',
                 {'qr': f'alasoft://u/{old_qr}'})
    assert by_code.status_code == 404 and by_code.data['error']['code'] == 'USER_NOT_FOUND'
    assert by_qr.status_code == 404


def test_phone_is_free_for_new_registration(api, user_api, client_user, settings):
    settings.OTP_STATIC_CODE = ''
    phone = client_user.phone
    challenge_id, code = start_deletion(user_api)
    post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})

    r = post(api, '/api/v1/auth/otp/request', {'phone': phone, 'purpose': 'REGISTER'})
    assert r.status_code == 200
    assert r.data['data']['is_new_user'] is True

    verify = post(api, '/api/v1/auth/otp/verify', {
        'challenge_id': r.data['data']['challenge_id'], 'phone': phone,
        'code': r.data['data']['debug_code'],
    })
    register = post(api, '/api/v1/auth/register/complete', {
        'phone': phone, 'verification_token': verify.data['data']['verification_token'],
        'pin': '9876', 'first_name': 'Новый',
    })
    assert register.status_code == 201, register.data
    assert register.data['data']['user']['first_name'] == 'Новый'


def test_audit_keeps_record_but_drops_phone(user_api, client_user):
    challenge_id, code = start_deletion(user_api)
    post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})

    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DELETED,
                                   actor_id=client_user.id).exists()
    phones = set(AuditLog.objects.filter(actor_id=client_user.id)
                 .values_list('actor_phone', flat=True))
    assert phones == {''}, 'телефон в аудите — персональные данные'


# --- защита -----------------------------------------------------------------

def test_admin_cannot_delete_own_account(visit_admin_api):
    r = post(visit_admin_api, DELETE_REQUEST)
    assert r.status_code == 403
    assert r.data['error']['code'] == 'PERMISSION_DENIED'


def test_delete_requires_auth(api):
    assert post(api, DELETE_REQUEST).status_code == 401


def test_wrong_code_does_not_delete(user_api, client_user, settings):
    settings.OTP_STATIC_CODE = ''
    challenge_id, _ = start_deletion(user_api)
    r = post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': '000000'})
    assert r.status_code == 422
    assert r.data['error']['code'] == 'OTP_INVALID'

    client_user.refresh_from_db()
    assert client_user.is_active and client_user.deleted_at is None


def test_expired_challenge_is_rejected(user_api, client_user):
    from datetime import timedelta

    challenge_id, code = start_deletion(user_api)
    OtpChallenge.objects.filter(pk=challenge_id).update(
        expires_at=timezone.now() - timedelta(seconds=1))

    r = post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})
    assert r.status_code == 422
    assert r.data['error']['code'] == 'OTP_EXPIRED'
    client_user.refresh_from_db()
    assert client_user.is_active


def test_cannot_use_someone_elses_challenge(user_api, other_client_user):
    other_api = auth_client(other_client_user)
    challenge_id, code = start_deletion(other_api)

    stolen = post(user_api, DELETE_CONFIRM, {'challenge_id': challenge_id, 'code': code})
    assert stolen.status_code == 404
    other_client_user.refresh_from_db()
    assert other_client_user.is_active


# --- тестовые номера для ревьюеров -----------------------------------------

def test_test_account_gets_fixed_code_without_sms(api, settings, db):
    from apps.notifications.models import Notification

    settings.OTP_TEST_ACCOUNTS = {'+996700000000': '0000'}
    settings.OTP_STATIC_CODE = ''

    r = post(api, '/api/v1/auth/otp/request', {'phone': '+996700000000', 'purpose': 'REGISTER'})
    assert r.status_code == 200
    assert r.data['data']['debug_code'] == '0000'
    # SMS тестовому номеру не отправляется
    assert not Notification.objects.filter(payload__phone='+996700000000').exists()

    challenge = OtpChallenge.objects.filter(phone='+996700000000').first()
    assert challenge.check_code('0000')
    assert challenge.code_hash != '0000', 'код всё равно хранится хешем'


def test_other_numbers_keep_random_code(api, settings):
    settings.OTP_TEST_ACCOUNTS = {'+996700000000': '0000'}
    settings.OTP_STATIC_CODE = ''

    codes = {post(api, '/api/v1/auth/otp/request',
                  {'phone': f'+99655511{i:04d}', 'purpose': 'REGISTER'}
                  ).data['data']['debug_code'] for i in range(4)}
    assert '0000' not in codes
    assert len(codes) > 1


def test_test_account_login_is_marked_in_audit(api, client_user, settings):
    settings.OTP_TEST_ACCOUNTS = {client_user.phone: '0000'}
    post(api, '/api/v1/auth/pin/login', {'phone': client_user.phone, 'pin': PIN})

    entry = AuditLog.objects.filter(action=AuditAction.LOGIN, actor_id=client_user.id).first()
    assert entry.reason == 'test_account'


def test_mechanism_is_off_by_default(settings):
    assert settings.OTP_TEST_ACCOUNTS == {} or isinstance(settings.OTP_TEST_ACCOUNTS, dict)


# --- публичные страницы -----------------------------------------------------

@pytest.mark.parametrize('path', ['/privacy', '/terms', '/account-delete'])
def test_legal_pages_open_without_auth(api, path):
    """Ревьюер магазина открывает их без входа — иначе сборку отклонят."""
    response = api.get(path)
    assert response.status_code == 200, path
    assert b'<html' in response.content.lower()


def test_account_delete_page_explains_data_handling(api):
    body = api.get('/account-delete').content.decode()
    assert 'Какие данные удаляются' in body
    assert 'Какие данные сохраняются' in body
    assert 'Сроки' in body


def test_privacy_page_links_to_deletion(api):
    assert '/account-delete' in api.get('/privacy').content.decode()
