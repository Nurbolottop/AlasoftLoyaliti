"""Операции над программой лояльности организации.

Общий код для директора (PATCH /director/organizations/{id}) и владельца
заведения (PATCH /admin/program): правила одинаковые, меняется только то,
откуда берётся организация.
"""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import ConflictError, ErrorCode
from apps.organizations.models import CashbackProgram, LoyaltyType, VisitProgram


def ensure_program(organization):
    """Возвращает программу организации, создавая её с дефолтами при отсутствии.

    Организация без записи программы отвечала 422 PROGRAM_INACTIVE на любое
    начисление и починить это из приложения было нельзя — такое состояние
    возникало при создании организации в обход API (например, из Django-админки).
    """
    if organization.loyalty_type == LoyaltyType.VISIT:
        program, _ = VisitProgram.objects.get_or_create(organization=organization)
    else:
        program, _ = CashbackProgram.objects.get_or_create(organization=organization)
    return program


def program_payload(organization):
    """Форма ответа /admin/program. Описана ProgramSettingsSerializer."""
    from apps.organizations.serializers import ProgramSettingsSerializer

    program = ensure_program(organization)
    is_visit = organization.loyalty_type == LoyaltyType.VISIT
    return ProgramSettingsSerializer({
        'organization': organization,
        'loyalty_type': organization.loyalty_type,
        'visit_program': program if is_visit else None,
        'cashback_program': None if is_visit else program,
        'can_change_loyalty_type': not _has_transactions(organization),
    }).data


def _has_transactions(organization):
    from apps.loyalty.models import Transaction

    return Transaction.objects.filter(organization=organization).exists()


@transaction.atomic
def update_loyalty_program(*, organization, loyalty_type=None, visit_data=None,
                           cashback_data=None, actor=None, request=None):
    """Меняет тип программы и её параметры.

    Смена типа при существующих транзакциях запрещена (ТЗ backend §6): иначе
    история операций начнёт противоречить действующим правилам.
    """
    before = {
        'loyalty_type': organization.loyalty_type,
        **_snapshot(organization),
    }

    if loyalty_type and loyalty_type != organization.loyalty_type:
        if _has_transactions(organization):
            raise ConflictError(
                code=ErrorCode.LOYALTY_TYPE_LOCKED,
                message='Нельзя сменить тип программы: по организации уже есть транзакции',
            )
        organization.loyalty_type = loyalty_type
        organization.save(update_fields=['loyalty_type', 'updated_at'])

    program = ensure_program(organization)
    data = visit_data if organization.loyalty_type == LoyaltyType.VISIT else cashback_data

    if data:
        for field, value in data.items():
            setattr(program, field, value)
        program.save()

    # Программу создал сигнал, поэтому в organization висит её первый,
    # ещё не изменённый экземпляр — сбрасываем кеш связанных объектов.
    organization.refresh_from_db()

    log_action(
        AuditAction.PROGRAM_UPDATED,
        actor=actor,
        request=request,
        entity_type='Organization',
        entity_id=organization.id,
        organization=organization,
        before=before,
        after={'loyalty_type': organization.loyalty_type, **_snapshot(organization)},
    )
    return program


def _snapshot(organization):
    program = organization.program
    if program is None:
        return {}
    if organization.loyalty_type == LoyaltyType.VISIT:
        return {
            'target_visits': program.target_visits,
            'reward_count': program.reward_count,
            'is_active': program.is_active,
        }
    return {
        'cashback_rate_bps': program.cashback_rate_bps,
        'max_spend_percent_bps': program.max_spend_percent_bps,
        'expiry_days': program.expiry_days,
        'is_active': program.is_active,
    }
