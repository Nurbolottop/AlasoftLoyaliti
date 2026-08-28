/* AlaSoft — панель директора.
   Отдельное веб-приложение поверх Director API (ТЗ §16, ТЗ backend §1).
   Своей бизнес-логики не содержит: любые суммы, лимиты и права считает сервер. */

const API = '/api/v1';
const store = {
  get access() { return localStorage.getItem('as_access'); },
  get refresh() { return localStorage.getItem('as_refresh'); },
  get user() { try { return JSON.parse(localStorage.getItem('as_user')); } catch { return null; } },
  save(tokens, user) {
    if (tokens?.access) localStorage.setItem('as_access', tokens.access);
    if (tokens?.refresh) localStorage.setItem('as_refresh', tokens.refresh);
    if (user) localStorage.setItem('as_user', JSON.stringify(user));
  },
  clear() { ['as_access', 'as_refresh', 'as_user'].forEach(k => localStorage.removeItem(k)); },
};

/* ---------- работа с API ---------- */

class ApiError extends Error {
  constructor(code, message, details, status) {
    super(message || 'Ошибка запроса');
    Object.assign(this, { code, details: details || {}, status });
  }
}

async function request(path, { method = 'GET', body, auth = true, retry = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && store.access) headers['Authorization'] = `Bearer ${store.access}`;
  if (method !== 'GET' && !['/auth/director/login', '/auth/refresh'].includes(path)) {
    headers['Idempotency-Key'] = crypto.randomUUID();
  }

  const res = await fetch(API + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
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

const api = {
  login: (phone, password) => request('/auth/director/login', {
    method: 'POST', body: { phone, password }, auth: false,
  }),
  dashboard: () => request('/director/dashboard'),
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

function qs(params) {
  if (!params) return '';
  const s = Object.entries(params)
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  return s ? '?' + s : '';
}

/* ---------- отображение значений ---------- */

const som = (t) => t == null ? '—' : (t / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' сом';
const pct = (bps) => bps == null ? '—' : (bps / 100) + '%';
const num = (n) => (n ?? 0).toLocaleString('ru-RU');
const dt = (s) => s ? new Date(s).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const PROGRAM_LABEL = { VISIT: 'Накопительная N+1', CASHBACK: 'Cashback' };
const TX_LABEL = {
  VISIT_EARN: 'Посещение', GIFT_CREATED: 'Подарок начислен', GIFT_REDEEM: 'Подарок использован',
  CASHBACK_EARN: 'Cashback начислен', CASHBACK_SPEND: 'Cashback списан',
  CASHBACK_EXPIRE: 'Cashback сгорел', REVERSAL: 'Отмена',
};
const AUDIT_LABEL = {
  LOGIN: 'Вход', LOGIN_FAILED: 'Неудачный вход', LOGOUT: 'Выход',
  PIN_SET: 'Установлен PIN', PIN_RESET: 'Сброшен PIN',
  ORGANIZATION_CREATED: 'Организация создана', ORGANIZATION_UPDATED: 'Организация изменена',
  ORGANIZATION_BLOCKED: 'Организация заблокирована', ORGANIZATION_UNBLOCKED: 'Организация разблокирована',
  PROGRAM_UPDATED: 'Программа изменена', ADMIN_CREATED: 'Создан администратор',
  ADMIN_RESET: 'Доступ администратора сброшен', VISIT_EARNED: 'Начислено посещение',
  CASHBACK_EARNED: 'Начислен cashback', CASHBACK_SPENT: 'Списан cashback',
  GIFT_REDEEMED: 'Использован подарок', REDEMPTION_CREATED: 'Запрос на списание',
  REDEMPTION_CONFIRMED: 'Списание подтверждено', REDEMPTION_REJECTED: 'Списание отклонено',
  TRANSACTION_REVERSED: 'Операция отменена', CASHBACK_EXPIRED: 'Cashback сгорел',
};

function statusBadge(s) {
  if (s === 'ACTIVE') return '<span class="badge ok">Активна</span>';
  if (s === 'BLOCKED') return '<span class="badge bad">Заблокирована</span>';
  if (s === 'COMPLETED') return '<span class="badge ok">Проведена</span>';
  if (s === 'REVERSED') return '<span class="badge bad">Отменена</span>';
  if (s === 'PENDING') return '<span class="badge warn">Ожидает</span>';
  return `<span class="badge neutral">${esc(s)}</span>`;
}

/* ---------- элементы интерфейса ---------- */

const $ = (s) => document.querySelector(s);
const view = () => $('#view');

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
  toast(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : 'Не удалось выполнить запрос', true);
}

function modal(title, html) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal').hidden = false;
}
const closeModal = () => { $('#modal').hidden = true; };

function loading() { view().innerHTML = '<div class="spinner">Загрузка…</div>'; }
function empty(text) { return `<div class="empty">${esc(text)}</div>`; }

function pager(meta, onPage) {
  const p = meta?.pagination;
  if (!p || p.total_pages <= 1) return '';
  setTimeout(() => {
    view().querySelectorAll('[data-page]').forEach(b =>
      b.onclick = () => onPage(Number(b.dataset.page)));
  }, 0);
  return `<div class="pager">
    <button class="btn ghost small" data-page="${p.page - 1}" ${p.has_previous ? '' : 'disabled'}>← Назад</button>
    <span class="muted">Стр. ${p.page} из ${p.total_pages} · всего ${num(p.total_items)}</span>
    <button class="btn ghost small" data-page="${p.page + 1}" ${p.has_next ? '' : 'disabled'}>Вперёд →</button>
  </div>`;
}

/* ---------- вход ---------- */

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
$('#modal').onclick = (e) => { if (e.target.id === 'modal') closeModal(); };

function enterApp() {
  $('#login-screen').hidden = true;
  $('#app').hidden = false;
  const u = store.user;
  $('#who').textContent = u ? `${u.full_name || 'Директор'} · ${u.phone}` : '';
  if (!location.hash) location.hash = '#/dashboard';
  route();
}

/* ---------- роутер ---------- */

const routes = {
  dashboard: { title: 'Дашборд', render: renderDashboard },
  organizations: { title: 'Организации', render: renderOrganizations },
  organization: { title: 'Организация', render: renderOrganization },
  users: { title: 'Пользователи', render: renderUsers },
  transactions: { title: 'Транзакции', render: renderTransactions },
  statistics: { title: 'Статистика', render: renderStatistics },
  audit: { title: 'Аудит', render: renderAudit },
};

async function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  const key = parts[0] || 'dashboard';
  const param = parts[1];
  const r = routes[key] || routes.dashboard;

  $('#page-title').textContent = r.title;
  $('#page-actions').innerHTML = '';
  document.querySelectorAll('[data-nav]').forEach(a =>
    a.classList.toggle('active', a.dataset.nav === (key === 'organization' ? 'organizations' : key)));

  loading();
  try { await r.render(param); }
  catch (e) { showError(e); view().innerHTML = empty('Не удалось загрузить данные'); }
}
window.addEventListener('hashchange', route);

/* ---------- дашборд ---------- */

async function renderDashboard() {
  const { data } = await api.dashboard();
  const s = data.statistics;
  view().innerHTML = `
    <div class="grid">
      ${stat('Организации', num(s.organizations_total), `активных ${num(s.organizations_active)}, заблокировано ${num(s.organizations_blocked)}`)}
      ${stat('Клиенты', num(s.users_total), `активны за период: ${num(s.users_active_in_period)}`)}
      ${stat('Операции за 30 дней', num(s.operations_total), `отмен: ${num(s.reversals)}`)}
      ${stat('Подарки', num(s.gifts_created), `использовано ${num(s.gifts_used)}`)}
      ${stat('Начислено cashback', som(s.cashback_earned), `списано ${som(s.cashback_spent)}`)}
      ${stat('Сгорело cashback', som(s.cashback_expired), 'за последние 30 дней')}
    </div>
    <div class="card">
      <h3>Программы лояльности</h3>
      <div class="row">
        <div><div class="muted">Накопительные (N+1)</div><div class="stat-inline">${num(s.organizations_by_program?.VISIT)}</div></div>
        <div><div class="muted">Cashback</div><div class="stat-inline">${num(s.organizations_by_program?.CASHBACK)}</div></div>
      </div>
    </div>
    <h3>Последние организации</h3>
    ${data.recent_organizations?.length ? orgTable(data.recent_organizations) : empty('Организаций пока нет. Создайте первую на вкладке «Организации».')}
  `;
}
const stat = (label, value, hint) =>
  `<div class="stat"><div class="label">${esc(label)}</div><div class="value">${value}</div>${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>`;

/* ---------- организации ---------- */

let orgFilters = { search: '', status: '', loyalty_type: '', page: 1 };

async function renderOrganizations() {
  $('#page-actions').innerHTML = '<button class="btn primary" id="new-org">+ Создать организацию</button>';
  $('#new-org').onclick = openCreateOrg;

  const { data, meta } = await api.orgs(orgFilters);
  view().innerHTML = `
    <div class="filters">
      <label>Поиск<input id="f-search" placeholder="название или телефон" value="${esc(orgFilters.search)}"></label>
      <label>Статус<select id="f-status">
        <option value="">Все</option>
        <option value="ACTIVE" ${orgFilters.status === 'ACTIVE' ? 'selected' : ''}>Активные</option>
        <option value="BLOCKED" ${orgFilters.status === 'BLOCKED' ? 'selected' : ''}>Заблокированные</option>
      </select></label>
      <label>Программа<select id="f-type">
        <option value="">Все</option>
        <option value="VISIT" ${orgFilters.loyalty_type === 'VISIT' ? 'selected' : ''}>Накопительная</option>
        <option value="CASHBACK" ${orgFilters.loyalty_type === 'CASHBACK' ? 'selected' : ''}>Cashback</option>
      </select></label>
      <button class="btn ghost" id="f-apply">Применить</button>
    </div>
    ${data.length ? orgTable(data) : empty('Ничего не найдено')}
    ${pager(meta, (p) => { orgFilters.page = p; renderOrganizations(); })}
  `;
  $('#f-apply').onclick = () => {
    orgFilters = {
      search: $('#f-search').value.trim(), status: $('#f-status').value,
      loyalty_type: $('#f-type').value, page: 1,
    };
    renderOrganizations();
  };
  $('#f-search').onkeydown = (e) => { if (e.key === 'Enter') $('#f-apply').click(); };
}

const orgTable = (orgs) => `
  <div class="table-wrap"><table>
    <thead><tr><th>Название</th><th>Программа</th><th>Статус</th><th>Администратор</th><th class="right">Клиентов</th><th></th></tr></thead>
    <tbody>${orgs.map(o => `
      <tr>
        <td><strong>${esc(o.name)}</strong><br><span class="muted">${esc(o.category?.name || o.address || '—')}</span></td>
        <td><span class="badge violet">${PROGRAM_LABEL[o.loyalty_type] || o.loyalty_type}</span><br>
            <span class="muted">${programSummary(o)}</span></td>
        <td>${statusBadge(o.status)}</td>
        <td>${o.admin ? esc(o.admin.phone) : '<span class="muted">не назначен</span>'}</td>
        <td class="right">${num(o.customers_count)}</td>
        <td class="right"><a class="btn ghost small" href="#/organization/${o.id}">Открыть</a></td>
      </tr>`).join('')}
    </tbody>
  </table></div>`;

function programSummary(o) {
  if (o.loyalty_type === 'VISIT' && o.visit_program)
    return `${o.visit_program.target_visits} посещений = ${o.visit_program.reward_count} подарок`;
  if (o.loyalty_type === 'CASHBACK' && o.cashback_program)
    return `${pct(o.cashback_program.cashback_rate_bps)}, до ${pct(o.cashback_program.max_spend_percent_bps)} чека, ${o.cashback_program.expiry_days} дн.`;
  return 'не настроена';
}

async function openCreateOrg() {
  const { data: cats } = await api.categories();
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

      <div id="visit-fields">
        <h3>Условия накопления</h3>
        <div class="row">
          <label>Посещений до подарка<input type="number" name="target_visits" value="5" min="1" max="100"></label>
          <label>Подарков за цикл<input type="number" name="reward_count" value="1" min="1" max="10"></label>
        </div>
        <label>Название подарка<input name="reward_title_ru" value="Подарок"></label>
      </div>

      <div id="cashback-fields" hidden>
        <h3>Условия cashback</h3>
        <div class="row">
          <label>Процент начисления<input type="number" name="rate" value="5" min="0.01" max="100" step="0.01"></label>
          <label>Макс. % оплаты чека<input type="number" name="max_spend" value="30" min="0.01" max="100" step="0.01"></label>
          <label>Срок сгорания, дней<input type="number" name="expiry_days" value="90" min="1" max="3650"></label>
        </div>
      </div>

      <div class="alert error" id="org-err" hidden></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" id="org-cancel">Отмена</button>
        <button type="submit" class="btn primary">Создать</button>
      </div>
    </form>`);

  $('#lt').onchange = (e) => {
    $('#visit-fields').hidden = e.target.value !== 'VISIT';
    $('#cashback-fields').hidden = e.target.value !== 'CASHBACK';
  };
  $('#org-cancel').onclick = closeModal;
  $('#org-form').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const body = {
      name: f.name, category: f.category, loyalty_type: f.loyalty_type,
      phone: f.phone || '', address: f.address || '', description_ru: f.description_ru || '',
    };
    if (f.loyalty_type === 'VISIT') {
      body.visit_program = {
        target_visits: +f.target_visits, reward_count: +f.reward_count,
        reward_title_ru: f.reward_title_ru || 'Подарок',
      };
    } else {
      body.cashback_program = {
        cashback_rate_bps: Math.round(+f.rate * 100),
        max_spend_percent_bps: Math.round(+f.max_spend * 100),
        expiry_days: +f.expiry_days,
      };
    }
    try {
      const r = await api.orgCreate(body);
      closeModal();
      toast('Организация создана');
      location.hash = `#/organization/${r.data.id}`;
    } catch (ex) {
      const el = $('#org-err');
      el.textContent = ex.message + (ex.details?.fields ? ': ' + JSON.stringify(ex.details.fields) : '');
      el.hidden = false;
    }
  };
}

