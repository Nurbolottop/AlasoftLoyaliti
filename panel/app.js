/* TOPTO Admin — панель управления платформой.
   Отдельное веб-приложение поверх Director API (ТЗ §16, ТЗ backend §1).
   Своей бизнес-логики не содержит: суммы, лимиты и права считает сервер. */

const API = '/api/v1';

const safeStorage = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* приватный режим */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* приватный режим */ } },
};

const store = {
  get access() { return safeStorage.get('as_access'); },
  get refresh() { return safeStorage.get('as_refresh'); },
  get user() { try { return JSON.parse(safeStorage.get('as_user')); } catch { return null; } },
  save(tokens, user) {
    if (tokens?.access) safeStorage.set('as_access', tokens.access);
    if (tokens?.refresh) safeStorage.set('as_refresh', tokens.refresh);
    if (user) safeStorage.set('as_user', JSON.stringify(user));
  },
  clear() { ['as_access', 'as_refresh', 'as_user'].forEach(safeStorage.del); },
};

/* ================= API ================= */

class ApiError extends Error {
  constructor(code, message, details, status) {
    super(message || 'Ошибка запроса');
    Object.assign(this, { code, details: details || {}, status });
  }
}

async function request(path, { method = 'GET', body, auth = true, retry = true } = {}) {
  const isForm = body instanceof FormData;
  const headers = isForm ? {} : { 'Content-Type': 'application/json' };
  if (auth && store.access) headers['Authorization'] = `Bearer ${store.access}`;
  if (method !== 'GET' && !['/auth/director/login', '/auth/refresh'].includes(path)) {
    headers['Idempotency-Key'] = crypto.randomUUID();
  }

  const res = await fetch(API + path, {
    method, headers, body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
  });

  // access живёт 15 минут — по 401 молча меняем его на новый и повторяем
  if (res.status === 401 && auth && retry && store.refresh) {
    if (await refreshTokens()) return request(path, { method, body, auth, retry: false });
    logout();
    throw new ApiError('TOKEN_INVALID', 'Сессия истекла, войдите заново', {}, 401);
  }

  let payload = null;
  try { payload = await res.json(); } catch { /* пустой ответ */ }

  if (!res.ok || payload?.success === false) {
    const e = payload?.error || {};
    throw new ApiError(e.code || 'HTTP_' + res.status, e.message, e.details, res.status);
  }
  return payload;
}

async function refreshTokens() {
  try {
    const r = await request('/auth/refresh', {
      method: 'POST', body: { refresh: store.refresh }, auth: false, retry: false,
    });
    store.save(r.data);
    return true;
  } catch { return false; }
}

function qs(params) {
  if (!params) return '';
  const s = Object.entries(params)
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  return s ? '?' + s : '';
}

const api = {
  login: (phone, password) => request('/auth/director/login', {
    method: 'POST', body: { phone, password }, auth: false,
  }),
  dashboard: (q) => request('/director/dashboard' + qs(q)),
  categories: () => request('/director/categories'),
  orgs: (q) => request('/director/organizations' + qs(q)),
  org: (id) => request(`/director/organizations/${id}`),
  orgCreate: (body) => request('/director/organizations', { method: 'POST', body }),
  orgUpdate: (id, body) => request(`/director/organizations/${id}`, { method: 'PATCH', body }),
  orgBlock: (id, reason) => request(`/director/organizations/${id}/block`, { method: 'POST', body: { reason } }),
  orgUnblock: (id) => request(`/director/organizations/${id}/unblock`, { method: 'POST', body: {} }),
  orgAdmin: (id, body) => request(`/director/organizations/${id}/admin`, { method: 'POST', body }),
  orgAdminRevoke: (id) => request(`/director/organizations/${id}/admin`, { method: 'DELETE' }),
  users: (q) => request('/director/users' + qs(q)),
  user: (id) => request(`/director/users/${id}`),
  transactions: (q) => request('/director/transactions' + qs(q)),
  reverse: (id, reason, force) => request(`/director/transactions/${id}/reverse`, {
    method: 'POST', body: { reason, force: !!force },
  }),
  statistics: (q) => request('/director/statistics' + qs(q)),
  audit: (q) => request('/director/audit' + qs(q)),
};

/* ================= иконки ================= */

const ICONS = {
  home: '<path d="m3 10.5 9-7.5 9 7.5V20a2 2 0 0 1-2 2h-4.5v-6.5h-5V22H5a2 2 0 0 1-2-2z"/>',
  store: '<path d="M3.5 9 5 4h14l1.5 5"/><path d="M3.5 9a2.8 2.8 0 0 0 5.67 0 2.8 2.8 0 0 0 5.66 0 2.8 2.8 0 0 0 5.67 0"/><path d="M5 11.5V20h14v-8.5"/><path d="M10 20v-4.5h4V20"/>',
  users: '<circle cx="9" cy="8" r="3.6"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="M15.5 4.6a3.6 3.6 0 0 1 0 6.8"/><path d="M21.5 20.5a6.5 6.5 0 0 0-3.6-5.8"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  'user-plus': '<circle cx="9" cy="8" r="3.6"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="M19 8v6M16 11h6"/>',
  'user-check': '<circle cx="9" cy="8" r="3.6"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="m15.5 11 2 2 4-4"/>',
  receipt: '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><path d="M7.5 8.5h9M7.5 12h9M7.5 15.5h5"/>',
  bars: '<path d="M4 20v-7M9 20V9M14 20V5M19 20v-9"/>',
  shield: '<path d="M12 3 4.5 6v5.5c0 4.8 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.7 7.5-9.5V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
  logout: '<path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4"/><path d="m16 16.5 4.5-4.5L16 7.5"/><path d="M20.5 12H9.5"/>',
  login: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M9.5 16.5 14 12 9.5 7.5"/><path d="M14 12H3.5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  'chev-down': '<path d="m6 9 6 6 6-6"/>',
  'chev-right': '<path d="m9 6 6 6-6 6"/>',
  'chev-left': '<path d="m15 6-6 6 6 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
  unlock: '<rect x="4.5" y="11" width="15" height="10" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 7.6-1.8"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8h.01"/>',
  gift: '<rect x="3.5" y="8" width="17" height="4.5" rx="1.2"/><path d="M5 12.5V20h14v-7.5M12 8v12"/><path d="M12 8S10.6 3.6 8.2 4.6C6.4 5.4 8 8 12 8zM12 8s1.4-4.4 3.8-3.4C17.6 5.4 16 8 12 8z"/>',
  phone: '<path d="M5.2 3.5h3.5l1.8 4.6-2.3 1.4a11 11 0 0 0 6.3 6.3l1.4-2.3 4.6 1.8v3.5a2 2 0 0 1-2.2 2A17 17 0 0 1 3.2 5.7a2 2 0 0 1 2-2.2z"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7 8.5 6 8.5-6"/>',
  pin: '<path d="M12 21s-7-6.1-7-11.4a7 7 0 0 1 14 0C19 14.9 12 21 12 21z"/><circle cx="12" cy="9.6" r="2.5"/>',
  idcard: '<rect x="3" y="4.5" width="18" height="15" rx="3"/><circle cx="9" cy="11" r="2"/><path d="M5.8 16a3.3 3.3 0 0 1 6.4 0M14.5 9.5h4M14.5 13h4"/>',
  edit: '<path d="M4 20h4L19.2 8.8a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  more: '<circle cx="5.5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="18.5" cy="12" r="1.4" fill="currentColor"/>',
  'more-v': '<circle cx="12" cy="5.5" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="18.5" r="1.4" fill="currentColor"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12.2 2.8 2.8L16 9.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  'pause-circle': '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  'x-circle': '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.9z"/>',
  sparkle: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/><circle cx="12" cy="12" r="3"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12.5 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
  cart: '<circle cx="9.5" cy="20" r="1.4"/><circle cx="17.5" cy="20" r="1.4"/><path d="M2.5 3.5h3l2.4 11.6a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 1.9-1.5L21.5 8H6.4"/>',
  coins: '<ellipse cx="10" cy="6" rx="6.5" ry="2.8"/><path d="M3.5 6v4c0 1.5 2.9 2.8 6.5 2.8s6.5-1.3 6.5-2.8V6"/><path d="M3.5 10v4c0 1.5 2.9 2.8 6.5 2.8"/><path d="M3.5 14v4c0 1.5 2.9 2.8 6.5 2.8"/><ellipse cx="16.5" cy="15.5" rx="4.5" ry="2"/><path d="M12 15.5V19c0 1.1 2 2 4.5 2s4.5-.9 4.5-2v-3.5"/>',
  wallet: '<path d="M19 7V5.5A1.5 1.5 0 0 0 17.5 4H5.5a2 2 0 0 0 0 4H19a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 20H5.5a2 2 0 0 1-2-2V6"/><path d="M16 14h.01"/>',
  percent: '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
  tag: '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l9 9a2 2 0 0 1 0 2.8l-6 6a2 2 0 0 1-2.8 0z"/><circle cx="8" cy="8" r="1.6"/>',
  download: '<path d="M12 3.5v12M7 10.5l5 5 5-5"/><path d="M4.5 20.5h15"/>',
  excel: '<rect x="2.5" y="5" width="11" height="14" rx="2"/><path d="m5.5 9 5 6M10.5 9l-5 6"/><path d="M13.5 7H19a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-5.5"/><path d="M17 7v10M13.5 12H21"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  lock: '<rect x="4.5" y="11" width="15" height="10" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/><path d="M12 15v2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  cashback: '<path d="M3.5 12a8.5 8.5 0 1 0 2.8-6.3L3.5 8.3"/><path d="M3.5 3.5v4.8h4.8"/><path d="M12 8v4l2.8 1.8"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  flame: '<path d="M12 21.5c3.9 0 6.5-2.7 6.5-6.4 0-4.6-4.3-6.6-4.6-11.6-2.8 1.7-5.4 5.2-5.4 8-.9-.6-1.8-1.7-1.9-3.3C5.7 10 5.5 12 5.5 15.1c0 3.7 2.6 6.4 6.5 6.4z"/>',
  key: '<circle cx="7.5" cy="15.5" r="4"/><path d="m10.4 12.6 9.1-9.1M16.5 6.5l3 3M14 9l2 2"/>',
  'arrow-up': '<path d="M12 19V5.5M6.5 11 12 5.5l5.5 5.5"/>',
  'arrow-down': '<path d="M12 5v13.5M6.5 13l5.5 5.5 5.5-5.5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  'user-x': '<circle cx="9" cy="8" r="3.6"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="m17 9 4 4M21 9l-4 4"/>',
  image: '<rect x="3.5" y="4" width="17" height="16" rx="3"/><circle cx="9" cy="9.5" r="1.8"/><path d="m4 17.5 5-5 4.5 4.5 2.5-2.5 4 4"/>',
};

const icon = (name, cls = '') =>
  `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;

let gradSeq = 0;
function logoMark(hole = '#fff', cls = 'mark') {
  const id = 'lg' + (++gradSeq);
  return `<svg class="${cls}" viewBox="0 0 48 56" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#D27BFF"/><stop offset=".55" stop-color="#9D4DFF"/><stop offset="1" stop-color="#6A1BF2"/>
    </linearGradient></defs>
    <path d="M8 4h32a7 7 0 0 1 0 14H31v31a7 7 0 0 1-14 0V18H8A7 7 0 0 1 8 4z" fill="url(#${id})"/>
    <circle cx="38" cy="11" r="3.2" fill="${hole}" opacity=".9"/>
  </svg>`;
}
const brandHtml = (hole) => `${logoMark(hole)}<div class="name"><b>TOPTO</b><span>Admin</span></div>`;
const phoneArt = () => `<div class="phone-art"><div class="phone-card">${logoMark('#F6F0FF', 'ic-logo')}</div></div>`;
const wave = (id) => `<svg class="wave" viewBox="0 0 600 200" preserveAspectRatio="none" aria-hidden="true">
  <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C9A8FF" stop-opacity=".55"/><stop offset="1" stop-color="#7C3AED" stop-opacity=".95"/></linearGradient></defs>
  <path d="M0 200 C120 120 220 210 330 150 S520 40 600 70 V200 Z" fill="url(#${id})"/>
  <path d="M120 200 C240 150 330 200 430 150 S560 110 600 120 V200 Z" fill="#6A1BF2" opacity=".55"/>
