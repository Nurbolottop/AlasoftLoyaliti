"""Reversal engine (ТЗ backend §17).

Ничего не удаляется и не правится задним числом: отмена всегда создаёт
связанную компенсирующую транзакцию с причиной и актором, а исходная запись
переводится в статус REVERSED.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import DomainError, ErrorCode
from apps.common.models import ActorType
from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Gift,
    GiftStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from apps.loyalty.services.state import lock_state
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_user

REVERSIBLE_TYPES = {
    TransactionType.VISIT_EARN,
    TransactionType.CASHBACK_EARN,
    TransactionType.CASHBACK_SPEND,
    TransactionType.GIFT_REDEEM,
}


def _not_reversible(message, details=None):
    return DomainError(
        code=ErrorCode.OPERATION_NOT_REVERSIBLE,
        message=message,
        details=details or {},
    )


@transaction.atomic
def reverse_transaction(*, transaction_id, actor, reason, organization=None, force=False, request=None):
    """Отменяет операцию, создавая компенсирующую запись.

    ``force`` доступен только директору и разрешает отмену частично
    израсходованного начисления (эскалация из §17).
    """
    if not reason or not str(reason).strip():
        raise DomainError(
            code=ErrorCode.VALIDATION_ERROR,
            message='Причина отмены обязательна',
            status_code=400,
        )

    original = (
        Transaction.objects.select_for_update()
        .filter(pk=transaction_id)
        .select_related('organization', 'user')
        .first()
    )
    if original is None:
        raise DomainError(code=ErrorCode.TRANSACTION_NOT_FOUND, message='Транзакция не найдена', status_code=404)

    if organization is not None and original.organization_id != organization.id:
        # Tenant isolation: admin не может отменить чужую операцию.
        raise DomainError(code=ErrorCode.TRANSACTION_NOT_FOUND, message='Транзакция не найдена', status_code=404)

    if original.status == TransactionStatus.REVERSED:
        raise _not_reversible('Операция уже отменена')
    if original.status != TransactionStatus.COMPLETED:
        raise _not_reversible('Отменить можно только завершённую операцию', {'status': original.status})
    if original.type not in REVERSIBLE_TYPES:
        raise _not_reversible('Операция этого типа не отменяется', {'type': original.type})

    state = lock_state(original.user, original.organization)
    handler = {
        TransactionType.VISIT_EARN: _reverse_visit,
        TransactionType.CASHBACK_EARN: _reverse_cashback_earn,
        TransactionType.CASHBACK_SPEND: _reverse_cashback_spend,
        TransactionType.GIFT_REDEEM: _reverse_gift_redeem,
    }[original.type]

    actor_type, actor_id = _actor(actor)
    compensation_meta = handler(original=original, state=state, force=force)

    reversal_tx = Transaction.objects.create(
        user=original.user,
        organization=original.organization,
        actor_type=actor_type,
        actor_id=actor_id,
        type=TransactionType.REVERSAL,
        amount=compensation_meta.get('amount'),
        status=TransactionStatus.COMPLETED,
        related_transaction=original,
        reason=str(reason)[:255],
        metadata={
            'original_transaction_id': str(original.id),
            'original_type': original.type,
            'forced': bool(force),
            **compensation_meta.get('metadata', {}),
        },
    )

    original.status = TransactionStatus.REVERSED
    original.save(update_fields=['status'])

    state.touch()
    state.save()

    log_action(
        AuditAction.TRANSACTION_REVERSED, actor=actor, request=request,
        entity_type='Transaction', entity_id=original.id,
        organization=original.organization, reason=str(reason)[:255],
        before={'type': original.type, 'amount': original.amount, 'status': 'COMPLETED'},
        after={'status': 'REVERSED', 'reversal_transaction_id': str(reversal_tx.id)},
    )
    notify_user(
        original.user, NotificationEvent.TRANSACTION_REVERSED,
        organization=original.organization,
        context={'transaction_id': str(original.id), 'reason': str(reason)[:120]},
    )

    return {'original': original, 'reversal': reversal_tx, 'state': state}


def _actor(actor):
    role = getattr(actor, 'role', None)
    if role == 'DIRECTOR':
        return ActorType.DIRECTOR, actor.id
    if role == 'ORGANIZATION_ADMIN':
        return ActorType.ADMIN, actor.id
    if role == 'USER':
        return ActorType.USER, actor.id
    return ActorType.SYSTEM, None


def _reverse_visit(*, original, state, force=False):
    """Компенсируем прогресс; связанный неиспользованный подарок аннулируем."""
    gift_ids = original.metadata.get('gifts_created') or []
    cancelled = []

    for gift_id in gift_ids:
        gift = Gift.objects.select_for_update().filter(pk=gift_id).first()
        if gift is None:
            continue
        if gift.status in (GiftStatus.USED, GiftStatus.PENDING_REDEMPTION):
            raise _not_reversible(
                'Подарок из этой операции уже использован или ожидает подтверждения',
                {'gift_id': str(gift.id), 'gift_status': gift.status},
            )
        if gift.status == GiftStatus.AVAILABLE:
            gift.status = GiftStatus.CANCELLED
            gift.cancelled_at = timezone.now()
            gift.save(update_fields=['status', 'cancelled_at', 'updated_at'])
            cancelled.append(str(gift.id))
            state.available_gifts = max(0, state.available_gifts - 1)
            state.total_gifts_earned = max(0, state.total_gifts_earned - 1)

    target = int(original.metadata.get('target_visits') or 0)
    if cancelled and target:
        # Подарок отменён — возвращаем прогресс в точку «на 1 меньше порога».
        state.visit_progress = max(0, state.visit_progress + target - 1)
    else:
        state.visit_progress = max(0, state.visit_progress - 1)

    state.total_visits = max(0, state.total_visits - 1)

    return {
        'amount': None,
        'metadata': {'cancelled_gifts': cancelled, 'progress_after': state.visit_progress},
    }


def _reverse_cashback_earn(*, original, state, force=False):
    """Уменьшаем исходный лот. Частично потраченный — только force директора."""
    lot_id = original.metadata.get('lot_id')
    lot = CashbackLot.objects.select_for_update().filter(pk=lot_id).first() if lot_id else None
    if lot is None:
        lot = CashbackLot.objects.select_for_update().filter(source_transaction=original).first()
    if lot is None:
        raise _not_reversible('Не найдено начисление для отмены')

    amount = int(original.amount or 0)
    spent_part = int(lot.original_amount) - int(lot.remaining_amount)

    if spent_part > 0 and not force:
        raise _not_reversible(
            'Часть начисления уже потрачена — отмена только через директора',
            {'lot_id': str(lot.id), 'spent_part': spent_part},
        )

    withdrawn = int(lot.remaining_amount)
    lot.remaining_amount = 0
    lot.status = CashbackLotStatus.CANCELLED
    lot.metadata = {**(lot.metadata or {}), 'cancelled_by_reversal_of': str(original.id)}
    lot.save(update_fields=['remaining_amount', 'status', 'metadata', 'updated_at'])

    state.cashback_available = max(0, state.cashback_available - withdrawn)
    state.cashback_total_earned = max(0, state.cashback_total_earned - amount)

    return {
        'amount': -withdrawn if withdrawn else 0,
        'metadata': {
            'lot_id': str(lot.id),
            'withdrawn': withdrawn,
            'unrecoverable_spent_part': spent_part,
        },
    }


def _reverse_cashback_spend(*, original, state, force=False):
    """Возвращаем стоимость в исходные лоты (сроки сгорания сохраняются)."""
    consumed = original.metadata.get('consumed_lots') or []
    if not consumed:
        raise _not_reversible('Нет данных о списанных начислениях')

    restored, lost = 0, 0
    now = timezone.now()
    details = []

    for entry in consumed:
        lot = CashbackLot.objects.select_for_update().filter(pk=entry.get('lot_id')).first()
        amount = int(entry.get('amount') or 0)
        if lot is None or amount <= 0:
            continue
        if lot.expires_at <= now or lot.status == CashbackLotStatus.EXPIRED:
            # Срок уже прошёл — возвращать нечего, фиксируем в метаданных.
            lost += amount
            details.append({'lot_id': str(lot.id), 'amount': amount, 'restored': False, 'reason': 'EXPIRED'})
            continue
        if lot.status == CashbackLotStatus.CANCELLED:
            lost += amount
            details.append({'lot_id': str(lot.id), 'amount': amount, 'restored': False, 'reason': 'CANCELLED'})
            continue

        lot.remaining_amount += amount
        lot.status = CashbackLotStatus.ACTIVE
        lot.save(update_fields=['remaining_amount', 'status', 'updated_at'])
        restored += amount
        details.append({'lot_id': str(lot.id), 'amount': amount, 'restored': True})

    state.cashback_available += restored
    state.cashback_total_spent = max(0, state.cashback_total_spent - int(original.amount or 0))

    return {
        'amount': restored,
        'metadata': {'restored_lots': details, 'restored_amount': restored, 'expired_amount': lost},
    }


def _reverse_gift_redeem(*, original, state, force=False):
    gift_id = original.metadata.get('gift_id')
    gift = Gift.objects.select_for_update().filter(pk=gift_id).first() if gift_id else None
    if gift is None:
        raise _not_reversible('Подарок не найден')
    if gift.status != GiftStatus.USED:
        raise _not_reversible('Подарок не находится в статусе «использован»', {'status': gift.status})

    gift.status = GiftStatus.AVAILABLE
    gift.used_at = None
    gift.redeem_transaction = None
    gift.save(update_fields=['status', 'used_at', 'redeem_transaction', 'updated_at'])

    state.available_gifts += 1
    state.total_gifts_used = max(0, state.total_gifts_used - 1)

    return {'amount': None, 'metadata': {'restored_gift_id': str(gift.id)}}
