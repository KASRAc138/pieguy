'use strict';
/* PieGuy settings UI — vanilla JS, talks to Python through QWebChannel (`bridge`). */

// ================================================================ bridge
let B = null;
const call = (name, ...args) => new Promise(res => {
  if (!B || typeof B[name] !== 'function') return res(null);
  B[name](...args, res);
});

function connectBridge(cb) {
  if (window.qt && window.qt.webChannelTransport && window.QWebChannel) {
    new QWebChannel(qt.webChannelTransport, ch => { B = ch.objects.bridge; cb(); });
  } else { B = makeMock(); cb(); }
}

function makeMock() {  // lets the UI run in a normal browser for design work
  const sig = () => { const f = []; return { connect: fn => f.push(fn), emit: v => f.forEach(x => x(v)) }; };
  const st = window.__MOCK_STATE || { config: { settings: {}, pies: [], contacts: [], flows: [], history: {} }, meta: { version: 'dev', uia: true }, startup: false };
  const m = {
    triggerCaptured: sig(), recordingDone: sig(), elementPicked: sig(), notice: sig(),
    getState: cb => cb(JSON.stringify(st)),
    saveConfig: (js, cb) => { st.config = JSON.parse(js); cb(true); },
    runItem: (js, cb) => { toast('▶ (mock) running'); cb && cb(); },
    runSteps: (js, cb) => { toast('▶ (mock) running'); cb && cb(); },
    captureTrigger: cb => { setTimeout(() => m.triggerCaptured.emit(JSON.stringify({ kind: 'key', vk: 192, mods: [], name: '`' })), 900); cb && cb(); },
    cancelCapture: cb => cb && cb(),
    startRecording: cb => { setTimeout(() => m.recordingDone.emit(JSON.stringify([
      { id: 's1', type: 'launch', enabled: true, path: 'C:\\Program Files\\Mozilla Firefox\\firefox.exe', if_not_running: true, wait_window: true },
      { id: 's2', type: 'click_element', enabled: true, button: 'left', method: 'element', timeout: 8, summary: 'Button “Attach” in firefox', target: { window: { process: 'firefox.exe', title: 'Telegram Web' }, element: { name: 'Attach', type_name: 'ButtonControl' }, rx: .5, ry: .9 } },
      { id: 's3', type: 'type_text', enabled: true, text: 'hello there' },
      { id: 's4', type: 'hotkey', enabled: true, keys: 'enter' }])), 700); cb && cb(); },
    pickElement: cb => { setTimeout(() => m.elementPicked.emit(JSON.stringify({ label: 'Button “Send” in firefox', window: { process: 'firefox.exe', title: 'Telegram' }, element: { name: 'Send', type_name: 'ButtonControl' }, rx: .9, ry: .95 })), 600); cb && cb(); },
    browseFile: (t, f, cb) => cb('C:\\Tools\\app.exe'),
    browseFolder: cb => cb('C:\\Users\\me\\Downloads'),
    listWindows: cb => cb(JSON.stringify([{ title: 'Telegram Web — Mozilla Firefox', process: 'firefox.exe' }, { title: 'v2rayN', process: 'v2rayn.exe' }, { title: 'Downloads', process: 'explorer.exe' }])),
    setStartup: (on, cb) => cb(on), openConfigFolder: cb => cb && cb(), openUrl: (u, cb) => cb && cb(),
    exportConfig: cb => cb('pieguy-config.json'), importConfig: cb => cb(''), resetConfig: cb => cb(JSON.stringify(st.config)),
    detectV2rayN: cb => cb('D:\\Apps\\v2rayN\\v2rayN.exe'), detectFirefox: cb => cb('C:\\Program Files\\Mozilla Firefox\\firefox.exe'),
    iconFor: (p, cb) => cb(''), previewPie: (id, cb) => { toast('Live preview opens on your desktop'); cb && cb(); },
    proxyState: cb => cb(JSON.stringify({ on: false, server: '' })),
  };
  return m;
}

// ================================================================= utils
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const uid = (p = 'i') => p + '_' + Math.random().toString(16).slice(2, 10);
const clone = o => JSON.parse(JSON.stringify(o));
const isPath = s => typeof s === 'string' && s.length > 3 && (/^[a-zA-Z]:[\\/]/.test(s) || s.startsWith('\\\\'));
const base = p => String(p || '').split(/[\\/]/).pop();
function toast(msg, ms = 2200) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), ms);
}
function ago(ts) {
  if (!ts) return 'never used';
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now'; if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago'; return Math.round(s / 86400) + ' d ago';
}
function tgUrl(chat, ver = 'k') {
  chat = (chat || '').trim();
  if (!chat) return `https://web.telegram.org/${ver}/`;
  if (chat.includes('web.telegram.org')) return chat.startsWith('http') ? chat : 'https://' + chat;
  if (chat.includes('{')) return chat;
  const m = chat.match(/^(?:https?:\/\/)?(?:t\.me|telegram\.me)\/([A-Za-z0-9_+]+)/); if (m) chat = m[1];
  chat = chat.replace(/^@/, '');
  if (/^\+\d+$/.test(chat)) return `https://web.telegram.org/${ver}/#?tgaddr=tg%3A%2F%2Fresolve%3Fphone%3D${chat.slice(1)}`;
  if (/^-?\d+$/.test(chat)) return `https://web.telegram.org/${ver}/#${chat}`;
  if (ver === 'a') return `https://web.telegram.org/a/#?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3D${chat}`;
  return `https://web.telegram.org/k/#@${chat}`;
}
const ICONS = {};  // path -> dataURL
function iconHTML(icon, cls = '') {
  if (isPath(icon)) {
    if (ICONS[icon] === undefined) { ICONS[icon] = ''; call('iconFor', icon).then(u => { ICONS[icon] = u || ''; renderPage(); }); }
    return ICONS[icon] ? `<img class="${cls}" src="${ICONS[icon]}">` : '🖼️';
  }
  return esc(icon || '•');
}

// ================================================================= state
const S = { cfg: null, meta: {}, startup: false, page: 'pie', pieId: null, path: [], sel: -1, open: {}, flowId: null, recSteps: null, recTarget: null };
const undoStack = [], redoStack = [];
let lastPush = { key: null, t: 0 };

function snapshot(key) {
  const now = Date.now();
  if (key && lastPush.key === key && now - lastPush.t < 900) { lastPush.t = now; return; }
  undoStack.push(JSON.stringify(S.cfg)); if (undoStack.length > 80) undoStack.shift();
  redoStack.length = 0; lastPush = { key, t: now };
}
function mutate(fn, { key = null, render = true } = {}) {
  snapshot(key); fn(); save(); if (render) renderPage();
}
function undo() { if (!undoStack.length) return; redoStack.push(JSON.stringify(S.cfg)); S.cfg = JSON.parse(undoStack.pop()); lastPush = {}; fixSel(); save(); renderPage(); toast('↶ Undone'); }
function redo() { if (!redoStack.length) return; undoStack.push(JSON.stringify(S.cfg)); S.cfg = JSON.parse(redoStack.pop()); fixSel(); save(); renderPage(); toast('↷ Redone'); }

let saveTimer = null;
function save() {
  const el = $('#saved'); el.textContent = '● Saving…'; el.classList.remove('pulse');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const ok = await call('saveConfig', JSON.stringify(S.cfg));
    el.textContent = ok === false ? '⚠ Save failed' : '✓ All changes saved'; el.classList.add('pulse');
    setTimeout(() => el.classList.remove('pulse'), 900);
  }, 300);
}

const pie = () => S.cfg.pies.find(p => p.id === S.pieId) || S.cfg.pies[0];
function levelItems() {
  let items = pie()?.items || [];
  for (const i of S.path) { const it = items[i]; if (!it) break; it.children = it.children || []; items = it.children; }
  return items;
}
const selItem = () => levelItems()[S.sel] || null;
function fixSel() {
  if (!S.cfg.pies.length) S.cfg.pies.push(newPie('Main'));
  if (!pie() || pie().id !== S.pieId) S.pieId = S.cfg.pies[0].id;
  let items = pie().items, ok = [];
  for (const i of S.path) { if (!items[i] || items[i].kind !== 'submenu') break; ok.push(i); items = items[i].children || []; }
  S.path = ok; if (S.sel >= levelItems().length) S.sel = levelItems().length - 1;
}
function newPie(name) { return { id: uid('pie'), name, enabled: true, apps: [], tap: 'sticky', trigger: { kind: 'key', vk: 0, mods: [], name: '' }, items: [] }; }

