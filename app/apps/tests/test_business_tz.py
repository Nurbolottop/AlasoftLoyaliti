"""Сценарии приёмки BACKEND_TZ §12 и новые правила TOPTO / TOPTO Business."""

import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.loyalty.models import NEVER_EXPIRES_AT, CashbackLot, Transaction, TransactionType
from apps.notifications.models import Notification
from apps.organizations.models import CashbackProgram, CatalogItem, OrganizationAdmin
from apps.tests.conftest import APIClient, post
from apps.users.models import User, UserDevice
from apps.users.services import issue_tokens

pytestmark = pytest.mark.django_db

SOM = 100


def business(user):
    """Клиент TOPTO Business: те же токены, заголовок X-App: business."""
    client = APIClient()
    tokens = issue_tokens(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}', HTTP_X_APP='business')
    return client


def client_app(user):
    client = APIClient()
    tokens = issue_tokens(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}', HTTP_X_APP='client')
    return client


def png(name='photo.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (40, 40), (120, 40, 200)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def add_employee(admin_api, **overrides):
    payload = {
        'full_name': 'Камила Ибраимова', 'position': 'MASTER', 'phone': '+996770112233',
        'can_accept_payment': True, 'can_award_bonuses': True, 'can_scan': True,
        **overrides,
    }
    response = admin_api.post('/api/v1/admin/employees', payload, format='json')
    assert response.status_code == 201, response.data
    return response.data['data']


# --- 12.1 Сотрудник ------------------------------------------------------------

def test_employee_scenario(visit_admin, visit_org, client_user):
    admin_api = business(visit_admin)
    created = add_employee(admin_api)
    master = User.objects.get(phone='+996770112233')
    assert master.is_registration_complete
    master_api = business(master)

    assert master_api.get('/api/v1/me').data['data']['role'] == 'ORGANIZATION_EMPLOYEE'
    assert visit_admin and admin_api.get('/api/v1/me').data['data']['role'] == 'ORGANIZATION_ADMIN'

    denied = master_api.get('/api/v1/admin/overview')
    assert denied.status_code == 403
    assert denied.data['error']['details']['permission'] == 'role'

    visit = post(master_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}, idempotency_key='e-1')
    assert visit.status_code == 200, visit.data
    tx = Transaction.objects.get(pk=visit.data['data']['transaction_id'])
    assert tx.employee_id == OrganizationAdmin.objects.get(pk=created['id']).pk
    assert tx.employee_name == 'Камила Ибраимова'

    patched = admin_api.patch(f'/api/v1/admin/employees/{created["id"]}', {'can_award_bonuses': False},
                              format='json')
    assert patched.status_code == 200
    assert Notification.objects.filter(user=master, event='STAFF_ACCESS_CHANGED', is_silent=True).exists()

    again = post(master_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}, idempotency_key='e-2')
    assert again.status_code == 403
    assert again.data['error']['details']['permission'] == 'can_award_bonuses'

    admin_api.patch(f'/api/v1/admin/employees/{created["id"]}', {'is_active': False}, format='json')
    blocked = master_api.get('/api/v1/admin/me')
    assert blocked.status_code == 403
    assert blocked.data['error']['code'] == 'USER_BLOCKED'

    # Операции в истории сохраняют имя сотрудника
    history = admin_api.get(f'/api/v1/admin/transactions?user_id={client_user.id}')
    item = [i for i in history.data['data'] if i['type'] == 'VISIT_EARN'][0]
    assert item['employee']['full_name'] == 'Камила Ибраимова'
    assert item['can_reverse'] is True


def test_disabled_employee_cannot_log_in_to_business(visit_admin, visit_org):
    admin_api = business(visit_admin)
    created = add_employee(admin_api)
    admin_api.patch(f'/api/v1/admin/employees/{created["id"]}', {'is_active': False}, format='json')

    api = APIClient()
    otp = api.post('/api/v1/auth/otp/request', {'phone': '+996770112233', 'purpose': 'LOGIN'}, format='json')
    verified = api.post('/api/v1/auth/otp/verify', {
        'challenge_id': otp.data['data']['challenge_id'], 'phone': '+996770112233',
        'code': otp.data['data']['debug_code'],
    }, format='json')
    login = api.post('/api/v1/auth/otp/login', {
        'phone': '+996770112233', 'verification_token': verified.data['data']['verification_token'],
    }, format='json', HTTP_X_APP='business')
    assert login.status_code == 403
    assert login.data['error']['code'] == 'USER_BLOCKED'