</svg>`;

/* ================= форматирование ================= */

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => Number(n ?? 0).toLocaleString('ru-RU');
const somValue = (t) => Number(t ?? 0) / 100;
const money = (t) => t == null ? '—' : somValue(t).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' сом';
const pct = (bps) => bps == null ? '—' : (bps / 100).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + '%';
const pad = (n) => String(n).padStart(2, '0');
const dDate = (s) => { if (!s) return '—'; const d = new Date(s); return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`; };
const dTime = (s) => { if (!s) return '—'; const d = new Date(s); return `${dDate(s)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dShortTime = (s) => { const d = new Date(s); return `${pad(d.getDate())} ${MONTHS[d.getMonth()]}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const dayLabel = (s) => { const d = parseIso(s); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };

function initials(name, fallback = '?') {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return fallback;
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
}
function tone(str) {
  let h = 0;
  for (const ch of String(str)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return 'tone-' + (1 + (h % 6));
}
const personName = (p) => p?.full_name || p?.phone || '—';

function avatar(entity, cls = '') {
  const url = entity?.logo_url || entity?.avatar_url;
  const name = entity?.name || entity?.full_name || entity?.phone;
  if (url) return `<div class="avatar ${cls}"><img src="${esc(url)}" alt=""></div>`;
  return `<div class="avatar ${cls} ${cls.includes('soft') || cls.includes('xxl') ? '' : tone(name)}">${esc(initials(name))}</div>`;
}

/* ================= справочники ================= */

const ORG_STATUS = { ACTIVE: ['ok', 'Активен'], BLOCKED: ['bad', 'Заблокирован'] };
const TX_STATUS = { COMPLETED: ['ok', 'Успешно'], REVERSED: ['bad', 'Отменена'], PENDING: ['warn', 'Ожидает'], FAILED: ['bad', 'Ошибка'] };
const badge = (map, s, extra = '') => { const [c, t] = map[s] || ['neutral', s]; return `<span class="badge ${c} ${extra}">${esc(t)}</span>`; };
const userBadge = (u, extra = '') => u.is_active ? `<span class="badge ok ${extra}">Активен</span>` : `<span class="badge bad ${extra}">Заблокирован</span>`;

const ACTOR_LABEL = { USER: 'Клиент', ADMIN: 'Администратор', DIRECTOR: 'Директор', SYSTEM: 'Система' };

const TX_META = {
  VISIT_EARN: { icon: 'calendar', label: 'Посещение' },
  GIFT_CREATED: { icon: 'gift', label: 'Подарок начислен' },
  GIFT_REDEEM: { icon: 'gift', label: 'Бесплатный визит' },
  CASHBACK_EARN: { icon: 'cashback', label: 'Cashback начисление' },
  CASHBACK_SPEND: { icon: 'wallet', label: 'Cashback списание' },
  CASHBACK_EXPIRE: { icon: 'flame', label: 'Cashback сгорел' },
  REVERSAL: { icon: 'undo', label: 'Отмена операции' },
};
const REVERSIBLE = ['VISIT_EARN', 'CASHBACK_EARN', 'CASHBACK_SPEND', 'GIFT_REDEEM'];

const AUDIT_META = {
  LOGIN: ['login', 'Вход в систему'], LOGIN_FAILED: ['x-circle', 'Неудачный вход'], LOGOUT: ['logout', 'Выход из системы'],
  PIN_SET: ['lock', 'Установлен PIN'], PIN_RESET: ['lock', 'Сброшен PIN'],
  PHONE_CHANGED: ['phone', 'Изменён номер телефона'], AVATAR_UPDATED: ['image', 'Изменено фото профиля'],
  ACCOUNT_DELETED: ['user-x', 'Аккаунт удалён'],
  ORGANIZATION_CREATED: ['store', 'Создана организация'], ORGANIZATION_UPDATED: ['edit', 'Изменены данные организации'],
  ORGANIZATION_BLOCKED: ['ban', 'Организация заблокирована'], ORGANIZATION_UNBLOCKED: ['unlock', 'Организация разблокирована'],
  PROGRAM_UPDATED: ['settings', 'Изменены настройки бонусов'],
  ADMIN_CREATED: ['key', 'Создан администратор'], ADMIN_RESET: ['key', 'Сброшен доступ администратора'],
  VISIT_EARNED: ['calendar', 'Начислено посещение'], CASHBACK_EARNED: ['cashback', 'Начислен cashback'],
  CASHBACK_SPENT: ['wallet', 'Списан cashback'], GIFT_REDEEMED: ['gift', 'Использован подарок'],
  REDEMPTION_CREATED: ['receipt', 'Запрос на списание'], REDEMPTION_CONFIRMED: ['check-circle', 'Списание подтверждено'],
  REDEMPTION_REJECTED: ['x-circle', 'Списание отклонено'], TRANSACTION_REVERSED: ['x-circle', 'Операция отменена'],
  CASHBACK_EXPIRED: ['flame', 'Cashback сгорел'],
};
const AUDIT_FIELD = {
  name: 'Название', status: 'Статус', loyalty_type: 'Тип программы', target_visits: 'Посещений до подарка',
  reward_count: 'Подарков за цикл', reward_title_ru: 'Подарок', cashback_rate_bps: 'Размер бонуса',
  max_spend_percent_bps: 'Макс. оплата бонусами', expiry_days: 'Срок сгорания, дн.', phone: 'Телефон',
  is_active: 'Активен', earn: 'Начислено', purchase_total: 'Сумма чека', spend: 'Списано',
};

function programShort(o) {
  if (o.loyalty_type === 'VISIT') {
    const p = o.visit_program || o.program;
    return p ? `${p.target_visits}+${p.reward_count}` : 'N+1';
  }
  const p = o.cashback_program || o.program;
  return p ? `Кешбэк ${pct(p.cashback_rate_bps)}` : 'Кешбэк';
}
function programText(o) {
  if (o.loyalty_type === 'VISIT') {
    const p = o.visit_program || o.program;
    if (!p) return 'Программа не настроена';
    const gifts = p.reward_count > 1 ? `${p.reward_count} подарка` : 'подарок';
    return `После ${p.target_visits} посещений — ${gifts}${p.reward_title_ru ? ` «${p.reward_title_ru}»` : ''}`;
  }
  const p = o.cashback_program || o.program;
  if (!p) return 'Программа не настроена';
  return `${pct(p.cashback_rate_bps)} с покупки, оплата бонусами до ${pct(p.max_spend_percent_bps)} чека, сгорают через ${p.expiry_days} дн.`;
}

/* ================= период ================= */

const PRESETS = {
  today: ['Сегодня', () => [new Date(), new Date()]],
  '7': ['7 дней', () => [addDays(new Date(), -6), new Date()]],
  '30': ['30 дней', () => [addDays(new Date(), -29), new Date()]],
  '90': ['90 дней', () => [addDays(new Date(), -89), new Date()]],
  month: ['Этот месяц', () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth(), 1), n]; }],
  prev: ['Прошлый месяц', () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth() - 1, 1), new Date(n.getFullYear(), n.getMonth(), 0)]; }],
  year: ['Год', () => [addDays(new Date(), -364), new Date()]],
};
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }

const period = (() => {
  let saved = null;
  try { saved = JSON.parse(safeStorage.get('topto_period')); } catch { /* пусто */ }
  const state = { preset: '30', from: '', to: '' };
  if (saved?.preset === 'custom' && saved.from && saved.to) Object.assign(state, saved);
  else if (saved?.preset && PRESETS[saved.preset]) state.preset = saved.preset;
  return state;
})();
function setPeriod(preset, from, to) {
  period.preset = preset;
  if (preset !== 'custom') [from, to] = PRESETS[preset][1]().map(isoDate);
  period.from = from; period.to = to;
  safeStorage.set('topto_period', JSON.stringify(period));
}
setPeriod(period.preset, period.from, period.to);
const periodParams = () => ({ date_from: period.from, date_to: period.to });
function periodLabel() {
  const a = parseIso(period.from), b = parseIso(period.to);
  const y = new Date().getFullYear();
  const f = (d) => `${pad(d.getDate())} ${MONTHS[d.getMonth()]}${d.getFullYear() !== y ? ' ' + d.getFullYear() : ''}`;
  return period.from === period.to ? f(a) : `${f(a)} – ${f(b)}`;
}
const periodSuffix = () => period.preset === 'custom' ? 'за период'
  : period.preset === 'today' ? 'за сегодня' : `за ${PRESETS[period.preset][0].toLowerCase()}`;

const rangeButton = () => `<button class="range-btn" data-range>${icon('calendar')}<span>${esc(periodLabel())}</span>${icon('chev-down', 'chev')}</button>`;

function openRangePicker(anchor) {
  openPopover(anchor, `
    <div class="presets">${Object.entries(PRESETS).map(([k, [t]]) =>
      `<button data-preset="${k}">${esc(t)}</button>`).join('')}</div>
    <div class="dates">
      <label>С<input type="date" id="rp-from" value="${period.from}"></label>
      <label>По<input type="date" id="rp-to" value="${period.to}"></label>
    </div>
    <button class="btn primary wide" id="rp-apply">Применить</button>`, (p) => {
    p.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
      setPeriod(b.dataset.preset); closePopover(); rerender();
    });
    p.querySelector('#rp-apply').onclick = () => {
      let f = p.querySelector('#rp-from').value, t = p.querySelector('#rp-to').value;
      if (!f || !t) return;
      if (f > t) [f, t] = [t, f];
      setPeriod('custom', f, t); closePopover(); rerender();
    };
  });
}

/* ================= элементы интерфейса ================= */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const view = () => $('#view');

function hydrateIcons(root = document) {
  $$('i[data-icon]', root).forEach(el => { el.outerHTML = icon(el.dataset.icon); });
  $$('[data-logo]', root).forEach(el => { el.innerHTML = brandHtml(el.closest('.login-art') ? '#4512B8' : '#3A0E99'); el.removeAttribute('data-logo'); });
}

function toast(msg, isError) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isError ? ' err' : '');
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, 4200);
}
function showError(e) {
  console.error(e);
  toast(e instanceof ApiError ? e.message : 'Не удалось выполнить запрос', true);
}

function modal(title, html) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal').hidden = false;
  setTimeout(() => $('#modal-body input:not([type=hidden]), #modal-body select')?.focus(), 30);
}
const closeModal = () => { $('#modal').hidden = true; };

/* поповер: выбор периода и меню «⋯» */
function openPopover(anchor, html, bind, cls = '') {
  const p = $('#popover');
  p.className = 'popover ' + cls;
  p.innerHTML = html;
  p.hidden = false;
  const r = anchor.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.right + scrollX - p.offsetWidth, document.documentElement.clientWidth - p.offsetWidth - 8));
  let top = r.bottom + scrollY + 8;
  if (r.bottom + p.offsetHeight + 16 > innerHeight && r.top > p.offsetHeight + 16) top = r.top + scrollY - p.offsetHeight - 8;
  p.style.left = left + 'px';
  p.style.top = top + 'px';
  p._anchor = anchor;
  bind?.(p);
}
function closePopover() { $('#popover').hidden = true; }
document.addEventListener('mousedown', (e) => {
  const p = $('#popover');
  if (!p.hidden && !p.contains(e.target) && !p._anchor?.contains(e.target)) closePopover();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closePopover(); closeModal(); }
});

function openMenu(anchor, items) {
  openPopover(anchor, items.map((it, i) =>
    `<button data-i="${i}" class="${it.danger ? 'danger' : ''}">${icon(it.icon)}${esc(it.label)}</button>`).join(''), (p) => {
    p.querySelectorAll('[data-i]').forEach(b => b.onclick = () => { closePopover(); items[+b.dataset.i].onClick(); });
  }, 'menu');
}

const loading = () => { view().innerHTML = '<div class="spinner">Загрузка…</div>'; };
const empty = (text) => `<div class="empty">${esc(text)}</div>`;

function pageHead({ title, sub, subViolet, crumbs, actions = '' }) {
  return `<div class="page-head">
    <div>
      ${crumbs ? `<div class="crumbs">${crumbs}</div>` : ''}
      ${title ? `<h1>${esc(title)}</h1>` : ''}
      ${sub ? `<div class="sub ${subViolet ? 'violet' : ''}">${esc(sub)}</div>` : ''}
    </div>
    <div class="head-actions">${actions}</div>
  </div>`;
}
const crumbsHtml = (items) => items.map(([t, href], i) => (href ? `<a href="${href}">${esc(t)}</a>` : `<span>${esc(t)}</span>`)
  + (i < items.length - 1 ? icon('chev-right') : '')).join('');

function statCard({ icon: ic, tone: tn = '', label, value, delta, tint = '', extra = '' }) {
  return `<div class="stat ${tint ? 'tint-' + tint : ''}">
    <div class="ibox ${tn}">${icon(ic)}</div>
    <div class="body">
      <div class="label">${esc(label)}</div>
      ${value != null ? `<div class="value">${value}</div>` : ''}
      ${extra}
      ${delta ? `<div class="delta">${delta}</div>` : ''}
    </div>
  </div>`;
}
const deltaUp = (text, suffix) => `<b>${icon('arrow-up')}${esc(text)}</b> ${esc(suffix || periodSuffix())}`;
const deltaPlain = (text) => esc(text);

