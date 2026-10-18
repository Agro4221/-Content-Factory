'use strict';

/* ============================================================
   БЕЗОПАСНОСТЬ
   ============================================================ */
function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeAiMode(mode) {
    return String(mode || 'LOCAL').trim().toUpperCase();
}

function aiModeIsLocal(mode) {
    return normalizeAiMode(mode) === 'LOCAL';
}

function parseTopicChannels(raw) {
    try {
        const value = JSON.parse(raw || '{}');
        return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch (e) {
        return {};
    }
}

function applyTheme(theme, persist = false) {
    const selected = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = selected;
    const button = document.getElementById('themeToggleBtn');
    const icon = document.getElementById('themeIcon');
    if (button) {
        const dark = selected === 'dark';
        button.setAttribute('aria-pressed', dark ? 'true' : 'false');
        button.setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
        button.title = dark ? 'Светлая тема' : 'Тёмная тема';
    }
    if (icon) {
        icon.innerHTML = selected === 'dark'
            ? '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"></path>'
            : '<path d="M20.9 13A8.2 8.2 0 0 1 11 3.1 8.8 8.8 0 1 0 20.9 13z"></path>';
    }
    if (persist) {
        try { localStorage.setItem('content-factory-theme', selected); } catch (e) { /* storage may be disabled */ }
    }
}

function setupTheme() {
    let saved = null;
    try { saved = localStorage.getItem('content-factory-theme'); } catch (e) { /* ignore */ }
    const prefersDark = typeof window.matchMedia === 'function'
        && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));
    const button = document.getElementById('themeToggleBtn');
    if (button && button.dataset.bound !== 'true') {
        button.dataset.bound = 'true';
        button.addEventListener('click', () => {
            const current = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
            applyTheme(current === 'dark' ? 'light' : 'dark', true);
        });
    }
}

let _localAiModelRequest = 0;

function localAiDefaultPort(provider) {
    return provider === 'ollama' ? '11434' : '1234';
}

function setLocalAiProviderFields(provider, previousProvider = '') {
    const host = document.getElementById('local_host');
    const port = document.getElementById('local_port');
    if (host && !String(host.value || '').trim()) host.value = '127.0.0.1';

    if (port) {
        const currentPort = String(port.value || '').trim();
        const previousDefault = previousProvider ? localAiDefaultPort(previousProvider) : '';
        if (!currentPort || (previousDefault && currentPort === previousDefault)) {
            port.value = localAiDefaultPort(provider);
        }
    }
}

function setLocalAiModelStatus(message, stateName = '') {
    const status = document.getElementById('localModelStatus');
    if (!status) return;
    status.textContent = message;
    if (stateName) status.dataset.state = stateName;
    else delete status.dataset.state;
}

async function refreshLocalAiModels(preferredModel = null) {
    const providerSelect = document.getElementById('local_ai_provider');
    const modelInput = document.getElementById('local_model');
    const modelOptions = document.getElementById('localModelOptions');
    const hostInput = document.getElementById('local_host');
    const portInput = document.getElementById('local_port');
    const refreshButton = document.getElementById('refreshLocalModelsBtn');
    if (!providerSelect || !modelInput || !modelOptions) return;

    const requestId = ++_localAiModelRequest;
    const provider = providerSelect.value === 'ollama' ? 'ollama' : 'lmstudio';
    const currentModel = String(preferredModel === null ? (modelInput.value || '') : preferredModel).trim();
    const host = String(hostInput ? hostInput.value : '127.0.0.1').trim().toLowerCase();
    const port = String((portInput && portInput.value) || localAiDefaultPort(provider)).trim();
    const isLoopback = ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host);

    if (refreshButton) refreshButton.disabled = true;

    if (!isLoopback) {
        modelOptions.textContent = '';
        setLocalAiModelStatus(
            'Для удалённого хоста укажите имя модели вручную. Автосписок доступен только для локального сервера.',
            'warn'
        );
        if (refreshButton) refreshButton.disabled = false;
        return;
    }

    if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
        modelOptions.textContent = '';
        setLocalAiModelStatus('Укажите корректный порт от 1 до 65535. Имя модели можно ввести вручную.', 'error');
        if (refreshButton) refreshButton.disabled = false;
        return;
    }

    const expectedPort = localAiDefaultPort(provider);
    if (port !== expectedPort) {
        modelOptions.textContent = '';
        setLocalAiModelStatus(
            'Автосписок работает на стандартном порту ' + expectedPort + '. Для собственного порта укажите имя модели вручную.',
            'warn'
        );
        if (refreshButton) refreshButton.disabled = false;
        return;
    }

    setLocalAiModelStatus(provider === 'ollama' ? 'Ищем модели в Ollama…' : 'Ищем модели в LM Studio…');

    try {
        const response = await fetch('/api/ai/models?provider=' + encodeURIComponent(provider), {
            headers: { 'Accept': 'application/json' },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || 'Локальный провайдер не отвечает.');
        }
        if (requestId !== _localAiModelRequest) return;

        const names = Array.from(new Set((Array.isArray(payload.models) ? payload.models : [])
            .map(item => String(item || '').trim())
            .filter(Boolean)));
        modelOptions.textContent = '';
        names.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            modelOptions.appendChild(option);
        });

        if (names.length && currentModel && !names.includes(currentModel)) {
            setLocalAiModelStatus(
                'Сохранённая модель отсутствует в списке. Проверьте имя или выберите установленную модель.',
                'warn'
            );
        } else if (names.length) {
            setLocalAiModelStatus('Найдено моделей: ' + names.length, 'success');
        } else {
            setLocalAiModelStatus(
                'Список пуст. Загрузите модель в приложении или введите её имя вручную.',
                'warn'
            );
        }
    } catch (error) {
        if (requestId !== _localAiModelRequest) return;
        modelOptions.textContent = '';
        setLocalAiModelStatus(
            'Не удалось получить список. Запустите ' + (provider === 'ollama' ? 'Ollama' : 'LM Studio') +
            ' или укажите имя модели вручную.',
            'error'
        );
        console.warn('Local AI model discovery failed:', error);
    } finally {
        if (requestId === _localAiModelRequest && refreshButton) refreshButton.disabled = false;
    }
}

async function setupLocalAiModels(settings = {}) {
    const providerSelect = document.getElementById('local_ai_provider');
    const modeSelect = document.getElementById('ai_mode');
    const host = document.getElementById('local_host');
    const port = document.getElementById('local_port');
    const refreshButton = document.getElementById('refreshLocalModelsBtn');
    if (!providerSelect) return;

    const savedPort = String(settings.local_port || (port && port.value) || '1234');
    providerSelect.value = savedPort === '11434' ? 'ollama' : 'lmstudio';
    providerSelect.dataset.previousValue = providerSelect.value;
    if (host && !String(host.value || '').trim()) host.value = '127.0.0.1';
    if (port && !String(port.value || '').trim()) port.value = localAiDefaultPort(providerSelect.value);

    if (providerSelect.dataset.bound !== 'true') {
        providerSelect.dataset.bound = 'true';
        providerSelect.addEventListener('change', () => {
            const previousProvider = providerSelect.dataset.previousValue || '';
            setLocalAiProviderFields(providerSelect.value, previousProvider);
            providerSelect.dataset.previousValue = providerSelect.value;
            void refreshLocalAiModels();
        });
    }
    if (refreshButton && refreshButton.dataset.bound !== 'true') {
        refreshButton.dataset.bound = 'true';
        refreshButton.addEventListener('click', () => void refreshLocalAiModels());
    }
    if (modeSelect && modeSelect.dataset.localModelsBound !== 'true') {
        modeSelect.dataset.localModelsBound = 'true';
        modeSelect.addEventListener('change', () => {
            if (aiModeIsLocal(modeSelect.value)) void refreshLocalAiModels();
        });
    }

    if (aiModeIsLocal((settings.ai_mode || (modeSelect && modeSelect.value) || 'LOCAL'))) {
        void refreshLocalAiModels(String(settings.local_model || ''));
    } else {
        setLocalAiModelStatus('Включите локальный режим, чтобы получить список моделей.');
    }
}

/* ============================================================
   КОНФИГУРАЦИЯ МОДУЛЕЙ И РОУТОВ
   ============================================================ */
const NAV_CONFIG = {
    posts: {
        label: 'Публикация постов',
        moduleColor: 'posts',
        routes: {
            'posts/overview': { title: 'Обзор',              crumb: 'Обзор' },
            'posts/dashboard':{ title: 'Публикации',               crumb: 'Публикации' },
            'posts/ai':       { title: 'AI',               crumb: 'AI' },
            'posts/parsing':  { title: 'Источники и правила',         crumb: 'Источники и правила' },
            'posts/proxy':    { title: 'Сеть',                crumb: 'Сеть' },
            'posts/logs':     { title: 'Журнал',             crumb: 'Журнал' },
            'posts/faq':      { title: 'Помощь',           crumb: 'Помощь' },
        }
    }
};

const DEFAULT_ROUTE = 'posts/overview';

/* ============================================================
   СОСТОЯНИЕ ПРИЛОЖЕНИЯ
   ============================================================ */
const state = {
    currentRoute: null,
    expandedGroups: new Set(['posts']),    // по умолчанию открыта «Публикация постов»
    sidebarCollapsed: false,
    mobileDrawerOpen: false,
    logFilters: new Map(),                 // scope -> level
    lastLogs: new Map(),                   // scope -> logs[]
    modules: {},                           // module_id -> runtime payload
    modulesSummary: {
        total_modules: 1,
        active_modules: 1,
        running_modules: 0,
        running_active_modules: 0,
        all_active_running: false,
        any_running: false,
        mixed: false,
        no_active_modules: false,
    },
    modulesApiAvailable: null,             // null=не проверено, true=новый API, false=legacy /api/bot/*
    selectedModules: new Set(['posts']),     // выбранные модули для пакетного запуска
    splitMenuOpen: false,
};

/* ============================================================
   УТИЛИТЫ ХРАНЕНИЯ
   ============================================================ */
function loadState() {
    try {
        const raw = sessionStorage.getItem('jostik.state');
        if (!raw) return;
        const data = JSON.parse(raw);
        if (Array.isArray(data.expandedGroups)) {
            state.expandedGroups = new Set(data.expandedGroups);
        }
        state.sidebarCollapsed = !!data.sidebarCollapsed;
        if (data.currentRoute && getRouteMeta(data.currentRoute)) {
            state.currentRoute = data.currentRoute;
        }
    } catch (e) { /* ignore */ }
}

function saveState() {
    try {
        sessionStorage.setItem('jostik.state', JSON.stringify({
            expandedGroups: Array.from(state.expandedGroups),
            sidebarCollapsed: state.sidebarCollapsed,
            currentRoute: state.currentRoute,
        }));
    } catch (e) { /* ignore */ }
}

function getRouteMeta(route) {
    for (const mod in NAV_CONFIG) {
        if (NAV_CONFIG[mod].routes[route]) {
            return { module: mod, ...NAV_CONFIG[mod].routes[route], moduleLabel: NAV_CONFIG[mod].label };
        }
    }
    return null;
}

/* ============================================================
   ТОСТЫ
   ============================================================ */
function toast({ type = 'info', title, text, duration = 4000 }) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    const icons = {
        success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
        warn:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        danger:  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        info:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    };
    el.innerHTML = `
        <span class="toast-icon">${icons[type] || icons.info}</span>
        <div class="toast-body">
            ${title ? `<p class="toast-title">${escapeHtml(title)}</p>` : ''}
            <p class="toast-text">${escapeHtml(text || '')}</p>
        </div>
    `;
    container.appendChild(el);
    setTimeout(() => {
        el.style.transition = 'opacity 0.2s, transform 0.2s';
        el.style.opacity = '0';
        el.style.transform = 'translateX(20px)';
        setTimeout(() => el.remove(), 250);
    }, duration);
}

/* ============================================================
   РОУТЕР
   ============================================================ */
function navigate(route) {
    if (!getRouteMeta(route)) {
        route = DEFAULT_ROUTE;
    }
    state.currentRoute = route;
    const meta = getRouteMeta(route);

    // Активный пункт меню
    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.route === route);
    });

    // Активный root
    document.querySelectorAll('.nav-group').forEach(el => {
        el.classList.toggle('active', el.dataset.module === meta.module);
    });

    // Раскрыть группу активного маршрута
    state.expandedGroups.add(meta.module);
    applyExpandedGroups();

    // Активный route
    document.querySelectorAll('.route').forEach(el => {
        el.classList.toggle('active', el.dataset.route === route);
    });

    // Хлебные крошки
    renderBreadcrumbs(meta);

    // Workflow-степпер (если он есть на текущем экране)
    if (typeof syncWorkflowStep === 'function') syncWorkflowStep();

    // Сброс proxy-визарда на шаг 1 при заходе на 1.3
    if (route === 'posts/proxy' && typeof wizardGoTo === 'function') wizardGoTo(1);

    // Модульные дашборды — подтягиваем данные при входе
    if (route === 'posts/dashboard') {
        const mod = 'posts';
        if (typeof renderModuleDashboard === 'function') renderModuleDashboard(mod);
    }

    // Панель здоровья модуля — на любой странице модуля
    // (REDESIGN §6.2: «в верхней части каждой страницы модуля»)
    if (typeof renderModuleHealthForRoute === 'function') renderModuleHealthForRoute(route);

    // Скролл наверх
    const content = document.querySelector('.content');
    if (content) content.scrollTop = 0;

    // Закрыть мобильный drawer
    if (state.mobileDrawerOpen) closeDrawer();

    if (typeof closeSplitMenu === 'function') closeSplitMenu();
    if (typeof updateBotButton === 'function') updateBotButton();
    saveState();
}