def test_employee_rules(visit_admin, visit_org, cashback_org, director):
    admin_api = business(visit_admin)
    me = admin_api.get('/api/v1/admin/me').data['data']
    assert me['position'] == 'ADMIN' and me['can_scan'] is True
    assert me['organization']['id'] == str(visit_org.id)

    # Себя удалить нельзя, последнего администратора — тоже
    assert admin_api.delete(f'/api/v1/admin/employees/{me["id"]}').data['error']['code'] == 'CANNOT_DELETE_SELF'
    demote = admin_api.patch(f'/api/v1/admin/employees/{me["id"]}', {'position': 'MASTER'}, format='json')
    assert demote.data['error']['code'] == 'LAST_ADMIN'

    created = add_employee(admin_api)
    duplicate = admin_api.post('/api/v1/admin/employees', {
        'full_name': 'Повтор', 'position': 'CASHIER', 'phone': '+996770112233',
    }, format='json')
    assert duplicate.status_code == 409
    assert duplicate.data['error']['code'] == 'EMPLOYEE_PHONE_TAKEN'

    listed = admin_api.get('/api/v1/admin/employees').data['data']
    assert {e['id'] for e in listed} == {me['id'], created['id']}

    assert admin_api.delete(f'/api/v1/admin/employees/{created["id"]}').status_code == 204
    assert admin_api.get(f'/api/v1/admin/employees/{created["id"]}').data['error']['code'] == 'EMPLOYEE_NOT_FOUND'

    # После удаления номер свободен для другого заведения
    other_admin = User.objects.create(phone='+996700000077', role='ORGANIZATION_ADMIN', is_registration_complete=True)
    OrganizationAdmin.objects.create(organization=cashback_org, user=other_admin, created_by=director)
    add_employee(business(other_admin))


def test_employee_avatar_upload(visit_admin, visit_org):
    admin_api = business(visit_admin)
    response = admin_api.post('/api/v1/admin/employees', {
        'full_name': 'Нурлан Осмонов', 'position': 'CASHIER', 'phone': '+996700456789',
        'can_scan': 'true', 'avatar': png(),
    }, format='multipart')
    assert response.status_code == 201, response.data
    assert response.data['data']['avatar_url']


# --- 12.2 Две роли -----------------------------------------------------------

def test_same_person_is_customer_and_employee(visit_admin, visit_org, cashback_admin_api, client_user):
    add_employee(business(visit_admin), phone=client_user.phone)
    api = client_app(client_user)
    assert api.get('/api/v1/me').data['data']['role'] == 'USER'
    assert business(client_user).get('/api/v1/me').data['data']['role'] == 'ORGANIZATION_EMPLOYEE'

    post(cashback_admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 100 * SOM}, idempotency_key='two-1')
    cards = api.get('/api/v1/me/loyalty').data['data']['cards']
    assert len(cards) == 1


# --- 12.3 Каталог --------------------------------------------------------------