// ============================================================ step types
const CATS = {
  apps: ['Apps & web', '#fb923c'], win: ['Windows', '#60a5fa'], input: ['Keyboard & mouse', '#f472b6'],
  clip: ['Clipboard & files', '#facc15'], net: ['Network & proxy', '#34d399'], tg: ['Telegram', '#38bdf8'], flow: ['Flow', '#a78bfa'],
};
const BROWSERS = [['firefox', 'Firefox'], ['chrome', 'Chrome'], ['edge', 'Edge'], ['default', 'Default browser']];
const T = {
  launch: { cat: 'apps', icon: '🚀', title: 'Launch app', desc: 'Start a program, shortcut or file',
    d: { path: '', args: '', if_not_running: true, wait_window: false, admin: false },
    f: [{ k: 'path', l: 'Program, shortcut or file', kind: 'file', full: 1, ph: 'firefox  ·  C:\\…\\app.exe  ·  shortcut.lnk' },
        { k: 'args', l: 'Arguments', ph: 'optional' }, { k: 'cwd', l: 'Start in', ph: 'optional folder' },
        { k: 'if_not_running', l: 'If already running, just focus it', kind: 'bool' }, { k: 'wait_window', l: 'Wait for its window', kind: 'bool' },
        { k: 'admin', l: 'Run as administrator', kind: 'bool' }],
    sum: s => base(s.path) || 'nothing set' },
  open_url: { cat: 'apps', icon: '🌐', title: 'Open URL', desc: 'Open a link in a chosen browser',
    d: { url: '', browser: 'firefox', reuse_title: '' },
    f: [{ k: 'url', l: 'URL', full: 1, ph: 'https://…  (variables like {clipboard} work)' },
        { k: 'browser', l: 'Browser', kind: 'select', o: BROWSERS },
        { k: 'reuse_title', l: 'Reuse tab titled', ph: 'e.g. Telegram (optional)' }],
    sum: s => (s.url || '—') + (s.browser && s.browser !== 'default' ? ` · ${s.browser}` : '') },
  open_path: { cat: 'apps', icon: '📂', title: 'Open file / folder', desc: 'Open with its default app',
    d: { path: '' }, f: [{ k: 'path', l: 'Path', kind: 'file', full: 1, ph: 'C:\\…  or {file}' }], sum: s => base(s.path) || '—' },
  close_app: { cat: 'apps', icon: '🛑', title: 'Close app', desc: 'Quit a program by process name',
    d: { process: '', force: false }, f: [{ k: 'process', l: 'Process', ph: 'e.g. telegram.exe' }, { k: 'force', l: 'Force kill', kind: 'bool' }],
    sum: s => s.process || '—' },
  run_command: { cat: 'apps', icon: '⌨️', title: 'Run command', desc: 'CMD or PowerShell command',
    d: { command: '', shell: 'cmd', hidden: true, wait: false, var: 'output' },
    f: [{ k: 'command', l: 'Command', kind: 'area', full: 1 }, { k: 'shell', l: 'Shell', kind: 'select', o: [['cmd', 'CMD'], ['powershell', 'PowerShell']] },
        { k: 'var', l: 'Save output to', ph: 'output', show: s => s.wait }, { k: 'hidden', l: 'Hide window', kind: 'bool' }, { k: 'wait', l: 'Wait until it finishes', kind: 'bool' }],
    sum: s => (s.command || '—').split('\n')[0] },

  focus_window: { cat: 'win', icon: '🎯', title: 'Focus window', desc: 'Bring a window to front (waits for it)',
    d: { title: '', process: '', timeout: 10 }, f: [{ k: '_win', kind: 'window', full: 1 }, { k: 'title', l: 'Title contains', ph: 're:regex also works' },
        { k: 'process', l: 'Process', ph: 'firefox.exe' }, { k: 'timeout', l: 'Timeout (s)', kind: 'num' }],
    sum: s => [s.title && `“${s.title}”`, s.process].filter(Boolean).join(' · ') || '—' },
  wait_window: { cat: 'win', icon: '⏳', title: 'Wait for window', desc: 'Pause until a window appears',
    d: { title: '', process: '', timeout: 15 }, f: [{ k: '_win', kind: 'window', full: 1 }, { k: 'title', l: 'Title contains' }, { k: 'process', l: 'Process' }, { k: 'timeout', l: 'Timeout (s)', kind: 'num' }],
    sum: s => [s.title && `“${s.title}”`, s.process].filter(Boolean).join(' · ') || '—' },
  window_op: { cat: 'win', icon: '🪟', title: 'Window action', desc: 'Minimize, maximize, close, pin on top',
    d: { op: 'minimize', target: 'active', title: '', process: '' },
    f: [{ k: 'op', l: 'Action', kind: 'select', o: [['minimize', 'Minimize'], ['maximize', 'Maximize'], ['toggle_max', 'Toggle maximize'], ['restore', 'Restore'], ['close', 'Close'], ['topmost', 'Toggle always-on-top']] },
        { k: 'target', l: 'Window', kind: 'select', o: [['active', 'The window under the pie'], ['match', 'Find by title/process']] },
        { k: 'title', l: 'Title contains', show: s => s.target === 'match' }, { k: 'process', l: 'Process', show: s => s.target === 'match' }],
    sum: s => `${s.op} · ${s.target === 'active' ? 'active window' : (s.title || s.process)}` },

  hotkey: { cat: 'input', icon: '⌨️', title: 'Press keys', desc: 'Shortcut or key sequence',
    d: { keys: '' }, f: [{ k: 'keys', l: 'Keys (click, then press)', kind: 'keys', full: 1 }], sum: s => s.keys || '—' },
  type_text: { cat: 'input', icon: '✍️', title: 'Type text', desc: 'Types any text (Persian too)',
    d: { text: '', paste: false }, f: [{ k: 'text', l: 'Text', kind: 'area', full: 1 }, { k: 'paste', l: 'Paste instead of typing (faster)', kind: 'bool', full: 1 }],
    sum: s => s.text ? `“${s.text.slice(0, 60)}”` : '—' },
  click_element: { cat: 'input', icon: '🖱️', title: 'Click element', desc: 'Clicks a specific button/field — found by what it is, not where',
    d: { target: null, button: 'left', double: false, method: 'element', timeout: 8 },
    f: [{ k: 'target', kind: 'element', full: 1 }, { k: 'button', l: 'Button', kind: 'select', o: [['left', 'Left'], ['right', 'Right'], ['middle', 'Middle']] },
        { k: 'method', l: 'Find by', kind: 'select', o: [['element', 'Element (smart)'], ['position', 'Position in window']] },
        { k: 'double', l: 'Double-click', kind: 'bool' }, { k: 'timeout', l: 'Wait up to (s)', kind: 'num' }],
    sum: s => s.summary || s.target?.label || (s.target ? 'recorded target' : 'no target — pick one') },
  hover: { cat: 'input', icon: '👆', title: 'Hover', desc: 'Rest the mouse on an element (opens hover menus)',
    d: { target: null, ms: 450, timeout: 5 },
    f: [{ k: 'target', kind: 'element', full: 1 }, { k: 'ms', l: 'Stay (ms)', kind: 'num' }, { k: 'timeout', l: 'Wait up to (s)', kind: 'num' }],
    sum: s => s.summary || s.target?.label || 'no target — pick one' },
  click_at: { cat: 'input', icon: '📍', title: 'Click position', desc: 'Click fixed coordinates',
    d: { x: 0, y: 0, relative: 'screen', button: 'left', double: false },
    f: [{ k: 'x', l: 'X', kind: 'num' }, { k: 'y', l: 'Y', kind: 'num' }, { k: 'relative', l: 'Relative to', kind: 'select', o: [['screen', 'Screen'], ['window', 'Active window']] },
        { k: 'button', l: 'Button', kind: 'select', o: [['left', 'Left'], ['right', 'Right'], ['middle', 'Middle']] }, { k: 'double', l: 'Double-click', kind: 'bool' }],
    sum: s => `${s.x}, ${s.y} (${s.relative})` },
  scroll: { cat: 'input', icon: '🖲️', title: 'Scroll', desc: 'Mouse wheel notches (− = down)',
    d: { amount: -3 }, f: [{ k: 'amount', l: 'Notches', kind: 'num' }], sum: s => `${s.amount > 0 ? '↑' : '↓'} ${Math.abs(s.amount)}` },
  drag: { cat: 'input', icon: '✋', title: 'Drag', desc: 'Recorded drag between two spots', hidden: 1,
    d: {}, f: [], sum: s => `${s.from?.window?.process || '?'} → ${s.to?.window?.process || '?'}` },

  get_files: { cat: 'clip', icon: '📎', title: 'Get files', desc: 'Selected in Explorer → copied → ask',
    d: { source: 'auto', var: 'files' },
    f: [{ k: 'source', l: 'Take files from', kind: 'select', full: 1, o: [['auto', 'Smart: Explorer selection → copied files → ask me'], ['explorer', 'Selected in Explorer'], ['clipboard', 'Copied files (clipboard)'], ['dialog', 'Always ask me (file picker)']] },
        { k: 'var', l: 'Save as variable', ph: 'files' }],
    sum: s => ({ auto: 'Explorer selection → clipboard → ask', explorer: 'Explorer selection', clipboard: 'clipboard', dialog: 'file picker' })[s.source] },
  set_clipboard_text: { cat: 'clip', icon: '📋', title: 'Copy text', desc: 'Put text on the clipboard',
    d: { text: '' }, f: [{ k: 'text', l: 'Text', kind: 'area', full: 1 }], sum: s => s.text ? `“${s.text.slice(0, 50)}”` : '—' },
  set_clipboard_files: { cat: 'clip', icon: '🗃️', title: 'Copy files', desc: 'Put files on the clipboard (pasteable)',
    d: { files: '{files}' }, f: [{ k: 'files', l: 'Files (one per line)', kind: 'area', full: 1 }], sum: s => s.files || '—' },
  copy_selection: { cat: 'clip', icon: '✂️', title: 'Copy selection', desc: 'Ctrl+C the selected text into a variable',
    d: { var: 'selection', wait_ms: 250 }, f: [{ k: 'var', l: 'Variable', ph: 'selection' }, { k: 'wait_ms', l: 'Wait (ms)', kind: 'num' }], sum: s => `→ {${s.var || 'selection'}}` },
  ask_text: { cat: 'clip', icon: '💭', title: 'Ask for text', desc: 'Prompt me for a value',
    d: { prompt: '', default: '', var: 'text' }, f: [{ k: 'prompt', l: 'Question', full: 1 }, { k: 'default', l: 'Default' }, { k: 'var', l: 'Variable', ph: 'text' }],
    sum: s => `${s.prompt || 'ask'} → {${s.var || 'text'}}` },

  v2rayn: { cat: 'net', icon: '🛰️', title: 'v2rayN', desc: 'System proxy / TUN mode, no clicking',
    d: { action: 'proxy_on', exe: '', port: '', wait_port: true },
    f: [{ k: 'action', l: 'Do', kind: 'select', full: 1, o: [['proxy_on', 'Start v2rayN + set system proxy'], ['tun_on', 'Enable TUN mode (restarts v2rayN as admin)'], ['tun_off', 'Disable TUN → back to system proxy'], ['proxy_off', 'Clear system proxy'], ['start', 'Just start v2rayN'], ['stop', 'Quit v2rayN + clear proxy']] },
        { k: 'exe', l: 'v2rayN.exe', kind: 'file', ph: 'auto-detect' }, { k: 'port', l: 'Proxy port', ph: 'auto (10808)' },
        { k: 'wait_port', l: 'Wait until the proxy is up', kind: 'bool', full: 1 }],
    sum: s => ({ proxy_on: 'Start + system proxy ON', tun_on: 'TUN mode ON', tun_off: 'TUN OFF → system proxy', proxy_off: 'System proxy OFF', start: 'Start', stop: 'Quit + proxy OFF' })[s.action] },
  system_proxy: { cat: 'net', icon: '🧭', title: 'System proxy', desc: 'Windows proxy on/off (any client)',
    d: { mode: 'on', server: '127.0.0.1:10808', bypass: '' },
    f: [{ k: 'mode', l: 'Mode', kind: 'select', o: [['on', 'On'], ['off', 'Off'], ['toggle', 'Toggle']] }, { k: 'server', l: 'Server', ph: '127.0.0.1:10808' },
        { k: 'bypass', l: 'Bypass list', full: 1, ph: 'default: localhost & LAN' }],
    sum: s => s.mode === 'on' ? `ON · ${s.server}` : s.mode.toUpperCase() },

  telegram_web: { cat: 'tg', icon: '✈️', title: 'Telegram Web', desc: 'Open a chat and send files / text',
    d: { chat: '{contact_url}', content: 'files', text: '', caption: '', browser: 'firefox', reuse_tab: true, load_wait_ms: 3500, send: true },
    f: [{ k: 'chat', l: 'Chat', kind: 'chat', full: 1 },
        { k: 'content', l: 'Send', kind: 'select', o: [['files', 'Files ({files})'], ['text', 'Text'], ['none', 'Nothing — just open the chat']] },
        { k: 'browser', l: 'Browser', kind: 'select', o: BROWSERS },
        { k: 'text', l: 'Text', kind: 'area', full: 1, show: s => s.content === 'text' },
        { k: 'caption', l: 'Caption', full: 1, ph: 'optional', show: s => s.content === 'files' },
        { k: 'load_wait_ms', l: 'Page load wait (ms)', kind: 'num' },
        { k: 'reuse_tab', l: 'Reuse an open Telegram tab', kind: 'bool' }, { k: 'send', l: 'Press Send automatically', kind: 'bool' }],
    sum: s => `${s.content === 'none' ? 'open' : 'send ' + s.content} → ${s.chat === '{contact_url}' ? 'chosen chat' : s.chat || '—'}` },

  wait: { cat: 'flow', icon: '⏱️', title: 'Wait', desc: 'Pause for a moment', d: { ms: 1000 }, f: [{ k: 'ms', l: 'Milliseconds', kind: 'num' }], sum: s => `${s.ms} ms` },
  notify: { cat: 'flow', icon: '🔔', title: 'Notify', desc: 'Show a small toast', d: { text: 'Done ✓' }, f: [{ k: 'text', l: 'Message', full: 1 }], sum: s => s.text },
  run_flow: { cat: 'flow', icon: '🔗', title: 'Run flow', desc: 'Run a reusable flow', d: { flow: '' }, f: [{ k: 'flow', l: 'Flow', kind: 'flow', full: 1 }],
    sum: s => S.cfg.flows.find(f => f.id === s.flow)?.name || 'choose a flow' },
};
const typeOf = s => T[s.type] || { cat: 'flow', icon: '❔', title: s.type, f: [], sum: () => '' };
const newStep = type => ({ id: uid('s'), type, enabled: true, ...clone(T[type].d) });

