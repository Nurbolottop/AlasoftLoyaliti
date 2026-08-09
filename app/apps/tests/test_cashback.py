"""Cashback: формула, лимит списания, FIFO, сгорание (ТЗ backend §14-15, §31)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Transaction,
    TransactionType,
    UserOrganizationState,
)
from apps.loyalty.services import cashback as cashback_service
from apps.tests.conftest import post

pytestmark = pytest.mark.django_db

QUOTE = '/api/v1/admin/cashback/quote'
EARN = '/api/v1/admin/cashback/earn'
REDEEM = '/api/v1/admin/cashback/redeem-request'

SOM = 100  # тыйын в соме


def give_cashback(user, organization, amount, *, days=90):
    """Прямое создание лота для подготовки сценария."""
    now = timezone.now()
    lot = CashbackLot.objects.create(
        user=user, organization=organization,
        original_amount=amount, remaining_amount=amount,
        earned_at=now, expires_at=now + timedelta(days=days),
    )
    state, _ = UserOrganizationState.objects.get_or_create(user=user, organization=organization)
    state.cashback_available += amount
    state.cashback_total_earned += amount
    state.save()
    return lot


def test_quote_formula_5_percent_1000_spend_300(cashback_admin_api, client_user, cashback_org):
    """Ставка 5%, чек 1000 сом, списание 300 → cash_paid 700, earn 35."""
    give_cashback(client_user, cashback_org, 500 * SOM)

    response = post(cashback_admin_api, QUOTE, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 300 * SOM,
    })
    assert response.status_code == 200, response.data
    data = response.data['data']

    assert data['cash_paid'] == 700 * SOM
    assert data['earn_amount'] == 35 * SOM
    assert data['max_allowed_spend'] == 300 * SOM  # 30% от 1000
    assert data['is_spend_allowed'] is True


def test_spend_above_30_percent_is_domain_error(cashback_admin_api, client_user, cashback_org):
    """Списание сверх лимита чека → 422 CASHBACK_LIMIT_EXCEEDED."""
    give_cashback(client_user, cashback_org, 1000 * SOM)

    response = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 400 * SOM,
    }, idempotency_key='over-limit')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'CASHBACK_LIMIT_EXCEEDED'
    assert response.data['error']['details']['max_allowed_spend'] == 300 * SOM


def test_spend_above_balance_is_rejected(cashback_admin_api, client_user, cashback_org):
    give_cashback(client_user, cashback_org, 50 * SOM)

    response = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 100 * SOM,
    }, idempotency_key='no-money')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'INSUFFICIENT_CASHBACK'


def test_earn_without_spend_needs_no_confirmation(cashback_admin_api, client_user, cashback_org):
    response = post(cashback_admin_api, EARN, {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM,
    }, idempotency_key='earn-1')

    assert response.status_code == 200, response.data
    assert response.data['data']['earn_amount'] == 50 * SOM
    assert response.data['data']['cashback_available'] == 50 * SOM

    lot = CashbackLot.objects.get(user=client_user, organization=cashback_org)
    assert lot.remaining_amount == 50 * SOM
    assert lot.expires_at > timezone.now() + timedelta(days=89)


def test_spend_requires_confirmation_then_applies(cashback_admin_api, user_api, client_user, cashback_org):
    give_cashback(client_user, cashback_org, 500 * SOM)

    request_response = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 300 * SOM,
    }, idempotency_key='spend-1')
    assert request_response.status_code == 200, request_response.data
    redemption_id = request_response.data['data']['redemption_id']

    # До подтверждения баланс не тронут
    assert cashback_service.available_amount(client_user, cashback_org) == 500 * SOM
    assert not Transaction.objects.filter(type=TransactionType.CASHBACK_SPEND).exists()

    confirm = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    assert confirm.status_code == 200, confirm.data

    # 500 − 300 списано + 35 начислено
    assert cashback_service.available_amount(client_user, cashback_org) == 235 * SOM
    assert Transaction.objects.filter(type=TransactionType.CASHBACK_SPEND).count() == 1
    assert Transaction.objects.get(type=TransactionType.CASHBACK_EARN).amount == 35 * SOM


def test_double_confirm_spends_once(cashback_admin_api, user_api, client_user, cashback_org):
    """Double confirm → одно списание."""
    give_cashback(client_user, cashback_org, 500 * SOM)
    redemption_id = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 300 * SOM,
    }, idempotency_key='dbl').data['data']['redemption_id']

    first = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    second = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    assert first.status_code == 200
    assert second.status_code == 200
    assert Transaction.objects.filter(type=TransactionType.CASHBACK_SPEND).count() == 1
    assert cashback_service.available_amount(client_user, cashback_org) == 235 * SOM


def test_fifo_spends_lot_closest_to_expiry(cashback_admin_api, user_api, client_user, cashback_org):
    far = give_cashback(client_user, cashback_org, 200 * SOM, days=80)
    near = give_cashback(client_user, cashback_org, 200 * SOM, days=5)

    redemption_id = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id),
        'purchase_total': 1000 * SOM,
        'spend_amount': 250 * SOM,
    }, idempotency_key='fifo').data['data']['redemption_id']
    post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')

    near.refresh_from_db()
    far.refresh_from_db()
    assert near.remaining_amount == 0
    assert near.status == CashbackLotStatus.SPENT
    assert far.remaining_amount == 150 * SOM


def test_expired_lot_is_not_available(client_user, cashback_org):
    lot = give_cashback(client_user, cashback_org, 100 * SOM)
    lot.expires_at = timezone.now() - timedelta(seconds=1)
    lot.save(update_fields=['expires_at'])

    assert cashback_service.available_amount(client_user, cashback_org) == 0


def test_expiry_job_burns_lot_and_writes_ledger(client_user, cashback_org):
    """Expiry 50 → −50 available и транзакция CASHBACK_EXPIRE."""
    lot = give_cashback(client_user, cashback_org, 50 * SOM)
    lot.expires_at = timezone.now() - timedelta(seconds=1)
    lot.save(update_fields=['expires_at'])

    result = cashback_service.expire_lots()
    assert result['amount_expired'] == 50 * SOM

    lot.refresh_from_db()
    assert lot.status == CashbackLotStatus.EXPIRED
    assert lot.remaining_amount == 0

    state = UserOrganizationState.objects.get(user=client_user, organization=cashback_org)
    assert state.cashback_available == 0
    assert state.cashback_total_expired == 50 * SOM
    assert Transaction.objects.filter(type=TransactionType.CASHBACK_EXPIRE).count() == 1


def test_expiry_job_is_idempotent(client_user, cashback_org):
    lot = give_cashback(client_user, cashback_org, 50 * SOM)
    lot.expires_at = timezone.now() - timedelta(seconds=1)
    lot.save(update_fields=['expires_at'])

    cashback_service.expire_lots()
    second_run = cashback_service.expire_lots()

    assert second_run['amount_expired'] == 0
    assert Transaction.objects.filter(type=TransactionType.CASHBACK_EXPIRE).count() == 1


def test_money_is_stored_as_integers(cashback_admin_api, client_user):
    post(cashback_admin_api, EARN, {
        'user_id': str(client_user.id), 'purchase_total': 99 * SOM + 99,
    }, idempotency_key='int-check')

    transaction = Transaction.objects.get(type=TransactionType.CASHBACK_EARN)
    assert isinstance(transaction.amount, int)
    # 5% от 9999 тыйын = 499.95 → округление вниз до 499
    assert transaction.amount == 499


def test_cashback_of_one_org_is_not_spendable_in_another(
    cashback_admin_api, client_user, cashback_org, visit_org, category, director
):
    from apps.organizations.models import CashbackProgram, LoyaltyType, Organization

    other = Organization.objects.create(
        name='Другой маркет', slug='other-market', category=category,
        loyalty_type=LoyaltyType.CASHBACK, created_by=director,
    )
    CashbackProgram.objects.create(organization=other)
    give_cashback(client_user, other, 1000 * SOM)

    # Админ cashback_org видит нулевой баланс: лоты чужой организации не считаются
    response = post(cashback_admin_api, QUOTE, {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 0,
    })
    assert response.data['data']['available_cashback'] == 0


def test_quote_does_not_change_state(cashback_admin_api, client_user, cashback_org):
    give_cashback(client_user, cashback_org, 500 * SOM)
    post(cashback_admin_api, QUOTE, {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 300 * SOM,
    })
    assert cashback_service.available_amount(client_user, cashback_org) == 500 * SOM
    assert not Transaction.objects.exists()


def test_pending_redemption_expires(cashback_admin_api, client_user, cashback_org, settings):
    from apps.loyalty.models import RedemptionRequest, RedemptionStatus
    from apps.loyalty.services import redemptions as redemption_service

    give_cashback(client_user, cashback_org, 500 * SOM)
    redemption_id = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 100 * SOM,
    }, idempotency_key='exp').data['data']['redemption_id']

    redemption = RedemptionRequest.objects.get(pk=redemption_id)
    redemption.expires_at = timezone.now() - timedelta(seconds=1)
    redemption.save(update_fields=['expires_at'])

    assert redemption_service.expire_pending()['expired'] == 1
    redemption.refresh_from_db()
    assert redemption.status == RedemptionStatus.EXPIRED


def test_confirm_after_expiry_is_rejected(cashback_admin_api, user_api, client_user, cashback_org):
    from apps.loyalty.models import RedemptionRequest

    give_cashback(client_user, cashback_org, 500 * SOM)
    redemption_id = post(cashback_admin_api, REDEEM, {
        'user_id': str(client_user.id), 'purchase_total': 1000 * SOM, 'spend_amount': 100 * SOM,
    }, idempotency_key='late').data['data']['redemption_id']

    RedemptionRequest.objects.filter(pk=redemption_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    response = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    assert response.status_code == 422
    assert response.data['error']['code'] == 'REDEMPTION_EXPIRED'