def test_catalog_flow(visit_admin, visit_org, cashback_org, client_user):
    admin_api = business(visit_admin)
    section = admin_api.post('/api/v1/admin/catalog/sections', {'name': 'Кофе', 'description': 'Напитки'},
                             format='json').data['data']
    item = admin_api.post(f'/api/v1/admin/catalog/sections/{section["id"]}/items', {
        'name': 'Капучино', 'price': str(350 * SOM), 'short_description': 'Классический',
        'volume': '300 мл', 'image': png(),
    }, format='multipart')
    assert item.status_code == 201, item.data
    item = item.data['data']
    assert item['image_url']

    public = APIClient().get(f'/api/v1/organizations/{visit_org.id}/catalog')
    assert public.status_code == 200
    assert public.data['data']['sections'][0]['items'][0]['name'] == 'Капучино'
    assert admin_api.get('/api/v1/admin/catalog/sections').data['data'][0]['items_count'] == 1
    assert admin_api.get('/api/v1/admin/catalog/services').data['data'] == [
        {'id': item['id'], 'name': 'Капучино', 'price': 350 * SOM}]

    visit = post(admin_api, '/api/v1/admin/visits',
                 {'user_id': str(client_user.id), 'service_id': item['id']}, idempotency_key='cat-1')
    tx = Transaction.objects.get(pk=visit.data['data']['transaction_id'])
    assert tx.service_name == 'Капучино' and tx.cash_paid == 350 * SOM

    assert admin_api.delete(f'/api/v1/admin/catalog/items/{item["id"]}').status_code == 204
    history = client_app(client_user).get('/api/v1/me/transactions').data['data']
    visit_item = [i for i in history if i['type'] == 'VISIT_EARN'][0]
    assert visit_item['label'] == 'Капучино'
    assert visit_item['service']['name'] == 'Капучино'

    # Услуга из чужого каталога
    foreign = CatalogItem.objects.create(
        organization=cashback_org, section=cashback_org.catalog_sections.create(name='Чужой'),
        name='Чужая', price=100, short_description='x',
    )
    bad = post(admin_api, '/api/v1/admin/visits',
               {'user_id': str(client_user.id), 'service_id': str(foreign.id)}, idempotency_key='cat-2')
    assert bad.status_code == 404
    assert bad.data['error']['code'] == 'CATALOG_ITEM_NOT_FOUND'


def test_image_validation_codes(visit_admin, visit_org):
    admin_api = business(visit_admin)
    gif = io.BytesIO()
    Image.new('RGB', (10, 10)).save(gif, format='GIF')
    response = admin_api.patch('/api/v1/admin/organization', {
        'cover': SimpleUploadedFile('a.gif', gif.getvalue(), content_type='image/gif'),
    }, format='multipart')
    assert response.status_code == 415
    assert response.data['error']['code'] == 'UNSUPPORTED_FILE_TYPE'


# --- 12.4 Цифры сходятся -----------------------------------------------------------

def test_revenue_numbers_match(cashback_admin, cashback_org, client_user, other_client_user):
    admin_api = business(cashback_admin)
    add_employee(admin_api)
    master_api = business(User.objects.get(phone='+996770112233'))

    post(admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 500 * SOM}, idempotency_key='rv-1')
    second = post(master_api, '/api/v1/admin/cashback/earn',
                  {'user_id': str(other_client_user.id), 'purchase_total': 300 * SOM}, idempotency_key='rv-2')

    def totals():
        overview = admin_api.get('/api/v1/admin/overview?period=1').data['data']['revenue']
        revenue = admin_api.get('/api/v1/admin/revenue?period=1').data['data']
        payments = sum(p['amount'] for p in admin_api.get('/api/v1/admin/payments?period=1').data['data'])
        staff = sum(
            admin_api.get(f'/api/v1/admin/employees/{e["id"]}/stats?period=1').data['data']['sales_total']
            for e in admin_api.get('/api/v1/admin/employees').data['data']
        )
        return overview, revenue['total'], payments, staff, revenue

    overview, total, payments, staff, revenue = totals()
    assert overview == total == payments == staff == 800 * SOM
    assert len(revenue['points']) == 7
    assert sum(p['amount'] for p in revenue['points']) == 800 * SOM
    assert revenue['average_check'] == 400 * SOM

    reverse = admin_api.post(f'/api/v1/admin/transactions/{second.data["data"]["transaction_id"]}/reverse',
                             {'reason': 'ошибка'}, format='json')
    assert reverse.status_code == 200, reverse.data
    assert totals()[:4] == (500 * SOM,) * 4

    week = admin_api.get('/api/v1/admin/revenue?period=7').data['data']
    assert len(week['points']) == 7
    assert admin_api.get('/api/v1/admin/revenue?period=3').status_code == 400


# --- 12.5 / 12.6 Списание, push и два приложения на одном телефоне -----------------

