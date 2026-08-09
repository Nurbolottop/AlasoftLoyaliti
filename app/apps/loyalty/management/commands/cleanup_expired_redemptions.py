from django.core.management.base import BaseCommand

from apps.loyalty.services import redemptions as redemption_service


class Command(BaseCommand):
    help = 'Переводит просроченные запросы на списание PENDING → EXPIRED'

    def handle(self, *args, **options):
        result = redemption_service.expire_pending()
        self.stdout.write(self.style.SUCCESS(f"Просрочено запросов: {result['expired']}"))
