const API_BASE = '';
const TOKEN_KEY = 'spiritagent_admin_token';
const tabCache = { users: null, models: null, logs: null, versions: null };

const TABS = {
    overview: { title: '总览', desc: '功能配置与最近活动' },
    users:    { title: '用户管理', desc: '管理可登录客户端的账户、使用权限与激活码' },
    ai:       { title: 'AI 配置', desc: '按功能组织能力，拖拽编排模型调用链' },
    settings: { title: '系统参数', desc: '全局业务参数与运行时调优（实时热生效，无需重启容器）' },
    versions: { title: '版本发布', desc: '桌面端更新包与版本分发' },
    logs: { title: '夜间日志', desc: '夜间活动的执行状态与阶段结果' },
};

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
const getItems = (data) => data?.items ?? [];

function pill(text, type = 'muted') { return `<span class="pill pill--${type}">${esc(text)}</span>`; }
function plat(label, on) { return `<span class="plat ${on ? 'plat--on' : 'plat--off'}">${esc(label)}</span>`; }

function fmtDate(s) {
    if (!s) return '—';
    const d = new Date(s);
    if (isNaN(d)) return '—';
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function fmtSize(b) {
    if (b === null || b === undefined || isNaN(b)) return '';
    if (b === 0) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0, n = b;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
}

function toast(msg, type = 'ok') {
    const wrap = $('#toast-wrap');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(0.4rem)';
        el.style.transition = 'all .16s ease';
        setTimeout(() => el.remove(), 200);
    }, 2600);
}

function stateRow(colspan, msg, hint = '', err = false) {
    return `<tr class="state-row ${err ? 'err' : ''}"><td colspan="${colspan}"><div class="state-msg">${esc(msg)}</div>${hint ? `<div class="state-hint">${esc(hint)}</div>` : ''}</td></tr>`;
}

function renderTable(tbodyId, countId, data, { unit, cols, empty, emptyHint, row }) {
    const items = getItems(data);
    $('#' + countId).textContent = items.length ? `${items.length} ${unit}` : '';
    $(tbodyId).innerHTML = items.length ? items.map(row).join('') : stateRow(cols, empty, emptyHint);
}

function closeAllMenus() {
    $$('.menu.open').forEach(m => {
        m.classList.remove('open');
        const pop = m.querySelector('.menu-pop');
        if (pop) {
            pop.style.left = '';
            pop.style.top = '';
            pop.style.visibility = '';
        }
    });
}

function menuHtml(actions) {
    const items = actions.map(a => {
        if (a === '|') return '<div class="menu-sep"></div>';
        return `<button type="button" class="menu-item${a.danger ? ' danger' : ''}" onclick="event.stopPropagation(); closeAllMenus(); ${a.onclick}">${esc(a.label)}</button>`;
    }).join('');
    return `<div class="menu">
        <button type="button" class="btn btn--icon" aria-label="更多操作" onclick="event.stopPropagation(); toggleMenu(this)" title="更多操作">⋯</button>
        <div class="menu-pop">${items}</div>
    </div>`;
}

function placeMenuPop(btn, pop) {
    const gap = 6;
    const margin = 8;
    pop.style.left = '0px';
    pop.style.top = '0px';
    pop.style.visibility = 'hidden';
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    const rect = btn.getBoundingClientRect();

    let top = rect.bottom + gap;
    if (top + ph > window.innerHeight - margin) {
        const flipped = rect.top - gap - ph;
        top = flipped >= margin ? flipped : Math.max(margin, window.innerHeight - margin - ph);
    }
    let left = rect.right - pw;
    if (left < margin) left = margin;
    if (left + pw > window.innerWidth - margin) left = Math.max(margin, window.innerWidth - margin - pw);

    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top)}px`;
    pop.style.visibility = '';
}

function toggleMenu(btn) {
    const menu = btn.closest('.menu');
    const pop = menu.querySelector('.menu-pop');
    const wasOpen = menu.classList.contains('open');
    closeAllMenus();
    if (wasOpen) return;
    menu.classList.add('open');
    placeMenuPop(btn, pop);
}

const TAB_LOADERS = { overview: loadOverview, users: loadUsers, ai: loadAiView, settings: loadSystemSettings, versions: loadVersions, logs: loadLogs };

function showTab(name) {
    $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
    const t = TABS[name];
    $('#topTitle').textContent = t.title;
    $('#topDesc').textContent = t.desc;
    closeAllMenus();
    syncXhsLoginVisibility();
    updateSaveActions();
    void TAB_LOADERS[name]();
}

function getToken() { return sessionStorage.getItem(TOKEN_KEY); }

// detail 可为文本、校验错误列表或 {error, reason, status}；限流与未处理异常在顶层返回 {error, reason, status}。
async function errorText(resp) {
    const body = await resp.json().catch(() => null);
    const detail = body?.detail;
    if (Array.isArray(detail)) return detail.map(item => `${item?.loc?.at(-1) ?? ''}: ${item?.msg ?? ''}`).join('；');
    if (detail && typeof detail === 'object') return detail.error || `HTTP ${resp.status}`;
    return detail || body?.error || `HTTP ${resp.status}`;
}

async function apiFetch(path, options = {}) {
    const headers = { ...(getToken() ? { Authorization: 'Bearer ' + getToken() } : {}) };
    if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    let resp;
    try { resp = await fetch(API_BASE + path, { ...options, headers: { ...headers, ...(options.headers || {}) } }); }
    catch (error) { if (error.name !== 'AbortError') { $('#runtimeChip').textContent = '连接失败'; $('#runtimeChip').className = 'status-chip failed'; } throw error; }
    if (resp.status === 401) {
        stopXhsLogin();
        sessionStorage.removeItem(TOKEN_KEY);
        location.reload();
        throw new Error('会话已过期，请重新登录');
    }
    if (!resp.ok) throw new Error(await errorText(resp));
    $('#runtimeChip').textContent = '已连接';
    $('#runtimeChip').className = 'status-chip connected';
    return resp;
}

function enterApp() {
    $('#login').style.display = 'none';
    $('#app').style.display = 'block';
    showTab('overview');
}

function logout() {
    if ((settingsDirty || aiSystemDirty || aiUserDirty) && !confirm('系统设置或 AI 配置有未保存的修改，确定退出登录？')) return;
    stopXhsLogin();
    sessionStorage.removeItem(TOKEN_KEY);
    location.reload();
}

$('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const u = $('#loginUser').value.trim(), p = $('#loginPass').value;
    if (!u || !p) return;
    const err = $('#loginError');
    try {
        const resp = await fetch(API_BASE + '/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        if (!resp.ok) {
            throw new Error(resp.status === 401 ? '用户名或密码错误'
                : resp.status === 429 ? '登录尝试过于频繁，请稍后再试'
                : await errorText(resp));
        }
        const data = await resp.json();
        sessionStorage.setItem(TOKEN_KEY, data.access_token);
        err.style.display = 'none';
        enterApp();
    } catch (e) {
        err.textContent = e.message || String(e);
        err.style.display = 'block';
    }
});



async function loadUsers() {
    try {
        const data = await (await apiFetch('/api/admin/users')).json();
        tabCache.users = data;
        if (!tabCache.models) {
            // 用户表展示能力覆盖列，静默拉取；失败不阻塞用户列表。
            apiFetch('/api/admin/model-configs').then(r => r.json()).then(d => { tabCache.models = d; renderUsers(tabCache.users); }).catch(() => {});
        }
        renderUsers(data);
    } catch (e) { $('#userList').innerHTML = stateRow(5, '加载失败：' + e.message, '', true); }
}

function filterUsers() {
    if (tabCache.users) renderUsers(tabCache.users);
}

function userOverrideSummary(id) {
    const item = tabCache.models?.items?.find(c => c.user_id === id);
    if (!item) return null;
    const groups = Object.entries(item.ai_config?.capabilities ?? {})
        .filter(([, cards]) => (cards ?? []).length > 0)
        .map(([group]) => READINESS_KEY_LABELS[group] ?? group);
    for (const [taskId, value] of Object.entries(item.ai_config?.tasks ?? {})) {
        if (Object.values(value.chains ?? {}).some(hasChainOverride)) groups.push((tabCache.models?.ai_tasks ?? []).find(task => task.id === taskId)?.label ?? taskId);
    }
    return groups.length ? groups : null;
}

function renderUsers(data) {
    const q = ($('#userSearch')?.value || '').trim().toLowerCase();
    const all = getItems(data);
    const items = q
        ? all.filter(u => String(u.id).includes(q) || String(u.username).toLowerCase().includes(q))
        : all;
    $('#userCount').textContent = items.length ? (items.length === all.length ? `${items.length} 个用户` : `${items.length} / ${all.length} 个用户`) : '';
    $('#userList').innerHTML = items.length ? items.map(u => {
        const initial = esc((u.username || '?').slice(0, 1).toUpperCase());
        const night = u.nightly_activity_enabled
            ? `<span class="status-line on"><span class="dot"></span>夜间已开启</span>`
            : `<span class="status-line muted"><span class="dot"></span>夜间已关闭</span>`;
        const active = u.is_active
            ? `<span class="status-line on"><span class="dot"></span>账户正常</span>`
            : `<span class="status-line off"><span class="dot"></span>账户已停用</span>`;
        const overrides = userOverrideSummary(u.id);
        const overrideCell = overrides
            ? `<div class="td-services">${overrides.map(g => pill(g, 'amber')).join('')}</div>`
            : '<span class="text-3">继承系统</span>';
        return `<tr>
            <td>
                <div class="cell-user">
                    <div class="avatar ${u.is_active ? '' : 'off'}">${initial}</div>
                    <div style="min-width:0">
                        <div class="cell-strong">${esc(u.username)}</div>
                        <div class="cell-sub">#${u.id}</div>
                    </div>
                </div>
            </td>
            <td>
                <div class="cell-stack">
                    ${active}
                    ${night}
                </div>
            </td>
            <td>${overrideCell}</td>
            <td><div class="cell-meta">${fmtDate(u.created_at)}</div></td>
            <td class="td-actions">
                <button class="btn btn--ghost btn--sm" onclick="editUser(${u.id})">编辑</button>
                <button class="btn btn--ghost btn--sm" onclick="manageActivation(${u.id})">激活码</button>
                ${menuHtml([
                    { label: overrides ? '编辑能力覆盖' : '设置能力覆盖', onclick: `openAiUserScope(${u.id})` },
                    { label: u.is_active ? '停用账户' : '启用账户', onclick: `toggleUser(${u.id})` },
                    { label: '导出备份', onclick: `exportUserPrompt(${u.id})` },
                    { label: '导入备份', onclick: `importUserPrompt(${u.id})` },
                    '|',
                    { label: '彻底删除', onclick: `deleteUser(${u.id})`, danger: true },
                ])}
            </td>
        </tr>`;
    }).join('') : stateRow(5, q ? '没有匹配的用户' : '暂无用户', q ? '换个关键词试试' : '点击右上角“创建用户”添加');
}

const DEFAULT_BASE_URL = 'http://localhost:10620';

// 每次打开弹窗时读取，系统设置中修改 public_base_url 后立即生效。
async function fetchPublicBaseUrl() {
    try {
        const data = await (await apiFetch('/api/admin/runtime-info')).json();
        return typeof data?.public_base_url === 'string' ? data.public_base_url : '';
    } catch (e) {
        toast('读取公网访问地址失败，请核对后端地址：' + e.message, 'warn');
        return '';
    }
}

function decodeActivationBaseUrl(code) {
    if (!code) return null;
    try {
        const raw = atob(code.replace(/-/g, '+').replace(/_/g, '/'));
        const data = JSON.parse(raw);
        return data?.b || null;
    } catch {
        return null;
    }
}

// baseUrl 回退顺序：激活码中的 b > public_base_url > DEFAULT_BASE_URL
async function resolveDefaultBaseUrl(existingCode) {
    return decodeActivationBaseUrl(existingCode) || (await fetchPublicBaseUrl()) || DEFAULT_BASE_URL;
}

// 所有写入激活码的操作共用：空地址提示并聚焦，返回空串表示中止。
function requireBaseUrl(input) {
    const value = input.value.trim();
    if (!value) { toast('请填写后端地址', 'err'); input.focus(); }
    return value;
}

async function showCreateUser() {
    $('#userModalTitle').textContent = '创建用户';
    $('#editUserId').value = '';
    $('#username').value = ''; $('#username').disabled = false;
    $('#baseUrl').value = await resolveDefaultBaseUrl(null);
    $('#nightlyEnabled').checked = true;
    $('#regenField').style.display = 'none';
    openModal('userModal');
    $('#username').focus();
}

function findCachedUser(id) {
    return (tabCache.users?.items ?? []).find(u => u.id === Number(id)) ?? null;
}

async function editUser(id) {
    const u = findCachedUser(id);
    if (!u) { toast('用户数据未加载，请稍后重试', 'err'); return; }
    $('#userModalTitle').textContent = '编辑用户';
    $('#editUserId').value = u.id;
    $('#username').value = u.username; $('#username').disabled = true;
    $('#baseUrl').value = await resolveDefaultBaseUrl(u.activation_code);
    $('#nightlyEnabled').checked = u.nightly_activity_enabled;
    $('#regenField').style.display = '';
    openModal('userModal');
}

async function saveUser() {
    const id = $('#editUserId').value;
    const baseUrl = requireBaseUrl($('#baseUrl'));
    if (!baseUrl) return;
    const payload = { nightly_activity_enabled: $('#nightlyEnabled').checked };
    if (!id) {
        const username = $('#username').value.trim();
        if (!username) { toast('请填写用户名', 'err'); $('#username').focus(); return; }
        payload.username = username;
    }
    payload.base_url = baseUrl;
    // 编辑时激活码只随后端地址变化；未变化时不弹出激活码。
    const previousCode = id ? findCachedUser(id)?.activation_code : null;
    try {
        const method = id ? 'PATCH' : 'POST';
        const url = id ? `/api/admin/users/${id}` : '/api/admin/users';
        const resp = await apiFetch(url, { method, body: JSON.stringify(payload) });
        const data = await resp.json();
        closeModal('userModal');
        toast(id ? '用户已更新' : '用户已创建');
        loadUsers();
        if (data.activation_code !== previousCode) showActivationCode(data.activation_code);
    } catch (e) { toast(e.message, 'err'); }
}

async function patchActivationUser(id, payload, { btn, busy, done, onCode } = {}) {
    const original = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = busy; }
    try {
        const data = await (await apiFetch(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) })).json();
        if (data.activation_code && onCode) onCode(data.activation_code);
        toast(done);
        loadUsers();
    } catch (e) { toast(e.message, 'err'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = original; } }
}

function regenerateToken() {
    const id = $('#editUserId').value;
    if (!id) return;
    const baseUrl = requireBaseUrl($('#baseUrl'));
    if (!baseUrl) return;
    patchActivationUser(id, { regenerate_token: true, base_url: baseUrl }, {
        done: '激活码已重新生成',
        onCode: showActivationCode,
    });
}

function showActivationCode(code) {
    $('#activationCodeText').value = code;
    openModal('activationModal');
}

async function copyText(value, fallbackInput) {
    if (!value) return;
    let copied;
    try {
        await navigator.clipboard.writeText(value);
        copied = true;
    } catch {
        fallbackInput.select();
        copied = document.execCommand('copy');
    }
    if (copied) toast('已复制到剪贴板');
    else toast('复制失败，请手动选中后复制', 'err');
}

async function copyActivationCode() {
    await copyText($('#activationCodeText').value, $('#activationCodeText'));
}

async function manageActivation(id) {
    const user = findCachedUser(id);
    if (!user) { toast('用户数据未加载，请稍后重试', 'err'); return; }
    $('#activationManageUserId').value = id;
    $('#activationManageTitle').textContent = '激活码管理 · ' + user.username;
    $('#activationManageCode').value = user.activation_code;
    $('#activationManageBaseUrl').value = await resolveDefaultBaseUrl(user.activation_code);
    openModal('activationManageModal');
}

function setManageCode(id, code) {
    const user = findCachedUser(id);
    if (user) user.activation_code = code;
    if ($('#activationManageUserId').value !== String(id)) return;
    $('#activationManageCode').value = code;
}

function updateBaseUrlFromManage() {
    const id = $('#activationManageUserId').value;
    if (!id) return;
    const baseUrl = requireBaseUrl($('#activationManageBaseUrl'));
    if (!baseUrl) return;
    patchActivationUser(id, { base_url: baseUrl }, {
        btn: $('#activationManageUpdateUrlBtn'), busy: '更新中…', done: '后端地址已更新，激活码已刷新',
        onCode: code => setManageCode(id, code),
    });
}

function regenerateFromManage() {
    const id = $('#activationManageUserId').value;
    if (!id) return;
    const baseUrl = requireBaseUrl($('#activationManageBaseUrl'));
    if (!baseUrl) return;
    patchActivationUser(id, { regenerate_token: true, base_url: baseUrl }, {
        btn: $('#activationManageGenBtn'), busy: '生成中…', done: '激活码已重新生成',
        onCode: code => setManageCode(id, code),
    });
}

async function copyActivationManageCode() {
    await copyText($('#activationManageCode').value, $('#activationManageCode'));
}

async function toggleUser(id) {
    try {
        await apiFetch(`/api/admin/users/${id}/toggle-active`, { method: 'PATCH' });
        toast('状态已切换');
        loadUsers();
    } catch (e) { toast(e.message, 'err'); }
}

async function deleteUser(id) {
    if (!confirm('确定彻底删除此用户及其所有关联数据？此操作不可撤销。')) return;
    try {
        await apiFetch(`/api/admin/users/${id}`, { method: 'DELETE' });
        toast('用户已删除');
        loadUsers();
    } catch (e) { toast(e.message, 'err'); }
}

// 键与 services/domains/backup/serializers.py 的 TABLES 及恢复失败分区 asset_files 对应。
const BACKUP_SECTION_LABELS = Object.freeze({
    user_preferences: '用户偏好', conversations: '会话', messages: '消息',
    user_model_configs: '模型配置与凭据', avatar_assets: '角色形象', companion_character_cards: '角色卡',
    companion_outfits: '衣柜', companion_action_packs: '动作包', companion_actions: '动作',
    companion_scenes: '场景', desktop_video_sets: '桌面生活组合', desktop_video_actions: '桌面生活动作', desktop_video_states: '桌面生活偏好', personas: '角色定义', user_settings: '用户设置',
    cron_jobs: '定时任务', companion_intents: '主动陪伴意图', memories: '长期记忆',
    companion_posts: '动态', companion_post_comments: '动态评论',
    companion_diary_entries: '日记', asset_files: '资产文件',
});
// 与 backup/serializers.py 的 BACKUP_SECTIONS 对齐；页面只负责勾选，表映射以服务端为准。
const BACKUP_SCOPE_HINTS = Object.freeze({
    all: '恢复备份中的全部数据类别。',
    identity: '恢复人设、头像与全身参考（含确认状态）、角色卡。音色需勾选设置，动作包与场景需分别恢复；缺少引导资料时继续完成对应步骤，不自动生成素材。',
    custom: '只恢复勾选的类别，不动目标已有但未勾选的数据。',
});
const BACKUP_MODE_LABELS = Object.freeze({ overwrite: '覆盖', merge: '合并' });
const BACKUP_SCOPE_LABELS = Object.freeze({
    all: '全部数据', identity: '基础身份', custom: '自定义',
    conversations: '会话记录', memories: '长期记忆', posts: '动态', diary: '日记',
    wardrobe: '衣柜与动作', scenes: '场景', desktop_life: '桌面生活', automation: '定时与意图', settings: '设置与凭据',
});
let backupImportRunning = false;

function onBackupScopeChange() {
    const scope = ($$('input[name="bk_scope"]').find(r => r.checked) || {}).value || 'all';
    $('#backupSectionBoxes').style.display = scope === 'custom' ? '' : 'none';
    $('#backupScopeHint').textContent = BACKUP_SCOPE_HINTS[scope] || BACKUP_SCOPE_HINTS.all;
}

function selectedBackupSections() {
    const scope = ($$('input[name="bk_scope"]').find(r => r.checked) || {}).value || 'all';
    if (scope === 'all') return null;
    if (scope === 'identity') return ['identity'];
    return $$('.bk_section').filter(el => el.checked).map(el => el.value);
}

function exportUserPrompt(id) {
    $('#userExportModal').dataset.userId = String(id);
    $('#exportSubmitBtn').disabled = false;
    openModal('userExportModal');
}

async function exportUser(id) {
    const user = findCachedUser(id);
    const label = user ? user.username : `#${id}`;
    const btn = $('#exportSubmitBtn');
    btn.disabled = true;
    try {
        const resp = await apiFetch(`/api/admin/users/${id}/export`);
        const blob = await resp.blob();
        let filename = `spiritagent-user-${id}-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')}.zip`;
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="?([^"]+)"?/);
        if (m) filename = m[1];
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; document.body.appendChild(a);
        a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast(`已导出用户 ${label}（全量）`);
        closeModal('userExportModal');
    } catch (e) { toast(`导出失败：${e.message || e}`, 'err'); }
    finally { btn.disabled = false; }
}