function renderBreadcrumbs(meta) {
    const bc = document.getElementById('breadcrumbs');
    if (!bc) return;
    bc.innerHTML = `
        <span class="breadcrumb-item">
            <span class="breadcrumb-module" data-mod="${meta.moduleColor}">${escapeHtml(meta.moduleLabel)}</span>
        </span>
        <span class="breadcrumb-sep">/</span>
        <span class="breadcrumb-item current">${escapeHtml(meta.crumb)}</span>
    `;
}

/* ============================================================
   SIDEBAR: РАСКРЫТИЕ ГРУПП
   ============================================================ */
function applyExpandedGroups() {
    document.querySelectorAll('.nav-group').forEach(group => {
        const mod = group.dataset.module;
        const expanded = state.expandedGroups.has(mod);
        group.classList.toggle('expanded', expanded);
        const btn = group.querySelector('.nav-group-toggle');
        if (btn) btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
}

function setupSidebar() {
    // Логотип + название → переход на дашборд
    const brand = document.querySelector('.sidebar-brand');
    if (brand) {
        brand.addEventListener('click', (e) => {
            e.preventDefault();
            // Всегда ведём на главный дашборд системы
            navigate('posts/overview');
        });
    }

    // === EVENT DELEGATION на корневой <nav> ===
    // Навешиваем ОДИН обработчик на sidebar-nav, который ловит клики
    // по .nav-group-toggle и .nav-item. Это надёжнее, чем attach к
    // каждому элементу — работает даже после динамических изменений
    // и не зависит от порядка инициализации.
    const nav = document.getElementById('sidebarNav');
    if (nav) {
        nav.addEventListener('click', (e) => {
            // 1. Клик по пункту меню
            const item = e.target.closest('.nav-item');
            if (item && nav.contains(item)) {
                e.preventDefault();
                const route = item.dataset.route;
                if (route) navigate(route);
                return;
            }
            // 2. Клик по заголовку группы
            const toggle = e.target.closest('.nav-group-toggle');
            if (toggle && nav.contains(toggle)) {
                e.preventDefault();
                const group = toggle.closest('.nav-group');
                if (!group) return;
                const mod = group.dataset.module;
                const isExpanded = state.expandedGroups.has(mod);

                if (isExpanded) {
                    state.expandedGroups.delete(mod);
                    applyExpandedGroups();
                    saveState();
                } else {
                    state.expandedGroups.add(mod);
                    const inThisModule = state.currentRoute && getRouteMeta(state.currentRoute)?.module === mod;
                    if (!inThisModule) {
                        const firstChild = group.querySelector('.nav-item');
                        if (firstChild) {
                            navigate(firstChild.dataset.route);
                            return;
                        }
                    }
                    applyExpandedGroups();
                    saveState();
                }
            }
        });
    }
}

/* ============================================================
   SIDEBAR: COLLAPSE / DRAWER
   ============================================================ */
function setupSidebarCollapse() {
    const app = document.getElementById('app');
    const btn = document.getElementById('collapseBtn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        if (window.innerWidth <= 860) {
            state.mobileDrawerOpen = !state.mobileDrawerOpen;
            app.classList.toggle('mobile-drawer-open', state.mobileDrawerOpen);
            return;
        }
        state.sidebarCollapsed = !state.sidebarCollapsed;
        app.classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
        saveState();
    });

    const drawerBtn = document.getElementById('drawerBtn');
    if (drawerBtn) {
        drawerBtn.addEventListener('click', () => {
            state.mobileDrawerOpen = true;
            app.classList.add('mobile-drawer-open');
        });
    }

    // Закрытие drawer по клику на бэкдроп
    app.addEventListener('click', (e) => {
        if (state.mobileDrawerOpen && e.target === app) closeDrawer();
    });
}

function closeDrawer() {
    state.mobileDrawerOpen = false;
    document.getElementById('app').classList.remove('mobile-drawer-open');
}

/* ============================================================
   ИНИЦИАЛИЗАЦИЯ ВНЕШНЕГО ВИДА
   ============================================================ */
function applyShellState() {
    const app = document.getElementById('app');
    app.classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
    applyExpandedGroups();
}

/* ============================================================
   УПРАВЛЕНИЕ БОТОМ
   ============================================================ */
const MODULE_NAMES = {
    posts: 'Публикация постов',
};

const MODULE_ORDER = ['posts'];
const MODULES_API_UNAVAILABLE_STATUSES = new Set([404, 405, 501]);

function getModuleRuntime(moduleId) {
    if (state.modules[moduleId]) return state.modules[moduleId];
    return {
        id: moduleId,
        name: MODULE_NAMES[moduleId] || moduleId,
        status: 'stopped',
        state: 'stopped',
        running: false,
        available: true,
        in_development: false,
    };
}

function getModulesSummary() {
    return {
        total_modules: Number(state.modulesSummary.total_modules || MODULE_ORDER.length),
        active_modules: Number(state.modulesSummary.active_modules || 0),
        running_modules: Number(state.modulesSummary.running_modules || 0),
        running_active_modules: Number(state.modulesSummary.running_active_modules || 0),
        all_active_running: !!state.modulesSummary.all_active_running,
        any_running: !!state.modulesSummary.any_running,
        mixed: !!state.modulesSummary.mixed,
        no_active_modules: !!state.modulesSummary.no_active_modules,
    };
}

function isModuleReadyToStart(moduleState) {
    const runtimeState = String(moduleState.state || moduleState.status || '').toLowerCase();
    return !!moduleState.available
        && !moduleState.running
        && !moduleState.in_development
        && runtimeState !== 'error'
        && runtimeState !== 'starting'
        && runtimeState !== 'stopping'
        && runtimeState !== 'development';
}

function canSelectModuleForBatch(moduleState) {
    return isModuleReadyToStart(moduleState);
}

function syncModuleSelection() {
    MODULE_ORDER.forEach((moduleId) => {
        const moduleState = getModuleRuntime(moduleId);
        if (!canSelectModuleForBatch(moduleState)) {
            state.selectedModules.delete(moduleId);
        }
    });
}

function getSelectedReadyModules() {
    return MODULE_ORDER.filter((moduleId) => {
        if (!state.selectedModules.has(moduleId)) return false;
        return isModuleReadyToStart(getModuleRuntime(moduleId));
    });
}

function setModuleSelected(moduleId, selected) {
    const moduleState = getModuleRuntime(moduleId);
    if (!canSelectModuleForBatch(moduleState)) {
        state.selectedModules.delete(moduleId);
        return;
    }
    if (selected) state.selectedModules.add(moduleId);
    else state.selectedModules.delete(moduleId);
}

function buildLegacyPayload(postsStatus = 'stopped') {
    const running = postsStatus === 'running';
    return {
        modules: [{
            id: 'posts',
            name: MODULE_NAMES.posts,
            status: running ? 'running' : 'stopped',
            state: running ? 'running' : 'stopped',
            running,
            available: true,
            in_development: false,
        }],
        summary: {
            total_modules: 1,
            active_modules: 1,
            running_modules: running ? 1 : 0,
            running_active_modules: running ? 1 : 0,
            all_active_running: running,
            any_running: running,
            mixed: false,
            no_active_modules: false,
        },
    };
}
async function refreshLegacyPostsStatus() {
    try {
        const res = await fetch('/api/bot/status');
        if (!res.ok) return false;
        const data = await res.json();
        const status = data.status === 'running' ? 'running' : 'stopped';
        applyModulesPayload(buildLegacyPayload(status));
        return true;
    } catch (e) {
        return false;
    }
}

function applyModulesPayload(payload) {
    if (!payload || !Array.isArray(payload.modules)) return;
    const map = {};
    payload.modules.forEach((module) => {
        if (module && module.id) map[module.id] = module;
    });
    state.modules = map;
    state.modulesSummary = payload.summary || {};
    updateSidebarModuleDots();
    updateBotButton();
    updateSplitMenu();
    syncOverviewLaunchButtons();
    updateModuleControlButtons();
    updateModuleControlButtons();
}

function getTopbarContext(route = state.currentRoute) {
    if (!route || route === 'posts/overview') return { kind: 'global' };
    if (route.startsWith('posts/')) return { kind: 'module', moduleId: 'posts' };
    return { kind: 'global' };
}

function setBotIcon(mode) {
    const icon = document.getElementById('botIcon');
    if (!icon) return;
    if (mode === 'stop') {
        icon.innerHTML = '<rect x="6" y="6" width="12" height="12" rx="2"></rect>';
        return;
    }
    if (mode === 'warning') {
        icon.innerHTML = '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="7.5" x2="12" y2="12.5"></line><line x1="12" y1="16.5" x2="12.01" y2="16.5"></line>';
        return;
    }
    if (mode === 'blocked') {
        icon.innerHTML = '<circle cx="12" cy="12" r="9"></circle><line x1="7" y1="17" x2="17" y2="7"></line>';
        return;
    }
    if (mode === 'mixed') {
        icon.innerHTML = '<path d="M5 5v14l6-4 8 4V5l-8 4-6-4z"></path>';
        return;
    }
    icon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
}

function setSplitTone(tone) {
    const mainBtn = document.getElementById('botToggleBtn');
    const arrowBtn = document.getElementById('botSplitArrow');
    const splitWrap = document.getElementById('botSplitBtn');
    const classes = ['btn-success', 'btn-danger', 'btn-warning', 'btn-ghost'];
    const toneClass = tone === 'danger'
        ? 'btn-danger'
        : tone === 'warning'
            ? 'btn-warning'
            : tone === 'neutral'
                ? 'btn-ghost'
                : 'btn-success';

    [mainBtn, arrowBtn].forEach((btn) => {
        if (!btn) return;
        btn.classList.remove(...classes);
        btn.classList.add(toneClass);
    });
    if (splitWrap) splitWrap.dataset.tone = tone;
}

function closeSplitMenu() {
    const menu = document.getElementById('botSplitMenu');
    const arrow = document.getElementById('botSplitArrow');
    if (menu) menu.classList.remove('open');
    if (arrow) arrow.setAttribute('aria-expanded', 'false');
    state.splitMenuOpen = false;
}

function toggleSplitMenu() {
    const menu = document.getElementById('botSplitMenu');
    const arrow = document.getElementById('botSplitArrow');
    if (!menu || !arrow) return;
    state.splitMenuOpen = !state.splitMenuOpen;
    menu.classList.toggle('open', state.splitMenuOpen);
    arrow.setAttribute('aria-expanded', state.splitMenuOpen ? 'true' : 'false');
}

function updateSidebarModuleDots() {
    MODULE_ORDER.forEach((moduleId) => {
        const dot = document.querySelector(`[data-module-dot="${moduleId}"]`);
        if (!dot) return;
        const moduleState = getModuleRuntime(moduleId);
        const runtimeState = String(moduleState.state || '').toLowerCase();
        const dotState = moduleState.running
            ? 'running'
            : (runtimeState || (moduleState.in_development ? 'development' : 'stopped'));
        dot.dataset.state = dotState;
    });
}