/* постраничная навигация с номерами страниц */
function pager(meta) {
  const p = meta?.pagination;
  if (!p || !p.total_items) return '';
  const from = (p.page - 1) * p.page_size + 1;
  const to = Math.min(p.page * p.page_size, p.total_items);
  const info = `<div class="muted">Показано ${num(from)}–${num(to)} из ${num(p.total_items)}</div>`;
  if (p.total_pages <= 1) return `<div class="footer-bar">${info}</div>`;
  const pages = pageList(p.page, p.total_pages);
  return `<div class="footer-bar">${info}<div class="pager">
    <button data-page="${p.page - 1}" ${p.has_previous ? '' : 'disabled'} aria-label="Назад">${icon('chev-left')}</button>
    ${pages.map(n => n === '…' ? '<span class="dots">…</span>'
      : `<button data-page="${n}" class="${n === p.page ? 'active' : ''}">${n}</button>`).join('')}
    <button data-page="${p.page + 1}" ${p.has_next ? '' : 'disabled'} aria-label="Вперёд">${icon('chev-right')}</button>
  </div></div>`;
}
function pageList(cur, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  if (cur <= 4) return [1, 2, 3, 4, 5, '…', total];
  if (cur >= total - 3) return [1, '…', total - 4, total - 3, total - 2, total - 1, total];
  return [1, '…', cur - 1, cur, cur + 1, '…', total];
}
function bindPager(root, onPage) {
  $$('[data-page]', root).forEach(b => b.onclick = () => onPage(Number(b.dataset.page)));
}

const searchBox = (id, placeholder, value) =>
  `<div class="search grow">${icon('search')}<input id="${id}" placeholder="${esc(placeholder)}" value="${esc(value)}" autocomplete="off"></div>`;
const selectBox = (id, options, value) =>
  `<select id="${id}">${options.map(([v, t]) => `<option value="${esc(v)}" ${String(value) === String(v) ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
const exportBtn = (id = 'export-btn') => `<button class="btn outline" id="${id}">${icon('excel')}Экспорт в Excel</button>`;

/* поиск с задержкой: не дёргаем API на каждую букву */
function bindSearch(input, apply) {
  if (!input) return;
  let t;
  input.oninput = () => { clearTimeout(t); t = setTimeout(() => apply(input.value.trim()), 350); };
  input.onkeydown = (e) => { if (e.key === 'Enter') { clearTimeout(t); apply(input.value.trim()); } };
}

/* ================= экспорт в Excel (CSV) ================= */

async function fetchAll(fn, params, limit = 5000) {
  const rows = [];
  for (let page = 1; rows.length < limit; page++) {
    const { data, meta } = await fn({ ...params, page, page_size: 100 });
    rows.push(...data);
    if (!meta?.pagination?.has_next) break;
  }
  return rows;
}
function downloadCsv(name, headers, rows) {
  // «;» и BOM — так Excel с русской локалью сразу раскладывает по колонкам и видит кириллицу
  const cell = (v) => {
    const s = String(v ?? '');
    return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = '﻿' + [headers, ...rows].map(r => r.map(cell).join(';')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: `${name}_${isoDate(new Date())}.csv` });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function runExport(btn, job) {
  const html = btn.innerHTML;
  btn.disabled = true; btn.textContent = 'Готовим файл…';
  try { await job(); toast('Файл выгружен'); }
  catch (e) { showError(e); }
  finally { btn.disabled = false; btn.innerHTML = html; }
}

/* ================= графики (SVG без библиотек) ================= */

const charts = new Map();
let chartSeq = 0;
function chartBox(cfg) {
  const id = 'chart' + (++chartSeq);
  charts.set(id, cfg);
  return `<div class="chart" id="${id}"></div>`;
}
function drawCharts() {
  for (const [id, cfg] of charts) {
    const el = document.getElementById(id);
    if (!el) { charts.delete(id); continue; }
    drawChart(el, cfg);
  }
}
let resizeT;
window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(drawCharts, 120); });