function importUserPrompt(id) {
    const modal = $('#userBackupModal');
    // 导入进行中只重新打开弹窗查看进度，不重置状态。
    if (backupImportRunning) {
        if (modal.dataset.userId !== String(id)) toast(`用户 #${modal.dataset.userId} 的备份仍在导入，请完成后再试`, 'warn');
        openModal('userBackupModal');
        return;
    }
    const user = findCachedUser(id);
    modal.dataset.userId = String(id);
    $('#backupUserId').textContent = `${id}${user ? ' (' + user.username + ')' : ''}`;
    $('#backupFile').value = '';
    $('#backupStatus').textContent = '';
    $('#backupSubmitBtn').disabled = false;
    const scopeAll = document.querySelector('input[name="bk_scope"][value="all"]');
    if (scopeAll) scopeAll.checked = true;
    onBackupScopeChange();
    openModal('userBackupModal');
}

async function submitUserBackup() {
    if (backupImportRunning) return;
    const userId = $('#userBackupModal').dataset.userId;
    const file = $('#backupFile').files[0];
    const mode = ($$('input[name="bk_mode"]').find(r => r.checked) || {}).value || 'overwrite';
    const sections = selectedBackupSections();
    const scope = ($$('input[name="bk_scope"]').find(r => r.checked) || {}).value || 'all';
    const modeLabel = (value) => BACKUP_MODE_LABELS[value] ?? value;
    const scopeLabel = BACKUP_SCOPE_LABELS[scope] || scope;
    const status = $('#backupStatus');
    const btn = $('#backupSubmitBtn');
    if (!file) { status.textContent = '请选择 .zip 文件'; status.style.color = 'var(--red)'; return; }
    if (scope === 'custom' && !(sections && sections.length)) {
        status.textContent = '请至少勾选一个恢复范围';
        status.style.color = 'var(--red)';
        return;
    }
    backupImportRunning = true;
    btn.disabled = true;
    status.textContent = `上传并导入中（范围：${scopeLabel}，模式：${modeLabel(mode)}），请稍候…`;
    status.style.color = 'var(--primary)';
    try {
        const fd = new FormData();
        fd.append('file', file);
        const qs = new URLSearchParams({ mode });
        if (sections) for (const item of sections) qs.append('sections', item);
        const resp = await apiFetch(`/api/admin/users/${userId}/import?${qs.toString()}`, {
            method: 'POST', body: fd, headers: {},
        });
        const data = await resp.json();
        const total = Object.values(data.imported || {}).reduce((a, b) => a + b, 0);
        const restoredFiles = Number(data.restored_files) || 0;
        const failures = Array.isArray(data.failed) ? data.failed : [];
        const restoredSections = Array.isArray(data.sections) && data.sections.length
            ? data.sections.map(s => BACKUP_SCOPE_LABELS[s] || s).join('、')
            : scopeLabel;
        const summary = `已导入 ${total} 行数据、恢复 ${restoredFiles} 个资产文件（范围：${restoredSections}）`;
        if (failures.length) {
            const details = failures.map(item => {
                const label = Object.prototype.hasOwnProperty.call(BACKUP_SECTION_LABELS, item.section)
                    ? BACKUP_SECTION_LABELS[item.section] : item.section;
                return `• ${label}（${Number(item.count) || 0} 项）：${item.reason}`;
            });
            status.textContent = `导入完成（尽力恢复）：${summary}\n未能导入：\n${details.join('\n')}`;
            status.style.color = 'var(--amber)';
            toast(`用户 #${userId} 已部分导入，请查看失败内容`, 'warn');
            loadUsers();
        } else {
            status.textContent = `导入完成（${modeLabel(data.mode)}）：${summary}`;
            status.style.color = 'var(--green)';
            toast(`已导入用户 #${userId} · ${scopeLabel} · ${modeLabel(data.mode)}`);
            setTimeout(() => { closeModal('userBackupModal'); loadUsers(); }, 600);
        }
    } catch (err) {
        const message = err.message || String(err);
        status.textContent = '导入失败：' + message; status.style.color = 'var(--red)';
        toast(`用户 #${userId} 导入失败：${message}`, 'err');
    } finally {
        backupImportRunning = false;
        btn.disabled = false;
    }
}