def test_redemption_pushes_go_to_the_right_app(cashback_admin, cashback_org, client_user,
                                               django_capture_on_commit_callbacks):
    from apps.tests.test_cashback import give_cashback

    give_cashback(client_user, cashback_org, 200 * SOM)
    # Покупатель и сотрудник заведения на одном телефоне
    UserDevice.objects.create(user=client_user, device_id='phone-1', app='CLIENT', fcm_token='client-token')
    UserDevice.objects.create(user=client_user, device_id='phone-1', app='BUSINESS', fcm_token='client-biz')
    UserDevice.objects.create(user=cashback_admin, device_id='phone-2', app='BUSINESS', fcm_token='admin-token')
    UserDevice.objects.create(user=cashback_admin, device_id='phone-2', app='CLIENT', fcm_token='admin-client')

    sent = []
    with mock.patch('apps.notifications.providers.push.ConsolePushProvider.send',
                    side_effect=lambda token, title, body, data, **kw: sent.append((token, data['event'])) or 'ok'):
        admin_api = business(cashback_admin)
        with django_capture_on_commit_callbacks(execute=True):
            redeem = post(admin_api, '/api/v1/admin/cashback/redeem-request', {
                'user_id': str(client_user.id), 'purchase_total': 400 * SOM, 'spend_amount': 100 * SOM,
            }, idempotency_key='rd-1')
        assert redeem.status_code == 200, redeem.data
        rid = redeem.data['data']['redemption_id']
        assert ('client-token', 'REDEMPTION_REQUESTED') in sent
        assert all(token != 'client-biz' for token, _ in sent)

        with django_capture_on_commit_callbacks(execute=True):
            confirm = post(client_app(client_user), f'/api/v1/me/redemptions/{rid}/confirm',
                           idempotency_key='rd-2')
        assert confirm.status_code == 200, confirm.data

    assert ('admin-token', 'REDEMPTION_CONFIRMED') in sent
    assert ('admin-client', 'REDEMPTION_CONFIRMED') not in sent

    client_feed = client_app(client_user).get('/api/v1/me/notifications')
    assert client_feed.data['meta']['unread'] >= 2
    assert {n['event'] for n in client_feed.data['data']} >= {'REDEMPTION_REQUESTED', 'REDEMPTION_CONFIRMED'}
    business_feed = admin_api.get('/api/v1/me/notifications')
    assert [n['event'] for n in business_feed.data['data']] == ['REDEMPTION_CONFIRMED']

    detail = admin_api.get(f'/api/v1/admin/redemptions/{rid}')
    assert detail.data['data']['status'] == 'CONFIRMED'

    # Выручка покупки с бонусами — деньгами оплаченная часть
    assert admin_api.get('/api/v1/admin/overview?period=1').data['data']['revenue'] == 300 * SOM


def test_notification_feed_read(user_api, visit_admin_api, client_user):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}, idempotency_key='nf-1')
    feed = user_api.get('/api/v1/me/notifications').data
    assert feed['meta']['unread'] == 1
    first = feed['data'][0]
    assert first['title'] and first['data']['organization_id']
    assert user_api.post(f'/api/v1/me/notifications/{first["id"]}/read').status_code == 204
    assert user_api.get('/api/v1/home').data['data']['unread_notifications'] == 0
    assert user_api.post('/api/v1/me/notifications/read-all').status_code == 204

    settings = user_api.patch('/api/v1/me/notification-settings', {'news': True}, format='json')
    assert settings.data['data'] == {'operations': True, 'expiring': True, 'news': True}


def test_device_app_is_part_of_key(user_api, client_user):
    for app in ('CLIENT', 'BUSINESS'):
        r = user_api.post('/api/v1/me/devices', {'device_id': 'same', 'platform': 'ios', 'app': app,
                                                  'fcm_token': app}, format='json')
        assert r.status_code == 201, r.data
    assert UserDevice.objects.filter(user=client_user, device_id='same').count() == 2


# --- 12.7 Присоединение ----------------------------------------------------------

