"""RBAC и изоляция арендаторов (ТЗ backend §3, §30)."""

import pytest

from apps.tests.conftest import auth_client, post
from apps.users.models import Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def foreign_admin(db, category, director):
    """Администратор другой организации того же типа."""
    from apps.organizations.models import LoyaltyType, Organization, OrganizationAdmin, VisitProgram

    organization = Organization.objects.create(
        name='Чужая кофейня', slug='foreign-coffee', category=category,
        loyalty_type=LoyaltyType.VISIT, created_by=director,
    )
    VisitProgram.objects.create(organization=organization, target_visits=5)
    user = User.objects.create(
        phone='+996700000077', role=Role.ORGANIZATION_ADMIN, is_registration_complete=True
    )
    OrganizationAdmin.objects.create(organization=organization, user=user, created_by=director)
    return user, organization


def test_admin_operates_only_in_own_organization(visit_admin_api, foreign_admin, client_user, visit_org):
    """Организация берётся из токена — подделать её через body нельзя."""
    foreign_user, foreign_org = foreign_admin
    foreign_api = auth_client(foreign_user)

    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='own-1')

    # Даже если админ пришлёт чужой organization_id, операция ляжет в его организацию
    post(foreign_api, '/api/v1/admin/visits',
         {'user_id': str(client_user.id), 'organization_id': str(visit_org.id)},
         idempotency_key='foreign-1')

    from apps.loyalty.models import Transaction

    assert Transaction.objects.filter(organization=visit_org).count() == 1
    assert Transaction.objects.filter(organization=foreign_org).count() == 1


def test_admin_sees_only_own_transactions(visit_admin_api, foreign_admin, client_user):
    foreign_user, _ = foreign_admin
    foreign_api = auth_client(foreign_user)

    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='iso-1')

    response = foreign_api.get('/api/v1/admin/transactions')
    assert response.status_code == 200
    assert response.data['data'] == []


def test_admin_cannot_reverse_foreign_transaction(visit_admin_api, foreign_admin, client_user):
    foreign_user, _ = foreign_admin
    foreign_api = auth_client(foreign_user)

    transaction_id = post(
        visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
        idempotency_key='iso-2',
    ).data['data']['transaction_id']

    response = post(foreign_api, f'/api/v1/admin/transactions/{transaction_id}/reverse',
                    {'reason': 'попытка'})
    assert response.status_code == 404
    assert response.data['error']['code'] == 'TRANSACTION_NOT_FOUND'


def test_user_cannot_access_admin_api(user_api, client_user):
    assert post(user_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}).status_code == 403
    assert user_api.get('/api/v1/admin/statistics').status_code == 403


def test_user_cannot_access_director_api(user_api):
    assert user_api.get('/api/v1/director/organizations').status_code == 403
    assert user_api.get('/api/v1/director/audit').status_code == 403


def test_admin_cannot_access_director_api(visit_admin_api):
    assert visit_admin_api.get('/api/v1/director/organizations').status_code == 403


def test_user_cannot_read_foreign_redemption(user_api, other_client_user, visit_admin_api, client_user, visit_org):
    """IDOR: подтвердить чужой запрос нельзя."""
    from apps.loyalty.models import Gift

    for i in range(5):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(other_client_user.id)},
             idempotency_key=f'idor{i}')
    gift = Gift.objects.get(user=other_client_user, organization=visit_org)
    redemption_id = post(
        visit_admin_api, f'/api/v1/admin/gifts/{gift.id}/redeem-request',
        {'user_id': str(other_client_user.id)}, idempotency_key='idor-redeem',
    ).data['data']['redemption_id']

    response = post(user_api, f'/api/v1/me/redemptions/{redemption_id}/confirm')
    assert response.status_code == 404
    assert response.data['error']['code'] == 'REDEMPTION_NOT_FOUND'


def test_user_history_is_scoped_to_self(user_api, other_client_user, visit_admin_api):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(other_client_user.id)},
         idempotency_key='scope-1')

    response = user_api.get('/api/v1/me/transactions')
    assert response.status_code == 200
    assert response.data['data'] == []


def test_revoked_admin_loses_access(visit_admin, visit_org):
    from apps.organizations.models import OrganizationAdmin

    api = auth_client(visit_admin)
    assert api.get('/api/v1/admin/statistics').status_code == 200

    OrganizationAdmin.objects.filter(user=visit_admin).update(is_active=False)
    assert api.get('/api/v1/admin/statistics').status_code == 401


def test_blocked_organization_revokes_admin_access(visit_admin, visit_org):
    api = auth_client(visit_admin)
    visit_org.status = 'BLOCKED'
    visit_org.save(update_fields=['status'])
    assert api.get('/api/v1/admin/statistics').status_code == 401


def test_catalog_hides_blocked_organizations(user_api, visit_org, cashback_org):
    visit_org.status = 'BLOCKED'
    visit_org.save(update_fields=['status'])

    response = user_api.get('/api/v1/organizations')
    slugs = [item['slug'] for item in response.data['data']]
    assert 'coffee' not in slugs
    assert 'market' in slugs
