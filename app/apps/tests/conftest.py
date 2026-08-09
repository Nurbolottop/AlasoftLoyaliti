import pytest
from rest_framework.test import APIClient as BaseAPIClient

from apps.organizations.models import (
    CashbackProgram,
    Category,
    LoyaltyType,
    Organization,
    OrganizationAdmin,
    VisitProgram,
)
from apps.users.models import Role, User
from apps.users.services import issue_tokens

PIN = '1234'


class APIClient(BaseAPIClient):
    """Клиент, который отдаёт итоговый JSON-конверт, а не сырой data сериализатора.

    Продакшен-ответ формирует EnvelopeJSONRenderer на этапе рендера, поэтому в
    тестах сравниваем именно то, что увидит мобильное приложение.
    """

    def generic(self, *args, **kwargs):
        response = super().generic(*args, **kwargs)
        try:
            response.data = response.json()
        except Exception:
            pass
        return response


@pytest.fixture
def api():
    return APIClient()


def auth_client(user):
    client = APIClient()
    tokens = issue_tokens(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}')
    return client


@pytest.fixture
def category(db):
    return Category.objects.create(slug='cafe', name_ru='Кафе', name_ky='Кафе')


@pytest.fixture
def director(db):
    user = User.objects.create(
        phone='+996700000001', role=Role.DIRECTOR, is_registration_complete=True
    )
    user.set_password('DirectorPass1!')
    user.save()
    return user


def _make_client(phone, first_name='Клиент'):
    user = User.objects.create(
        phone=phone, role=Role.USER, first_name=first_name, is_registration_complete=True
    )
    user.set_pin(PIN)
    user.save()
    user.ensure_identity()
    return user


@pytest.fixture
def client_user(db):
    return _make_client('+996700000100')


@pytest.fixture
def other_client_user(db):
    return _make_client('+996700000101', 'Другой')


@pytest.fixture
def visit_org(db, category, director):
    organization = Organization.objects.create(
        name='Кофейня', slug='coffee', category=category,
        loyalty_type=LoyaltyType.VISIT, created_by=director,
    )
    VisitProgram.objects.create(organization=organization, target_visits=5, reward_count=1)
    return organization


@pytest.fixture
def cashback_org(db, category, director):
    organization = Organization.objects.create(
        name='Маркет', slug='market', category=category,
        loyalty_type=LoyaltyType.CASHBACK, created_by=director,
    )
    CashbackProgram.objects.create(
        organization=organization,
        cashback_rate_bps=500,        # 5%
        max_spend_percent_bps=3000,   # 30%
        expiry_days=90,
    )
    return organization


def _make_admin(phone, organization, director):
    user = User.objects.create(
        phone=phone, role=Role.ORGANIZATION_ADMIN, is_registration_complete=True
    )
    user.set_pin(PIN)
    user.save()
    OrganizationAdmin.objects.create(organization=organization, user=user, created_by=director)
    return user


@pytest.fixture
def visit_admin(db, visit_org, director):
    return _make_admin('+996700000010', visit_org, director)


@pytest.fixture
def cashback_admin(db, cashback_org, director):
    return _make_admin('+996700000011', cashback_org, director)


@pytest.fixture
def visit_admin_api(visit_admin):
    return auth_client(visit_admin)


@pytest.fixture
def cashback_admin_api(cashback_admin):
    return auth_client(cashback_admin)


@pytest.fixture
def user_api(client_user):
    return auth_client(client_user)


@pytest.fixture
def director_api(director):
    return auth_client(director)


def post(client, url, payload=None, idempotency_key=None):
    headers = {}
    if idempotency_key:
        headers['HTTP_IDEMPOTENCY_KEY'] = idempotency_key
    return client.post(url, payload or {}, format='json', **headers)