// ============================================================ steps editor
const EDITORS = {};  // id -> () => steps array
function stepsEditor(id, getSteps, opts = {}) {
  EDITORS[id] = getSteps;
  const steps = getSteps();
  const cards = steps.map((s, i) => stepCard(id, s, i)).join('');
  return `<div class="steps" data-ed="${id}">${cards || `<div class="empty"><span class="big">✨</span>No steps yet — add one, or record what you do.</div>`}</div>
    <div class="add-row" style="margin-top:10px">
      <button class="btn" data-act="add-step" data-ed="${id}"><span class="e">＋</span> Add step</button>
      ${opts.record === false ? '' : `<button class="btn" data-act="rec-into" data-ed="${id}"><span class="e">⏺️</span> Record steps</button>`}
    </div>`;
}
function stepCard(ed, s, i) {
  const t = typeOf(s), c = CATS[t.cat][1], open = S.open[s.id];
  return `<div class="step ${open ? 'open' : ''} ${s.enabled === false ? 'off' : ''}" style="--c:${c}" data-sid="${s.id}" data-ed="${ed}">
    <div class="step-h" data-act="toggle">
      <span class="grip" title="Drag to reorder">⋮⋮</span>
      <span class="num">${i + 1}</span>
      <span class="sicon">${t.icon}</span>
      <div class="stext"><div class="stitle">${esc(t.title)}</div><div class="ssum">${esc(t.sum(s) ?? '')}</div></div>
      <div class="sact">
        <label class="toggle" title="Enabled" data-stop><input type="checkbox" data-act="en" ${s.enabled !== false ? 'checked' : ''}><i></i></label>
        <button class="btn ghost icon" data-act="test" title="Run just this step">▶</button>
        <button class="btn ghost icon" data-act="more" title="More">⋯</button>
      </div>
    </div>
    ${open ? `<div class="step-b">${t.f.filter(f => !f.show || f.show(s)).map(f => fieldHTML(s, f)).join('')}
      <div class="adv">
        <div class="field"><label>Then wait (ms)</label><input class="input" type="number" data-f="delay_after" value="${esc(s.delay_after ?? 0)}"></div>
        <div class="field"><label>If it fails</label><select class="input" data-f="on_error">
          <option value="stop" ${s.on_error !== 'continue' ? 'selected' : ''}>Stop the action</option>
          <option value="continue" ${s.on_error === 'continue' ? 'selected' : ''}>Skip & continue</option></select></div>
      </div></div>` : ''}
  </div>`;
}
function fieldHTML(s, f) {
  const v = s[f.k], cls = f.full ? 'field full' : 'field', lab = f.l ? `<label>${esc(f.l)}</label>` : '';
  switch (f.kind) {
    case 'bool': return `<label class="check ${f.full ? 'full' : ''}"><span class="toggle"><input type="checkbox" data-f="${f.k}" ${v ? 'checked' : ''}><i></i></span><span>${esc(f.l)}</span></label>`;
    case 'select': return `<div class="${cls}">${lab}<select class="input" data-f="${f.k}">${f.o.map(([a, b]) => `<option value="${a}" ${v === a ? 'selected' : ''}>${esc(b)}</option>`).join('')}</select></div>`;
    case 'num': return `<div class="${cls}">${lab}<input class="input" type="number" data-f="${f.k}" value="${esc(v ?? 0)}"></div>`;
    case 'area': return `<div class="${cls}">${lab}<textarea class="input" data-f="${f.k}" rows="3" placeholder="${esc(f.ph || '')}">${esc(v || '')}</textarea></div>`;
    case 'file': return `<div class="field full">${lab}<div class="row"><input class="input" data-f="${f.k}" value="${esc(v || '')}" placeholder="${esc(f.ph || '')}"><button class="btn sm" data-act="browse" data-k="${f.k}">Browse…</button></div></div>`;
    case 'keys': return `<div class="${cls}">${lab}<div class="row"><input class="input keys-input" readonly data-keys="${f.k}" value="${esc(v || '')}" placeholder="Click here, then press keys…"><button class="btn sm ghost" data-act="keys-clear" data-k="${f.k}">Clear</button></div>
      <div class="chips" style="margin-top:6px">${['ctrl+c', 'ctrl+v', 'alt+tab', 'win+d', 'ctrl+shift+t', 'enter', 'esc', 'media_play_pause', 'media_next', 'volume_up', 'volume_mute'].map(k => `<span class="chip" data-act="keys-add" data-k="${f.k}" data-v="${k}">${k}</span>`).join('')}</div>
      <div class="hint" style="margin-top:4px">Several combos run in order, e.g. <code>ctrl+a ctrl+c</code>.</div></div>`;
    case 'flow': return `<div class="${cls}">${lab}<select class="input" data-f="${f.k}"><option value="">— choose —</option>${S.cfg.flows.map(fl => `<option value="${fl.id}" ${v === fl.id ? 'selected' : ''}>${esc(fl.icon || '🔗')} ${esc(fl.name)}</option>`).join('')}</select></div>`;
    case 'window': return `<div class="${cls}"><button class="btn sm" data-act="pickwin" style="align-self:flex-start"><span class="e">🪟</span> Pick from open windows</button></div>`;
    case 'chat': return `<div class="${cls}">${lab}<div class="row"><input class="input" data-f="${f.k}" value="${esc(v || '')}" placeholder="@username, t.me link, or {contact_url}"><button class="btn sm" data-act="pickchat">💬 Chats</button></div>
      <div class="hint">${v === '{contact_url}' ? 'Uses the chat you pick in the pie (Contacts slice).' : esc(tgUrl(v, S.cfg.settings.telegram_version))}</div></div>`;
    case 'element': {
      const tg = s.target;
      return `<div class="${cls}"><label>Target</label><div class="elem-card"><span class="sicon" style="--c:#f472b6">${tg ? '🎯' : '❔'}</span>
        <div class="t"><b>${esc(s.summary || tg?.label || (tg ? 'Recorded target' : 'Nothing picked yet'))}</b>
        <span class="hint">${tg ? esc(`${tg.window?.process || ''} · “${(tg.window?.title || '').slice(0, 40)}”`) : 'Pick the button or field to click'}</span></div>
        <button class="btn sm primary" data-act="pick">🎯 Pick</button></div>
        ${S.meta.uia === false ? '<div class="warn" style="margin-top:8px">Element mode needs the <code>uiautomation</code> package (run install.bat). Position fallback still works.</div>' : ''}</div>`;
    }
    default: return `<div class="${cls}">${lab}<input class="input" data-f="${f.k}" value="${esc(v ?? '')}" placeholder="${esc(f.ph || '')}"></div>`;
  }
}
const edSteps = el => EDITORS[el.closest('[data-ed]')?.dataset.ed]?.() || [];
const stepOf = el => { const sid = el.closest('[data-sid]')?.dataset.sid; return edSteps(el).find(s => s.id === sid); };
function refreshCard(el) {
  const card = el.closest('.step'); if (!card) return;
  const s = stepOf(card); if (!s) return;
  const t = typeOf(s); $('.ssum', card).textContent = t.sum(s) ?? '';
  card.classList.toggle('off', s.enabled === false);
}

// palette
function openPalette(onPick) {
  const cats = Object.entries(CATS).map(([k, [name, c]]) => {
    const tiles = Object.entries(T).filter(([, t]) => t.cat === k && !t.hidden).map(([type, t]) =>
      `<button class="ptile" style="--c:${c}" data-type="${type}" data-q="${esc((t.title + ' ' + t.desc).toLowerCase())}"><span class="sicon">${t.icon}</span><span><b>${esc(t.title)}</b><span class="d">${esc(t.desc)}</span></span></button>`).join('');
    return `<div class="pcat" data-cat="${k}"><div class="palette-cat" style="--c:${c}">${name}</div><div class="pgrid">${tiles}</div></div>`;
  }).join('');
  const m = modal(`<div class="modal-h"><h2>Add a step</h2><input class="input" id="pal-q" placeholder="Search… (proxy, click, telegram, keys)" style="width:280px"></div><div class="modal-b">${cats}</div>`);
  const q = $('#pal-q', m); setTimeout(() => q.focus(), 50);
  q.oninput = () => { const v = q.value.toLowerCase(); $$('.ptile', m).forEach(t => t.style.display = t.dataset.q.includes(v) ? '' : 'none'); $$('.pcat', m).forEach(c => c.style.display = $$('.ptile', c).some(t => t.style.display !== 'none') ? '' : 'none'); };
  m.addEventListener('click', e => { const t = e.target.closest('.ptile'); if (t) { closeModal(); onPick(t.dataset.type); } });
}

// ================================================================ modals
function modal(html, cls = '') {
  closeModal();
  const bg = document.createElement('div'); bg.className = 'modal-bg'; bg.innerHTML = `<div class="modal ${cls}">${html}</div>`;
  bg.addEventListener('mousedown', e => { if (e.target === bg) closeModal(); });
  document.body.appendChild(bg); return bg.firstElementChild;
}
function closeModal() { $$('.modal-bg').forEach(m => { m.dispatchEvent(new Event('closing')); m.remove(); }); }
function popMenu(anchor, entries) {
  $$('.menu').forEach(m => m.remove());
  const m = document.createElement('div'); m.className = 'menu';
  m.innerHTML = entries.map((e, i) => e === '-' ? '<div style="height:1px;background:var(--line);margin:4px"></div>' : `<button data-i="${i}"><span class="e">${e.icon || ''}</span>${esc(e.label)}</button>`).join('');
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(r.left, innerWidth - m.offsetWidth - 10) + 'px';
  m.style.top = (r.bottom + m.offsetHeight + 8 > innerHeight ? r.top - m.offsetHeight - 6 : r.bottom + 6) + 'px';
  m.addEventListener('click', e => { const b = e.target.closest('button'); if (b) { m.remove(); entries[+b.dataset.i].fn(); } });
  setTimeout(() => document.addEventListener('mousedown', function h(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('mousedown', h); } }), 0);
}

const EMOJIS = ('📤 📥 📎 📁 📂 🗂️ 📋 📝 ✏️ 🖊️ 📌 📍 🔗 🧷 💾 🗃️ 🗑️ 🔍 ' +
  '💬 ✈️ 📨 📧 📞 🔔 📣 💌 🗨️ 👥 👤 ❤️ ⭐ 🔥 ✨ ⚡ 💡 🎯 🏷️ ' +
  '🌐 🛰️ 🛡️ 🔒 🔓 🔑 🧭 📡 🟢 🔴 ⭕ ✅ ❌ ⚠️ ⛔ 🔁 🔄 ' +
  '🚀 🦊 🧩 🛠️ ⚙️ 🔧 🧪 💻 🖥️ ⌨️ 🖱️ 🪟 📸 🎥 🖼️ 🎨 🧠 🤖 ' +
  '🎵 ⏯️ ⏭️ ⏮️ 🔊 🔉 🔇 🎧 🎮 🕹️ 📺 ⏰ ⏱️ 🗓️ ☕ 🍕 🏠 🌙 ☀️ ' +
  '1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ 🅰️ 🅱️ 🔵 🟣 🟡 🟠 ⚪ ⚫ 💎 🪄 🎁 🧿 🥧 🍀').split(' ');