function niceScale(max) {
  if (max <= 0) return { top: 4, step: 1 };
  const raw = max / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
  const s = Math.max(step, max < 5 ? 1 : step);
  return { top: Math.ceil(max / s) * s, step: s };
}
function monotonePath(pts) {
  const n = pts.length;
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  const dx = [], m = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / (dx[i] || 1); }
  const t = [m[0]];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  t[n - 1] = m[n - 2];
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += ` C${pts[i][0] + h},${pts[i][1] + t[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

function drawChart(el, cfg) {
  const { labels, series, height = 300, fmt = num, endLabels = true, dots = false } = cfg;
  const W = Math.max(el.clientWidth, 280), H = height;
  const n = labels.length;
  const max = Math.max(0, ...series.flatMap(s => s.values));
  const { top, step } = niceScale(max);
  const axisW = Math.max(...Array.from({ length: Math.round(top / step) + 1 }, (_, i) => fmt(i * step).length)) * 8 + 18;
  const L = axisW, R = endLabels ? 44 : 20, T = endLabels ? 44 : 16, B = 38;
  const x = (i) => n === 1 ? (L + W - R) / 2 : L + i * (W - L - R) / (n - 1);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const uid = el.id;

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">`;
  svg += '<defs>' + series.map((s, i) => `<linearGradient id="${uid}g${i}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="${s.color}" stop-opacity=".32"/><stop offset="1" stop-color="${s.color}" stop-opacity=".02"/></linearGradient>`).join('') + '</defs>';
  for (let v = 0; v <= top + 1e-9; v += step) {
    svg += `<line class="grid-line" x1="${L}" x2="${W - R + 10}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text class="axis" x="${L - 14}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
  }
  const ticks = Math.min(n, Math.max(2, Math.floor((W - L - R) / 90)));
  // равный шаг подписей; последняя точка всегда подписана, соседнюю слишком близкую убираем
  const stepX = Math.max(1, Math.ceil((n - 1) / Math.max(1, ticks - 1)));
  const shown = new Set();
  for (let i = 0; i < n; i += stepX) shown.add(i);
  if (n > 1 && !shown.has(n - 1)) {
    const prev = Math.floor((n - 1) / stepX) * stepX;
    if (n - 1 - prev < stepX * 0.6) shown.delete(prev);
    shown.add(n - 1);
  }
  shown.forEach(i => { svg += `<text class="axis" x="${x(i)}" y="${H - 10}" text-anchor="middle">${esc(labels[i])}</text>`; });

  series.forEach((s, si) => {
    const pts = s.values.map((v, i) => [x(i), y(v)]);
    const line = monotonePath(pts);
    if (s.fill) svg += `<path d="${line} L${pts[n - 1][0]},${y(0)} L${pts[0][0]},${y(0)} Z" fill="url(#${uid}g${si})"/>`;
    svg += `<path d="${line}" fill="none" stroke="${s.color}" stroke-width="${s.width || 3}" stroke-linecap="round"/>`;
    if (dots) shown.forEach(i => { svg += `<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="5" fill="${s.color}" stroke="#fff" stroke-width="2"/>`; });
    if (endLabels && n) {
      const [lx, ly] = pts[n - 1];
      const text = fmt(s.values[n - 1]);
      const w = text.length * 9 + 22;
      const bx = Math.min(lx - w / 2, W - w - 2);
      svg += `<circle cx="${lx}" cy="${ly}" r="6" fill="#fff" stroke="${s.color}" stroke-width="3"/>`;
      svg += `<rect x="${bx}" y="${ly - 44}" width="${w}" height="30" rx="9" fill="${s.color}"/>`;
      svg += `<text x="${bx + w / 2}" y="${ly - 24}" text-anchor="middle" fill="#fff" font-size="15" font-weight="700" font-family="Manrope, sans-serif">${esc(text)}</text>`;
    }
  });
  svg += `<g class="hover" style="display:none"><line x1="0" x2="0" y1="${T - 10}" y2="${y(0)}" stroke="#C7B2FF" stroke-dasharray="4 4"/>${series.map(s => `<circle r="5.5" fill="#fff" stroke="${s.color}" stroke-width="3"/>`).join('')}</g>`;
  svg += `<rect x="${L}" y="0" width="${Math.max(1, W - L - R)}" height="${H - B}" fill="transparent" class="hit"/>`;
  svg += '</svg>';
  el.innerHTML = svg + '<div class="tip" hidden></div>';

  const hit = $('.hit', el), hover = $('.hover', el), tip = $('.tip', el);
  hit.onmousemove = (e) => {
    const r = el.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const i = n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round((mx - L) / ((W - L - R) / (n - 1)))));
    hover.style.display = '';
    $('line', hover).setAttribute('x1', x(i)); $('line', hover).setAttribute('x2', x(i));
    $$('circle', hover).forEach((c, si) => { c.setAttribute('cx', x(i)); c.setAttribute('cy', y(series[si].values[i])); });
    tip.hidden = false;
    tip.style.left = x(i) + 'px';
    tip.style.top = Math.min(...series.map(s => y(s.values[i]))) + 'px';
    tip.innerHTML = `<b>${esc(cfg.tipLabels?.[i] || labels[i])}</b>` + series.map(s =>
      `<div><i style="background:${s.color}"></i>${esc(s.name)}: <strong>${esc(fmt(s.values[i]))}</strong></div>`).join('');
  };
  hit.onmouseleave = () => { hover.style.display = 'none'; tip.hidden = true; };
}

/* ================= вход и каркас ================= */

$('#login-form').onsubmit = async (e) => {
  e.preventDefault();
  const btn = $('#login-btn'), err = $('#login-error');
  btn.disabled = true; btn.textContent = 'Вход…'; err.hidden = true;
  try {
    const r = await api.login($('#login-phone').value.trim(), $('#login-password').value);
    if (r.data.user.role !== 'DIRECTOR') throw new ApiError('PERMISSION_DENIED', 'Эта учётная запись не директор платформы');
    store.save(r.data.tokens, r.data.user);
    enterApp();
  } catch (ex) {
    err.textContent = ex.message; err.hidden = false;
  } finally {
    btn.disabled = false; btn.textContent = 'Войти';
  }
};

function logout() {
  store.clear();
  $('#app').hidden = true;
  $('#login-screen').hidden = false;
}
$('#logout-btn').onclick = logout;
$('#modal-close').onclick = closeModal;
$('#modal').onmousedown = (e) => { if (e.target.id === 'modal') closeModal(); };

function enterApp() {
  $('#login-screen').hidden = true;
  $('#app').hidden = false;
  const u = store.user;
  $('#who-name').textContent = u?.full_name || u?.phone || 'Директор';
  $('#who-avatar').textContent = initials(u?.full_name, 'P');
  if (!location.hash) location.hash = '#/dashboard';
  route();
}

/* ================= роутер ================= */

const routes = {
  dashboard: { nav: 'dashboard', render: renderDashboard },
  organizations: { nav: 'organizations', render: renderOrganizations },
  organization: { nav: 'organizations', render: renderOrganization },
  users: { nav: 'users', render: renderUsers },
  client: { nav: 'users', render: renderClient },
  transactions: { nav: 'transactions', render: renderTransactions },
  statistics: { nav: 'statistics', render: renderStatistics },
  audit: { nav: 'audit', render: renderAudit },
};

let routeToken = 0;
function parseHash() {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean);
  return { key: parts[0] || 'dashboard', params: parts.slice(1), query: Object.fromEntries(new URLSearchParams(query)) };
}

/* soft — перерисовка после фильтра: старый экран остаётся до прихода данных */
async function route(soft = false) {
  const token = ++routeToken;
  const { key, params, query } = parseHash();
  const r = routes[key] || routes.dashboard;
  if (!soft) closePopover();
  if (key === 'client' && query.via === 'org') $$('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === 'organizations'));
  else $$('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === r.nav));

  if (!soft) { loading(); window.scrollTo(0, 0); }
  try {
    const html = await r.render({ params, query, isCurrent: () => token === routeToken });
    if (token !== routeToken || html == null) return;
    view().innerHTML = html.html;
    bindCommon();
    html.bind?.();
    drawCharts();
  } catch (e) {
    if (token !== routeToken) return;
    showError(e);
    view().innerHTML = empty('Не удалось загрузить данные');
  }
}
const rerender = () => route(true);
window.addEventListener('hashchange', () => route());

function bindCommon() {
  $$('[data-range]', view()).forEach(b => b.onclick = () => openRangePicker(b));
}

/* ================= Главная ================= */

async function renderDashboard() {
  const { data } = await api.dashboard(periodParams());
  const s = data.statistics;
  const series = s.series || [];
  const first = series[0];
  const usersBefore = first ? first.users_total - first.users_new : 0;
  const growth = usersBefore > 0 ? ((s.users_total - usersBefore) / usersBefore) * 100 : null;
  const growthText = growth == null ? `+${num(s.users_new_in_period)}`
    : `${growth >= 0 ? '+' : ''}${growth.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`;

  return {
    html: `
      ${pageHead({ title: 'Главная', sub: 'Обзор платформы TOPTO', actions: rangeButton() })}
      <div class="hero">
        ${wave('hw')}
        ${phoneArt()}
        <h2>Добро пожаловать в TOPTO Admin! 👋</h2>
        <p>Общее состояние платформы: пользователи и организации, которые работают с TOPTO.</p>
      </div>
      <div class="stats">
        ${statCard({ icon: 'users', label: 'Активных пользователей', value: num(s.users_active), delta: deltaUp(growthText), tint: 'violet' })}
        ${statCard({ icon: 'store', label: 'Организаций', value: num(s.organizations_total), delta: deltaUp('+' + num(s.organizations_new_in_period)), tint: 'blue' })}
        ${statCard({ icon: 'user-plus', tone: 'green', label: 'Новых пользователей', value: num(s.users_new_in_period), delta: deltaPlain(periodSuffix()), tint: 'green' })}
      </div>
      <div class="split">
        <div class="card">
          <div class="card-head">
            <h3>Динамика подключений</h3>
            <div class="legend"><span><i style="background:#6A1BF2"></i>Пользователи</span><span><i style="background:#B794FF"></i>Организации</span></div>
          </div>
          ${series.length ? chartBox({
            labels: series.map(p => dayLabel(p.date)),
            series: [
              { name: 'Пользователи', color: '#6A1BF2', values: series.map(p => p.users_total), fill: true },
              { name: 'Организации', color: '#B794FF', values: series.map(p => p.organizations_total), width: 2.5 },
            ],
          }) : empty('Нет данных за период')}
        </div>
        <div class="card promo">
          ${wave('pw')}
          <h3>Платформа TOPTO</h3>
          <p>Управление лояльностью<br>и клиентами для бизнеса</p>
          ${phoneArt()}
        </div>
      </div>`,
  };
}

/* ================= Организации ================= */

let orgFilters = { search: '', status: '', category: '', page: 1 };
let orgSelected = null;
let orgSideTab = 'info';
let categoriesCache = null;
async function getCategories() {
  if (!categoriesCache) categoriesCache = (await api.categories()).data;
  return categoriesCache;
}

async function renderOrganizations() {
  const [list, statsRes, cats] = await Promise.all([
    api.orgs({ ...orgFilters, page_size: 10 }), api.statistics(periodParams()), getCategories(),
  ]);
  const { data, meta } = list;
  const s = statsRes.data;
  const selected = data.find(o => o.id === orgSelected) || data[0];
  orgSelected = selected?.id || null;

  return {
    html: `
      ${pageHead({ title: 'Организации', sub: 'Управление всеми организациями платформы TOPTO', actions: rangeButton() })}
      <div class="stats">
        ${statCard({ icon: 'store', label: 'Всего организаций', value: num(s.organizations_total), delta: deltaUp('+' + num(s.organizations_new_in_period)) })}
        ${statCard({ icon: 'check-circle', tone: 'green', label: 'Активные', value: num(s.organizations_active), delta: deltaPlain('принимают клиентов') })}
        ${statCard({ icon: 'pause-circle', tone: 'red', label: 'Временно отключены', value: num(s.organizations_blocked), delta: deltaPlain('заблокированы') })}
        ${statCard({ icon: 'star', tone: 'pink', label: 'Новые', value: num(s.organizations_new_in_period), delta: deltaPlain(periodSuffix()) })}
      </div>
      <div class="toolbar">
        ${searchBox('f-search', 'Поиск по названию, телефону или адресу…', orgFilters.search)}
        ${selectBox('f-cat', [['', 'Все категории'], ...cats.map(c => [c.id, c.name_ru || c.name])], orgFilters.category)}
        ${selectBox('f-status', [['', 'Все статусы'], ['ACTIVE', 'Активные'], ['BLOCKED', 'Заблокированные']], orgFilters.status)}
        <button class="btn outline" id="new-org">${icon('plus')}Добавить организацию</button>
      </div>
      <div class="split">
        <div class="card flush">
          ${data.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Организация</th><th>Категория</th><th>Программа<br>лояльности</th><th>Статус</th><th>Дата<br>подключения</th><th></th></tr></thead>
            <tbody>${data.map(o => `
              <tr class="clickable ${o.id === orgSelected ? 'selected' : ''}" data-org="${o.id}">
                <td><div class="cell-main">${avatar(o)}<div><div class="t">${esc(o.name)}</div><div class="s">${esc(o.address || o.phone || '—')}</div></div></div></td>
                <td>${o.category ? `<span class="badge violet">${esc(o.category.name_ru || o.category.name)}</span>` : '—'}</td>
                <td><span class="badge ${o.loyalty_type === 'VISIT' ? 'violet' : 'neutral'}">${esc(programShort(o))}</span></td>
                <td>${badge(ORG_STATUS, o.status)}</td>
                <td class="nowrap">${dDate(o.created_at)}</td>
                <td class="right"><button class="icon-btn" data-org-menu="${o.id}" aria-label="Действия">${icon('more-v')}</button></td>
              </tr>`).join('')}
            </tbody></table></div>` : empty('Организации не найдены')}
          ${pager(meta)}
        </div>
        ${selected ? orgSidePanel(selected) : ''}
      </div>`,
    bind() {
      bindSearch($('#f-search'), (v) => { orgFilters = { ...orgFilters, search: v, page: 1 }; rerender(); });
      $('#f-cat').onchange = (e) => { orgFilters = { ...orgFilters, category: e.target.value, page: 1 }; rerender(); };
      $('#f-status').onchange = (e) => { orgFilters = { ...orgFilters, status: e.target.value, page: 1 }; rerender(); };
      $('#new-org').onclick = openCreateOrg;
      bindPager(view(), (p) => { orgFilters.page = p; rerender(); });
      $$('[data-org]').forEach(tr => tr.onclick = (e) => {
        if (e.target.closest('[data-org-menu]')) return;
        orgSelected = tr.dataset.org; rerender();
      });
      $$('[data-org-menu]').forEach(b => b.onclick = () => {
        const o = data.find(x => x.id === b.dataset.orgMenu);
        openMenu(b, orgMenuItems(o, rerender));
      });
      $$('[data-side-tab]').forEach(b => b.onclick = () => { orgSideTab = b.dataset.sideTab; rerender(); });
      $('#side-block')?.addEventListener('click', () => (selected.status === 'ACTIVE' ? blockOrg : unblockOrg)(selected, rerender));
    },
  };
}

function orgSidePanel(o) {
  return `<div class="card side">
    <div class="side-head">
      ${avatar(o, 'xl')}
      <div style="flex:1;min-width:0">
        <div class="t">${esc(o.name)} ${badge(ORG_STATUS, o.status)}</div>
        ${o.category ? `<div class="s">Сфера: <b>${esc(o.category.name_ru || o.category.name)}</b></div>` : ''}
        ${o.address ? `<div class="s">${icon('pin')}${esc(o.address)}</div>` : ''}
      </div>
    </div>
    <div class="tabs">
      <button data-side-tab="info" class="${orgSideTab === 'info' ? 'active' : ''}">Информация</button>
      <button data-side-tab="loyalty" class="${orgSideTab === 'loyalty' ? 'active' : ''}">Лояльность</button>
    </div>
    ${orgSideTab === 'info' ? `
      <div class="kv">
        <div class="k">${icon('phone')}Телефон</div><div class="v">${esc(o.phone || '—')}</div>
        <div class="k">${icon('key')}Администратор</div><div class="v">${o.admin ? esc(o.admin.phone) : '<span class="muted">не назначен</span>'}</div>
        <div class="k">${icon('calendar')}Дата подключения</div><div class="v">${dDate(o.created_at)}</div>
        ${o.status === 'BLOCKED' && o.blocked_reason ? `<div class="k">${icon('ban')}Причина блокировки</div><div class="v">${esc(o.blocked_reason)}</div>` : ''}
      </div>` : `
      <div class="kv">
        <div class="k">${icon('gift')}Тип</div><div class="v">${o.loyalty_type === 'VISIT' ? 'Накопительная N+1' : 'Cashback'}</div>
        <div class="k">${icon('info')}Условия</div><div class="v">${esc(programText(o))}</div>
      </div>`}
    <h3 style="margin-top:22px;font-size:17px">Статистика</h3>
    <div class="mini-stats" style="margin-top:14px">
      <div class="mini-stat"><div class="ibox sm">${icon('users')}</div><div><div class="l">Клиенты</div><div class="n">${num(o.customers_count)}</div></div></div>
      <div class="mini-stat"><div class="ibox sm">${icon('gift')}</div><div><div class="l">Программа</div><div class="n">${esc(programShort(o))}</div></div></div>
    </div>
    <a class="btn primary wide" href="#/organization/${o.id}">${icon('eye')}Подробный</a>
    ${o.status === 'ACTIVE'
      ? `<button class="btn ghost wide" id="side-block">${icon('ban')}Заблокировать</button>`
      : `<button class="btn ghost wide" id="side-block">${icon('unlock')}Разблокировать</button>`}
    <div class="note">${icon('info')}<div>Для просмотра клиентов, транзакций и настроек лояльности перейдите в подробный раздел организации.</div></div>
  </div>`;
}

function orgMenuItems(o, after) {
  return [
    { icon: 'eye', label: 'Открыть', onClick: () => { location.hash = `#/organization/${o.id}`; } },
    { icon: 'edit', label: 'Редактировать', onClick: () => openEditOrg(o, after) },
    { icon: 'key', label: o.admin ? 'Сбросить PIN администратора' : 'Назначить администратора', onClick: () => openAdminForm(o.id, !!o.admin, o.admin?.phone, after) },
    o.status === 'ACTIVE'
      ? { icon: 'ban', label: 'Заблокировать', danger: true, onClick: () => blockOrg(o, after) }
      : { icon: 'unlock', label: 'Разблокировать', onClick: () => unblockOrg(o, after) },
  ];
}
function blockOrg(o, after) {
  confirmModal('Заблокировать организацию', `Пока «${o.name}» заблокирована, её администратор не сможет начислять и списывать. Причина попадёт в аудит.`, true,
    async (reason) => { await api.orgBlock(o.id, reason); toast('Организация заблокирована'); after(); });
}
async function unblockOrg(o, after) {
  try { await api.orgUnblock(o.id); toast('Организация разблокирована'); after(); } catch (e) { showError(e); }
}

function programFields(prefix, o = {}) {
  const v = o.visit_program || {}, c = o.cashback_program || {};
  return `
    <div id="${prefix}-visit">
      <div class="row">
        <label>Посещений до подарка<input type="number" name="target_visits" value="${v.target_visits ?? 5}" min="1" max="100"></label>
        <label>Подарков за цикл<input type="number" name="reward_count" value="${v.reward_count ?? 1}" min="1" max="10"></label>
      </div>
      <label>Название подарка<input name="reward_title_ru" value="${esc(v.reward_title_ru || 'Подарок')}" maxlength="150"></label>
    </div>
    <div id="${prefix}-cashback">
      <div class="row">
        <label>Процент начисления<input type="number" name="rate" value="${(c.cashback_rate_bps ?? 500) / 100}" min="0.01" max="100" step="0.01"></label>
        <label>Макс. % оплаты чека<input type="number" name="max_spend" value="${(c.max_spend_percent_bps ?? 3000) / 100}" min="0.01" max="100" step="0.01"></label>
        <label>Срок сгорания, дней<input type="number" name="expiry_days" value="${c.expiry_days ?? 90}" min="1" max="3650"></label>
      </div>
    </div>`;
}
function programBody(type, f) {
  return type === 'VISIT'
    ? { visit_program: { target_visits: +f.target_visits, reward_count: +f.reward_count, reward_title_ru: f.reward_title_ru || 'Подарок' } }
    : { cashback_program: {
        cashback_rate_bps: Math.round(+f.rate * 100),
        max_spend_percent_bps: Math.round(+f.max_spend * 100),
        expiry_days: +f.expiry_days } };
}
const fieldErrors = (ex) => ex.message + (ex.details?.fields
  ? ': ' + Object.entries(ex.details.fields).map(([k, v]) => `${k} — ${[].concat(v).join(', ')}`).join('; ') : '');

async function openCreateOrg() {
  const cats = await getCategories();
  modal('Новая организация', `
    <form id="org-form">
      <label>Название *<input name="name" required maxlength="150"></label>
      <div class="row">
        <label>Категория<select name="category">${cats.map(c => `<option value="${c.id}">${esc(c.name_ru)}</option>`).join('')}</select></label>
        <label>Тип программы *<select name="loyalty_type" id="lt">
          <option value="VISIT">Накопительная N+1</option>
          <option value="CASHBACK">Cashback</option>
        </select></label>
      </div>
      <div class="row">
        <label>Телефон<input name="phone" placeholder="+996312000000"></label>
        <label>Адрес<input name="address" maxlength="255"></label>
      </div>
      <label>Описание<textarea name="description_ru"></textarea></label>
      <div class="section-title">Условия программы</div>
      ${programFields('new')}
      <div class="alert error" id="org-err" hidden></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" id="org-cancel">Отмена</button>
        <button type="submit" class="btn primary">Создать</button>
      </div>
    </form>`);
  const sync = () => {
    $('#new-visit').hidden = $('#lt').value !== 'VISIT';
    $('#new-cashback').hidden = $('#lt').value !== 'CASHBACK';
  };
  sync();
  $('#lt').onchange = sync;
  $('#org-cancel').onclick = closeModal;
  $('#org-form').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const body = {
      name: f.name, category: f.category, loyalty_type: f.loyalty_type,
      phone: f.phone || '', address: f.address || '', description_ru: f.description_ru || '',
      ...programBody(f.loyalty_type, f),
    };
    try {
      const r = await api.orgCreate(body);
      closeModal();
      toast('Организация создана');
      location.hash = `#/organization/${r.data.id}`;
    } catch (ex) {
      const el = $('#org-err'); el.textContent = fieldErrors(ex); el.hidden = false;
    }
  };
}

