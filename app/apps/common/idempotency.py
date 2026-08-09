"""Idempotency-Key для критических POST (ТЗ backend §2, §23)."""

import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import ConflictError, DomainError, ErrorCode
from apps.common.models import ActorType, IdempotencyRecord

IDEMPOTENCY_HEADER = 'HTTP_IDEMPOTENCY_KEY'


def _request_hash(payload) -> str:
    body = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def get_idempotency_key(request, required=True):
    key = request.META.get(IDEMPOTENCY_HEADER, '').strip()
    if not key and required:
        raise DomainError(
            code=ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            message='Требуется заголовок Idempotency-Key',
            status_code=400,
        )
    return key or None


def actor_of(request):
    user = request.user
    role = getattr(user, 'role', None)
    if role == 'USER':
        return ActorType.USER, user.id
    if role == 'ORGANIZATION_ADMIN':
        return ActorType.ADMIN, user.id
    if role == 'DIRECTOR':
        return ActorType.DIRECTOR, user.id
    return ActorType.SYSTEM, None


def run_idempotent(request, endpoint, payload, handler, required=True):
    """Выполняет handler() ровно один раз для (actor, key).

    Повтор с тем же телом отдаёт сохранённый ответ, с другим — 409
    IDEMPOTENCY_CONFLICT. Незавершённая параллельная попытка тоже 409:
    клиент должен повторить запрос позже.
    """
    key = get_idempotency_key(request, required=required)
    if key is None:
        return handler(), 200

    actor_type, actor_id = actor_of(request)
    payload_hash = _request_hash(payload)

    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                actor_type=actor_type,
                actor_id=actor_id,
                key=key,
                endpoint=endpoint,
                request_hash=payload_hash,
            )
    except IntegrityError:
        record = IdempotencyRecord.objects.filter(
            actor_type=actor_type, actor_id=actor_id, key=key
        ).first()
        if record is None:
            raise ConflictError(
                code=ErrorCode.IDEMPOTENCY_CONFLICT,
                message='Конфликт идемпотентности, повторите запрос',
            )
        if record.request_hash != payload_hash or record.endpoint != endpoint:
            raise ConflictError(
                code=ErrorCode.IDEMPOTENCY_CONFLICT,
                message='Ключ идемпотентности уже использован с другими параметрами',
            )
        if record.completed_at is None:
            raise ConflictError(
                code=ErrorCode.IDEMPOTENCY_CONFLICT,
                message='Запрос с этим ключом ещё выполняется',
            )
        return record.response_body, record.response_status

    try:
        result = handler()
    except Exception:
        # Неудачную попытку не фиксируем: клиент вправе повторить тот же ключ.
        IdempotencyRecord.objects.filter(pk=record.pk).delete()
        raise

    record.response_body = json.loads(json.dumps(result, default=str))
    record.response_status = 200
    record.completed_at = timezone.now()
    record.save(update_fields=['response_body', 'response_status', 'completed_at', 'updated_at'])
    return record.response_body, record.response_status
