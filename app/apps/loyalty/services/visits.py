"""VISIT engine: +1 посещение и выдача подарка на пороге (ТЗ backend §12)."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.models import ActorType
from apps.loyalty.models import (
    Gift,
    GiftStatus,
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


@transaction.atomic
def earn_visit(*, user, organization, admin, idempotency_key='', request=None, note=''):
    """Начисляет посещение и, при достижении target, создаёт подарок.

    Порядок ровно как в ТЗ §12: authorize → resolve → check → lock → ledger →
    progress → gift → commit → push (после commit).
    """
    ensure_client(user)
    program = ensure_organization_operational(organization, expected_type=LoyaltyType.VISIT)

    state = lock_state(user, organization)

    visit_tx = Transaction.objects.create(
        user=user,
        organization=organization,
        actor_type=ActorType.ADMIN if admin else ActorType.SYSTEM,
        actor_id=getattr(admin, 'id', None),
        type=TransactionType.VISIT_EARN,
        amount=None,
        status=TransactionStatus.COMPLETED,
        idempotency_key=idempotency_key or '',
        reason=note or '',
        metadata={
            'target_visits': program.target_visits,
            'reward_count': program.reward_count,
            'progress_before': state.visit_progress,
        },
    )

    state.visit_progress += 1
    state.total_visits += 1

    created_gifts = []
    if state.visit_progress >= program.target_visits:
        state.visit_progress -= program.target_visits
        for _ in range(program.reward_count):
            gift_tx = Transaction.objects.create(
                user=user,
                organization=organization,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                type=TransactionType.GIFT_CREATED,
                amount=None,
                status=TransactionStatus.COMPLETED,
                related_transaction=visit_tx,
                metadata={'target_visits': program.target_visits},
            )
            gift = Gift.objects.create(
                user=user,
                organization=organization,
                status=GiftStatus.AVAILABLE,
                title_ru=program.reward_title_ru or 'Подарок',
                title_ky=program.reward_title_ky or 'Белек',
                source_transaction=gift_tx,
                metadata={'target_visits': program.target_visits, 'visit_transaction_id': str(visit_tx.id)},
            )
            gift_tx.metadata['gift_id'] = str(gift.id)
            gift_tx.save(update_fields=['metadata'])
            created_gifts.append(gift)

        state.total_gifts_earned += len(created_gifts)
        state.available_gifts += len(created_gifts)

    visit_tx.metadata['progress_after'] = state.visit_progress
    visit_tx.metadata['gifts_created'] = [str(g.id) for g in created_gifts]
    visit_tx.save(update_fields=['metadata'])

    state.touch()
    state.save()

    log_action(
        AuditAction.VISIT_EARNED,
        actor=admin,
        request=request,
        entity_type='Transaction',
        entity_id=visit_tx.id,
        organization=organization,
        after={
            'user_id': str(user.id),
            'progress': state.visit_progress,
            'gifts_created': len(created_gifts),
        },
    )

    notify_user(
        user, NotificationEvent.VISIT_EARNED, organization=organization,
        context={'progress': state.visit_progress, 'target': program.target_visits},
    )
    for gift in created_gifts:
        notify_user(
            user, NotificationEvent.GIFT_EARNED, organization=organization,
            context={'gift_id': str(gift.id)},
        )

    return {
        'transaction': visit_tx,
        'state': state,
        'gifts': created_gifts,
        'program': program,
    }
