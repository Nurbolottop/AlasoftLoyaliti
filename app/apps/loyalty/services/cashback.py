"""Cashback engine: quote, начисление, списание FIFO, сгорание.

Вся арифметика целочисленная, суммы в тыйынах (ТЗ backend §14, §15).
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import DomainError, ErrorCode
from apps.common.models import ActorType
from apps.common.money import apply_bps
from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from apps.loyalty.services.state import (
    ensure_client,
    ensure_organization_operational,
    lock_state,
)
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_user
from apps.organizations.models import LoyaltyType


def active_lots_qs(user, organization, *, now=None):
    now = now or timezone.now()
    return CashbackLot.objects.filter(
        user=user,
        organization=organization,
        status=CashbackLotStatus.ACTIVE,
        remaining_amount__gt=0,
        expires_at__gt=now,
    )


def available_amount(user, organization, *, now=None):
    """Доступный cashback по лотам. Сгоревшие лоты не учитываются."""
    total = active_lots_qs(user, organization, now=now).aggregate(total=Sum('remaining_amount'))
    return int(total['total'] or 0)


def quote(*, user, organization, purchase_total, requested_spend=0):
    """Серверный расчёт: сколько можно списать и сколько будет начислено.

    max_allowed = min(available, purchase_total × max_spend_percent)
    cash_paid   = purchase_total − spend
    earn        = rate × cash_paid
    """
    ensure_client(user)
    program = ensure_organization_operational(organization, expected_type=LoyaltyType.CASHBACK)

    purchase_total = int(purchase_total)
    requested_spend = int(requested_spend or 0)

    if purchase_total <= 0:
        raise DomainError(code=ErrorCode.INVALID_AMOUNT, message='Сумма покупки должна быть больше нуля')
    if requested_spend < 0:
        raise DomainError(code=ErrorCode.INVALID_AMOUNT, message='Сумма списания не может быть отрицательной')
    if purchase_total < program.min_purchase_amount:
        raise DomainError(
            code=ErrorCode.INVALID_AMOUNT,
            message='Сумма покупки меньше минимальной для программы',
            details={'min_purchase_amount': program.min_purchase_amount},
        )

    available = available_amount(user, organization)
    limit_by_percent = apply_bps(purchase_total, program.max_spend_percent_bps)
    max_allowed = min(available, limit_by_percent)

    cash_paid = purchase_total - requested_spend
    earn = apply_bps(cash_paid, program.cashback_rate_bps) if cash_paid > 0 else 0

    return {
        'purchase_total': purchase_total,
        'requested_spend': requested_spend,
        'available_cashback': available,
        'limit_by_percent': limit_by_percent,
        'max_allowed_spend': max_allowed,
        'cash_paid': max(cash_paid, 0),
        'earn_amount': earn,
        'program': program.snapshot(),
        'is_spend_allowed': requested_spend <= max_allowed,
        'requires_confirmation': requested_spend > 0,
    }


def validate_spend(quote_data):
    requested = quote_data['requested_spend']
    if requested > quote_data['purchase_total']:
        raise DomainError(
            code=ErrorCode.CASHBACK_LIMIT_EXCEEDED,
            message='Списание больше суммы покупки',
            details={'max_allowed_spend': quote_data['max_allowed_spend']},
        )
    if requested > quote_data['available_cashback']:
        raise DomainError(
            code=ErrorCode.INSUFFICIENT_CASHBACK,
            message='Недостаточно cashback на балансе',
            details={
                'available_cashback': quote_data['available_cashback'],
                'requested_spend': requested,
            },
        )
    if requested > quote_data['limit_by_percent']:
        raise DomainError(
            code=ErrorCode.CASHBACK_LIMIT_EXCEEDED,
            message='Превышен лимит оплаты чека кэшбэком',
            details={
                'max_allowed_spend': quote_data['max_allowed_spend'],
                'limit_by_percent': quote_data['limit_by_percent'],
            },
        )
    return True


def _create_lot(*, user, organization, amount, program_snapshot, source_transaction, now=None):
    now = now or timezone.now()
    return CashbackLot.objects.create(
        user=user,
        organization=organization,
        original_amount=amount,
        remaining_amount=amount,
        earned_at=now,
        expires_at=now + timedelta(days=program_snapshot['expiry_days']),
        source_transaction=source_transaction,
        metadata={'program': program_snapshot},
    )


def _spend_lots_fifo(*, user, organization, amount, now=None):
    """Списывает amount из лотов по возрастанию expires_at (FIFO по сгоранию)."""
    now = now or timezone.now()
    remaining = int(amount)
    consumed = []

    lots = (
        active_lots_qs(user, organization, now=now)
        .select_for_update()
        .order_by('expires_at', 'earned_at')
    )
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot.remaining_amount, remaining)
        lot.remaining_amount -= take
        if lot.remaining_amount == 0:
            lot.status = CashbackLotStatus.SPENT
        lot.save(update_fields=['remaining_amount', 'status', 'updated_at'])
        consumed.append({'lot_id': str(lot.id), 'amount': take, 'expires_at': lot.expires_at.isoformat()})
        remaining -= take

    if remaining > 0:
        # Параллельное списание успело израсходовать баланс — откатываем всё.
        raise DomainError(
            code=ErrorCode.INSUFFICIENT_CASHBACK,
            message='Недостаточно cashback на балансе',
            details={'shortfall': remaining},
        )
    return consumed


@transaction.atomic
def earn_only(*, user, organization, purchase_total, admin, idempotency_key='', request=None):
    """Покупка без списания: сразу начисляем, подтверждение не требуется."""
    ensure_client(user)
    program = ensure_organization_operational(organization, expected_type=LoyaltyType.CASHBACK)

    state = lock_state(user, organization)
    quote_data = quote(user=user, organization=organization, purchase_total=purchase_total, requested_spend=0)
    snapshot = program.snapshot()
    earn = quote_data['earn_amount']

    earn_tx = None
    lot = None
    if earn > 0:
        earn_tx = Transaction.objects.create(
            user=user,
            organization=organization,
            actor_type=ActorType.ADMIN if admin else ActorType.SYSTEM,
            actor_id=getattr(admin, 'id', None),
            type=TransactionType.CASHBACK_EARN,
            amount=earn,
            status=TransactionStatus.COMPLETED,
            idempotency_key=idempotency_key or '',
            metadata={
                'purchase_total': int(purchase_total),
                'cash_paid': quote_data['cash_paid'],
                'spend_amount': 0,
                'program': snapshot,
            },
        )
        lot = _create_lot(
            user=user, organization=organization, amount=earn,
            program_snapshot=snapshot, source_transaction=earn_tx,
        )
        earn_tx.metadata['lot_id'] = str(lot.id)
        earn_tx.save(update_fields=['metadata'])

        state.cashback_available += earn
        state.cashback_total_earned += earn

    state.touch()
    state.save()

    log_action(
        AuditAction.CASHBACK_EARNED,
        actor=admin,
        request=request,
        entity_type='Transaction',
        entity_id=getattr(earn_tx, 'id', ''),
        organization=organization,
        after={'user_id': str(user.id), 'earn': earn, 'purchase_total': int(purchase_total)},
    )

    if earn > 0:
        notify_user(
            user, NotificationEvent.CASHBACK_EARNED, organization=organization,
            context={'amount_tiyin': earn},
        )

    return {
        'transaction': earn_tx,
        'lot': lot,
        'state': state,
        'quote': quote_data,
    }


def apply_confirmed_redemption(*, redemption, state):
    """Проводит подтверждённое пользователем списание + начисление.

    Вызывается строго внутри транзакции подтверждения, когда строка состояния
    уже заблокирована (ТЗ backend §14, шаги 6-8).
    """
    user = redemption.user
    organization = redemption.organization
    program = organization.cashback_program
    snapshot = redemption.metadata.get('program') or program.snapshot()
    now = timezone.now()

    spend = int(redemption.spend_amount or 0)
    earn = int(redemption.earn_amount or 0)
    transactions = []

    if spend > 0:
        consumed = _spend_lots_fifo(user=user, organization=organization, amount=spend, now=now)
        spend_tx = Transaction.objects.create(
            user=user,
            organization=organization,
            actor_type=ActorType.USER,
            actor_id=user.id,
            type=TransactionType.CASHBACK_SPEND,
            amount=spend,
            status=TransactionStatus.COMPLETED,
            metadata={
                'redemption_id': str(redemption.id),
                'purchase_total': redemption.purchase_total,
                'cash_paid': redemption.cash_paid,
                'consumed_lots': consumed,
                'program': snapshot,
            },
        )
        state.cashback_available -= spend
        state.cashback_total_spent += spend
        transactions.append(spend_tx)

    if earn > 0:
        earn_tx = Transaction.objects.create(
            user=user,
            organization=organization,
            actor_type=ActorType.ADMIN,
            actor_id=redemption.created_by_admin_id,
            type=TransactionType.CASHBACK_EARN,
            amount=earn,
            status=TransactionStatus.COMPLETED,
            metadata={
                'redemption_id': str(redemption.id),
                'purchase_total': redemption.purchase_total,
                'cash_paid': redemption.cash_paid,
                'spend_amount': spend,
                'program': snapshot,
            },
        )
        lot = _create_lot(
            user=user, organization=organization, amount=earn,
            program_snapshot=snapshot, source_transaction=earn_tx, now=now,
        )
        earn_tx.metadata['lot_id'] = str(lot.id)
        earn_tx.save(update_fields=['metadata'])

        state.cashback_available += earn
        state.cashback_total_earned += earn
        transactions.append(earn_tx)

    state.touch()
    state.save()
    return transactions


def expire_lots(*, now=None, batch_size=500):
    """ExpireCashbackLots: идемпотентная джоба сгорания (ТЗ backend §15, §26)."""
    now = now or timezone.now()
    expired_total = 0
    affected = []

    lot_ids = list(
        CashbackLot.objects.filter(
            status=CashbackLotStatus.ACTIVE,
            expires_at__lte=now,
        ).values_list('id', flat=True)[:batch_size]
    )

    for lot_id in lot_ids:
        with transaction.atomic():
            lot = CashbackLot.objects.select_for_update().filter(pk=lot_id).first()
            if lot is None or lot.status != CashbackLotStatus.ACTIVE or lot.expires_at > now:
                continue

            amount = int(lot.remaining_amount)
            lot.status = CashbackLotStatus.EXPIRED
            lot.expired_at = now
            lot.remaining_amount = 0
            lot.save(update_fields=['status', 'expired_at', 'remaining_amount', 'updated_at'])

            if amount <= 0:
                continue

            state = lock_state(lot.user, lot.organization)
            expire_tx = Transaction.objects.create(
                user_id=lot.user_id,
                organization_id=lot.organization_id,
                actor_type=ActorType.SYSTEM,
                type=TransactionType.CASHBACK_EXPIRE,
                amount=amount,
                status=TransactionStatus.COMPLETED,
                metadata={'lot_id': str(lot.id), 'expires_at': lot.expires_at.isoformat()},
            )
            state.cashback_available = max(0, state.cashback_available - amount)
            state.cashback_total_expired += amount
            state.save(update_fields=[
                'cashback_available', 'cashback_total_expired', 'updated_at'
            ])

            expired_total += amount
            affected.append({'lot_id': str(lot.id), 'amount': amount, 'transaction_id': str(expire_tx.id)})

            log_action(
                AuditAction.CASHBACK_EXPIRED,
                actor_type=ActorType.SYSTEM,
                entity_type='CashbackLot',
                entity_id=lot.id,
                organization=lot.organization,
                after={'amount': amount, 'user_id': str(lot.user_id)},
            )
            notify_user(
                lot.user, NotificationEvent.CASHBACK_EXPIRED, organization=lot.organization,
                context={'amount_tiyin': amount},
            )

    return {'lots_processed': len(lot_ids), 'amount_expired': expired_total, 'details': affected}


def lots_expiring_soon(*, days, now=None):
    now = now or timezone.now()
    return CashbackLot.objects.filter(
        status=CashbackLotStatus.ACTIVE,
        remaining_amount__gt=0,
        expires_at__gt=now,
        expires_at__lte=now + timedelta(days=days),
    ).select_related('user', 'organization')


def next_expiry(user, organization, *, now=None):
    lot = active_lots_qs(user, organization, now=now).order_by('expires_at').first()
    if lot is None:
        return None
    return {'amount': lot.remaining_amount, 'expires_at': lot.expires_at}


def recalculate_available(user, organization):
    """Сверка агрегата с ledger: используется в отменах и проверках."""
    return available_amount(user, organization)


__all__ = [
    'quote', 'validate_spend', 'earn_only', 'apply_confirmed_redemption',
    'expire_lots', 'available_amount', 'active_lots_qs', 'next_expiry',
    'lots_expiring_soon', 'recalculate_available',
]