function updateSplitMenu() {
    syncModuleSelection();
    const summary = getModulesSummary();
    const selectedReady = getSelectedReadyModules();
    const startAll = document.getElementById('menuStartAll');
    const stopAll = document.getElementById('menuStopAll');
    if (startAll) {
        const canStartSelected = selectedReady.length > 0;
        startAll.disabled = !canStartSelected;
        startAll.title = canStartSelected
            ? `Запустить выбранные модули (${selectedReady.length})`
            : 'Выберите готовые к запуску модули';
    }
    if (stopAll) {
        stopAll.disabled = !summary.any_running;
        stopAll.title = summary.any_running ? '' : 'Нет запущенных модулей';
    }

    MODULE_ORDER.forEach((moduleId) => {
        const row = document.querySelector(`[data-module-row="${moduleId}"]`);
        const btn = document.querySelector(`[data-module-action="${moduleId}"]`);
        if (!btn) return;
        const moduleState = getModuleRuntime(moduleId);
        const nameEl = btn.querySelector('.split-module-name');
        const readyDot = btn.querySelector('[data-module-ready-dot]');
        const statusLabel = btn.querySelector('[data-module-status-label]');
        const checkbox = row
            ? row.querySelector('[data-module-select]')
            : document.querySelector(`[data-module-select="${moduleId}"]`);
        const runtimeState = String(moduleState.state || '').toLowerCase();
        const readyToStart = isModuleReadyToStart(moduleState);
        const canInteract = (moduleState.available || moduleState.running)
            && runtimeState !== 'starting'
            && runtimeState !== 'stopping';
        const selectable = canSelectModuleForBatch(moduleState);

        if (nameEl) nameEl.textContent = moduleState.name;
        if (readyDot) readyDot.dataset.ready = readyToStart ? 'true' : 'false';
        if (checkbox) {
            checkbox.checked = state.selectedModules.has(moduleId);
            checkbox.disabled = !selectable;
        }
        if (statusLabel) {
            if (runtimeState === 'error') {
                statusLabel.textContent = 'error';
                statusLabel.className = 'split-menu-status danger';
            } else if (runtimeState === 'starting') {
                statusLabel.textContent = 'starting';
                statusLabel.className = 'split-menu-status warn';
            } else if (runtimeState === 'stopping') {
                statusLabel.textContent = 'stopping';
                statusLabel.className = 'split-menu-status warn';
            } else if (moduleState.running) {
                statusLabel.textContent = 'running';
                statusLabel.className = 'split-menu-status ready';
            } else if (moduleState.in_development || runtimeState === 'development') {
                statusLabel.textContent = 'в разраб.';
                statusLabel.className = 'split-menu-status dev';
            } else if (readyToStart) {
                statusLabel.textContent = 'ready';
                statusLabel.className = 'split-menu-status ready';
            } else {
                statusLabel.textContent = 'недоступен';
                statusLabel.className = 'split-menu-status danger';
            }
        }

        btn.disabled = !canInteract;
        btn.title = !canInteract ? 'Модуль недоступен.' : '';
    });
}

function syncOverviewLaunchButtons() {
    const posts = getModuleRuntime('posts');
    const postsStart = document.getElementById('postsLocalStartBtn');
    const postsStop = document.getElementById('postsLocalStopBtn');
    const postsHint = document.getElementById('postsLocalHint');
    if (postsStart) postsStart.disabled = posts.running || !posts.available;
    if (postsStop) postsStop.disabled = !posts.running;
    if (postsHint) {
        postsHint.textContent = posts.running
            ? 'Модуль активен. Можно остановить его вручную или через шапку.'
            : 'Модуль остановлен. Запуск доступен здесь и через кнопку в шапке.';
    }

    [
    ].forEach((item) => {
        const moduleState = getModuleRuntime(item.id);
        const runtimeState = String(moduleState.state || '').toLowerCase();
        const actionBtn = document.getElementById(item.button);
        const prefillBtn = document.getElementById(item.prefill);
        if (!actionBtn) return;

        if (runtimeState === 'starting' || runtimeState === 'stopping') {
            actionBtn.disabled = true;
            actionBtn.className = 'btn btn-warning';
            actionBtn.textContent = runtimeState === 'starting' ? '… Запуск модуля' : '… Остановка модуля';
            actionBtn.title = 'Дождитесь завершения перехода состояния';
            actionBtn.onclick = null;
            if (prefillBtn) prefillBtn.hidden = true;
        } else if (moduleState.available || moduleState.running) {
            actionBtn.disabled = false;
            actionBtn.className = runtimeState === 'error'
                ? 'btn btn-warning'
                : (moduleState.running ? 'btn btn-danger' : 'btn btn-success');
            actionBtn.textContent = moduleState.running ? '■ Остановить' : '▶ Запустить модуль';
            actionBtn.title = '';
            actionBtn.onclick = () => handleModuleAction(item.id);
            if (prefillBtn) prefillBtn.hidden = true;
        } else {
            actionBtn.disabled = true;
            actionBtn.className = 'btn btn-ghost';
            actionBtn.textContent = '⊘ Запуск временно недоступен';
            actionBtn.title = 'Модуль недоступен.';
            actionBtn.onclick = null;
            if (prefillBtn) prefillBtn.hidden = false;
        }
    });
}

function updateBotButton() {
    const btn = document.getElementById('botToggleBtn');
    const label = document.getElementById('botLabel');
    const summaryLabel = document.getElementById('botSummary');
    if (!btn || !label) return;

    const context = getTopbarContext();
    const summary = getModulesSummary();
    const hasError = MODULE_ORDER.some((moduleId) => getModuleRuntime(moduleId).state === 'error');

    let text = 'Запустить все';
    let tone = 'success';
    let iconMode = 'play';
    let disabled = false;
    let stateAttr = 'stopped';

    if (context.kind === 'global') {
        if (hasError) {
            text = 'Ошибка — подробнее';
            tone = 'danger';
            iconMode = 'warning';
        } else if (summary.no_active_modules) {
            text = 'Запустить все';
            tone = 'neutral';
            disabled = true;
            iconMode = 'blocked';
        } else if (summary.all_active_running) {
            text = 'Остановить все';
            tone = 'danger';
            iconMode = 'stop';
            stateAttr = 'running';
        } else if (summary.mixed) {
            text = `Смешанный [${summary.running_modules}/${summary.total_modules}]`;
            tone = 'warning';
            iconMode = 'mixed';
        } else {
            text = 'Запустить все';
            tone = 'success';
            iconMode = 'play';
        }
    } else {
        const moduleState = getModuleRuntime(context.moduleId);
        if (!moduleState.available && !moduleState.running) {
            text = 'Модуль недоступен';
            tone = 'neutral';
            iconMode = 'blocked';
            disabled = true;
        } else if (moduleState.running) {
            text = 'Остановить';
            tone = 'danger';
            iconMode = 'stop';
            stateAttr = 'running';
        } else {
            text = 'Запустить';
            tone = 'success';
            iconMode = 'play';
        }
    }

    btn.disabled = disabled;
    btn.dataset.state = stateAttr;
    label.textContent = text;
    setSplitTone(tone);
    setBotIcon(iconMode);

    if (summaryLabel) {
        const showSummary = summary.running_modules > 0;
        summaryLabel.hidden = !showSummary;
        if (showSummary) summaryLabel.textContent = `${summary.running_modules}/${summary.total_modules} активно`;
    }
}

function updateModuleControlButtons(module, status) {
    const moduleState = getModuleRuntime(module);
    const startBtn = document.getElementById(`${module}StartBtn`);
    const stopBtn = document.getElementById(`${module}StopBtn`);
    if (!startBtn && !stopBtn) return;

    if (moduleState.in_development && !moduleState.running) {
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.title = 'Модуль подключится в следующем релизе';
        }
        if (stopBtn) {
            stopBtn.disabled = true;
            stopBtn.title = 'Модуль пока недоступен';
        }
        return;
    }

    const effectiveState = String(moduleState.state || status || '').toLowerCase();
    const isRunning = (status || moduleState.status) === 'running' || !!moduleState.running;
    const isBusy = effectiveState === 'starting' || effectiveState === 'stopping';
    if (startBtn) {
        startBtn.disabled = isRunning || isBusy;
        startBtn.title = '';
    }
    if (stopBtn) {
        stopBtn.disabled = !isRunning || isBusy;
        stopBtn.title = '';
    }
}

async function refreshModulesStatus() {
    try {
        const res = await fetch('/api/modules/status');
        if (res.ok) {
            state.modulesApiAvailable = true;
            const data = await res.json();
            applyModulesPayload(data);
            return;
        }
        if (MODULES_API_UNAVAILABLE_STATUSES.has(res.status)) {
            state.modulesApiAvailable = false;
            await refreshLegacyPostsStatus();
            return;
        }
        if (state.modulesApiAvailable === false) {
            await refreshLegacyPostsStatus();
        }
    } catch (e) {
        if (state.modulesApiAvailable === false) {
            await refreshLegacyPostsStatus();
        }
    }
}

async function callLegacyPostsAction(action) {
    try {
        const statusRes = await fetch('/api/bot/status');
        if (!statusRes.ok) {
            toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось получить статус модуля.' });
            return { ok: false, data: {} };
        }
        const statusData = await statusRes.json();
        let running = statusData.status === 'running';
        let changed = false;

        const needToggle = (action === 'start' && !running) || (action === 'stop' && running);
        if (needToggle) {
            const toggleRes = await fetch('/api/bot/toggle', { method: 'POST' });
            let toggleData = {};
            try {
                toggleData = await toggleRes.json();
            } catch (_) {
                toggleData = {};
            }
            if (toggleRes.status === 403) {
                toast({ type: 'warn', title: 'Доступ запрещён', text: 'Приложение отклонило запуск. Проверьте настройки доступа.' });
                return { ok: false, data: toggleData };
            }
            if (!toggleRes.ok) {
                toast({ type: 'warn', title: 'Запуск модуля', text: toggleData.detail || 'Операция не выполнена.' });
                return { ok: false, data: toggleData };
            }
            running = toggleData.status === 'running';
            changed = true;
        }

        applyModulesPayload(buildLegacyPayload(running ? 'running' : 'stopped'));
        return { ok: true, data: getModuleRuntime('posts'), changed };
    } catch (e) {
        toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось выполнить действие для модуля.' });
        return { ok: false, data: {} };
    }
}

async function callModuleAction(moduleId, action) {
    if (moduleId === 'posts' && state.modulesApiAvailable === false) {
        return await callLegacyPostsAction(action);
    }
    try {
        const res = await fetch(`/api/modules/${moduleId}/${action}`, { method: 'POST' });
        if (MODULES_API_UNAVAILABLE_STATUSES.has(res.status) && moduleId === 'posts') {
            state.modulesApiAvailable = false;
            return await callLegacyPostsAction(action);
        }
        const data = await res.json();

        if (res.status === 403) {
            toast({ type: 'warn', title: 'Доступ запрещён', text: 'Приложение отклонило запрос. Проверьте настройки доступа.' });
            return { ok: false, data };
        }

        if (!res.ok) {
            const detail = typeof data.detail === 'string'
                ? data.detail
                : (data.detail && data.detail.msg) || data.msg || 'Операция не выполнена.';
            toast({ type: 'warn', title: MODULE_NAMES[moduleId] || 'Модуль', text: detail });
            await refreshModulesStatus();
            return { ok: false, data };
        }

        if (data && data.id) {
            state.modules[data.id] = { ...getModuleRuntime(data.id), ...data };
        }
        updateSidebarModuleDots();
        updateBotButton();
        updateSplitMenu();
        syncOverviewLaunchButtons();
        updateModuleControlButtons(moduleId, data.status);
        return { ok: true, data };
    } catch (e) {
        toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось выполнить действие для модуля.' });
        return { ok: false, data: {} };
    }
}

async function startAllActiveModules() {
    const selectedReady = getSelectedReadyModules();
    if (!selectedReady.length) {
        toast({ type: 'info', title: 'Запуск модулей', text: 'Выберите хотя бы один готовый к запуску модуль.' });
        return;
    }

    if (state.modulesApiAvailable === false) {
        let started = 0;
        for (const moduleId of selectedReady) {
            if (moduleId !== 'posts') continue;
            const result = await callLegacyPostsAction('start');
            if (!result.ok) return;
            if (result.changed) started += 1;
        }
        toast({
            type: started > 0 ? 'success' : 'info',
            title: 'Запуск модулей',
            text: started > 0 ? `Запущено модулей: ${started}` : 'Выбранные модули уже запущены.',
        });
        return;
    }

    try {
        const probe = await fetch('/api/modules/status');
        if (MODULES_API_UNAVAILABLE_STATUSES.has(probe.status)) {
            state.modulesApiAvailable = false;
            let started = 0;
            for (const moduleId of selectedReady) {
                if (moduleId !== 'posts') continue;
                const result = await callLegacyPostsAction('start');
                if (!result.ok) return;
                if (result.changed) started += 1;
            }
            toast({
                type: started > 0 ? 'success' : 'info',
                title: 'Запуск модулей',
                text: started > 0 ? `Запущено модулей: ${started}` : 'Выбранные модули уже запущены.',
            });
            return;
        }

        let started = 0;
        for (const moduleId of selectedReady) {
            const moduleState = getModuleRuntime(moduleId);
            if (moduleState.running) continue;
            const result = await callModuleAction(moduleId, 'start');
            if (!result.ok) return;
            started += 1;
        }
        await refreshModulesStatus();
        toast({
            type: started > 0 ? 'success' : 'info',
            title: 'Запуск модулей',
            text: started > 0 ? `Запущено модулей: ${started}` : 'Выбранные модули уже запущены.',
        });
    } catch (e) {
        toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось запустить выбранные модули.' });
    }
}