const ICON_PATHS = {
    overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    ai: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
    users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M17 5a3 3 0 0 1 0 6m1 3a5 5 0 0 1 3 5v2"/>',
    settings: '<circle cx="12" cy="12" r="4"/><path d="m9 3-1 3-3 1-2 3 2 2-1 4 3 2 3-1 2 4 3-2 1-3 4-1 1-4-3-2 1-4-4-1-2-3-3 1Z"/>',
    versions: '<path d="M4 14v6h16v-6M12 3v12m-5-7 5-5 5 5"/>',
    upload: '<path d="M4 16v5h16v-5M12 3v13M7 8l5-5 5 5"/>',
    logs: '<path d="M6 3h9l4 4v14H6V3Zm9 0v5h4M9 12h7m-7 4h5"/>',
    logout: '<path d="M9 3H4v18h5m5-15 6 6-6 6m-5-6h11"/>',
    scope: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="9" r="3"/><path d="M5 18a7 7 0 0 1 14 0"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    alert: '<path d="m12 3 10 17H2L12 3Zm0 5v5m0 3v1"/>',
    llm: '<path d="M4 4h16v12H9l-5 4V4Zm4 4h8m-8 4h5"/>',
    image_gen: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
    video_gen: '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    tts: '<path d="M5 9H2v6h3l5 5V4L5 9Zm9 0a5 5 0 0 1 0 6m3-10a10 10 0 0 1 0 14"/>',
    stt: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10a7 7 0 0 0 14 0m-7 7v5m-4 0h8"/>',
    embedding: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M7 5h10M6 7l5 10M18 7l-5 10"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    appearance: '<circle cx="12" cy="8" r="4"/><path d="M4 22v-2a8 8 0 0 1 16 0v2"/>',
    outfit: '<path d="m8 3-6 4 3 5 3-2v11h8V10l3 2 3-5-6-4a4 4 0 0 1-8 0Z"/>',
    scene: '<path d="m3 12 9-9 9 9M5 10v11h14V10m-9 11v-7h4v7"/>',
    moon: '<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z"/>',
};
const GROUP_ICONS = { '陪伴聊天': 'llm', '专业预设': 'users', '定时任务': 'clock', '主动陪伴': 'ai', '记忆': 'embedding', '夜间': 'moon', '动态': 'logs', '形象初始化': 'appearance', '换装': 'outfit', '场景': 'scene', '窗口动作': 'video_gen', '桌面生活': 'scene', '语音': 'tts' };
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name] ?? ICON_PATHS.ai}</svg>`; }
function injectIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
const READINESS_KEY_LABELS = Object.freeze({ llm: '大语言模型 LLM', stt: '语音识别', tts: '语音合成', image_gen: '图像生成', video_gen: '视频生成', embedding: '向量嵌入' });
const SERVICE_SHORT = { llm: 'LLM', stt: '转写', tts: '语音', image_gen: '图像', video_gen: '视频', embedding: '向量' };
function taskGroups() {
    const groups = new Map();
    for (const task of tabCache.models?.ai_tasks ?? []) {
        if (!groups.has(task.group)) groups.set(task.group, []);
        groups.get(task.group).push(task);
    }
    return groups;
}
function userOverrideCount(group) {
    const task = aiTaskChain(group);
    return (tabCache.models?.items ?? []).filter(item => task ? hasChainOverride(item.ai_config?.tasks?.[task.id]?.chains?.[task.service]) : item.ai_config?.capabilities?.[group]?.length).length;
}
function computeReadiness(systemConfig) {
    const result = {};
    for (const definition of tabCache.models?.ai_tasks ?? []) for (const service of definition.services) {
        const task = { ...definition, service };
        const group = `task:${task.id}:${service}`;
        const resolved = effectiveTaskChain(systemConfig, emptyAiConfig(), task);
        const status = !resolved.cards.some(card => card.available) ? 'bad' : resolved.cards.some(card => !card.available) ? 'warn' : 'ok';
        result[group] = { ...resolved, label: `${task.label} · ${READINESS_KEY_LABELS[service]}`, service, groupLabel: task.group, status };
    }
    return result;
}
function readinessIssues(readiness) {
    const issues = new Map();
    for (const [target, value] of Object.entries(readiness)) {
        if (value.status === 'ok') continue;
        const problems = value.cards.length ? value.cards.filter(card => !card.available).map(card => ({ key: `${value.service}:${card.id}:${card.issue}`, label: `${card.model_name} · ${card.issue}` })) : [{ key: value.sourceKey, label: `${READINESS_KEY_LABELS[value.service]} · ${value.source === '未配置' ? '默认链未配置' : '所选链没有兼容模型'}` }];
        for (const problem of problems) {
            if (!issues.has(problem.key)) issues.set(problem.key, { label: problem.label, target: value.cards.length ? value.sourceKey : value.sourceKey ?? target, groups: new Set() });
            issues.get(problem.key).groups.add(value.groupLabel);
        }
    }
    return [...issues.values()];
}
function updateAiNavBadge(readiness) {
    const count = readinessIssues(readiness).length;
    $('#aiNavBadge').hidden = !count;
    $('#aiNavBadge').textContent = String(count);
}
function renderOverviewGroups(readiness) {
    const groups = taskGroups();
    let complete = 0;
    $('#ovGroups').innerHTML = [...groups].map(([label, definitions]) => {
        const entries = definitions.flatMap(task => task.services.map(service => ({ target: `task:${task.id}:${service}`, task, service, readiness: readiness[`task:${task.id}:${service}`] })));
        const ready = entries.filter(item => item.readiness.status === 'ok').length;
        if (ready === entries.length) complete++;
        const tone = ready === entries.length ? '' : entries.some(item => item.readiness.status === 'bad') ? 'bad' : 'warn';
        return `<article class="function-tile"><div class="function-title">${icon(GROUP_ICONS[label])}<button data-open-ai="${entries[0].target}">${esc(label)}</button><span class="function-status ${tone}">${ready}/${entries.length}</span></div><div class="service-statuses">${entries.map(({ target, task, service, readiness: r }) => `<button class="service-status ${r.status === 'ok' ? '' : r.status}" data-open-ai="${target}" title="${esc(`${task.label} · ${READINESS_KEY_LABELS[service]}：${r.status === 'ok' ? '已配置' : r.status === 'warn' ? '需完善' : '未配置可用模型'}`)}" aria-label="${esc(`${task.label} · ${READINESS_KEY_LABELS[service]}，${r.status === 'ok' ? '已配置' : '需处理'}`)}">${icon(service)}<span>${SERVICE_SHORT[service]} ${r.status === 'ok' ? '●' : r.status === 'warn' ? '◐' : '○'}</span></button>`).join('')}</div></article>`;
    }).join('');
    $('#ovReady').innerHTML = `${complete}<small>/ ${groups.size} 组</small>`;
    $('#ovReadyMeta').textContent = `${Object.values(readiness).filter(item => item.status === 'ok').length} / ${Object.keys(readiness).length} 项能力已配置`;
    const issues = readinessIssues(readiness);
    $('#ovIssues').textContent = String(issues.length);
    const affected = new Set(issues.flatMap(issue => [...issue.groups]));
    $('#ovIssuesMeta').textContent = affected.size ? `影响 ${affected.size} 个功能组` : '配置检查通过';
    $('#ovIssueCount').textContent = issues.length ? `${issues.length} 项` : '';
    $('#ovTodo').innerHTML = issues.length ? issues.map(issue => `<div class="issue-item">${icon('alert')}<div class="issue-body"><strong>${esc(issue.label)}</strong><span class="hint" title="${esc([...issue.groups].join('、'))}">影响 ${issue.groups.size} 组 · ${esc([...issue.groups].slice(0, 3).join('、'))}${issue.groups.size > 3 ? '…' : ''}</span></div><button class="btn btn--text" data-open-ai="${esc(issue.target)}">定位 →</button></div>`).join('') : `<div class="issue-item">${icon('check')}<span class="hint">当前没有待处理的配置问题</span></div>`;
    updateAiNavBadge(readiness);
}
async function loadOverview() {
    try {
        const [models, versions, logs] = await Promise.all(['/api/admin/model-configs', '/api/update/versions', '/api/admin/nightly-activity-logs'].map(async path => (await apiFetch(path)).json()));
        tabCache.models = models;
        tabCache.versions = versions;
        renderOverviewGroups(computeReadiness(normalizeAiConfig(models.system_ai_config)));
        const active = getItems(versions).find(version => version.is_active);
        $('#ovVersion').textContent = active ? `v${active.version}` : '—';
        $('#ovVersionMeta').textContent = active ? '已上线' : '暂无上线版本';
        const overrides = models.items.filter(item => Object.values(item.ai_config?.capabilities ?? {}).some(cards => cards.length) || Object.values(item.ai_config?.tasks ?? {}).some(task => Object.values(task.chains ?? {}).some(hasChainOverride)));
        $('#ovOverrides').textContent = String(overrides.length);
        $('#ovOverridesMeta').textContent = overrides.length ? '位用户使用独立配置' : '全部继承系统配置';
        const events = getItems(logs).slice(0, 4).map(log => {
            const [label, tone] = LOG_STATUS[log.status] ?? [log.status, 'muted'];
            return { tone, title: `夜间活动 · 用户 ${log.user_id} · ${label}`, meta: `${log.target_date} · ${log.summary || fmtDate(log.updated_at || log.created_at)}` };
        });
        if (active) events.push({ tone: '', title: `版本 v${active.version} 已上线`, meta: fmtDate(active.created_at) });
        $('#ovTimeline').innerHTML = events.length ? events.map(event => `<li><div class="tl-dot ${event.tone}"></div><div class="tl-body"><div class="title">${esc(event.title)}</div><div class="meta">${esc(event.meta)}</div></div></li>`).join('') : '<li class="hint">暂无活动记录</li>';
    } catch (error) {
        $('#ovGroups').innerHTML = `<div class="state-msg">加载失败：${esc(error.message)}<button class="btn btn--text" onclick="loadOverview()">重试</button></div>`;
    }
}

// AI 编辑器：页面与分组共享作用域草稿，系统参数保持独立。
let aiView = { scope: 'system', userId: null, section: 'providers', group: 'providers', paletteOpen: true };
let aiSystemDirty = false;
let aiUserDirty = false;
let aiSaving = false;
let aiLoadRevision = 0;
let aiCacheStale = false;
let lastUserAiRaw = null;
let scopeChangeResolve = null;
function currentAiDirty() { return aiView.scope === 'system' ? aiSystemDirty : aiUserDirty; }
function aiActiveEditor() { return aiView.scope === 'system' ? aiEditors.system : aiEditors.user; }
function aiEditorName() { return aiView.scope === 'system' ? 'system' : 'user'; }
function aiScopeOptions() {
    const overridden = new Set((tabCache.models?.items ?? []).map(item => item.user_id));
    return '<option value="system">系统配置</option>' + (tabCache.users?.items ?? []).map(user => `<option value="${user.id}">${esc(user.username)} (#${user.id})${overridden.has(user.id) ? ' · 覆盖' : ''}</option>`).join('');
}
function renderAiTree() {
    const node = (key, label, glyph, sub = '') => `<button type="button" class="tree-item${(aiView.section === 'tasks' ? aiTaskChain(aiView.group)?.group === key : aiView.group === key) ? ' active' : ''}" data-ai-node="${esc(key)}">${icon(glyph)}<span>${esc(label)}</span>${sub ? `<span class="tree-sub">${esc(sub)}</span>` : ''}</button>`;
    if (aiView.section === 'tasks') $('#aiTree').innerHTML = [...taskGroups()].map(([label, tasks]) => node(label, label, GROUP_ICONS[label], String(tasks.reduce((sum, task) => sum + task.services.length, 0)))).join('');
    else if (aiView.section === 'models') $('#aiTree').innerHTML = Object.entries(READINESS_KEY_LABELS).map(([service, label]) => node(service, label, service, String(aiActiveEditor().config.capabilities[service].length))).join('');
    else $('#aiTree').innerHTML = node('providers', '供应商信息库', 'scope');
    $('#aiTree').onclick = event => {
        const button = event.target.closest('[data-ai-node]');
        if (!button) return;
        const key = button.dataset.aiNode;
        if (aiView.section === 'tasks') { const task = taskGroups().get(key)[0]; aiSelectNode(`task:${task.id}:${task.services[0]}`); }
        else aiSelectNode(key);
    };
}
function hasChainOverride(value) { return Boolean(value && (value.chain?.length)); }
function aiUsageHtml(task) {
    const usages = task.service_usage[task.service];
    return `<div class="usage-tags">${usages.slice(0, 2).map(text => `<span>${esc(text)}</span>`).join('')}${usages.length > 2 ? `<details><summary>+${usages.length - 2} 用途</summary><ul>${usages.map(text => `<li>${esc(text)}</li>`).join('')}</ul></details>` : ''}</div>`;
}
function chainNodeHtml(card, index, length) {
    return `<article class="chain-node" data-ai-index="${index}" draggable="true" title="${esc(card.model_name)}"><div class="node-top"><span class="node-order">${String(index + 1).padStart(2, '0')}</span><span>${index ? '后备模型' : '首选模型'}</span><span class="spacer"></span><span>${esc(card.scope || '')}</span></div><strong class="node-name">${esc(card.model_name)}</strong><div class="node-provider">${esc(card.provider_name)}</div>${card.issue ? `<div class="node-issue">${esc(card.issue)}</div>` : ''}<div class="node-actions"><button class="btn btn--ghost" data-ai-action="up" title="上移" aria-label="上移 ${esc(card.model_name)}"${index ? '' : ' disabled'}>←</button><button class="btn btn--ghost" data-ai-action="down" title="下移" aria-label="下移 ${esc(card.model_name)}"${index < length - 1 ? '' : ' disabled'}>→</button><button class="btn btn--text" data-ai-action="model-config" data-ai-model-id="${esc(card.id)}">详情</button><button class="btn btn--text" data-ai-action="remove" title="移除引用" aria-label="移除 ${esc(card.model_name)}">×</button></div></article>`;
}
function abilityCardHtml(definition, service, multiTask) {
    const task = { ...definition, service };
    const editor = aiActiveEditor();
    const key = `task:${task.id}:${service}`;
    const policy = editor.config.tasks[task.id]?.chains?.[service] ?? {};
    const system = aiEditorName() === 'system' ? editor.config : aiSystemConfig(editor);
    const resolved = effectiveTaskChain(system, aiEditorName() === 'user' ? editor.config : emptyAiConfig(), task);
    const ownChain = policy.chain ?? [];
    const models = aiModels(editor, service);
    const cards = ownChain.map(id => resolved.cards.find(card => card.id === id) ?? models.find(card => card.id === id) ?? { id, model_name: '模型引用失效', provider_name: '模型不存在', issue: '模型引用失效' });
    const sourceLabel = resolved.source.includes('默认') ? `${resolved.source.startsWith('用户') ? '用户' : '系统'}默认 · ${READINESS_KEY_LABELS[service]}` : `${resolved.source.startsWith('用户') ? '用户' : '系统'}任务 · ${task.label} · ${SERVICE_SHORT[service]}`;
    const chain = cards.length ? cards.map((card, index) => chainNodeHtml(card, index, cards.length)).join('') : `<div class="chain-placeholder"><div>${icon(service)} <span>${resolved.source === '未配置' ? '默认链未配置' : `继承${esc(sourceLabel)}`}</span>${resolved.source !== '未配置' && !resolved.cards.some(card => card.available) ? '<p class="hint">所选链没有可用模型</p>' : ''}</div><button class="btn btn--text" data-ai-source="${esc(resolved.sourceKey)}">查看来源 →</button></div>`;
    return `<article class="ability-card${aiView.group === key ? ' selected' : ''}" data-ai-group="${key}" tabindex="-1"><div class="ability-heading">${icon(service)}<h3>${esc(multiTask ? task.label : READINESS_KEY_LABELS[service])}</h3>${multiTask ? `<span class="hint">${READINESS_KEY_LABELS[service]}</span>` : ''}<span class="chain-state pill ${cards.length && !resolved.cards.some(card => card.available) ? 'pill--red' : 'pill--muted'}">${cards.length ? '独立配置' : '默认继承'}</span></div>${aiUsageHtml(task)}<div class="chain-track">${chain}</div><div class="chain-footer"><button class="btn btn--text" data-ai-choose="${key}">＋ 添加模型</button></div></article>`;
}
function renderAiChain() {
    const editor = aiActiveEditor();
    const editorName = aiEditorName();
    const selectedTask = aiTaskChain(aiView.group);
    if (aiView.section === 'tasks' && !selectedTask) {
        const first = (tabCache.models?.ai_tasks ?? [])[0];
        if (!first) return;
        aiView.group = `task:${first.id}:${first.services[0]}`;
        return renderAiScope();
    }
    if (aiView.section === 'tasks') {
        const tasks = taskGroups().get(selectedTask.group);
        $('#aiChainTitle').textContent = selectedTask.group;
        $('#aiChainCount').textContent = `${tasks.reduce((sum, task) => sum + task.services.length, 0)} 项能力 · ${aiView.scope === 'system' ? '系统配置' : `用户 #${aiView.userId}`}`;
        $('#aiChainBody').innerHTML = tasks.flatMap(task => task.services.map(service => abilityCardHtml(task, service, tasks.length > 1))).join('');
    } else {
        const group = aiView.section === 'providers' ? 'providers' : aiView.group;
        $('#aiChainTitle').textContent = aiGroupLabel(group);
        const inherited = aiView.scope !== 'system' && !aiList(editor, group).length;
        $('#aiChainCount').textContent = group === 'providers' ? `${editor.config.providers.length} 条供应商` : '拖动模型调整默认调用顺序';
        $('#aiChainBody').innerHTML = (inherited ? `<p class="layer-hint">${group === 'providers' ? '模型可引用系统供应商，也可添加用户供应商。' : `继承系统默认 · ${READINESS_KEY_LABELS[group]}`}</p>` : '') + callListHtml(editorName, group);
    }
    $('#aiDeleteBtn').hidden = aiView.scope === 'system';
    const paletteVisible = aiView.section === 'tasks' && aiView.paletteOpen;
    $('#aiPalette').hidden = !paletteVisible;
    $('.ai-layout').classList.toggle('without-palette', !paletteVisible);
    $('#aiPaletteToggle').hidden = aiView.section !== 'tasks';
    $('#aiPaletteToggle').setAttribute('aria-expanded', String(paletteVisible));
    renderAiPalette();
    bindAiCardEvents();
    updateSaveActions();
}
function renderAiPalette() {
    if (aiView.section !== 'tasks') return;
    const task = aiTaskChain(aiView.group);
    if (!task) return;
    const search = $('#aiPaletteSearch').value.trim().toLowerCase();
    const cards = aiModels(aiActiveEditor(), task.service).filter(card => aiModelSupportsTask(card, task) && `${card.model_name} ${card.provider_name}`.toLowerCase().includes(search));
    const existing = aiActiveEditor().config.tasks[task.id]?.chains?.[task.service]?.chain ?? [];
    $('#aiPaletteCount').textContent = `${SERVICE_SHORT[task.service]} · ${cards.length}`;
    $('#aiPaletteList').innerHTML = cards.length ? cards.map(card => `<article class="palette-model" draggable="true" data-palette-model="${card.id}" data-palette-service="${task.service}" title="${esc(card.model_name)}"><div><strong>${esc(card.model_name)}</strong><span class="hint">${esc(card.scope)} · ${esc(card.provider_name)}</span></div><button class="btn btn--ghost" data-palette-add="${card.id}" aria-label="添加 ${esc(card.model_name)}"${existing.includes(card.id) ? ' disabled' : ''}>${existing.includes(card.id) ? '✓' : '+'}</button></article>`).join('') : `<p class="hint">没有兼容模型。可在能力模型中添加。</p>`;
}
function aiSelectNode(group) {
    if (group === 'providers') aiView.section = 'providers';
    else if (aiTaskChain(group)) aiView.section = 'tasks';
    else if (Object.hasOwn(READINESS_KEY_LABELS, group)) aiView.section = 'models';
    else return;
    aiView.group = group;
    renderAiScope();
}
function renderAiScope() {
    $$('#aiSections button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.aiSection === aiView.section)));
    renderAiTree();
    renderAiChain();
}
function toggleAiPalette() { aiView.paletteOpen = !aiView.paletteOpen; renderAiChain(); }
function selectAiAbility(group) {
    if (!aiTaskChain(group)) return;
    aiView.group = group;
    $$('#aiChainBody .ability-card').forEach(card => card.classList.toggle('selected', card.dataset.aiGroup === group));
    renderAiPalette();
}
async function onAiScopeChange() { const value = $('#aiScopeSelect').value; $('#aiScopeSelect').value = aiView.scope; await changeAiScope(value); }
async function changeAiScope(value) {
    if (value === aiView.scope) return true;
    if (aiSaving) return false;
    if (currentAiDirty()) {
        const choice = await new Promise(resolve => { scopeChangeResolve = resolve; openModal('scopeChangeModal'); });
        if (choice === 'cancel') return false;
        if (choice === 'save' && !await saveAiScope()) return false;
        if (choice === 'discard') discardAiEdits();
    }
    resetAiDiscovery();
    aiView.scope = String(value);
    aiView.userId = value === 'system' ? null : Number(value);
    if (value !== 'system') showModelConfigForUserSilent(aiView.userId);
    $('#aiScopeSelect').value = aiView.scope;
    renderAiScope();
    return true;
}
async function openAiGroup(group) {
    showTab('ai');
    await loadAiView();
    if (!await changeAiScope('system')) return;
    if (group.startsWith('system:') || group.startsWith('user:')) await navigateAiSource(group);
    else aiSelectNode(group);
    $$('#aiChainBody [data-ai-group]').find(card => card.dataset.aiGroup === group)?.focus();
}
async function loadAiView() {
    const revision = ++aiLoadRevision;
    try {
        const [models, users] = await Promise.all([
            !aiCacheStale && tabCache.models ? tabCache.models : apiFetch('/api/admin/model-configs').then(response => response.json()),
            tabCache.users ?? apiFetch('/api/admin/users').then(response => response.json()),
        ]);
        if (revision !== aiLoadRevision) return;
        tabCache.models = models; tabCache.users = users;
        aiCacheStale = false;
        aiEditors.system.support = models.ai_provider_support;
        aiEditors.system.defaults = models.ai_provider_defaults;
        if (!aiSettingsLoaded || !aiSystemDirty) {
            lastSystemAiRaw = structuredClone(models.system_ai_config);
            aiEditors.system.config = normalizeAiConfig(lastSystemAiRaw);
            aiSettingsLoaded = true;
        }
        if (aiView.scope !== 'system' && !aiUserDirty) showModelConfigForUserSilent(aiView.userId);
        $('#aiScopeSelect').innerHTML = aiScopeOptions();
        $('#aiScopeSelect').value = aiView.scope;
        renderAiScope();
    } catch (error) { $('#aiChainBody').innerHTML = `<div class="state-msg">加载失败：${esc(error.message)}<button class="btn btn--text" onclick="loadAiView()">重试</button></div>`; }
}
function showModelConfigForUserSilent(userId) {
    lastUserAiRaw = structuredClone(tabCache.models?.items?.find(item => item.user_id === userId)?.ai_config ?? emptyAiConfig());
    aiEditors.user.config = normalizeAiConfig(lastUserAiRaw);
    aiEditors.user.support = tabCache.models?.ai_provider_support ?? {};
    aiEditors.user.defaults = tabCache.models?.ai_provider_defaults ?? {};
}
function savedAiConfig(raw) {
    const result = structuredClone(raw);
    for (const provider of result.providers) {
        provider.api_key_set = provider.clear_api_key ? false : Boolean(provider.api_key || provider.api_key_set);
        provider.api_key = '';
        delete provider.clear_api_key;
    }
    return result;
}
async function saveAiScope() {
    if (aiSaving || !aiSettingsLoaded || !validateAiCards(aiEditorName())) return false;
    const scope = aiView.scope;
    const payload = structuredClone(aiActiveEditor().config);
    aiSaving = true;
    $('#panel-ai').inert = true;
    $('#modelDetailBody').inert = true;
    updateSaveActions();
    try {
        await apiFetch(scope === 'system' ? '/api/admin/system-settings' : `/api/admin/${aiView.userId}/model-config`, { method: 'PUT', body: JSON.stringify({ ai_config: payload }) });
        const saved = savedAiConfig(payload);
        if (scope === 'system') { lastSystemAiRaw = saved; aiEditors.system.config = normalizeAiConfig(saved); }
        else { lastUserAiRaw = saved; aiEditors.user.config = normalizeAiConfig(saved); }
        clearAiDirty();
        toast('AI 配置已保存并实时生效');
        await refreshAiCache();
        renderAiScope();
        loadOverviewIfActive();
        return true;
    } catch (error) { toast('保存失败：' + error.message, 'err'); return false; }
    finally { aiSaving = false; $('#panel-ai').inert = false; $('#modelDetailBody').inert = false; updateSaveActions(); }
}
async function refreshAiCache() {
    resetAiDiscovery();
    try {
        const models = await (await apiFetch('/api/admin/model-configs')).json();
        tabCache.models = models;
        aiCacheStale = false;
        if (!aiSystemDirty) { lastSystemAiRaw = structuredClone(models.system_ai_config); aiEditors.system.config = normalizeAiConfig(lastSystemAiRaw); }
        if (aiView.scope !== 'system' && !aiUserDirty) showModelConfigForUserSilent(aiView.userId);
        $('#aiScopeSelect').innerHTML = aiScopeOptions();
        $('#aiScopeSelect').value = aiView.scope;
        return true;
    } catch { aiCacheStale = true; toast('列表刷新失败，当前草稿已保留', 'err'); return false; }
}
function markAiDirty(editorName) { if (editorName === 'system') aiSystemDirty = true; else aiUserDirty = true; updateSaveActions(); }
function clearAiDirty() { if (aiView.scope === 'system') aiSystemDirty = false; else aiUserDirty = false; updateSaveActions(); }
function discardAiEdits() {
    resetAiDiscovery();
    aiActiveEditor().config = normalizeAiConfig(aiView.scope === 'system' ? lastSystemAiRaw : lastUserAiRaw);
    clearAiDirty(); renderAiScope();
}
function loadOverviewIfActive() { if ($('#panel-overview').classList.contains('active')) void loadOverview(); }
async function deleteModelConfig(userId) {
    if (!confirm('确定删除此用户的全部覆盖配置？删除后该用户继承系统配置。')) return;
    try { await apiFetch(`/api/admin/${userId}/model-config`, { method: 'DELETE' }); tabCache.models.items = tabCache.models.items.filter(item => item.user_id !== userId); clearAiDirty(); await refreshAiCache(); showModelConfigForUserSilent(userId); renderAiScope(); toast('用户覆盖配置已清除'); }
    catch (error) { toast(error.message, 'err'); }
}
async function openAiUserScope(userId) { showTab('ai'); await loadAiView(); await changeAiScope(String(userId)); }
function addAiReference(group, id, position = null) {
    const task = aiTaskChain(group);
    const editor = aiActiveEditor();
    const card = aiModels(editor, task?.service).find(model => model.id === id);
    if (!task || !card || !aiModelSupportsTask(card, task)) return false;
    const chain = aiTaskPolicy(editor, task).chain;
    if (chain.includes(id)) { toast('该模型已在调用链中', 'err'); return false; }
    if (task.service === 'embedding') chain.splice(0, chain.length, id);
    else chain.splice(position == null ? chain.length : position, 0, id);
    markAiDirty(aiEditorName()); renderAiScope();
    focusAiModel(group, id);
    return true;
}
function focusAiModel(group, id) {
    const values = aiList(aiActiveEditor(), group);
    const index = values.findIndex(value => (typeof value === 'string' ? value : value.id) === id);
    const section = $$('#aiChainBody [data-ai-group]').find(element => element.dataset.aiGroup === group);
    const item = index >= 0 ? section?.querySelector(`[data-ai-index="${index}"]`) : null;
    if (item) { item.tabIndex = -1; item.focus({ preventScroll: true }); }
    else section?.querySelector('button:not(:disabled)')?.focus();
}
function updateSaveActions() {
    const current = $('.panel.active')?.id;
    const ai = current === 'panel-ai';
    const settings = current === 'panel-settings';
    $('#pageSaveActions').hidden = !ai && !settings;
    const dirty = ai ? currentAiDirty() : settingsDirty;
    const saving = ai ? aiSaving : settingsSaving;
    const loaded = ai ? aiSettingsLoaded : settingsLoaded;
    $('#pageDraftStatus').textContent = !loaded ? '正在加载配置…' : saving ? '正在保存…' : dirty ? '有未保存的修改' : '已保存';
    $('#pageDraftStatus').classList.toggle('dirty', dirty);
    $('#pageSaveBtn').disabled = !loaded || saving || !dirty;
    $('#pageDiscardBtn').disabled = !loaded || saving || !dirty;
    $('#pageSaveBtn').textContent = saving ? '保存中…' : ai ? '保存配置' : '保存参数';
    $('#aiDraftDot').hidden = !aiSystemDirty && !aiUserDirty;
    $('#settingsDraftDot').hidden = !settingsDirty;
}
function saveCurrentPage() { return $('#panel-ai').classList.contains('active') ? saveAiScope() : saveSystemSettingsForm(); }
function discardCurrentPage() { if ($('#panel-ai').classList.contains('active')) discardAiEdits(); else if (lastSettingsRaw) renderSystemSettings(lastSettingsRaw); }


const AI_NAMES = { deepseek: 'DeepSeek', mimo: '小米 MiMo', minimax: 'MiniMax', gemini: 'Google Gemini', grok: 'xAI Grok', qwen: '千问', local: '本地 Local', custom: '自定义' };
const AI_SERVICES = { llm: '大语言模型 LLM', stt: '语音识别 STT', tts: '语音合成 TTS', image_gen: '图像生成', video_gen: '视频生成', embedding: '向量嵌入 Embedding' };
function emptyAiConfig() { return { providers: [], capabilities: { llm: [], stt: [], tts: [], image_gen: [], video_gen: [], embedding: [] }, tasks: {} }; }
function normalizeAiConfig(raw) {
    const config = structuredClone(raw ?? emptyAiConfig());
    config.providers = config.providers || [];
    config.capabilities = config.capabilities || {};
    config.tasks = config.tasks || {};
    for (const key of Object.keys(AI_SERVICES)) config.capabilities[key] = config.capabilities[key] || [];
    return config;
}
const aiEditors = {
    system: { config: emptyAiConfig(), support: {}, defaults: {} },
    user: { config: emptyAiConfig(), support: {}, defaults: {} },
};
let lastSystemAiRaw = null;
let aiDrag = null;
// 点过“清除”的卡片：API Key 留空保持清除，填写后以新密钥为准；卡片对象原样提交（接口禁止额外字段），故不把标记写进卡片。
const clearedKeyCards = new WeakSet();
let aiModelDiscovery = new WeakMap();
const aiProviderDiscovery = new Map();
let aiModelPicker = null;
const AI_MODALITIES = { text: '文字', image: '图片', audio: '音频', video: '视频', embeddings: '向量' };
const AI_OUTPUTS = { llm: 'text', stt: 'text', tts: 'audio', image_gen: 'image', video_gen: 'video', embedding: 'embeddings' };
let aiSettingsLoaded = false;
let settingsDirty = false;
let settingsLoaded = false;
let settingsSaving = false;
let lastSettingsRaw = null;

function aiList(editor, group) {
    const task = aiTaskChain(group);
    if (task) return aiTaskPolicy(editor, task).chain;
    return group === 'providers' ? editor.config.providers : editor.config.capabilities[group];
}

function aiTaskChain(group) {
    const [, taskId, service] = group.split(':');
    const task = (tabCache.models?.ai_tasks ?? []).find(task => group.startsWith('task:') && task.id === taskId && task.services.includes(service));
    return task ? { ...task, service } : null;
}

function aiTaskPolicy(editor, task) {
    const value = editor.config.tasks[task.id] ??= { chains: {} };
    return value.chains[task.service] ??= { chain: [] };
}

function aiService(group) {
    return aiTaskChain(group)?.service ?? group;
}

function aiGroupLabel(group) {
    const task = aiTaskChain(group);
    return task ? task.label + ' · ' + READINESS_KEY_LABELS[task.service] : READINESS_KEY_LABELS[group] ?? '供应商信息库';
}

function aiSystemConfig(editor) {
    return editor === aiEditors.system ? emptyAiConfig() : normalizeAiConfig(tabCache.models?.system_ai_config);
}

function modelEntries(config, service, scope, system = null) {
    const defaults = tabCache.models?.ai_provider_defaults ?? aiEditors.system.defaults;
    return (config.capabilities[service] ?? []).map(card => {
        const provider = config.providers.find(item => item.id === card.provider_id) ?? system?.providers.find(item => item.id === card.provider_id);
        const settings = defaults?.[service]?.[provider?.provider_type];
        const keySet = Boolean(provider?.api_key || (provider?.api_key_set && !provider?.clear_api_key));
        const issue = !provider ? '供应商引用失效' : !settings ? '供应商不支持此能力' : !card.model_name?.trim() ? '未填写模型名称' : service === 'llm' && (!parseAiContextWindow(card.context_window) || !parseAiContextWindow(card.max_output_tokens)) ? '缺窗口或输出上限' : !(provider.base_url || settings.base_url) ? '缺 Base URL' : settings.requires_api_key && !keySet ? '缺 API Key' : '';
        return { ...card, service, scope, provider: provider?.provider_type, provider_name: provider?.name ?? '供应商不存在', available: !issue, issue };
    });
}

function aiModels(editor, service) {
    const isSystem = editor === aiEditors.system;
    const system = aiSystemConfig(editor);
    return [...modelEntries(editor.config, service, isSystem ? '系统' : '用户', isSystem ? null : system), ...(isSystem ? [] : modelEntries(system, service, '系统'))];
}

function aiModelSupportsTask(card, task) {
    const supported = tabCache.models?.ai_provider_support?.[task.service] ?? [];
    if (!supported.includes(card.provider)) return false;
    const settings = (tabCache.models?.ai_provider_defaults ?? aiEditors.system.defaults)?.[task.service]?.[card.provider];
    const required = task.service === 'stt' ? ['audio'] : ['text'];
    if (!required.every(type => (card.input_modalities ?? []).includes(type) && (settings?.input_modalities ?? []).includes(type))) return false;
    if (!(card.output_modalities ?? []).includes(AI_OUTPUTS[task.service])) return false;
    return task.id !== 'voice_design' || (settings?.supports_voice_design && (!settings.voice_design_model || card.model_name === settings.voice_design_model));
}

function effectiveTaskChain(system, user, task) {
    const layers = [
        { values: user.tasks[task.id]?.chains?.[task.service]?.chain, owner: user, scope: '用户', refs: true, source: '用户任务链' },
        { values: system.tasks[task.id]?.chains?.[task.service]?.chain, owner: system, scope: '系统', refs: true, source: '系统任务链' },
        { values: user.capabilities[task.service], owner: user, scope: '用户', source: '用户默认能力链' },
        { values: system.capabilities[task.service], owner: system, scope: '系统', source: '系统默认能力链' },
    ];
    const layer = layers.find(item => item.values?.length);
    if (!layer) return { cards: [], source: '未配置', sourceKey: `system:${task.service}` };
    let values = task.service === 'embedding' ? layer.values.slice(0, 1) : layer.values;
    const catalog = [...modelEntries(layer.owner, task.service, layer.scope, layer.owner === user ? system : null), ...(layer.owner === user ? modelEntries(system, task.service, '系统') : [])];
    values = values.map(value => catalog.find(card => card.id === (layer.refs ? value : value.id)) ?? { id: value, model_name: '模型引用失效', provider_name: '模型不存在', available: false, issue: '模型引用失效' });
    const cards = layer.refs ? values.map(card => card.provider && !aiModelSupportsTask(card, task) ? { ...card, available: false, issue: '模型不支持此任务能力' } : card) : values.filter(card => !card.provider || aiModelSupportsTask(card, task));
    const sourceKey = `${layer.owner === user ? 'user' : 'system'}:${layer.refs ? `task:${task.id}:` : ''}${task.service}`;
    return { cards, source: layer.source, sourceKey };
}

function aiProviderLabel(provider) {
    return AI_NAMES[provider] ? `${AI_NAMES[provider]} (${provider})` : provider;
}

function parseAiContextWindow(raw) {
    const match = String(raw ?? '').trim().match(/^(\d+)(?:\.(\d+))?([km])?$/i);
    if (!match || (match[2] && !match[3])) return null;
    const scale = match[3]?.toLowerCase() === 'm' ? 6 : match[3] ? 3 : 0;
    const fraction = match[2] || '';
    if (/[1-9]/.test(fraction.slice(scale))) return null;
    // 拼出整数后再转换，避免小数乘法在大窗口值上丢失精度。
    const value = Number(match[1] + fraction.slice(0, scale).padEnd(scale, '0'));
    return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function validateAiCards(editorName) {
    const editor = aiEditors[editorName];
    const ids = new Set();
    const system = aiSystemConfig(editor);
    const inheritedIds = new Set([...system.providers, ...Object.values(system.capabilities).flat()].map(card => card.id));
    const validId = id => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) && !ids.has(id) && !inheritedIds.has(id);
    const fail = message => { toast(message, 'err'); return false; };
    for (const provider of editor.config.providers) {
        if (!validId(provider.id)) return fail('供应商 ID 无效或重复，请重新加载');
        ids.add(provider.id);
        if (!provider.name?.trim()) return fail('请填写供应商名称');
        if (!Object.values(editor.support).flat().includes(provider.provider_type)) return fail('供应商类型未注册');
    }
    const windows = [];
    for (const service of Object.keys(AI_SERVICES)) {
        for (const card of editor.config.capabilities[service]) {
            if (!validId(card.id)) return fail('模型卡片 ID 无效或重复，请重新加载');
            ids.add(card.id);
            const provider = aiSource(editor, card.provider_id);
            if (!provider || !(editor.support[service] ?? []).includes(provider.provider_type)) return fail(aiGroupLabel(service) + '：请选择支持此能力的已有供应商');
            if (!card.model_name?.trim()) return fail(aiGroupLabel(service) + '：请填写具体模型名称');
            if (service === 'image_gen' && provider.provider_type === 'local' && card.model_name !== 'qwen') return fail('本地生图使用固定 Qwen 工作流');
            if (service === 'llm') {
                const window = parseAiContextWindow(card.context_window);
                if (window === null) return fail('请填写有效的上下文窗口，例如 32k、1M 或 32768');
                const output = parseAiContextWindow(card.max_output_tokens);
                if (!output || output > window) return fail('输出 Token 上限须为正整数，且不能超过上下文窗口');
                card.max_output_tokens = output;
                windows.push([card, window]);
            }
        }
    }
    for (const [taskId, value] of Object.entries(editor.config.tasks)) {
        if (!(tabCache.models?.ai_tasks ?? []).some(task => task.id === taskId)) return fail('存在未知的任务配置，请重新加载');
        for (const [service, policy] of Object.entries(value.chains ?? {})) {
            const task = aiTaskChain(`task:${taskId}:${service}`);
            if (!task) return fail('任务包含不支持的能力，请重新加载');
            const chain = policy.chain ?? [];
            const models = aiModels(editor, service);
            if (new Set(chain).size !== chain.length) return fail(task.label + '：不能重复引用同一模型卡片');
            if (service === 'embedding' && chain.length > 1) return fail('记忆向量只能选择一张模型卡片');
            if (chain.some(id => !models.some(card => card.id === id && aiModelSupportsTask(card, task)))) return fail(task.label + '：请引用支持此能力的已有模型卡片');
        }
    }
    for (const [card, window] of windows) card.context_window = window;
    return true;
}

function aiSource(editor, providerId) {
    return editor.config.providers.find(card => card.id === providerId) ?? aiSystemConfig(editor).providers.find(card => card.id === providerId);
}

function aiAvailableProviderTypes(editor) {
    const providers = [
        ...(editor === aiEditors.system ? editor.config.providers : tabCache.models?.system_ai_config?.providers ?? []),
        ...(tabCache.models?.items ?? [])
            .filter(item => editor !== aiEditors.user || item.user_id != aiView.userId)
            .flatMap(item => item.ai_config?.providers ?? []),
        ...(editor === aiEditors.user ? editor.config.providers : []),
    ];
    const used = new Set(providers.map(card => card.provider_type));
    return [...new Set(Object.values(editor.support).flat())].filter(type => type === 'local' || type === 'custom' || !used.has(type));
}

function aiProviderOptions(editor, service) {
    const supported = editor.support[service] ?? [];
    return [...editor.config.providers.map(card => ({ ...card, scope: editor === aiEditors.system ? '系统' : '用户' })), ...aiSystemConfig(editor).providers.map(card => ({ ...card, scope: '系统' }))].filter(card => supported.includes(card.provider_type));
}

function aiModelLabel(card) {
    return card.scope + ' · ' + card.provider_name + ' · ' + card.model_name + (card.context_window ? ' · 窗 ' + card.context_window : '') + (card.issue ? ' · ' + card.issue : '');
}

function aiRemovalReferences(editor, group, id) {
    const scopes = [{ config: editor.config, label: editor === aiEditors.system ? '系统' : '当前用户' }];
    if (editor === aiEditors.system) scopes.push(...(tabCache.models?.items ?? []).map(item => ({ config: normalizeAiConfig(item.ai_config), label: '用户 #' + item.user_id })));
    const references = [];
    for (const { config, label } of scopes) {
        if (group === 'providers') {
            for (const [service, cards] of Object.entries(config.capabilities)) {
                for (const card of cards) if (card.provider_id === id) references.push(label + ' · ' + AI_SERVICES[service] + ' · ' + card.model_name);
            }
        } else {
            for (const [taskId, task] of Object.entries(config.tasks)) {
                for (const [service, policy] of Object.entries(task.chains ?? {})) if (policy.chain?.includes(id)) references.push(label + ' · ' + aiGroupLabel(`task:${taskId}:${service}`));
            }
        }
    }
    return references;
}

function callListHtml(editorName, group) {
    const editor = aiEditors[editorName];
    const library = group === 'providers';
    const cards = aiList(editor, group);
    const items = cards.map((card, index) => {
        const provider = library ? card : aiSource(editor, card.provider_id);
        const summary = library ? card.name : `${provider?.name ?? '供应商引用失效'} · ${card.model_name}${group === 'llm' ? ` · ${card.context_window ?? '未填写'} tokens` : ''}`;
        const keySet = library && Boolean(card.api_key || (card.api_key_set && !card.clear_api_key));
        const body = library ? `<div class="field"><label>供应商名称<input data-ai-field="name" maxlength="128" value="${esc(card.name)}" required></label></div><div class="field"><label>API Key <span class="keybadge ${keySet ? 'on' : 'off'}">${keySet ? '已设置' : '未设置'}</span><input type="password" autocomplete="new-password" data-ai-field="api_key" value="${esc(card.api_key)}" placeholder="${keySet ? '留空保持已保存密钥' : '填写 API Key'}"></label><button class="btn btn--text" data-ai-action="clear-key">清除密钥</button></div><div class="field"><label>Base URL<input data-ai-field="base_url" value="${esc(card.base_url)}" placeholder="${card.provider_type === 'custom' ? '如 https://host/v1' : '留空使用适配器默认地址'}"></label></div>` : '';
        return `<article class="call-item" data-ai-index="${index}"><div class="call-item-head"><button class="btn btn--ghost" draggable="true" data-ai-handle aria-label="拖拽排序 ${esc(summary)}" title="拖拽排序">⠿</button><span class="num">${String(index + 1).padStart(2, '0')}</span><span class="prov">${esc(summary)}</span>${library ? `<span class="keybadge ${keySet ? 'on' : 'off'}">${keySet ? '密钥已设置' : '未设置密钥'}</span>` : aiModelNeedsConfiguration(card, group) ? '<span class="keybadge off">待补全</span>' : ''}<button class="btn btn--ghost" data-ai-action="up" aria-label="上移 ${esc(card.model_name ?? card.name)}" title="上移"${index ? '' : ' disabled'}>↑</button><button class="btn btn--ghost" data-ai-action="down" aria-label="下移 ${esc(card.model_name ?? card.name)}" title="下移"${index < cards.length - 1 ? '' : ' disabled'}>↓</button><button class="btn btn--text" data-ai-action="remove" aria-label="移除 ${esc(card.model_name ?? card.name)}" title="移除">×</button>${library ? '<button class="btn btn--ghost" data-ai-action="toggle">配置</button>' : `<button class="btn btn--ghost" data-ai-action="model-config" data-ai-model-id="${card.id}">详情</button>`}</div>${body ? `<div class="call-item-body" hidden>${body}</div>` : ''}</article>`;
    }).join('');
    const options = library ? aiAvailableProviderTypes(editor).map(type => `<option value="${esc(type)}">${esc(aiProviderLabel(type))}</option>`).join('') : '';
    const hint = library ? '添加供应商后，可在能力模型中选择。' : editorName === 'user' ? '暂无用户模型，沿用系统默认顺序。' : '先选择供应商，再添加能力模型。';
    return `<div data-ai-group="${group}"><div class="call-list">${items}</div>${cards.length ? '' : `<p class="layer-hint">${hint}</p>`}<div class="layer-actions">${library ? `<select data-ai-add-source aria-label="选择供应商类型">${options || '<option value="">暂无可添加的供应商</option>'}</select><input data-ai-provider-name placeholder="供应商名称" maxlength="128" aria-label="新供应商名称">` : ''}<button class="btn btn--primary btn--sm" data-ai-action="add"${library && !options ? ' disabled' : ''}>＋ ${library ? '添加供应商' : '添加模型'}</button></div>${group === 'embedding' ? '<p class="layer-hint">向量默认仅使用首个模型，失败时使用关键词召回。</p>' : ''}</div>`;
}

function newAiId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
}

