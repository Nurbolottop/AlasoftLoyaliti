from django.core.management.base import BaseCommand, CommandError

from apps.common.phone import normalize_phone
from apps.users.models import Role, User


class Command(BaseCommand):
    help = 'Создаёт аккаунт директора AlaSoft (вход по телефону и паролю)'

    def add_arguments(self, parser):
        parser.add_argument('--phone', required=True)
        parser.add_argument('--password', required=True)
        parser.add_argument('--first-name', default='')
        parser.add_argument('--last-name', default='')
        parser.add_argument('--staff', action='store_true', help='Дать доступ в django-admin')

    def handle(self, *args, **options):
        try:
            phone = normalize_phone(options['phone'])
        except Exception as exc:
            raise CommandError(f'Некорректный телефон: {exc}')

        if len(options['password']) < 8:
            raise CommandError('Пароль должен быть не короче 8 символов')

        user = User.objects.filter(phone=phone).first()
        if user and user.role != Role.DIRECTOR:
            raise CommandError(f'Номер {phone} уже занят ролью {user.role}')

        if user is None:
            user = User(phone=phone, role=Role.DIRECTOR)

        user.role = Role.DIRECTOR
        user.first_name = options['first_name'] or user.first_name
        user.last_name = options['last_name'] or user.last_name
        user.is_registration_complete = True
        user.is_active = True
        if options['staff']:
            user.is_staff = True
        user.set_password(options['password'])
        user.save()

        self.stdout.write(self.style.SUCCESS(f'Директор готов: {phone} (id={user.id})'))