async function stopAllModules() {
    if (state.modulesApiAvailable === false) {
        const result = await callLegacyPostsAction('stop');
        if (!result.ok) return;
        const stopped = result.changed ? 1 : 0;
        toast({
            type: 'info',
            title: 'Остановка модулей',
            text: stopped > 0 ? 'Остановлен модуль: Публикация постов.' : 'Запущенных модулей не было.',
        });
        return;
    }
    try {
        const res = await fetch('/api/modules/stop-all', { method: 'POST' });
        if (MODULES_API_UNAVAILABLE_STATUSES.has(res.status)) {
            state.modulesApiAvailable = false;
            const result = await callLegacyPostsAction('stop');
            if (!result.ok) return;
            const stopped = result.changed ? 1 : 0;
            toast({
                type: 'info',
                title: 'Остановка модулей',
                text: stopped > 0 ? 'Остановлен модуль: Публикация постов.' : 'Запущенных модулей не было.',
            });
            return;
        }
        const data = await res.json();
        if (!res.ok) {
            toast({ type: 'warn', title: 'Остановка модулей', text: data.detail || 'Не удалось остановить модули.' });
            return;
        }
        applyModulesPayload(data);
        const stopped = Array.isArray(data.stopped) ? data.stopped.length : 0;
        toast({
            type: 'info',
            title: 'Остановка модулей',
            text: stopped > 0 ? `Остановлено модулей: ${stopped}` : 'Запущенных модулей не было.',
        });
    } catch (e) {
        toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось остановить модули.' });
    }
}

async function handleModuleAction(moduleId) {
    const moduleState = getModuleRuntime(moduleId);
    const runtimeState = String(moduleState.state || '').toLowerCase();
    if (!moduleState.available && !moduleState.running) {
        toast({
            type: 'warn',
            title: MODULE_NAMES[moduleId] || 'Модуль',
            text: 'Модуль временно недоступен.',
        });
        return;
    }
    if (runtimeState === 'starting' || runtimeState === 'stopping') {
        toast({
            type: 'info',
            title: MODULE_NAMES[moduleId] || 'Модуль',
            text: 'Операция уже выполняется, дождитесь завершения.',
        });
        return;
    }

    const action = moduleState.running ? 'stop' : 'start';
    const result = await callModuleAction(moduleId, action);
    if (!result.ok) return;

    const current = getModuleRuntime(moduleId);
    const data = result.data || {};
    const started = current.running;
    if (action === 'start' && started) {
        if (data.idle || current.state === 'idle') {
            toast({
                type: 'warn',
                title: MODULE_NAMES[moduleId] || 'Модуль',
                text: data.idle_reason || data.detail || 'Модуль запущен, но сценарии не выполняются. Проверьте настройки.',
            });
            return;
        }
        if (data.detail && data.started === false) {
            toast({ type: 'info', title: MODULE_NAMES[moduleId] || 'Модуль', text: data.detail });
            return;
        }
        toast({
            type: 'success',
            title: MODULE_NAMES[moduleId] || 'Модуль',
            text: data.detail || 'Модуль запущен. Сценарии выполняются.',
        });
        return;
    }
    toast({
        type: started ? 'success' : 'info',
        title: MODULE_NAMES[moduleId] || 'Модуль',
        text: started ? 'Модуль запущен.' : 'Модуль остановлен.',
    });
}

async function handleTopbarMainAction() {
    const context = getTopbarContext();
    const summary = getModulesSummary();
    if (context.kind === 'global') {
        if (summary.no_active_modules) {
            toast({ type: 'warn', title: 'Запуск недоступен', text: 'Нет активных модулей для запуска.' });
            return;
        }
        if (summary.all_active_running) {
            await stopAllModules();
            return;
        }
        await startAllActiveModules();
        return;
    }
    await handleModuleAction(context.moduleId);
}

async function toggleBot() {
    await handleTopbarMainAction();
}

async function refreshBotStatus() {
    await refreshModulesStatus();
    const posts = getModuleRuntime('posts');
    _dashCache.bot = posts.status;
    renderDashboardFromCache();
}

/* ============================================================
   ПРОКСИ — проверка соединения и индикатор в шапке
   ============================================================ */
let _proxyStatusCache = {
    state: 'unknown',
    reachable: false,
    proxy_type: '',
    message: '',
};

function proxyPillLabel(status) {
    switch ((status && status.state) || 'unknown') {
        case 'active': return 'Активен';
        case 'disabled': return 'Выключен';
        case 'no_host': return 'Нет хоста';
        case 'checking': return 'Проверка…';
        case 'error': return 'Недоступен';
        default: return '—';
    }
}

function proxyPillState(status) {
    switch ((status && status.state) || 'unknown') {
        case 'active': return 'ok';
        case 'disabled': return 'warn';
        case 'no_host': return 'warn';
        case 'checking': return 'check';
        case 'error': return 'err';
        default: return 'check';
    }
}

function getProxyDisplayStatus(settingsOverride) {
    const status = _proxyStatusCache || {};
    const settings = settingsOverride || _dashCache.settings || {};
    const useProxy = settings.use_proxy === '1';
    const hasHost = (settings.proxy_host || '').trim();
    const port = (settings.proxy_port || '').trim();

    if (status.state === 'checking') {
        return { value: 'Проверка…', hint: status.message || 'проверяем соединение', state: 'check' };
    }
    if (!useProxy || status.state === 'disabled') {
        return { value: 'Не активен', hint: 'прямое подключение', state: 'warn' };
    }
    if (!hasHost || status.state === 'no_host') {
        return { value: 'Не активен', hint: 'укажите хост в настройках прокси', state: 'warn' };
    }
    if (status.state === 'active' && status.reachable) {
        const type = status.proxy_type || (settings.proxy_type || 'HTTP').toUpperCase();
        const hostPart = port ? `${settings.proxy_host}:${port}` : settings.proxy_host;
        return { value: 'Активен', hint: `${type} · ${hostPart}`, state: 'ok' };
    }
    if (status.state === 'error' || status.reachable === false) {
        return {
            value: 'Не активен',
            hint: status.message || 'сервер недоступен',
            state: 'err',
        };
    }
    return { value: '—', hint: 'ожидание проверки', state: 'check' };
}

function applyProxyStatusUI(status) {
    if (status) _proxyStatusCache = { ..._proxyStatusCache, ...status };
    const pill = document.getElementById('proxyPill');
    const text = document.getElementById('proxyText');
    if (pill) pill.dataset.state = proxyPillState(_proxyStatusCache);
    if (text) text.textContent = proxyPillLabel(_proxyStatusCache);
    if (typeof renderDashboardFromCache === 'function') renderDashboardFromCache();
    const route = state.currentRoute || '';
    if (route.startsWith('posts/')) {
        if (typeof renderModuleHealthForRoute === 'function') renderModuleHealthForRoute(route);
    }
}

function refreshProxyStatus() {
    applyProxyStatusUI(_proxyStatusCache);
}

async function fetchProxyStatus() {
    try {
        const res = await fetch('/api/proxy/status');
        if (!res.ok) return null;
        const data = await res.json();
        applyProxyStatusUI(data);
        return data;
    } catch (e) {
        return null;
    }
}

function collectProxyFormData() {
    const form = document.getElementById('form-proxy');
    if (!form) return {};
    return Object.fromEntries(new FormData(form).entries());
}

async function testProxyConnection(data) {
    try {
        const res = await fetch('/api/proxy/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await res.json().catch(() => ({}));
        if (!res.ok) {
            if (res.status === 404) {
                return {
                    ok: false,
                    reachable: false,
                    state: 'error',
                    message: 'Проверка прокси недоступна — перезапустите приложение (main.py), чтобы подтянуть обновление.',
                };
            }
            return {
                ok: false,
                reachable: false,
                state: 'error',
                message: (result && (result.detail || result.msg)) || `HTTP ${res.status}`,
            };
        }
        return result;
    } catch (_) {
        return {
            ok: false,
            reachable: false,
            state: 'error',
            message: 'Не удалось проверить соединение с прокси. Проверьте подключение и повторите попытку.',
        };
    }
}

function setupProxyPillNavigation() {
    const pill = document.getElementById('proxyPill');
    if (!pill) return;
    pill.addEventListener('click', () => navigate('posts/proxy'));
}

/* ============================================================
   DASHBOARD CACHE + AUTO-RENDER
   ============================================================
   Храним последние значения трёх эндпоинтов и перерисовываем
   дашборд по мере их обновления. Это избавляет от необходимости
   прокидывать состояние через 4 разные функции.
   ============================================================ */
const _dashCache = { bot: 'stopped', settings: null };
function renderDashboardFromCache() {
    if (!_dashCache.settings) return;
    renderDashboard(_dashCache.bot || 'stopped', _dashCache.settings);
    renderDashRecent((state.lastLogs.get('posts') || []).slice(0, 6));
}

/* ============================================================
   ЗАГРУЗКА / СОХРАНЕНИЕ НАСТРОЕК
   ============================================================ */
async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        for (const key in data) {
            const el = document.querySelector(`[name="${key}"]`);
            if (el) {
                el.value = data[key];
                if (key === 'poll_delay') {
                    const dv = document.getElementById('delayValue');
                    if (dv) dv.textContent = data[key];
                }
            }
        }
        // Fetch model names in the background; settings and dashboard must not
        // wait for a local Ollama/LM Studio server that may be turned off.
        if (typeof setupLocalAiModels === 'function') void setupLocalAiModels(data);
        if (typeof toggleAIFields === 'function') toggleAIFields();
        if (typeof updateProxyFields === 'function') updateProxyFields();
        refreshProxyStatus();
        fetchProxyStatus();
        renderProxyReadonly(data);

        const topics = parseTopicChannels(data.topic_channels);

        const catContainer = document.getElementById('categories-container');
        if (catContainer) {
            catContainer.textContent = '';
            if (Object.keys(topics).length) {
                for (const [k, v] of Object.entries(topics)) {
                    addCategoryRow(k, v);
                }
            }
        }

        // Dashboard uses settings returned by the real settings API.
        // The old onboarding renderer was removed with the licensing UI; do not
        // call a missing function here, or hydration will abort before this cache updates.
        _dashCache.settings = data;
        renderDashboardFromCache();

        // Модульные дашборды: подтягиваем реальные значения из настроек
        // Обзор модулей рисуется из /api/stats/* — не перезаписываем карточки настройками.
        const route = state.currentRoute || '';

        // Empty states для sources / categories
        updateEmptyStates(data, topics);
    } catch (e) {
        console.error('loadSettings error', e);
    }
}

function splitRange(raw, fallbackMin = '', fallbackMax = '') {
    const value = String(raw || '').trim();
    if (!value) return [fallbackMin, fallbackMax];
    const cleaned = value.replace(/\s+/g, '');
    if (!cleaned.includes('-')) return [cleaned, cleaned];
    const [left, right] = cleaned.split('-', 2);
    return [left || fallbackMin, right || fallbackMax];
}

async function saveSettings(formId, sourceBtn) {
    const form = document.getElementById(formId);
    if (!form) return;
    // Найдём кнопку, если не передана — по data-save
    const btn = sourceBtn || document.querySelector(`[data-save="${formId}"]`);
    clearFieldErrors(form);
    setSaveState(btn, 'loading');
    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());

    if (formId === 'form-ai' && normalizeAiMode(data.ai_mode) === 'LOCAL'
        && !String(data.local_model || '').trim()) {
        toast({ type: 'warn', title: 'Выберите модель', text: 'Запустите Ollama или LM Studio и выберите установленную модель.' });
        setSaveState(btn, 'error', 'Выберите модель');
        return;
    }

    if (formId === 'form-parsing') {
        const topics = Object.create(null);
        document.querySelectorAll('.dynamic-row').forEach(row => {
            const k = row.querySelector('.cat-key').value.trim();
            const v = row.querySelector('.cat-val').value.trim();
            if (k && v) topics[k] = v;
        });
        data.topic_channels = JSON.stringify(topics);
    }
    if (formId === 'form-proxy') {
        const useProxy = data.use_proxy === '1';
        const hasHost = (data.proxy_host || '').trim();
        if (useProxy && hasHost) {
            applyProxyStatusUI({ state: 'checking', message: 'Проверка соединения…' });
            const testResult = await testProxyConnection(data);
            if (!testResult.reachable) {
                toast({
                    type: 'danger',
                    title: 'Сервер не найден',
                    text: testResult.message || 'Соединение с прокси-сервером недоступно',
                });
                applyProxyStatusUI(testResult);
                setSaveState(btn, 'error', 'Недоступен');
                return;
            }
            applyProxyStatusUI(testResult);
        } else if (useProxy && !hasHost) {
            applyProxyStatusUI({ state: 'no_host', reachable: false, message: 'Укажите хост прокси' });
        } else {
            applyProxyStatusUI({ state: 'disabled', reachable: false, message: 'Прокси выключен' });
        }
    }

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (res.status === 403) {
            toast({ type: 'warn', title: 'Доступ запрещён', text: 'Приложение отклонило сохранение настроек.' });
            setSaveState(btn, 'error', 'Нет доступа');
            return;
        }
        const result = await res.json().catch(() => ({}));
        if (!res.ok || result.status === 'error') {
            const field = result.field || (result.detail && result.detail.field) || null;
            const msg = result.msg || (result.detail && result.detail.msg) || result.detail || `HTTP ${res.status}`;
            if (field) markFieldError(form, field, String(msg));
            toast({
                type: 'danger',
                title: 'Ошибка валидации',
                text: field ? `${field}: ${msg}` : String(msg),
            });
            setSaveState(btn, 'error', 'Ошибка');
            return;
        }
        if (result.status === 'partial') {
            toast({
                type: 'warn',
                title: 'Частично сохранено',
                text: `Принято ${result.saved} полей. Неизвестные ключи: ${result.rejected_keys.join(', ')}`
            });
            setSaveState(btn, 'error', 'Частично');
        } else {
            toast({ type: 'success', title: 'Сохранено', text: 'Настройки применены.' });
            setSaveState(btn, 'success', 'Готово');
            markSaved(formId);
            _dashCache.settings = { ...(_dashCache.settings || {}), ...data };
            renderDashboardFromCache();
            if (formId === 'form-proxy') {
                fetchProxyStatus();
            } else {
                refreshProxyStatus();
            }
        }
    } catch (e) {
        toast({ type: 'danger', title: 'Ошибка', text: 'Не удалось сохранить настройки: ' + e.message });
        setSaveState(btn, 'error', 'Ошибка');
    }
}