function newAiModel(providerId, service, providerType) {
    return { id: newAiId(), provider_id: providerId, model_name: service === 'image_gen' && providerType === 'local' ? 'qwen' : '',
        input_modalities: [service === 'stt' ? 'audio' : 'text'], output_modalities: [AI_OUTPUTS[service]],
        ...(service === 'llm' ? { context_window: '', max_output_tokens: '' } : {}) };
}

function aiModelNeedsConfiguration(card, service) {
    const window = parseAiContextWindow(card.context_window);
    const output = parseAiContextWindow(card.max_output_tokens);
    return !card.model_name?.trim() || (service === 'llm' && (!window || !output || output > window));
}

function aiTokenField(card, field, label, choices, required = false) {
    const listId = 'tokens-' + card.id + '-' + field;
    return `<div class="field"><label>${esc(label)}<input type="text" data-ai-field="${field}" list="${listId}" value="${esc(card[field] ?? '')}" placeholder="选择常用值或自行填写"${required ? ' required' : ''}></label>
        <datalist id="${listId}">${choices.map(value => `<option value="${value}">${value.toLocaleString()}</option>`).join('')}</datalist></div>`;
}

function aiModelFields(card, service, settings, localImage) {
    const state = aiModelDiscoveryState(card, service);
    const choices = aiCatalogModels(state.catalog, service);
    const selected = state.metadata;
    const candidates = state.candidates?.[card.model_name] ?? [];
    const types = (field, title) => `<div class="field"><span>${title}</span><div>${Object.entries(AI_MODALITIES).filter(([type]) => type !== 'embeddings' || field === 'output_modalities').map(([type, label]) => `<label style="display:inline-flex;gap:.35rem;margin-right:1rem"><input type="checkbox" data-ai-modality="${field}" value="${type}"${(card[field] ?? []).includes(type) ? ' checked' : ''}>${label}</label>`).join('')}</div></div>`;
    return `<div class="field"><label>模型名称<input type="text" data-ai-field="model_name" list="models-${card.id}" value="${esc(card.model_name)}" placeholder="搜索列表或填写供应商模型 ID" required${localImage ? ' readonly' : ''}></label>
        <datalist id="models-${card.id}">${choices.map(model => `<option value="${esc(model.model_name)}">${esc(model.display_name || model.model_name)}</option>`).join('')}</datalist>
        <div class="layer-actions"><button class="btn btn--ghost" data-ai-action="model-info"${state.loading || !card.model_name ? ' disabled' : ''}>获取模型信息</button></div>
        ${state.loading ? '<p class="hint">正在获取模型信息…</p>' : ''}
        ${(state.warnings ?? []).map(message => `<p class="hint">${esc(message)}</p>`).join('')}
        ${candidates.length ? `<label>请选择对应的 OpenRouter 模型<select data-ai-catalog-choice><option value="">模型名称有多个匹配</option>${candidates.map(model => `<option value="${esc(model.catalog_id || model.model_name)}">${esc(model.display_name + ' · ' + model.model_name)}</option>`).join('')}</select></label>` : ''}
        ${selected ? `<p class="hint">${aiModelMetadataHtml(selected)}</p><button class="btn btn--ghost" data-ai-action="apply-model-info">填入建议值</button>` : ''}
        ${localImage ? '<p class="hint">固定 Qwen 工作流，模型文件由服务匹配。</p>' : ''}
        ${settings.voice_design_model ? `<p class="hint">自定义音色设计须配置 ${esc(settings.voice_design_model)} 模型。</p>` : ''}</div>
        ${service === 'llm' ? aiTokenField(card, 'context_window', '上下文窗口（tokens）', [32768, 131072, 256000, 1048576], true) + aiTokenField(card, 'max_output_tokens', '输出 Token 上限（含推理）', [8192, 16384, 32768, 65536, 131072], true) + '<p class="hint">上下文窗口容纳输入与输出，输出上限包含推理。按实际供应商或部署填写；k = 1000，M = 1000000。建议值可修改，保存后即时生效。</p>' : ''}
        ${types('input_modalities', '支持的输入类型')}${types('output_modalities', '支持的输出类型')}
        <p class="hint">当前适配器可传递的输入：${(settings.input_modalities ?? []).map(type => esc(AI_MODALITIES[type] || type)).join('、')}。功能按模型配置与协议支持共同筛选。</p>`;
}

