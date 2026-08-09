import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

app = Celery('alasoft')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Scheduler (ТЗ backend §26)
app.conf.beat_schedule = {
    'expire-cashback-lots': {
        'task': 'loyalty.expire_cashback_lots',
        'schedule': crontab(minute='*/15'),
    },
    'notify-expiring-cashback': {
        'task': 'loyalty.notify_expiring_cashback',
        'schedule': crontab(hour=9, minute=0),
    },
    'cleanup-expired-redemptions': {
        'task': 'loyalty.cleanup_expired_redemptions',
        'schedule': crontab(minute='*'),
    },
    'cleanup-otp-challenges': {
        'task': 'users.cleanup_otp_challenges',
        'schedule': crontab(minute=0, hour='*'),
    },
    'cleanup-idempotency-records': {
        'task': 'common.cleanup_idempotency_records',
        'schedule': crontab(minute=30, hour=3),
    },
}