function clearFieldErrors(form) {
    form.querySelectorAll('.field-error').forEach((el) => el.classList.remove('field-error'));
    form.querySelectorAll('.field-error-msg').forEach((el) => el.remove());
}

function markFieldError(form, fieldName, msg) {
    const input = form.querySelector(`[name="${fieldName}"]`);
    if (!input) return;
    input.classList.add('field-error');
    const group = input.closest('.form-group');
    if (!group) return;
    const hint = document.createElement('div');
    hint.className = 'field-error-msg';
    hint.textContent = msg;
    group.appendChild(hint);
}

/* ============================================================
   EMPTY STATES — источники, категории
   ============================================================
   Пустое состояние — это инструкция, не ошибка.
   Когда поле или список пуст, рисуем рядом CTA, объясняющий,
   что добавить и зачем.
   ============================================================ */
function updateEmptyStates(settings, topics) {
    // 1. Sources: показываем empty-state, если textarea пуст
    const sources = document.getElementById('sources');
    const sourcesEmpty = document.getElementById('sourcesEmpty');
    if (sources && sourcesEmpty) {
        const hasContent = !!(sources.value || '').trim();
        sourcesEmpty.style.display = hasContent ? 'none' : '';
    }
    // 2. Categories: показываем empty-state, если контейнер пуст
    const catContainer = document.getElementById('categories-container');
    const catEmpty = document.getElementById('categoriesEmpty');
    if (catContainer && catEmpty) {
        const hasContent = catContainer.children.length > 0;
        catEmpty.style.display = hasContent ? 'none' : '';
    }
}

function addCategoryRow(catName = '', chanId = '') {
    const container = document.getElementById('categories-container');
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'dynamic-row';
    const keyInput = document.createElement('input');
    keyInput.type = 'text';
    keyInput.className = 'cat-key';
    keyInput.placeholder = 'Название (напр. спорт)';
    keyInput.value = catName;
    const valInput = document.createElement('input');
    valInput.type = 'text';
    valInput.className = 'cat-val';
    valInput.placeholder = 'ID или ссылка куда постить';
    valInput.value = chanId;
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'remove-btn';
    removeBtn.innerHTML = '&times;';
    removeBtn.onclick = () => {
        row.remove();
        // Пересчитать empty-state после удаления
        const topics = Object.create(null);
        document.querySelectorAll('.dynamic-row').forEach(r => {
            const k = r.querySelector('.cat-key').value.trim();
            const v = r.querySelector('.cat-val').value.trim();
            if (k && v) topics[k] = v;
        });
        updateEmptyStates(_dashCache.settings || {}, topics);
    };
    row.appendChild(keyInput);
    row.appendChild(valInput);
    row.appendChild(removeBtn);
    container.appendChild(row);
}

/* ============================================================
   AI MODE TOGGLE
   ============================================================ */
function toggleAIFields() {
    const mode = document.getElementById('ai_mode');
    if (!mode) return;
    const isLocal = aiModeIsLocal(mode.value);
    const localF = document.getElementById('local-ai-fields');
    const remoteF = document.getElementById('remote-ai-fields');
    if (localF) localF.style.display = isLocal ? '' : 'none';
    if (remoteF) remoteF.style.display = isLocal ? 'none' : '';
}

/* ============================================================
   PROXY TYPE → SMART FIELDS
   ============================================================
   При смене типа в <select> показываем только релевантные поля:
   HTTP / SOCKS5 — логин/пароль; MTProto — секретный ключ.
   ============================================================ */
function updateProxyFields() {
    const sel = document.getElementById('proxy_type');
    if (!sel) return;
    const type = (sel.value || 'HTTP').toUpperCase();
    const credBlock   = document.getElementById('proxy-creds-block');
    const secretBlock = document.getElementById('proxy-secret-block');
    const hostInput   = document.getElementById('proxy_host');

    if (type === 'MTPROTO') {
        if (credBlock)   credBlock.style.display = 'none';
        if (secretBlock) secretBlock.style.display = 'block';
        if (hostInput)   hostInput.placeholder = 'proxy.example.com или MTProto-ссылка';
    } else {
        if (credBlock)   credBlock.style.display = 'block';
        if (secretBlock) secretBlock.style.display = 'none';
        if (hostInput)   hostInput.placeholder = '192.168.1.1';
    }
}

/* ============================================================
   PROXY WIZARD — guided 4-step setup
   ============================================================ */
const PROXY_TYPE_HINTS = {
    HTTP:    'HTTP-прокси: хост и порт обязательны, логин и пароль — нет.',
    SOCKS5:  'SOCKS5: хост и порт обязательны, логин и пароль — нет.',
    MTProto: 'MTProto: нужен секретный ключ (начинается с dd или ee).',
};

function setupProxyWizard() {
    // Type card selection
    document.querySelectorAll('.type-card').forEach(card => {
        card.addEventListener('click', () => {
            document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            const type = card.dataset.type;
            const hidden = document.getElementById('proxy_type');
            if (hidden) hidden.value = type;
            updateProxyTypeUI(type);
        });
    });

    // Next/Back
    document.querySelectorAll('[data-wizard-next]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const target = parseInt(btn.dataset.wizardNext, 10);
            if (target === 4) {
                const data = collectProxyFormData();
                const useProxy = data.use_proxy === '1';
                const hasHost = (data.proxy_host || '').trim();
                if (useProxy && hasHost) {
                    btn.disabled = true;
                    applyProxyStatusUI({ state: 'checking', message: 'Проверка соединения…' });
                    const testResult = await testProxyConnection(data);
                    btn.disabled = false;
                    if (!testResult.reachable) {
                        toast({
                            type: 'danger',
                            title: 'Сервер не найден',
                            text: testResult.message || 'Соединение с прокси-сервером недоступно',
                        });
                        applyProxyStatusUI(testResult);
                        return;
                    }
                    applyProxyStatusUI(testResult);
                }
                refreshProxySummary();
            }
            wizardGoTo(target);
        });
    });
    document.querySelectorAll('[data-wizard-back]').forEach(btn => {
        btn.addEventListener('click', () => {
            wizardGoTo(parseInt(btn.dataset.wizardBack, 10));
        });
    });

    // If a type is pre-selected (loaded from DB), reflect it
    const hidden = document.getElementById('proxy_type');
    if (hidden && hidden.value) {
        const sel = document.querySelector(`.type-card[data-type="${hidden.value}"]`);
        if (sel) sel.classList.add('selected');
        updateProxyTypeUI(hidden.value);
    }
}

function updateProxyTypeUI(type) {
    const credBlock   = document.getElementById('proxy-creds-block');
    const secretBlock = document.getElementById('proxy-secret-block');
    const hostInput   = document.getElementById('proxy_host');
    const hint        = document.getElementById('proxyTypeHint');
    if (type === 'MTProto') {
        if (credBlock) credBlock.style.display = 'none';
        if (secretBlock) secretBlock.style.display = 'block';
        if (hostInput) hostInput.placeholder = 'proxy.example.com или MTProto-ссылка';
    } else {
        if (credBlock) credBlock.style.display = 'block';
        if (secretBlock) secretBlock.style.display = 'none';
        if (hostInput) hostInput.placeholder = '192.168.1.1';
    }
    if (hint) hint.textContent = PROXY_TYPE_HINTS[type] || '';
}

function wizardGoTo(step) {
    document.querySelectorAll('[data-wizard-panel]').forEach(panel => {
        panel.hidden = parseInt(panel.dataset.wizardPanel, 10) !== step;
    });
    document.querySelectorAll('.wizard-step').forEach(s => {
        const n = parseInt(s.dataset.step, 10);
        s.classList.toggle('active', n === step);
        s.classList.toggle('done', n < step);
    });
}

function refreshProxySummary() {
    const use = document.getElementById('use_proxy');
    const type = document.getElementById('proxy_type');
    const host = document.getElementById('proxy_host');
    const port = document.getElementById('proxy_port');
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || '—'; };
    set('summaryUseProxy', use && use.value === '1' ? 'Включён' : 'Выключен');
    set('summaryType', type ? (type.value || 'HTTP').toUpperCase() : '—');
    set('summaryHost', host ? host.value : '—');
    set('summaryPort', port ? port.value : '—');
}

/* ============================================================
   DASHBOARD WIRING — клики по карточкам модулей
   ============================================================ */
function setupDashboard() {
    document.querySelectorAll('.dash-module').forEach(card => {
        const open = () => {
            const target = card.dataset.target;
            if (target) navigate(target);
        };
        card.addEventListener('click', open);
        card.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                open();
            }
        });
    });
    document.querySelectorAll('[data-go-route]').forEach(button => {
        button.addEventListener('click', () => navigate(button.dataset.goRoute));
    });
}

/* ============================================================
   SAVE STATE MACHINE — idle / loading / success / error / blocked
   ============================================================
   Состояния:
     idle    — обычная кнопка, готова к нажатию
     loading — ожидание ответа
     success — сохранение прошло успешно
     error   — действие не выполнено
     blocked — действие временно недоступно

   Помимо короткого state-feedback, рядом с кнопкой появляется
   persistent-индикатор "✓ Сохранено N мин назад" — он не
   исчезает, пока пользователь снова не нажмёт «Сохранить».
   ============================================================ */
function setSaveState(btn, stateName, label) {
    if (!btn) return;
    if (stateName === 'idle') {
        delete btn.dataset.state;
        if (label) btn.textContent = label;
        btn.disabled = false;
        return;
    }
    btn.dataset.state = stateName;
    if (label && stateName !== 'loading') {
        // Сохраняем оригинальный label, но показываем новый
        const orig = btn.dataset.origLabel || btn.textContent;
        btn.dataset.origLabel = orig;
        btn.textContent = label;
    }
    if (stateName === 'loading') {
        btn.disabled = true;
    } else if (stateName === 'success' || stateName === 'error') {
        btn.disabled = false;
        // Возврат к idle через 1.6с
        setTimeout(() => {
            if (btn.dataset.state === stateName) {
                delete btn.dataset.state;
                if (btn.dataset.origLabel) {
                    btn.textContent = btn.dataset.origLabel;
                    delete btn.dataset.origLabel;
                }
            }
        }, 1600);
    } else if (stateName === 'blocked') {
        // Заблокировано по причине, которую показываем пользователю
        btn.disabled = true;
    }
}

/* ============================================================
   LAST SAVED INDICATOR
   ============================================================
   Показывает "✓ Сохранено 2 мин назад" под кнопкой Сохранить.
   Сохраняется в localStorage с меткой времени; пересчитывается
   каждые 30 секунд для акутальности "X мин назад".
   ============================================================ */
