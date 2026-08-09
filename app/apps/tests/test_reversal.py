"""Отмена операций: история сохраняется, создаётся компенсация (ТЗ §13, §17)."""

import pytest

from apps.loyalty.models import (
    CashbackLot,
    Gift,
    GiftStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
    UserOrganizationState,
)
from apps.loyalty.services import cashback as cashback_service
from apps.tests.conftest import post
from apps.tests.test_cashback import SOM, give_cashback

pytestmark = pytest.mark.django_db


def test_reversal_keeps_original_and_adds_compensation(visit_admin_api, client_user, visit_org):
    """Исходная запись остаётся + компенсирующая транзакция."""
    transaction_id = post(
        visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
        idempotency_key='rev-1',
    ).data['data']['transaction_id']

    response = post(visit_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse',
                    {'reason': 'ошибка кассира'})
    assert response.status_code == 200, response.data

    original = Transaction.objects.get(pk=transaction_id)
    assert original.status == TransactionStatus.REVERSED
    assert Transaction.objects.filter(pk=transaction_id).exists()  # физически не удалена

    reversal = Transaction.objects.get(type=TransactionType.REVERSAL)
    assert reversal.related_transaction_id == original.id
    assert reversal.reason == 'ошибка кассира'
    assert reversal.actor_id is not None

    state = UserOrganizationState.objects.get(user=client_user, organization=visit_org)
    assert state.visit_progress == 0


def test_reason_is_required(visit_admin_api, client_user):
    transaction_id = post(
        visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
        idempotency_key='rev-2',
    ).data['data']['transaction_id']

    response = post(visit_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse', {})
    assert response.status_code == 422


def test_double_reversal_is_forbidden(visit_admin_api, client_user):
    transaction_id = post(
        visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
        idempotency_key='rev-3',
    ).data['data']['transaction_id']

    post(visit_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse', {'reason': 'раз'})
    second = post(visit_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse',
                  {'reason': 'два'})

    assert second.status_code == 422
    assert second.data['error']['code'] == 'OPERATION_NOT_REVERSIBLE'
    assert Transaction.objects.filter(type=TransactionType.REVERSAL).count() == 1


def test_reversing_visit_cancels_unused_gift(visit_admin_api, client_user, visit_org):
    for i in range(5):
        response = post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
                        idempotency_key=f'rev-g{i}')
    last_transaction_id = response.data['data']['transaction_id']

    gift = Gift.objects.get(user=client_user, organization=visit_org)
    assert gift.status == GiftStatus.AVAILABLE

    reverse = post(visit_admin_api, f'/api/v1/admin/transactions/{last_transaction_id}/reverse',
                   {'reason': 'случайное начисление'})
    assert reverse.status_code == 200

    gift.refresh_from_db()
    assert gift.status == GiftStatus.CANCELLED

    state = UserOrganizationState.objects.get(user=client_user, organization=visit_org)
    assert state.available_gifts == 0
    assert state.visit_progress == 4  # вернулись в точку «перед порогом»


def test_cannot_reverse_visit_whose_gift_is_used(visit_admin_api, user_api, client_user, visit_org):
    for i in range(5):
        response = post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
                        idempotency_key=f'rev-u{i}')
    last_transaction_id = response.data['data']['transaction_id']

    gift = Gift.objects.get(user=client_user, organization=visit_org)
    redemption_id = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(client_user.id)}, idempotency_key='rev-u-redeem',
    ).data['data']['redemption_id']
    post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    reverse = post(visit_admin_api, f'/api/v1/admin/transactions/{last_transaction_id}/reverse',
                   {'reason': 'поздно'})
    assert reverse.status_code == 422
    assert reverse.data['error']['code'] == 'OPERATION_NOT_REVERSIBLE'


def test_gift_redeem_reversal_returns_gift(visit_admin_api, user_api, client_user, visit_org):
    for i in range(5):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
             idempotency_key=f'rev-r{i}')
    gift = Gift.objects.get(user=client_user, organization=visit_org)
    redemption_id = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(client_user.id)}, idempotency_key='rev-r-redeem',
    ).data['data']['redemption_id']
    post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    redeem_tx = Transaction.objects.get(type=TransactionType.GIFT_REDEEM)
    response = post(visit_admin_api, f'/api/v1/admin/transactions/{redeem_tx.id}/reverse',
                    {'reason': 'подарок не выдан'})
    assert response.status_code == 200

    gift.refresh_from_db()
    assert gift.status == GiftStatus.AVAILABLE


