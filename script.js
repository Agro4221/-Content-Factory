(() => {
  'use strict';

  const STORAGE_KEY = 'roadmap:v2:snapshot';
  const THEME_KEY = 'roadmap-theme';

  const state = {
    addRowTargetListId: null,
    addKpiTargetListId: null,
    addTaskTarget: { month: null, week: null, listId: null }
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function uid(prefix = 'id') {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function openModal(id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.classList.add('active');
    m.setAttribute('aria-hidden', 'false');
    document.body.dataset.modalOpen = 'true';
    document.body.style.overflow = 'hidden';

    const focusTarget =
      m.querySelector('[data-autofocus="true"]') ||
      m.querySelector('input, textarea, select, button');
    if (focusTarget) {
      focusTarget.focus();
    }
  }

  function closeModal(id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.classList.remove('active');
    m.setAttribute('aria-hidden', 'true');
    // если все модалки закрыты — возвращаем прокрутку
    const anyOpen = document.querySelector('.modal.active');
    if (!anyOpen) {
      delete document.body.dataset.modalOpen;
      document.body.style.overflow = '';
    }
  }

  function showSaved() {
    const el = $('#saveStatus');
    if (!el) return;
    el.style.opacity = '1';
    el.style.transition = 'opacity 0.2s';
    const span = el.querySelector('span');
    if (span) {
      span.textContent = 'Сохранено';
    }
    window.clearTimeout(showSaved._t);
    showSaved._t = window.setTimeout(() => {
      el.style.opacity = '0.9';
    }, 1800);
  }

  function snapshot() {
    const root = $('.container');
    if (!root) return;

    const data = {
      theme: document.documentElement.getAttribute('data-theme') || 'light',
      html: root.innerHTML
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    showSaved();
  }

  function restore() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;

    try {
      const data = JSON.parse(raw);
      const root = $('.container');
      if (root && typeof data.html === 'string') root.innerHTML = data.html;
      if (data.theme) document.documentElement.setAttribute('data-theme', data.theme);
    } catch (e) {
      // ignore
    }
  }

  function setThemeFromSaved() {
    const saved = localStorage.getItem(THEME_KEY);
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (prefersDark ? 'dark' : 'light');

    document.documentElement.setAttribute('data-theme', theme);

    const toggle = $('#themeToggle');
    if (toggle) {
      const icon = $('i', toggle);
      const label = $('.control-label', toggle);
      if (theme === 'dark') {
        if (icon) icon.className = 'fas fa-sun';
        if (label) label.textContent = 'Светлая тема';
      } else {
        if (icon) icon.className = 'fas fa-moon';
        if (label) label.textContent = 'Тёмная тема';
      }
    }
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem(THEME_KEY, next);
    setThemeFromSaved();
    snapshot();
  }

  function enableEditing(el) {
    if (!el) return;
    el.setAttribute('contenteditable', 'true');
    el.classList.add('editing');
    el.focus();

    if (!el.dataset.original) el.dataset.original = el.innerHTML;

    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }

    const onKey = (e) => {
      if (e.key === 'Escape') {
        el.innerHTML = el.dataset.original || '';
        el.blur();
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        el.blur();
      }
    };

    const onBlur = () => {
      el.removeEventListener('keydown', onKey);
      el.setAttribute('contenteditable', 'false');
      el.classList.remove('editing');
      if (el.dataset.original !== el.innerHTML) {
        el.dataset.original = el.innerHTML;
        snapshot();
      }
    };

    el.addEventListener('blur', onBlur, { once: true });
    el.addEventListener('keydown', onKey);
  }

  function addComponentRow(listId, text) {
    const list = document.getElementById(listId);
    if (!list) return;

    const rowId = uid('row');
    const row = document.createElement('div');
    row.className = 'row';
    row.dataset.rowId = rowId;
    row.innerHTML = `
      <button class="btn-del" title="Удалить строку" aria-label="Удалить строку"><i class="fas fa-minus"></i></button>
      <div class="row-body" data-editable="true">${escapeHtml(text).replace(/\n/g, '<br>')}</div>
    `;

    list.appendChild(row);
    snapshot();
  }

  function addKpiRow(listId, name, value) {
    const list = document.getElementById(listId);
    if (!list) return;

    const rowId = uid('kpi');
    const row = document.createElement('div');
    row.className = 'kpi-row';
    row.dataset.rowId = rowId;
    row.innerHTML = `
      <button class="btn-del" title="Удалить KPI" aria-label="Удалить KPI"><i class="fas fa-minus"></i></button>
      <label class="kpi-line" data-editable="true"><span class="kpi-name"><strong>${escapeHtml(name)}</strong></span> — <span class="kpi-value-inline">${escapeHtml(value)}</span></label>
    `;

    list.appendChild(row);
    snapshot();
  }

  function addTaskRow(listId, title, description, responsible) {
    const list = document.getElementById(listId);
    if (!list) return;

    const colorVar =
      responsible === 'assistant' ? 'var(--secondary-color)' :
      responsible === 'team' ? 'var(--success-color)' :
      'var(--primary-color)';

    const row = document.createElement('div');
    row.className = 'row task-row';
    row.dataset.rowId = uid('task');
    row.innerHTML = `
      <button class="btn-del" title="Удалить задачу" aria-label="Удалить задачу"><i class="fas fa-minus"></i></button>
      <div class="row-body" data-editable="true">
        <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${colorVar}; margin-right:8px; transform: translateY(-1px);"></span>
        <strong>${escapeHtml(title)}:</strong> ${escapeHtml(description)}
      </div>
    `;

    list.appendChild(row);
    snapshot();
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function setupNavActiveOnScroll() {
    const links = $$('.nav-link');
    const sections = $$('#architecture, #kpi, #phase1, #phase2, #phase3, #phase4, #phase5');

    function setActive(id) {
      links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${id}`));
    }

    window.addEventListener('scroll', () => {
      const y = window.scrollY + 120;
      let current = null;

      for (const s of sections) {
        if (s.offsetTop <= y) current = s.id;
      }
      if (current) setActive(current);
    });
  }

  function exportHTML() {
    const container = $('.container');
    if (!container) return;

    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const cssLink = $('link[href="styles.css"]');
    const cssHref = cssLink ? cssLink.href : null;

    const build = (cssText) => {
      const html = `<!DOCTYPE html>
<html lang="ru" data-theme="${theme}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Riverside Cabanas — Дорожная карта</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" />
<style>${cssText || ''}</style>
</head>
<body>
<div class="container">${container.innerHTML}</div>
</body>
</html>`;

      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Riverside-Cabanas-Roadmap-${new Date().toISOString().slice(0, 10)}.html`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    };

    if (cssHref) {
      fetch(cssHref).then(r => r.text()).then(build).catch(() => build(''));
    } else {
      build('');
    }
  }

  function resetAll() {
    if (!confirm('Сбросить все изменения и очистить сохранения?')) return;
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(THEME_KEY);
    location.reload();
  }

  function wireEvents() {
    document.addEventListener('click', (e) => {
      const themeToggle = e.target.closest('#themeToggle');
      if (themeToggle) {
        toggleTheme();
        return;
      }

      const nav = e.target.closest('.nav-link');
      if (nav) {
        e.preventDefault();
        const id = nav.getAttribute('href').slice(1);
        const s = document.getElementById(id);
        if (s) s.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }

      const del = e.target.closest('.btn-del');
      if (del) {
        const row = del.closest('.row, .kpi-row');
        if (row) {
          row.remove();
          snapshot();
        }
        return;
      }

      const addBtn = e.target.closest('[data-add-to]');
      if (addBtn) {
        state.addRowTargetListId = addBtn.getAttribute('data-add-to');
        $('#rowText').value = '';
        openModal('addRowModal');
        return;
      }

      const addKpiBtn = e.target.closest('[data-add-kpi-to]');
      if (addKpiBtn) {
        state.addKpiTargetListId = addKpiBtn.getAttribute('data-add-kpi-to');
        $('#kpiNameInput').value = '';
        $('#kpiValueInput').value = '';
        openModal('addKpiModal');
        return;
      }

      const addTaskBtn = e.target.closest('.btn-add-task');
      if (addTaskBtn) {
        const month = addTaskBtn.getAttribute('data-month');
        const week = addTaskBtn.getAttribute('data-week');

        const map = {
          february: { 1: 'tasks-feb-1', 2: 'tasks-feb-2', 3: 'tasks-feb-3', 4: 'tasks-feb-4' },
          march: { 5: 'tasks-mar-5', 6: 'tasks-mar-6', 7: 'tasks-mar-7', 8: 'tasks-mar-8' },
          april: { 9: 'tasks-apr-9', 10: 'tasks-apr-10', 11: 'tasks-apr-11', 12: 'tasks-apr-12', 13: 'tasks-apr-13' },
          may: { 14: 'tasks-may-14', 15: 'tasks-may-15', 16: 'tasks-may-16', 17: 'tasks-may-17' }
        };

        const listId = map[month] && map[month][week] ? map[month][week] : null;
        if (!listId) return;

        state.addTaskTarget = { month, week, listId };
        $('#taskTitle').value = '';
        $('#taskDescription').value = '';
        $('#taskResponsible').value = 'manager';
        openModal('taskModal');
        return;
      }

      const close = e.target.closest('[data-close-modal]');
      if (close) {
        closeModal(close.getAttribute('data-close-modal'));
        return;
      }

      const backdrop = e.target.classList.contains('modal') ? e.target : null;
      if (backdrop && backdrop.id) {
        closeModal(backdrop.id);
        return;
      }

      const printBtn = e.target.closest('#printBtn');
      if (printBtn) {
        window.print();
        return;
      }

      const exportBtn = e.target.closest('#exportBtn');
      if (exportBtn) {
        exportHTML();
        return;
      }

      const resetBtn = e.target.closest('#resetBtn');
      if (resetBtn) {
        resetAll();
        return;
      }

      const saveRow = e.target.closest('#saveRowBtn');
      if (saveRow) {
        const txt = ($('#rowText').value || '').trim();
        if (!txt) return;
        addComponentRow(state.addRowTargetListId, txt);
        closeModal('addRowModal');
        return;
      }

      const saveKpi = e.target.closest('#saveKpiBtn');
      if (saveKpi) {
        const name = ($('#kpiNameInput').value || '').trim();
        const value = ($('#kpiValueInput').value || '').trim();
        if (!name || !value) return;
        addKpiRow(state.addKpiTargetListId, name, value);
        closeModal('addKpiModal');
        return;
      }

      const saveTask = e.target.closest('#saveTaskBtn');
      if (saveTask) {
        const title = ($('#taskTitle').value || '').trim();
        const desc = ($('#taskDescription').value || '').trim();
        const resp = ($('#taskResponsible').value || 'manager').trim();
        if (!title || !desc) return;
        addTaskRow(state.addTaskTarget.listId, title, desc, resp);
        closeModal('taskModal');
        return;
      }
    });

    document.addEventListener('dblclick', (e) => {
      const editable = e.target.closest('[data-editable="true"]');
      if (!editable) return;
      enableEditing(editable);
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const activeModal = document.querySelector('.modal.active');
        if (activeModal && activeModal.id) closeModal(activeModal.id);
      }
    });

    document.addEventListener('input', (e) => {
      const editable = e.target.closest('[contenteditable="true"]');
      if (editable) {
        // не спамим snapshot на каждый input; сохранение на blur
      }
    });

    window.addEventListener('beforeunload', () => {
      snapshot();
    });
  }

  function init() {
    restore();
    setThemeFromSaved();
    setupNavActiveOnScroll();
    wireEvents();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
