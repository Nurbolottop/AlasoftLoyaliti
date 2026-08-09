from celery import shared_task

from apps.users.services import cleanup_otp_challenges as _cleanup_otp


@shared_task(name='users.cleanup_otp_challenges')
def cleanup_otp_challenges():
    """CleanupOtpChallenges (ТЗ backend §26)."""
    return _cleanup_otp()