/* ---------- карточка организации ---------- */

async function renderOrganization(id) {
  const { data: o } = await api.org(id);
  $('#page-title').textContent = o.name;
  $('#page-actions').innerHTML = `<a class="btn ghost" href="#/organizations">← К списку</a>`;

  view().innerHTML = `
    <div class="grid">
      ${stat('Статус', o.status === 'ACTIVE' ? 'Активна' : 'Заблокирована', o.blocked_reason || '')}
      ${stat('Программа', PROGRAM_LABEL[o.loyalty_type], programSummary(o))}
      ${stat('Клиентов', num(o.customers_count), '')}
    </div>

    <div class="card">
      <h3>Реквизиты</h3>
      <div class="row">
        <div><div class="muted">Телефон</div>${esc(o.phone || '—')}</div>
        <div><div class="muted">Адрес</div>${esc(o.address || '—')}</div>
        <div><div class="muted">Категория</div>${esc(o.category?.name_ru || '—')}</div>
        <div><div class="muted">Создана</div>${dt(o.created_at)}</div>
      </div>
      <p class="muted" style="margin-top:14px">${esc(o.description_ru || '')}</p>
    </div>

    <div class="card">
      <h3>Настройки программы</h3>
      <form id="prog-form">${o.loyalty_type === 'VISIT' ? `
        <div class="row">
          <label>Посещений до подарка<input type="number" name="target_visits" value="${o.visit_program?.target_visits ?? 5}" min="1" max="100"></label>
          <label>Подарков за цикл<input type="number" name="reward_count" value="${o.visit_program?.reward_count ?? 1}" min="1" max="10"></label>
          <label>Название подарка<input name="reward_title_ru" value="${esc(o.visit_program?.reward_title_ru || 'Подарок')}"></label>
        </div>` : `
        <div class="row">
          <label>Процент начисления<input type="number" name="rate" value="${(o.cashback_program?.cashback_rate_bps ?? 500) / 100}" min="0.01" max="100" step="0.01"></label>
          <label>Макс. % оплаты чека<input type="number" name="max_spend" value="${(o.cashback_program?.max_spend_percent_bps ?? 3000) / 100}" min="0.01" max="100" step="0.01"></label>
          <label>Срок сгорания, дней<input type="number" name="expiry_days" value="${o.cashback_program?.expiry_days ?? 90}" min="1" max="3650"></label>
        </div>`}
        <button class="btn primary">Сохранить настройки</button>
      </form>
      <p class="muted" style="margin-top:10px">Изменения действуют только на будущие операции: применённые параметры сохраняются в каждой транзакции.</p>
    </div>

    <div class="card">
      <h3>Администратор организации</h3>
      ${o.admin ? `
        <div class="row">
          <div><div class="muted">Телефон (логин в приложении)</div><strong>${esc(o.admin.phone)}</strong></div>
          <div><div class="muted">Имя</div>${esc(o.admin.full_name || '—')}</div>
          <div><div class="muted">PIN задан</div>${o.admin.has_pin ? 'да' : 'нет'}</div>
        </div>
        <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap">
          <button class="btn ghost" id="admin-reset">Сбросить PIN</button>
          <button class="btn ghost" id="admin-revoke">Отозвать доступ</button>
        </div>` : `
        <p class="muted">Не назначен — организация не сможет обслуживать клиентов.</p>
        <button class="btn primary" id="admin-add">Назначить администратора</button>`}
    </div>

    <div class="card">
      <h3>Управление</h3>
      ${o.status === 'ACTIVE'
        ? `<button class="btn danger" id="block-btn">Заблокировать организацию</button>
           <p class="muted" style="margin-top:10px">Пока организация заблокирована, её администратор не сможет начислять и списывать.</p>`
        : `<button class="btn primary" id="unblock-btn">Разблокировать</button>`}
    </div>
  `;

  $('#prog-form').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const body = o.loyalty_type === 'VISIT'
      ? { visit_program: { target_visits: +f.target_visits, reward_count: +f.reward_count, reward_title_ru: f.reward_title_ru } }
      : { cashback_program: {
            cashback_rate_bps: Math.round(+f.rate * 100),
            max_spend_percent_bps: Math.round(+f.max_spend * 100),
            expiry_days: +f.expiry_days } };
    try { await api.orgUpdate(id, body); toast('Настройки сохранены'); renderOrganization(id); }
    catch (ex) { showError(ex); }
  };

  const bind = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
  bind('#block-btn', () => confirmModal('Заблокировать организацию', 'Укажите причину — она попадёт в аудит.', true,
    async (reason) => { await api.orgBlock(id, reason); toast('Организация заблокирована'); renderOrganization(id); }));
  bind('#unblock-btn', async () => { try { await api.orgUnblock(id); toast('Организация разблокирована'); renderOrganization(id); } catch (e) { showError(e); } });
  bind('#admin-add', () => openAdminForm(id, false));
  bind('#admin-reset', () => openAdminForm(id, true, o.admin.phone));
  bind('#admin-revoke', () => confirmModal('Отозвать доступ администратора', 'Администратор перестанет обслуживать клиентов сразу же.', false,
    async () => { await api.orgAdminRevoke(id); toast('Доступ отозван'); renderOrganization(id); }));
}

