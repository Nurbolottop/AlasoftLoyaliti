from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.common.models import IdempotencyRecord


@shared_task(name='common.cleanup_idempotency_records')
def cleanup_idempotency_records():
    """Чистит отработавшие ключи идемпотентности старше срока хранения."""
    cutoff = timezone.now() - timedelta(days=settings.IDEMPOTENCY_RETENTION_DAYS)
    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
    return {'deleted': deleted}
