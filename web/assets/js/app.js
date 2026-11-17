/* ==========================================================================
   LifeOps — app.js (web interface)
   --------------------------------------------------------------------------
   Talks to the FastAPI app on the same address. Everything from the server
   is shown with textContent only (never innerHTML), so stored text can never
   become page code. Changes send the session's CSRF token; new items send an
   Idempotency-Key so a double tap or a retry never creates duplicates.

   TABLE OF CONTENTS
     01  Settings & elements
     02  Helpers (elements, dates, menus, toast, announcements)
     03  Talking to the server
     04  Login, logout, session
     05  Navigation between views (#/today … #/review/ID)
     06  Today
     07  Inbox (notes, uploads, suggestions)
     08  Review (check a proposal, save a record)
     09  Records
     10  Tasks (list, dialog, actions)
     11  Settings
     12  Theme
     13  Init
   ========================================================================== */
(function () {
  'use strict';

  /* ==========================================================================
     01  SETTINGS & ELEMENTS
     ========================================================================== */
  const THEME_KEY = 'lifeops-theme'; // also read by theme-init.js
  const THEMES = ['auto', 'light', 'dark'];
  const TOAST_MS = 2600;
  const VIEWS = ['today', 'inbox', 'tasks', 'records', 'settings', 'review', 'activity'];
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // same as MAX_FILE_BYTES in app/documents.py
  const UPLOAD_TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/heic', 'image/heif'];
  const UPLOAD_EXTENSIONS = /\.(pdf|jpe?g|png|heic|heif)$/i;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const CATEGORY_NAMES = {
    bills: 'Bills',
    warranty: 'Warranty',
    renewal: 'Renewal',
    home: 'Home',
    health: 'Health',
    vehicle: 'Vehicle',
    other: 'Other',
  };
  const DOC_TYPE_NAMES = {
    bill: 'Bill',
    warranty: 'Warranty',
    renewal: 'Renewal',
    receipt: 'Receipt',
    other: 'Other',
  };
  const DOC_TO_CATEGORY = {
    bill: 'bills',
    warranty: 'warranty',
    renewal: 'renewal',
    receipt: 'bills',
    other: 'other',
  };
  const CONFIDENCE = { high: 'sure', medium: 'likely', low: 'unsure' };
  const GROUPS = [
    { key: 'overdue', title: 'Overdue', empty: '' },
    { key: 'due_soon', title: 'Due in the next 7 days', empty: 'Nothing due this week. Nice.' },
    { key: 'later', title: 'Later or no date', empty: '' },
    { key: 'snoozed', title: 'Snoozed', empty: '' },
    { key: 'done', title: 'Done recently', empty: '' },
  ];
  // Review form: the fields the user confirms, in order
  const REVIEW_FIELDS = [
    { name: 'doc_type', label: 'What is it?', type: 'select' },
    { name: 'title', label: 'Title', type: 'text', max: 200 },
    { name: 'supplier', label: 'From (supplier / company)', type: 'text', max: 200 },
    { name: 'amount', label: 'Amount', type: 'text', max: 20, inputmode: 'decimal' },
    { name: 'currency', label: 'Currency', type: 'currency' },
    { name: 'reference', label: 'Reference / account no.', type: 'text', max: 100 },
    { name: 'due_date', label: 'Due date', type: 'date' },
    { name: 'expiry_date', label: 'Expires / renew by', type: 'date' },
  ];

  const $ = (id) => document.getElementById(id);
  const els = {
    loginView: $('loginView'),
    loginForm: $('loginForm'),
    loginEmail: $('loginEmail'),
    loginPassword: $('loginPassword'),
    loginError: $('loginError'),
    setupForm: $('setupForm'),
    setupEmail: $('setupEmail'),
    setupPassword: $('setupPassword'),
    setupPassword2: $('setupPassword2'),
    setupError: $('setupError'),
    codeForm: $('codeForm'),
    loginCode: $('loginCode'),
    codeError: $('codeError'),
    codeBack: $('codeBack'),
    twoStepState: $('twoStepState'),
    twoStepBtn: $('twoStepBtn'),
    twoStepDialog: $('twoStepDialog'),
    twoStepForm: $('twoStepForm'),
    twoStepTitle: $('twoStepTitle'),
    twoStepText: $('twoStepText'),
    twoStepQr: $('twoStepQr'),
    twoStepKey: $('twoStepKey'),
    twoStepPasswordField: $('twoStepPasswordField'),
    twoStepPassword: $('twoStepPassword'),
    twoStepCodeField: $('twoStepCodeField'),
    twoStepCode: $('twoStepCode'),
    twoStepCodes: $('twoStepCodes'),
    twoStepError: $('twoStepError'),
    twoStepSubmit: $('twoStepSubmit'),
    twoStepCancel: $('twoStepCancel'),
    shell: $('shell'),
    main: $('main'),
    inboxBadge: $('inboxBadge'),
    themeBtn: $('themeBtn'),
    todayDate: $('todayDate'),
    todayStats: $('todayStats'),
    billsList: $('billsList'),
    expiringList: $('expiringList'),
    todayGroups: $('todayGroups'),
    captureForm: $('captureForm'),
    captureText: $('captureText'),
    uploadInput: $('uploadInput'),
    inboxList: $('inboxList'),
    taskList: $('taskList'),
    recordList: $('recordList'),
    reviewSource: $('reviewSource'),
    reviewNote: $('reviewNote'),
    reviewForm: $('reviewForm'),
    reviewFields: $('reviewFields'),
    reviewMakeTask: $('reviewMakeTask'),
    reviewTaskFields: $('reviewTaskFields'),
    reviewTaskTitle: $('reviewTaskTitle'),
    reviewTaskDue: $('reviewTaskDue'),
    reviewError: $('reviewError'),
    reviewReject: $('reviewReject'),
    accountEmail: $('accountEmail'),
    timezoneSelect: $('timezoneSelect'),
    logoutBtn: $('logoutBtn'),
    taskDialog: $('taskDialog'),
    taskForm: $('taskForm'),
    taskDialogTitle: $('taskDialogTitle'),
    taskTitle: $('taskTitle'),
    taskDue: $('taskDue'),
    taskCategory: $('taskCategory'),
    taskDetails: $('taskDetails'),
    taskDetailsField: $('taskDetailsField'),
    taskError: $('taskError'),
    confirmDialog: $('confirmDialog'),
    confirmTitle: $('confirmTitle'),
    confirmText: $('confirmText'),
    confirmBtn: $('confirmBtn'),
    activityList: $('activityList'),
    activityMore: $('activityMore'),
    deleteDialog: $('deleteDialog'),
    deleteForm: $('deleteForm'),
    deleteTitle: $('deleteTitle'),
    deleteText: $('deleteText'),
    deletePassword: $('deletePassword'),
    deleteConfirm: $('deleteConfirm'),
    deleteError: $('deleteError'),
    reminderTime: $('reminderTime'),
    pushOnBtn: $('pushOnBtn'),
    pushTestBtn: $('pushTestBtn'),
    pushOffBtn: $('pushOffBtn'),
    pushStatus: $('pushStatus'),
    readerSelect: $('readerSelect'),
    readerHelp: $('readerHelp'),
    toast: $('toast'),
    announcer: $('announcer'),
  };

  const state = {
    csrf: '',
    me: null,
    view: 'today',
    reviewId: null, // inbox item being reviewed (#/review/ID)
    deleteScope: 'data', // 'data' or 'account' (the delete dialog)
    activityBefore: null, // paging: show events older than this id
    review: null, // the review data from the server
    today: null, // "YYYY-MM-DD" in the user's timezone (from the server)
    inboxShow: 'active',
    taskShow: 'open',
    dialog: null, // { mode: 'new' | 'edit' | 'fromInbox', task?, item? }
    confirm: null, // function to run when the confirm button is pressed
    theme: 'auto',
    busy: false,
  };

  /* ==========================================================================
     02  HELPERS
     ========================================================================== */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, className, onClick) {
    const b = el('button', className, label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
  }

  /** The row's "⋯" menu (a native <details>, keyboard-friendly) holding the row's actions. Items are buttons
   *  ({label, onClick, danger}) or links ({label, href}). Returns null when there is nothing to show. */
  function moreMenu(label, items) {
    if (!items.length) return null;
    const details = el('details', 'more');
    const summary = el('summary', '', '⋯');
    summary.setAttribute('aria-label', `Actions for ${label}`);
    const menu = el('div', 'more-menu');
    items.forEach((item) => {
      const cls = `btn btn-small${item.danger ? ' is-danger' : ''}`;
      if (item.href) {
        const link = el('a', cls, item.label);
        link.href = item.href;
        link.addEventListener('click', () => (details.open = false));
        menu.append(link);
        return;
      }
      menu.append(
        button(item.label, cls, () => {
          details.open = false;
          item.onClick();
        }),
      );
    });
    details.append(summary, menu);
    return details;
  }

  /** Whole days from today to a date (both "YYYY-MM-DD", compared as calendar dates). */
  function daysFromToday(isoDate) {
    const toDay = (iso) =>
      Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)));
    return Math.round((toDay(isoDate) - toDay(state.today)) / 86400000);
  }

  /** "Mon, 12 Mar 2027" (a calendar date: no timezone shift). */
  function prettyDate(isoDate, withYear = false) {
    const [y, m, d] = isoDate.split('-').map(Number);
    const options = { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' };
    if (withYear) options.year = 'numeric';
    return new Intl.DateTimeFormat(undefined, options).format(new Date(Date.UTC(y, m - 1, d)));
  }

  /** A friendly due label and chip style for a task. */
  function dueChip(task) {
    if (task.status === 'done') return { text: '✓ Done', cls: 'chip chip--done' };
    if (task.status === 'cancelled') return { text: 'Cancelled', cls: 'chip' };
    if (task.snoozed_until && daysFromToday(task.snoozed_until) > 0) {
      return { text: `Snoozed to ${prettyDate(task.snoozed_until)}`, cls: 'chip chip--info' };
    }
    if (!task.due_date) return null;
    const days = daysFromToday(task.due_date);
    if (days < 0) return { text: `⚠ Overdue · ${prettyDate(task.due_date)}`, cls: 'chip chip--overdue' };
    if (days === 0) return { text: 'Due today', cls: 'chip chip--soon' };
    if (days === 1) return { text: 'Due tomorrow', cls: 'chip chip--soon' };
    if (days <= 7) return { text: `Due ${prettyDate(task.due_date)}`, cls: 'chip chip--soon' };
    return { text: `Due ${prettyDate(task.due_date)}`, cls: 'chip' };
  }

  function addDays(isoDate, days) {
    const [y, m, d] = isoDate.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatMoney(amount, currency) {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount));
    } catch {
      return `${currency || ''} ${amount}`.trim();
    }
  }

  let toastTimer = 0;
  function showToast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, TOAST_MS);
    announce(message);
  }

  function announce(message) {
    els.announcer.textContent = '';
    setTimeout(() => {
      els.announcer.textContent = message;
    }, 50);
  }

  /* ==========================================================================
     03  TALKING TO THE SERVER
     ========================================================================== */
  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  /** JSON (or file upload) request. Changes carry the CSRF token; {once: true} adds an Idempotency-Key. */
  async function api(path, { method = 'GET', body, form, once = false } = {}) {
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-CSRF-Token'] = state.csrf;
    if (once) headers['Idempotency-Key'] = crypto.randomUUID();
    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body: form || (body === undefined ? undefined : JSON.stringify(body)),
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch {
      throw new ApiError("Can't reach LifeOps. Is the server running?", 0);
    }
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    // A 401 from the login steps means a wrong password or code (shown on the form), not an expired session
    if (response.status === 401 && path !== '/api/login' && path !== '/api/login/code') {
      showLogin();
      throw new ApiError('Please log in again.', 401);
    }
    if (!response.ok) throw new ApiError(data.detail || 'Something went wrong.', response.status);
    return data;
  }

  /** Run a change, show a message, then redraw the current view (on any outcome). */
  async function change(work, success) {
    try {
      await work();
      if (success) showToast(success);
    } catch (error) {
      if (error.status !== 401) showToast(error.message);
    }
    await renderView();
  }

  /* ==========================================================================
     04  LOGIN, LOGOUT, SESSION
     ========================================================================== */
  /** True when LifeOps has no account yet (the server only says so on this computer). */
  async function setupNeeded() {
    try {
      return (await (await fetch('/api/setup', { cache: 'no-store' })).json()).needed === true;
    } catch {
      return false; // can't tell: show the normal login
    }
  }

  /** The login screen, or "Create your account" when LifeOps has no account yet. */
  async function showLogin() {
    els.shell.hidden = true;
    els.loginView.hidden = false;
    const needed = await setupNeeded();
    els.setupForm.hidden = !needed;
    els.loginForm.hidden = needed;
    els.codeForm.hidden = true;
    (needed ? els.setupEmail : els.loginEmail).focus();
  }

  async function createAccount(event) {
    event.preventDefault();
    els.setupError.hidden = true;
    const username = els.setupEmail.value.trim();
    const password = els.setupPassword.value;
    const problem = !/^[A-Za-z0-9._-]{3,32}$/.test(username)
      ? 'Use 3 to 32 letters or numbers for the username (dots, dashes and underscores are fine).'
      : password.length < 12
        ? 'Use at least 12 characters for the password.'
        : password !== els.setupPassword2.value
          ? "The two passwords don't match."
          : '';
    if (problem) {
      els.setupError.textContent = problem;
      els.setupError.hidden = false;
      return;
    }
    try {
      await api('/api/setup', { method: 'POST', body: { username, password } });
      els.setupPassword.value = '';
      els.setupPassword2.value = '';
      await startSession();
      showToast('Welcome to LifeOps!');
    } catch (error) {
      els.setupError.textContent = error.message;
      els.setupError.hidden = false;
    }
  }

  async function startSession() {
    const me = await api('/api/me');
    state.me = me;
    state.csrf = me.csrf_token;
    els.accountEmail.textContent = me.username;
    els.loginView.hidden = true;
    els.shell.hidden = false;
    await route();
  }

  async function login(event) {
    event.preventDefault();
    els.loginError.hidden = true;
    try {
      const result = await api('/api/login', {
        method: 'POST',
        body: { username: els.loginEmail.value.trim(), password: els.loginPassword.value },
      });
      els.loginPassword.value = '';
      if (result.two_step) {
        showCodeStep(result.ticket);
        return;
      }
      await startSession();
    } catch (error) {
      els.loginError.textContent = error.message;
      els.loginError.hidden = false;
      els.loginPassword.focus();
    }
  }

  /* ---- Step 2 of logging in: the authenticator code ---- */
  let loginTicket = '';

  function showCodeStep(ticket) {
    loginTicket = ticket;
    els.loginForm.hidden = true;
    els.codeForm.hidden = false;
    els.codeError.hidden = true;
    els.loginCode.value = '';
    els.loginCode.focus();
  }

  async function submitCode(event) {
    event.preventDefault();
    els.codeError.hidden = true;
    try {
      await api('/api/login/code', {
        method: 'POST',
        body: { ticket: loginTicket, code: els.loginCode.value.trim() },
      });
      loginTicket = '';
      await startSession();
    } catch (error) {
      els.codeError.textContent = error.message;
      els.codeError.hidden = false;
      if (/expired/i.test(error.message)) backToPassword();
      else els.loginCode.select();
    }
  }

  function backToPassword() {
    loginTicket = '';
    els.codeForm.hidden = true;
    els.loginForm.hidden = false;
    els.loginPassword.focus();
  }

  async function logout() {
    try {
      await api('/api/logout', { method: 'POST' });
    } catch {
      // already logged out: show the login screen anyway
    }
    state.me = null;
    state.csrf = '';
    showLogin();
  }

  /* ==========================================================================
     05  NAVIGATION (#/today, #/inbox, #/tasks, #/records, #/settings, #/review/ID)
     ========================================================================== */
  async function route() {
    const [name, id] = location.hash.replace('#/', '').split('/');
    if (name === 'review' && /^\d+$/.test(id || '')) {
      state.reviewId = Number(id);
      await go('review');
    } else {
      await go(VIEWS.includes(name) && name !== 'review' ? name : 'today');
    }
  }

  async function go(view) {
    state.view = view;
    VIEWS.forEach((name) => {
      $(`view-${name}`).hidden = name !== view;
    });
    const tabView = { review: 'inbox', activity: 'settings' }[view] || view;
    document.querySelectorAll('.tab').forEach((tab) => {
      if (tab.dataset.view === tabView) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
    document.title = `${view.charAt(0).toUpperCase()}${view.slice(1)} — LifeOps`;
    await renderView();
  }

  async function renderView() {
    if (!state.me) return;
    try {
      if (!state.today) state.today = (await api('/api/today')).today;
      if (state.view === 'today') await renderToday();
      else if (state.view === 'inbox') await renderInbox();
      else if (state.view === 'tasks') await renderTasks();
      else if (state.view === 'records') await renderRecords();
      else if (state.view === 'review') await renderReview();
      else if (state.view === 'settings') renderSettings();
      else if (state.view === 'activity') await renderActivity(false);
      if (!['today', 'inbox'].includes(state.view)) refreshInboxBadge();
    } catch (error) {
      if (error.status !== 401) showToast(error.message);
    }
  }

  /* ==========================================================================
     06  TODAY
     ========================================================================== */
  async function renderToday() {
    const data = await api('/api/today');
    state.today = data.today;
    els.todayDate.textContent = prettyDate(data.today);
    updateInboxBadge(data.inbox_waiting);
    drawStats(data);
    drawBills(data.bills_due);
    drawExpiring(data.expiring);
    const sections = GROUPS.filter((g) => data[g.key].length || g.empty).map((group) => {
      const section = el('section');
      const title = el('h2', 'group-title', group.title);
      title.append(el('span', 'group-count', String(data[group.key].length)));
      const list = el('ul', 'list');
      if (!data[group.key].length) list.append(el('li', 'empty', group.empty));
      data[group.key].forEach((task) => list.append(taskItem(task)));
      section.append(title, list);
      return section;
    });
    els.todayGroups.replaceChildren(...sections);
  }

  /* ---- Today's dashboard ---- */
  /** Four numbers at the top. Inbox and Bills are links to where you'd act on them. */
  function drawStats(data) {
    const totals = data.bills_total.map((t) => formatMoney(t.amount, t.currency)).join(' · ');
    const stats = [
      { num: data.overdue.length, label: 'Overdue', alert: data.overdue.length > 0 },
      { num: data.due_soon.length, label: 'Due this week' },
      { num: data.inbox_waiting, label: 'In Inbox', href: '#/inbox' },
      {
        num: totals || '0',
        label: `Bills due${data.bills_due_count ? ` (${data.bills_due_count})` : ''}`,
        href: '#/records',
        money: true,
      },
    ];
    els.todayStats.replaceChildren(
      ...stats.map((stat) => {
        const box = el(
          stat.href ? 'a' : 'div',
          `stat${stat.alert ? ' stat--alert' : ''}${stat.money ? ' stat--money' : ''}`,
        );
        if (stat.href) box.href = stat.href;
        box.append(el('span', 'stat-num', String(stat.num)), el('span', 'stat-label', stat.label));
        return box;
      }),
    );
  }

  /** "Due in 3 days", "Due today", "Overdue · Nov 11" for a bill. */
  function billWhen(dueDate) {
    const days = daysFromToday(dueDate);
    if (days < 0) return { text: `Overdue · ${prettyDate(dueDate)}`, alert: true };
    if (days === 0) return { text: 'Due today', alert: true };
    if (days === 1) return { text: 'Due tomorrow' };
    return { text: `Due ${prettyDate(dueDate)}` };
  }

  function drawBills(bills) {
    if (!bills.length) {
      els.billsList.replaceChildren(el('li', 'dash-empty', 'No bills due. Saved bills show up here.'));
      return;
    }
    els.billsList.replaceChildren(
      ...bills.map((bill) => {
        const li = el('li', 'dash-row');
        const left = el('div', 'dash-main');
        const when = billWhen(bill.due_date);
        left.append(el('p', 'dash-title', bill.title));
        const meta = el('p', 'dash-meta');
        meta.append(el('span', when.alert ? 'is-alert' : '', when.text));
        if (bill.supplier) meta.append(el('span', '', ` · ${bill.supplier}`));
        left.append(meta);
        li.append(
          left,
          el('span', 'dash-amount', bill.amount ? formatMoney(bill.amount, bill.currency) : '—'),
        );
        return li;
      }),
    );
  }

  function drawExpiring(records) {
    if (!records.length) {
      els.expiringList.replaceChildren(
        el('li', 'dash-empty', 'Nothing expiring. Warranties and renewals show up here.'),
      );
      return;
    }
    els.expiringList.replaceChildren(
      ...records.map((record) => {
        const li = el('li', 'dash-row');
        const left = el('div', 'dash-main');
        const days = daysFromToday(record.expiry_date);
        left.append(el('p', 'dash-title', record.title));
        left.append(
          el(
            'p',
            'dash-meta',
            `${DOC_TYPE_NAMES[record.doc_type] || record.doc_type} · ends ${prettyDate(record.expiry_date, true)}`,
          ),
        );
        li.append(
          left,
          el(
            'span',
            `dash-days${days <= 30 ? ' is-alert' : ''}`,
            days === 0 ? 'today' : `in ${days} day${days === 1 ? '' : 's'}`,
          ),
        );
        return li;
      }),
    );
  }

  /** The Inbox count in the tabs, refreshed in the background (Today and Inbox update it themselves). */
  function refreshInboxBadge() {
    api('/api/today')
      .then((data) => updateInboxBadge(data.inbox_waiting))
      .catch(() => {});
  }

  function updateInboxBadge(count) {
    els.inboxBadge.hidden = !count;
    els.inboxBadge.textContent = String(count);
    els.inboxBadge.setAttribute('aria-label', `${count} waiting`);
  }

  /* ==========================================================================
     07  INBOX (notes, uploads, suggestions)
     ========================================================================== */
  async function renderInbox() {
    const data = await api(`/api/inbox?show=${state.inboxShow}`);
    if (state.inboxShow === 'active') {
      updateInboxBadge(data.items.filter((i) => ['new', 'needs_review', 'failed'].includes(i.status)).length);
    }
    const empty = {
      active: 'All clear. Anything you jot down or upload lands here until you sort it.',
      converted: 'Notes and documents you turn into tasks or records show up here.',
      archived: 'Nothing archived.',
    }[state.inboxShow];
    els.inboxList.replaceChildren(
      ...(data.items.length ? data.items.map(inboxItem) : [el('li', 'empty', empty)]),
    );
  }

  function statusChip(item) {
    return {
      new: null,
      processing: { text: 'Reading…', cls: 'chip chip--info' },
      needs_review: { text: 'Ready to review', cls: 'chip chip--soon' },
      failed: { text: "⚠ Couldn't read", cls: 'chip chip--overdue' },
      converted: { text: '✓ Sorted', cls: 'chip chip--done' },
      archived: { text: 'Archived', cls: 'chip' },
    }[item.status];
  }

  function inboxItem(item) {
    const li = el('li', 'item item--note');
    const body = el('div', 'item-body');
    if (item.file) {
      const title = el('p', 'item-title', item.file.name);
      body.append(title);
    } else {
      body.append(el('p', 'item-text', item.content));
    }
    const meta = el('div', 'item-meta');
    const chip = statusChip(item);
    if (chip) meta.append(el('span', chip.cls, chip.text));
    if (item.file) {
      const pages = item.file.pages ? ` · ${item.file.pages} page${item.file.pages === 1 ? '' : 's'}` : '';
      meta.append(el('span', '', `${formatBytes(item.file.size)}${pages}`));
    }
    meta.append(el('span', '', `Saved ${new Date(item.created_at).toLocaleString()}`));
    body.append(meta);
    if (item.status === 'failed' && item.error) body.append(el('p', 'form-error', item.error));
    const actions = inboxActions(item);
    if (actions.childElementCount) body.append(actions);
    li.append(body);
    const menu = moreMenu(item.file ? item.file.name : 'this note', inboxMenu(item));
    if (menu) li.append(menu);
    return li;
  }

  /** The one main action for an Inbox item (Review, Try again, Get suggestions or Restore), shown on the row. */
  function inboxActions(item) {
    const actions = el('div', 'item-actions');
    const post = (path, message) =>
      change(() => api(path, { method: 'POST', body: { version: item.version } }), message);
    if (item.status === 'needs_review') {
      const review = el('a', 'btn btn-small btn-primary', 'Review');
      review.href = `#/review/${item.id}`;
      actions.append(review);
    } else if (item.status === 'failed') {
      actions.append(
        button('Try again', 'btn btn-small btn-primary', () =>
          post(`/api/inbox/${item.id}/retry`, 'Trying again'),
        ),
      );
    } else if (item.status === 'new' && item.kind === 'text') {
      actions.append(
        button('Get suggestions', 'btn btn-small btn-primary', () =>
          change(async () => {
            await api(`/api/inbox/${item.id}/suggest`, { method: 'POST', body: { version: item.version } });
            location.hash = `#/review/${item.id}`;
          }, 'Suggestions ready'),
        ),
      );
    } else if (item.status === 'archived') {
      actions.append(
        button('Restore', 'btn btn-small btn-primary', () =>
          post(`/api/inbox/${item.id}/restore`, 'Restored'),
        ),
      );
    }
    return actions;
  }

  /** The less common actions for an Inbox item, in its ⋯ menu. */
  function inboxMenu(item) {
    const items = [];
    if (item.status === 'new' && item.kind === 'text') {
      items.push({ label: 'Make a task', onClick: () => openTaskDialog({ mode: 'fromInbox', item }) });
    }
    if (item.file) items.push({ label: 'Download', href: `/api/files/${item.file.id}` });
    if (['new', 'needs_review', 'failed'].includes(item.status)) {
      items.push({
        label: 'Archive',
        onClick: () =>
          change(
            () => api(`/api/inbox/${item.id}/archive`, { method: 'POST', body: { version: item.version } }),
            'Archived',
          ),
      });
    }
    items.push({
      label: 'Delete',
      danger: true,
      onClick: () =>
        askConfirm(
          item.file ? 'Delete this document?' : 'Delete this note?',
          'It will be removed for good.',
          () => change(() => api(`/api/inbox/${item.id}`, { method: 'DELETE' }), 'Deleted'),
        ),
    });
    return items;
  }

  async function capture(event) {
    event.preventDefault();
    const content = els.captureText.value.trim();
    if (!content) {
      els.captureText.setAttribute('aria-invalid', 'true');
      showToast('Type something first, or upload a document.');
      els.captureText.focus();
      return;
    }
    els.captureText.removeAttribute('aria-invalid');
    try {
      await api('/api/inbox', { method: 'POST', body: { content }, once: true });
      els.captureText.value = '';
      showToast('Saved to your Inbox');
    } catch (error) {
      showToast(error.message); // the text stays in the box, so nothing is lost
    }
    showActiveInbox();
  }

  /** Check the file in the browser first (quick), then upload; the server checks again (safe). */
  async function uploadFile() {
    const file = els.uploadInput.files[0];
    els.uploadInput.value = '';
    if (!file) return;
    if (!UPLOAD_TYPES.includes(file.type) && !UPLOAD_EXTENSIONS.test(file.name)) {
      showToast('Only PDF, JPEG, PNG or HEIC files can be uploaded.');
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      showToast('That file is over 10 MB.');
      return;
    }
    if (state.busy) return;
    state.busy = true;
    showToast(`Uploading ${file.name}…`);
    try {
      const form = new FormData();
      form.append('file', file);
      const item = await api('/api/inbox/upload', { method: 'POST', form, once: true });
      if (item.status === 'needs_review') {
        showToast('Read it: check the suggestions');
        location.hash = `#/review/${item.id}`;
      } else {
        showToast('Uploaded');
        showActiveInbox();
      }
    } catch (error) {
      showToast(error.message);
    } finally {
      state.busy = false;
    }
  }

  function showActiveInbox() {
    state.inboxShow = 'active';
    syncSegments('[data-inbox-show]', 'active');
    renderInbox().catch((error) => showToast(error.message));
  }

  /* ==========================================================================
     08  REVIEW (check a proposal, save a record)
     Suggestions are shown with how sure the reader is and where it found
     them. Nothing is saved until "Save record".
     ========================================================================== */
  async function renderReview() {
    const data = await api(`/api/inbox/${state.reviewId}/review`);
    state.review = data;
    els.reviewError.hidden = true;
    if (data.status !== 'needs_review' || !data.proposal) {
      els.reviewSource.replaceChildren(el('p', 'muted', 'This item has already been sorted.'));
      els.reviewForm.hidden = true;
      els.reviewNote.hidden = true;
      return;
    }
    els.reviewForm.hidden = false;
    drawReviewSource(data);
    els.reviewNote.hidden = !data.proposal.note;
    els.reviewNote.textContent = data.proposal.note;
    if (data.run && data.run.attempts > 1) {
      els.reviewSource.append(el('p', 'run-line', `Read on attempt ${data.run.attempts}.`));
    }
    const fields = data.proposal.fields;
    els.reviewFields.replaceChildren(
      ...REVIEW_FIELDS.map((spec) => reviewField(spec, fields[spec.name] || {})),
    );
    // Suggested task: pay by the due date, or check a month before something expires
    const docType = fields.doc_type.value || 'other';
    const title = fields.title.value || '';
    els.reviewMakeTask.checked = Boolean(fields.due_date.value || fields.expiry_date.value);
    els.reviewTaskTitle.value = suggestedTaskTitle(docType, title);
    els.reviewTaskDue.value = fields.due_date.value
      ? fields.due_date.value
      : fields.expiry_date.value
        ? addDays(fields.expiry_date.value, -30)
        : '';
    els.reviewTaskFields.hidden = !els.reviewMakeTask.checked;
  }

  function drawReviewSource(data) {
    const parts = [];
    if (data.file) {
      parts.push(el('p', 'item-title', data.file.name));
      const meta = el(
        'p',
        'muted small',
        `${formatBytes(data.file.size)}${data.file.pages ? ` · ${data.file.pages} page${data.file.pages === 1 ? '' : 's'}` : ''}`,
      );
      parts.push(meta);
      if (['image/jpeg', 'image/png'].includes(data.file.media_type)) {
        const img = el('img', 'review-photo');
        img.src = `/api/files/${data.file.id}?inline=1`;
        img.alt = `The uploaded photo: ${data.file.name}`;
        parts.push(img);
      }
      const link = el('a', 'link-btn review-open', 'Open the original');
      link.href = `/api/files/${data.file.id}`;
      parts.push(link);
    } else {
      parts.push(el('p', 'item-text', data.content));
    }
    els.reviewSource.replaceChildren(...parts);
  }

  function suggestedTaskTitle(docType, title) {
    const base = title || DOC_TYPE_NAMES[docType] || 'item';
    if (docType === 'bill') return `Pay ${base}`.slice(0, 200);
    if (docType === 'warranty' || docType === 'renewal') return `Check / renew: ${base}`.slice(0, 200);
    return `Follow up: ${base}`.slice(0, 200);
  }

  /** One field of the review form: input, how sure, and the evidence ("found on page 2: …"). */
  function reviewField(spec, found) {
    const wrap = el('div', 'field');
    const id = `rf-${spec.name}`;
    const label = el('label', 'field-label', spec.label);
    label.htmlFor = id;
    if (found.value && found.confidence) {
      label.append(
        el(
          'span',
          `chip conf${found.confidence === 'high' ? ' chip--done' : ''}`,
          CONFIDENCE[found.confidence],
        ),
      );
    }
    let input;
    if (spec.type === 'select') {
      input = el('select', 'input');
      Object.entries(DOC_TYPE_NAMES).forEach(([value, name]) => {
        const option = el('option', '', name);
        option.value = value;
        input.append(option);
      });
      input.value = found.value || 'other';
    } else if (spec.type === 'currency') {
      input = el('select', 'input');
      ['PHP', 'USD', 'EUR', 'JPY', 'SGD'].forEach((code) => {
        const option = el('option', '', code);
        option.value = code;
        input.append(option);
      });
      input.value = found.value || 'PHP';
    } else {
      input = el('input', 'input');
      input.type = spec.type === 'date' ? 'date' : 'text';
      if (spec.max) input.maxLength = spec.max;
      if (spec.inputmode) input.inputMode = spec.inputmode;
      input.autocomplete = 'off';
      input.value = found.value || '';
    }
    input.id = id;
    input.name = spec.name;
    wrap.append(label, input);
    if (found.date_kind === 'ambiguous' && found.options && found.options.length) {
      wrap.append(el('p', 'evidence', 'This date could be read two ways. Which one is it?'));
      const choices = el('div', 'choices');
      found.options.forEach((option) =>
        choices.append(
          button(prettyDate(option, true), 'btn btn-small', () => {
            input.value = option;
            announce(`Chosen: ${prettyDate(option, true)}`);
          }),
        ),
      );
      wrap.append(choices);
    }
    if (found.evidence && found.evidence.snippet) {
      const evidence = el('p', 'evidence');
      evidence.append(
        document.createTextNode(found.evidence.page ? `Found on page ${found.evidence.page}: ` : 'Found: '),
      );
      evidence.append(el('q', '', found.evidence.snippet));
      wrap.append(evidence);
    }
    return wrap;
  }

  function reviewValue(name) {
    const input = els.reviewForm.querySelector(`[name="${name}"]`);
    return input ? input.value.trim() : '';
  }

  async function saveReview(event) {
    event.preventDefault();
    const data = state.review;
    if (!data) return;
    const title = reviewValue('title');
    if (!title) {
      showReviewError('Give it a title, e.g. "Electricity bill".');
      $('rf-title').focus();
      return;
    }
    const amount = reviewValue('amount');
    const docType = reviewValue('doc_type');
    const body = {
      version: data.version,
      doc_type: docType,
      title,
      supplier: reviewValue('supplier'),
      amount: amount || null,
      currency: amount ? reviewValue('currency') : null,
      reference: reviewValue('reference'),
      due_date: reviewValue('due_date') || null,
      expiry_date: reviewValue('expiry_date') || null,
      make_task: els.reviewMakeTask.checked,
      task_title: els.reviewTaskTitle.value.trim() || null,
      task_due_date: els.reviewTaskDue.value || null,
      task_category: DOC_TO_CATEGORY[docType] || 'other',
    };
    try {
      const result = await api(`/api/inbox/${data.id}/accept`, { method: 'POST', body, once: true });
      showToast(result.task ? 'Record saved, and a task added' : 'Record saved');
      location.hash = '#/records';
    } catch (error) {
      showReviewError(error.message);
    }
  }

  function showReviewError(message) {
    els.reviewError.textContent = message;
    els.reviewError.hidden = false;
  }

  async function rejectReview() {
    const data = state.review;
    if (!data) return;
    try {
      await api(`/api/inbox/${data.id}/reject`, { method: 'POST', body: { version: data.version } });
      showToast('Suggestions rejected. The item is back in your Inbox.');
      location.hash = '#/inbox';
    } catch (error) {
      showReviewError(error.message);
    }
  }

  /* ==========================================================================
     09  RECORDS
     ========================================================================== */
  async function renderRecords() {
    const data = await api('/api/records');
    els.recordList.replaceChildren(
      ...(data.records.length
        ? data.records.map(recordItem)
        : [el('li', 'empty', 'No records yet. Upload a bill or warranty in the Inbox and review it.')]),
    );
  }

  function recordItem(record) {
    const li = el('li', 'item item--note');
    const body = el('div', 'item-body');
    body.append(el('p', 'item-title', record.title));
    const meta = el('div', 'item-meta');
    meta.append(el('span', 'cat', DOC_TYPE_NAMES[record.doc_type] || record.doc_type));
    if (record.amount) meta.append(el('span', 'record-amount', formatMoney(record.amount, record.currency)));
    if (record.supplier) meta.append(el('span', '', record.supplier));
    if (record.reference) meta.append(el('span', '', `Ref ${record.reference}`));
    body.append(meta);
    const dates = el('div', 'item-meta');
    if (record.due_date) dates.append(el('span', '', `Due ${prettyDate(record.due_date, true)}`));
    if (record.expiry_date) dates.append(el('span', '', `Expires ${prettyDate(record.expiry_date, true)}`));
    if (dates.childElementCount) body.append(dates);
    li.append(body);
    const menu = moreMenu(
      record.title,
      record.file ? [{ label: `Download ${record.file.name}`, href: `/api/files/${record.file.id}` }] : [],
    );
    if (menu) li.append(menu);
    return li;
  }

  /* ==========================================================================
     10  TASKS
     ========================================================================== */
  async function renderTasks() {
    const data = await api(`/api/tasks?status=${state.taskShow}`);
    const empty = {
      open: 'No open tasks. Add one, or sort something from your Inbox.',
      done: 'Finished tasks show up here.',
      cancelled: 'Nothing cancelled.',
    }[state.taskShow];
    els.taskList.replaceChildren(
      ...(data.tasks.length ? data.tasks.map(taskItem) : [el('li', 'empty', empty)]),
    );
  }

  /** One task row: tick to complete/reopen, its date and category, and a ⋯ menu with the rest. */
  function taskItem(task) {
    const done = task.status === 'done';
    const li = el('li', `item${done ? ' is-done' : ''}`);
    const check = el('button', 'item-check');
    check.type = 'button';
    check.setAttribute('aria-pressed', String(done));
    check.setAttribute(
      'aria-label',
      done ? `Mark "${task.title}" as not done` : `Mark "${task.title}" as done`,
    );
    check.append(icon('check'));
    check.disabled = task.status === 'cancelled';
    check.addEventListener('click', () =>
      change(
        () =>
          api(`/api/tasks/${task.id}/status`, {
            method: 'POST',
            body: { version: task.version, action: done ? 'reopen' : 'complete' },
          }),
        done ? 'Reopened' : 'Nice, done!',
      ),
    );

    const body = el('div', 'item-body');
    body.append(el('p', 'item-title', task.title));
    if (task.details && task.source_inbox_id === null) {
      body.append(el('p', 'item-text muted small', task.details));
    }
    const meta = el('div', 'item-meta');
    const chip = dueChip(task);
    if (chip) meta.append(el('span', chip.cls, chip.text));
    meta.append(el('span', 'cat', CATEGORY_NAMES[task.category] || task.category));
    if (task.source_record_id) meta.append(el('span', '', 'From a record'));
    else if (task.source_inbox_id) meta.append(el('span', '', 'From an Inbox note'));
    body.append(meta);
    li.append(check, body);
    const menu = moreMenu(task.title, taskMenu(task));
    if (menu) li.append(menu);
    return li;
  }

  /** A task's actions, in its ⋯ menu. */
  function taskMenu(task) {
    const status = (action, message) =>
      change(
        () =>
          api(`/api/tasks/${task.id}/status`, { method: 'POST', body: { version: task.version, action } }),
        message,
      );
    const extra = [];
    if (task.status === 'open') {
      extra.push(
        { label: 'Edit', onClick: () => openTaskDialog({ mode: 'edit', task }) },
        {
          label: 'Snooze a week',
          onClick: () =>
            change(
              () =>
                api(`/api/tasks/${task.id}/snooze`, {
                  method: 'POST',
                  body: { version: task.version, until: addDays(state.today, 7) },
                }),
              'Snoozed for a week',
            ),
        },
        { label: 'Cancel task', onClick: () => status('cancel', 'Task cancelled') },
      );
    } else if (task.status === 'cancelled') {
      extra.push({ label: 'Reopen', onClick: () => status('reopen', 'Reopened') });
    }
    extra.push({
      label: 'Delete',
      danger: true,
      onClick: () =>
        askConfirm('Delete this task?', `"${task.title}" will be removed for good.`, () =>
          change(() => api(`/api/tasks/${task.id}`, { method: 'DELETE' }), 'Task deleted'),
        ),
    });
    return extra;
  }

  /** The task dialog: new task, edit task, or turn an Inbox note into a task. */
  function openTaskDialog(dialog) {
    state.dialog = dialog;
    const task = dialog.task;
    const fromNote = dialog.mode === 'fromInbox';
    els.taskDialogTitle.textContent = { new: 'New task', edit: 'Edit task', fromInbox: 'Make a task' }[
      dialog.mode
    ];
    els.taskTitle.value = task ? task.title : fromNote ? firstLine(dialog.item.content) : '';
    els.taskDue.value = task && task.due_date ? task.due_date : '';
    els.taskCategory.value = task ? task.category : 'other';
    els.taskDetails.value = task ? task.details : '';
    els.taskDetailsField.hidden = fromNote; // the note itself becomes the task's notes
    els.taskError.hidden = true;
    els.taskTitle.removeAttribute('aria-invalid');
    els.taskDialog.showModal();
    els.taskTitle.focus();
  }

  function firstLine(text) {
    return text.split('\n')[0].slice(0, 200);
  }

  async function saveTask(event) {
    event.preventDefault();
    const title = els.taskTitle.value.trim();
    if (!title) {
      els.taskTitle.setAttribute('aria-invalid', 'true');
      els.taskError.textContent = 'Give the task a name.';
      els.taskError.hidden = false;
      els.taskTitle.focus();
      return;
    }
    const due = els.taskDue.value || null;
    const category = els.taskCategory.value;
    const { mode, task, item } = state.dialog;
    try {
      if (mode === 'new') {
        await api('/api/tasks', {
          method: 'POST',
          body: { title, due_date: due, category, details: els.taskDetails.value.trim() },
          once: true,
        });
      } else if (mode === 'edit') {
        await api(`/api/tasks/${task.id}`, {
          method: 'PATCH',
          body: {
            version: task.version,
            title,
            category,
            details: els.taskDetails.value.trim(),
            due_date: due,
            clear_due_date: !due,
          },
        });
      } else {
        await api(`/api/inbox/${item.id}/task`, {
          method: 'POST',
          body: { version: item.version, title, category, due_date: due },
          once: true,
        });
      }
    } catch (error) {
      els.taskError.textContent = error.message;
      els.taskError.hidden = false;
      return;
    }
    els.taskDialog.close();
    showToast(mode === 'edit' ? 'Saved' : 'Task added');
    await renderView();
  }

  function askConfirm(title, text, onYes) {
    els.confirmTitle.textContent = title;
    els.confirmText.textContent = text;
    state.confirm = onYes;
    els.confirmDialog.showModal();
  }

  /* ==========================================================================
     11  SETTINGS
     ========================================================================== */
  function renderSettings() {
    if (!els.timezoneSelect.options.length) {
      const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
      const list = zones.includes(state.me.timezone) ? zones : [state.me.timezone, ...zones];
      els.timezoneSelect.replaceChildren(
        ...list.map((zone) => Object.assign(el('option', '', zone), { value: zone })),
      );
    }
    els.timezoneSelect.value = state.me.timezone;
    els.reminderTime.value = state.me.reminder_time;
    els.readerSelect.value = state.me.extractor;
    drawTwoStep();
    els.readerSelect.querySelector('option[value="claude"]').disabled = !state.me.claude_available;
    els.readerSelect.querySelector('option[value="local"]').disabled =
      !state.me.local_model && state.me.extractor !== 'local';
    els.readerHelp.textContent = readerHelp(state.me);
    drawPushState().catch(() => {});
  }

  /** Timezone and reminder time save themselves when changed (no Save button); only the changed one is sent,
   *  so the Activity history doesn't list a change that didn't happen. */
  async function saveSettings(field) {
    const body =
      field === 'timezone'
        ? { timezone: els.timezoneSelect.value }
        : { reminder_time: els.reminderTime.value };
    if (field !== 'timezone' && !els.reminderTime.value) return; // half-typed time: wait for a full one
    try {
      state.me = await api('/api/me', { method: 'PATCH', body });
      state.csrf = state.me.csrf_token;
      state.today = null;
      showToast('Saved');
    } catch (error) {
      showToast(error.message);
    }
  }

  /* ---- Two-step login (Google Authenticator) ---- */
  const twoStep = { mode: 'on', stage: 'password' };

  function drawTwoStep() {
    const on = state.me.two_step;
    const left = state.me.recovery_codes_left;
    els.twoStepState.textContent = on ? `· on (${left} recovery code${left === 1 ? '' : 's'} left)` : '· off';
    els.twoStepBtn.textContent = on ? 'Turn off' : 'Turn on';
  }

  function openTwoStep() {
    twoStep.mode = state.me.two_step ? 'off' : 'on';
    twoStep.stage = 'password';
    els.twoStepTitle.textContent =
      twoStep.mode === 'on' ? 'Turn on two-step login' : 'Turn off two-step login';
    els.twoStepText.textContent =
      twoStep.mode === 'on'
        ? 'After your password, LifeOps will also ask for a 6-digit code from Google Authenticator. First, confirm your password.'
        : 'Confirm your password and a code from the app (or a recovery code).';
    showTwoStepFields({ password: true, code: twoStep.mode === 'off' });
    els.twoStepPassword.value = '';
    els.twoStepCode.value = '';
    els.twoStepSubmit.textContent = twoStep.mode === 'on' ? 'Continue' : 'Turn off';
    els.twoStepCancel.hidden = false;
    els.twoStepError.hidden = true;
    els.twoStepDialog.showModal();
    els.twoStepPassword.focus();
  }

  function showTwoStepFields({ password = false, code = false, qr = false, codes = false }) {
    els.twoStepPasswordField.hidden = !password;
    els.twoStepCodeField.hidden = !code;
    els.twoStepQr.hidden = !qr;
    els.twoStepKey.hidden = !qr;
    els.twoStepCodes.hidden = !codes;
  }

  /** Draw the setup QR code as an SVG, in the browser (the secret never goes to a QR service). */
  function drawQr(text) {
    const { modules, size } = window.QrEncoder.encode(text, { ecc: 'M' });
    const quiet = 2;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${size + quiet * 2} ${size + quiet * 2}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'QR code for Google Authenticator');
    let d = '';
    modules.forEach((row, y) =>
      row.forEach((dark, x) => {
        if (dark) d += `M${x + quiet} ${y + quiet}h1v1h-1z`;
      }),
    );
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', '#000');
    svg.append(path);
    els.twoStepQr.replaceChildren(svg);
  }

  async function submitTwoStep(event) {
    event.preventDefault();
    els.twoStepError.hidden = true;
    try {
      if (twoStep.mode === 'off') {
        await api('/api/two-step/off', {
          method: 'POST',
          body: { password: els.twoStepPassword.value, code: els.twoStepCode.value.trim() },
        });
        els.twoStepDialog.close();
        showToast('Two-step login is off');
      } else if (twoStep.stage === 'password') {
        const result = await api('/api/two-step/start', {
          method: 'POST',
          body: { password: els.twoStepPassword.value },
        });
        els.twoStepPassword.value = '';
        drawQr(result.uri);
        els.twoStepKey.textContent = result.secret.match(/.{1,4}/g).join(' ');
        els.twoStepText.textContent =
          'In Google Authenticator, tap + and scan this code (or choose "Enter a setup key" and type the key below). Then type the 6-digit code it shows.';
        showTwoStepFields({ qr: true, code: true });
        twoStep.stage = 'code';
        els.twoStepCode.focus();
      } else if (twoStep.stage === 'code') {
        const result = await api('/api/two-step/confirm', {
          method: 'POST',
          body: { code: els.twoStepCode.value.trim() },
        });
        els.twoStepText.textContent =
          'Two-step login is on. Save these recovery codes somewhere safe (each works once) in case you lose your phone. They will not be shown again.';
        els.twoStepCodes.replaceChildren(...result.recovery_codes.map((code) => el('li', '', code)));
        showTwoStepFields({ codes: true });
        els.twoStepSubmit.textContent = "I've saved them";
        els.twoStepCancel.hidden = true;
        twoStep.stage = 'done';
      } else {
        els.twoStepDialog.close();
      }
    } catch (error) {
      els.twoStepError.textContent = error.message;
      els.twoStepError.hidden = false;
    }
    if (twoStep.stage === 'done' || twoStep.mode === 'off') {
      try {
        state.me = await api('/api/me');
        state.csrf = state.me.csrf_token;
        drawTwoStep();
      } catch {
        // shown on the next visit
      }
    }
  }

  /* ---- Reminders on this device (Web Push) ---- */
  const pushSupported = () =>
    'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  async function currentSubscription() {
    if (!pushSupported()) return null;
    const registration = await navigator.serviceWorker.ready;
    return registration.pushManager.getSubscription();
  }

  async function drawPushState() {
    if (!pushSupported()) {
      els.pushOnBtn.hidden = true;
      els.pushStatus.textContent =
        "This browser can't receive reminders. The Today view always shows what's due.";
      return;
    }
    const subscription = await currentSubscription();
    const on = Boolean(subscription) && Notification.permission === 'granted';
    els.pushOnBtn.hidden = on;
    els.pushTestBtn.hidden = !on;
    els.pushOffBtn.hidden = !on;
    const status = await api('/api/push/status');
    els.pushStatus.textContent = on
      ? `Reminders are on for this device. ${status.pending_reminders} reminder${status.pending_reminders === 1 ? '' : 's'} planned.`
      : Notification.permission === 'denied'
        ? 'Notifications are blocked for LifeOps in this browser. Allow them in the browser settings, then try again.'
        : 'Reminders are off on this device.';
  }

  /** Ask only after a tap (browsers and iPhone require it), then register this device with the server. */
  async function turnPushOn() {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        showToast('Notifications were not allowed.');
        await drawPushState();
        return;
      }
      const { public_key: key } = await api('/api/push/key');
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToBytes(key),
      });
      await api('/api/push/subscribe', { method: 'POST', body: subscription.toJSON() });
      showToast('Reminders are on for this device');
    } catch (error) {
      // Browser errors are technical: explain the usual causes in plain words
      const blocked = error && (error.name === 'NotAllowedError' || /permission/i.test(error.message || ''));
      showToast(
        blocked
          ? "This window can't get reminders. Private/incognito windows can't; try a normal window."
          : error.message || "Reminders couldn't be turned on.",
      );
    }
    await drawPushState();
  }

  async function turnPushOff() {
    const subscription = await currentSubscription();
    if (subscription) {
      await api('/api/push/unsubscribe', { method: 'POST', body: { endpoint: subscription.endpoint } }).catch(
        () => {},
      );
      await subscription.unsubscribe();
    }
    showToast('Reminders are off on this device');
    await drawPushState();
  }

  async function sendTestPush() {
    try {
      await api('/api/push/test', { method: 'POST' });
      showToast('Test sent: it should appear in a moment');
    } catch (error) {
      showToast(error.message);
    }
  }

  function base64UrlToBytes(text) {
    const padded = (text + '='.repeat((4 - (text.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
  }

  /** One line under the reader choice: what the chosen reader does, or what's needed to use the others. */
  function readerHelp(me) {
    if (me.extractor === 'local') {
      return me.local_model
        ? `Uses ${me.local_model} on this computer: nothing leaves it. Photos need a vision model.`
        : "Ollama isn't running. Open the Ollama app; until then uploads stop with a Try again button.";
    }
    if (me.extractor === 'claude') {
      return 'Claude reads photos and scans too. Only the document being read is sent.';
    }
    const local = me.local_model
      ? `On this computer uses ${me.local_model}.`
      : 'On this computer needs the Ollama app open with a model.';
    const claude = me.claude_available ? '' : ' Claude needs an ANTHROPIC_API_KEY on the server.';
    return local + claude;
  }

  async function saveReader() {
    try {
      state.me = await api('/api/me', { method: 'PATCH', body: { extractor: els.readerSelect.value } });
      state.csrf = state.me.csrf_token;
      const names = { claude: 'Claude', local: 'The model on this computer', mock: 'The built-in reader' };
      showToast(`${names[state.me.extractor]} will read new uploads`);
      els.readerHelp.textContent = readerHelp(state.me);
    } catch (error) {
      showToast(error.message);
      els.readerSelect.value = state.me.extractor;
    }
  }

  /** Plain words for an activity event (it never contains your content, only what happened). */
  const ACTIVITY_WORDS = {
    'session:login': 'Logged in',
    'session:logout': 'Logged out',
    'inbox:captured': 'Saved a note to the Inbox',
    'inbox:uploaded': 'Uploaded a document',
    'inbox:proposal_made': 'Suggestions were made',
    'inbox:proposal_rejected': 'Rejected the suggestions',
    'inbox:converted': 'Sorted an Inbox item',
    'inbox:archived': 'Archived an Inbox item',
    'inbox:new': 'Restored an Inbox item',
    'inbox:deleted': 'Deleted an Inbox item',
    'inbox:failed': "Couldn't read an Inbox item",
    'record:created': 'Saved a record',
    'task:created': 'Added a task',
    'task:edited': 'Edited a task',
    'task:complete': 'Completed a task',
    'task:reopen': 'Reopened a task',
    'task:cancel': 'Cancelled a task',
    'task:snoozed': 'Snoozed a task',
    'task:deleted': 'Deleted a task',
    'run:running': 'Started reading',
    'run:needs_review': 'Finished reading: ready to review',
    'run:failed': 'Reading failed',
    'run:completed': 'Done with a document (accepted or rejected)',
    'run:cancelled': 'Stopped reading (item archived)',
    'settings:timezone_changed': 'Changed the timezone',
    'settings:reminder_time_changed': 'Changed the reminder time',
    'settings:reader_changed': 'Changed the document reader',
    'push:subscribed': 'Turned on reminders on a device',
    'two_step:turned_on': 'Turned on two-step login',
    'two_step:turned_off': 'Turned off two-step login',
    'two_step:recovery_code_used': 'Logged in with a recovery code',
    'account:created': 'Created the account',
    'push:unsubscribed': 'Turned off reminders on a device',
    'reminder:sent': 'Sent a reminder',
    'account:exported': 'Exported all data',
    'account:data_deleted': 'Deleted all data',
  };

  async function renderActivity(more) {
    if (!more) state.activityBefore = null;
    const query = state.activityBefore ? `?before=${state.activityBefore}` : '';
    const data = await api(`/api/activity${query}`);
    const items = data.events.map((event) => {
      const li = el('li');
      const words = ACTIVITY_WORDS[`${event.entity}:${event.action}`] || `${event.entity}: ${event.action}`;
      li.append(el('span', '', words), el('span', 'activity-when', new Date(event.at).toLocaleString()));
      return li;
    });
    if (!more) {
      els.activityList.replaceChildren(...(items.length ? items : [el('li', 'empty', 'Nothing yet.')]));
    } else {
      els.activityList.append(...items);
    }
    if (data.events.length) state.activityBefore = data.events[data.events.length - 1].id;
    els.activityMore.hidden = !data.more;
  }

  function openDelete(scope) {
    state.deleteScope = scope;
    const account = scope === 'account';
    els.deleteTitle.textContent = account ? 'Delete my account?' : 'Delete all my data?';
    els.deleteText.textContent = account
      ? 'Your login and everything you stored (notes, documents, records, tasks, history) will be removed for good, and you will be signed out. Export first if you want a copy.'
      : 'Everything you stored (notes, documents, records, tasks, history) will be removed for good. Your login stays. Export first if you want a copy.';
    els.deletePassword.value = '';
    els.deleteConfirm.value = '';
    els.deleteError.hidden = true;
    els.deleteDialog.showModal();
    els.deletePassword.focus();
  }

  async function confirmDelete(event) {
    event.preventDefault();
    try {
      const result = await api('/api/account/delete', {
        method: 'POST',
        body: {
          scope: state.deleteScope,
          password: els.deletePassword.value,
          confirm: els.deleteConfirm.value.trim(),
        },
      });
      els.deletePassword.value = '';
      els.deleteDialog.close();
      if (result.scope === 'account') {
        state.me = null;
        showLogin();
        showToast('Your account was deleted.');
      } else {
        showToast('All your data was deleted.');
        location.hash = '#/today';
      }
    } catch (error) {
      els.deleteError.textContent = error.message;
      els.deleteError.hidden = false;
    }
  }

  /* ==========================================================================
     12  THEME (auto → light → dark)
     ========================================================================== */
  function applyTheme(theme) {
    state.theme = theme;
    if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    els.themeBtn.setAttribute('aria-label', `Theme: ${theme}. Switch to ${next}`);
    els.themeBtn.title = `Theme: ${theme}`;
  }

  function cycleTheme() {
    const theme = THEMES[(THEMES.indexOf(state.theme) + 1) % THEMES.length];
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // storage blocked: the choice just isn't remembered
    }
    showToast(`Theme: ${theme}`);
  }

  /* ==========================================================================
     13  INIT
     ========================================================================== */
  function syncSegments(selector, value) {
    document.querySelectorAll(selector).forEach((seg) => {
      const key = seg.dataset.inboxShow || seg.dataset.taskShow;
      seg.setAttribute('aria-pressed', String(key === value));
    });
  }

  /** Close any open "⋯ More" menu when clicking elsewhere or pressing Escape. */
  function closeMenus(except) {
    document.querySelectorAll('details.more[open]').forEach((menu) => {
      if (menu !== except) menu.open = false;
    });
  }

  function bindEvents() {
    els.loginForm.addEventListener('submit', login);
    els.setupForm.addEventListener('submit', createAccount);
    els.codeForm.addEventListener('submit', submitCode);
    els.codeBack.addEventListener('click', backToPassword);
    els.twoStepBtn.addEventListener('click', openTwoStep);
    els.twoStepForm.addEventListener('submit', submitTwoStep);
    els.twoStepDialog.addEventListener('cancel', (event) => {
      if (twoStep.stage === 'done') event.preventDefault(); // Esc too
    });
    els.twoStepDialog.addEventListener('click', (event) => {
      if (twoStep.stage === 'done') return; // recovery codes stay until "I've saved them"
      if (event.target === els.twoStepDialog || event.target.closest('[data-close]')) {
        els.twoStepDialog.close();
      }
    });
    els.logoutBtn.addEventListener('click', logout);
    els.themeBtn.addEventListener('click', cycleTheme);
    els.captureForm.addEventListener('submit', capture);
    els.uploadInput.addEventListener('change', uploadFile);
    els.timezoneSelect.addEventListener('change', () => saveSettings('timezone'));
    let timeTimer = 0; // typing a time changes it digit by digit: save once it settles
    els.reminderTime.addEventListener('change', () => {
      clearTimeout(timeTimer);
      timeTimer = setTimeout(() => saveSettings('reminder_time'), 700);
    });
    els.taskForm.addEventListener('submit', saveTask);
    els.reviewForm.addEventListener('submit', saveReview);
    els.reviewReject.addEventListener('click', rejectReview);
    els.reviewMakeTask.addEventListener('change', () => {
      els.reviewTaskFields.hidden = !els.reviewMakeTask.checked;
    });
    window.addEventListener('hashchange', route);
    els.pushOnBtn.addEventListener('click', turnPushOn);
    els.pushOffBtn.addEventListener('click', turnPushOff);
    els.pushTestBtn.addEventListener('click', sendTestPush);
    els.readerSelect.addEventListener('change', saveReader);
    els.activityMore.addEventListener('click', () =>
      renderActivity(true).catch((error) => showToast(error.message)),
    );
    document
      .querySelectorAll('[data-delete]')
      .forEach((b) => b.addEventListener('click', () => openDelete(b.dataset.delete)));
    els.deleteForm.addEventListener('submit', confirmDelete);
    els.deleteDialog.addEventListener('click', (event) => {
      if (event.target === els.deleteDialog || event.target.closest('[data-close]')) els.deleteDialog.close();
    });
    document.addEventListener('click', (event) => closeMenus(event.target.closest('details.more')));
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMenus(null);
    });
    document
      .querySelectorAll('[data-new-task]')
      .forEach((b) => b.addEventListener('click', () => openTaskDialog({ mode: 'new' })));
    document.querySelectorAll('[data-inbox-show]').forEach((seg) =>
      seg.addEventListener('click', () => {
        state.inboxShow = seg.dataset.inboxShow;
        syncSegments('[data-inbox-show]', state.inboxShow);
        renderInbox().catch((error) => showToast(error.message));
      }),
    );
    document.querySelectorAll('[data-task-show]').forEach((seg) =>
      seg.addEventListener('click', () => {
        state.taskShow = seg.dataset.taskShow;
        syncSegments('[data-task-show]', state.taskShow);
        renderTasks().catch((error) => showToast(error.message));
      }),
    );
    [els.taskDialog, els.confirmDialog].forEach((dialog) =>
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog || event.target.closest('[data-close]')) dialog.close();
      }),
    );
    els.confirmBtn.addEventListener('click', () => {
      els.confirmDialog.close();
      if (state.confirm) state.confirm();
      state.confirm = null;
    });
  }

  async function init() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      applyTheme(['light', 'dark'].includes(saved) ? saved : 'auto');
    } catch {
      applyTheme('auto');
    }
    bindEvents();
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {}); // the app still works without it
    }
    try {
      await startSession();
    } catch {
      showLogin();
    }
  }

  init();
})();