const _saveMeta = { lastSaved: null, timer: null };
function markSaved(formId) {
    const ts = Date.now();
    try { localStorage.setItem(`jostik.saved.${formId}`, String(ts)); } catch (e) {}
    _saveMeta.lastSaved = ts;
    renderSaveMeta(formId);
}
function renderSaveMeta(formId) {
    let ts = _saveMeta.lastSaved;
    if (!ts) {
        try { ts = parseInt(localStorage.getItem(`jostik.saved.${formId}`) || '0', 10) || null; } catch (e) {}
    }
    if (!ts) return;
    // Ищем .card-footer, в котором лежит кнопка
    const btn = document.querySelector(`[data-save="${formId}"]`);
    if (!btn) return;
    const footer = btn.closest('.card-footer');
    if (!footer) return;
    let meta = footer.querySelector('.save-meta');
    if (!meta) {
        meta = document.createElement('div');
        meta.className = 'save-meta';
        // Вставляем перед кнопкой (или в начало footer)
        const metaSlot = footer.querySelector('.meta');
        if (metaSlot && metaSlot.nextSibling) {
            footer.insertBefore(meta, metaSlot.nextSibling);
        } else {
            footer.appendChild(meta);
        }
    }
    const minutes = Math.max(0, Math.floor((Date.now() - ts) / 60000));
    const label = minutes === 0 ? 'только что' : minutes < 60 ? `${minutes} мин назад` : `${Math.floor(minutes / 60)} ч назад`;
    meta.classList.add('saved');
    meta.innerHTML = `
        <svg class="save-meta-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        Сохранено ${label}
    `;
}
function startSaveMetaTicker() {
    if (_saveMeta.timer) return;
    _saveMeta.timer = setInterval(() => {
        // Перерендерить все видимые save-meta
        document.querySelectorAll('.save-meta').forEach(meta => {
            const formId = meta.closest('.card-footer')?.querySelector('[data-save]')?.dataset.save;
            if (formId) renderSaveMeta(formId);
        });
    }, 30000);
}

/* ============================================================
   DASHBOARD — реальное состояние локального pipeline
   ============================================================ */