const COLORS = ['#8b5cf6', '#a78bfa', '#6366f1', '#38bdf8', '#22d3ee', '#2dd4bf', '#34d399', '#a3e635', '#facc15', '#fbbf24', '#fb923c', '#f87171', '#f472b6', '#e879f9', '#94a3b8'];
function openEmoji(current, onPick) {
  const m = modal(`<div class="modal-h"><h2>Choose an icon</h2><input class="input" id="em-t" value="${esc(isPath(current) ? '' : current)}" placeholder="Type or paste any emoji / text" style="width:230px"><button class="btn" id="em-ok">Use</button></div>
    <div class="modal-b"><div class="emoji-grid">${EMOJIS.filter(Boolean).map(e => `<button data-e="${e}">${e}</button>`).join('')}</div>
    <div class="row" style="margin-top:16px"><button class="btn" id="em-file"><span class="e">🖼️</span> Use a program's icon or an image…</button><span class="hint">Pick any .exe / .lnk / .png / .ico</span></div></div>`);
  m.addEventListener('click', async e => {
    const b = e.target.closest('[data-e]'); if (b) { closeModal(); onPick(b.dataset.e); }
    if (e.target.closest('#em-ok')) { const v = $('#em-t', m).value.trim(); if (v) { closeModal(); onPick(v); } }
    if (e.target.closest('#em-file')) { const p = await call('browseFile', 'Choose an icon source', 'Icons and programs (*.exe *.lnk *.png *.ico *.svg *.jpg);;All files (*.*)'); if (p) { closeModal(); onPick(p); } }
  });
}

// ================================================================ pie SVG
function arcPath(r0, r1, a1, a2, gap) {
  const gi = gap / r0, go = gap / r1, P = (r, a) => `${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`;
  const b1 = a1 + go, b2 = a2 - go, c1 = a1 + gi, c2 = a2 - gi, large = (b2 - b1) > Math.PI ? 1 : 0, largeI = (c2 - c1) > Math.PI ? 1 : 0;
  return `M ${P(r0, c1)} L ${P(r1, b1)} A ${r1} ${r1} 0 ${large} 1 ${P(r1, b2)} L ${P(r0, c2)} A ${r0} ${r0} 0 ${largeI} 0 ${P(r0, c1)} Z`;
}
function pieSVG(items, { sel = -1, title = '', sub = '', R = 215, r0 = 72, gap = 3, labels = true, uidp = 'p' } = {}) {
  const st = S.cfg.settings, A = st.accent || '#8b5cf6', A2 = st.accent2 || '#22d3ee', n = Math.max(1, items.length), step = 2 * Math.PI / n;
  let defs = `<radialGradient id="${uidp}glow"><stop offset=".55" stop-color="${A}" stop-opacity=".35"/><stop offset=".8" stop-color="${A2}" stop-opacity=".1"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
    <radialGradient id="${uidp}disc"><stop offset="0" stop-color="#1e1b30"/><stop offset="1" stop-color="#0e0e18"/></radialGradient>
    <linearGradient id="${uidp}ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${A}"/><stop offset="1" stop-color="${A2}"/></linearGradient>
    <filter id="${uidp}blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7"/></filter>`;
  let body = '';
  items.forEach((it, i) => {
    const c = it.color || A, mid = -Math.PI / 2 + i * step, a1 = mid - step / 2, a2 = mid + step / 2, on = i === sel;
    const push = on ? 12 : 0, dx = Math.cos(mid) * push, dy = Math.sin(mid) * push;
    defs += `<radialGradient id="${uidp}g${i}" cx="0" cy="0" r="${R}" gradientUnits="userSpaceOnUse"><stop offset="${r0 / R}" stop-color="#26243a" stop-opacity=".95"/><stop offset="1" stop-color="${c}" stop-opacity="${on ? .85 : .28}"/></radialGradient>`;
    const d = items.length === 1 ? null : arcPath(r0 + 4, R, a1, a2, gap);
    const rm = (r0 + R) / 2 + 4, ix = Math.cos(mid) * rm, iy = Math.sin(mid) * rm;
    const icon = isPath(it.icon) ? (ICONS[it.icon] ? `<image href="${ICONS[it.icon]}" x="${ix - 15}" y="${iy - 26}" width="30" height="30"/>` : (iconHTML(it.icon), `<text x="${ix}" y="${iy - 4}" font-size="26" text-anchor="middle" font-family="var(--emoji)">🖼️</text>`))
      : `<text x="${ix}" y="${iy - 2}" font-size="28" text-anchor="middle" font-family="var(--emoji)">${esc(it.icon || '•')}</text>`;
    const arcw = Math.max(56, Math.min(120, rm * step - 12)), maxch = Math.floor(arcw / 6.4);
    const lab = (it.label || '').length > maxch ? it.label.slice(0, maxch - 1) + '…' : (it.label || '');
    const shape = d ? `<path class="body" d="${d}" fill="url(#${uidp}g${i})" stroke="${c}" stroke-opacity="${on ? 1 : .25}" stroke-width="${on ? 2 : 1}"/>
        <path d="${arcPath(R - 4, R, a1, a2, gap)}" fill="${c}" opacity="${on ? 1 : .7}"/>`
      : `<circle r="${R}" fill="url(#${uidp}g${i})" stroke="${c}" stroke-opacity=".5"/><circle r="${r0 + 4}" fill="#12111c"/>`;
    const glow = on && d ? `<path d="${d}" fill="none" stroke="${c}" stroke-width="10" opacity=".45" filter="url(#${uidp}blur)"/>` : '';
    const chev = it.kind === 'submenu' || it.kind === 'contacts' ? `<g transform="translate(${Math.cos(mid) * (R + 12)} ${Math.sin(mid) * (R + 12)}) rotate(${mid * 180 / Math.PI})"><path d="M-3.5 -5.5 L4.5 0 L-3.5 5.5 Z" fill="${c}" stroke="${c}" stroke-width="1.5" stroke-linejoin="round"/></g>` : '';
    const badge = it.kind === 'contacts' ? `<circle cx="${ix + 16}" cy="${iy - 24}" r="9" fill="${c}"/><text x="${ix + 16}" y="${iy - 20.5}" font-size="10" font-weight="800" fill="#0b0a13" text-anchor="middle">${S.cfg.contacts.length}</text>` : '';
    body += `<g class="sl ${on ? 'sel' : ''}" data-i="${i}" transform="translate(${dx.toFixed(1)} ${dy.toFixed(1)})">${glow}${shape}${icon}${badge}
      ${labels ? `<text x="${ix}" y="${iy + 24}" font-size="12.5" font-weight="${on ? 700 : 600}" fill="#fff" fill-opacity="${on ? 1 : .85}" text-anchor="middle" font-family="var(--font)">${esc(lab)}</text>` : ''}${chev}</g>`;
  });
  if (!items.length) body += `<circle r="${R}" fill="none" stroke="rgba(255,255,255,.14)" stroke-dasharray="6 8"/>`;
  const hub = `<g class="hub" data-hub="1"><circle r="${r0 - 6}" fill="#1a1828" stroke="url(#${uidp}ring)" stroke-width="2"/>
    <text y="-2" font-size="14" font-weight="800" fill="#fff" text-anchor="middle" font-family="var(--display)">${esc((title || '').slice(0, 14))}</text>
    <text y="16" font-size="10.5" font-weight="600" fill="${A2}" text-anchor="middle">${esc(sub)}</text></g>`;
  return `<defs>${defs}</defs><circle r="${R + 64}" fill="url(#${uidp}glow)"/><circle r="${R + 5}" fill="url(#${uidp}disc)" stroke="url(#${uidp}ring)" stroke-width="1.3" stroke-opacity=".8"/>${body}${hub}`;
}

