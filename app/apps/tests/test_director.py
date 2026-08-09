"""Director API: организации, доступы, аудит, статистика (ТЗ §16, §21)."""

import pytest

from apps.organizations.models import LoyaltyType, Organization, OrganizationStatus
from apps.tests.conftest import post
from apps.users.models import Role, User

pytestmark = pytest.mark.django_db

ORGANIZATIONS = '/api/v1/director/organizations'


def test_director_creates_organization_with_program(director_api, category):
    response = post(director_api, ORGANIZATIONS, {
        'name': 'Барбершоп Алтын',
        'category': str(category.id),
        'loyalty_type': 'VISIT',
        'phone': '+996312111222',
        'address': 'Бишкек',
        'visit_program': {'target_visits': 7, 'reward_count': 1},
    })
    assert response.status_code == 201, response.data
    data = response.data['data']
    assert data['loyalty_type'] == 'VISIT'
    assert data['visit_program']['target_visits'] == 7
    assert data['status'] == OrganizationStatus.ACTIVE


def test_director_creates_cashback_organization(director_api, category):
    response = post(director_api, ORGANIZATIONS, {
        'name': 'Автомойка Аква',
        'category': str(category.id),
        'loyalty_type': 'CASHBACK',
        'cashback_program': {
            'cashback_rate_bps': 700, 'max_spend_percent_bps': 5000, 'expiry_days': 60
        },
    })
    assert response.status_code == 201, response.data
    assert response.data['data']['cashback_program']['cashback_rate_bps'] == 700


def test_loyalty_type_locked_after_transactions(director_api, visit_admin_api, client_user, visit_org):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='lock-1')

    response = director_api.patch(
        f'{ORGANIZATIONS}/{visit_org.id}', {'loyalty_type': 'CASHBACK'}, format='json'
    )
    assert response.status_code == 409
    assert response.data['error']['code'] == 'LOYALTY_TYPE_LOCKED'


def test_block_and_unblock_organization(director_api, visit_org):
    block = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/block', {'reason': 'жалобы'})
    assert block.status_code == 200
    visit_org.refresh_from_db()
    assert visit_org.status == OrganizationStatus.BLOCKED
    assert visit_org.blocked_reason == 'жалобы'

    unblock = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/unblock')
    assert unblock.status_code == 200
    visit_org.refresh_from_db()
    assert visit_org.status == OrganizationStatus.ACTIVE


def test_director_creates_organization_admin(director_api, visit_org):
    response = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/admin', {
        'phone': '+996700333444', 'first_name': 'Админ',
    })
    assert response.status_code == 201, response.data
    assert len(response.data['data']['temporary_pin']) == 4

    admin = User.objects.get(phone='+996700333444')
    assert admin.role == Role.ORGANIZATION_ADMIN
    assert admin.active_admin_membership.organization_id == visit_org.id


def test_only_one_active_admin_per_organization(director_api, visit_org, visit_admin):
    response = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/admin',
                    {'phone': '+996700333555'})
    assert response.status_code == 409
    assert response.data['error']['code'] == 'ADMIN_ALREADY_EXISTS'

    replaced = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/admin',
                    {'phone': '+996700333555', 'replace_existing': True})
    assert replaced.status_code == 201
    assert visit_org.admins.filter(is_active=True).count() == 1


def test_admin_phone_cannot_belong_to_client(director_api, visit_org, client_user):
    response = post(director_api, f'{ORGANIZATIONS}/{visit_org.id}/admin',
                    {'phone': client_user.phone})
    assert response.status_code == 409
    assert response.data['error']['code'] == 'USER_ALREADY_EXISTS'


def test_director_searches_users_by_public_code(director_api, client_user):
    response = director_api.get(f'/api/v1/director/users?search={client_user.public_code}')
    assert response.status_code == 200
    assert len(response.data['data']) == 1
    assert response.data['data'][0]['id'] == str(client_user.id)


def test_director_searches_users_by_phone(director_api, client_user):
    response = director_api.get(f'/api/v1/director/users?search={client_user.phone}')
    assert response.status_code == 200
    assert len(response.data['data']) == 1


def test_director_sees_global_ledger(director_api, visit_admin_api, cashback_admin_api,
                                     client_user, visit_org, cashback_org):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='gl-1')
    post(cashback_admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 100_00},
         idempotency_key='gl-2')

    response = director_api.get('/api/v1/director/transactions')
    assert response.status_code == 200
    organizations = {item['organization']['id'] for item in response.data['data']}
    assert {str(visit_org.id), str(cashback_org.id)} <= organizations


def test_director_statistics_platform_and_organization(director_api, visit_admin_api,
                                                       client_user, visit_org):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='stat-1')

    platform = director_api.get('/api/v1/director/statistics')
    assert platform.status_code == 200
    assert platform.data['data']['organizations_total'] >= 1
    assert platform.data['data']['operations_total'] >= 1

    per_org = director_api.get(f'/api/v1/director/statistics?organization_id={visit_org.id}')
    assert per_org.status_code == 200
    assert per_org.data['data']['visits'] == 1


def test_audit_log_records_organization_creation(director_api, category):
    post(director_api, ORGANIZATIONS, {
        'name': 'Аудит-тест', 'category': str(category.id), 'loyalty_type': 'VISIT',
    })
    response = director_api.get('/api/v1/director/audit?action=ORGANIZATION_CREATED')
    assert response.status_code == 200
    assert len(response.data['data']) == 1
    assert response.data['data'][0]['actor_type'] == 'DIRECTOR'


def test_admin_statistics_scoped_to_own_organization(visit_admin_api, client_user, visit_org):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='ast-1')

    response = visit_admin_api.get('/api/v1/admin/statistics')
    assert response.status_code == 200
    assert response.data['data']['organization_id'] == str(visit_org.id)
    assert response.data['data']['visits'] == 1