function applyAiModelMetadata(card, metadata) {
    for (const field of ['context_window', 'max_output_tokens', 'input_modalities', 'output_modalities']) {
        if (metadata[field] != null) card[field] = structuredClone(metadata[field]);
    }
}

function resetAiDiscovery() {
    aiProviderDiscovery.clear();
    aiModelDiscovery = new WeakMap();
    if (aiModelPicker) closeModal('aiModelPickerModal');
}

function invalidateAiProviderDiscovery(providerId) {
    for (const [key, state] of aiProviderDiscovery) {
        if (state.providerId === providerId) aiProviderDiscovery.delete(key);
    }
}

function aiDiscoveryProvider(provider) {
    return { id: provider.id, name: provider.name, provider_type: provider.provider_type, base_url: provider.base_url || '', api_key: provider.api_key || '', api_key_set: Boolean(provider.api_key_set), clear_api_key: Boolean(provider.clear_api_key) };
}

function aiProviderDiscoveryState(editorName, group, providerId) {
    const editor = aiEditors[editorName];
    const provider = aiSource(editor, providerId);
    if (!provider) return null;
    const snapshot = JSON.stringify(aiDiscoveryProvider(provider));
    const key = JSON.stringify([editorName, aiView.userId, group, providerId]);
    let state = aiProviderDiscovery.get(key);
    if (!state || state.snapshot !== snapshot || state.config !== editor.config) {
        state = { key, snapshot, editorName, config: editor.config, scope: aiView.scope, userId: aiView.userId, group, providerId,
            models: [], candidates: {}, warnings: [], loaded: false, loading: false };
        aiProviderDiscovery.set(key, state);
    }
    return state;
}

function aiDiscoveryIsCurrent(state) {
    const editor = aiEditors[state.editorName];
    const provider = aiSource(editor, state.providerId);
    return aiProviderDiscovery.get(state.key) === state && editor.config === state.config && aiView.scope === state.scope && aiView.userId === state.userId
        && provider && JSON.stringify(aiDiscoveryProvider(provider)) === state.snapshot;
}

function aiCatalogModels(state, service) {
    return (state?.models ?? []).filter(model => !model.services?.length || model.services.includes(service));
}

function aiModelDiscoveryState(card, group) {
    const catalog = aiProviderDiscoveryState(aiView.scope === 'system' ? 'system' : 'user', group, card.provider_id);
    const detail = aiModelDiscovery.get(card);
    if (detail?.catalog === catalog && detail?.modelName === card.model_name) return detail;
    if (detail) aiModelDiscovery.delete(card);
    return { catalog, metadata: catalog?.models.find(model => model.model_name === card.model_name), candidates: catalog?.candidates ?? {} };
}

function aiModelMetadataHtml(metadata) {
    const fields = { context_window: '窗口', max_output_tokens: '输出上限', input_modalities: '输入类型', output_modalities: '输出类型' };
    const values = Object.entries(fields).filter(([field]) => metadata[field] != null).map(([field, label]) => {
        const value = metadata[field];
        const source = metadata.field_sources?.[field];
        return esc(label) + '：' + esc(Array.isArray(value) ? value.map(type => AI_MODALITIES[type] || type).join('、') : value)
            + (source ? '（' + esc(source) + '）' : '');
    });
    if (metadata.catalog_url) values.push(`<a href="${esc(metadata.catalog_url)}" target="_blank" rel="noopener noreferrer">OpenRouter 详情</a>`);
    return values.join(' · ');
}

async function requestAiModelDiscovery(state, options = {}) {
    const response = await apiFetch('/api/admin/ai/models/discover', { method: 'POST', body: JSON.stringify({
        provider: JSON.parse(state.snapshot), user_id: state.editorName === 'system' ? null : Number(state.userId), service: aiService(state.group), ...options
    }) });
    return response.json();
}

function openAiModelPicker(editorName, group) {
    const editor = aiEditors[editorName];
    const providers = aiProviderOptions(editor, aiService(group));
    if (!providers.length) { toast('请先配置供应商', 'err'); return; }
    aiModelPicker = { editorName, group, selected: new Set(), returnFocus: document.activeElement };
    $('#aiModelPickerTitle').textContent = '添加模型 · ' + AI_SERVICES[aiService(group)];
    $('#aiModelPickerProvider').innerHTML = providers.map(provider => `<option value="${esc(provider.id)}">${esc(provider.scope + ' · ' + provider.name)}</option>`).join('');
    openModal('aiModelPickerModal');
    changeAiModelPickerProvider();
    $('#aiModelPickerProvider').focus();
}

function changeAiModelPickerProvider() {
    const picker = aiModelPicker;
    if (!picker) return;
    picker.providerId = $('#aiModelPickerProvider').value;
    picker.selected.clear();
    picker.catalog = aiProviderDiscoveryState(picker.editorName, picker.group, picker.providerId);
    $('#aiModelPickerSearch').value = '';
    $('#aiModelPickerManual').value = '';
    const provider = aiSource(aiEditors[picker.editorName], picker.providerId);
    $('#aiModelPickerManualField').hidden = picker.group === 'image_gen' && provider?.provider_type === 'local';
    void loadAiModelPickerModels();
}

async function loadAiModelPickerModels(refresh = false) {
    const state = aiModelPicker?.catalog;
    if (!state) return;
    if (!refresh && (state.loaded || state.loading)) { renderAiModelPickerResults(); return; }
    if (refresh) {
        for (const card of aiList(aiEditors[state.editorName], state.group)) {
            if (card.provider_id === state.providerId) aiModelDiscovery.delete(card);
        }
    }
    const request = {};
    Object.assign(state, { models: [], candidates: {}, warnings: [], loaded: false, loading: true, request });
    aiModelPicker.selected.clear();
    renderAiModelPickerResults();
    try {
        const data = await requestAiModelDiscovery(state, { refresh });
        if (!aiDiscoveryIsCurrent(state) || state.request !== request) return;
        Object.assign(state, { models: data.models ?? [], candidates: data.candidates ?? {}, warnings: data.warnings ?? [], loaded: true });
    } catch (error) {
        if (!aiDiscoveryIsCurrent(state) || state.request !== request) return;
        state.warnings = [error.message];
    } finally {
        if (state.request === request) state.loading = false;
        if (aiModelPicker?.catalog === state && aiDiscoveryIsCurrent(state)) renderAiModelPickerResults();
    }
}

function aiModelPickerExisting() {
    const picker = aiModelPicker;
    return new Set(aiList(aiEditors[picker.editorName], picker.group).filter(card => card.provider_id === picker.providerId).map(card => card.model_name.trim()));
}

function aiModelPickerSelection() {
    const picker = aiModelPicker;
    const existing = aiModelPickerExisting();
    const selected = aiCatalogModels(picker.catalog, aiService(picker.group)).filter(model => picker.selected.has(model.model_name)).map(model => model.model_name);
    const manual = $('#aiModelPickerManualField').hidden ? [] : $('#aiModelPickerManual').value.split(/\r?\n/).map(name => name.trim()).filter(Boolean);
    return [...new Set([...selected, ...manual])].filter(name => !existing.has(name));
}

function updateAiModelPickerCount() {
    if (!aiModelPicker) return;
    const names = aiModelPickerSelection();
    const invalid = names.some(name => name.length > 128);
    $('#aiModelPickerCount').textContent = invalid ? '模型 ID 不能超过 128 个字符' : '将添加 ' + names.length + ' 个模型';
    $('#aiModelPickerAdd').disabled = !names.length || invalid;
}

function renderAiModelPickerResults() {
    const picker = aiModelPicker;
    if (!picker) return;
    const state = picker.catalog;
    const existing = aiModelPickerExisting();
    const search = $('#aiModelPickerSearch').value.trim().toLocaleLowerCase();
    const models = aiCatalogModels(state, aiService(picker.group)).filter(model => (model.model_name + ' ' + model.display_name).toLocaleLowerCase().includes(search));
    $('#aiModelPickerRefresh').disabled = Boolean(state?.loading);
    $('#aiModelPickerStatus').innerHTML = (state?.loading ? '<p class="hint">正在获取模型列表…</p>' : '')
        + (state?.warnings ?? []).map(message => `<p class="hint">${esc(message)}</p>`).join('')
        + (!state?.loading && !models.length ? '<p class="hint">没有可选模型，可调整搜索或手动填写模型 ID。</p>' : '');
    $('#aiModelPickerList').innerHTML = models.map(model => {
        const added = existing.has(model.model_name);
        const pending = aiModelNeedsConfiguration(model, aiService(picker.group));
        const ambiguous = state.candidates[model.model_name]?.length;
        return `<label class="ai-model-option"><input type="checkbox" data-ai-pick-model="${esc(model.model_name)}"${added ? ' disabled' : picker.selected.has(model.model_name) ? ' checked' : ''}>
            <span><strong>${esc(model.model_name)}</strong> ${added ? '<span class="keybadge">已添加</span>' : pending ? '<span class="keybadge off">待补全</span>' : ''}
                ${model.display_name && model.display_name !== model.model_name ? `<span class="hint">${esc(model.display_name)}</span>` : ''}
                <span class="hint">${aiModelMetadataHtml(model) || '暂无参数信息，添加后可手动配置。'}</span>
                ${ambiguous ? '<span class="hint">存在多个目录匹配，添加后请选择对应模型。</span>' : ''}
            </span></label>`;
    }).join('');
    $('#aiModelPickerList').hidden = !models.length;
    updateAiModelPickerCount();
}

function selectAiModelPickerModel(event) {
    const name = event.target.dataset.aiPickModel;
    if (!aiModelPicker || !name) return;
    if (event.target.checked) aiModelPicker.selected.add(name); else aiModelPicker.selected.delete(name);
    updateAiModelPickerCount();
}

function addAiModelPickerSelection() {
    const picker = aiModelPicker;
    if (!picker) return;
    if (!picker.catalog || !aiDiscoveryIsCurrent(picker.catalog) || aiView.group !== picker.group) {
        closeModal('aiModelPickerModal'); toast('配置已变化，请重新选择供应商', 'err'); return;
    }
    const names = aiModelPickerSelection();
    if (!names.length || names.some(name => name.length > 128)) return;
    const editor = aiEditors[picker.editorName];
    const provider = aiSource(editor, picker.providerId);
    const cards = names.map(name => {
        const card = { ...newAiModel(provider.id, aiService(picker.group), provider.provider_type), model_name: name };
        const metadata = picker.catalog.models.find(model => model.model_name === name);
        if (metadata) applyAiModelMetadata(card, metadata);
        return card;
    });
    aiList(editor, picker.group).push(...cards);
    markAiDirty(picker.editorName);
    renderAiTree();
    const pending = cards.find(card => aiModelNeedsConfiguration(card, aiService(picker.group)));
    if (pending) openAiModelCard(pending.id); else renderAiChain();
    closeModal('aiModelPickerModal');
    toast('已添加 ' + cards.length + ' 个模型' + (pending ? '，请补全标记的模型参数后保存' : '，保存后生效'));
}

