"""Проверка SMS-провайдера: отправляет одно сообщение и печатает результат.

Нужна в момент подключения провайдера — чтобы не выяснять через регистрацию
живого пользователя, верны ли креды и подписан ли отправитель.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.phone import normalize_phone
from apps.notifications.providers.sms import SmsSendError, get_sms_provider


class Command(BaseCommand):
    help = 'Отправляет тестовое SMS через текущий SMS_PROVIDER'

    def add_arguments(self, parser):
        parser.add_argument('phone')
        parser.add_argument('--text', default='TOPTO: проверка отправки SMS')

    def handle(self, *args, **options):
        try:
            phone = normalize_phone(options['phone'])
        except Exception as exc:
            raise CommandError(f'Некорректный номер: {exc}')

        provider = get_sms_provider()
        self.stdout.write(f'Провайдер: {provider.name} (SMS_PROVIDER={settings.SMS_PROVIDER})')
        if provider.name == 'console':
            self.stdout.write(self.style.WARNING(
                'Это режим console: сообщение только попадёт в лог, реальной отправки не будет.'
            ))

        try:
            message_id = provider.send(phone, options['text'])
        except SmsSendError as exc:
            raise CommandError(f'Провайдер отказал: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Отправлено на {phone}. ID сообщения: {message_id or "(провайдер не вернул)"}'
        ))