def test_untouched_cashback_earn_reversal(cashback_admin_api, client_user, cashback_org):
    transaction_id = post(cashback_admin_api, '/api/v1/admin/cashback/earn', {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM,
    }, idempotency_key='rev-c1').data['data']['transaction_id']

    response = post(cashback_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse',
                    {'reason': 'ошибочная сумма'})
    assert response.status_code == 200

    assert cashback_service.available_amount(client_user, cashback_org) == 0
    lot = CashbackLot.objects.get(source_transaction_id=transaction_id)
    assert lot.remaining_amount == 0
    assert lot.status == 'CANCELLED'


def test_partially_spent_earn_reversal_is_escalated_to_director(
    cashback_admin_api, director_api, user_api, client_user, cashback_org
):
    """Частично потраченное начисление админ отменить не может (§17)."""
    earn_tx_id = post(cashback_admin_api, '/api/v1/admin/cashback/earn', {
        'user_id': str(client_user.id), 'purchase_total': 10_000 * SOM,
    }, idempotency_key='rev-c2').data['data']['transaction_id']

    redemption_id = post(cashback_admin_api, '/api/v1/admin/cashback/redeem-request', {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 100 * SOM,
    }, idempotency_key='rev-c2-spend').data['data']['redemption_id']
    post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    denied = post(cashback_admin_api, f'/api/v1/admin/transactions/{earn_tx_id}/reverse',
                  {'reason': 'ошибка'})
    assert denied.status_code == 422
    assert denied.data['error']['code'] == 'OPERATION_NOT_REVERSIBLE'

    forced = post(director_api, f'/api/v1/director/transactions/{earn_tx_id}/reverse',
                  {'reason': 'разбор инцидента', 'force': True})
    assert forced.status_code == 200


def test_cashback_spend_reversal_restores_lots(cashback_admin_api, user_api, client_user, cashback_org):
    lot = give_cashback(client_user, cashback_org, 500 * SOM)

    redemption_id = post(cashback_admin_api, '/api/v1/admin/cashback/redeem-request', {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 300 * SOM,
    }, idempotency_key='rev-s1').data['data']['redemption_id']
    post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    lot.refresh_from_db()
    assert lot.remaining_amount == 200 * SOM

    spend_tx = Transaction.objects.get(type=TransactionType.CASHBACK_SPEND)
    response = post(cashback_admin_api, f'/api/v1/admin/transactions/{spend_tx.id}/reverse',
                    {'reason': 'клиент отменил покупку'})
    assert response.status_code == 200

    lot.refresh_from_db()
    assert lot.remaining_amount == 500 * SOM

    reversal = Transaction.objects.filter(type=TransactionType.REVERSAL).latest('created_at')
    assert reversal.metadata['restored_amount'] == 300 * SOM


def test_expire_transaction_is_not_reversible(client_user, cashback_org, director_api):
    lot = give_cashback(client_user, cashback_org, 50 * SOM)
    from django.utils import timezone
    from datetime import timedelta

    lot.expires_at = timezone.now() - timedelta(seconds=1)
    lot.save(update_fields=['expires_at'])
    cashback_service.expire_lots()

    expire_tx = Transaction.objects.get(type=TransactionType.CASHBACK_EXPIRE)
    response = post(director_api, f'/api/v1/director/transactions/{expire_tx.id}/reverse',
                    {'reason': 'вернуть'})
    assert response.status_code == 422
    assert response.data['error']['code'] == 'OPERATION_NOT_REVERSIBLE'


def test_reversal_is_written_to_audit(visit_admin_api, client_user):
    from apps.audit.models import AuditAction, AuditLog

    transaction_id = post(
        visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
        idempotency_key='rev-audit',
    ).data['data']['transaction_id']
    post(visit_admin_api, f'/api/v1/admin/transactions/{transaction_id}/reverse',
         {'reason': 'проверка аудита'})

    entry = AuditLog.objects.filter(action=AuditAction.TRANSACTION_REVERSED).first()
    assert entry is not None
    assert entry.reason == 'проверка аудита'
    assert entry.before['status'] == 'COMPLETED'
    assert entry.after['status'] == 'REVERSED'
