"""USER API: карты, история, единый QR при независимых балансах (ТЗ §9-10, §14)."""

import pytest

from apps.tests.conftest import post
from apps.tests.test_cashback import SOM, give_cashback

pytestmark = pytest.mark.django_db


def test_one_qr_works_in_all_organizations_with_independent_balances(
    visit_admin_api, cashback_admin_api, user_api, client_user, visit_org, cashback_org
):
    """Один QR во всех организациях, но балансы независимы (критерий §21)."""
    qr = f'alasoft://u/{client_user.qr_token}'

    in_visit = post(visit_admin_api, '/api/v1/admin/customers/resolve-qr', {'qr': qr})
    in_cashback = post(cashback_admin_api, '/api/v1/admin/customers/resolve-qr', {'qr': qr})
    assert in_visit.status_code == 200
    assert in_cashback.status_code == 200
    assert in_visit.data['data']['customer']['id'] == in_cashback.data['data']['customer']['id']

    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='qr-1')
    post(cashback_admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 1000 * SOM}, idempotency_key='qr-2')

    loyalty = user_api.get('/api/v1/me/loyalty')
    cards = {card['organization']['id']: card for card in loyalty.data['data']['cards']}

    visit_card = cards[str(visit_org.id)]
    cashback_card = cards[str(cashback_org.id)]
    assert visit_card['visit_progress'] == 1
    assert visit_card['target_visits'] == 5
    assert 'cashback_available' not in visit_card
    assert cashback_card['cashback_available'] == 50 * SOM


def test_cashback_is_not_presented_as_single_wallet(
    cashback_admin_api, user_api, client_user, cashback_org, category, director
):
    """Cashback разных организаций не складывается в один кошелёк (§10)."""
    from apps.organizations.models import CashbackProgram, LoyaltyType, Organization

    second = Organization.objects.create(
        name='Второй маркет', slug='second-market', category=category,
        loyalty_type=LoyaltyType.CASHBACK, created_by=director,
    )
    CashbackProgram.objects.create(organization=second)

    give_cashback(client_user, cashback_org, 100 * SOM)
    give_cashback(client_user, second, 200 * SOM)

    response = user_api.get('/api/v1/me/loyalty')
    breakdown = response.data['data']['summary']['cashback_by_organization']
    assert len(breakdown) == 2
    assert {item['amount'] for item in breakdown} == {100 * SOM, 200 * SOM}
    # Единой суммы в ответе нет
    assert 'cashback_total' not in response.data['data']['summary']


def test_history_labels_are_localized(visit_admin_api, user_api, client_user):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='hist-1')

    response = user_api.get('/api/v1/me/transactions')
    assert response.status_code == 200
    labels = [item['label'] for item in response.data['data']]
    assert '+1 посещение' in labels

    client_user.language = 'ky'
    client_user.save(update_fields=['language'])
    ky_response = user_api.get('/api/v1/me/transactions')
    assert '+1 баруу' in [item['label'] for item in ky_response.data['data']]


def test_cashback_history_shows_amounts(cashback_admin_api, user_api, client_user):
    post(cashback_admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 1000 * SOM}, idempotency_key='hist-2')

    response = user_api.get('/api/v1/me/transactions')
    earn = [item for item in response.data['data'] if item['type'] == 'CASHBACK_EARN'][0]
    assert earn['amount'] == 50 * SOM
    assert earn['amount_som'] == '50.00'
    assert earn['label'] == '+50.00 сом'


def test_my_cashback_shows_lots_with_expiry(user_api, client_user, cashback_org):
    give_cashback(client_user, cashback_org, 300 * SOM, days=10)

    response = user_api.get('/api/v1/me/cashback')
    assert response.status_code == 200
    data = response.data['data']
    assert data['organizations'][0]['available'] == 300 * SOM
    assert data['organizations'][0]['next_expiry']['amount'] == 300 * SOM
    assert len(data['lots']) == 1


def test_catalog_shows_program_rules(user_api, visit_org, cashback_org):
    response = user_api.get('/api/v1/organizations')
    assert response.status_code == 200
    by_slug = {item['slug']: item for item in response.data['data']}

    assert by_slug['coffee']['program']['target_visits'] == 5
    assert by_slug['market']['program']['cashback_rate_bps'] == 500
    assert by_slug['market']['program']['max_spend_percent_bps'] == 3000


def test_my_cards_show_only_organizations_with_activity(user_api, visit_admin_api,
                                                        client_user, visit_org, cashback_org):
    empty = user_api.get('/api/v1/me/loyalty')
    assert empty.data['data']['cards'] == []

    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='cards-1')

    filled = user_api.get('/api/v1/me/loyalty')
    assert len(filled.data['data']['cards']) == 1


def test_home_screen_summary(user_api, visit_admin_api, client_user):
    for i in range(5):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
             idempotency_key=f'home{i}')

    response = user_api.get('/api/v1/home')
    assert response.status_code == 200
    data = response.data['data']
    assert data['available_gifts_total'] == 1
    assert data['user']['public_code'] == client_user.public_code
    assert data['user']['qr_payload'].startswith('alasoft://u/')


def test_profile_update(user_api, client_user):
    response = user_api.patch('/api/v1/me', {'first_name': 'Нурбек', 'language': 'ky'}, format='json')
    assert response.status_code == 200
    assert response.data['data']['first_name'] == 'Нурбек'
    assert response.data['data']['language'] == 'ky'


def test_device_registration(user_api):
    response = post(user_api, '/api/v1/me/devices', {
        'device_id': 'device-1', 'platform': 'ios', 'fcm_token': 'fcm-abc',
    })
    assert response.status_code == 201
    assert response.data['data']['device_id'] == 'device-1'
    # FCM-токен наружу не отдаётся
    assert 'fcm_token' not in response.data['data']


def test_pagination_meta_present(user_api, visit_admin_api, client_user):
    for i in range(3):
        post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
             idempotency_key=f'pag{i}')

    response = user_api.get('/api/v1/me/transactions?page_size=2')
    assert response.status_code == 200
    pagination = response.data['meta']['pagination']
    assert pagination['page_size'] == 2
    assert pagination['total_items'] == 3
    assert pagination['has_next'] is True


def test_error_envelope_shape(api):
    response = post(api, '/api/v1/auth/pin/login', {'phone': '+996555000111', 'pin': '1234'})
    assert response.status_code == 401
    assert response.data['success'] is False
    assert set(response.data['error'].keys()) == {'code', 'message', 'details'}


def test_success_envelope_shape(user_api):
    response = user_api.get('/api/v1/me')
    assert response.data['success'] is True
    assert 'data' in response.data