async function openEditOrg(o, after) {
  const cats = await getCategories();
  modal('Редактировать организацию', `
    <form id="edit-form">
      <label>Название *<input name="name" required maxlength="150" value="${esc(o.name)}"></label>
      <div class="row">
        <label>Категория<select name="category">${cats.map(c => `<option value="${c.id}" ${o.category?.id === c.id ? 'selected' : ''}>${esc(c.name_ru)}</option>`).join('')}</select></label>
        <label>Телефон<input name="phone" value="${esc(o.phone || '')}" placeholder="+996312000000"></label>
      </div>
      <label>Адрес<input name="address" maxlength="255" value="${esc(o.address || '')}"></label>
      <label>Описание<textarea name="description_ru">${esc(o.description_ru || '')}</textarea></label>
      <label>Логотип<input type="file" name="logo" accept="image/png,image/jpeg,image/webp"></label>
      <div class="alert error" id="edit-err" hidden></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" id="edit-cancel">Отмена</button>
        <button type="submit" class="btn primary">Сохранить</button>
      </div>
    </form>`);
  $('#edit-cancel').onclick = closeModal;
  $('#edit-form').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const logo = fd.get('logo');
    try {
      await api.orgUpdate(o.id, {
        name: fd.get('name'), category: fd.get('category'), phone: fd.get('phone') || '',
        address: fd.get('address') || '', description_ru: fd.get('description_ru') || '',
      });
      if (logo && logo.size) {
        const upload = new FormData();
        upload.append('logo', logo);
        await api.orgUpdate(o.id, upload);
      }
      closeModal(); toast('Изменения сохранены'); after();
    } catch (ex) {
      const el = $('#edit-err'); el.textContent = fieldErrors(ex); el.hidden = false;
    }
  };
}

function openAdminForm(orgId, isReset, phone = '', after) {
  modal(isReset ? 'Сбросить доступ администратора' : 'Назначить администратора', `
    <form id="adm-form">
      <label>Телефон администратора *<input name="phone" value="${esc(phone)}" placeholder="+996700000010" required></label>
      <label>Имя<input name="first_name" placeholder="Имя сотрудника"></label>
      <div class="alert info">Администратор входит в мобильном приложении по этому телефону и PIN-коду. PIN покажем один раз — сразу после создания.</div>
      <div class="alert error" id="adm-err" hidden></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" id="adm-cancel">Отмена</button>
        <button type="submit" class="btn primary">${isReset ? 'Сбросить PIN' : 'Назначить'}</button>
      </div>
    </form>`);
  $('#adm-cancel').onclick = closeModal;
  $('#adm-form').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api.orgAdmin(orgId, { phone: f.phone, first_name: f.first_name || '', replace_existing: true });
      modal('Доступ выдан', `
        <div class="alert success">Передайте эти данные администратору. PIN больше нигде не отображается.</div>
        <div class="pin-box">
          <div class="muted">Телефон (логин)</div><h2>${esc(r.data.admin.phone)}</h2>
          <div class="muted" style="margin-top:14px">Временный PIN</div><h2>${esc(r.data.temporary_pin)}</h2>
        </div>
        <div class="modal-actions"><button class="btn primary" id="ok">Готово</button></div>`);
      $('#ok').onclick = () => { closeModal(); after?.(); };
    } catch (ex) {
      const el = $('#adm-err'); el.textContent = fieldErrors(ex); el.hidden = false;
    }
  };
}

function confirmModal(title, text, needReason, onConfirm) {
  modal(title, `
    <p style="margin-top:0">${esc(text)}</p>
    ${needReason ? '<label>Причина *<input id="cm-reason" required maxlength="255"></label>' : ''}
    <div class="alert error" id="cm-err" hidden></div>
    <div class="modal-actions">
      <button class="btn ghost" id="cm-cancel">Отмена</button>
      <button class="btn danger" id="cm-ok">Подтвердить</button>
    </div>`);
  $('#cm-cancel').onclick = closeModal;
  $('#cm-ok').onclick = async () => {
    const reason = needReason ? $('#cm-reason').value.trim() : '';
    if (needReason && !reason) { $('#cm-reason').focus(); return; }
    try { await onConfirm(reason); closeModal(); }
    catch (ex) { const el = $('#cm-err'); el.textContent = ex.message; el.hidden = false; }
  };
}

/* ================= Карточка организации ================= */

const ORG_TABS = [['overview', 'Обзор'], ['clients', 'Клиенты'], ['transactions', 'Транзакции'], ['loyalty', 'Лояльность'], ['settings', 'Настройки']];
let orgClientFilters = { search: '', is_active: '', page: 1 };
let orgClientSelected = null;
let orgTxFilters = { search: '', type: '', page: 1 };
let lastOrgId = null;

async function renderOrganization({ params }) {
  const [id, tab = 'overview'] = params;
  if (id !== lastOrgId) {
    lastOrgId = id;
    orgClientFilters = { search: '', is_active: '', page: 1 };
    orgTxFilters = { search: '', type: '', page: 1 };
    orgClientSelected = null;
  }
  const { data: o } = await api.org(id);
  const reload = () => rerender();
  const tabRender = { overview: orgOverview, clients: orgClients, transactions: orgTransactions, loyalty: orgLoyalty, settings: orgSettings }[tab] || orgOverview;
  const body = await tabRender(o);

  return {
    html: `
      <div class="page-head" style="margin-bottom:0">
        <div class="crumbs">${crumbsHtml([['Организации', '#/organizations'], ['Подробный']])}</div>
        <div class="head-actions">${rangeButton()}</div>
      </div>
      <div class="entity-head">
        ${avatar(o, 'xl')}
        <div class="info">
          <h1>${esc(o.name)} ${badge(ORG_STATUS, o.status, 'lg')}</h1>
          <div class="meta">
            ${o.category ? `<span>${icon('sparkle')}Сфера: <b>${esc(o.category.name_ru || o.category.name)}</b></span>` : ''}
            ${o.address ? `<span>${icon('pin')}${esc(o.address)}</span>` : ''}
          </div>
        </div>
        <div class="head-actions">
          <button class="btn ghost square" id="org-more" aria-label="Действия">${icon('more')}</button>
          <button class="btn outline lg" id="org-edit">${icon('edit')}Редактировать</button>
        </div>
      </div>
      <div class="tabs lg">${ORG_TABS.map(([k, t]) => `<a href="#/organization/${o.id}/${k}" class="${k === tab ? 'active' : ''}">${t}</a>`).join('')}</div>
      ${body.html}`,
    bind() {
      $('#org-edit').onclick = () => openEditOrg(o, reload);
      $('#org-more').onclick = (e) => openMenu(e.currentTarget, orgMenuItems(o, reload).filter(it => it.label !== 'Открыть' && it.label !== 'Редактировать'));
      body.bind?.();
    },
  };
}

async function orgOverview(o) {
  const [{ data: s }, tx] = await Promise.all([
    api.statistics({ organization_id: o.id, ...periodParams() }),
    api.transactions({ organization_id: o.id, page_size: 5 }),
  ]);
  return {
    html: `
      <div class="stats">
        ${statCard({ icon: 'users', label: 'Клиенты', value: num(s.customers_total), delta: deltaUp('+' + num(s.customers_new)) })}
        ${statCard({ icon: 'user-plus', tone: 'green', label: 'Новые клиенты', value: num(s.customers_new), delta: deltaPlain(periodSuffix()) })}
        ${statCard({ icon: 'gift', label: 'Программа лояльности', value: esc(programShort(o)) })}
        ${statCard({ icon: 'calendar', label: 'Дата подключения', value: dDate(o.created_at) })}
      </div>
      <div class="split even">
        <div class="card">
          <h3 style="margin-bottom:12px">Информация об организации</h3>
          <div class="kv lined">
            <div class="k">${icon('store')}Название</div><div class="v">${esc(o.name)}</div>
            <div class="k">${icon('idcard')}Категория</div><div class="v">${esc(o.category?.name_ru || '—')}</div>
            <div class="k">${icon('phone')}Телефон</div><div class="v">${esc(o.phone || '—')}</div>
            <div class="k">${icon('pin')}Адрес</div><div class="v">${esc(o.address || '—')}</div>
            <div class="k">${icon('key')}Администратор</div><div class="v">${o.admin ? esc(o.admin.phone) : '<span class="muted">не назначен</span>'}</div>
            <div class="k">${icon('calendar')}Статус</div><div class="v">${badge(ORG_STATUS, o.status)}</div>
          </div>
        </div>
        <div class="stack">
          <div class="card">
            <h3 style="margin-bottom:16px">Программа лояльности</h3>
            <div class="program">
              <div class="ibox square">${icon('gift')}</div>
              <div style="flex:1"><div class="n">${esc(programShort(o))}</div><div class="d">${esc(programText(o))}</div></div>
              <a class="btn outline" href="#/organization/${o.id}/loyalty">Подробнее ${icon('chev-right')}</a>
            </div>
          </div>
          <div class="card flush">
            <div class="card-head" style="padding:22px 22px 0">
              <h3>Последние транзакции</h3>
              <a class="link-btn" href="#/organization/${o.id}/transactions">Все транзакции ${icon('chev-right')}</a>
            </div>
            ${tx.data.length ? `<div class="table-wrap"><table class="plain">
              <thead><tr><th>Дата и время</th><th>Клиент</th><th>Действие</th><th>Детали</th></tr></thead>
              <tbody>${tx.data.map(t => `<tr>
                <td class="nowrap">${dTime(t.created_at)}</td>
                <td class="strong">${esc(personName(t.user))}</td>
                <td><span class="badge ${t.type.startsWith('GIFT') ? 'ok' : 'violet'}">${esc(TX_META[t.type]?.label || t.type)}</span></td>
                <td>${txDetails(t)}</td>
              </tr>`).join('')}</tbody></table></div>` : empty('Операций пока нет')}
          </div>
        </div>
      </div>`,
  };
}