// ============================================================ page: pie
function triggerHTML(t) {
  if (!t || (!t.vk && !t.button)) return `<span class="hint">No trigger</span>`;
  const keys = [...(t.mods || []).map(m => m[0].toUpperCase() + m.slice(1)), t.name || '?'];
  return keys.map(k => `<span class="kbd">${esc(k)}</span>`).join('<span class="plus">+</span>');
}
function renderPiePage() {
  const p = pie(), items = levelItems(), st = S.cfg.settings;
  $('#pie-tabs').innerHTML = S.cfg.pies.map(x => `<button class="tab ${x.id === p.id ? 'on' : ''}" data-act="pie-tab" data-id="${x.id}">${x.enabled === false ? '⏸ ' : '🥧 '}${esc(x.name)}</button>`).join('') +
    `<button class="tab add" data-act="pie-new">＋ New pie</button>`;
  // crumbs
  let chain = [{ label: p.name, depth: 0 }], its = p.items;
  S.path.forEach((i, d) => { chain.push({ label: its[i].label, depth: d + 1 }); its = its[i].children || []; });
  $('#crumbs').innerHTML = chain.map((c, i) => `<a class="${i === chain.length - 1 ? 'cur' : ''}" data-act="crumb" data-d="${c.depth}">${i === 0 ? '🥧 ' : ''}${esc(c.label)}</a>`).join('<span class="sep">›</span>');
  const title = S.path.length ? chain[chain.length - 1].label : p.name;
  const sub = S.path.length ? '↑ back' : (items.length ? `${items.length} slices` : '+ add slice');
  $('#pie').innerHTML = pieSVG(items, { sel: S.sel, title, sub, gap: st.gap ?? 3, labels: st.show_labels !== false });
  $('#stage-foot').innerHTML = `
    <div class="trigger-chip"><span class="hint">Hold</span>${triggerHTML(p.trigger)}<button class="btn sm ghost" data-act="capture">Change</button></div>
    <select class="input" style="width:auto" data-pie-f="tap" title="What a quick tap does">
      <option value="sticky" ${p.tap === 'sticky' ? 'selected' : ''}>Tap: keep open</option>
      <option value="passthrough" ${p.tap === 'passthrough' ? 'selected' : ''}>Tap: normal key</option>
      <option value="close" ${p.tap === 'close' ? 'selected' : ''}>Tap: nothing</option></select>
    <button class="btn primary" data-act="add-slice"><span class="e">＋</span> Add slice</button>
    <button class="btn" data-act="preview"><span class="e">👁️</span> Try it live</button>`;
  renderInspector();
}
function renderInspector() {
  const it = selItem(), el = $('#inspector'), p = pie();
  if (!it) {
    el.innerHTML = `<div class="insp-head"><div class="sec-title"><h3>🥧 Pie settings</h3>
        <label class="check"><span class="toggle"><input type="checkbox" data-pie-f="enabled" ${p.enabled !== false ? 'checked' : ''}><i></i></span>Enabled</label></div>
      <div class="field"><label>Name</label><input class="input" data-pie-f="name" value="${esc(p.name)}"></div></div>
      <div class="insp-body scroll">
        <div class="field"><label>Trigger</label><div class="row">${triggerHTML(p.trigger)}<span style="flex:1"></span><button class="btn sm" data-act="capture">🎹 Set trigger</button></div>
          <span class="hint">Hold it anywhere: flick toward a slice and release. Keyboard keys or mouse middle / side buttons.</span></div>
        <div class="field"><label>Only in these apps</label>${chipsInput('apps', p.apps || [], 'e.g. firefox.exe — empty = everywhere')}
          <span class="hint">An app-specific pie wins over a global one with the same trigger.</span></div>
        <div class="info">💡 <b>Click a slice</b> to edit it. <b>Double-click</b> a 📂 submenu to go inside. In the real pie: flick & release, or tap the trigger and click; <code>1-9</code> pick, <code>Esc</code> closes, right-click goes back.</div>
        ${S.cfg.pies.length > 1 ? `<button class="btn danger" data-act="pie-del">🗑 Delete this pie</button>` : ''}
      </div>`;
    return;
  }
  const kinds = [['action', '⚡', 'Action'], ['submenu', '📂', 'Submenu'], ['contacts', '💬', 'Chats']];
  let body = '';
  if (it.kind === 'submenu') {
    const kids = it.children || [];
    body = `<div class="sec-title"><h3>📂 Sub-pie <span class="pill">${kids.length}</span></h3><button class="btn sm primary" data-act="dive">Open sub-pie →</button></div>
      <div class="chips">${kids.map(k => `<span class="chip" data-act="dive">${iconHTML(k.icon)} ${esc(k.label)}</span>`).join('') || '<span class="hint">Empty — open it and add slices.</span>'}</div>
      <div class="hint">In the pie, cross the outer edge of this slice (or hover a moment) to open the sub-pie.</div>`;
  } else if (it.kind === 'contacts') {
    const useFlow = !!it.flow && !(it.steps || []).length;
    body = `<div class="info">💬 Shows your <b>chats</b> (Chats page) as a sub-pie — most recently used first. Picking one runs the steps below with <code>{contact_url}</code> and <code>{contact_name}</code> set.</div>
      <div class="kv"><div class="field"><label>Max chats shown</label><input class="input" type="number" min="1" max="12" data-item-f="max" value="${it.max || 8}"></div>
        <div class="field"><label>Chats</label><button class="btn" data-act="go" data-page="contacts">${S.cfg.contacts.length} chats · Manage →</button></div></div>
      <div class="field"><label>When a chat is picked, run</label><div class="seg">
        <button class="${useFlow ? 'on' : ''}" data-act="ct-mode" data-v="flow">🔗 A flow</button><button class="${!useFlow ? 'on' : ''}" data-act="ct-mode" data-v="steps">⚡ Custom steps</button></div></div>
      ${useFlow ? `<div class="field"><label>Flow</label><div class="row"><select class="input" data-item-f="flow">${S.cfg.flows.map(f => `<option value="${f.id}" ${it.flow === f.id ? 'selected' : ''}>${esc(f.icon || '🔗')} ${esc(f.name)}</option>`).join('')}</select>
        <button class="btn sm" data-act="edit-flow" data-id="${it.flow}">Edit flow →</button></div></div>`
        : stepsEditor('item', () => (it.steps = it.steps || []))}`;
  } else {
    const n = (it.steps || []).length;
    body = `<div class="sec-title"><h3>⚡ Steps <span class="pill">${n}</span></h3><button class="btn sm" data-act="run-item" ${n ? '' : 'disabled'}>▶ Test run</button></div>
      ${stepsEditor('item', () => (it.steps = it.steps || []))}
      <div class="hint">Variables: <code>{files}</code> <code>{file}</code> <code>{filename}</code> <code>{clipboard}</code> <code>{date}</code> + anything you save.</div>`;
  }
  el.innerHTML = `<div class="insp-head">
      <div class="idrow"><div class="bigicon" data-act="icon" style="background:linear-gradient(145deg, ${it.color}66, #1b1829)">${iconHTML(it.icon)}</div>
        <input class="input name-input" data-item-f="label" value="${esc(it.label)}" placeholder="Slice name"></div>
      <div class="swatches">${COLORS.map(c => `<span class="sw ${it.color === c ? 'on' : ''}" data-act="color" data-c="${c}" style="background:${c};color:${c}"></span>`).join('')}
        <label class="sw custom" title="Custom color"><input type="color" data-item-f="color" value="${it.color || '#8b5cf6'}"></label></div>
      <div class="row"><div class="seg">${kinds.map(([k, e, l]) => `<button class="${it.kind === k ? 'on' : ''}" data-act="kind" data-v="${k}">${e} ${l}</button>`).join('')}</div>
        <span style="flex:1"></span>
        <button class="btn ghost icon" data-act="move" data-d="-1" title="Move counter-clockwise">↺</button>
        <button class="btn ghost icon" data-act="move" data-d="1" title="Move clockwise">↻</button>
        <button class="btn ghost icon" data-act="dup-slice" title="Duplicate">⧉</button>
        <button class="btn ghost icon danger" data-act="del-slice" title="Delete">🗑</button></div>
    </div><div class="insp-body scroll">${body}</div>`;
}
function chipsInput(key, arr, ph) {
  return `<div class="chips" data-chips="${key}">${arr.map((a, i) => `<span class="chip">${esc(a)}<span class="x" data-act="chip-del" data-i="${i}">✕</span></span>`).join('')}</div>
    <div class="row" style="margin-top:6px"><input class="input" data-chip-in="${key}" placeholder="${esc(ph)}"><button class="btn sm" data-act="chip-add" data-k="${key}">Add</button><button class="btn sm" data-act="chip-win" data-k="${key}">🪟</button></div>`;
}

// ======================================================== page: contacts
function renderContacts() {
  const h = S.cfg.history || {};
  $('#cgrid').innerHTML = S.cfg.contacts.map(c => `<div class="card contact" style="--c:${c.color || '#38bdf8'}" data-act="ct-edit" data-id="${c.id}">
      <div class="row"><div class="avatar">${esc(c.emoji || '💬')}</div><div style="min-width:0;flex:1"><div class="nm">${esc(c.name)}</div><div class="url">${esc(c.url)}</div></div></div>
      <div class="meta"><span>🕘 ${ago(h[c.id])}</span><span class="row" style="gap:2px"><button class="btn ghost icon" data-act="ct-open" data-id="${c.id}" title="Open in Telegram Web">↗</button><button class="btn ghost icon danger" data-act="ct-del" data-id="${c.id}" title="Delete">🗑</button></span></div>
    </div>`).join('') + `<div class="card contact add" data-act="ct-new"><div><span class="big">＋</span>Add chat</div></div>`;
}
const CT_EMOJI = ['💬', '❤️', '👩', '👨', '👥', '💼', '🏠', '⭐', '🔖', '📌', '🎓', '🛒', '🎮', '🤖', '📢', '🧑‍💻'];
function editContact(c) {
  const isNew = !c; c = c ? clone(c) : { id: uid('c'), name: '', url: '', emoji: '💬', color: '#38bdf8' };
  const m = modal(`<div class="modal-h"><h2>${isNew ? 'New chat' : 'Edit chat'}</h2></div><div class="modal-b" style="display:flex;flex-direction:column;gap:14px">
    <div class="row"><div class="avatar" id="ce-av" style="--c:${c.color}">${esc(c.emoji)}</div><div class="field" style="flex:1"><label>Name</label><input class="input" id="ce-name" value="${esc(c.name)}" placeholder="Mom, Work group, Saved messages…"></div></div>
    <div class="field"><label>Telegram chat</label><input class="input" id="ce-url" value="${esc(c.url)}" placeholder="@username  ·  t.me/username  ·  +989…  ·  or paste the Telegram Web URL">
      <span class="hint" id="ce-res"></span>
      <span class="hint">Private chat or group without a username? Open it in Telegram Web and copy the address bar (looks like <code>web.telegram.org/k/#123456789</code>).</span></div>
    <div class="field"><label>Icon</label><div class="row"><div class="chips" id="ce-em">${CT_EMOJI.map(x => `<span class="chip" data-em="${x}" style="font-family:var(--emoji);font-size:16px;padding:3px 8px">${x}</span>`).join('')}</div>
      <input class="input" id="ce-emt" style="width:70px;flex:none;text-align:center;font-family:var(--emoji)" value="${esc(c.emoji)}"></div></div>
    <div class="field"><label>Color</label><div class="swatches" id="ce-sw">${COLORS.map(x => `<span class="sw ${x === c.color ? 'on' : ''}" data-c="${x}" style="background:${x};color:${x}"></span>`).join('')}</div></div>
    <div class="row" style="justify-content:flex-end"><button class="btn ghost" data-x>Cancel</button><button class="btn" id="ce-test">↗ Open to test</button><button class="btn primary" id="ce-save">Save chat</button></div></div>`);
  const upd = () => { $('#ce-res', m).innerHTML = '→ <code>' + esc(tgUrl($('#ce-url', m).value, S.cfg.settings.telegram_version)) + '</code>'; };
  const setEmoji = v => { c.emoji = v; $('#ce-av', m).textContent = v; $('#ce-emt', m).value = v; };
  upd(); $('#ce-url', m).oninput = upd; $('#ce-emt', m).oninput = e => { c.emoji = e.target.value.trim() || '💬'; $('#ce-av', m).textContent = c.emoji; };
  setTimeout(() => $('#ce-name', m).focus(), 50);
  m.addEventListener('click', e => {
    const sw = e.target.closest('#ce-sw .sw'); if (sw) { c.color = sw.dataset.c; $$('#ce-sw .sw', m).forEach(x => x.classList.toggle('on', x === sw)); $('#ce-av', m).style.setProperty('--c', c.color); }
    const em = e.target.closest('[data-em]'); if (em) setEmoji(em.dataset.em);
    if (e.target.closest('[data-x]')) closeModal();
    if (e.target.closest('#ce-test')) call('openUrl', tgUrl($('#ce-url', m).value, S.cfg.settings.telegram_version));
    if (e.target.closest('#ce-save')) {
      c.name = $('#ce-name', m).value.trim() || 'Chat'; c.url = $('#ce-url', m).value.trim();
      mutate(() => { const i = S.cfg.contacts.findIndex(x => x.id === c.id); if (i >= 0) S.cfg.contacts[i] = c; else S.cfg.contacts.push(c); });
      closeModal(); toast(isNew ? '💬 Chat added' : '✓ Saved');
    }
  });
}

// =========================================================== page: flows
function renderFlows() {
  if (!S.cfg.flows.find(f => f.id === S.flowId)) S.flowId = S.cfg.flows[0]?.id || null;
  $('#flow-list').innerHTML = `<button class="btn primary" data-act="flow-new" style="margin-bottom:6px">＋ New flow</button>` +
    S.cfg.flows.map(f => `<div class="li ${f.id === S.flowId ? 'on' : ''}" data-act="flow-sel" data-id="${f.id}"><span class="e">${esc(f.icon || '🔗')}</span><div class="t"><b>${esc(f.name)}</b><span>${(f.steps || []).length} steps</span></div></div>`).join('');
  const f = S.cfg.flows.find(x => x.id === S.flowId), pane = $('#flow-pane');
  if (!f) { pane.innerHTML = `<div class="empty"><span class="big">🔗</span>Flows are reusable step lists. Chat slices use one — e.g. “Send file via Telegram Web”.</div>`; return; }
  const users = [];
  const walk = (items, pth) => items.forEach(it => { if (it.flow === f.id || (it.steps || []).some(s => s.flow === f.id)) users.push(pth + it.label); walk(it.children || [], pth + it.label + ' › '); });
  S.cfg.pies.forEach(p => walk(p.items, p.name + ' › '));
  pane.innerHTML = `<div class="idrow"><div class="bigicon" data-act="flow-icon" style="background:linear-gradient(145deg,#8b5cf655,#1b1829)">${esc(f.icon || '🔗')}</div>
      <input class="input name-input" data-flow-f="name" value="${esc(f.name)}"><button class="btn" data-act="flow-run">▶ Test</button><button class="btn ghost danger" data-act="flow-del">🗑</button></div>
    <div class="hint">${users.length ? 'Used by: ' + users.map(esc).join(', ') : 'Not used by any slice yet — add a “Run flow” step or a Chats slice.'}</div>
    ${stepsEditor('flow', () => (f.steps = f.steps || []))}
    <div class="info">In chat flows, <code>{contact_url}</code>, <code>{contact_name}</code> come from the picked chat. <code>{files}</code> comes from “Get files”.</div>`;
}

