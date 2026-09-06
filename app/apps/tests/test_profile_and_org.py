"""Профиль пользователя и карточка своей организации (запросы Android-команды)."""

import io

import pytest
from PIL import Image

from apps.organizations.models import Category, Organization, OrganizationStatus
from apps.tests.conftest import post
from apps.users.models import OtpChallenge, OtpPurpose, User

pytestmark = pytest.mark.django_db

ORG = '/api/v1/admin/organization'
AVATAR = '/api/v1/me/avatar'
PHONE_REQUEST = '/api/v1/me/phone/request'
PHONE_CONFIRM = '/api/v1/me/phone/confirm'


def image_file(name='photo.png', size=(800, 600)):
    buffer = io.BytesIO()
    Image.new('RGB', size, (120, 80, 200)).save(buffer, format='PNG')
    buffer.seek(0)
    buffer.name = name
    return buffer


# --- своя организация -------------------------------------------------------

def test_admin_reads_own_organization(visit_admin_api, visit_org):
    r = visit_admin_api.get(ORG)
    assert r.status_code == 200, r.data
    data = r.data['data']
    assert data['id'] == str(visit_org.id)
    assert data['name'] == visit_org.name
    assert data['loyalty_type'] == 'VISIT'
    assert 'slug' not in data, 'slug ломает публичные ссылки, наружу не отдаём'


def test_admin_renames_organization(visit_admin_api, visit_org):
    r = visit_admin_api.patch(ORG, {'name': 'Кофейня на Чуй', 'phone': '+996312777888',
                                    'address': 'Бишкек, Чуй 100'}, format='json')
    assert r.status_code == 200, r.data
    visit_org.refresh_from_db()
    assert visit_org.name == 'Кофейня на Чуй'
    assert visit_org.phone == '+996312777888'
    assert visit_org.slug == 'coffee', 'slug должен остаться прежним'


def test_admin_uploads_and_clears_logo(visit_admin_api, visit_org):
    r = visit_admin_api.patch(ORG, {'logo': image_file('logo.png')}, format='multipart')
    assert r.status_code == 200, r.data
    assert r.data['data']['logo_url']

    visit_org.refresh_from_db()
    assert visit_org.logo
    assert visit_org.logo.name.endswith('.webp'), 'логотип приводится к WEBP'

    cleared = visit_admin_api.patch(ORG, {'logo': None}, format='json')
    assert cleared.status_code == 200
    assert cleared.data['data']['logo_url'] is None


def test_admin_changes_category(visit_admin_api, visit_org, db):
    other = Category.objects.create(slug='barber', name_ru='Барбершоп', name_ky='Барбершоп')
    r = visit_admin_api.patch(ORG, {'category_id': str(other.id)}, format='json')
    assert r.status_code == 200, r.data
    assert r.data['data']['category']['slug'] == 'barber'


def test_admin_cannot_change_status_or_type(visit_admin_api, visit_org):
    r = visit_admin_api.patch(ORG, {'status': 'BLOCKED', 'loyalty_type': 'CASHBACK'}, format='json')
    assert r.status_code == 200
    visit_org.refresh_from_db()
    assert visit_org.status == OrganizationStatus.ACTIVE
    assert visit_org.loyalty_type == 'VISIT'


def test_admin_edits_only_own_organization(visit_admin_api, cashback_org, visit_org):
    visit_admin_api.patch(ORG, {'id': str(cashback_org.id), 'name': 'Подмена'}, format='json')
    cashback_org.refresh_from_db()
    visit_org.refresh_from_db()
    assert cashback_org.name != 'Подмена'
    assert visit_org.name == 'Подмена'


def test_organization_edit_is_audited(visit_admin_api):
    from apps.audit.models import AuditAction, AuditLog

    visit_admin_api.patch(ORG, {'name': 'Новое имя'}, format='json')
    entry = AuditLog.objects.filter(action=AuditAction.ORGANIZATION_UPDATED).first()
    assert entry is not None
    assert entry.after['name'] == 'Новое имя'


def test_user_cannot_use_admin_organization(user_api, director_api):
    assert user_api.get(ORG).status_code == 403
    assert director_api.get(ORG).status_code == 403


# --- аватар -----------------------------------------------------------------

def test_avatar_upload_delete_and_visibility(user_api, client_user):
    assert user_api.get('/api/v1/me').data['data']['avatar_url'] is None

    r = user_api.post(AVATAR, {'file': image_file()}, format='multipart')
    assert r.status_code == 200, r.data
    assert r.data['data']['avatar_url']

    client_user.refresh_from_db()
    assert client_user.avatar.name.endswith('.webp')
    assert user_api.get('/api/v1/me').data['data']['avatar_url']

    deleted = user_api.delete(AVATAR)
    assert deleted.status_code == 200
    assert deleted.data['data']['avatar_url'] is None
    client_user.refresh_from_db()
    assert not client_user.avatar


def test_avatar_visible_to_admin_on_resolve(user_api, visit_admin_api, client_user):
    user_api.post(AVATAR, {'file': image_file()}, format='multipart')
    r = post(visit_admin_api, '/api/v1/admin/customers/resolve-code',
             {'code': client_user.public_code})
    assert r.status_code == 200
    assert r.data['data']['customer']['avatar_url']


