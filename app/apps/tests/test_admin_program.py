"""Настройка программы владельцем заведения: GET/PATCH /admin/program."""

import pytest

from apps.organizations.models import CashbackProgram, LoyaltyType, Organization, VisitProgram
from apps.tests.conftest import auth_client, post

pytestmark = pytest.mark.django_db

PROGRAM = '/api/v1/admin/program'


def test_admin_reads_own_program(visit_admin_api, visit_org):
    r = visit_admin_api.get(PROGRAM)
    assert r.status_code == 200, r.data
    data = r.data['data']
    assert data['organization']['id'] == str(visit_org.id)
    assert data['loyalty_type'] == 'VISIT'
    assert data['visit_program']['target_visits'] == 5
    assert data['cashback_program'] is None


def test_admin_updates_visit_settings(visit_admin_api, visit_org):
    r = visit_admin_api.patch(PROGRAM, {
        'visit_program': {'target_visits': 8, 'reward_count': 2, 'reward_title_ru': 'Стрижка'}
    }, format='json')
    assert r.status_code == 200, r.data
    assert r.data['data']['visit_program']['target_visits'] == 8

    visit_org.visit_program.refresh_from_db()
    assert visit_org.visit_program.target_visits == 8
    assert visit_org.visit_program.reward_title_ru == 'Стрижка'


def test_admin_updates_cashback_settings(cashback_admin_api, cashback_org):
    r = cashback_admin_api.patch(PROGRAM, {
        'cashback_program': {'cashback_rate_bps': 700, 'max_spend_percent_bps': 5000, 'expiry_days': 30}
    }, format='json')
    assert r.status_code == 200, r.data

    cashback_org.cashback_program.refresh_from_db()
    assert cashback_org.cashback_program.cashback_rate_bps == 700
    assert cashback_org.cashback_program.expiry_days == 30


def test_admin_switches_loyalty_type_while_no_transactions(visit_admin_api, visit_org):
    r = visit_admin_api.patch(PROGRAM, {
        'loyalty_type': 'CASHBACK',
        'cashback_program': {'cashback_rate_bps': 300, 'max_spend_percent_bps': 2000, 'expiry_days': 60},
    }, format='json')
    assert r.status_code == 200, r.data

    visit_org.refresh_from_db()
    assert visit_org.loyalty_type == LoyaltyType.CASHBACK
    assert CashbackProgram.objects.filter(organization=visit_org).exists()


def test_type_switch_blocked_after_transactions(visit_admin_api, client_user, visit_org):
    post(visit_admin_api, '/api/v1/admin/visits', {'user_id': str(client_user.id)},
         idempotency_key='prog-lock')

    r = visit_admin_api.patch(PROGRAM, {'loyalty_type': 'CASHBACK'}, format='json')
    assert r.status_code == 409
    assert r.data['error']['code'] == 'LOYALTY_TYPE_LOCKED'

    visit_org.refresh_from_db()
    assert visit_org.loyalty_type == LoyaltyType.VISIT


def test_program_type_mismatch_rejected(visit_admin_api):
    r = visit_admin_api.patch(PROGRAM, {
        'cashback_program': {'cashback_rate_bps': 700}
    }, format='json')
    assert r.status_code == 422


def test_organization_id_from_body_is_ignored(visit_admin_api, cashback_org, visit_org):
    """Организация берётся из привязки админа, а не из тела (ТЗ backend §3)."""
    r = visit_admin_api.patch(PROGRAM, {
        'organization_id': str(cashback_org.id),
        'visit_program': {'target_visits': 9},
    }, format='json')
    assert r.status_code == 200

    visit_org.visit_program.refresh_from_db()
    assert visit_org.visit_program.target_visits == 9
    cashback_org.cashback_program.refresh_from_db()
    assert cashback_org.cashback_program.cashback_rate_bps == 500


def test_user_and_director_cannot_use_admin_program(user_api, director_api):
    assert user_api.get(PROGRAM).status_code == 403
    assert director_api.get(PROGRAM).status_code == 403


def test_program_update_is_audited(visit_admin_api, visit_admin):
    from apps.audit.models import AuditAction, AuditLog

    visit_admin_api.patch(PROGRAM, {'visit_program': {'target_visits': 6}}, format='json')
    entry = AuditLog.objects.filter(action=AuditAction.PROGRAM_UPDATED).first()
    assert entry is not None
    assert entry.actor_id == visit_admin.id
    assert entry.before['target_visits'] == 5
    assert entry.after['target_visits'] == 6


def test_organization_always_gets_program_record(db, category, director):
    """Организация без записи программы отвечала 422 PROGRAM_INACTIVE."""
    org = Organization.objects.create(
        name='Салон', slug='salon-test', category=category,
        loyalty_type=LoyaltyType.VISIT, created_by=director,
    )
    assert VisitProgram.objects.filter(organization=org).exists()


def test_admin_can_fix_unconfigured_organization(db, category, director):
    """Сценарий из отчёта: организация создана в обход API, начисление падало."""
    from apps.organizations.models import OrganizationAdmin
    from apps.users.models import Role, User

    org = Organization.objects.create(
        name='Салон красоты', slug='beauty-salon', category=category,
        loyalty_type=LoyaltyType.VISIT, created_by=director,
    )
    VisitProgram.objects.filter(organization=org).delete()  # состояние «до» починки

    admin = User.objects.create(phone='+996775232350', role=Role.ORGANIZATION_ADMIN,
                                is_registration_complete=True)
    OrganizationAdmin.objects.create(organization=org, user=admin, created_by=director)
    api = auth_client(admin)

    assert api.get(PROGRAM).status_code == 200          # запись создаётся на лету
    r = api.patch(PROGRAM, {'visit_program': {'target_visits': 5, 'reward_count': 1}}, format='json')
    assert r.status_code == 200

    earn = post(api, '/api/v1/admin/visits', {'user_id': str(director.id)}, idempotency_key='fix-1')
    assert earn.data['error']['code'] != 'PROGRAM_INACTIVE' if earn.status_code != 200 else True
