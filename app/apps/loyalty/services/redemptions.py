"""RedemptionRequest: подтверждение списаний пользователем (ТЗ backend §16).

Confirm — атомарный compare-and-set PENDING→CONFIRMED. Повторный confirm
возвращает прежний результат и не создаёт второе списание.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import DomainError, ErrorCode
from apps.common.models import ActorType
from apps.loyalty.models import (
    Gift,
    GiftStatus,
    RedemptionRequest,
    RedemptionStatus,
    RedemptionType,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services.state import (
    ensure_client,
    ensure_organization_operational,
    lock_state,
)
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_user
from apps.organizations.models import LoyaltyType


def _ttl():
    return timezone.now() + timedelta(seconds=settings.REDEMPTION_TTL_SECONDS)


@transaction.atomic
def create_gift_request(*, user, organization, gift_id, admin, idempotency_key='', request=None):
    ensure_client(user)
    ensure_organization_operational(organization, expected_type=LoyaltyType.VISIT)

    gift = (
        Gift.objects.select_for_update()
        .filter(pk=gift_id, user=user, organization=organization)
        .first()
    )
    if gift is None:
        raise DomainError(code=ErrorCode.GIFT_NOT_FOUND, message='Подарок не найден', status_code=404)
    if gift.status != GiftStatus.AVAILABLE:
        raise DomainError(
            code=ErrorCode.GIFT_NOT_AVAILABLE,
            message='Подарок недоступен к использованию',
            details={'status': gift.status},
        )

    redemption = RedemptionRequest.objects.create(
        user=user,
        organization=organization,
        type=RedemptionType.GIFT,
        gift=gift,
        status=RedemptionStatus.PENDING,
        expires_at=_ttl(),
        created_by_admin=admin,
        idempotency_key=idempotency_key or '',
        metadata={'gift_title_ru': gift.title_ru, 'gift_title_ky': gift.title_ky},
    )

    gift.status = GiftStatus.PENDING_REDEMPTION
    gift.save(update_fields=['status', 'updated_at'])

    log_action(
        AuditAction.REDEMPTION_CREATED, actor=admin, request=request,
        entity_type='RedemptionRequest', entity_id=redemption.id, organization=organization,
        after={'type': 'GIFT', 'gift_id': str(gift.id), 'user_id': str(user.id)},
    )
    notify_user(
        user, NotificationEvent.REDEMPTION_REQUESTED, organization=organization,
        context={'redemption_id': str(redemption.id), 'subject': gift.title(user.language), 'type': 'GIFT'},
    )
    return redemption


@transaction.atomic
def create_cashback_request(*, user, organization, purchase_total, spend_amount,
                            admin, idempotency_key='', request=None):
    ensure_client(user)
    ensure_organization_operational(organization, expected_type=LoyaltyType.CASHBACK)

    quote_data = cashback_service.quote(
        user=user, organization=organization,
        purchase_total=purchase_total, requested_spend=spend_amount,
    )
    cashback_service.validate_spend(quote_data)

    if quote_data['requested_spend'] <= 0:
        raise DomainError(
            code=ErrorCode.INVALID_AMOUNT,
            message='Для покупки без списания используйте начисление без подтверждения',
        )

    redemption = RedemptionRequest.objects.create(
        user=user,
        organization=organization,
        type=RedemptionType.CASHBACK,
        purchase_total=quote_data['purchase_total'],
        spend_amount=quote_data['requested_spend'],
        cash_paid=quote_data['cash_paid'],
        earn_amount=quote_data['earn_amount'],
        status=RedemptionStatus.PENDING,
        expires_at=_ttl(),
        created_by_admin=admin,
        idempotency_key=idempotency_key or '',
        metadata={'program': quote_data['program']},
    )

    log_action(
        AuditAction.REDEMPTION_CREATED, actor=admin, request=request,
        entity_type='RedemptionRequest', entity_id=redemption.id, organization=organization,
        after={
            'type': 'CASHBACK',
            'user_id': str(user.id),
            'purchase_total': redemption.purchase_total,
            'spend_amount': redemption.spend_amount,
            'earn_amount': redemption.earn_amount,
        },
    )
    notify_user(
        user, NotificationEvent.REDEMPTION_REQUESTED, organization=organization,
        context={
            'redemption_id': str(redemption.id),
            'subject': f'списание {redemption.spend_amount / 100:.2f}',
            'type': 'CASHBACK',
            'amount_tiyin': redemption.spend_amount,
        },
    )
    return redemption


def _serialize_result(redemption):
    return {
        'redemption_id': str(redemption.id),
        'status': redemption.status,
        'transactions': redemption.result_transactions,
    }


@transaction.atomic
def confirm(*, redemption_id, user, request=None):
    """Compare-and-set PENDING→CONFIRMED, затем проведение операции."""
    # of=('self',) обязателен: gift — nullable FK, а Postgres не умеет
    # FOR UPDATE по nullable-стороне outer join.
    redemption = (
        RedemptionRequest.objects.select_for_update(of=('self',))
        .filter(pk=redemption_id, user=user)
        .select_related('organization', 'gift')
        .first()
    )
    if redemption is None:
        raise DomainError(code=ErrorCode.REDEMPTION_NOT_FOUND, message='Запрос не найден', status_code=404)

    if redemption.status == RedemptionStatus.CONFIRMED:
        # Повторное подтверждение — тот же результат, без второго списания.
        return _serialize_result(redemption), False

    if redemption.status != RedemptionStatus.PENDING:
        raise DomainError(
            code=ErrorCode.REDEMPTION_NOT_PENDING,
            message='Запрос уже обработан',
            details={'status': redemption.status},
        )

    if redemption.is_expired:
        _expire(redemption)
        raise DomainError(code=ErrorCode.REDEMPTION_EXPIRED, message='Срок подтверждения истёк')

    organization = redemption.organization
    ensure_organization_operational(organization)

    state = lock_state(redemption.user, organization)
    transactions = []

    if redemption.type == RedemptionType.GIFT:
        gift = Gift.objects.select_for_update().get(pk=redemption.gift_id)
        if gift.status != GiftStatus.PENDING_REDEMPTION:
            raise DomainError(
                code=ErrorCode.GIFT_NOT_AVAILABLE,
                message='Подарок больше недоступен',
                details={'status': gift.status},
            )
        redeem_tx = Transaction.objects.create(
            user=redemption.user,
            organization=organization,
            actor_type=ActorType.USER,
            actor_id=redemption.user_id,
            type=TransactionType.GIFT_REDEEM,
            amount=None,
            status=TransactionStatus.COMPLETED,
            metadata={'gift_id': str(gift.id), 'redemption_id': str(redemption.id)},
        )
        gift.status = GiftStatus.USED
        gift.used_at = timezone.now()
        gift.redeem_transaction = redeem_tx
        gift.save(update_fields=['status', 'used_at', 'redeem_transaction', 'updated_at'])

        state.available_gifts = max(0, state.available_gifts - 1)
        state.total_gifts_used += 1
        state.touch()
        state.save()
        transactions.append(redeem_tx)
    else:
        transactions = cashback_service.apply_confirmed_redemption(redemption=redemption, state=state)

    redemption.status = RedemptionStatus.CONFIRMED
    redemption.resolved_at = timezone.now()
    redemption.result_transactions = [str(tx.id) for tx in transactions]
    redemption.save(update_fields=['status', 'resolved_at', 'result_transactions', 'updated_at'])

    log_action(
        AuditAction.REDEMPTION_CONFIRMED, actor=redemption.user, request=request,
        entity_type='RedemptionRequest', entity_id=redemption.id, organization=organization,
        after={'transactions': redemption.result_transactions, 'type': redemption.type},
    )
    notify_user(
        redemption.user, NotificationEvent.REDEMPTION_CONFIRMED, organization=organization,
        context={'redemption_id': str(redemption.id), 'type': redemption.type},
    )
    return _serialize_result(redemption), True


@transaction.atomic
def reject(*, redemption_id, user, reason='', request=None):
    redemption = (
        RedemptionRequest.objects.select_for_update(of=('self',))
        .filter(pk=redemption_id, user=user)
        .select_related('organization', 'gift')
        .first()
    )
    if redemption is None:
        raise DomainError(code=ErrorCode.REDEMPTION_NOT_FOUND, message='Запрос не найден', status_code=404)

    if redemption.status == RedemptionStatus.REJECTED:
        return _serialize_result(redemption), False
    if redemption.status != RedemptionStatus.PENDING:
        raise DomainError(
            code=ErrorCode.REDEMPTION_NOT_PENDING,
            message='Запрос уже обработан',
            details={'status': redemption.status},
        )

    redemption.status = RedemptionStatus.REJECTED
    redemption.reject_reason = reason or ''
    redemption.resolved_at = timezone.now()
    redemption.save(update_fields=['status', 'reject_reason', 'resolved_at', 'updated_at'])
    _release_gift(redemption)

    log_action(
        AuditAction.REDEMPTION_REJECTED, actor=redemption.user, request=request,
        entity_type='RedemptionRequest', entity_id=redemption.id,
        organization=redemption.organization, reason=reason,
    )
    notify_user(
        redemption.user, NotificationEvent.REDEMPTION_REJECTED,
        organization=redemption.organization,
        context={'redemption_id': str(redemption.id)},
    )
    return _serialize_result(redemption), True


@transaction.atomic
def cancel_by_admin(*, redemption_id, organization, admin, reason='', request=None):
    redemption = (
        RedemptionRequest.objects.select_for_update()
        .filter(pk=redemption_id, organization=organization)
        .first()
    )
    if redemption is None:
        raise DomainError(code=ErrorCode.REDEMPTION_NOT_FOUND, message='Запрос не найден', status_code=404)
    if redemption.status != RedemptionStatus.PENDING:
        raise DomainError(
            code=ErrorCode.REDEMPTION_NOT_PENDING,
            message='Запрос уже обработан',
            details={'status': redemption.status},
        )

    redemption.status = RedemptionStatus.CANCELLED
    redemption.reject_reason = reason or ''
    redemption.resolved_at = timezone.now()
    redemption.save(update_fields=['status', 'reject_reason', 'resolved_at', 'updated_at'])
    _release_gift(redemption)

    log_action(
        AuditAction.REDEMPTION_REJECTED, actor=admin, request=request,
        entity_type='RedemptionRequest', entity_id=redemption.id,
        organization=organization, reason=reason,
    )
    return redemption


def _release_gift(redemption):
    """Возвращает подарок в AVAILABLE после отклонения/истечения запроса."""
    if redemption.type != RedemptionType.GIFT or redemption.gift_id is None:
        return
    gift = Gift.objects.select_for_update().filter(pk=redemption.gift_id).first()
    if gift and gift.status == GiftStatus.PENDING_REDEMPTION:
        gift.status = GiftStatus.AVAILABLE
        gift.save(update_fields=['status', 'updated_at'])


def _expire(redemption):
    redemption.status = RedemptionStatus.EXPIRED
    redemption.resolved_at = timezone.now()
    redemption.save(update_fields=['status', 'resolved_at', 'updated_at'])
    _release_gift(redemption)


def expire_pending(*, now=None, batch_size=500):
    """CleanupExpiredRedemptions: PENDING → EXPIRED (ТЗ backend §26)."""
    now = now or timezone.now()
    ids = list(
        RedemptionRequest.objects.filter(
            status=RedemptionStatus.PENDING, expires_at__lte=now
        ).values_list('id', flat=True)[:batch_size]
    )
    expired = 0
    for redemption_id in ids:
        with transaction.atomic():
            redemption = RedemptionRequest.objects.select_for_update().filter(pk=redemption_id).first()
            if redemption is None or redemption.status != RedemptionStatus.PENDING:
                continue
            if redemption.expires_at > now:
                continue
            _expire(redemption)
            expired += 1
    return {'expired': expired}
