"""Шаблоны push/SMS на русском и кыргызском (ТЗ backend §25, BACKEND_TZ §9)."""

from apps.common.money import tiyin_to_som_str
from apps.notifications.models import NotificationApp, NotificationEvent

PUSH_TEMPLATES = {
    NotificationEvent.VISIT_EARNED: {
        'ru': ('{organization}', 'Засчитано посещение. Прогресс {progress}/{target}'),
        'ky': ('{organization}', 'Баруу эсептелди. Прогресс {progress}/{target}'),
    },
    NotificationEvent.GIFT_EARNED: {
        'ru': ('Подарок в {organization}', 'Вы накопили подарок. Покажите его при следующем визите'),
        'ky': ('{organization} белеги', 'Сиз белек чогулттуңуз. Кийинки келгениңизде көрсөтүңүз'),
    },
    NotificationEvent.CASHBACK_EARNED: {
        'ru': ('{organization}', 'Начислено {amount} бонусов'),
        'ky': ('{organization}', '{amount} бонус кошулду'),
    },
    NotificationEvent.REDEMPTION_REQUESTED: {
        'ru': ('Подтвердите списание', '{organization}: {subject}. Подтвердите в приложении'),
        'ky': ('Эсептен чыгарууну ырастаңыз', '{organization}: {subject}. Колдонмодон ырастаңыз'),
    },
    NotificationEvent.REDEMPTION_CONFIRMED: {
        'ru': ('{organization}', 'Списание подтверждено'),
        'ky': ('{organization}', 'Эсептен чыгаруу ырасталды'),
    },
    NotificationEvent.REDEMPTION_REJECTED: {
        'ru': ('{organization}', 'Списание отклонено'),
        'ky': ('{organization}', 'Эсептен чыгаруу четке кагылды'),
    },
    NotificationEvent.CASHBACK_EXPIRING: {
        'ru': ('{organization}', 'Через {days} дн. сгорит {amount} бонусов'),
        'ky': ('{organization}', '{days} күндөн кийин {amount} бонус күйөт'),
    },
    NotificationEvent.CASHBACK_EXPIRED: {
        'ru': ('{organization}', 'Сгорело {amount} бонусов'),
        'ky': ('{organization}', '{amount} бонус күйдү'),
    },
    NotificationEvent.TRANSACTION_REVERSED: {
        'ru': ('{organization}', 'Операция отменена'),
        'ky': ('{organization}', 'Операция жокко чыгарылды'),
    },
    NotificationEvent.FRIEND_JOINED: {
        'ru': ('TOPTO', '{friend_name} присоединился по вашему приглашению'),
        'ky': ('TOPTO', '{friend_name} сиздин чакыруу менен кошулду'),
    },
    NotificationEvent.ORGANIZATION_NEWS: {
        'ru': ('{organization}', '{subject}'),
        'ky': ('{organization}', '{subject}'),
    },
    NotificationEvent.CUSTOMER_JOINED: {
        'ru': ('{organization}', 'Новый клиент: {customer_name}'),
        'ky': ('{organization}', 'Жаңы кардар: {customer_name}'),
    },
    NotificationEvent.ORGANIZATION_BLOCKED: {
        'ru': ('{organization}', 'Заведение заблокировано. {reason}'),
        'ky': ('{organization}', 'Мекеме бөгөттөлдү. {reason}'),
    },
    NotificationEvent.DAILY_SUMMARY: {
        'ru': ('Итоги дня — {organization}', 'Выручка {revenue} сом, клиентов {visitors}, новых {new_customers}'),
        'ky': ('Күндүн жыйынтыгы — {organization}', 'Киреше {revenue} сом, кардарлар {visitors}, жаңы {new_customers}'),
    },
    NotificationEvent.SECURITY_ALERT: {
        'ru': ('PAYDA', '{subject}'),
        'ky': ('PAYDA', '{subject}'),
    },
}

# Те же события, но для сотрудника заведения — другой текст
BUSINESS_PUSH_TEMPLATES = {
    NotificationEvent.REDEMPTION_CONFIRMED: {
        'ru': ('{organization}', '{customer_name} подтвердил списание'),
        'ky': ('{organization}', '{customer_name} эсептен чыгарууну ырастады'),
    },
    NotificationEvent.REDEMPTION_REJECTED: {
        'ru': ('{organization}', '{customer_name}: списание не подтверждено'),
        'ky': ('{organization}', '{customer_name}: эсептен чыгаруу ырасталган жок'),
    },
    NotificationEvent.TRANSACTION_REVERSED: {
        'ru': ('{organization}', 'Ваша операция отменена: {reason}'),
        'ky': ('{organization}', 'Сиздин операция жокко чыгарылды: {reason}'),
    },
}

SMS_TEMPLATES = {
    NotificationEvent.OTP: {
        'ru': 'PAYDA: код подтверждения {code}. Никому его не сообщайте.',
        'ky': 'PAYDA: ырастоо коду {code}. Эч кимге айтпаңыз.',
    },
    NotificationEvent.SECURITY_ALERT: {
        'ru': 'PAYDA: {subject}',
        'ky': 'PAYDA: {subject}',
    },
}


class _SafeDict(dict):
    """Неизвестный плейсхолдер — пустая строка, а не KeyError."""

    def __missing__(self, key):
        return ''


def _normalize(context):
    data = _SafeDict(context or {})
    if 'amount_tiyin' in data:
        data['amount'] = tiyin_to_som_str(data['amount_tiyin']).removesuffix('.00')
    if 'revenue_tiyin' in data:
        data['revenue'] = tiyin_to_som_str(data['revenue_tiyin']).removesuffix('.00')
    data.setdefault('organization', 'PAYDA')
    data.setdefault('subject', '')
    return data


def render_push(event, language, context, app=NotificationApp.CLIENT):
    templates = {}
    if app == NotificationApp.BUSINESS:
        templates = BUSINESS_PUSH_TEMPLATES.get(event, {})
    templates = templates or PUSH_TEMPLATES.get(event, {})
    title_tpl, body_tpl = templates.get(language) or templates.get('ru') or ('PAYDA', '')
    data = _normalize(context)
    return title_tpl.format_map(data).strip(), body_tpl.format_map(data).strip()


def render_sms(event, language, context):
    templates = SMS_TEMPLATES.get(event, {})
    tpl = templates.get(language) or templates.get('ru') or ''
    return tpl.format_map(_normalize(context))