// ========================================================== page: record
function renderRecord() {
  const pane = $('#rec-pane');
  if (!S.recSteps) {
    pane.innerHTML = `<div class="rec-hero"><button class="rec-btn" data-act="rec-start" title="Start recording"></button>
      <h2>Show it once. PieGuy repeats it.</h2>
      <p>Press record, do the task normally, then press <span class="kbd">Pause</span> or click the red pill. You get clean, editable steps — not a blind replay of mouse coordinates.</p>
      ${S.meta.uia === false ? '<div class="warn">Element-accurate clicks need the <code>uiautomation</code> package — run <b>install.bat</b>. Without it, clicks fall back to position-in-window.</div>' : ''}
      ${!S.meta.elevated ? '<div class="info">🛡 Recording inside an app that runs as <b>administrator</b> (e.g. v2rayN with TUN)? Windows hides it from normal apps — turn on <b>Settings → Run as administrator</b>. The live box turns <b style="color:#f87171">red</b> when this happens.</div>' : ''}
      <div class="how">
        <div><span class="e">🎯</span><b>Clicks → elements</b><span>A live box shows what PieGuy sees under your mouse — buttons, menus, dropdown items — and remembers <i>which</i> one you clicked.</span></div>
        <div><span class="e">✍️</span><b>Typing → text</b><span>Your typos & backspaces are cleaned up; shortcuts become key steps.</span></div>
        <div><span class="e">🚀</span><b>Apps → launch</b><span>Opening an app via Start/taskbar becomes one “Launch app” step.</span></div>
        <div><span class="e">⏱️</span><b>Pauses → waits</b><span>Long pauses become Wait steps you can tweak.</span></div>
      </div></div>`;
    return;
  }
  const sel = selItem();
  pane.innerHTML = `<div class="pane"><div class="sec-title"><h3>⏺️ Recorded <span class="pill">${S.recSteps.length} steps</span></h3>
      <div class="row"><button class="btn" data-act="rec-test">▶ Test</button><button class="btn ghost" data-act="rec-discard">Discard</button></div></div>
    <div class="hint">Tweak anything below, then save it.</div>
    ${stepsEditor('rec', () => S.recSteps, { record: false })}
    <div class="row" style="flex-wrap:wrap"><button class="btn primary" data-act="rec-save-slice">🥧 Save as new slice</button>
      <button class="btn" data-act="rec-save-flow">🔗 Save as flow</button>
      ${sel && sel.kind === 'action' ? `<button class="btn" data-act="rec-append">＋ Append to “${esc(sel.label)}”</button>` : ''}
      <button class="btn ghost" data-act="rec-start">⏺ Record again</button></div></div>`;
}

// ======================================================== page: settings
const THEMES = [['#8b5cf6', '#22d3ee'], ['#f43f5e', '#fb923c'], ['#10b981', '#a3e635'], ['#3b82f6', '#a855f7'], ['#ec4899', '#8b5cf6'], ['#f59e0b', '#ef4444'], ['#06b6d4', '#3b82f6'], ['#e2e8f0', '#64748b']];
function slider(k, label, min, max, stepv = 1, fmt = v => v) {
  const v = S.cfg.settings[k];
  return `<div class="slider"><span>${label}</span><input type="range" min="${min}" max="${max}" step="${stepv}" value="${v}" data-set="${k}" data-num="1"><output>${fmt(v)}</output></div>`;
}
function renderSettings() {
  const st = S.cfg.settings;
  $('#sgrid').innerHTML = `
  <div class="card scard"><h3><span class="e">🚀</span> General</h3>
    <div class="srow"><div class="l"><b>Start with Windows</b><span>Runs quietly in the tray at login</span></div><label class="toggle"><input type="checkbox" data-act="startup" ${S.startup ? 'checked' : ''}><i></i></label></div>
    <div class="srow"><div class="l"><b>Run as administrator</b><span>Needed to see & control admin apps (v2rayN in TUN mode, installers…). Apps PieGuy opens still start normally.</span>
      <span style="display:block;margin-top:3px">Now: ${S.meta.elevated ? '<b style="color:var(--ok)">🛡 admin</b>' : '<b style="color:var(--muted)">normal</b>'}</span></div>
      <label class="toggle"><input type="checkbox" data-act="admin" ${st.run_as_admin ? 'checked' : ''}><i></i></label></div>
    <div class="field"><label>Never open the pie in</label>${chipsInput('excluded', st.excluded_apps || [], 'e.g. valorant.exe (games)')}</div>
  </div>
  <div class="card scard"><h3><span class="e">🎨</span> Look</h3>
    <div class="row" style="align-items:flex-start;gap:16px"><svg viewBox="-300 -300 600 600" style="width:170px;height:170px;flex:none" id="mini"></svg>
      <div style="flex:1;display:flex;flex-direction:column;gap:10px"><div class="themes">${THEMES.map(([a, b]) => `<span class="theme ${st.accent === a && st.accent2 === b ? 'on' : ''}" data-act="theme" data-a="${a}" data-b="${b}" style="background:linear-gradient(135deg,${a},${b})"></span>`).join('')}</div>
      <div class="row"><label class="hint">Custom</label><input type="color" data-set="accent" value="${st.accent}"><input type="color" data-set="accent2" value="${st.accent2}"></div>
      <label class="check"><span class="toggle"><input type="checkbox" data-set="show_labels" ${st.show_labels !== false ? 'checked' : ''}><i></i></span>Show labels</label></div></div>
    ${slider('radius', 'Size', 120, 260)}${slider('inner', 'Center hub', 40, 100)}${slider('gap', 'Slice gap', 0, 10)}
    ${slider('font_size', 'Label size', 10, 16)}${slider('dim', 'Backdrop dim', 0, .7, .01, v => Math.round(v * 100) + '%')}${slider('anim_ms', 'Animation', 60, 400, 10, v => v + 'ms')}
  </div>
  <div class="card scard"><h3><span class="e">🖐️</span> Feel</h3>
    ${slider('submenu_dwell_ms', 'Submenu hover', 150, 1200, 10, v => v + 'ms')}
    ${slider('tap_ms', 'Tap threshold', 100, 600, 10, v => v + 'ms')}
    ${slider('deadzone', 'Center deadzone', 8, 70, 1, v => v + 'px')}
    <div class="hint">Hold shorter than the tap threshold = a tap (menu stays open / key passes through). Moving past a submenu slice's edge opens it instantly.</div>
  </div>
  <div class="card scard"><h3><span class="e">🌐</span> Apps & network</h3>
    <div class="srow"><div class="l"><b>Telegram Web version</b><span>For chats given as @username</span></div><div class="seg">
      <button class="${st.telegram_version !== 'a' ? 'on' : ''}" data-act="tgver" data-v="k">K</button><button class="${st.telegram_version === 'a' ? 'on' : ''}" data-act="tgver" data-v="a">A</button></div></div>
    <div class="field"><label>Firefox</label><div class="row"><input class="input" data-set="firefox_path" value="${esc(st.firefox_path || '')}" placeholder="auto-detect"><button class="btn sm" data-act="detect" data-k="firefox_path">Detect</button><button class="btn sm" data-act="set-browse" data-k="firefox_path">…</button></div></div>
    <div class="field"><label>v2rayN.exe</label><div class="row"><input class="input" data-set="v2rayn_path" value="${esc(st.v2rayn_path || '')}" placeholder="auto-detect (start v2rayN once, then Detect)"><button class="btn sm" data-act="detect" data-k="v2rayn_path">Detect</button><button class="btn sm" data-act="set-browse" data-k="v2rayn_path">…</button></div></div>
    <div class="hint" id="proxy-state">System proxy: …</div>
  </div>
  <div class="card scard"><h3><span class="e">💾</span> Data</h3>
    <div class="row" style="flex-wrap:wrap"><button class="btn" data-act="export">⬆ Export</button><button class="btn" data-act="import">⬇ Import</button><button class="btn" data-act="folder">📁 Open data folder</button><button class="btn danger" data-act="reset">↺ Reset to defaults</button></div>
    <div class="hint">Config: <code>${esc(S.meta.config_path || 'data/config.json')}</code> · changes save instantly · Ctrl+Z undo</div>
  </div>
  <div class="card scard"><h3><span class="e">🥧</span> About</h3>
    <div class="hint">PieGuy ${esc(S.meta.version || '')} · element recording: ${S.meta.uia ? '<b style="color:var(--ok)">ready</b>' : '<b style="color:var(--warn)">install uiautomation</b>'}</div>
    <div class="hint">Pie: hold trigger → flick → release · tap trigger = sticky menu · <code>1-9</code> pick · <code>Backspace</code>/right-click back · <code>Esc</code> close · <code>Pause</code> stops recording.</div>
  </div>`;
  $('#mini').innerHTML = pieSVG((pie().items || []).slice(0, 12), { R: 175 + (st.radius - 170) * .4, r0: st.inner * (175 / st.radius) + 10, gap: st.gap, labels: false, title: '', uidp: 'm' });
  call('proxyState').then(js => { if (!js) return; const s = JSON.parse(js); const el = $('#proxy-state'); if (el) el.innerHTML = `System proxy right now: <b style="color:${s.on ? 'var(--ok)' : 'var(--muted)'}">${s.on ? 'ON · ' + esc(s.server) : 'OFF'}</b>`; });
}

// ================================================================ router
const PAGES = { pie: ['Pie', 'Build your radial menu — click a slice to edit it'], contacts: ['Chats', 'Telegram chats your “Send to…” slice offers'], flows: ['Flows', 'Reusable step sequences'], record: ['Record', 'Do it once — get editable steps'], settings: ['Settings', 'Look, feel, startup & data'] };
function go(page) {
  S.page = page;
  $$('.nav').forEach(n => n.classList.toggle('on', n.dataset.page === page));
  $$('.page').forEach(p => p.classList.toggle('on', p.id === 'page-' + page));
  $('#page-title').textContent = PAGES[page][0]; $('#page-sub').textContent = PAGES[page][1];
  renderPage();
}
function renderPage() {
  document.documentElement.style.setProperty('--accent', S.cfg.settings.accent || '#8b5cf6');
  document.documentElement.style.setProperty('--accent2', S.cfg.settings.accent2 || '#22d3ee');
  const t = pie().trigger; $('#status-txt').textContent = t && (t.vk || t.button) ? 'Hold ' + (t.name || '') : 'No trigger';
  ({ pie: renderPiePage, contacts: renderContacts, flows: renderFlows, record: renderRecord, settings: renderSettings })[S.page]();
}