async function discoverAiModelInfo(editorName, group, card, catalogId = null) {
    const editor = aiEditors[editorName];
    const catalog = aiProviderDiscoveryState(editorName, group, card.provider_id);
    if (!catalog) return;
    const modelName = card.model_name;
    const request = {};
    const current = () => aiDiscoveryIsCurrent(catalog) && $('#modelDetailModal').classList.contains('open') && $('#modelDetailModal').dataset.modelId === card.id && aiList(editor, group).includes(card) && aiModelDiscovery.get(card)?.request === request && card.model_name === modelName && card.provider_id === catalog.providerId;
    aiModelDiscovery.set(card, { ...aiModelDiscoveryState(card, group), catalog, modelName, loading: true, request });
    openAiModelCard(card.id);
    try {
        const data = await requestAiModelDiscovery(catalog, { model_name: modelName, ...(catalogId ? { catalog_id: catalogId } : {}) });
        if (!current()) return;
        const metadata = data.models?.find(model => model.model_name === card.model_name) ?? null;
        aiModelDiscovery.set(card, { ...aiModelDiscovery.get(card), metadata, candidates: data.candidates ?? {}, warnings: data.warnings ?? [] });
    } catch (error) {
        if (current()) aiModelDiscovery.set(card, { ...aiModelDiscovery.get(card), warnings: [error.message] });
    } finally {
        if (aiModelDiscovery.get(card)?.request === request) aiModelDiscovery.get(card).loading = false;
        if (current()) openAiModelCard(card.id);
    }
}

function openAiModelCard(cardId) {
    const service = Object.keys(AI_SERVICES).find(key => aiModels(aiActiveEditor(), key).some(card => card.id === cardId));
    if (!service) return;
    $('#modelDetailModal').dataset.modelId = cardId;
    const editor = aiActiveEditor();
    const card = aiModels(editor, service).find(model => model.id === cardId);
    const index = editor.config.capabilities[service].findIndex(model => model.id === cardId);
    $('#modelDetailTitle').textContent = card.model_name;
    $('#modelDetailModal .modal-foot .hint').textContent = index < 0 ? '系统模型在系统配置中维护' : '修改计入当前作用域的配置草稿';
    if (index < 0) {
        $('#modelDetailBody').innerHTML = `<p class="layer-hint">系统模型 · 当前用户可引用此模型</p><div class="card card-pad"><h3>${esc(card.provider_name)}</h3><p>${esc(card.model_name)}</p>${service === 'llm' ? `<p class="hint">上下文 ${esc(card.context_window)} · 输出上限 ${esc(card.max_output_tokens)}</p>` : ''}<p class="hint">输入：${esc((card.input_modalities ?? []).map(type => AI_MODALITIES[type]).join('、'))}</p><p class="hint">输出：${esc((card.output_modalities ?? []).map(type => AI_MODALITIES[type]).join('、'))}</p></div><button class="btn btn--ghost" data-ai-source="system:${service}">定位系统模型 →</button>`;
    } else {
        const ownCard = editor.config.capabilities[service][index];
        const provider = aiSource(editor, ownCard.provider_id);
        const suppliers = aiProviderOptions(editor, service);
        const supplierField = `<div class="field"><label>供应商<select data-ai-field="provider_id">${suppliers.map(item => `<option value="${item.id}"${item.id === ownCard.provider_id ? ' selected' : ''}>${esc(item.scope + ' · ' + item.name)}</option>`).join('')}</select></label></div>`;
        const fields = aiModelFields(ownCard, service, editor.defaults?.[service]?.[provider?.provider_type] ?? {}, service === 'image_gen' && provider?.provider_type === 'local');
        const references = aiRemovalReferences(editor, service, cardId);
        $('#modelDetailBody').innerHTML = `<p class="layer-hint">${references.length ? `用于 ${references.length} 项任务 · 修改会同步影响这些引用` : '能力默认链中的模型'}</p><div data-ai-group="${service}"><article class="call-item" data-ai-index="${index}"><div class="call-item-body">${supplierField}${fields}</div></article></div>`;
    }
    bindAiCardEvents();
    openModal('modelDetailModal');
}
async function navigateAiSource(value) {
    if (!value) return;
    const [scope, ...parts] = value.split(':');
    if (scope === 'system' || scope === 'user') {
        if (scope === 'system' && !await changeAiScope('system')) return;
        aiSelectNode(parts.join(':'));
    } else aiSelectNode(value);
    closeModal('modelDetailModal');
}
function aiEventCard(target) {
    const section = target.closest('[data-ai-group]');
    const item = target.closest('[data-ai-index]');
    if (!section || !item) return null;
    const group = section.dataset.aiGroup;
    return { group, index: Number(item.dataset.aiIndex), card: aiList(aiActiveEditor(), group)[Number(item.dataset.aiIndex)] };
}
function bindAiCardEvents() {
    for (const root of [$('#aiChainBody'), $('#modelDetailBody')]) {
        root.oninput = event => {
            if (aiSaving) return;
            const target = event.target;
            const context = aiEventCard(target);
            if (!context) return;
            const { group, card } = context;
            if (target.dataset.aiModality) {
                const values = new Set(card[target.dataset.aiModality] ?? []);
                if (target.checked) values.add(target.value); else values.delete(target.value);
                card[target.dataset.aiModality] = [...values];
            } else if (target.dataset.aiField) {
                const field = target.dataset.aiField;
                card[field] = target.value;
                if (group === 'providers' && ['api_key', 'base_url'].includes(field)) invalidateAiProviderDiscovery(card.id);
                if (field === 'api_key' && clearedKeyCards.has(card)) card.clear_api_key = !target.value.trim();
                target.setCustomValidity('');
            } else return;
            markAiDirty(aiEditorName());
        };
        root.onchange = event => {
            if (aiSaving) return;
            const target = event.target;
            const context = aiEventCard(target);
            if (!context) return;
            const { group, card } = context;
            if (target.dataset.aiCatalogChoice !== undefined && target.value) { void discoverAiModelInfo(aiEditorName(), group, card, target.value); return; }
            if (target.dataset.aiField === 'model_name') {
                const catalog = aiModelDiscoveryState(card, group).catalog;
                const metadata = catalog?.models.find(model => model.model_name === card.model_name);
                card.input_modalities = metadata?.input_modalities ?? [aiService(group) === 'stt' ? 'audio' : 'text'];
                card.output_modalities = metadata?.output_modalities ?? [AI_OUTPUTS[aiService(group)]];
                if (aiService(group) === 'llm') { card.context_window = ''; card.max_output_tokens = ''; }
                if (metadata) applyAiModelMetadata(card, metadata);
                aiModelDiscovery.set(card, { catalog, modelName: card.model_name, metadata: metadata ?? null, candidates: catalog?.candidates ?? {} });
                openAiModelCard(card.id);
            } else if (target.dataset.aiField === 'provider_id') {
                const provider = aiSource(aiActiveEditor(), card.provider_id);
                Object.assign(card, { ...newAiModel(card.provider_id, aiService(group), provider?.provider_type), id: card.id });
                aiModelDiscovery.delete(card); openAiModelCard(card.id);
            }
        };
        root.onclick = event => {
            if (aiSaving) return;
            const source = event.target.closest('[data-ai-source]');
            if (source) { void navigateAiSource(source.dataset.aiSource); return; }
            const choose = event.target.closest('[data-ai-choose]');
            if (choose) { selectAiAbility(choose.dataset.aiChoose); aiView.paletteOpen = true; $('#aiPalette').hidden = false; $('.ai-layout').classList.remove('without-palette'); $('#aiPaletteToggle').setAttribute('aria-expanded', 'true'); $('#aiPaletteSearch').focus(); return; }
            const ability = event.target.closest('.ability-card');
            if (ability) selectAiAbility(ability.dataset.aiGroup);
            const button = event.target.closest('[data-ai-action]');
            if (!button) return;
            const section = button.closest('[data-ai-group]');
            const group = section.dataset.aiGroup;
            const cards = aiList(aiActiveEditor(), group);
            const index = Number(button.closest('[data-ai-index]')?.dataset.aiIndex);
            const action = button.dataset.aiAction;
            const focusId = typeof cards[index] === 'string' ? cards[index] : cards[index]?.id;
            if (action === 'model-config') { openAiModelCard(button.dataset.aiModelId); return; }
            if (action === 'model-info') { void discoverAiModelInfo(aiEditorName(), group, cards[index]); return; }
            if (action === 'apply-model-info') {
                const metadata = aiModelDiscoveryState(cards[index], group).metadata;
                if (metadata) { applyAiModelMetadata(cards[index], metadata); openAiModelCard(cards[index].id); markAiDirty(aiEditorName()); }
                return;
            }
            if (action === 'toggle') {
                if (group !== 'providers') { openAiModelCard(cards[index].id); return; }
                const body = button.closest('.call-item').querySelector('.call-item-body');
                body.hidden = !body.hidden;
                button.textContent = body.hidden ? '配置' : '收起'; return;
            }
            if (action === 'add') {
                if (group !== 'providers') { openAiModelPicker(aiEditorName(), group); return; }
                const value = section.querySelector('[data-ai-add-source]').value;
                if (!value || !aiAvailableProviderTypes(aiActiveEditor()).includes(value)) return;
                cards.push({ id: newAiId(), name: section.querySelector('[data-ai-provider-name]').value.trim() || AI_NAMES[value] || value, provider_type: value, api_key: '', base_url: '' });
            } else if (action === 'remove') {
                if (!aiTaskChain(group)) {
                    const references = aiRemovalReferences(aiActiveEditor(), group, cards[index].id);
                    if (references.length) { toast('请先移除引用：' + references.join('；'), 'err'); return; }
                }
                if (group === 'providers') invalidateAiProviderDiscovery(cards[index].id);
                cards.splice(index, 1);
            } else if (action === 'clear-key') {
                Object.assign(cards[index], { api_key: '', api_key_set: false, clear_api_key: true });
                clearedKeyCards.add(cards[index]); invalidateAiProviderDiscovery(cards[index].id);
            } else if (action === 'up' || action === 'down') {
                const target = index + (action === 'up' ? -1 : 1);
                if (target < 0 || target >= cards.length) return;
                cards.splice(target, 0, cards.splice(index, 1)[0]);
            } else return;
            markAiDirty(aiEditorName()); renderAiScope();
            focusAiModel(group, focusId);
        };
    }
    $('#aiPaletteList').onclick = event => { const button = event.target.closest('[data-palette-add]'); if (button && !aiSaving) addAiReference(aiView.group, button.dataset.paletteAdd); };
    const dragRoot = $('#panel-ai');
    dragRoot.ondragstart = event => {
        const palette = event.target.closest('[data-palette-model]');
        const node = event.target.closest('.chain-node');
        const handle = event.target.closest('[data-ai-handle]');
        if (aiSaving || (!palette && !node && !handle) || event.target.closest('input, select, textarea')) { event.preventDefault(); return; }
        if (palette) aiDrag = { kind: 'copy', id: palette.dataset.paletteModel, service: palette.dataset.paletteService };
        else {
            const item = node ?? handle.closest('[data-ai-index]');
            aiDrag = { kind: 'move', group: item.closest('[data-ai-group]').dataset.aiGroup, index: Number(item.dataset.aiIndex) };
        }
        event.dataTransfer.effectAllowed = palette ? 'copyMove' : 'move';
        event.dataTransfer.setData('text/plain', 'spiritagent-model');
        $$('#aiChainBody .ability-card').forEach(card => card.classList.toggle('drag-compatible', compatibleAiDrop(card.dataset.aiGroup)));
    };
    dragRoot.ondragover = event => {
        const section = event.target.closest('[data-ai-group]');
        clearAiDropMarkers();
        if (!section || !compatibleAiDrop(section.dataset.aiGroup)) return;
        event.preventDefault(); event.dataTransfer.dropEffect = aiDrag.kind === 'copy' ? 'copy' : 'move';
        (event.target.closest('[data-ai-index]') ?? section.querySelector('.chain-placeholder') ?? section).classList.add('ai-drop-target');
    };
    dragRoot.ondragenter = event => {
        const section = event.target.closest('[data-ai-group]');
        if (section && compatibleAiDrop(section.dataset.aiGroup)) event.preventDefault();
    };
    dragRoot.ondrop = event => {
        const section = event.target.closest('[data-ai-group]');
        if (!section || !compatibleAiDrop(section.dataset.aiGroup)) { clearAiDrag(); return; }
        event.preventDefault();
        const group = section.dataset.aiGroup;
        const target = event.target.closest('[data-ai-index]');
        const cards = aiList(aiActiveEditor(), group);
        let position = target ? Number(target.dataset.aiIndex) : cards.length;
        if (target && aiTaskChain(group) && event.clientX > target.getBoundingClientRect().left + target.getBoundingClientRect().width / 2) position++;
        const drag = aiDrag; clearAiDrag();
        if (aiTaskChain(group)) aiView.group = group;
        if (drag.kind === 'copy') addAiReference(group, drag.id, position);
        else {
            const [card] = cards.splice(drag.index, 1);
            if (position > drag.index) position--;
            cards.splice(position, 0, card);
            markAiDirty(aiEditorName()); renderAiScope();
        }
    };
    dragRoot.ondragend = clearAiDrag;
}
function compatibleAiDrop(group) {
    if (!aiDrag || aiSaving || !group) return false;
    if (aiDrag.kind === 'move') return aiDrag.group === group;
    const task = aiTaskChain(group);
    const card = task && aiModels(aiActiveEditor(), task.service).find(model => model.id === aiDrag.id);
    return Boolean(task && aiDrag.service === task.service && card && aiModelSupportsTask(card, task));
}
function clearAiDropMarkers() { $$('#panel-ai .ai-drop-target').forEach(element => element.classList.remove('ai-drop-target')); }
function clearAiDrag() { aiDrag = null; clearAiDropMarkers(); $$('#panel-ai .drag-compatible').forEach(element => element.classList.remove('drag-compatible')); }


// SETTINGS_SECTIONS 是设置表单的唯一事实来源，并派生读写键名。
const num = (key, label, step, extra = {}) => ({ key, label, type: 'number', step, ...extra });
const text = (key, label, placeholder) => ({ key, label, type: 'text', placeholder });
const bool = (key, label) => ({ key, label, type: 'bool' });
const sel = (key, label, options) => ({ key, label, type: 'select', options });
const secret = (key, label, hint = '') => ({ key, label, type: 'secret', hint });