async function orgClients(o) {
  const [list, { data: s }] = await Promise.all([
    api.users({ organization_id: o.id, ...orgClientFilters, page_size: 10 }),
    api.statistics({ organization_id: o.id, ...periodParams() }),
  ]);
  const { data, meta } = list;
  const sel = data.find(u => u.id === orgClientSelected) || data[0];
  orgClientSelected = sel?.id || null;
  let detail = null;
  if (sel) detail = (await api.user(sel.id)).data;
  const state = detail?.organizations.find(x => x.organization_id === o.id);

  return {
    html: `
      <div class="stats">
        ${statCard({ icon: 'users', label: 'Всего клиентов', value: num(s.customers_total) })}
        ${statCard({ icon: 'user-plus', tone: 'green', label: 'Новые клиенты', value: num(s.customers_new), delta: deltaPlain(periodSuffix()) })}
        ${statCard({ icon: 'user-check', label: 'Активные клиенты', value: num(s.customers_active), delta: deltaPlain(periodSuffix()) })}
      </div>
      <div class="toolbar">
        ${searchBox('c-search', 'Поиск по имени, телефону или коду…', orgClientFilters.search)}
        ${selectBox('c-status', [['', 'Все статусы'], ['true', 'Активные'], ['false', 'Заблокированные']], orgClientFilters.is_active)}
        <span class="spacer"></span>
        ${exportBtn()}
      </div>
      <div class="split">
        <div class="card flush">
          ${data.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Клиент</th><th>Телефон</th><th>Дата регистрации</th><th>Статус</th><th></th></tr></thead>
            <tbody>${data.map(u => `<tr class="clickable ${u.id === orgClientSelected ? 'selected' : ''}" data-client="${u.id}">
              <td><div class="cell-main">${avatar(u, 'soft')}<div class="t">${esc(u.full_name || '—')}</div></div></td>
              <td class="strong nowrap">${esc(u.phone)}</td>
              <td>${dDate(u.date_joined)}</td>
              <td>${userBadge(u)}</td>
              <td class="right"><a class="icon-btn" href="#/client/${u.id}?org=${o.id}&via=org" aria-label="Открыть">${icon('more')}</a></td>
            </tr>`).join('')}</tbody></table></div>` : empty('Клиентов не найдено')}
          ${pager(meta)}
        </div>
        ${detail ? `<div class="card side">
          <div class="side-head">
            ${avatar(detail, 'xl soft')}
            <div style="flex:1;min-width:0"><div class="t">${esc(detail.full_name || detail.phone)} ${userBadge(detail)}</div></div>
          </div>
          <div class="kv">
            <div class="k">${icon('phone')}Телефон</div><div class="v">${esc(detail.phone)}</div>
            <div class="k">${icon('calendar')}Дата регистрации</div><div class="v">${dDate(detail.date_joined)}</div>
            ${state ? (o.loyalty_type === 'VISIT' ? `
              <div class="k">${icon('users')}Посещений</div><div class="v">${num(state.total_visits)}</div>
              <div class="k">${icon('star')}Прогресс</div><div class="v">${num(state.visit_progress)} из ${state.program?.target_visits ?? '—'}</div>
              <div class="k">${icon('gift')}Подарки</div><div class="v">${num(state.available_gifts)} доступно</div>` : `
              <div class="k">${icon('wallet')}Бонусы</div><div class="v">${money(state.cashback_available)}</div>
              <div class="k">${icon('cashback')}Начислено</div><div class="v">${money(state.cashback_total_earned)}</div>`) : ''}
          </div>
          <hr>
          <a class="btn outline wide" href="#/client/${detail.id}?org=${o.id}&via=org">${icon('eye')}Подробный</a>
        </div>` : ''}
      </div>`,
    bind() {
      bindSearch($('#c-search'), (v) => { orgClientFilters = { ...orgClientFilters, search: v, page: 1 }; rerender(); });
      $('#c-status').onchange = (e) => { orgClientFilters = { ...orgClientFilters, is_active: e.target.value, page: 1 }; rerender(); };
      bindPager(view(), (p) => { orgClientFilters.page = p; rerender(); });
      $$('[data-client]').forEach(tr => tr.onclick = (e) => {
        if (e.target.closest('a')) return;
        orgClientSelected = tr.dataset.client; rerender();
      });
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, async () => {
        const rows = await fetchAll(api.users, { organization_id: o.id, ...orgClientFilters });
        downloadCsv(`клиенты_${o.name}`, ['Клиент', 'Телефон', 'Код', 'Дата регистрации', 'Статус'],
          rows.map(u => [u.full_name, u.phone, u.public_code, dDate(u.date_joined), u.is_active ? 'Активен' : 'Заблокирован']));
      });
    },
  };
}

async function orgTransactions(o) {
  const params = { organization_id: o.id, ...orgTxFilters, ...periodParams(), page_size: 10 };
  const { data, meta } = await api.transactions(params);
  return {
    html: `
      <div class="toolbar">
        ${searchBox('ot-search', 'Поиск по клиенту или сотруднику…', orgTxFilters.search)}
        ${selectBox('ot-type', [['', 'Все операции'], ...Object.entries(TX_META).map(([k, v]) => [k, v.label])], orgTxFilters.type)}
        <span class="spacer"></span>
        ${exportBtn()}
      </div>
      <div class="card flush">${txTable(data, { showOrg: false })}${pager(meta)}</div>`,
    bind() {
      bindSearch($('#ot-search'), (v) => { orgTxFilters = { ...orgTxFilters, search: v, page: 1 }; rerender(); });
      $('#ot-type').onchange = (e) => { orgTxFilters = { ...orgTxFilters, type: e.target.value, page: 1 }; rerender(); };
      bindPager(view(), (p) => { orgTxFilters.page = p; rerender(); });
      bindTxTable(data);
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, () => exportTransactions({ ...params, page_size: undefined }, `транзакции_${o.name}`));
    },
  };
}

async function orgLoyalty(o) {
  const { data: s } = await api.statistics({ organization_id: o.id, ...periodParams() });
  const isVisit = o.loyalty_type === 'VISIT';
  return {
    html: `
      <div class="stats">
        ${isVisit ? `
          ${statCard({ icon: 'calendar', label: 'Посещений', value: num(s.visits), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'gift', tone: 'green', label: 'Подарков выдано', value: num(s.gifts_created), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'check-circle', label: 'Подарков использовано', value: num(s.gifts_used), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'star', tone: 'pink', label: 'Повторные клиенты', value: num(s.repeat_customers), delta: deltaPlain('2 и более посещений') })}` : `
          ${statCard({ icon: 'cart', label: 'Сумма покупок', value: money(s.turnover), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'coins', tone: 'green', label: 'Начислено бонусов', value: money(s.cashback_earned), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'gift', label: 'Использовано бонусов', value: money(s.cashback_spent), delta: deltaPlain(periodSuffix()) })}
          ${statCard({ icon: 'wallet', tone: 'pink', label: 'Бонусный остаток', value: money(s.cashback_active_balance), delta: deltaPlain('не сгоревший') })}`}
      </div>
      <div class="split half">
        <div class="card">
          <h3 style="margin-bottom:16px">Программа лояльности</h3>
          <div class="program">
            <div class="ibox square">${icon('gift')}</div>
            <div><div class="n">${esc(programShort(o))}</div><div class="d">${esc(programText(o))}</div></div>
          </div>
          <div class="note">${icon('info')}<div>Изменения действуют только на будущие операции: применённые параметры сохраняются в каждой транзакции.</div></div>
        </div>
        <div class="card">
          <h3 style="margin-bottom:16px">Настройки программы</h3>
          <form id="prog-form">
            ${programFields('edit', o)}
            <button class="btn primary">Сохранить настройки</button>
          </form>
        </div>
      </div>`,
    bind() {
      $(isVisit ? '#edit-cashback' : '#edit-visit').remove();
      $('#prog-form').onsubmit = async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        try { await api.orgUpdate(o.id, programBody(o.loyalty_type, f)); toast('Настройки сохранены'); rerender(); }
        catch (ex) { toast(fieldErrors(ex), true); }
      };
    },
  };
}

async function orgSettings(o) {
  return {
    html: `
      <div class="split half">
        <div class="card">
          <h3 style="margin-bottom:16px">Администратор организации</h3>
          ${o.admin ? `
            <div class="kv lined">
              <div class="k">${icon('phone')}Телефон (логин)</div><div class="v">${esc(o.admin.phone)}</div>
              <div class="k">${icon('user')}Имя</div><div class="v">${esc(o.admin.full_name || '—')}</div>
              <div class="k">${icon('lock')}PIN задан</div><div class="v">${o.admin.has_pin ? 'да' : 'нет'}</div>
              <div class="k">${icon('calendar')}Назначен</div><div class="v">${dDate(o.admin.created_at)}</div>
            </div>
            <div class="row" style="margin-top:18px">
              <button class="btn outline" id="admin-reset">${icon('key')}Сбросить PIN</button>
              <button class="btn danger-outline" id="admin-revoke">${icon('user-x')}Отозвать доступ</button>
            </div>` : `
            <p class="muted" style="margin-top:0">Не назначен — организация не сможет обслуживать клиентов.</p>
            <button class="btn primary" id="admin-add">${icon('user-plus')}Назначить администратора</button>`}
        </div>
        <div class="card">
          <h3 style="margin-bottom:16px">Доступ организации</h3>
          ${o.status === 'ACTIVE' ? `
            <p class="muted" style="margin-top:0">Пока организация заблокирована, её администратор не сможет начислять и списывать бонусы.</p>
            <button class="btn danger-outline" id="block-btn">${icon('ban')}Заблокировать организацию</button>` : `
            <div class="alert error">Заблокирована ${o.blocked_at ? dTime(o.blocked_at) : ''}${o.blocked_reason ? ` — ${esc(o.blocked_reason)}` : ''}</div>
            <button class="btn primary" id="unblock-btn">${icon('unlock')}Разблокировать</button>`}
        </div>
      </div>`,
    bind() {
      const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
      on('#block-btn', () => blockOrg(o, rerender));
      on('#unblock-btn', () => unblockOrg(o, rerender));
      on('#admin-add', () => openAdminForm(o.id, false, '', rerender));
      on('#admin-reset', () => openAdminForm(o.id, true, o.admin.phone, rerender));
      on('#admin-revoke', () => confirmModal('Отозвать доступ администратора', 'Администратор сразу перестанет обслуживать клиентов.', false,
        async () => { await api.orgAdminRevoke(o.id); toast('Доступ отозван'); rerender(); }));
    },
  };
}

/* ================= Транзакции (общая таблица) ================= */

function txDetails(t) {
  const m = t.metadata || {};
  switch (t.type) {
    case 'VISIT_EARN':
      return m.progress_before != null ? `Визит №${m.progress_before + 1}${m.target_visits ? ` из ${m.target_visits}` : ''}` : 'Посещение';
    case 'CASHBACK_EARN':
      return `<span class="plus">+${money(t.amount)}</span>${m.purchase_total ? `<div class="muted" style="font-size:12.5px">чек ${money(m.purchase_total)}</div>` : ''}`;
    case 'CASHBACK_SPEND': case 'CASHBACK_EXPIRE':
      return `<span class="minus">−${money(t.amount)}</span>`;
    case 'GIFT_CREATED': case 'GIFT_REDEEM':
      return esc(m.gift_title_ru || 'Подарок');
    case 'REVERSAL':
      return esc(t.reason || 'Отмена');
    default:
      return t.amount != null ? money(t.amount) : '—';
  }
}
function txActor(t) {
  if (t.actor) return `<div class="cell-who"><div class="strong">${esc(personName(t.actor))}</div><div class="s">${esc(ACTOR_LABEL[t.actor_type] || '')}</div></div>`;
  return `<span class="muted">${esc(ACTOR_LABEL[t.actor_type] || '—')}</span>`;
}
function txTable(rows, { showOrg = true } = {}) {
  if (!rows.length) return empty('Операций не найдено');
  return `<div class="table-wrap"><table>
    <thead><tr><th>Клиент</th><th>Операция</th>${showOrg ? '<th>Организация</th>' : ''}<th>Кто выдал</th><th>Дата и время</th><th>Детали</th><th>Статус</th><th></th></tr></thead>
    <tbody>${rows.map(t => {
      const meta = TX_META[t.type] || { icon: 'receipt', label: t.type };
      return `<tr>
        <td class="strong">${t.user ? `<a href="#/client/${t.user.id}?org=${t.organization?.id || ''}" style="color:inherit">${esc(personName(t.user))}</a>` : '—'}</td>
        <td><div class="cell-op">${icon(meta.icon)}<div>${esc(meta.label)}</div></div></td>
        ${showOrg ? `<td>${t.organization ? `<a href="#/organization/${t.organization.id}" style="color:inherit">${esc(t.organization.name)}</a>` : '—'}</td>` : ''}
        <td>${txActor(t)}</td>
        <td class="nowrap">${dTime(t.created_at)}</td>
        <td>${txDetails(t)}</td>
        <td>${t.type === 'GIFT_REDEEM' && t.status === 'COMPLETED' ? '<span class="badge violet">Использован</span>' : badge(TX_STATUS, t.status)}</td>
        <td class="right"><button class="icon-btn" data-tx="${t.id}" aria-label="Действия">${icon('more')}</button></td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
}
function bindTxTable(rows) {
  $$('[data-tx]').forEach(b => b.onclick = () => {
    const t = rows.find(x => x.id === b.dataset.tx);
    const items = [];
    if (t.user) items.push({ icon: 'user', label: 'Открыть клиента', onClick: () => { location.hash = `#/client/${t.user.id}?org=${t.organization?.id || ''}`; } });
    if (t.organization) items.push({ icon: 'store', label: 'Открыть организацию', onClick: () => { location.hash = `#/organization/${t.organization.id}`; } });
    if (t.status === 'COMPLETED' && REVERSIBLE.includes(t.type)) items.push({ icon: 'undo', label: 'Отменить операцию', danger: true, onClick: () => openReverse(t.id) });
    openMenu(b, items);
  });
}
async function exportTransactions(params, name) {
  const rows = await fetchAll(api.transactions, params);
  downloadCsv(name, ['Дата и время', 'Клиент', 'Телефон клиента', 'Организация', 'Операция', 'Сумма, сом', 'Кто выдал', 'Статус', 'Причина'],
    rows.map(t => [dTime(t.created_at), t.user?.full_name || '', t.user?.phone || '', t.organization?.name || '',
      TX_META[t.type]?.label || t.type, t.amount != null ? String(somValue(t.amount)).replace('.', ',') : '',
      t.actor ? personName(t.actor) : ACTOR_LABEL[t.actor_type] || '', TX_STATUS[t.status]?.[1] || t.status, t.reason || '']));
}

function openReverse(id) {
  modal('Отмена операции', `
    <div class="alert info">Операция не удаляется: в журнале останется исходная запись, а рядом появится компенсирующая. Причина обязательна и попадёт в аудит.</div>
    <label>Причина *<input id="rev-reason" maxlength="255" required></label>
    <label style="display:flex;gap:9px;align-items:center;color:var(--text)">
      <input type="checkbox" id="rev-force" style="width:auto;margin:0"> Принудительно (для частично потраченных начислений)
    </label>
    <div class="alert error" id="rev-err" hidden></div>
    <div class="modal-actions">
      <button class="btn ghost" id="rev-cancel">Отмена</button>
      <button class="btn danger" id="rev-ok">Отменить операцию</button>
    </div>`);
  $('#rev-cancel').onclick = closeModal;
  $('#rev-ok').onclick = async () => {
    const reason = $('#rev-reason').value.trim();
    if (!reason) { $('#rev-reason').focus(); return; }
    try {
      await api.reverse(id, reason, $('#rev-force').checked);
      closeModal(); toast('Операция отменена'); rerender();
    } catch (ex) {
      const el = $('#rev-err'); el.textContent = ex.message; el.hidden = false;
    }
  };
}

let txFilters = { search: '', organization_id: '', type: '', page: 1 };
let orgOptionsCache = null;
async function orgOptions() {
  if (!orgOptionsCache) orgOptionsCache = await fetchAll(api.orgs, {}, 500);
  return orgOptionsCache;
}

async function renderTransactions() {
  const base = { search: txFilters.search, organization_id: txFilters.organization_id, type: txFilters.type, ...periodParams() };
  const [list, done, reversed, orgs] = await Promise.all([
    api.transactions({ ...base, page: txFilters.page, page_size: 10 }),
    api.transactions({ ...base, status: 'COMPLETED', page_size: 1 }),
    api.transactions({ ...base, status: 'REVERSED', page_size: 1 }),
    orgOptions(),
  ]);
  const total = list.meta?.pagination?.total_items ?? 0;
  const count = (r) => r.meta?.pagination?.total_items ?? 0;
  return {
    html: `
      ${pageHead({ title: 'Транзакции', sub: 'Все операции в системе TOPTO.', actions: rangeButton() })}
      <div class="stats">
        ${statCard({ icon: 'layers', label: 'Всего операций', value: num(total) })}
        ${statCard({ icon: 'check-circle', tone: 'green', label: 'Успешные', value: num(count(done)) })}
        ${statCard({ icon: 'x-circle', tone: 'red', label: 'Отменённые', value: num(count(reversed)) })}
      </div>
      <div class="toolbar">
        ${searchBox('t-search', 'Поиск по клиенту, организации или сотруднику…', txFilters.search)}
        ${selectBox('t-org', [['', 'Все организации'], ...orgs.map(o => [o.id, o.name])], txFilters.organization_id)}
        ${selectBox('t-type', [['', 'Все операции'], ...Object.entries(TX_META).map(([k, v]) => [k, v.label])], txFilters.type)}
        ${exportBtn()}
      </div>
      <div class="card flush">${txTable(list.data)}</div>
      ${pager(list.meta)}`,
    bind() {
      bindSearch($('#t-search'), (v) => { txFilters = { ...txFilters, search: v, page: 1 }; rerender(); });
      $('#t-org').onchange = (e) => { txFilters = { ...txFilters, organization_id: e.target.value, page: 1 }; rerender(); };
      $('#t-type').onchange = (e) => { txFilters = { ...txFilters, type: e.target.value, page: 1 }; rerender(); };
      bindPager(view(), (p) => { txFilters.page = p; rerender(); });
      bindTxTable(list.data);
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, () => exportTransactions(base, 'транзакции'));
    },
  };
}