function openAdminForm(orgId, isReset, phone = '') {
  modal(isReset ? 'Сбросить доступ администратора' : 'Назначить администратора', `
    <form id="adm-form">
      <label>Телефон администратора *<input name="phone" value="${esc(phone)}" placeholder="+996700000010" required></label>
      <label>Имя<input name="first_name" placeholder="Имя сотрудника"></label>
      <p class="muted">Администратор входит в мобильном приложении по этому телефону и PIN-коду. PIN будет показан один раз — сразу после создания.</p>
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
        <div class="card" style="box-shadow:none;background:var(--bg)">
          <div class="muted">Телефон (логин)</div><h2>${esc(r.data.admin.phone)}</h2>
          <div class="muted" style="margin-top:14px">Временный PIN</div><h2>${esc(r.data.temporary_pin)}</h2>
        </div>
        <div class="modal-actions"><button class="btn primary" id="ok">Готово</button></div>`);
      $('#ok').onclick = () => { closeModal(); renderOrganization(orgId); };
    } catch (ex) {
      const el = $('#adm-err'); el.textContent = ex.message; el.hidden = false;
    }
  };
}

function confirmModal(title, text, needReason, onConfirm) {
  modal(title, `
    <p>${esc(text)}</p>
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

/* ---------- пользователи ---------- */

let userSearch = { search: '', page: 1 };

async function renderUsers() {
  const { data, meta } = await api.users(userSearch);
  view().innerHTML = `
    <div class="filters">
      <label>Поиск<input id="u-search" placeholder="телефон, имя, ID или 6-значный код" value="${esc(userSearch.search)}" style="min-width:320px"></label>
      <button class="btn ghost" id="u-apply">Найти</button>
    </div>
    ${data.length ? `
    <div class="table-wrap"><table>
      <thead><tr><th>Клиент</th><th>Телефон</th><th>Код</th><th>Регистрация</th><th>Статус</th><th></th></tr></thead>
      <tbody>${data.map(u => `
        <tr>
          <td><strong>${esc(u.full_name || '—')}</strong></td>
          <td class="mono">${esc(u.phone)}</td>
          <td><span class="badge violet">${esc(u.public_code || '—')}</span></td>
          <td>${dt(u.date_joined)}</td>
          <td>${u.is_active ? '<span class="badge ok">Активен</span>' : '<span class="badge bad">Заблокирован</span>'}</td>
          <td class="right"><button class="btn ghost small" data-user="${u.id}">Открыть</button></td>
        </tr>`).join('')}
      </tbody>
    </table></div>` : empty('Клиенты не найдены')}
    ${pager(meta, (p) => { userSearch.page = p; renderUsers(); })}
  `;
  $('#u-apply').onclick = () => { userSearch = { search: $('#u-search').value.trim(), page: 1 }; renderUsers(); };
  $('#u-search').onkeydown = (e) => { if (e.key === 'Enter') $('#u-apply').click(); };
  view().querySelectorAll('[data-user]').forEach(b => b.onclick = () => openUser(b.dataset.user));
}

async function openUser(id) {
  const { data: u } = await api.user(id);
  modal(u.full_name || u.phone, `
    <div class="row" style="margin-bottom:16px">
      <div><div class="muted">Телефон</div><strong>${esc(u.phone)}</strong></div>
      <div><div class="muted">6-значный код</div><strong>${esc(u.public_code || '—')}</strong></div>
      <div><div class="muted">Язык</div>${u.language === 'ky' ? 'Кыргызча' : 'Русский'}</div>
    </div>
    <h3>Балансы по организациям</h3>
    ${u.organizations?.length ? `
      <div class="table-wrap"><table>
        <thead><tr><th>Организация</th><th>Прогресс</th><th>Подарки</th><th>Cashback</th></tr></thead>
        <tbody>${u.organizations.map(o => `
          <tr>
            <td>${esc(o.organization_name)}<br><span class="muted">${PROGRAM_LABEL[o.loyalty_type]}</span></td>
            <td>${o.loyalty_type === 'VISIT' ? num(o.visit_progress) : '—'}</td>
            <td>${o.loyalty_type === 'VISIT' ? num(o.available_gifts) : '—'}</td>
            <td>${o.loyalty_type === 'CASHBACK' ? som(o.cashback_available) : '—'}</td>
          </tr>`).join('')}
        </tbody>
      </table></div>
      <p class="muted" style="margin-top:12px">Баланс каждой организации расходуется только в ней — общего кошелька нет.</p>`
      : '<p class="muted">Клиент ещё не участвует ни в одной программе.</p>'}
    <div class="modal-actions"><button class="btn ghost" id="u-close">Закрыть</button></div>`);
  $('#u-close').onclick = closeModal;
}

/* ---------- транзакции ---------- */

let txFilters = { type: '', status: '', organization_id: '', date_from: '', date_to: '', page: 1 };

async function renderTransactions() {
  const [{ data, meta }, orgsRes] = await Promise.all([api.transactions(txFilters), api.orgs({ page_size: 100 })]);
  view().innerHTML = `
    <div class="filters">
      <label>Организация<select id="t-org">
        <option value="">Все</option>
        ${orgsRes.data.map(o => `<option value="${o.id}" ${txFilters.organization_id === o.id ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}
      </select></label>
      <label>Тип<select id="t-type">
        <option value="">Все</option>
        ${Object.entries(TX_LABEL).map(([k, v]) => `<option value="${k}" ${txFilters.type === k ? 'selected' : ''}>${v}</option>`).join('')}
      </select></label>
      <label>Статус<select id="t-status">
        <option value="">Все</option>
        <option value="COMPLETED" ${txFilters.status === 'COMPLETED' ? 'selected' : ''}>Проведённые</option>
        <option value="REVERSED" ${txFilters.status === 'REVERSED' ? 'selected' : ''}>Отменённые</option>
      </select></label>
      <button class="btn ghost" id="t-apply">Применить</button>
    </div>
    ${data.length ? `
    <div class="table-wrap"><table>
      <thead><tr><th>Дата</th><th>Операция</th><th>Организация</th><th class="right">Сумма</th><th>Статус</th><th></th></tr></thead>
      <tbody>${data.map(t => `
        <tr>
          <td>${dt(t.created_at)}</td>
          <td>${TX_LABEL[t.type] || esc(t.type)}${t.reason ? `<br><span class="muted">${esc(t.reason)}</span>` : ''}</td>
          <td>${esc(t.organization?.name || '—')}</td>
          <td class="right">${t.amount == null ? '—' : som(t.amount)}</td>
          <td>${statusBadge(t.status)}</td>
          <td class="right">${t.status === 'COMPLETED' && ['VISIT_EARN','CASHBACK_EARN','CASHBACK_SPEND','GIFT_REDEEM'].includes(t.type)
            ? `<button class="btn ghost small" data-rev="${t.id}">Отменить</button>` : ''}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>` : empty('Операций не найдено')}
    ${pager(meta, (p) => { txFilters.page = p; renderTransactions(); })}
  `;
  $('#t-apply').onclick = () => {
    txFilters = { ...txFilters, organization_id: $('#t-org').value, type: $('#t-type').value, status: $('#t-status').value, page: 1 };
    renderTransactions();
  };
  view().querySelectorAll('[data-rev]').forEach(b => b.onclick = () => openReverse(b.dataset.rev));
}

function openReverse(id) {
  modal('Отмена операции', `
    <p>Операция не удаляется: в журнале останется исходная запись, а рядом появится компенсирующая. Причина обязательна и попадёт в аудит.</p>
    <label>Причина *<input id="rev-reason" maxlength="255" required></label>
    <label style="display:flex;gap:9px;align-items:center;margin-top:6px">
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
      closeModal(); toast('Операция отменена'); renderTransactions();
    } catch (ex) {
      const el = $('#rev-err'); el.textContent = ex.message; el.hidden = false;
    }
  };
}

/* ---------- статистика ---------- */

async function renderStatistics() {
  const orgsRes = await api.orgs({ page_size: 100 });
  const orgId = renderStatistics.orgId || '';
  const { data: s } = await api.statistics(orgId ? { organization_id: orgId } : {});

  view().innerHTML = `
    <div class="filters">
      <label>Разрез<select id="s-org">
        <option value="">Вся платформа</option>
        ${orgsRes.data.map(o => `<option value="${o.id}" ${orgId === o.id ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}
      </select></label>
    </div>
    <div class="grid">${(orgId ? orgStats(s) : platformStats(s)).join('')}</div>
    <p class="muted">Период: последние 30 дней.</p>`;
  $('#s-org').onchange = (e) => { renderStatistics.orgId = e.target.value; renderStatistics(); };
}

const platformStats = (s) => [
  stat('Организаций', num(s.organizations_total), `активных ${num(s.organizations_active)}`),
  stat('Клиентов', num(s.users_total), `завершили регистрацию ${num(s.users_registered)}`),
  stat('Активны за период', num(s.users_active_in_period), ''),
  stat('Операций', num(s.operations_total), `отмен ${num(s.reversals)}`),
  stat('Начислено cashback', som(s.cashback_earned), ''),
  stat('Списано cashback', som(s.cashback_spent), ''),
  stat('Сгорело cashback', som(s.cashback_expired), ''),
  stat('Подарков создано', num(s.gifts_created), `использовано ${num(s.gifts_used)}`),
];

const orgStats = (s) => s.loyalty_type === 'VISIT' ? [
  stat('Клиентов', num(s.customers_total), `активных ${num(s.customers_active)}`),
  stat('Посещений', num(s.visits), ''),
  stat('Подарков создано', num(s.gifts_created), `использовано ${num(s.gifts_used)}`),
  stat('Доступно подарков', num(s.gifts_available_now), ''),
  stat('Повторные клиенты', num(s.repeat_customers), '2 и более посещений'),
  stat('Отмен', num(s.reversals), ''),
] : [
  stat('Клиентов', num(s.customers_total), `активных ${num(s.customers_active)}`),
  stat('Оборот', som(s.turnover), 'сумма чеков'),
  stat('Начислено', som(s.cashback_earned), ''),
  stat('Списано', som(s.cashback_spent), ''),
  stat('Сгорело', som(s.cashback_expired), ''),
  stat('Активный баланс', som(s.cashback_active_balance), 'не сгоревший остаток'),
  stat('Отмен', num(s.reversals), ''),
];

/* ---------- аудит ---------- */

let auditFilters = { action: '', page: 1 };

async function renderAudit() {
  const { data, meta } = await api.audit(auditFilters);
  view().innerHTML = `
    <div class="filters">
      <label>Действие<select id="a-action">
        <option value="">Все</option>
        ${Object.entries(AUDIT_LABEL).map(([k, v]) => `<option value="${k}" ${auditFilters.action === k ? 'selected' : ''}>${v}</option>`).join('')}
      </select></label>
      <button class="btn ghost" id="a-apply">Применить</button>
    </div>
    ${data.length ? `
    <div class="table-wrap"><table>
      <thead><tr><th>Дата</th><th>Действие</th><th>Кто</th><th>Организация</th><th>Причина</th></tr></thead>
      <tbody>${data.map(a => `
        <tr>
          <td>${dt(a.created_at)}</td>
          <td>${AUDIT_LABEL[a.action] || esc(a.action)}</td>
          <td>${esc(a.actor_phone || a.actor_type)}<br><span class="muted">${esc(a.actor_type)}</span></td>
          <td>${esc(a.organization_name || '—')}</td>
          <td>${esc(a.reason || '—')}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>` : empty('Записей нет')}
    ${pager(meta, (p) => { auditFilters.page = p; renderAudit(); })}
  `;
  $('#a-apply').onclick = () => { auditFilters = { action: $('#a-action').value, page: 1 }; renderAudit(); };
}

/* ---------- старт ---------- */

if (store.access && store.user) enterApp();