function renderDashboard(botStatus, settings) {
    if (!settings) return;

    const sources = String(settings.sources || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    const topics = parseTopicChannels(settings.topic_channels);
    const topicCount = Object.keys(topics).length;
    const telegramReady = !!(
        String(settings.tg_api_id || '').trim()
        && String(settings.tg_api_hash || '').trim()
        && String(settings.bot_token || '').trim()
    );
    const localMode = aiModeIsLocal(settings.ai_mode);
    const model = localMode
        ? String(settings.local_model || '').trim()
        : String(settings.remote_model || '').trim();
    const aiReady = !!model && (localMode || (
        String(settings.remote_api_key || '').trim()
        && String(settings.remote_url || '').trim()
    ));

    const setStat = (name, value, hint, status) => {
        const card = document.querySelector(`.dash-stat[data-stat="${name}"]`);
        const valueEl = document.getElementById(`dash${name.charAt(0).toUpperCase() + name.slice(1)}Value`);
        const hintEl = document.getElementById(`dash${name.charAt(0).toUpperCase() + name.slice(1)}Hint`);
        if (card) card.dataset.state = status || 'check';
        if (valueEl) valueEl.textContent = value;
        if (hintEl) hintEl.textContent = hint;
    };

    setStat(
        'bot',
        botStatus === 'running' ? 'Работает' : 'Остановлен',
        botStatus === 'running' ? 'обработка активна' : 'готов к запуску',
        botStatus === 'running' ? 'ok' : 'warn'
    );
    setStat(
        'ai',
        aiReady ? 'Настроен' : 'Не настроен',
        aiReady ? (localMode ? 'локальная модель выбрана' : 'облачный провайдер задан') : 'выберите модель и сохраните',
        aiReady ? 'ok' : 'warn'
    );
    const sourceCountLabel = sources.length === 1 ? 'канал' : (sources.length > 1 && sources.length < 5 ? 'канала' : 'каналов');
    setStat(
        'sources',
        sources.length ? String(sources.length) : 'Нет',
        sources.length ? sourceCountLabel + ' добавлено' : 'добавьте каналы-доноры',
        sources.length ? 'ok' : 'warn'
    );
    const proxyDisplay = getProxyDisplayStatus(settings);
    setStat('proxy', proxyDisplay.value, proxyDisplay.hint, proxyDisplay.state);

    const checks = [
        { ok: telegramReady, label: 'Telegram', meta: telegramReady ? 'ключи указаны' : 'нужны API ID, Hash и токен' },
        { ok: aiReady, label: 'AI', meta: model || 'модель не выбрана' },
        { ok: sources.length > 0, label: 'Источники', meta: sources.length ? `каналов: ${sources.length}` : 'добавьте источник' },
        { ok: topicCount > 0, label: 'Маршруты', meta: topicCount ? `тем: ${topicCount}` : 'сопоставьте тему и канал' },
    ];
    renderDashChecks('dashModPostsChecks', checks);

    const postsCard = document.querySelector('.dash-module[data-mod="posts"]');
    const badge = document.getElementById('dashModPostsBadge');
    if (postsCard) {
        const allOk = checks.every(check => check.ok);
        const noneOk = checks.every(check => !check.ok);
        postsCard.dataset.state = allOk ? 'ok' : (noneOk ? 'err' : 'warn');
        if (badge) {
            badge.textContent = allOk ? 'Готов к запуску' : (noneOk ? 'Требует настройки' : 'Нужно завершить настройку');
            badge.className = allOk ? 'badge badge-success' : (noneOk ? 'badge badge-danger' : 'badge badge-warn');
        }
    }

    const nextTitle = document.getElementById('dashNextTitle');
    const nextDesc = document.getElementById('dashNextDesc');
    const nextBtn = document.getElementById('dashNextBtn');
    if (nextTitle && nextDesc && nextBtn) {
        let action;
        if (!telegramReady) {
            action = { title: 'Подключите Telegram', desc: 'Добавьте API ID, API Hash и токен бота.', route: 'posts/parsing', label: 'Настроить Telegram' };
        } else if (!aiReady) {
            action = { title: 'Выберите модель AI', desc: 'Укажите установленную локальную модель или облачный API.', route: 'posts/ai', label: 'Настроить AI' };
        } else if (!sources.length) {
            action = { title: 'Добавьте источники', desc: 'Укажите Telegram-каналы, из которых брать публикации.', route: 'posts/parsing', label: 'Добавить источники' };
        } else if (!topicCount) {
            action = { title: 'Настройте маршруты публикации', desc: 'Свяжите темы с целевыми Telegram-каналами.', route: 'posts/parsing', label: 'Настроить маршруты' };
        } else if (botStatus !== 'running') {
            action = { title: 'Всё готово к запуску', desc: 'Проверьте настройки и запустите обработку постов.', run: true, label: 'Запустить парсер' };
        } else {
            action = { title: 'Публикация запущена', desc: 'Новые события, предупреждения и ошибки появятся в журнале.', route: 'posts/logs', label: 'Открыть журнал' };
        }
        nextTitle.textContent = action.title;
        nextDesc.textContent = action.desc;
        nextBtn.textContent = action.label + ' →';
        nextBtn.onclick = () => action.run ? toggleBot() : navigate(action.route);
    }

    const updated = document.getElementById('dashUpdatedAt');
    if (updated) updated.textContent = 'Состояние обновляется автоматически';
}

function renderDashChecks(containerId, checks) {
    const wrap = document.getElementById(containerId);
    if (!wrap) return;
    wrap.innerHTML = checks.map(c => {
        const state = c.ok ? 'ok' : 'err';
        const icon  = c.ok ? '✓' : '×';
        return `<li>
            <span class="dash-check ${state}">${icon}</span>
            <span class="dash-check-text">${escapeHtml(c.label)}</span>
            ${c.meta ? `<span class="dash-check-meta">${escapeHtml(c.meta)}</span>` : `<span class="dash-check-meta">${c.ok ? 'готово' : 'нужно'}</span>`}
        </li>`;
    }).join('');
}

function renderDashRecent(logs) {
    const wrap = document.getElementById('dashRecent');
    if (!wrap) return;
    if (!logs || !logs.length) {
        wrap.innerHTML = `<div class="dash-empty-recent">Журнал пуст — событий пока нет.</div>`;
        return;
    }
    wrap.innerHTML = logs.slice(0, 6).map(log => {
        const lvl = classifyLog(log.type);
        return `<div class="dash-recent">
            <span class="dash-recent-time">${escapeHtml(formatLogTime(log))}</span>
            <span class="dash-recent-type ${lvl}">${escapeHtml(log.type || '?')}</span>
            <span class="dash-recent-msg" title="${escapeHtml(log.message || '')}">${escapeHtml(log.message || '')}</span>
        </div>`;
    }).join('');
}

/* ============================================================
   POSTS STATS — реальные счётчики из БД
   ============================================================
   Получает с /api/stats/posts: published_total, skipped_total,
   last_post_ts. Обновляет нижнюю часть карточки модуля «Публикация
   постов» на главном дашборде.
   ============================================================ */
async function refreshPostsStats() {
    try {
        const res = await fetch('/api/stats/posts');
        if (!res.ok) return;
        const data = await res.json();
        if (data.proxy_status) applyProxyStatusUI(data.proxy_status);
        const pubEl = document.getElementById('dashModPostsPublished');
        const skipEl = document.getElementById('dashModPostsSkipped');
        const lastEl = document.getElementById('dashModPostsLast');
        const pubIcon = document.getElementById('dashModPostsPubIcon');
        const skipIcon = document.getElementById('dashModPostsSkipIcon');
        const lastIcon = document.getElementById('dashModPostsLastIcon');
        if (pubEl) {
            const n = data.published_total || 0;
            pubEl.textContent = n === 0 ? 'ещё нет' : n.toLocaleString('ru-RU');
            if (pubIcon) {
                pubIcon.className = n > 0 ? 'dash-check ok' : 'dash-check';
                pubIcon.textContent = n > 0 ? '✓' : '·';
            }
        }
        if (skipEl) {
            const n = data.skipped_total || 0;
            skipEl.textContent = n === 0 ? '0' : n.toLocaleString('ru-RU');
            if (skipIcon) {
                skipIcon.className = n > 0 ? 'dash-check warn' : 'dash-check';
                skipIcon.textContent = n > 0 ? '!' : '·';
            }
        }
        if (lastEl) {
            if (data.last_post_ts) {
                const src = data.last_post_source ? ` · ${data.last_post_source}` : '';
                lastEl.textContent = formatLogTime(data.last_post_ts) + src;
                if (lastIcon) {
                    lastIcon.className = 'dash-check ok';
                    lastIcon.textContent = '✓';
                }
            } else {
                lastEl.textContent = 'нет публикаций';
            }
        }
    } catch (e) { /* silent */ }
}

/* ============================================================
   MODULE DASHBOARDS — единый рендерер трёх модулей
   ============================================================
   Структура (REDESIGN §6.1, EXPERT-PROMPT §5):
     1. Панель здоровья (4 мини-карточки)
     2. Сводные счётчики (4 крупные карточки)
     3. Что требует внимания (alert-карточка, если есть проблемы)
     4. Последние события (5 строк) + Топ-источники (только posts)
     5. Следующее действие (CTA)

   Три модуля — три разных renderer'а, потому что наборы полей разные.
   Источник истины — /api/stats/posts.
   ============================================================ */
async function renderModuleDashboard(module) {
    if (module !== 'posts') return;

    let stats = {};
    try {
        const res = await fetch('/api/stats/posts');
        if (res.ok) stats = await res.json();
    } catch (e) { /* best-effort dashboard refresh */ }

    let logs = [];
    try {
        const response = await fetch('/api/logs?module=posts&limit=10');
        if (response.ok) logs = await response.json();
    } catch (e) { /* best-effort dashboard refresh */ }

    renderPostsDashboard(stats, logs);
}

/* ============================================================
   RENDER MODULE HEALTH FOR ROUTE — определить модуль и применить
   ============================================================ */
async function renderModuleHealthForRoute(route) {
    let module = null;
    if (route.startsWith('posts/')) module = 'posts';
    if (!module) return;

    // Проверяем, что на текущей странице есть .mod-health для этого модуля
    const activeRoute = document.querySelector('.route.active');
    if (!activeRoute) return;
    const healthPanels = activeRoute.querySelectorAll('.mod-health');
    if (!healthPanels.length) return;

    // Подтягиваем stats для модуля (best-effort, GET — без проверки лицензии)
    let stats = {};
    try {
        const url = module === 'posts' ? '/api/stats/posts' : null;
        if (url) {
            const res = await fetch(url);
            if (res.ok) stats = await res.json();
        }
    } catch (e) { /* silent */ }

    // Применяем к каждой health-панели в текущем роуте
    healthPanels.forEach(panel => {
        // Сохраняем ID модуля в data-module, чтобы _setHealthItem находил элементы
        panel.dataset.module = module;
        renderModuleHealth(module, stats);
    });
}

/* ============================================================
   RENDER MODULE HEALTH — для любой страницы модуля
   ============================================================
   REDESIGN §6.2: панель здоровья модуля в верхней части каждой
   страницы модуля (не только на странице обзора). Эта функция
   заполняет 4 мини-карточки (bot / ai / proxy / sources) в
   `.mod-health` внутри секции текущего роута. Если на странице
   нет health-панели с ожидаемыми data-h, ничего не делает.
   ============================================================ */
function renderModuleHealth(module, stats) {
    if (!stats || module !== 'posts') return;

    if (stats.bot_running) _setHealthItem('bot', 'Работает', 'парсинг идёт', 'ok');
    else _setHealthItem('bot', 'Остановлен', 'нажмите кнопку запуска в шапке', 'warn');

    if (stats.ai_status === 'error') _setHealthItem('ai', `${stats.ai_mode || '—'}`, 'ошибка за последний час', 'err');
    else if (stats.ai_model) _setHealthItem('ai', stats.ai_mode || '—', stats.ai_model, 'ok');
    else _setHealthItem('ai', 'не настроен', 'модель не указана', 'warn');

    if (stats.proxy_status) applyProxyStatusUI(stats.proxy_status);
    const proxyDisplay = getProxyDisplayStatus(_dashCache.settings || {});
    _setHealthItem('proxy', proxyDisplay.value, proxyDisplay.hint, proxyDisplay.state);

    if ((stats.sources_count || 0) > 0) {
        _setHealthItem('sources', `${stats.sources_count} каналов`, `${stats.active_sources_24h || 0} активных за 24ч`, 'ok');
    } else {
        _setHealthItem('sources', 'не заданы', 'добавьте в настройках парсинга', 'err');
    }
}

function _setHealthItem(name, value, hint, state) {
    const el = document.querySelector(`.mod-health-item[data-h="${name}"]`);
    if (!el) return;
    el.dataset.state = state || 'ok';
    const v = el.querySelector('[data-hv]'); if (v) v.textContent = value;
    const h = el.querySelector('[data-hh]'); if (h) h.textContent = hint || '—';
}
function _setCounter(name, value, hint, state) {
    const el = document.querySelector(`.dash-stat[data-stat="${name}"]`);
    if (!el) return;
    el.dataset.state = state || 'ok';
    const v = document.getElementById(name.replace(/^./, c => 'p' + c[0].toUpperCase() + c.slice(1).replace(/-./g, x => x[1].toUpperCase())));
    // Прямой ID надёжнее — ищем в element
    const idMap = {
        'published-today': ['pPubToday', 'pPubTodayHint'],
        'published-24h':   ['pPub24h',   'pPub24hHint'],
        'successes-24h':   ['pSucc24h',  'pSucc24hHint'],
        'errors-24h':      ['pErr24h',   'pErr24hHint'],
    };
    if (idMap[name]) {
        const [vid, hid] = idMap[name];
        const vEl = document.getElementById(vid);
        const hEl = document.getElementById(hid);
        if (vEl) vEl.textContent = value;
        if (hEl) hEl.textContent = hint || '—';
    }
}
function _setNextAction(title, desc, btnLabel, onClick) {
    const t = document.getElementById('postsNextTitle');
    const d = document.getElementById('postsNextDesc');
    const b = document.getElementById('postsNextBtn');
    if (t) t.textContent = title;
    if (d) d.textContent = desc;
    if (b) {
        b.textContent = btnLabel + ' →';
        b.onclick = onClick;
    }
}
function _setAlerts(alerts) {
    const wrap = document.getElementById('postsAlerts');
    const body = document.getElementById('postsAlertsBody');
    const desc = document.getElementById('postsAlertsDesc');
    if (!wrap || !body) return;
    if (!alerts || !alerts.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    if (desc) desc.textContent = alerts.length === 1 ? 'Один пункт требует вашего внимания.' : `${alerts.length} пункта требуют вашего внимания.`;
    body.innerHTML = alerts.map(a => {
        const cls = a.severity || 'warn';
        const icon = cls === 'danger'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
            : cls === 'success'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        return `<div class="callout ${cls}" style="margin-bottom: 8px;">
            <div class="callout-icon">${icon}</div>
            <div class="callout-body">
                <p class="callout-title">${escapeHtml(a.title)}</p>
                <p class="callout-text">${escapeHtml(a.text)}</p>
            </div>
            ${a.action ? `<button class="btn btn-ghost" type="button" onclick="${a.action}">${escapeHtml(a.actionLabel || 'Открыть')}</button>` : ''}
        </div>`;
    }).join('');
}
function _renderRecent(containerId, logs, scope) {
    const wrap = document.getElementById(containerId);
    if (!wrap) return;
    if (!logs || !logs.length) {
        wrap.innerHTML = `<div class="dash-empty-recent">Журнал пуст — событий пока нет.</div>`;
        return;
    }
    wrap.innerHTML = logs.slice(0, 5).map(log => {
        const lvl = classifyLog(log.type);
        const translation = translateLog(log.type) || '';
        return `<div class="dash-recent" data-log-type="${escapeHtml(log.type || '')}" data-log-scope="${scope}" title="${escapeHtml(translation || log.message || '')}">
            <span class="dash-recent-time">${escapeHtml(formatLogTime(log))}</span>
            <span class="dash-recent-type ${lvl}">${escapeHtml(log.type || '?')}</span>
            <span class="dash-recent-msg">${escapeHtml(log.message || '')}</span>
        </div>`;
    }).join('');
    // Клик → лог с фильтром по типу
    wrap.querySelectorAll('.dash-recent').forEach(row => {
        row.addEventListener('click', () => {
            const t = row.dataset.logType || '';
            const sc = row.dataset.logScope || scope;
            if (!t) return;
            // Маппинг типа → фильтр уровня в консоли
            const lvl = classifyLog(t);
            state.logFilters.set(sc, lvl);
            navigate(`${sc}/logs`);
        });
    });
}

function renderPostsDashboard(stats, logs) {
    // 1. Панель здоровья — переиспользуем общий рендерер
    renderModuleHealth('posts', stats);

    // 2. Счётчики
    _setCounter('published-today', String(stats.published_today || 0),
                stats.published_today > 0 ? 'постов сегодня' : 'пока нет публикаций',
                stats.published_today > 0 ? 'ok' : 'warn');
    _setCounter('published-24h',   String(stats.published_24h || 0),
                `всего ${stats.published_total || 0}`, stats.published_24h > 0 ? 'ok' : 'warn');
    _setCounter('successes-24h',   String(stats.successes_24h || 0),
                'успешных операций', stats.successes_24h > 0 ? 'ok' : 'warn');
    _setCounter('errors-24h',      String(stats.errors_24h || 0),
                stats.errors_24h > 0 ? 'есть ошибки — см. журнал' : 'чисто',
                stats.errors_24h > 0 ? 'err' : 'ok');

    // 3. Алерты
    const alerts = [];
    if (stats.errors_24h > 0 && stats.last_error_code) {
        alerts.push({
            severity: 'danger',
            title: `${stats.last_error_code} — последняя ошибка`,
            text: stats.last_error_msg || 'Откройте журнал для подробностей.',
            action: "navigate('posts/logs')",
            actionLabel: 'Открыть журнал',
        });
    }
    if (stats.ai_status === 'error') {
        alerts.push({
            severity: 'warn',
            title: 'AI недоступен',
            text: `За последний час были ошибки ${aiModeIsLocal(stats.ai_mode) ? 'LM Studio / Ollama' : 'облачного API'}. Бот будет пропускать рерайт.`,
            action: "navigate('posts/ai')",
            actionLabel: 'Проверить AI',
        });
    }
    if ((stats.sources_count || 0) === 0) {
        alerts.push({
            severity: 'warn',
            title: 'Нет источников',
            text: 'Бот не знает, откуда брать посты. Добавьте каналы-доноры.',
            action: "navigate('posts/parsing')",
            actionLabel: 'Добавить источники',
        });
    }
    const settings = _dashCache.settings || {};
    if (settings.use_proxy === '1') {
        const proxyDisplay = getProxyDisplayStatus(settings);
        if (proxyDisplay.state === 'err') {
            alerts.push({
                severity: 'danger',
                title: 'Прокси недоступен',
                text: proxyDisplay.hint,
                action: "navigate('posts/proxy')",
                actionLabel: 'Настройки прокси',
            });
        } else if (proxyDisplay.state === 'warn' && _proxyStatusCache.state === 'no_host') {
            alerts.push({
                severity: 'warn',
                title: 'Прокси не настроен',
                text: 'Включён режим прокси, но хост не указан.',
                action: "navigate('posts/proxy')",
                actionLabel: 'Заполнить прокси',
            });
        }
    }
    _setAlerts(alerts);

    // 4. Последние события
    _renderRecent('postsRecent', logs, 'posts');

    // 5. Топ-источники
    const topWrap = document.getElementById('postsTopSources');
    if (topWrap) {
        const top = stats.top_sources_24h || [];
        if (!top.length) {
            topWrap.innerHTML = `<div class="dash-empty-recent">За последние 24 часа публикаций не было.</div>`;
        } else {
            const max = Math.max(...top.map(t => t.count), 1);
            topWrap.innerHTML = top.map(t => {
                const pct = Math.max(8, Math.round((t.count / max) * 100));
                return `<div class="top-source-row">
                    <div class="top-source-name">${escapeHtml(t.source)}</div>
                    <div class="top-source-bar"><div class="top-source-fill" style="width: ${pct}%;"></div></div>
                    <div class="top-source-count">${t.count}</div>
                </div>`;
            }).join('');
        }
    }

    // 6. Следующее действие
    const nextByPriority =
        (stats.errors_24h > 0)        ? { title: 'Разобрать ошибки', desc: 'За 24ч зафиксированы ошибки — откройте журнал.', label: 'Открыть журнал', on: () => navigate('posts/logs') } :
        (stats.sources_count === 0)   ? { title: 'Добавить источники', desc: 'Без каналов-доноров парсер не знает, что публиковать.', label: 'Добавить', on: () => navigate('posts/parsing') } :
        (!stats.bot_running)          ? { title: 'Запустить модули', desc: 'Всё настроено. Можно запускать активные модули.', label: 'Запустить', on: () => toggleBot() } :
        (stats.ai_status === 'error') ? { title: 'Проверить AI', desc: 'Нейросеть недоступна — посты не рерайтятся.', label: 'Проверить', on: () => navigate('posts/ai') } :
                                        { title: 'Следить за консолью', desc: 'Система работает. Здесь видно, что происходит.', label: 'Открыть консоль', on: () => navigate('posts/logs') };
    _setNextAction(nextByPriority.title, nextByPriority.desc, nextByPriority.label, nextByPriority.on);

    // Бейдж модуля
    const badge = document.getElementById('postsDashModuleState');
    if (badge) {
        if (stats.bot_running) { badge.textContent = 'Работает';   badge.className = 'badge badge-success'; }
        else if ((stats.published_total || 0) > 0) { badge.textContent = 'Готов';   badge.className = 'badge badge-info'; }
        else { badge.textContent = 'Не активен'; badge.className = 'badge badge-warn'; }
    }
}

/* ============================================================
   MODULE-SPECIFIC DASHBOARDS — заполнение полей из настроек
   ============================================================ */

/* ============================================================
   ============================================================ */
function renderProxyReadonly(settings) {
    const map = {
        use_proxy:   settings.use_proxy === '1' ? 'Включён' : 'Выключен',
        proxy_type:  (settings.proxy_type || 'HTTP').toUpperCase(),
        proxy_host:  settings.proxy_host || '— не задан —',
        proxy_port:  settings.proxy_port || '—',
    };
    document.querySelectorAll('[data-readonly]').forEach(el => {
        const k = el.dataset.readonly;
        if (k in map) el.textContent = map[k];
    });
}

/* ============================================================
   HUMAN TRANSLATIONS — для известных кодов ошибок
   ============================================================ */
const LOG_TRANSLATIONS = {
    'ERR-201': 'Telegram API: проверьте tg_api_id / tg_api_hash.',
    'ERR-202': 'Невалидный токен бота. Запросите новый у @BotFather.',
    'ERR-203': 'Сессия Telegram аннулирована. Перезапустите парсер.',
    'ERR-204': 'Telegram выдал FloodWait — бот сам подождёт.',
    'ERR-205': 'Бот не добавлен в канал или не имеет прав публикации.',
    'ERR-207': 'Повреждён курсор источника. Автоматический сброс запрещён, чтобы не потерять историю.',
    'ERR-301': 'Прокси не отвечает. Проверьте хост/порт или замените прокси.',
    'ERR-408': 'Telegram отклонил публикацию. Исправьте права/получателя; запись оставлена для повтора.',
    'ERR-DB-501': 'Публикация уже отправлена, но SQLite не записала её. Проверьте канал перед перезапуском.',
    'OK-501': 'Публикация успешно отправлена и записана в SQLite.',
    'WARN-400': 'Не настроен целевой канал для этой темы. Пост ожидает настройки маршрута.',
    'WARN-404': 'AI вернул ответ неправильного формата. Пост оставлен для повторной обработки.',
    'WARN-405': 'AI недоступен. Пост оставлен для повторной обработки.',
    'WARN-406': 'Временная ошибка отправки Telegram. Пост ожидает повторной попытки.',
    'WARN-407': 'Медиа отправлены, но дополнительный текст не доставлен. Проверьте сообщение в канале.',
    'WARN-500': 'Текст сокращён до лимита Telegram.',
    'WARN-501': 'Вложение не скачано. Пост оставлен для повторной попытки.',
    'ERR-302': 'Неверный логин или пароль прокси.',
    'ERR-401': 'LM Studio / Ollama недоступны. Запустите локальный сервер.',
    'ERR-402': 'Модель не загружена в LM Studio / Ollama.',
    'ERR-403': 'Облачный API недоступен. Проверьте URL, ключ и баланс.',
    'BEHAVE-BOOT': 'Модуль поведения запущен.',
    'BEHAVE-STOP': 'Модуль поведения остановлен.',
    'BEHAVE-SYS': 'Системное действие модуля поведения.',
    'REV-BOOT': 'Модуль отзывов запущен.',
    'REV-STOP': 'Модуль отзывов остановлен.',
    'REV-SYS': 'Системное действие модуля отзывов.',
    'REV-ACC': 'Операция с аккаунтами модуля отзывов.',
    'REV-QUEUE': 'Пополнение очереди отзывов.',
    'REV-OK': 'Отзыв успешно опубликован.',
    'REV-WARN': 'Предупреждение при публикации отзыва.',
    'REV-ERR': 'Ошибка обработки очереди отзывов.',
    'REV-IDLE': 'Достигнут дневной лимит отзывов.',
};

function translateLog(type) {
    if (!type) return '';
    return LOG_TRANSLATIONS[type] || '';
}

/* ============================================================
   WORKFLOW STEPPER
   ============================================================ */
function setupWorkflow() {
    const steps = Array.from(document.querySelectorAll('.workflow-step'));
    if (!steps.length) return;
    steps.forEach(step => {
        step.addEventListener('click', () => {
            const target = step.dataset.stepTarget || step.dataset.route;
            if (target) navigate(target);
        });
    });
    syncWorkflowStep();
}

function syncWorkflowStep() {
    const steps = Array.from(document.querySelectorAll('.workflow-step'));
    if (!steps.length) return;
    const route = state.currentRoute;
    steps.forEach(step => {
        const target = step.dataset.stepTarget || step.dataset.route;
        step.classList.toggle('current', target === route);
    });
}

/* ============================================================
   СЕРВЕРНЫЕ ЧАСЫ (сайдбар) И ФОРМАТ ВРЕМЕНИ ЛОГОВ
   ============================================================ */
function formatDisplayDateTime(d) {
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
}


function formatLogTime(tsOrLog) {
    if (tsOrLog && typeof tsOrLog === 'object') {
        if (tsOrLog.timestamp_display) return tsOrLog.timestamp_display;
        tsOrLog = tsOrLog.timestamp;
    }
    if (!tsOrLog) return '—';
    const raw = String(tsOrLog).trim();
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (match) {
        const d = new Date(
            Number(match[1]),
            Number(match[2]) - 1,
            Number(match[3]),
            Number(match[4]),
            Number(match[5]),
            Number(match[6] || 0),
        );
        if (!isNaN(d.getTime())) return formatDisplayDateTime(d);
    }
    return String(tsOrLog);
}

/* ============================================================
   КОНСОЛЬ (ЛОГИ)
   ============================================================ */
function classifyLog(type) {
    const t = (type || '').toUpperCase();
    if (t.startsWith('ERR-') || t === 'SYS-ERR') return 'error';
    if (t.startsWith('WARN-')) return 'warn';
    if (t.startsWith('SYS-') || t.startsWith('OK-') || t === 'SUCCESS' || t === 'OK') return 'success';
    return 'info';
}

function renderLogs(scope, logs, level = 'all') {
    const wrap = document.querySelector(`.console-wrap[data-console][data-scope="${scope}"]`);
    if (!wrap) return;
    const win = wrap.querySelector('[data-console-window]');
    const meta = wrap.querySelector('[data-console-meta]');
    if (!win) return;

    const filtered = level === 'all' ? logs : logs.filter(l => classifyLog(l.type) === level);

    if (!filtered.length) {
        win.innerHTML = `
            <div class="log-empty">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <div>${logs.length ? 'Нет событий этого уровня' : 'Журнал пуст — событий пока нет'}</div>
            </div>
        `;
        if (meta) meta.textContent = logs.length ? `Показано: 0 из ${logs.length} записей` : '';
        return;
    }

    const frag = document.createDocumentFragment();
    for (const log of [...filtered].reverse()) {
        const line = document.createElement('div');
        line.className = 'log-line';
        const lvl = classifyLog(log.type);
        const time = document.createElement('span');
        time.className = 'log-time';
        time.textContent = `[${formatLogTime(log)}] `;
        const tag = document.createElement('span');
        tag.className = `log-type ${lvl}`;
        tag.textContent = (log.type || '?');
        const msg = document.createElement('span');
        msg.className = 'log-msg';
        msg.textContent = ' ' + (log.message || '');
        line.appendChild(time);
        line.appendChild(tag);
        line.appendChild(msg);
        // Человекочитаемая подсказка для известных кодов
        const translation = translateLog(log.type);
        if (translation) {
            const hint = document.createElement('span');
            hint.className = 'log-hint';
            hint.innerHTML = `<strong>Что это:</strong> ${escapeHtml(translation)} · решение в модуле 1.5 FAQ.`;
            line.appendChild(hint);
        } else if (lvl === 'error') {
            const hint = document.createElement('span');
            hint.className = 'log-hint';
            hint.textContent = 'Решение — в модуле FAQ. Если не справитесь — обратитесь к разработчику.';
            line.appendChild(hint);
        }
        frag.appendChild(line);
    }
    win.innerHTML = '';
    win.appendChild(frag);
    if (meta) meta.textContent = `Показано: ${filtered.length} из ${logs.length} записей`;
    win.scrollTop = win.scrollHeight;
}

function setupConsoleFilters() {
    document.querySelectorAll('[data-console]').forEach(wrap => {
        const scope = wrap.dataset.scope;
        state.logFilters.set(scope, 'all');
        wrap.querySelectorAll('.console-filter').forEach(btn => {
            btn.addEventListener('click', () => {
                wrap.querySelectorAll('.console-filter').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const level = btn.dataset.level;
                state.logFilters.set(scope, level);
                const logs = state.lastLogs.get(scope) || [];
                renderLogs(scope, logs, level);
            });
        });
    });
}

async function fetchLogs() {
    // Изоляция скоупа: каждая активная консоль получает СВОИ логи через ?module=.
    // Без этого все три модуля рисовали бы один и тот же глобальный журнал.
    const active = Array.from(document.querySelectorAll('[data-console]'))
        .filter(w => w.closest('.route').classList.contains('active'));
    if (!active.length) return;

    await Promise.all(active.map(async wrap => {
        const scope = wrap.dataset.scope;
        try {
            const res = await fetch(`/api/logs?module=${encodeURIComponent(scope)}&limit=100`);
            const logs = await res.json();
            state.lastLogs.set(scope, logs);
            const level = state.logFilters.get(scope) || 'all';
            renderLogs(scope, logs, level);
        } catch (e) { /* silent */ }
    }));
}

/* ============================================================
   FAQ SEARCH
   ============================================================ */
function setupFaqSearch() {
    document.querySelectorAll('[data-faq-search]').forEach(input => {
        const route = input.closest('.route');
        if (!route) return;
        const items = route.querySelectorAll('.faq-item');
        const empty = route.querySelector('[data-faq-empty]');
        const emptyQ = route.querySelector('[data-faq-empty-q]');

        input.addEventListener('input', () => {
            const q = input.value.trim().toLowerCase();
            let visible = 0;
            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                const match = !q || text.includes(q);
                item.style.display = match ? '' : 'none';
                if (match) visible++;
            });
            if (empty) {
                empty.style.display = (q && visible === 0) ? '' : 'none';
                if (emptyQ) emptyQ.textContent = input.value;
            }
        });
    });
}