/* ================= Клиенты ================= */

let userFilters = { search: '', is_active: '', page: 1 };

async function renderUsers() {
  const [{ data, meta }, { data: s }] = await Promise.all([
    api.users({ ...userFilters, page_size: 10 }), api.statistics(periodParams()),
  ]);
  return {
    html: `
      ${pageHead({ title: 'Клиенты', sub: 'Все клиенты, зарегистрированные в TOPTO.' })}
      <div class="stats">
        ${statCard({ icon: 'users', label: 'Всего клиентов', value: num(s.users_total) })}
        ${statCard({ icon: 'check-circle', tone: 'green', label: 'Активные', value: num(s.users_active) })}
        ${statCard({ icon: 'user', tone: 'grey', label: 'Неактивные', value: num(s.users_total - s.users_active) })}
      </div>
      <div class="toolbar">
        ${searchBox('u-search', 'Поиск по ФИО, телефону или коду…', userFilters.search)}
        ${selectBox('u-status', [['', 'Все клиенты'], ['true', 'Активные'], ['false', 'Неактивные']], userFilters.is_active)}
        <span class="spacer"></span>
        ${exportBtn()}
      </div>
      <div class="card flush">
        ${data.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Клиент</th><th>Телефон</th><th>Код</th><th>Регистрация</th><th>Статус</th><th></th></tr></thead>
          <tbody>${data.map(u => `<tr class="clickable" data-user="${u.id}">
            <td><div class="cell-main">${avatar(u, 'soft')}<div class="t">${esc(u.full_name || '—')}</div></div></td>
            <td class="nowrap">${esc(u.phone)}</td>
            <td>${u.public_code ? `<span class="badge code">${esc(u.public_code)}</span>` : '—'}</td>
            <td class="nowrap">${dTime(u.date_joined)}</td>
            <td>${u.is_active ? '<span class="badge ok">Активен</span>' : '<span class="badge neutral">Неактивен</span>'}</td>
            <td class="right"><a class="btn ghost sm" href="#/client/${u.id}">Открыть</a></td>
          </tr>`).join('')}</tbody></table></div>` : empty('Клиенты не найдены')}
      </div>
      ${pager(meta)}`,
    bind() {
      bindSearch($('#u-search'), (v) => { userFilters = { ...userFilters, search: v, page: 1 }; rerender(); });
      $('#u-status').onchange = (e) => { userFilters = { ...userFilters, is_active: e.target.value, page: 1 }; rerender(); };
      bindPager(view(), (p) => { userFilters.page = p; rerender(); });
      $$('[data-user]').forEach(tr => tr.onclick = (e) => {
        if (e.target.closest('a')) return;
        location.hash = `#/client/${tr.dataset.user}`;
      });
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, async () => {
        const rows = await fetchAll(api.users, { search: userFilters.search, is_active: userFilters.is_active });
        downloadCsv('клиенты', ['Клиент', 'Телефон', 'Код', 'Регистрация', 'Статус'],
          rows.map(u => [u.full_name, u.phone, u.public_code, dTime(u.date_joined), u.is_active ? 'Активен' : 'Неактивен']));
      });
    },
  };
}

/* ================= Карточка клиента ================= */

async function renderClient({ params, query }) {
  const [id] = params;
  const { data: u } = await api.user(id);
  const states = u.organizations || [];
  const st = states.find(x => x.organization_id === query.org) || states[0];
  const fromOrg = query.via === 'org' && st?.organization_id === query.org;
  const tx = st ? await api.transactions({ user_id: id, organization_id: st.organization_id, page_size: 10 }) : { data: [] };
  const isVisit = st?.loyalty_type === 'VISIT';

  const crumbs = fromOrg
    ? crumbsHtml([['Организации', '#/organizations'], [st.organization_name, `#/organization/${st.organization_id}`], ['Клиенты', `#/organization/${st.organization_id}/clients`], ['Подробный']])
    : crumbsHtml([['Клиенты', '#/users'], ['Подробный']]);

  return {
    html: `
      <div class="page-head" style="margin-bottom:0">
        <div class="crumbs">${crumbs}</div>
      </div>
      <div class="entity-head">
        <button class="back-btn" id="back" aria-label="Назад">${icon('chev-left')}</button>
        ${avatar(u, 'xxl')}
        <div class="info"><h1>${esc(u.full_name || u.phone)} ${userBadge(u, 'lg')}</h1></div>
        <div class="head-actions">
          <a class="btn outline lg" href="#/transactions" id="all-ops">${icon('receipt')}Все операции</a>
        </div>
      </div>
      <div class="tabs lg"><a class="active">Обзор</a></div>
      ${states.length > 1 ? `<div class="org-switch">${states.map(x =>
        `<button data-st="${x.organization_id}" class="${x === st ? 'active' : ''}">${esc(x.organization_name)}</button>`).join('')}</div>` : ''}
      <div class="split even" style="margin-bottom:20px">
        <div class="card">
          <h3 style="margin-bottom:14px">Основная информация</h3>
          <div class="kv big">
            <div class="k">${icon('users')}</div><div class="v">${esc(u.full_name || '—')}</div>
            <div class="k">${icon('phone')}</div><div class="v">${esc(u.phone)}</div>
            <div class="k">${icon('lock')}</div><div class="v">${esc(u.public_code || '—')} <span class="muted" style="font-weight:500;font-size:13px">код клиента</span></div>
            <div class="k">${icon('calendar')}</div><div class="v">${dDate(u.date_joined)}</div>
            ${st ? `<div class="k">${icon('store')}</div><div class="v"><a href="#/organization/${st.organization_id}" style="color:inherit">${esc(st.organization_name)}</a></div>` : ''}
            <div class="k">${icon('shield')}</div><div class="v">${userBadge(u, 'lg')}</div>
          </div>
        </div>
        <div class="card">${st ? (isVisit ? visitLoyalty(st) : cashbackLoyalty(st)) : `<h3>Лояльность</h3>${empty('Клиент ещё не участвует ни в одной программе')}`}</div>
      </div>
      ${st ? `<div class="split even">
        <div class="card">
          <h3 style="margin-bottom:10px">${isVisit ? 'Статистика по клиенту' : 'Статистика по бонусам'}</h3>
          <div class="list-stat">${isVisit ? `
            <div><div class="ibox sm">${icon('store')}</div><div class="l">Всего посещений</div><div class="n">${num(st.total_visits)}</div></div>
            <div><div class="ibox sm">${icon('gift')}</div><div class="l">Полученных бонусов</div><div class="n">${num(st.total_gifts_earned)}</div></div>
            <div><div class="ibox sm">${icon('percent')}</div><div class="l">Использованных бонусов</div><div class="n">${num(st.total_gifts_used)}</div></div>` : `
            <div><div class="ibox sm">${icon('coins')}</div><div class="l">Начислено бонусов</div><div class="n">${money(st.cashback_total_earned)}</div></div>
            <div><div class="ibox sm">${icon('gift')}</div><div class="l">Использовано бонусов</div><div class="n">${money(st.cashback_total_spent)}</div></div>
            <div><div class="ibox sm">${icon('flame')}</div><div class="l">Сгорело</div><div class="n">${money(st.cashback_total_expired)}</div></div>
            <div><div class="ibox sm">${icon('wallet')}</div><div class="l">Текущий баланс</div><div class="n">${money(st.cashback_available)}</div></div>`}
            <div><div class="ibox sm">${icon('calendar')}</div><div class="l">${isVisit ? 'Последнее посещение' : 'Последняя операция'}</div><div class="n" style="font-size:17px">${dTime(st.last_activity_at)}</div></div>
          </div>
        </div>
        <div class="card flush">
          <div class="card-head" style="padding:22px 22px 0"><h3>${isVisit ? 'История посещений' : 'История операций с бонусами'}</h3></div>
          ${clientHistory(tx, st, isVisit)}
        </div>
      </div>` : ''}`,
    bind() {
      $('#back').onclick = () => (history.length > 1 ? history.back() : (location.hash = '#/users'));
      $('#all-ops').onclick = (e) => {
        e.preventDefault();
        txFilters = { search: u.phone, organization_id: '', type: '', page: 1 };
        location.hash = '#/transactions';
      };
      $$('[data-st]').forEach(b => b.onclick = () => { location.hash = `#/client/${id}?org=${b.dataset.st}${query.via ? '&via=' + query.via : ''}`; });
    },
  };
}

