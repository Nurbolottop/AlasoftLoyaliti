from django.core.management.base import BaseCommand

from apps.loyalty.services import cashback as cashback_service


class Command(BaseCommand):
    help = 'Сжигает просроченные cashback-лоты (аналог celery-задачи ExpireCashbackLots)'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        result = cashback_service.expire_lots(batch_size=options['batch_size'])
        self.stdout.write(self.style.SUCCESS(
            f"Обработано лотов: {result['lots_processed']}, сгорело: {result['amount_expired']} тыйын"
        ))