// ================================================================ events
document.addEventListener('click', async e => {
  const a = e.target.closest('[data-act]'); if (!a) return;
  if (a.closest('[data-stop]') && a.dataset.act !== 'en') return;
  const act = a.dataset.act, it = selItem(), p = pie();
  switch (act) {
    // nav / pie tabs
    case 'go': go(a.dataset.page); break;
    case 'pie-tab': S.pieId = a.dataset.id; S.path = []; S.sel = -1; renderPage(); break;
    case 'pie-new': { const np = newPie('Pie ' + (S.cfg.pies.length + 1)); mutate(() => S.cfg.pies.push(np)); S.pieId = np.id; S.path = []; S.sel = -1; renderPage(); captureTrigger(); break; }
    case 'pie-del': if (confirmDel('this pie')) mutate(() => { S.cfg.pies = S.cfg.pies.filter(x => x.id !== p.id); S.pieId = null; S.path = []; S.sel = -1; fixSel(); }); break;
    case 'crumb': S.path = S.path.slice(0, +a.dataset.d); S.sel = -1; renderPage(); break;
    case 'capture': captureTrigger(); break;
    case 'preview': call('previewPie', p.id); break;
    case 'add-slice': {
      const n = levelItems().length; if (n >= 12) { toast('12 slices max per ring — use a submenu 📂'); break; }
      mutate(() => levelItems().push({ id: uid(), label: 'New slice', icon: '✨', color: COLORS[n % COLORS.length], kind: 'action', steps: [] })); S.sel = n; renderPage();
      setTimeout(() => { const i = $('.name-input'); i && (i.focus(), i.select()); }, 30); break;
    }
    // item
    case 'icon': openEmoji(it.icon, v => mutate(() => { it.icon = v; })); break;
    case 'color': mutate(() => { it.color = a.dataset.c; }); break;
    case 'kind': mutate(() => {
      it.kind = a.dataset.v;
      if (it.kind === 'submenu') it.children = it.children || [];
      if (it.kind === 'contacts' && !it.flow && !(it.steps || []).length) it.flow = S.cfg.flows[0]?.id || '';
    }); break;
    case 'move': { const items = levelItems(), j = (S.sel + +a.dataset.d + items.length) % items.length; mutate(() => { [items[S.sel], items[j]] = [items[j], items[S.sel]]; }); S.sel = j; renderPage(); break; }
    case 'dup-slice': { const items = levelItems(); if (items.length >= 12) { toast('Ring is full (12)'); break; } const c = reId(clone(it)); c.label += ' copy'; mutate(() => items.splice(S.sel + 1, 0, c)); S.sel++; renderPage(); break; }
    case 'del-slice': mutate(() => { levelItems().splice(S.sel, 1); S.sel = -1; }); toast('🗑 Deleted — Ctrl+Z to undo'); break;
    case 'dive': if (it?.kind === 'submenu') { S.path.push(S.sel); S.sel = -1; renderPage(); } break;
    case 'run-item': call('runItem', JSON.stringify(it)); break;
    case 'ct-mode': mutate(() => { if (a.dataset.v === 'flow') { it.steps = []; it.flow = it.flow || S.cfg.flows[0]?.id || ''; } else { it.steps = it.steps?.length ? it.steps : [newStep('telegram_web')]; } }); break;
    case 'edit-flow': S.flowId = a.dataset.id; go('flows'); break;
    // chips
    case 'chip-add': case 'chip-win': case 'chip-del': chipAction(a, act); break;
    // steps
    case 'toggle': { if (e.target.closest('.sact')) break; const s = stepOf(a); S.open[s.id] = !S.open[s.id]; renderPage(); break; }
    case 'en': { const s = stepOf(a); mutate(() => { s.enabled = a.checked; }, { render: false }); refreshCard(a); break; }
    case 'test': { const s = stepOf(a); call('runSteps', JSON.stringify([s])); break; }
    case 'more': { const s = stepOf(a), arr = edSteps(a), i = arr.indexOf(s);
      popMenu(a, [{ icon: '⧉', label: 'Duplicate', fn: () => mutate(() => arr.splice(i + 1, 0, reStep(clone(s)))) },
        { icon: '⬆', label: 'Move up', fn: () => i > 0 && mutate(() => arr.splice(i - 1, 0, arr.splice(i, 1)[0])) },
        { icon: '⬇', label: 'Move down', fn: () => i < arr.length - 1 && mutate(() => arr.splice(i + 1, 0, arr.splice(i, 1)[0])) },
        { icon: '▶', label: 'Run from here', fn: () => call('runSteps', JSON.stringify(arr.slice(i))) }, '-',
        { icon: '🗑', label: 'Delete step', fn: () => mutate(() => arr.splice(i, 1)) }]); break; }
    case 'add-step': { const ed = a.dataset.ed; openPalette(type => { const s = newStep(type); mutate(() => EDITORS[ed]().push(s)); S.open[s.id] = true; renderPage(); }); break; }
    case 'rec-into': S.recTarget = a.dataset.ed; call('startRecording'); break;
    case 'browse': { const s = stepOf(a), k = a.dataset.k; const pth = await call('browseFile', 'Choose', ''); if (pth) { mutate(() => { s[k] = pth; }); } break; }
    case 'keys-clear': { const s = stepOf(a); mutate(() => { s[a.dataset.k] = ''; }); break; }
    case 'keys-add': { const s = stepOf(a), k = a.dataset.k; mutate(() => { s[k] = (s[k] ? s[k] + ' ' : '') + a.dataset.v; }); break; }
    case 'pick': { S.pickStep = { ed: a.closest('[data-ed]').dataset.ed, sid: stepOf(a).id }; call('pickElement'); break; }
    case 'pickwin': { const s = stepOf(a); const ws = JSON.parse(await call('listWindows') || '[]');
      popMenu(a, ws.slice(0, 18).map(w => ({ icon: '🪟', label: `${w.process} — ${w.title.slice(0, 50)}`, fn: () => mutate(() => { s.process = w.process; s.title = w.title.split(/ [—–-] /)[0].slice(0, 40); }) }))); break; }
    case 'pickchat': { const s = stepOf(a);
      popMenu(a, [{ icon: '🎯', label: 'The chat picked in the pie ({contact_url})', fn: () => mutate(() => { s.chat = '{contact_url}'; }) }, '-',
        ...S.cfg.contacts.map(c => ({ icon: c.emoji, label: c.name, fn: () => mutate(() => { s.chat = c.url; }) }))]); break; }
    // contacts
    case 'ct-new': editContact(null); break;
    case 'ct-edit': if (!e.target.closest('button')) editContact(S.cfg.contacts.find(c => c.id === a.dataset.id)); break;
    case 'ct-open': call('openUrl', tgUrl(S.cfg.contacts.find(c => c.id === a.dataset.id).url, S.cfg.settings.telegram_version)); break;
    case 'ct-del': if (confirmDel('this chat')) mutate(() => { S.cfg.contacts = S.cfg.contacts.filter(c => c.id !== a.dataset.id); }); break;
    // flows
    case 'flow-new': { const f = { id: uid('flow'), name: 'New flow', icon: '🔗', steps: [] }; mutate(() => S.cfg.flows.push(f)); S.flowId = f.id; renderPage(); break; }
    case 'flow-sel': S.flowId = a.dataset.id; renderPage(); break;
    case 'flow-icon': { const f = S.cfg.flows.find(x => x.id === S.flowId); openEmoji(f.icon, v => mutate(() => { f.icon = v; })); break; }
    case 'flow-run': call('runSteps', JSON.stringify(S.cfg.flows.find(x => x.id === S.flowId).steps)); break;
    case 'flow-del': if (confirmDel('this flow')) mutate(() => { S.cfg.flows = S.cfg.flows.filter(x => x.id !== S.flowId); }); break;
    // record
    case 'rec-start': S.recTarget = null; call('startRecording'); break;
    case 'rec-discard': S.recSteps = null; renderPage(); break;
    case 'rec-test': call('runSteps', JSON.stringify(S.recSteps)); break;
    case 'rec-save-slice': {
      const items = pie().items; if (items.length >= 12) { toast('Main ring is full — add it to a submenu instead'); break; }
      const sl = { id: uid(), label: 'Recorded', icon: '⏺️', color: '#f87171', kind: 'action', steps: S.recSteps };
      mutate(() => items.push(sl)); S.recSteps = null; S.path = []; S.sel = items.length - 1; go('pie'); toast('🥧 Saved as a new slice'); break; }
    case 'rec-save-flow': { const f = { id: uid('flow'), name: 'Recorded flow', icon: '⏺️', steps: S.recSteps }; mutate(() => S.cfg.flows.push(f)); S.recSteps = null; S.flowId = f.id; go('flows'); break; }
    case 'rec-append': mutate(() => { it.steps.push(...S.recSteps); }); S.recSteps = null; go('pie'); break;
    // settings
    case 'admin': {
      const r = await call('setAdmin', a.checked); S.cfg.settings.run_as_admin = a.checked && r !== 'declined';
      if (r === 'relaunching') toast('🛡 Approve the Windows prompt — PieGuy restarts as admin', 4000);
      else if (r === 'declined') { a.checked = false; toast('Admin mode not enabled (prompt declined)'); }
      else if (r === 'restart') toast('Admin mode off — restart PieGuy (tray → Quit, then open it) to apply', 4500);
      break; }
    case 'startup': { const r = await call('setStartup', a.checked); S.startup = !!r; toast(S.startup ? '🚀 Starts with Windows' : 'Startup off'); break; }
    case 'theme': mutate(() => { S.cfg.settings.accent = a.dataset.a; S.cfg.settings.accent2 = a.dataset.b; }); break;
    case 'tgver': mutate(() => { S.cfg.settings.telegram_version = a.dataset.v; }); break;
    case 'detect': { const v = a.dataset.k === 'v2rayn_path' ? await call('detectV2rayN') : await call('detectFirefox');
      if (v) { mutate(() => { S.cfg.settings[a.dataset.k] = v; }); toast('✓ Found ' + base(v)); } else toast('Not found — is it running? Use … to browse'); break; }
    case 'set-browse': { const v = await call('browseFile', 'Choose program', 'Programs (*.exe)'); if (v) mutate(() => { S.cfg.settings[a.dataset.k] = v; }); break; }
    case 'export': { const r = await call('exportConfig'); if (r) toast('⬆ Exported'); break; }
    case 'import': { const r = await call('importConfig'); if (r) { snapshot(); S.cfg = JSON.parse(r); fixSel(); renderPage(); toast('⬇ Imported'); } break; }
    case 'reset': if (confirm('Reset everything to the defaults? (Ctrl+Z can undo)')) { snapshot(); S.cfg = JSON.parse(await call('resetConfig')); S.path = []; S.sel = -1; fixSel(); renderPage(); } break;
    case 'folder': call('openConfigFolder'); break;
  }
});
function confirmDel(what) { return confirm(`Delete ${what}? (Ctrl+Z can undo)`); }
function reStep(s) { s.id = uid('s'); return s; }
function reId(it) { it.id = uid(); (it.steps || []).forEach(reStep); (it.children || []).forEach(reId); return it; }