/* ============================================================
   ИНИЦИАЛИЗАЦИЯ
   ============================================================ */
function setupSaveButtons() {
    document.querySelectorAll('[data-save]').forEach(btn => {
        btn.addEventListener('click', () => saveSettings(btn.dataset.save, btn));
    });
    const addBtn = document.getElementById('addCategory');
    if (addBtn) addBtn.addEventListener('click', () => addCategoryRow());

    // proxy status updates when fields change
    const proxyFields = ['use_proxy', 'proxy_host'];
    proxyFields.forEach(name => {
        const el = document.querySelector(`[name="${name}"]`);
        if (el) el.addEventListener('change', refreshProxyStatus);
    });
}

function setupBotButton() {
    const mainBtn = document.getElementById('botToggleBtn');
    const arrowBtn = document.getElementById('botSplitArrow');
    const splitWrap = document.getElementById('botSplitBtn');
    const splitMenu = document.getElementById('botSplitMenu');
    const startAll = document.getElementById('menuStartAll');
    const stopAll = document.getElementById('menuStopAll');

    if (mainBtn) {
        mainBtn.addEventListener('click', (e) => {
            e.preventDefault();
            toggleBot();
        });
    }

    if (arrowBtn) {
        arrowBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleSplitMenu();
        });
    }

    if (splitMenu) {
        splitMenu.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    }

    if (startAll) {
        startAll.addEventListener('click', async (e) => {
            e.preventDefault();
            await startAllActiveModules();
            closeSplitMenu();
        });
    }

    if (stopAll) {
        stopAll.addEventListener('click', async (e) => {
            e.preventDefault();
            await stopAllModules();
            closeSplitMenu();
        });
    }

    document.querySelectorAll('[data-module-select]').forEach((checkbox) => {
        checkbox.addEventListener('click', (e) => {
            e.stopPropagation();
        });
        checkbox.addEventListener('change', (e) => {
            e.stopPropagation();
            const moduleId = checkbox.dataset.moduleSelect;
            if (!moduleId) return;
            setModuleSelected(moduleId, checkbox.checked);
            updateSplitMenu();
        });
    });

    document.querySelectorAll('[data-module-check-wrap]').forEach((wrap) => {
        wrap.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    });

    document.querySelectorAll('[data-module-action]').forEach((btn) => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            const moduleId = btn.dataset.moduleAction;
            if (!moduleId) return;
            await handleModuleAction(moduleId);
            closeSplitMenu();
        });
    });

    document.addEventListener('click', (e) => {
        if (!state.splitMenuOpen) return;
        if (splitWrap && !splitWrap.contains(e.target)) closeSplitMenu();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && state.splitMenuOpen) closeSplitMenu();
    });
}

function setupModuleButtons() {
    const postsLocalStart = document.getElementById('postsLocalStartBtn');
    const postsLocalStop = document.getElementById('postsLocalStopBtn');

    if (postsLocalStart) postsLocalStart.addEventListener('click', () => handleModuleAction('posts'));
    if (postsLocalStop) postsLocalStop.addEventListener('click', () => handleModuleAction('posts'));

}

const _pollTimers = { logs: null, bot: null, posts: null, proxy: null };

function stopPollingTimers() {
    Object.keys(_pollTimers).forEach((key) => {
        if (_pollTimers[key]) {
            clearInterval(_pollTimers[key]);
            _pollTimers[key] = null;
        }
    });
}

function startPollingTimers() {
    stopPollingTimers();
    _pollTimers.logs = setInterval(fetchLogs, 2000);
    _pollTimers.bot = setInterval(refreshBotStatus, 5000);
    _pollTimers.posts = setInterval(refreshPostsStats, 15000);
    _pollTimers.proxy = setInterval(fetchProxyStatus, 10000);
}

window.addEventListener('DOMContentLoaded', () => {
    loadState();
    applyShellState();
    setupTheme();
    setupSidebar();
    setupSidebarCollapse();
    setupSaveButtons();
    setupBotButton();
    setupModuleButtons();
    setupConsoleFilters();
    setupFaqSearch();
    setupWorkflow();
    setupProxyWizard();
    setupProxyPillNavigation();
    setupDashboard();
    toggleAIFields();
    if (typeof updateProxyFields === 'function') updateProxyFields();

    const initialRoute = state.currentRoute || DEFAULT_ROUTE;
    navigate(initialRoute);

    refreshBotStatus();
    loadSettings().then(() => {
        ['form-ai', 'form-parsing', 'form-proxy'].forEach(renderSaveMeta);
    });
    refreshPostsStats();
    fetchProxyStatus();
    startSaveMetaTicker();
    startPollingTimers();
});

window.addEventListener('beforeunload', () => {
    stopPollingTimers();
    if (_saveMeta.timer) {
        clearInterval(_saveMeta.timer);
        _saveMeta.timer = null;
    }
});