const SETTINGS_SECTIONS = [
    {
        title: '能力运行参数', hint: '请求、重试与媒体任务的全局运行参数',
        blocks: [
            ['sub', 'LLM 网络重试调优'],
            ['grid', [
                num('llm_max_retry_attempts', '失败最大重试次数', 1, { min: 0 }),
                num('llm_base_retry_delay', '初始重试退避 (秒)', 0.5, { min: 0 }),
                num('llm_max_retry_delay', '最大重试延迟 (秒)', 1, { min: 0 }),
            ]],
            ['sub', '视频生成轮询与下载调优'],
            ['grid', [
                num('video_gen_poll_interval_seconds', '轮询间隔 (秒)', 0.5, { min: 0.5 }),
                num('video_gen_poll_backoff_max_seconds', '轮询退避上限 (秒)', 1, { min: 1 }),
                num('video_gen_download_max_bytes', '产物下载上限 (字节)', 1048576, { min: 1048576 }),
            ]],
        ],
    },
    {
        title: '对话编排与上下文压缩', hint: '工具循环预算与长会话摘要压缩',
        blocks: [
            ['field', bool('enable_context_compression', '启用长会话自动上下文压缩摘要')],
            ['grid', [
                num('agent_max_loop_turns', '工具循环轮数上限', 1),
                num('companion_chat_max_loop_turns', '陪伴聊天工具循环上限 (轮)', 1),
                num('context_compression_threshold', '压缩触发阈值 (占上下文比例)', 0.05, { min: 0.3, max: 1 }),
                num('context_summary_target_tokens', '摘要压缩目标 Token 数', 100, { min: 100 }),
                num('ipc_future_timeout_seconds', 'IPC 异步调用等待超时 (秒)', 10, { min: 10 }),
            ], true],
        ],
    },
    {
        title: '陪伴交互与主动行为', hint: '桌面互动节流、LLM 成本冷却、主动回合预算与动作制作额度',
        blocks: [
            ['sub', '互动节流与成本冷却'],
            ['grid', [
                num('companion_idle_expression_min_interval_seconds', '空闲表达最小间隔 (秒)', 0.5),
                num('companion_approach_cooldown_seconds', '主动搭话冷却 (秒)', 30),
                num('companion_contact_quiet_seconds', '用户接触静默窗口 (秒)', 5),
                num('desktop_disconnect_grace_seconds', '桌面断线宽限 (秒)', 5),
            ]],
            ['sub', '主动回合预算'],
            ['grid', [
                num('companion_max_pending_intents', '待处理意图上限 (条)', 1),
                num('companion_min_turn_interval_seconds', '主动回合最小间隔 (秒)', 5),
                num('companion_max_loop_turns', '主动回合工具循环上限 (轮)', 1),
            ]],
            ['sub', '动作制作'],
            ['field', bool('action_autocreate_enabled', '允许伙伴自主创建新动作')],
            ['grid', [
                num('action_autonomous_create_daily_limit', '伙伴自主制作动作上限 (/天)', 1, { min: 1 }),
                num('action_user_requested_create_daily_limit', '用户请求制作动作上限 (/天)', 1, { min: 1 }),
            ]],
        ],
    },
    {
        title: '记忆维护与夜间整理', hint: '记忆维护节流、夜间窗口与规划日记预算',
        blocks: [
            ['sub', '记忆维护与召回注入'],
            ['grid', [
                num('memory_review_interval_seconds', '记忆维护最小间隔 (秒)', 60),
                num('memory_recall_max_content_chars', '召回内容截断 (字)', 100),
            ]],
            ['sub', '夜间整理与规划'],
            ['grid', [
                num('nightly_window_start_hour', '夜间窗口开始 (时)', 1, { min: 0, max: 23 }),
                num('nightly_window_end_hour', '夜间窗口结束 (时)', 1, { min: 0, max: 23 }),
                num('nightly_scan_interval_seconds', '夜间扫描间隔 (秒)', 10),
                num('reflection_max_content_chars', '反思内容上限 (字符)', 100),
            ]],
            ['sub', '调度与自动化'],
            ['grid', [
                num('scheduler_interval_seconds', '调度器 tick 周期 (秒)', 1),
                num('cron_max_active_per_user', '单用户活跃 Cron 上限', 1),
            ]],
        ],
    },
    {
        title: '社交资讯来源', hint: '配置伙伴夜间检索的新内容来源，登录凭据只保存在服务端',
        blocks: [
            ['sub', '来源与登录凭据'],
            ['grid', [
                sel('social_search_backend', '社交资讯采集方式', [['free', '免费自托管 (X、小红书)'], ['tikhub', 'TikHub API']]),
                secret('x_search_cookies', 'X 登录 Cookie', '粘贴运营账号的 Cookie，需包含 auth_token 和 ct0；填写后保存生效，留空保留已保存值。'),
                text('xhs_mcp_base_url', '小红书自托管服务地址', 'http://xiaohongshu-mcp:18060'),
                secret('xhs_mcp_auth_token', '小红书服务鉴权令牌 (可选)', '填写采集服务已配置的访问令牌；留空保留已保存值。'),
                secret('tikhub_api_key', 'TikHub API Key'),
                text('tikhub_base_url', 'TikHub 服务地址', 'https://api.tikhub.io'),
            ], true],
            ['xhs-login'],
            ['sub', '每日检索预算'],
            ['grid', [
                num('topic_search_requests_per_night', '每账户每夜搜索 HTTP 上限 (含登录和接口准备)', 1, { min: 0, max: 60 }),
                num('topic_candidate_limit', '每账户每夜候选内容上限', 1, { min: 0, max: 300 }),
                num('topic_detail_requests_per_night', '每账户每夜详情 HTTP 请求上限', 1, { min: 0, max: 60 }),
                num('topic_reserve_limit', '每天话题储备上限', 1, { min: 0, max: 5 }),
            ], true],
        ],
    },
    {
        title: '联网搜索与网页抓取', hint: 'Web Search & Extract 后端与凭据',
        blocks: [
            ['grid', [
                sel('web_search_backend', '搜索后端', [['ddgs', 'ddgs (DuckDuckGo 免密钥)'], ['brave-free', 'brave-free (Brave Search)'], ['tavily', 'tavily (Tavily AI 搜索)']]),
                sel('web_extract_backend', '抓取提取后端', [['tavily', 'tavily (Tavily AI 抓取)'], ['ddgs', 'ddgs']]),
                secret('brave_search_api_key', 'Brave Search API Key'),
                secret('tavily_api_key', 'Tavily API Key'),
                text('tavily_base_url', 'Tavily Base URL (可选覆盖)', '留空使用官方默认地址'),
                num('web_search_default_results', '默认搜索结果条数', 1, { min: 1, max: 100 }),
            ], true],
        ],
    },
    {
        title: '接口限流与安全防护', hint: '频控、生成配额与附件空间',
        blocks: [
            ['field', bool('rate_limit_enabled', '开启接口速率限制 (Rate Limiting)')],
            ['grid', [
                num('login_rate_limit_per_minute', '登录频次上限 (/分钟)', 1, { min: 1 }),
                num('media_stt_rate_limit_per_minute', '语音识别 STT (/分钟)', 1, { min: 1 }),
                num('media_tts_rate_limit_per_minute', '语音合成 TTS (/分钟)', 1, { min: 1 }),
                num('media_video_rate_limit_per_minute', '视频附件上传 (/分钟)', 1, { min: 1 }),
                num('companion_avatar_generate_rate_limit_per_minute', '伙伴头像生成 (/分钟)', 1, { min: 1 }),
                num('companion_outfit_generate_rate_limit_per_hour', '伙伴换装外观生成 (/小时)', 1, { min: 1 }),
            ]],
            ['sub', '场景、动态与安全配额'],
            ['grid', [
                num('scene_llm_create_per_24h', '角色自主新增场景上限 (/24小时)'),
                num('scene_store_max_attempts', '场景图片转存最大重试次数', 1, { min: 1 }),
                num('post_autonomous_per_day', '伙伴自主发布动态配额 (/24小时)', 1, { min: 0 }),
                num('max_attachments_per_turn', '单回合附件数量上限', 1),
                num('attachment_session_quota_bytes', '会话附件目录配额 (字节)', 1048576),
            ]],
            ['field', bool('diary_nightly_enabled', '开启夜间自主日记')],
            ['field', bool('ssrf_guard_enabled', '开启出站 SSRF 守卫 (默认关闭)')],
            ['field', text('ssrf_allowed_cidrs', 'SSRF 豁免 IP 网段 (逗号分隔 CIDR)', '例如 198.18.0.0/15,fdfe:dcba:9876::/64')],
        ],
    },
    {
        title: '远程与系统运行时', hint: '公网地址与日志调优',
        blocks: [
            ['sub', '运行时与日志调优'],
            ['grid', [
                text('public_base_url', '公网访问基础地址 (public_base_url)', 'https://example.com'),
                num('temp_file_ttl_hours', '临时媒体文件存活时长 (小时)', 1, { min: 1 }),
                num('access_token_expire_minutes', '登录令牌有效期 (用户与管理端，分钟)', 1, { min: 1, max: 5256000 }),
                sel('log_level', '日志输出级别', ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map(v => [v, v])),
                sel('log_format', '日志格式', [['json', 'json'], ['text', 'text']]),
            ]],
            ['field', bool('llm_debug_logging', '开启 LLM Debug 面包屑追踪日志')],
            ['field', { ...num('llm_debug_max_chars', 'LLM Debug 单字段截断字符数', 1, { min: 0 }), narrow: true }],
        ],
    },
];

const SETTINGS_FIELDS = SETTINGS_SECTIONS.flatMap(s => s.blocks.flatMap(b => (b[0] === 'grid' ? b[1] : b[0] === 'field' ? [b[1]] : [])));
const SETTING_NUM_KEYS = SETTINGS_FIELDS.filter(f => f.type === 'number').map(f => f.key);
const SETTING_TEXT_KEYS = SETTINGS_FIELDS.filter(f => f.type === 'text' || f.type === 'select').map(f => f.key);
const SETTING_BOOL_KEYS = SETTINGS_FIELDS.filter(f => f.type === 'bool').map(f => f.key);
const SETTING_KEY_BADGES = SETTINGS_FIELDS.filter(f => f.type === 'secret').map(f => ({ key: f.key, badge: `sys_${f.key}_badge` }));

function settingsFieldHtml(f) {
    const id = `sys_${f.key}`;
    const search = esc(`${f.label} ${f.key}`.toLowerCase());
    const wrapOpen = `<div class="field settings-field${f.narrow ? ' field--narrow' : ''}" data-search-text="${search}">`;
    if (f.type === 'bool') {
        return `${wrapOpen}<label class="checkbox-row"><input type="checkbox" id="${id}"> ${f.label}</label></div>`;
    }
    if (f.type === 'select') {
        const options = f.options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
        return `${wrapOpen}<label for="${id}">${f.label}</label><select id="${id}">${options}</select></div>`;
    }
    if (f.type === 'secret') {
        return `${wrapOpen}<div class="label-row"><label for="${id}">${f.label}</label><span class="keybadge off" id="${id}_badge">未设置</span></div>`
            + `<input type="password" id="${id}" autocomplete="new-password" placeholder="留空保持原值">`
            + `<label class="checkbox-row secret-clear"><input type="checkbox" id="${id}_clear" data-secret-input="${id}" disabled> 保存时清除已保存的值</label>${f.hint ? `<p class="hint">${esc(f.hint)}</p>` : ''}</div>`;
    }
    const attrs = ` type="${f.type}"${f.step ? ` step="${f.step}"` : ''}${f.min !== undefined ? ` min="${f.min}"` : ''}${f.max !== undefined ? ` max="${f.max}"` : ''}${f.placeholder ? ` placeholder="${f.placeholder}"` : ''}`;
    return `${wrapOpen}<label for="${id}">${f.label}</label><input${attrs} id="${id}"></div>`;
}

function setKeyBadge(id, set) {
    const el = $('#' + id);
    if (!el) return;
    el.textContent = set ? '已设置' : '未设置';
    el.className = 'keybadge ' + (set ? 'on' : 'off');
}

let selectedSettingsSection = 0;
let settingsLoadPromise = null;
function renderSettingsForm() {
    $('#systemSettingsForm').inert = !settingsLoaded;
    $('#systemSettingsForm').innerHTML = SETTINGS_SECTIONS.map((section, index) => `<section class="card settings-section" id="settings-sec-${index}" data-search-text="${esc(`${section.title} ${section.hint}`.toLowerCase())}"${index ? ' hidden' : ''}><div class="card-head"><h2>${section.title}</h2><span class="spacer"></span><details class="inline-help"><summary>说明 ⓘ</summary><p>${section.hint}</p></details></div><div class="card-pad">${section.blocks.map(block => block[0] === 'sub' ? `<h3 class="subhead">${block[1]}</h3>` : block[0] === 'grid' ? `<div class="grid${block[2] ? ' grid--wide' : ''}">${block[1].map(settingsFieldHtml).join('')}</div>` : block[0] === 'xhs-login' ? $('#xhsLoginTemplate').innerHTML : settingsFieldHtml(block[1])).join('')}</div></section>`).join('');
    $('#settingsRail').innerHTML = SETTINGS_SECTIONS.map((section, index) => `<button type="button" class="rail-link${index ? '' : ' active'}" data-settings-section="${index}">${section.title}</button>`).join('');
    $('#settingsRail').onclick = event => {
        const button = event.target.closest('[data-settings-section]');
        if (!button) return;
        selectedSettingsSection = Number(button.dataset.settingsSection);
        $('#settingsSearch').value = '';
        filterSettingsFields();
    };
    $('#systemSettingsForm').addEventListener('input', markSettingsDirty);
    $('#systemSettingsForm').addEventListener('change', event => {
        const input = event.target.dataset.secretInput && $('#' + event.target.dataset.secretInput);
        if (input) { input.value = ''; input.disabled = event.target.checked; }
        markSettingsDirty();
    });
}
function markSettingsDirty() { settingsDirty = true; updateSaveActions(); syncXhsLoginVisibility(); }
function clearSettingsDirty() { settingsDirty = false; updateSaveActions(); }
function filterSettingsFields() {
    const search = $('#settingsSearch').value.trim().toLowerCase();
    let matches = 0;
    $$('.settings-section').forEach((section, index) => {
        const sectionMatches = search && section.dataset.searchText.includes(search);
        let visible = 0;
        $$('.settings-field', section).forEach(field => {
            field.hidden = Boolean(search && !sectionMatches && !field.dataset.searchText.includes(search));
            if (!field.hidden) visible++;
        });
        section.hidden = search ? !visible : index !== selectedSettingsSection;
        if (!section.hidden) matches++;
        const button = $$('#settingsRail button')[index];
        button.hidden = Boolean(search && !visible);
        button.classList.toggle('active', !search && index === selectedSettingsSection);
    });
    $('#settingsEmpty').hidden = Boolean(matches);
    syncXhsLoginVisibility();
}
async function loadSystemSettings() {
    if (settingsLoaded || settingsDirty) return;
    if (settingsLoadPromise) return settingsLoadPromise;
    settingsLoadPromise = (async () => {
        try { renderSystemSettings(await (await apiFetch('/api/admin/system-settings')).json()); }
        catch (error) { toast('加载系统参数失败：' + error.message, 'err'); }
        finally { settingsLoadPromise = null; }
    })();
    return settingsLoadPromise;
}
function renderSystemSettings(data) {
    if (!data) return;
    const sourceChanged = lastSettingsRaw && (data.xhs_mcp_base_url !== lastSettingsRaw.xhs_mcp_base_url || xhsConfigHasDraft());
    if (sourceChanged) stopXhsLogin('服务设置已更新，请重新检查登录状态。');
    lastSettingsRaw = structuredClone(data);
    settingsLoaded = true;
    $('#systemSettingsForm').inert = false;
    for (const key of SETTING_TEXT_KEYS) {
        const field = $('#sys_' + key);
        const value = data[key] ?? '';
        if (field) field.value = Array.isArray(value) ? value.join(', ') : value;
    }
    for (const key of SETTING_NUM_KEYS) { const field = $('#sys_' + key); if (field) field.value = data[key] ?? ''; }
    for (const key of SETTING_BOOL_KEYS) { const field = $('#sys_' + key); if (field) field.checked = Boolean(data[key]); }
    for (const item of SETTING_KEY_BADGES) {
        const isSet = Boolean(data[item.key + '_set']);
        const input = $('#sys_' + item.key);
        const clear = $('#sys_' + item.key + '_clear');
        if (input) { input.value = ''; input.disabled = false; }
        if (clear) { clear.checked = false; clear.disabled = !isSet; }
        setKeyBadge(item.badge, isSet);
    }
    clearSettingsDirty();
    renderXhsLogin();
}
async function saveSystemSettingsForm() {
    if (!settingsLoaded || settingsSaving) return false;
    const invalid = $$('#systemSettingsForm input, #systemSettingsForm select').find(field => !field.checkValidity());
    if (invalid) {
        const section = invalid.closest('.settings-section');
        selectedSettingsSection = Number(section.id.replace('settings-sec-', ''));
        $('#settingsSearch').value = ''; filterSettingsFields(); invalid.reportValidity(); return false;
    }
    const payload = {};
    for (const key of SETTING_TEXT_KEYS) { const field = $('#sys_' + key); if (field) payload[key] = field.value.trim(); }
    for (const key of SETTING_NUM_KEYS) { const field = $('#sys_' + key); if (field) payload[key] = field.value === '' ? '' : Number(field.value); }
    for (const key of SETTING_BOOL_KEYS) { const field = $('#sys_' + key); if (field) payload[key] = field.checked; }
    for (const item of SETTING_KEY_BADGES) {
        const field = $('#sys_' + item.key);
        if ($('#sys_' + item.key + '_clear')?.checked) { payload[item.key] = ''; payload['clear_' + item.key] = true; }
        else if (field?.value) payload[item.key] = field.value.trim();
    }
    settingsSaving = true; $('#panel-settings').inert = true; updateSaveActions();
    try {
        const response = await apiFetch('/api/admin/system-settings', { method: 'PUT', body: JSON.stringify(payload) });
        renderSystemSettings(await response.json());
        toast('系统参数已保存并实时生效'); return true;
    } catch (error) { toast('保存系统参数失败：' + error.message, 'err'); return false; }
    finally { settingsSaving = false; $('#panel-settings').inert = false; updateSaveActions(); syncXhsLoginVisibility(); }
}

const xhsLogin = { revision: 0, controller: null, pollTimer: null, expiryTimer: null, image: null, expiresAt: 0, busy: false, status: 'unchecked', message: '尚未检查登录状态', error: '' };

function xhsConfigHasDraft() {
    return settingsLoaded && (
        $('#sys_xhs_mcp_base_url')?.value.trim() !== String(lastSettingsRaw?.xhs_mcp_base_url ?? '').trim()
        || Boolean($('#sys_xhs_mcp_auth_token')?.value.trim())
        || Boolean($('#sys_xhs_mcp_auth_token_clear')?.checked)
    );
}

function xhsLoginViewVisible() {
    const manager = $('#xhsLoginManager');
    return document.visibilityState === 'visible' && $('#panel-settings').classList.contains('active')
        && manager && !manager.hidden && !manager.closest('.settings-section').hidden && Boolean(getToken());
}

function renderXhsLogin() {
    if (!$('#xhsLoginManager')) return;
    const hasAddress = Boolean(lastSettingsRaw?.xhs_mcp_base_url);
    const draft = xhsConfigHasDraft();
    const enabled = settingsLoaded && hasAddress && !draft && !settingsSaving && xhsLoginViewVisible();
    const status = $('#xhsLoginStatus');
    status.textContent = xhsLogin.status === 'logged-in' ? '已登录' : xhsLogin.image ? '等待扫码' : xhsLogin.status === 'expired' ? '已到期' : hasAddress ? '待检查' : '未配置';
    status.className = 'pill pill--' + (xhsLogin.status === 'logged-in' ? 'green' : xhsLogin.image ? 'amber' : 'muted');
    $('#xhsLoginHint').textContent = draft ? '服务地址或令牌有未保存的修改，请先保存后再操作。'
        : !hasAddress ? '请先填写并保存小红书服务地址，再获取登录二维码。' : xhsLogin.message;
    $('#xhsQrButton').disabled = !enabled || xhsLogin.busy;
    $('#xhsQrButton').textContent = xhsLogin.busy ? '正在处理…' : xhsLogin.image || xhsLogin.status === 'expired' ? '刷新二维码' : '获取登录二维码';
    $('#xhsStatusButton').disabled = !enabled || xhsLogin.busy;
    $('#xhsClearButton').disabled = !enabled || xhsLogin.busy;
    $('#xhsStopButton').hidden = !xhsLogin.image && !xhsLogin.busy;
    $('#xhsQrRegion').hidden = !xhsLogin.image;
    const image = $('#xhsQrImage');
    if (xhsLogin.image) {
        if (image.getAttribute('src') !== xhsLogin.image) image.src = xhsLogin.image;
        $('#xhsQrExpiry').textContent = `二维码剩余 ${Math.max(0, Math.ceil((xhsLogin.expiresAt - Date.now()) / 1000))} 秒`;
    } else image.removeAttribute('src');
    $('#xhsLoginError').hidden = !xhsLogin.error;
    $('#xhsLoginError').textContent = xhsLogin.error;
}

