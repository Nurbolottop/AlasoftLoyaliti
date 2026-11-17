import logging

from apps.audit.models import AuditLog
from apps.common.net import client_ip
from apps.common.logging import get_request_id, scrub
from apps.common.models import ActorType

logger = logging.getLogger('alasoft.audit')


def actor_from(user):
    role = getattr(user, 'role', None)
    if getattr(user, '_acting_staff', None) is not None:
        # Действует как сотрудник заведения, даже если в профиле роль USER
        return ActorType.ADMIN, user.id, user.phone
    if role == 'USER':
        return ActorType.USER, user.id, user.phone
    if role == 'ORGANIZATION_ADMIN':
        return ActorType.ADMIN, user.id, user.phone
    if role == 'DIRECTOR':
        return ActorType.DIRECTOR, user.id, user.phone
    return ActorType.SYSTEM, None, ''


def log_action(action, *, actor=None, request=None, entity_type='', entity_id='',
               organization=None, before=None, after=None, reason='', actor_type=None,
               actor_id=None, actor_phone=''):
    """Пишет запись аудита. Никогда не роняет бизнес-операцию."""
    try:
        if actor is not None:
            actor_type, actor_id, actor_phone = actor_from(actor)
        return AuditLog.objects.create(
            actor_type=actor_type or ActorType.SYSTEM,
            actor_id=actor_id,
            actor_phone=actor_phone or '',
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id or ''),
            organization=organization,
            before=scrub(before or {}),
            after=scrub(after or {}),
            reason=reason or '',
            ip_address=client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '')[:255] if request else ''),
            request_id=get_request_id(),
        )
    except Exception:
        logger.exception('Не удалось записать audit log для действия %s', action)
        return None