def test_avatar_rejects_non_image(user_api):
    bad = io.BytesIO(b'not an image at all')
    bad.name = 'payload.png'
    r = user_api.post(AVATAR, {'file': bad}, format='multipart')
    assert r.status_code == 422


# --- смена номера -----------------------------------------------------------

def test_phone_change_full_flow(user_api, client_user, settings):
    settings.OTP_STATIC_CODE = ''
    old_phone, old_code, old_qr = client_user.phone, client_user.public_code, client_user.qr_token

    r = post(user_api, PHONE_REQUEST, {'new_phone': '+996555010203'})
    assert r.status_code == 200, r.data
    challenge_id, code = r.data['data']['challenge_id'], r.data['data']['debug_code']

    # до подтверждения номер прежний
    client_user.refresh_from_db()
    assert client_user.phone == old_phone

    confirm = post(user_api, PHONE_CONFIRM, {'challenge_id': challenge_id, 'code': code})
    assert confirm.status_code == 200, confirm.data
    assert confirm.data['data']['phone'] == '+996555010203'

    client_user.refresh_from_db()
    assert client_user.phone == '+996555010203'
    # карты и история привязаны к пользователю, а не к номеру
    assert client_user.public_code == old_code
    assert client_user.qr_token == old_qr


def test_phone_change_code_goes_to_new_number(user_api, client_user):
    post(user_api, PHONE_REQUEST, {'new_phone': '+996555010204'})
    challenge = OtpChallenge.objects.filter(purpose=OtpPurpose.PHONE_CHANGE).first()
    assert challenge.phone == '+996555010204'
    assert challenge.user_id == client_user.id


def test_phone_change_rejects_taken_number(user_api, other_client_user):
    r = post(user_api, PHONE_REQUEST, {'new_phone': other_client_user.phone})
    assert r.status_code == 409
    assert r.data['error']['code'] == 'USER_ALREADY_EXISTS'


def test_phone_change_rejects_same_number(user_api, client_user):
    r = post(user_api, PHONE_REQUEST, {'new_phone': client_user.phone})
    assert r.status_code == 400


def test_phone_change_rejects_wrong_code(user_api, client_user, settings):
    settings.OTP_STATIC_CODE = ''
    r = post(user_api, PHONE_REQUEST, {'new_phone': '+996555010205'})
    challenge_id = r.data['data']['challenge_id']

    bad = post(user_api, PHONE_CONFIRM, {'challenge_id': challenge_id, 'code': '000000'})
    assert bad.status_code == 422
    assert bad.data['error']['code'] == 'OTP_INVALID'
    client_user.refresh_from_db()
    assert client_user.phone != '+996555010205'


def test_cannot_confirm_someone_elses_phone_change(user_api, other_client_user, client_user):
    """IDOR: чужой челлендж подтвердить нельзя."""
    from apps.tests.conftest import auth_client

    other_api = auth_client(other_client_user)
    r = post(other_api, PHONE_REQUEST, {'new_phone': '+996555010206'})
    challenge_id = r.data['data']['challenge_id']

    stolen = post(user_api, PHONE_CONFIRM, {'challenge_id': challenge_id, 'code': '1234'})
    assert stolen.status_code == 404


def test_phone_change_requires_auth(api):
    assert post(api, PHONE_REQUEST, {'new_phone': '+996555010207'}).status_code == 401


def test_login_works_with_new_phone(api, user_api, client_user, settings):
    settings.OTP_STATIC_CODE = ''
    r = post(user_api, PHONE_REQUEST, {'new_phone': '+996555010208'})
    post(user_api, PHONE_CONFIRM, {
        'challenge_id': r.data['data']['challenge_id'], 'code': r.data['data']['debug_code'],
    })
    assert post(api, '/api/v1/auth/pin/login',
                {'phone': '+996555010208', 'pin': '1234'}).status_code == 200


# --- категории со счётчиком -------------------------------------------------

def test_categories_expose_organizations_count(user_api, visit_org, cashback_org, category):
    empty = Category.objects.create(slug='empty', name_ru='Пусто', name_ky='Пусто')

    r = user_api.get('/api/v1/categories')
    assert r.status_code == 200
    by_slug = {c['slug']: c['organizations_count'] for c in r.data['data']}
    assert by_slug[category.slug] == 2
    assert by_slug[empty.slug] == 0


def test_blocked_organizations_are_not_counted(user_api, visit_org, cashback_org, category):
    visit_org.status = OrganizationStatus.BLOCKED
    visit_org.save(update_fields=['status'])

    r = user_api.get('/api/v1/categories')
    counts = {c['slug']: c['organizations_count'] for c in r.data['data']}
    assert counts[category.slug] == 1


def test_has_organizations_filter(user_api, visit_org, category):
    Category.objects.create(slug='empty2', name_ru='Пусто', name_ky='Пусто')

    r = user_api.get('/api/v1/categories?has_organizations=true')
    slugs = [c['slug'] for c in r.data['data']]
    assert category.slug in slugs
    assert 'empty2' not in slugs


# --- форма ответа /admin/program --------------------------------------------

def test_program_response_shape_is_stable(visit_admin_api):
    r = visit_admin_api.get('/api/v1/admin/program')
    assert set(r.data['data'].keys()) == {
        'organization', 'loyalty_type', 'visit_program',
        'cashback_program', 'can_change_loyalty_type',
    }
