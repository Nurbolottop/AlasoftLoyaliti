"""Демо-данные для разработки и приёмки (ТЗ backend §32: seed)."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.organizations.models import (
    CashbackProgram,
    Category,
    LoyaltyType,
    Organization,
    OrganizationAdmin,
    VisitProgram,
)
from apps.users.models import Role, User

CATEGORIES = [
    ('cafe', 'Кафе и рестораны', 'Кафе жана ресторандар', 1),
    ('beauty', 'Красота', 'Сулуулук', 2),
    ('barbershop', 'Барбершоп', 'Барбершоп', 3),
    ('carwash', 'Автомойка', 'Автожуучу жай', 4),
    ('shop', 'Магазины', 'Дүкөндөр', 5),
    ('service', 'Услуги', 'Кызматтар', 6),
]


class Command(BaseCommand):
    help = 'Создаёт директора, категории, две демо-организации, админов и клиента'

    def add_arguments(self, parser):
        parser.add_argument('--director-password', default='AlaSoft2026!')
        parser.add_argument('--pin', default='1234')

    @transaction.atomic
    def handle(self, *args, **options):
        pin = options['pin']

        for slug, name_ru, name_ky, order in CATEGORIES:
            Category.objects.get_or_create(
                slug=slug,
                defaults={'name_ru': name_ru, 'name_ky': name_ky, 'sort_order': order},
            )
        self.stdout.write(f'Категорий: {Category.objects.count()}')

        director, created = User.objects.get_or_create(
            phone='+996700000001',
            defaults={'role': Role.DIRECTOR, 'first_name': 'Директор', 'is_registration_complete': True,
                      'is_staff': True, 'is_superuser': True},
        )
        director.role = Role.DIRECTOR
        director.is_staff = True
        director.is_superuser = True
        director.set_password(options['director_password'])
        director.save()
        self.stdout.write(f'Директор: {director.phone} / {options["director_password"]}')

        cafe = self._organization(
            name='Кофейня Ала-Тоо', slug='ala-too-coffee', category='cafe',
            loyalty_type=LoyaltyType.VISIT, director=director,
            phone='+996312900001', address='г. Бишкек, ул. Чуй 100',
        )
        VisitProgram.objects.get_or_create(
            organization=cafe,
            defaults={'target_visits': 5, 'reward_count': 1,
                      'reward_title_ru': 'Бесплатный кофе', 'reward_title_ky': 'Акысыз кофе'},
        )

        shop = self._organization(
            name='Маркет Береке', slug='bereke-market', category='shop',
            loyalty_type=LoyaltyType.CASHBACK, director=director,
            phone='+996312900002', address='г. Бишкек, пр. Манаса 20',
        )
        CashbackProgram.objects.get_or_create(
            organization=shop,
            defaults={'cashback_rate_bps': 500, 'max_spend_percent_bps': 3000, 'expiry_days': 90},
        )

        self._admin('+996700000010', 'Админ Кофейни', cafe, director, pin)
        self._admin('+996700000011', 'Админ Маркета', shop, director, pin)

        client, _ = User.objects.get_or_create(
            phone='+996700000100',
            defaults={'role': Role.USER, 'first_name': 'Азамат', 'last_name': 'Тестов'},
        )
        client.is_registration_complete = True
        client.set_pin(pin)
        client.save()
        client.ensure_identity()

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово.\n'
            f'  Директор:  +996700000001 / {options["director_password"]}\n'
            f'  Админ VISIT:    +996700000010 / PIN {pin} → {cafe.name}\n'
            f'  Админ CASHBACK: +996700000011 / PIN {pin} → {shop.name}\n'
            f'  Клиент:    +996700000100 / PIN {pin}, код {client.public_code}\n'
        ))

    def _organization(self, *, name, slug, category, loyalty_type, director, phone, address):
        organization, _ = Organization.objects.get_or_create(
            slug=slug,
            defaults={
                'name': name,
                'category': Category.objects.filter(slug=category).first(),
                'loyalty_type': loyalty_type,
                'phone': phone,
                'address': address,
                'description_ru': f'{name} — демо-организация для проверки платформы.',
                'description_ky': f'{name} — платформаны текшерүү үчүн демо-уюм.',
                'working_hours': [
                    {'day': day, 'open': '09:00', 'close': '21:00', 'is_closed': False}
                    for day in range(1, 8)
                ],
                'created_by': director,
            },
        )
        return organization

    def _admin(self, phone, name, organization, director, pin):
        user, _ = User.objects.get_or_create(
            phone=phone,
            defaults={'role': Role.ORGANIZATION_ADMIN, 'first_name': name},
        )
        user.role = Role.ORGANIZATION_ADMIN
        user.is_registration_complete = True
        user.set_pin(pin)
        user.save()

        OrganizationAdmin.objects.filter(user=user, is_active=True).exclude(
            organization=organization
        ).update(is_active=False)
        OrganizationAdmin.objects.get_or_create(
            organization=organization, user=user,
            defaults={'created_by': director, 'is_active': True},
        )
        return user