def test_join_organization(user_api, visit_admin, visit_org, client_user):
    response = user_api.post(f'/api/v1/organizations/{visit_org.id}/join')
    assert response.status_code == 200
    assert response.data['data']['is_member'] is True
    assert user_api.post(f'/api/v1/organizations/{visit_org.id}/join').status_code == 200

    cards = user_api.get(f'/api/v1/me/loyalty?organization_id={visit_org.id}').data['data']['cards']
    assert cards[0]['visit_progress'] == 0

    admin_api = business(visit_admin)
    new = admin_api.get('/api/v1/admin/customers?filter=new').data['data']
    assert new[0]['customer']['id'] == str(client_user.id)
    assert admin_api.get('/api/v1/admin/customers/new?period=7').data['data'][0]['registered_at']
    assert Notification.objects.filter(user=visit_admin, event='CUSTOMER_JOINED', app='BUSINESS').count() == 1

    visit_org.status = 'BLOCKED'
    visit_org.save(update_fields=['status'])
    blocked = user_api.post(f'/api/v1/organizations/{visit_org.id}/join')
    assert blocked.status_code == 409
    assert blocked.data['error']['code'] == 'ORGANIZATION_BLOCKED'


def test_scan_does_not_make_customer(visit_admin_api, client_user, visit_org):
    response = visit_admin_api.post('/api/v1/admin/customers/resolve-code',
                                    {'code': client_user.public_code}, format='json')
    assert response.status_code == 200
    assert response.data['data']['is_member'] is False
    assert visit_admin_api.get('/api/v1/admin/customers').data['data'] == []


# --- 12.8 Карточка заведения --------------------------------------------------------

def test_organization_card_fields(user_api, visit_admin, visit_org, client_user):
    admin_api = business(visit_admin)
    response = admin_api.patch('/api/v1/admin/organization', {
        'cover': png('cover.png'), 'instagram': '@barberpro.kg', 'kind': 'Барбершоп',
        'card_title': 'Карта гостя', 'card_description': 'Каждый 6-й визит бесплатно',
        'tags': ['Стрижки', 'Борода'], 'latitude': '42.8246', 'longitude': '74.6128',
    }, format='multipart')
    assert response.status_code == 200, response.data
    hours = admin_api.patch('/api/v1/admin/organization', {'working_hours': [
        {'day': d, 'open': '00:00', 'close': '23:59', 'is_closed': False} for d in range(1, 8)
    ]}, format='json')
    assert hours.status_code == 200, hours.data
    assert admin_api.patch('/api/v1/admin/organization', {'working_hours': [{'day': 9}]},
                           format='json').status_code == 422

    public = APIClient().get(f'/api/v1/organizations/{visit_org.id}?lat=42.83&lng=74.61').data['data']
    assert public['instagram_url'] == 'https://instagram.com/barberpro.kg'
    assert public['cover_url'] and public['kind'] == 'Барбершоп'
    assert public['is_open_now'] is True and public['distance_km'] is not None
    assert public['is_member'] is False and public['rating'] is None

    post(admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}, idempotency_key='card-1')
    card = user_api.get('/api/v1/home').data['data']['cards'][0]
    assert card['card_title'] == 'Карта гостя'
    assert card['organization']['category']['slug'] == 'cafe'

    listed = user_api.get('/api/v1/organizations?tag=Борода&open_now=true').data['data']
    assert [o['id'] for o in listed] == [str(visit_org.id)]
    assert user_api.put(f'/api/v1/me/favorites/organizations/{visit_org.id}').status_code == 204
    assert user_api.get('/api/v1/organizations?favorites=true').data['data'][0]['is_favorite'] is True


# --- 12.9 Кешбэк без срока ------------------------------------------------------------

def test_cashback_without_expiry(cashback_admin, cashback_org, client_user, user_api):
    admin_api = business(cashback_admin)
    response = admin_api.patch('/api/v1/admin/program', {'cashback_program': {'expiry_days': 0}}, format='json')
    assert response.status_code == 200, response.data
    post(admin_api, '/api/v1/admin/cashback/earn',
         {'user_id': str(client_user.id), 'purchase_total': 100 * SOM}, idempotency_key='ne-1')
    assert CashbackLot.objects.get(user=client_user).expires_at == NEVER_EXPIRES_AT
    card = user_api.get('/api/v1/home').data['data']['cards'][0]
    assert card['next_expiry'] is None


