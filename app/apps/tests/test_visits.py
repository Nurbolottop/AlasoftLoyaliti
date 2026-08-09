"""VISIT engine и подтверждение подарка (ТЗ backend §12, §31)."""

import pytest

from apps.loyalty.models import (
    Gift,
    GiftStatus,
    RedemptionStatus,
    Transaction,
    TransactionType,
    UserOrganizationState,
)
from apps.tests.conftest import post

pytestmark = pytest.mark.django_db

VISITS = '/api/v1/admin/visits'


def earn(api, user, key=None, note=''):
    return post(api, VISITS, {'user_id': str(user.id), 'note': note}, idempotency_key=key)


def test_visit_4_of_5_plus_one_creates_gift(visit_admin_api, client_user, visit_org):
    """VISIT 4/5 +1 → прогресс 0 и Gift AVAILABLE."""
    for i in range(4):
        response = earn(visit_admin_api, client_user, key=f'k{i}')
        assert response.status_code == 200, response.data

    state = UserOrganizationState.objects.get(user=client_user, organization=visit_org)
    assert state.visit_progress == 4
    assert state.available_gifts == 0

    fifth = earn(visit_admin_api, client_user, key='k4')
    data = fifth.data['data']
    assert data['visit_progress'] == 0
    assert len(data['gifts_created']) == 1

    state.refresh_from_db()
    assert state.visit_progress == 0
    assert state.available_gifts == 1

    gift = Gift.objects.get(user=client_user, organization=visit_org)
    assert gift.status == GiftStatus.AVAILABLE


def test_repeated_idempotency_key_does_not_double_count(visit_admin_api, client_user, visit_org):
    """Повтор с тем же Idempotency-Key не даёт второго +1."""
    first = earn(visit_admin_api, client_user, key='same-key')
    second = earn(visit_admin_api, client_user, key='same-key')

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data['data']['transaction_id'] == second.data['data']['transaction_id']

    state = UserOrganizationState.objects.get(user=client_user, organization=visit_org)
    assert state.visit_progress == 1
    assert Transaction.objects.filter(type=TransactionType.VISIT_EARN).count() == 1


def test_same_key_with_different_body_conflicts(visit_admin_api, client_user, other_client_user):
    earn(visit_admin_api, client_user, key='shared')
    conflict = earn(visit_admin_api, other_client_user, key='shared')
    assert conflict.status_code == 409
    assert conflict.data['error']['code'] == 'IDEMPOTENCY_CONFLICT'


def test_gifts_accumulate_as_separate_entities(visit_admin_api, client_user, visit_org):
    for i in range(10):
        earn(visit_admin_api, client_user, key=f'acc{i}')

    gifts = Gift.objects.filter(user=client_user, organization=visit_org)
    assert gifts.count() == 2
    assert all(g.status == GiftStatus.AVAILABLE for g in gifts)


def test_gift_requires_user_confirmation(visit_admin_api, user_api, client_user, visit_org):
    """Списание подарка невозможно без подтверждения USER (критерий §21)."""
    for i in range(5):
        earn(visit_admin_api, client_user, key=f'g{i}')
    gift = Gift.objects.get(user=client_user, organization=visit_org)

    request_response = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(client_user.id)}, idempotency_key='redeem-1',
    )
    assert request_response.status_code == 200, request_response.data
    redemption_id = request_response.data['data']['redemption_id']

    gift.refresh_from_db()
    assert gift.status == GiftStatus.PENDING_REDEMPTION
    assert not Transaction.objects.filter(type=TransactionType.GIFT_REDEEM).exists()

    pending = user_api.get('/api/v1/me/redemptions/pending')
    assert len(pending.data['data']) == 1

    confirm = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    assert confirm.status_code == 200, confirm.data

    gift.refresh_from_db()
    assert gift.status == GiftStatus.USED
    assert Transaction.objects.filter(type=TransactionType.GIFT_REDEEM).count() == 1


def test_double_confirm_does_not_redeem_twice(visit_admin_api, user_api, client_user, visit_org):
    for i in range(5):
        earn(visit_admin_api, client_user, key=f'dc{i}')
    gift = Gift.objects.get(user=client_user, organization=visit_org)
    redemption_id = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(client_user.id)}, idempotency_key='dc-redeem',
    ).data['data']['redemption_id']

    first = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    second = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    assert first.status_code == 200
    assert second.status_code == 200
    assert Transaction.objects.filter(type=TransactionType.GIFT_REDEEM).count() == 1


def test_rejected_redemption_returns_gift_to_available(visit_admin_api, user_api, client_user, visit_org):
    for i in range(5):
        earn(visit_admin_api, client_user, key=f'rj{i}')
    gift = Gift.objects.get(user=client_user, organization=visit_org)
    redemption_id = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(client_user.id)}, idempotency_key='rj-redeem',
    ).data['data']['redemption_id']

    reject = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/reject', {'reason': 'передумал'})
    assert reject.status_code == 200
    assert reject.data['data']['status'] == RedemptionStatus.REJECTED

    gift.refresh_from_db()
    assert gift.status == GiftStatus.AVAILABLE


def test_visit_on_cashback_organization_is_rejected(cashback_admin_api, client_user):
    response = earn(cashback_admin_api, client_user, key='mismatch')
    assert response.status_code == 422
    assert response.data['error']['code'] == 'LOYALTY_TYPE_MISMATCH'


def test_blocked_organization_rejects_operations(visit_admin_api, client_user, visit_org):
    visit_org.status = 'BLOCKED'
    visit_org.save(update_fields=['status'])

    response = earn(visit_admin_api, client_user, key='blocked')
    # Доступ администратора заблокированной организации отзывается на входе.
    assert response.status_code in (401, 403)


def test_resolve_by_code_and_qr(visit_admin_api, client_user):
    by_code = post(visit_admin_api, '/api/v1/admin/customers/resolve-code',
                   {'code': client_user.public_code})
    assert by_code.status_code == 200
    assert by_code.data['data']['customer']['id'] == str(client_user.id)

    by_qr = post(visit_admin_api, '/api/v1/admin/customers/resolve-qr',
                 {'qr': f'alasoft://u/{client_user.qr_token}'})
    assert by_qr.status_code == 200
    assert by_qr.data['data']['customer']['id'] == str(client_user.id)
    # Полный телефон клиента администратору не отдаётся
    assert '***' in by_qr.data['data']['customer']['phone_masked']


def test_resolve_unknown_code(visit_admin_api):
    response = post(visit_admin_api, '/api/v1/admin/customers/resolve-code', {'code': '000000'})
    assert response.status_code == 404
    assert response.data['error']['code'] == 'USER_NOT_FOUND'