function stopXhsLogin(message = '') {
    xhsLogin.revision++;
    xhsLogin.controller?.abort();
    xhsLogin.controller = null;
    clearTimeout(xhsLogin.pollTimer);
    clearTimeout(xhsLogin.expiryTimer);
    xhsLogin.pollTimer = xhsLogin.expiryTimer = null;
    xhsLogin.image = null;
    xhsLogin.expiresAt = 0;
    xhsLogin.busy = false;
    xhsLogin.error = '';
    if (message) { xhsLogin.status = 'unchecked'; xhsLogin.message = message; }
    renderXhsLogin();
}

function syncXhsLoginVisibility() {
    if (!$('#xhsLoginManager')) return;
    if ((!xhsLoginViewVisible() || xhsConfigHasDraft()) && (xhsLogin.image || xhsLogin.controller)) {
        stopXhsLogin('已停止本页面扫码检查；重新进入后可检查登录状态。');
    } else renderXhsLogin();
}

async function xhsLoginRequest(path, method = 'GET') {
    if (!xhsLoginViewVisible() || !settingsLoaded || settingsSaving || xhsConfigHasDraft() || !lastSettingsRaw?.xhs_mcp_base_url || xhsLogin.busy) return null;
    const token = getToken();
    const revision = xhsLogin.revision;
    const controller = new AbortController();
    xhsLogin.controller = controller;
    xhsLogin.busy = true;
    xhsLogin.error = '';
    renderXhsLogin();
    const current = () => xhsLogin.revision === revision && getToken() === token && xhsLoginViewVisible() && !xhsConfigHasDraft();
    try {
        const response = await apiFetch(`/api/admin/social/xiaohongshu/login${path}`, { method, signal: controller.signal, cache: 'no-store' });
        const result = await response.json();
        return current() ? result : null;
    } catch (error) {
        if (current() && error.name !== 'AbortError') xhsLogin.error = error.message || '小红书登录操作失败';
        return null;
    } finally {
        if (xhsLogin.controller === controller) { xhsLogin.controller = null; xhsLogin.busy = false; renderXhsLogin(); }
    }
}

function armXhsQrTimers() {
    clearTimeout(xhsLogin.pollTimer);
    clearTimeout(xhsLogin.expiryTimer);
    if (!xhsLogin.image || !xhsLoginViewVisible()) return;
    const revision = xhsLogin.revision;
    xhsLogin.pollTimer = setTimeout(async () => {
        if (revision !== xhsLogin.revision || !xhsLogin.image || !xhsLoginViewVisible()) return;
        await checkXhsLogin(true);
        if (revision === xhsLogin.revision && xhsLogin.image && !xhsLogin.error) armXhsQrTimers();
    }, 3000);
    const tick = () => {
        if (revision !== xhsLogin.revision || !xhsLogin.image || !xhsLoginViewVisible()) return;
        if (Date.now() >= xhsLogin.expiresAt) {
            stopXhsLogin();
            xhsLogin.status = 'expired';
            xhsLogin.message = '二维码已到期，请刷新后扫码。';
            renderXhsLogin();
        } else { renderXhsLogin(); xhsLogin.expiryTimer = setTimeout(tick, 1000); }
    };
    tick();
}

async function startXhsQrLogin() {
    stopXhsLogin();
    const result = await xhsLoginRequest('/qrcode', 'POST');
    if (!result) return;
    if (typeof result.is_logged_in !== 'boolean') {
        xhsLogin.error = '服务返回的登录状态无法读取。';
    } else if (result.is_logged_in) {
        xhsLogin.status = 'logged-in';
        xhsLogin.message = '采集账号已登录，可检查登录状态确认当前账号。';
    } else {
        const expiresAt = Date.parse(result.expires_at);
        if (typeof result.image !== 'string' || result.image.length > 1_500_000 || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(result.image) || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
            xhsLogin.error = '服务返回的二维码不可用，请重试。';
        } else {
            xhsLogin.image = result.image;
            xhsLogin.expiresAt = expiresAt;
            xhsLogin.status = 'awaiting-scan';
            xhsLogin.message = '请使用小红书 App 扫码；检查将在离开此页或二维码到期时停止。';
            armXhsQrTimers();
        }
    }
    renderXhsLogin();
}

async function checkXhsLogin(polling = false) {
    const result = await xhsLoginRequest('/status');
    if (!result) return;
    if (typeof result.configured !== 'boolean' || typeof result.is_logged_in !== 'boolean') {
        xhsLogin.error = '服务返回的登录状态无法读取。';
    } else if (result.is_logged_in) {
        stopXhsLogin();
        xhsLogin.status = 'logged-in';
        xhsLogin.message = typeof result.username === 'string' && result.username ? `当前采集账号：${result.username}` : '当前采集账号已登录。';
    } else if (!result.configured) {
        stopXhsLogin();
        xhsLogin.message = '服务尚未配置，请保存服务地址后重试。';
    } else if (xhsLogin.image) {
        xhsLogin.message = '尚未完成登录，请继续扫码；二维码到期后可刷新。';
    } else {
        xhsLogin.status = 'unchecked';
        xhsLogin.message = '采集账号尚未登录，可获取二维码进行登录。';
    }
    renderXhsLogin();
    if (!polling && xhsLogin.image && !xhsLogin.error) armXhsQrTimers();
}

async function clearXhsSavedLogin() {
    if (!confirm('确定清除小红书采集账号已保存的登录信息？现存二维码在到期前仍可能完成登录；此操作不会撤销已发出的二维码。')) return;
    stopXhsLogin();
    const result = await xhsLoginRequest('', 'DELETE');
    if (!result) return;
    xhsLogin.status = 'unchecked';
    xhsLogin.message = typeof result.message === 'string' ? result.message : '已清除已保存的登录信息；现存二维码到期前仍可能完成登录。';
    renderXhsLogin();
}

document.addEventListener('visibilitychange', syncXhsLoginVisibility);
window.addEventListener('pagehide', () => stopXhsLogin());
renderSettingsForm();


async function loadLogs() {
    const userId = $('#logUserId').value.trim();
    const dateVal = $('#logDate').value;
    const params = new URLSearchParams();
    if (userId) params.set('user_id', userId);
    if (dateVal) params.set('target_date', dateVal);
    try {
        const data = await (await apiFetch('/api/admin/nightly-activity-logs' + (params.toString() ? '?' + params : ''))).json();
        tabCache.logs = data;
        renderLogs(data);
    } catch (e) { $('#logList').innerHTML = stateRow(6, '加载失败：' + e.message, '', true); }
}

// 未列出的状态原样显示。
const LOG_STATUS = Object.freeze({
    completed: ['已完成', 'green'],
    completed_with_errors: ['部分失败', 'red'],
    failed: ['失败', 'red'],
    running: ['运行中', 'amber'],
    skipped: ['跳过', 'muted'],
});

function renderLogs(data) {
    renderTable('#logList', 'logCount', data, {
        unit: '条记录', cols: 6, empty: '暂无夜间活动记录', emptyHint: '当夜间流水线运行后会在此展示',
        row: r => {
            const [statusLabel, statusTone] = Object.hasOwn(LOG_STATUS, r.status) ? LOG_STATUS[r.status] : [r.status, 'muted'];
            return `<tr style="cursor:pointer" onclick="openLogDetail(${r.id})">
                <td class="td-id">${r.id}</td>
                <td><div class="cell-strong">用户 ${esc(String(r.user_id))}</div><div class="cell-sub">${esc(r.summary || '')}</div></td>
                <td>${esc(r.target_date)}</td>
                <td>${pill(statusLabel, statusTone)}</td>
                <td>${fmtDate(r.updated_at || r.created_at)}</td>
                <td class="td-actions"><button class="btn btn--ghost btn--sm" onclick="event.stopPropagation(); openLogDetail(${r.id})">查看</button></td>
            </tr>`;
        },
    });
}

async function openLogDetail(id) {
    const items = tabCache.logs?.items ?? [];
    const item = items.find(r => r.id === id);
    if (!item) return;
    $('#logDetailTitle').textContent = `日志 #${item.id} · 用户 ${item.user_id} · ${item.target_date}`;
    const stageLabels = { memory_review: '记忆整理', planning: '自主活动规划', reflection: '相处理解反思', 'journal nightly': '日记创作', 'action memory': '活动记忆' };
    const stages = Array.isArray(item.payload?.stages) ? item.payload.stages : [];
    $('#logStages').innerHTML = `<p class="layer-hint">${esc(item.summary || '暂无摘要')}</p>` + stages.map(stage => {
        const tone = stage.status === 'ok' ? 'green' : stage.status === 'error' ? 'red' : 'muted';
        const label = stage.status === 'ok' ? '已完成' : stage.status === 'error' ? '失败' : stage.status === 'skipped' ? '跳过' : stage.status;
        return `<div class="log-stage">${icon(tone === 'green' ? 'check' : 'alert')}<div><strong>${esc(stageLabels[stage.stage] ?? stage.stage)}</strong><p class="hint">${esc(stage.reason || stage.error || '')}</p></div><span class="spacer"></span>${pill(label, tone)}</div>`;
    }).join('');
    $('#logDetailBody').textContent = JSON.stringify(item, null, 2);
    openModal('logDetailModal');
}

async function loadVersions() {
    try {
        const data = await (await apiFetch('/api/update/versions')).json();
        renderVersions(data);
    } catch (e) { $('#versionList').innerHTML = stateRow(6, '加载失败：' + e.message, '', true); }
}

function renderVersions(data) {
    renderTable('#versionList', 'versionCount', data, {
        unit: '个版本', cols: 6, empty: '暂无历史版本', emptyHint: '上传一个 .zip 更新包以开始分发',
        row: v => {
            const size = fmtSize(v.exe_size);
            return `<tr>
                <td><div class="cell-strong">v${esc(v.version)}</div><div class="cell-sub mono">${size ? esc(size) : '—'}</div></td>
                <td class="td-notes">${esc(v.release_notes) || '<span class="text-3">—</span>'}</td>
                <td class="td-platforms">
                    ${plat('Windows', !!v.exe_filename)}
                    ${plat('macOS', !!v.mac_filename)}
                    ${plat('Runner', !!v.runner_filename)}
                </td>
                <td>${v.is_active ? pill('上线', 'green') : pill('下线', 'muted')}</td>
                <td><div>${fmtDate(v.created_at)}</div><div class="cell-sub">${esc(v.created_by) || '—'}</div></td>
                <td class="td-actions">
                    ${v.is_active
                        ? `<button class="btn btn--ghost btn--sm" onclick="setActive(${v.id}, false)">下线</button>`
                        : `<button class="btn btn--primary btn--sm" onclick="setActive(${v.id}, true)">上线</button>`}
                    ${menuHtml([
                        { label: '永久删除', onclick: `deleteVersion(${v.id})`, danger: true },
                    ])}
                </td>
            </tr>`;
        },
    });
}

async function setActive(id, active) {
    try {
        await apiFetch(`/api/update/versions/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: active }) });
        toast(active ? '版本已上线' : '版本已下线');
        loadVersions();
    } catch (e) { toast(e.message, 'err'); }
}

async function deleteVersion(id) {
    if (!confirm('确定永久删除此版本及其文件？此操作不可撤销。')) return;
    try {
        await apiFetch(`/api/update/versions/${id}`, { method: 'DELETE' });
        toast('版本已删除');
        loadVersions();
    } catch (e) { toast(e.message, 'err'); }
}

$('#fileInput').addEventListener('change', (e) => {
    $('#fileName').textContent = e.target.files[0] ? e.target.files[0].name : '';
});

$('#uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData();
    const rn = $('#releaseNotes').value;
    if (rn) formData.append('release_notes', rn);
    formData.append('file', $('#fileInput').files[0]);
    const status = $('#uploadStatus');
    if (!$('#fileInput').files.length || $('#uploadForm').inert) return;
    $('#uploadForm').inert = true;
    status.textContent = '正在上传并处理版本，请稍候…'; status.style.color = 'var(--primary)';
    try {
        const resp = await apiFetch('/api/update/versions', { method: 'POST', body: formData, headers: {} });
        const data = await resp.json();
        status.textContent = `发布成功 · v${data.version}`; status.style.color = 'var(--green)';
        $('#uploadForm').reset(); $('#fileName').textContent = '';
        toast('新版本已发布');
        loadVersions();
    } catch (err) {
        status.textContent = '发布失败：' + err.message;
        status.style.color = 'var(--red)';
    }
    finally { $('#uploadForm').inert = false; }
});

const modalFocus = new Map();
function openModal(id) {
    const modal = $('#' + id);
    if (!modal.classList.contains('open')) modalFocus.set(id, document.activeElement);
    modal.classList.add('open'); document.body.style.overflow = 'hidden'; $('#app').inert = true;
    modal.querySelector('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')?.focus();
}
function closeModal(id) {
    if (!$('#' + id).classList.contains('open')) return;
    $('#' + id).classList.remove('open');
    const active = $('.modal.open');
    document.body.style.overflow = active ? 'hidden' : '';
    $('#app').inert = Boolean(active);
    if (id === 'scopeChangeModal' && scopeChangeResolve) { const resolve = scopeChangeResolve; scopeChangeResolve = null; resolve('cancel'); }
    if (id === 'modelDetailModal') renderAiScope();
    if (id === 'aiModelPickerModal' && aiModelPicker) {
        const target = aiModelPicker.returnFocus;
        aiModelPicker = null;
        if (target?.isConnected) target.focus();
        else $('#aiChainBody [data-ai-action="add"]')?.focus();
    }
    const focus = modalFocus.get(id);
    modalFocus.delete(id);
    if (focus?.isConnected && !focus.closest('[inert]')) focus.focus();
    else if (active) active.querySelector('button')?.focus();
    else if ($('#panel-ai').classList.contains('active')) $('#pageSaveBtn').focus();
}

document.addEventListener('click', (e) => {
    const target = e.target instanceof Element ? e.target : null;
    if (!target) return;
    const aiTarget = target.closest('[data-open-ai]');
    if (aiTarget) { void openAiGroup(aiTarget.dataset.openAi); return; }
    const choice = target.closest('[data-scope-choice]');
    if (choice && scopeChangeResolve) { const resolve = scopeChangeResolve; scopeChangeResolve = null; closeModal('scopeChangeModal'); resolve(choice.dataset.scopeChoice); return; }
    if (!target.closest('.menu')) closeAllMenus();
    if (target.classList.contains('modal')) { closeModal(target.id); return; }
    const closer = target.closest('[data-close]');
    if (closer) closeModal(closer.closest('.modal').id);
});
window.addEventListener('resize', () => closeAllMenus());
document.addEventListener('scroll', (e) => {
    if (e.target instanceof Element && e.target.closest('.menu')) return;
    closeAllMenus();
}, true);
window.addEventListener('beforeunload', (e) => {
    if (!settingsDirty && !aiSystemDirty && !aiUserDirty) return;
    e.preventDefault();
    e.returnValue = '';
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeAllMenus();
        const modal = $$('.modal.open').at(-1);
        if (modal) closeModal(modal.id);
        return;
    }
    const modal = $$('.modal.open').at(-1);
    if (modal) {
        if (e.key === 'Tab') {
            const controls = $$('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary', modal).filter(el => el.getClientRects().length);
            const first = controls[0], last = controls.at(-1);
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') e.preventDefault();
        return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S') && ($('#panel-settings')?.classList.contains('active') || $('#panel-ai')?.classList.contains('active'))) {
        e.preventDefault();
        if ($('#panel-ai')?.classList.contains('active')) saveAiScope();
        else saveSystemSettingsForm();
    }
});

$('#aiSections').onclick = event => {
    const button = event.target.closest('[data-ai-section]');
    if (!button || aiSaving) return;
    const section = button.dataset.aiSection;
    const service = aiService(aiView.group);
    if (section === 'providers') aiSelectNode('providers');
    else if (section === 'models') aiSelectNode(Object.hasOwn(AI_SERVICES, service) ? service : 'llm');
    else {
        const first = tabCache.models?.ai_tasks?.[0];
        if (first) aiSelectNode(`task:${first.id}:${first.services[0]}`);
    }
};
$('#releaseDrop').ondragover = event => { if (!$('#uploadForm').inert) { event.preventDefault(); $('#releaseDrop').classList.add('ai-drop-target'); } };
$('#releaseDrop').ondragleave = () => $('#releaseDrop').classList.remove('ai-drop-target');
$('#releaseDrop').ondrop = event => {
    event.preventDefault(); $('#releaseDrop').classList.remove('ai-drop-target');
    if ($('#uploadForm').inert) return;
    const file = event.dataTransfer.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.zip')) { toast('请选择 ZIP 更新包', 'err'); return; }
    const transfer = new DataTransfer(); transfer.items.add(file);
    $('#fileInput').files = transfer.files;
    $('#fileName').textContent = file.name;
};
$$('.modal').forEach(modal => {
    const heading = modal.querySelector('h2');
    if (heading && !heading.id) heading.id = modal.id + 'Title';
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
    if (heading) modal.setAttribute('aria-labelledby', heading.id);
});
$$('.nav-item').forEach(button => { button.setAttribute('aria-label', TABS[button.dataset.tab].title); button.title = TABS[button.dataset.tab].title; });
injectIcons();
updateSaveActions();
if (getToken()) enterApp();