def test_min_purchase_blocks_spending_only(cashback_admin, cashback_org, client_user):
    from apps.tests.test_cashback import give_cashback

    CashbackProgram.objects.filter(organization=cashback_org).update(min_purchase_amount=500 * SOM)
    give_cashback(client_user, cashback_org, 100 * SOM)
    admin_api = business(cashback_admin)
    quote = admin_api.post('/api/v1/admin/cashback/quote', {
        'user_id': str(client_user.id), 'purchase_total': 200 * SOM, 'spend_amount': 50 * SOM,
    }, format='json').data['data']
    assert quote['is_spend_allowed'] is False and quote['earn_amount'] > 0

    redeem = post(admin_api, '/api/v1/admin/cashback/redeem-request', {
        'user_id': str(client_user.id), 'purchase_total': 200 * SOM, 'spend_amount': 50 * SOM,
    }, idempotency_key='mp-1')
    assert redeem.status_code == 409
    assert redeem.data['error']['code'] == 'MIN_PURCHASE_NOT_REACHED'

    earn = post(admin_api, '/api/v1/admin/cashback/earn',
                {'user_id': str(client_user.id), 'purchase_total': 200 * SOM}, idempotency_key='mp-2')
    assert earn.status_code == 200


# --- прочее ------------------------------------------------------------------------------

def test_customer_card_and_note(visit_admin, visit_org, client_user):
    admin_api = business(visit_admin)
    for i in range(3):
        post(admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)}, idempotency_key=f'cc-{i}')
    note = admin_api.patch(f'/api/v1/admin/customers/{client_user.id}', {'note': 'Любит классику'}, format='json')
    assert note.status_code == 200
    state = admin_api.get(f'/api/v1/admin/customers/{client_user.id}/state').data['data']
    assert state['customer']['note'] == 'Любит классику'
    assert state['customer']['is_regular'] is True
    assert state['customer']['phone'] == client_user.phone

    rows = admin_api.get('/api/v1/admin/customers?filter=today').data['data']
    assert rows[0]['last_operation']['amount'] == 1
    assert rows[0]['public_code'] == client_user.public_code   # прежние поля остались


def test_registration_with_referral_and_without_pin(client_user):
    inviter = client_app(client_user).get('/api/v1/me/referral').data['data']
    api = APIClient()
    otp = api.post('/api/v1/auth/otp/request', {'phone': '+996700999888', 'purpose': 'REGISTER'}, format='json')
    verified = api.post('/api/v1/auth/otp/verify', {
        'challenge_id': otp.data['data']['challenge_id'], 'phone': '+996700999888',
        'code': otp.data['data']['debug_code'],
    }, format='json')
    token = verified.data['data']['verification_token']

    denied = api.post('/api/v1/auth/register/complete', {
        'phone': '+996700999888', 'verification_token': token, 'first_name': 'Мирлан',
    }, format='json', HTTP_X_APP='business')
    assert denied.status_code == 403

    response = api.post('/api/v1/auth/register/complete', {
        'phone': '+996700999888', 'verification_token': token, 'first_name': 'Мирлан',
        'referral_code': inviter['code'],
    }, format='json')
    assert response.status_code == 201, response.data
    assert response.data['data']['user']['has_pin'] is False

    after = client_app(client_user).get('/api/v1/me/referral').data['data']
    assert after['invited_count'] == 1 and after['friends'][0]['first_name'] == 'Мирлан'
    assert Notification.objects.filter(user=client_user, event='FRIEND_JOINED').exists()


def test_profile_gender_and_support(user_api):
    assert user_api.patch('/api/v1/me', {'gender': 'FEMALE'}, format='json').data['data']['gender'] == 'FEMALE'
    assert user_api.patch('/api/v1/me', {'gender': ''}, format='json').data['data']['gender'] is None
    assert APIClient().get('/api/v1/support/contacts').status_code == 200


def test_cashback_earn_reverse_flag(cashback_admin_api, client_user):
    earn = post(cashback_admin_api, '/api/v1/admin/cashback/earn',
                {'user_id': str(client_user.id), 'purchase_total': 100 * SOM}, idempotency_key='cr-1')
    tx_id = earn.data['data']['transaction_id']
    items = cashback_admin_api.get('/api/v1/admin/transactions').data['data']
    assert [i for i in items if i['id'] == tx_id][0]['can_reverse'] is True
    assert Transaction.objects.get(pk=tx_id).type == TransactionType.CASHBACK_EARN