async function chipAction(a, act) {
  const key = a.dataset.k || a.closest('.field, .insp-body, .scard')?.querySelector('[data-chips]')?.dataset.chips;
  const arrRef = () => key === 'apps' ? (pie().apps = pie().apps || []) : (S.cfg.settings.excluded_apps = S.cfg.settings.excluded_apps || []);
  const norm = v => { v = v.trim().toLowerCase(); return v && !v.endsWith('.exe') ? v + '.exe' : v; };
  if (act === 'chip-add') { const inp = $(`[data-chip-in="${key}"]`); const v = norm(inp.value); if (v) mutate(() => arrRef().push(v)); }
  if (act === 'chip-del') { const k2 = a.closest('[data-chips]').dataset.chips; const arr = k2 === 'apps' ? pie().apps : S.cfg.settings.excluded_apps; mutate(() => arr.splice(+a.dataset.i, 1)); }
  if (act === 'chip-win') { const ws = JSON.parse(await call('listWindows') || '[]'); const seen = [...new Set(ws.map(w => w.process))];
    popMenu(a, seen.map(pn => ({ icon: '🪟', label: pn, fn: () => mutate(() => { if (!arrRef().includes(pn)) arrRef().push(pn); }) }))); }
}

// inputs (typing never re-renders the field you're in)
document.addEventListener('input', e => {
  const el = e.target;
  if (el.dataset.f) {
    const s = stepOf(el); if (!s) return;
    const v = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
    mutate(() => { s[el.dataset.f] = v; }, { key: s.id + el.dataset.f, render: false });
    refreshCard(el);
    if (el.tagName === 'SELECT' || el.type === 'checkbox') renderPage();  // may show/hide fields
    if (el.dataset.f === 'chat') { const h = el.closest('.field').querySelector('.hint'); if (h) h.textContent = v === '{contact_url}' ? 'Uses the chat you pick in the pie.' : tgUrl(v, S.cfg.settings.telegram_version); }
  } else if (el.dataset.itemF) {
    const it = selItem(); const k = el.dataset.itemF;
    mutate(() => { it[k] = el.type === 'number' ? +el.value : el.value; }, { key: it.id + k, render: false });
    $('#pie').innerHTML = pieSVG(levelItems(), { sel: S.sel, title: S.path.length ? '' : pie().name, sub: '', gap: S.cfg.settings.gap, labels: S.cfg.settings.show_labels !== false });
    if (k === 'color') renderInspector();
    if (k === 'flow') renderInspector();
  } else if (el.dataset.pieF) {
    const k = el.dataset.pieF, v = el.type === 'checkbox' ? el.checked : el.value;
    mutate(() => { pie()[k] = v; }, { key: pie().id + k, render: false });
    if (k === 'name' || k === 'enabled') { $('#pie-tabs').innerHTML; renderPiePage(); const n = $('[data-pie-f="name"]'); if (k === 'name' && n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }
  } else if (el.dataset.flowF) {
    const f = S.cfg.flows.find(x => x.id === S.flowId);
    mutate(() => { f[el.dataset.flowF] = el.value; }, { key: f.id + 'name', render: false });
    const li = $(`.li[data-id="${f.id}"] b`); if (li) li.textContent = el.value;
  } else if (el.dataset.set) {
    const k = el.dataset.set, v = el.type === 'checkbox' ? el.checked : el.dataset.num ? +el.value : el.value;
    mutate(() => { S.cfg.settings[k] = v; }, { key: 'set' + k, render: false });
    const out = el.parentElement.querySelector('output'); if (out) out.textContent = el.dataset.num ? (k === 'dim' ? Math.round(v * 100) + '%' : v + (k.endsWith('_ms') ? 'ms' : k === 'deadzone' ? 'px' : '')) : '';
    const st = S.cfg.settings;
    const mini = $('#mini'); if (mini) mini.innerHTML = pieSVG((pie().items || []).slice(0, 12), { R: 175 + (st.radius - 170) * .4, r0: st.inner * (175 / st.radius) + 10, gap: st.gap, labels: false, title: '', uidp: 'm' });
    if (k.startsWith('accent')) { document.documentElement.style.setProperty('--' + k, v); }
  }
});
document.addEventListener('change', e => { if (e.target.dataset.pieF === 'tap') toast('✓ Tap behavior updated'); });
document.addEventListener('keydown', e => {
  const el = e.target;
  if (el.classList?.contains('keys-input')) {
    e.preventDefault();
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
    const tok = codeToken(e.code); if (!tok) return;
    const combo = [e.ctrlKey && 'ctrl', e.shiftKey && 'shift', e.altKey && 'alt', e.metaKey && 'win', tok].filter(Boolean).join('+');
    const s = stepOf(el), k = el.dataset.keys;
    mutate(() => { s[k] = el._fresh ? combo : (s[k] ? s[k] + ' ' + combo : combo); }, { key: s.id + k, render: false });
    el._fresh = false; el.value = s[k]; refreshCard(el); return;
  }
  if (e.key === 'Escape' && $('.modal-bg')) { e.preventDefault(); if ($('.capture')) call('cancelCapture'); closeModal(); return; }
  if (e.target.matches('input, textarea, select')) {
    if (e.key === 'Enter' && e.target.dataset.chipIn) chipAction({ dataset: { k: e.target.dataset.chipIn } }, 'chip-add');
    return;
  }
  if ((e.ctrlKey && e.key.toLowerCase() === 'z' && !e.shiftKey)) { e.preventDefault(); undo(); }
  else if (e.ctrlKey && (e.key.toLowerCase() === 'y' || (e.shiftKey && e.key.toLowerCase() === 'z'))) { e.preventDefault(); redo(); }
  else if (e.key === 'Escape') { closeModal(); }
  else if (e.key === 'Delete' && S.page === 'pie' && selItem()) { mutate(() => { levelItems().splice(S.sel, 1); S.sel = -1; }); toast('🗑 Deleted — Ctrl+Z to undo'); }
});
document.addEventListener('focusin', e => { if (e.target.classList?.contains('keys-input')) { e.target.classList.add('listening'); e.target._fresh = true; } });
document.addEventListener('focusout', e => { if (e.target.classList?.contains('keys-input')) e.target.classList.remove('listening'); });
function codeToken(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^F\d{1,2}$/.test(code)) return code.toLowerCase();
  if (/^Numpad\d$/.test(code)) return 'num' + code.slice(6);
  return { Enter: 'enter', NumpadEnter: 'enter', Space: 'space', Escape: 'esc', Tab: 'tab', Backspace: 'backspace', Delete: 'delete', Insert: 'insert', Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
    ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down', PrintScreen: 'printscreen', Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']',
    Semicolon: ';', Quote: "'", Backquote: '`', Comma: ',', Period: '.', Slash: '/', Backslash: '\\', CapsLock: 'capslock', Pause: 'pause', ContextMenu: 'apps' }[code] || '';
}

// pie svg interactions
let lastSliceClick = { i: -1, t: 0 };
$('#pie').addEventListener('click', e => {
  const sl = e.target.closest('.sl'), hub = e.target.closest('[data-hub]');
  if (sl) {
    const i = +sl.dataset.i, now = Date.now(), dbl = lastSliceClick.i === i && now - lastSliceClick.t < 420;
    lastSliceClick = { i, t: now };
    const it = levelItems()[i];
    if (dbl && it.kind === 'submenu') { S.path.push(i); S.sel = -1; lastSliceClick = { i: -1, t: 0 }; }
    else if (dbl && it.kind === 'contacts') { go('contacts'); return; }
    else S.sel = i;
    renderPiePage();
  }
  else if (hub) { if (S.path.length) { S.sel = S.path.pop(); } else S.sel = -1; renderPiePage(); }
  else { S.sel = -1; renderPiePage(); }
});

// drag & drop steps
let dragSid = null;
document.addEventListener('mousedown', e => { const g = e.target.closest('.grip'); if (g) g.closest('.step').draggable = true; });
document.addEventListener('dragstart', e => { const st = e.target.closest?.('.step'); if (!st) return; dragSid = st.dataset.sid; st.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
document.addEventListener('dragover', e => {
  const st = e.target.closest?.('.step'); if (!st || !dragSid) return; e.preventDefault();
  $$('.drop-before, .drop-after').forEach(x => x.classList.remove('drop-before', 'drop-after'));
  const r = st.getBoundingClientRect(); st.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
});
document.addEventListener('drop', e => {
  const st = e.target.closest?.('.step'); if (!st || !dragSid) return; e.preventDefault();
  const arr = edSteps(st), from = arr.findIndex(s => s.id === dragSid); if (from < 0) return;
  let to = arr.findIndex(s => s.id === st.dataset.sid); if (st.classList.contains('drop-after')) to++;
  mutate(() => { const [m] = arr.splice(from, 1); arr.splice(to > from ? to - 1 : to, 0, m); });
});
document.addEventListener('dragend', () => { dragSid = null; $$('.step').forEach(s => { s.draggable = false; s.classList.remove('dragging', 'drop-before', 'drop-after'); }); });

$$('.nav').forEach(n => n.addEventListener('click', () => go(n.dataset.page)));
$('#undo').onclick = undo; $('#redo').onclick = redo;

// ====================================================== trigger capture
function captureTrigger() {
  const m = modal(`<div class="capture"><div class="modal-h" style="justify-content:center"><h2 style="flex:none">Press your trigger</h2></div>
    <div class="ring">🎹</div><div>Press the <b>key</b> (with modifiers if you like) or the <b>mouse button</b> — middle, back or forward — you want to hold.</div>
    <div class="hint" style="margin-top:8px">Good picks: <span class="kbd">CapsLock</span> <span class="kbd">\`</span> <span class="kbd">Right Alt</span> <span class="kbd">Mouse back</span> · Esc cancels</div>
    <button class="btn ghost" style="margin-top:16px" data-x>Cancel</button></div>`);
  call('captureTrigger');
  m.addEventListener('click', e => { if (e.target.closest('[data-x]')) { call('cancelCapture'); closeModal(); } });
  m.parentElement.addEventListener('closing', () => call('cancelCapture'));
}
function onTriggerCaptured(js) {
  closeModal();
  const t = JSON.parse(js); if (!t) return;
  const risky = t.kind === 'key' && !t.mods.length && ((t.vk >= 0x41 && t.vk <= 0x5A) || (t.vk >= 0x30 && t.vk <= 0x39) || [0x20, 0x0D, 0x09, 0x08].includes(t.vk));
  mutate(() => { pie().trigger = t; if (risky) pie().tap = 'passthrough'; });
  toast(risky ? `⚠ “${t.name}” is a typing key — taps will still type it` : `✓ Trigger: ${[...t.mods, t.name].join(' + ')}`, 3200);
}

// ================================================================= boot
connectBridge(async () => {
  const st = JSON.parse(await call('getState'));
  S.cfg = st.config; S.meta = st.meta || {}; S.startup = st.startup; fixSel();
  B.triggerCaptured.connect(onTriggerCaptured);
  B.notice.connect(m => toast(m, 4000));
  B.elementPicked.connect(js => {
    const d = JSON.parse(js); const ps = S.pickStep; S.pickStep = null; if (!d || !ps) return;
    const s = EDITORS[ps.ed]?.().find(x => x.id === ps.sid); if (!s) return;
    mutate(() => { s.target = d; s.summary = d.label; }); toast('🎯 ' + d.label);
  });
  B.recordingDone.connect(js => {
    const steps = JSON.parse(js);
    if (!steps.length) { toast('Nothing was recorded'); return; }
    if (S.recTarget && EDITORS[S.recTarget]) { const arr = EDITORS[S.recTarget](); mutate(() => arr.push(...steps)); toast(`⏺ Added ${steps.length} recorded steps`); S.recTarget = null; return; }
    S.recSteps = steps; go('record');
  });
  go('pie');
});
