"""Шаблоны push/SMS на русском и кыргызском (ТЗ backend §25)."""

from apps.common.money import tiyin_to_som_str
from apps.notifications.models import NotificationEvent

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
        'ru': ('{organization}', 'Начислено {amount} сом кэшбэка'),
        'ky': ('{organization}', '{amount} сом кэшбэк кошулду'),
    },
    NotificationEvent.REDEMPTION_REQUESTED: {
        'ru': ('Подтвердите списание', '{organization}: {subject}. Подтвердите в приложении'),
        'ky': ('Эсептен чыгарууну ырастаңыз', '{organization}: {subject}. Колдонмодон ырастаңыз'),
    },
    NotificationEvent.REDEMPTION_CONFIRMED: {
        'ru': ('{organization}', 'Списание подтверждено: {subject}'),
        'ky': ('{organization}', 'Эсептен чыгаруу ырасталды: {subject}'),
    },
    NotificationEvent.REDEMPTION_REJECTED: {
        'ru': ('{organization}', 'Списание отклонено'),
        'ky': ('{organization}', 'Эсептен чыгаруу четке кагылды'),
    },
    NotificationEvent.CASHBACK_EXPIRING: {
        'ru': ('{organization}', 'Через {days} дн. сгорит {amount} сом кэшбэка'),
        'ky': ('{organization}', '{days} күндөн кийин {amount} сом кэшбэк күйөт'),
    },
    NotificationEvent.CASHBACK_EXPIRED: {
        'ru': ('{organization}', 'Сгорело {amount} сом кэшбэка'),
        'ky': ('{organization}', '{amount} сом кэшбэк күйдү'),
    },
    NotificationEvent.TRANSACTION_REVERSED: {
        'ru': ('{organization}', 'Операция отменена администратором'),
        'ky': ('{organization}', 'Операция администратор тарабынан жокко чыгарылды'),
    },
    NotificationEvent.SECURITY_ALERT: {
        'ru': ('AlaSoft', '{subject}'),
        'ky': ('AlaSoft', '{subject}'),
    },
}

SMS_TEMPLATES = {
    NotificationEvent.OTP: {
        'ru': 'AlaSoft: код подтверждения {code}. Никому его не сообщайте.',
        'ky': 'AlaSoft: ырастоо коду {code}. Эч кимге айтпаңыз.',
    },
}


def _normalize(context):
    data = dict(context or {})
    if 'amount_tiyin' in data:
        data.setdefault('amount', tiyin_to_som_str(data['amount_tiyin']))
    data.setdefault('organization', 'AlaSoft')
    data.setdefault('subject', '')
    return data


def render_push(event, language, context):
    templates = PUSH_TEMPLATES.get(event, {})
    title_tpl, body_tpl = templates.get(language) or templates.get('ru') or ('AlaSoft', '')
    data = _normalize(context)
    return title_tpl.format(**data), body_tpl.format(**data)


def render_sms(event, language, context):
    templates = SMS_TEMPLATES.get(event, {})
    tpl = templates.get(language) or templates.get('ru') or ''
    return tpl.format(**_normalize(context))