function visitLoyalty(st) {
  const target = st.program?.target_visits || 0;
  const reward = st.program?.reward_count || 1;
  const done = Math.min(st.visit_progress, target);
  const steps = Array.from({ length: target }, (_, i) =>
    (i ? `<div class="bar ${i < done ? 'on' : ''}"></div>` : '') +
    `<div class="dot ${i < done ? 'on' : ''}">${i < done ? icon('check') : i + 1}</div>`).join('');
  const left = Math.max(0, target - st.visit_progress);
  return `
    <h3 style="margin-bottom:16px">Лояльность</h3>
    <div class="program">
      <div class="ibox square">${icon('gift')}</div>
      <div><div class="n">${target}+${reward}</div><div class="d">После ${target} посещений — ${reward > 1 ? reward + ' подарка' : 'подарок'}</div></div>
    </div>
    <div class="progress-box">
      <div class="ph"><span>Прогресс в программе</span><span>${done} из ${target}</span></div>
      <div class="progress-row">
        <div class="steps">${steps}</div>
        <div class="next-gift">${icon('gift')}<div>
          ${st.available_gifts > 0
            ? `<div class="l">Доступно подарков</div><div class="v">${num(st.available_gifts)}</div>`
            : `<div class="l">До подарка</div><div class="v">${left} ${left === 1 ? 'посещение' : left < 5 ? 'посещения' : 'посещений'}</div>`}
        </div></div>
      </div>
    </div>`;
}
function cashbackLoyalty(st) {
  return `
    <h3 style="margin-bottom:16px">Бонусный баланс</h3>
    <div class="program">
      <div class="ibox square">${icon('coins')}</div>
      <div><div class="n">${money(st.cashback_available)}</div><div class="d">Доступно для оплаты</div></div>
    </div>
    <div class="bal-grid">
      <div class="mini-stat"><div class="ibox sm">${icon('gift')}</div><div><div class="l">Всего начислено</div><div class="n">${money(st.cashback_total_earned)}</div></div></div>
      <div class="mini-stat"><div class="ibox sm">${icon('tag')}</div><div><div class="l">Всего использовано</div><div class="n">${money(st.cashback_total_spent)}</div></div></div>
      <div class="mini-stat"><div class="ibox sm">${icon('percent')}</div><div><div class="l">Процент начисления</div><div class="n">${pct(st.program?.cashback_rate_bps)}</div></div></div>
    </div>`;
}
function clientHistory(tx, st, isVisit) {
  const rows = tx.data || [];
  if (!rows.length) return empty('Операций пока нет');
  const total = tx.meta?.pagination?.total_items ?? rows.length;
  if (isVisit) {
    return `<div class="table-wrap"><table class="plain">
      <thead><tr><th>№</th><th>Дата и время</th><th>Действие</th><th>Детали</th></tr></thead>
      <tbody>${rows.map((t, i) => `<tr>
        <td class="strong" style="color:var(--violet-600)">${total - i}</td>
        <td class="nowrap">${dTime(t.created_at)}</td>
        <td><span class="badge ${t.type.startsWith('GIFT') ? 'ok' : t.type === 'REVERSAL' ? 'bad' : 'violet'}">${esc(TX_META[t.type]?.label || t.type)}</span>${t.status === 'REVERSED' ? ' <span class="badge bad">отменена</span>' : ''}</td>
        <td>${txDetails(t)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  }
  // баланс после операции восстанавливаем от текущего остатка назад; на отмене — дальше неизвестен
  let balance = st.cashback_available, known = true;
  const delta = { CASHBACK_EARN: 1, CASHBACK_SPEND: -1, CASHBACK_EXPIRE: -1 };
  return `<div class="table-wrap"><table class="plain">
    <thead><tr><th>№</th><th>Дата и время</th><th>Действие</th><th>Сумма</th><th>Баланс</th></tr></thead>
    <tbody>${rows.map((t, i) => {
      const after = known ? balance : null;
      if (t.type in delta && t.status === 'COMPLETED') balance -= delta[t.type] * (t.amount || 0);
      else if (t.type === 'REVERSAL' || t.status === 'REVERSED') known = false;
      const sign = delta[t.type];
      return `<tr>
        <td class="strong" style="color:var(--violet-600)">${total - i}</td>
        <td class="nowrap">${dTime(t.created_at)}</td>
        <td><span class="badge ${sign > 0 ? 'violet' : 'bad'}">${sign > 0 ? 'Начисление' : sign < 0 ? (t.type === 'CASHBACK_EXPIRE' ? 'Сгорание' : 'Списание') : esc(TX_META[t.type]?.label || t.type)}</span></td>
        <td class="${sign > 0 ? 'plus' : 'minus'} nowrap">${t.amount != null ? (sign > 0 ? '+' : '−') + money(t.amount) : '—'}</td>
        <td class="strong nowrap">${after != null ? money(after) : '—'}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
}

/* ================= Статистика ================= */

let statOrg = '';
let statMetric = '';

async function renderStatistics() {
  const [orgs, { data: s }] = await Promise.all([
    orgOptions(), api.statistics({ ...(statOrg ? { organization_id: statOrg } : {}), ...periodParams() }),
  ]);
  const series = s.series || [];
  const isVisit = s.loyalty_type === 'VISIT';
  const metrics = statOrg
    ? (isVisit ? [['visits', 'Посещения']] : [['cashback_earned', 'Начислено'], ['cashback_spent', 'Использовано']])
    : [['cashback_earned', 'Бонусы'], ['visits', 'Посещения'], ['operations', 'Операции']];
  if (!metrics.some(([k]) => k === statMetric)) statMetric = metrics[0][0];
  const isMoney = statMetric.startsWith('cashback');
  const metricName = { cashback_earned: 'Начислено бонусов', cashback_spent: 'Использовано бонусов', visits: 'Посещения', operations: 'Операции' }[statMetric];
  const values = series.map(p => isMoney ? somValue(p[statMetric]) : p[statMetric]);
  const preset = period.preset;

  const orgSelect = `<select id="s-org">${[['', 'Все организации'], ...orgs.map(o => [o.id, o.name])]
    .map(([v, t]) => `<option value="${v}" ${v === statOrg ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;

  const cards = isVisit ? [
    statCard({ icon: 'calendar', label: 'Посещений', value: num(s.visits) }),
    statCard({ icon: 'gift', label: 'Подарков выдано', value: num(s.gifts_created) }),
    statCard({ icon: 'check-circle', label: 'Подарков использовано', value: num(s.gifts_used) }),
    statCard({ icon: 'star', label: 'Доступно подарков', value: num(s.gifts_available_now) }),
  ] : [
    statCard({ icon: 'cart', label: 'Сумма покупок', value: moneyBig(s.turnover) }),
    statCard({ icon: 'coins', label: 'Начислено бонусов', value: moneyBig(s.cashback_earned) }),
    statCard({ icon: 'gift', label: 'Использовано бонусов', value: moneyBig(s.cashback_spent) }),
    statCard({ icon: 'wallet', label: 'Бонусный остаток', value: moneyBig(s.cashback_active_balance) }),
  ];
  const summary = isVisit
    ? `<b>Итого за период:</b> ${num(s.visits)} посещений, выдано ${num(s.gifts_created)} подарков, использовано ${num(s.gifts_used)}.`
    : `<b>Итого за период:</b> на сумму ${money(s.turnover)} начислено ${money(s.cashback_earned)} бонусов, использовано ${money(s.cashback_spent)}.`;

  return {
    html: `
      ${pageHead({ title: 'Статистика', sub: 'Статистика по бонусам.', subViolet: true, actions: `
        <div class="segmented">${[['today', 'Сегодня'], ['7', '7 дней'], ['30', '30 дней']].map(([k, t]) =>
          `<button data-preset="${k}" class="${preset === k ? 'active' : ''}">${t}</button>`).join('')}</div>
        <button class="range-btn" data-range style="min-width:0">${icon('calendar')}<span>${preset === 'custom' || !['today', '7', '30'].includes(preset) ? esc(periodLabel()) : 'Период'}</span>${icon('chev-down', 'chev')}</button>
        <button class="btn outline lg" id="export-btn">${icon('download')}Экспорт в Excel</button>` })}
      <div class="stats five">
        ${statCard({ icon: 'store', label: 'Организация', extra: orgSelect })}
        ${cards.join('')}
      </div>
      <div class="card">
        <div class="card-head">
          <h3>Динамика: ${esc(metricName.toLowerCase())}</h3>
          ${metrics.length > 1 ? `<div class="segmented">${metrics.map(([k, t]) => `<button data-metric="${k}" class="${k === statMetric ? 'active' : ''}">${t}</button>`).join('')}</div>` : ''}
        </div>
        ${series.length ? chartBox({
          labels: series.map(p => dayLabel(p.date)),
          series: [{ name: metricName, color: '#6A1BF2', values, fill: true }],
          endLabels: false, dots: true, height: 320,
          fmt: (v) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 }),
        }) : empty('Нет данных за период')}
      </div>
      <div class="summary">${icon('info')}<div>${summary}</div></div>`,
    bind() {
      $$('[data-preset]', view()).forEach(b => b.onclick = () => { setPeriod(b.dataset.preset); rerender(); });
      $$('[data-metric]').forEach(b => b.onclick = () => { statMetric = b.dataset.metric; rerender(); });
      $('#s-org').onchange = (e) => { statOrg = e.target.value; rerender(); };
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, async () => {
        const orgName = orgs.find(o => o.id === statOrg)?.name || 'платформа';
        downloadCsv(`статистика_${orgName}`, ['Дата', 'Посещения', 'Начислено бонусов, сом', 'Использовано бонусов, сом', 'Операций'],
          series.map(p => [dDate(p.date + 'T00:00:00'), p.visits, String(somValue(p.cashback_earned)).replace('.', ','),
            String(somValue(p.cashback_spent)).replace('.', ','), p.operations]));
      });
    },
  };
}
const moneyBig = (t) => `${somValue(t).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} <small>сом</small>`;

/* ================= Аудит ================= */

let auditFilters = { action: '', search: '', page: 1 };

function auditDetails(a) {
  const b = a.before || {}, f = a.after || {};
  if (a.action === 'LOGIN' && !a.reason) return 'Успешный вход';
  if (a.action === 'ORGANIZATION_CREATED') return `Создана «${esc(f.name || a.organization_name || '')}»`;
  if (a.action === 'ORGANIZATION_UPDATED' && b.name && f.name && b.name !== f.name)
    return `Изменено название:<br>«${esc(b.name)}» → «${esc(f.name)}»`;
  if (a.action === 'TRANSACTION_REVERSED' && a.reason) return `${esc(a.reason)}${a.entity_id ? `<div class="muted" style="font-size:12.5px">ID операции: ${esc(String(a.entity_id).slice(0, 8))}</div>` : ''}`;
  if (a.reason) return esc(a.reason);
  if (a.action.startsWith('ADMIN_') && f.phone) return `Администратор ${esc(f.phone)}`;
  if (a.action === 'ADMIN_RESET' && f.is_active === false) return 'Доступ администратора отозван';
  if (a.action === 'CASHBACK_EARNED' && f.earn != null) return `+${money(f.earn)}${f.purchase_total ? `, чек ${money(f.purchase_total)}` : ''}`;
  const flat = (o) => Object.fromEntries(Object.entries(o).flatMap(([k, v]) => v && typeof v === 'object' && !Array.isArray(v) ? Object.entries(v) : [[k, v]]));
  const fb = flat(b), fa = flat(f);
  const show = (k, v) => k.endsWith('_bps') ? pct(v) : typeof v === 'boolean' ? (v ? 'да' : 'нет') : v ?? '—';
  const diff = Object.keys({ ...fb, ...fa })
    .filter(k => AUDIT_FIELD[k] && JSON.stringify(fb[k]) !== JSON.stringify(fa[k]) && k in fb)
    .map(k => `${AUDIT_FIELD[k]}: ${esc(show(k, fb[k]))} → ${esc(show(k, fa[k]))}`);
  return diff.length ? diff.join('<br>') : '<span class="muted">—</span>';
}

async function renderAudit() {
  const params = { ...auditFilters, ...periodParams(), page_size: 10 };
  const { data, meta } = await api.audit(params);
  return {
    html: `
      ${pageHead({ title: 'Аудит', sub: 'История действий в системе.', subViolet: true, actions: `${rangeButton()}<button class="btn outline lg" id="export-btn">${icon('download')}Экспорт в Excel</button>` })}
      <div class="toolbar">
        ${selectBox('a-action', [['', 'Все действия'], ...Object.entries(AUDIT_META).map(([k, [, t]]) => [k, t])], auditFilters.action)}
        ${searchBox('a-search', 'Поиск по телефону, организации или причине…', auditFilters.search)}
      </div>
      <div class="card flush">
        ${data.length ? `<div class="table-wrap"><table class="plain">
          <thead><tr><th>Дата</th><th>Действие</th><th>Кто</th><th>Организация</th><th>Причина</th></tr></thead>
          <tbody>${data.map(a => {
            const [ic, label] = AUDIT_META[a.action] || ['info', a.action];
            return `<tr>
              <td><div class="cell-date">${icon('calendar')}${dShortTime(a.created_at)}</div></td>
              <td><div class="cell-op"><div class="ibox">${icon(ic)}</div><span class="strong" style="font-weight:600">${esc(label)}</span></div></td>
              <td><div class="cell-who"><div>${esc(a.actor_phone || ACTOR_LABEL[a.actor_type] || '—')}</div><div class="s">${esc(ACTOR_LABEL[a.actor_type] || a.actor_type)}</div></div></td>
              <td>${a.organization ? `<a href="#/organization/${a.organization}" style="color:inherit">${esc(a.organization_name)}</a>` : '—'}</td>
              <td>${auditDetails(a)}</td>
            </tr>`;
          }).join('')}</tbody></table></div>` : empty('Записей нет')}
      </div>
      ${pager(meta)}`,
    bind() {
      $('#a-action').onchange = (e) => { auditFilters = { ...auditFilters, action: e.target.value, page: 1 }; rerender(); };
      bindSearch($('#a-search'), (v) => { auditFilters = { ...auditFilters, search: v, page: 1 }; rerender(); });
      bindPager(view(), (p) => { auditFilters.page = p; rerender(); });
      $('#export-btn').onclick = (e) => runExport(e.currentTarget, async () => {
        const rows = await fetchAll(api.audit, { action: auditFilters.action, search: auditFilters.search, ...periodParams() });
        const strip = (h) => h.replace(/<br>/g, '; ').replace(/<[^>]+>/g, ' ').replace(/&[a-z#0-9]+;/g, (m) => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" }[m] || m)).replace(/\s+/g, ' ').trim();
        downloadCsv('аудит', ['Дата', 'Действие', 'Кто', 'Роль', 'Организация', 'Причина / изменения', 'IP'],
          rows.map(a => [dTime(a.created_at), AUDIT_META[a.action]?.[1] || a.action, a.actor_phone || '', ACTOR_LABEL[a.actor_type] || a.actor_type,
            a.organization_name || '', strip(auditDetails(a)), a.ip_address || '']));
      });
    },
  };
}

/* ================= старт ================= */

hydrateIcons();
$$('.login-art [data-logo-mark], .phone-card [data-logo-mark]').forEach(el => { el.outerHTML = logoMark('#F6F0FF', 'ic-logo'); });
if (store.access && store.user) enterApp();
