// The app shell: screens, the chat with Hange, the open model's workspace with its four stages
// (Brief, Build, Review, Export), the command menu, shortcuts, toasts and the theme. applyState(snapshot)
// is the one entry point the engine calls; it fans the snapshot out to the studio and
// to every panel that shows agent state. loadData(data) fills everything else.
// Talking back to the engine goes through MG.app.handlers, which live.js sets (demo.js
// sets canned ones); a missing handler means that action is not available.
window.MG = window.MG || {};
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = s => MG.esc(s);

  MG.icon = (name, cls) => `<svg class="ic${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  // Small flat drawings of models for thumbnails (demo data has no renders).
  MG.thumb = function (kind, w) {
    const h = Math.round(w * .62);
    const k = {
      chair: '<path d="M18 100 Q100 126 182 100" fill="none" stroke="#8A5A35" stroke-width="6" stroke-linecap="round"/><path d="M62 110 L62 62 M138 110 L148 16 M56 62 L146 64 M62 62 L62 42 M58 42 L140 42 M141 50 L100 52" fill="none" stroke="#9C6A42" stroke-width="6" stroke-linecap="round"/><path d="M108 22 L112 62 M124 20 L127 63" stroke="#B5824E" stroke-width="4"/>',
      lamp: '<rect x="70" y="104" width="60" height="10" rx="3" fill="#3A3D42"/><path d="M100 104 L84 60 L120 26" fill="none" stroke="#5B6168" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M108 16 L146 26 L136 46 L104 36 Z" fill="#D9A33A"/><circle cx="84" cy="60" r="5" fill="#3A3D42"/>',
      mug: '<rect x="60" y="30" width="62" height="78" rx="10" fill="#E3DCCB" stroke="#B9AE98" stroke-width="2"/><path d="M122 48 Q150 48 150 68 Q150 90 122 90" fill="none" stroke="#B9AE98" stroke-width="9"/><rect x="60" y="30" width="62" height="12" rx="6" fill="#4F6B7C"/>',
      shelf: '<rect x="64" y="8" width="72" height="110" fill="#8A6A4C"/><rect x="70" y="14" width="60" height="98" fill="#5E4630"/>' + [38, 62, 86, 110].map(y => `<rect x="70" y="${y}" width="60" height="4" fill="#8A6A4C"/><rect x="74" y="${y - 18}" width="7" height="18" fill="#7C9CB5"/><rect x="83" y="${y - 15}" width="6" height="15" fill="#C98A5A"/><rect x="104" y="${y - 18}" width="8" height="18" fill="#6E9A6A"/>`).join(''),
      table: '<path d="M26 54 L174 54" stroke="#6B4A34" stroke-width="10" stroke-linecap="round"/><path d="M44 58 L52 112 M156 58 L148 112 M48 88 H152" stroke="#7A5A40" stroke-width="5" stroke-linecap="round"/>'
    }[kind] || '';
    return `<svg width="${w}" height="${h}" viewBox="0 0 200 124" role="img" aria-label="${kind}">${k}</svg>`;
  };
  // A thumbnail: the real render when there is one, the drawing for demo data, else an empty tile.
  MG.pic = function (item, w, fallbackKind) {
    const url = item && (item.thumb || item.url);
    if (url) return `<img class="pic" src="${esc(url)}" alt="" loading="lazy" draggable="false">`;
    const kind = (item && item.kind) || fallbackKind;
    if (kind && /^(chair|lamp|mug|shelf|table)$/.test(kind)) return MG.thumb(kind, w);
    return `<span class="nopic">${MG.icon('box')}</span>`;
  };

  // Plain text with light formatting: **bold**, `code`, bullet and numbered lists, line breaks.
  MG.md = function (text) {
    const inline = s => esc(s).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
    const out = [];
    let list = null;
    String(text || '').split(/\r?\n/).forEach(line => {
      const m = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/);
      if (m) {
        const tag = m[1] ? 'ol' : 'ul';
        if (!list || list.tag !== tag) { if (list) out.push(`</${list.tag}>`); list = { tag }; out.push(`<${tag}>`); }
        out.push(`<li>${inline(m[2])}</li>`);
        return;
      }
      if (list) { out.push(`</${list.tag}>`); list = null; }
      out.push(line.trim() ? `<p>${inline(line)}</p>` : '');
    });
    if (list) out.push(`</${list.tag}>`);
    return out.join('');
  };

  const app = MG.app = {
    data: { model: null, chat: [], models: [], parts: [], activity: [], settings: {}, limits: {} },
    source: 'Waiting for data',
    commands: [],
    handlers: {},
    feed: [],
    prev: {},
    chatOpen: true,
    chatScope: 'model',   // the chat shows this model's messages, or 'all'
    screen: 'studio',
    tab: 'brief',         // the open stage: brief | build | review | export
    tabs: {},             // the stage each model was last left on
    auto: {},             // what autoTab saw last, to notice a build starting, a score, an accept
    pending: [],          // reference images attached to the next chat message
    newModel: { attachments: [], size: null, budget: null }
  };

  // ---------------------------------------------------------------------------------
  function initTheme() {
    let t = null;
    try { t = localStorage.getItem('mg-theme'); } catch (e) { /* storage blocked */ }
    if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    setTheme(t);
  }
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('mg-theme', t); } catch (e) { /* storage blocked */ }
    $$('[data-act="theme"]').forEach(b => { b.title = t === 'dark' ? 'Light mode' : 'Dark mode'; b.innerHTML = MG.icon(t === 'dark' ? 'sun' : 'moon') + `<span class="rl">${b.title}</span>`; });
    $$('input[name="theme"]').forEach(r => { r.checked = r.value === t; });
    if (app.viewer && app.viewer.refreshBackground) requestAnimationFrame(() => app.viewer.refreshBackground());
  }
  app.setTheme = setTheme;

  // ---- screens --------------------------------------------------------------------
  function show(screen) {
    app.screen = screen;
    $$('.screen').forEach(s => s.classList.toggle('on', s.dataset.screen === screen));
    $$('.rail [data-screen]').forEach(b => { b.classList.toggle('on', b.dataset.screen === screen); b.setAttribute('aria-current', b.dataset.screen === screen ? 'page' : 'false'); });
    closeMenus();
    renderCrumb();
    if (screen === 'models') renderModels();
    if (screen === 'parts') renderParts();
    if (screen === 'activity') renderActivityScreen();
    if (screen === 'new') renderNew();
    if (screen === 'settings') renderSettings();
    if (screen === 'studio') requestAnimationFrame(relayout);
  }
  app.show = show;

  function selectedVersion() {
    const m = app.data.model;
    if (!m || !m.versions || !m.versions.length) return null;
    return m.versions.find(v => v.id === m.selected) || m.versions[0];
  }
  app.selectedVersion = selectedVersion;

  function renderCrumb() {
    const m = app.data.model;
    const names = { studio: null, models: 'Models', parts: 'Parts library', activity: 'Activity', settings: 'Settings', new: 'New model' };
    const el = $('#crumb');
    if (app.screen === 'studio' && m) {
      el.innerHTML = `<button class="crumb-link" data-go="models">Models</button><span class="mu">/</span><span class="b5 ell">${esc(m.name)}</span>`;
    } else el.innerHTML = `<span class="b5">${esc(names[app.screen] || 'Studio')}</span>`;
  }

  // ---- chat with Hange ------------------------------------------------------------
  // Messages in a row from the same side form a group: Hange's picture shows on the first one only.
  function msgHTML(m, i, list) {
    const cont = i > 0 && list[i - 1].from === m.from ? ' cont' : '';
    const time = m.time ? `<div class="ctime">${esc(m.time)}</div>` : '';
    const att = (m.attachments || []).length ? `<div class="atts">${m.attachments.map(u => `<button class="att" data-act="lightbox" data-src="${esc(u)}" aria-label="Open the attached image"><img src="${esc(u)}" alt=""></button>`).join('')}</div>` : '';
    if (m.from === 'you') return `<div class="cm me${cont}"><div class="ccol"><div class="bub" dir="auto">${att}${m.text && m.text !== '(see the attached image)' ? esc(m.text) : ''}</div>${time}</div></div>`;
    let card = '';
    if (m.version) {
      const v = m.version;
      const parts = String(v.meta || '').split(' · ');
      const score = /^\d+(\.\d+)? \/ 10$/.test(parts[0]) ? parts.shift().replace(' / 10', '') : null;
      card = `<button class="vcard lift" data-act="open-version" data-project="${esc(v.project || '')}" data-v="${esc(v.number != null ? 'v' + v.number : '')}" title="Open ${esc(v.title)}"><span class="vthumb">${MG.pic(v, 64, v.kind || 'chair')}</span><span class="grow"><span class="b5 block ell">${esc(v.title)}</span><span class="xs mu block">${esc(parts.join(' · '))}</span></span>${score ? `<span class="vscore" aria-label="score ${esc(score)} out of 10">${esc(score)}</span>` : ''}</button>`;
    }
    return `<div class="cm them${cont}"><div class="cav">${cont ? '' : MG.portrait('hange', 28)}</div><div class="ccol"><div class="bub md" dir="auto">${MG.md(m.text)}</div>${card}${time}</div></div>`;
  }
  const TYPING_MAX_MS = 5 * 60 * 1000;   // stop the dots if no reply ever comes
  // The messages the chat shows: the open model's (and one just sent, not stored yet), or all of them.
  function chatList() {
    const all = app.data.chat || [], m = app.data.model;
    if (app.chatScope === 'all' || !m || !m.project) return all;
    return all.filter(x => x.project === m.project || (!x.project && (x.pending || !x.id)));
  }
  // With every model's messages showing, a small tag says where the talk moves to another model.
  function modelTag(m, i, list) {
    if (app.chatScope !== 'all' || !m.project) return '';
    let prev = null;
    for (let k = i - 1; k >= 0 && !prev; k--) prev = list[k].project;
    if (prev === m.project) return '';
    const known = (app.data.models || []).find(x => x.project === m.project);
    return `<div class="ctag-row"><button class="ctag" data-go="studio" data-project="${esc(m.project)}" title="Open this model">${esc(known ? known.name : m.project)}</button></div>`;
  }
  // The run's progress card goes under Hange's first reply to the message that started it
  // (the last message of yours before the run began); with no reply yet, at the end.
  function chatRunIndex(list) {
    const run = currentRun(), m = app.data.model;
    if (!run || !run.started) return -1;
    if (app.chatScope !== 'all' && m && m.project && run.project && run.project !== m.project) return -1;
    let mine = -1;
    list.forEach((m, i) => { if (m.from === 'you' && m.at && m.at <= run.started) mine = i; });
    if (mine < 0) return -1;
    if (Date.parse(run.started) - Date.parse(list[mine].at) > 10 * 60 * 1000) return list.length - 1;  // not started from the chat
    const reply = list.findIndex((m, i) => i > mine && m.from !== 'you');
    return reply >= 0 ? reply : list.length - 1;
  }
  function renderChatRun() {
    let el = $('#chat-run');
    const list = chatList(), at = chatRunIndex(list);
    if ((el ? Number(el.dataset.at) : -1) !== at) { renderChat(); el = $('#chat-run'); }
    if (!el) return;
    const run = currentRun();
    const html = progressCard(run, 'chat');
    if (el.innerHTML !== html) el.innerHTML = html;
  }
  function renderChat(keepScroll) {
    const box = $('#messages'), list = chatList(), before = box.scrollTop;
    const last = list[list.length - 1];
    if (!last || last.from !== 'you') app.typingSince = null;
    const typing = app.typingSince && Date.now() - app.typingSince < TYPING_MAX_MS;
    const runAt = chatRunIndex(list);
    const runSlot = i => i === runAt ? `<div class="chat-run" id="chat-run" data-at="${i}">${progressCard(currentRun(), 'chat')}</div>` : '';
    const asks = app.chatLoaded ? asking().map(w => ({ w, at: askAt(list, w.data.since) })) : [];
    app.askKey = askKey(asks);
    const askSlot = i => asks.filter(a => a.at === i).map(a => `<div class="cm them ask-row"><div class="cav">${MG.portrait(a.w.castId, 28)}</div><div class="ccol"><div class="ask" id="ask-${esc(a.w.id)}" data-agent="${esc(a.w.id)}">${askCard(a.w)}</div></div></div>`).join('');
    box.innerHTML = (askSlot(-1) + list.map((m, i, l) => modelTag(m, i, l) + msgHTML(m, i, l) + runSlot(i) + askSlot(i)).join('') +
      (typing ? `<div class="cm them"><div class="cav">${MG.portrait('hange', 28)}</div><div class="ccol"><div class="bub typing" aria-label="Hange is typing"><span></span><span></span><span></span></div></div></div>` : '')) ||
      `<div class="empty">${list.length < (app.data.chat || []).length ? 'No messages about this model yet. All models shows the rest of the chat.' : 'Describe an object and Hange starts the team on it.'}</div>`;
    asks.forEach(a => { const el = document.getElementById('ask-' + a.w.id); if (el) el._html = askCard(a.w); });
    box.scrollTop = keepScroll ? before : box.scrollHeight;
    observeAsks();
    renderStrip();
  }
  app.say = function (m) {
    if (m.project === undefined && app.data.model && app.data.model.project && app.screen === 'studio') m.project = app.data.model.project;
    app.data.chat.push(m);
    if (m.from === 'you' && m.pending) {
      app.typingSince = Date.now();
      clearTimeout(app.typingTimer);
      app.typingTimer = setTimeout(renderChat, TYPING_MAX_MS + 50);
    }
    renderChat();
    return m;
  };
  app.renderChat = renderChat;
  // Put text in the composer and open the chat, for "ask for a change" and similar.
  app.compose = function (text) {
    show('studio');
    toggleChat(true);
    const ta = $('#composer');
    ta.value = text || '';
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  };

  // The changes the next version acts on: one plain-words line each (who and what), the full
  // instruction one click away. The critic's come first; the fixes the checks found are marked.
  // Which <details> are open is kept across re-renders (the snapshot redraws them every few seconds).
  const openDetails = new Set();
  document.addEventListener('toggle', ev => {
    const k = ev.target.dataset && ev.target.dataset.open;
    if (k) ev.target.open ? openDetails.add(k) : openDetails.delete(k);
  }, true);
  const isOpen = k => openDetails.has(k) ? ' open' : '';

  function changeList(items, scope) {
    const who = role => { const c = MG.cast[MG.roleCast[role]]; return c ? c.name : MG.cap(role); };
    return `<ol class="changes clist">${items.map((i, n) => { const k = `${scope}:${n}:${i.target}`; return `<li dir="auto"><details data-open="${esc(k)}"${isOpen(k)}><summary>${MG.icon('chevron-right', 'chev')}<span class="grow"><span class="b5">${esc(who(i.role))}</span> · ${esc(i.short || i.target)}${i.auto ? ' <span class="badge">found by the checks</span>' : ''}</span></summary><div class="xs mu change-full"><span class="b5">${esc(i.target)}</span>: ${esc(i.text)}</div></details></li>`; }).join('')}</ol>`;
  }

  function changesCard(items, scope) {
    const annie = items.filter(i => !i.auto).length, checks = items.length - annie;
    const label = (annie ? `Annie's ${annie} change${annie !== 1 ? 's' : ''}` : '')
      + (annie && checks ? ' + ' : '') + (checks ? `${checks} from the checks` : '');
    const k = scope + ':card';
    return `<details class="changes-card" data-open="${esc(k)}"${isOpen(k)}><summary class="b5">${MG.icon('chevron-right', 'chev')}<span>${esc(label)}</span></summary>${changeList(items, scope)}</details>`;
  }

  function nextSection(n) {
    if (!n) return '';
    const head = n.building ? `v${n.building} is changing` : 'Next version will change';
    const comments = n.comments ? `<div class="lbl">Your comments (they win where they disagree with Annie)</div><p class="xs" dir="auto">${esc(n.comments)}</p>` : '';
    const list = (n.items || []).length ? changeList(n.items, 'panel-v' + n.from)
      : `<div class="xs mu">${n.comments ? 'Nothing from Annie on top of your comments.' : 'Nothing: Annie asked for no changes on v' + n.from + '.'}</div>`;
    return `<section class="sec" id="next-changes"><div class="lbl">${esc(head)} <span class="mu">· starts from v${esc(n.from)}</span></div>${comments}${list}</section>`;
  }

  // ---- questions in the chat ------------------------------------------------------------------
  // A question waiting on the person is a card in the chat: after the messages of Hange's that
  // follow the moment it was asked (so after the "v4 is ready" report), before your next message.
  // The strip pinned at the top of the chat lists the ones scrolled out of view; pressing one
  // scrolls to its card. Cards update in place, so a redraw keeps the scroll and what is open.
  const asking = () => (app.studio ? app.studio.agents() : []).filter(w => w.state === 'waiting' && w.data.question);
  function askAt(list, since) {
    const t = Date.parse(since);
    let i = -1;
    if (isNaN(t)) i = list.length - 1;
    else list.forEach((m, k) => { if (m.at && Date.parse(m.at) <= t) i = k; });
    while (i + 1 < list.length && list[i + 1].from !== 'you') i++;
    return i;
  }
  const askKey = asks => asks.map(a => a.w.data.question.id + '@' + a.at).join('|');
  const askWho = w => w.role === 'director' ? 'Hange' : MG.displayName(app.studio, w);
  function askCard(w) {
    const q = w.data.question, hange = w.role === 'director';
    const head = (hange ? 'Needs your answer' : esc(askWho(w)) + ' needs your answer') + ' · ' + esc(MG.ago(w.data.since));
    const btns = (q.options || []).map(o => `<button class="btn${/^accept\b/i.test(o) ? ' primary' : ''}" data-act="answer" data-agent="${esc(w.id)}" data-choice="${esc(o)}">${esc(o)}</button>`).join('')
      + (hange ? '' : `<button class="btn" data-act="reply-pinned" data-agent="${esc(w.id)}">Reply</button>`);
    return `<div class="ask-k">${head}</div><div class="ask-q" dir="auto">${esc(q.text)}</div><div class="ask-btns">${btns}</div>`
      + ((q.items || []).length ? changesCard(q.items, 'q-' + q.id) : '');
  }

  // Which cards are on screen in the chat (a card counts once a good part of it shows).
  const seenAsks = new Set();
  let askObs = null;
  function observeAsks() {
    const box = $('#messages');
    if (!askObs && 'IntersectionObserver' in window) {
      askObs = new IntersectionObserver(entries => {
        entries.forEach(e => {
          const shown = e.isIntersecting && (e.intersectionRatio >= .5 || e.intersectionRect.height >= 90);
          shown ? seenAsks.add(e.target.dataset.agent) : seenAsks.delete(e.target.dataset.agent);
        });
        renderStrip();
      }, { root: box, threshold: [0, .1, .25, .5, .75, 1] });
    }
    if (!askObs) return;
    askObs.disconnect();
    $$('.ask', box).forEach(el => askObs.observe(el));
  }

  const firstSentence = text => (String(text || '').split(/(?<=[.?!])\s/)[0] || '').replace(/[.]$/, '');
  function renderStrip() {
    const el = $('#pinned');
    if (!el) return;
    const hidden = app.chatLoaded ? asking().filter(w => !seenAsks.has(w.id)) : [];
    const line = w => `<button class="ask-strip" data-act="goto-ask" data-agent="${esc(w.id)}" title="Go to the question in the chat"><span class="ask-dot" aria-hidden="true"></span><span class="ask-s-t" dir="auto"><span class="b5">${esc(askWho(w))}</span> · ${esc(firstSentence(w.data.question.text))}</span><span class="ask-s-ago">${esc(MG.ago(w.data.since))}</span>${MG.icon('chevron-down')}</button>`;
    const html = hidden.slice(0, 2).map(line).join('')
      + (hidden.length > 2 ? `<button class="ask-strip more" data-act="goto-ask" data-agent="${esc(hidden[2].id)}">+${hidden.length - 2} more</button>` : '');
    if (el._html !== html) { el.innerHTML = html; el._html = html; }
  }

  // Called on every snapshot and every few seconds: a new, answered or moved question redraws
  // the chat; otherwise only the cards that changed (their "waiting for" time) are redrawn.
  function renderAsks() {
    const box = $('#messages');
    if (!box || !app.chatLoaded) return;
    const list = chatList();
    const asks = asking().map(w => ({ w, at: askAt(list, w.data.since) }));
    const key = askKey(asks);
    if (key !== (app.askKey || '')) {
      const had = (app.askKey || '').split('|').map(k => k.split('@')[0]);
      renderChat(asks.every(a => had.includes(a.w.data.question.id)));  // a new question shows: go to the end
      return;
    }
    asks.forEach(a => {
      const el = document.getElementById('ask-' + a.w.id);
      const html = askCard(a.w);
      if (el && el._html !== html) { el.innerHTML = html; el._html = html; }
    });
    renderStrip();
  }

  function gotoAsk(id) {
    show('studio');
    toggleChat(true);
    const asker = app.studio && app.studio.find && app.studio.find(id);
    if (asker && asker.data.question && app.data.model) setTab(askTab(asker));
    const box = $('#messages'), el = document.getElementById('ask-' + id);
    if (!box || !el) return;
    const er = el.getBoundingClientRect(), br = box.getBoundingClientRect();
    const tall = er.height > box.clientHeight * .8;
    const top = box.scrollTop + (er.top - br.top) - (tall ? 10 : (box.clientHeight - er.height) / 2);
    box.scrollTo({ top: Math.max(0, top), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    clearTimeout(el._flash);
    el._flash = setTimeout(() => el.classList.remove('flash'), 1300);
  }
  app.gotoAsk = gotoAsk;
  app.asks = { askAt, askCard, renderStrip, firstSentence };  // for the page-code test

  // Reference images waiting to go with the next message.
  function renderAttachChips() {
    const box = $('#attach-chips');
    if (!box) return;
    box.innerHTML = app.pending.map((a, i) => `<span class="achip"><img src="${esc(a.url)}" alt=""><button class="icon-btn sm" data-act="unattach" data-i="${i}" aria-label="Remove this image">${MG.icon('x')}</button></span>`).join('');
  }
  async function attachTo(list, files, after) {
    const up = app.handlers.upload;
    if (!up) { toast('Attaching images needs the engine running'); return; }
    for (const f of [...files].slice(0, 4)) {
      if (!/^image\//.test(f.type)) { toast(f.name + ' is not an image'); continue; }
      try { list.push(await up(f)); } catch (e) { toast('Could not attach ' + f.name + ': ' + e.message); }
    }
    after();
  }

  // ---- the chat column and the navigation ------------------------------------------------
  function toggleChat(open) {
    app.chatOpen = open == null ? !app.chatOpen : open;
    $('#work').classList.toggle('chat-closed', !app.chatOpen);
    $('#chat').setAttribute('aria-hidden', String(!app.chatOpen));
    savePref('chat', app.chatOpen);
  }
  app.toggleChat = toggleChat;
  function toggleNav(open) {
    app.navOpen = open == null ? !app.navOpen : open;
    $('#app').classList.toggle('nav-open', app.navOpen);
    const b = $('[data-act="nav"]'), verb = app.navOpen ? 'Collapse' : 'Expand';
    b.title = verb + ' navigation (Ctrl B)'; b.setAttribute('aria-label', verb + ' navigation'); b.setAttribute('aria-expanded', String(app.navOpen));
    savePref('nav', app.navOpen);
  }
  // Keep the studio clear of the chips over the floor, so Fit frames it in the space that is left.
  function relayout() {
    if (!app.studio) return;
    app.studio.setInsets({ l: 12, r: 12, t: 52, b: 52 });
  }
  app.relayout = relayout;

  function sendChat() {
    const ta = $('#composer');
    const text = ta.value.trim();
    if (!text && !app.pending.length) return;
    const attachments = app.pending.slice();
    ta.value = '';
    app.pending = []; renderAttachChips();
    app.say({ from: 'you', text, attachments: attachments.map(a => a.url), pending: true });
    const h = app.handlers.chatDirector;
    if (h) h(text, { attachments: attachments.map(a => a.id) });
    else document.dispatchEvent(new CustomEvent('studio:chat', { detail: { agent: 'director', text } }));
  }

  // ---- toasts ---------------------------------------------------------------------
  function toast(text, action, onAction, agent) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = `${agent ? MG.portrait(agent, 24) : ''}<span dir="auto">${esc(text)}</span>${action ? `<button class="link">${esc(action)}</button>` : ''}`;
    if (action) t.querySelector('button').onclick = () => { onAction && onAction(); t.remove(); };
    $('#toasts').appendChild(t);
    requestAnimationFrame(() => t.classList.add('on'));
    setTimeout(() => { t.classList.remove('on'); setTimeout(() => t.remove(), 300); }, 5200);
  }
  app.toast = toast;

  // ---- the Build stage's run steps, and the one progress line in the top bar ---------------
  function renderRun() {
    const run = MG.state && MG.state.runs && MG.state.runs[0];
    const box = $('#run');
    if (!run) { setHtml(box, `<div class="sec-h">This build</div><div class="empty">No build is running. Describe a model to Hange and the team starts here.</div>`); setHtml($('#progress'), ''); return; }
    const m = app.data.model;
    const other = m && m.project && run.project && run.project !== m.project;
    const runMeta = `${run.started ? 'started ' + MG.clock(run.started) : ''}${run.cost != null ? ' · $' + Number(run.cost).toFixed(2) + (run.finished ? '' : ' so far') : ''}`;
    const p = runProgress(run);
    const failed = run.steps.some(s => s.state === 'failed');
    setHtml(box, `<div class="card-h"><span class="sec-h flat ell" title="${esc(run.title)}">${esc(run.title)}</span><span class="xs mu ell" title="${esc(runMeta)}">${esc(runMeta)}</span></div>
      ${other ? `<div class="xs mu other-run">This is the build of another model. <button class="link xs" data-act="open-run" data-project="${esc(run.project)}">Open it</button></div>` : ''}
      <span class="pbar${run.busy ? ' busy' : ''}${failed ? ' failed' : ''}"><span style="width:${run.busy || failed ? p.percent : 100}%"></span></span>
      <div class="xs mu run-line">${esc(run.busy ? [run.round, `step ${p.pos} of ${p.n}`, p.eta].filter(Boolean).join(' · ') : [run.round, p.outcome].filter(Boolean).join(' · '))}</div>
      <ol class="steps">${run.steps.map(s => {
        const names = (Array.isArray(s.agent) ? s.agent : [s.agent]).map(a => { const w = app.studio.find(a); return w ? w.castId : null; }).filter(Boolean);
        return `<li class="step st-${s.state || 'todo'}"><span class="mark">${s.state === 'done' ? MG.icon('check') : ''}</span><div class="grow"><div><span class="b5">${esc(s.label)}</span> <span class="mu">· ${esc(names.map(n => MG.cast[n].name).filter((v, i, l) => l.indexOf(v) === i).join(', '))}</span></div>${s.note ? `<div class="xs mu" dir="auto">${esc(s.note)}</div>` : ''}</div><span class="xs mu">${esc(s.at ? MG.clock(s.at) : s.state === 'active' ? 'now' : s.state === 'waiting' ? 'needs you' : s.state === 'failed' ? 'failed' : '')}</span></li>`;
      }).join('')}</ol>`);
    const lead = run.busy ? `<span class="sq s-working"></span><span class="b5 ell">${esc(run.model || run.title)}</span><span class="mu">· ${esc(run.current || 'working')}</span>`
      : `<span class="b5 ell">${esc(run.model || run.title)}</span><span class="mu">· ${esc(p.outcome)}</span>`;
    setHtml($('#progress'), `<button class="prog-btn row" data-act="open-run" data-project="${esc(run.project || '')}" title="Open the Build stage of ${esc(run.model || run.title)}">${lead}
      <span class="pline${run.busy ? ' busy' : ''}${failed ? ' failed' : ''}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.percent}" aria-label="Build progress"><span style="width:${run.busy || failed ? p.percent : 100}%"></span></span>
      <span class="xs mu">${esc(run.busy ? [`step ${p.pos} of ${p.n}`, p.eta].filter(Boolean).join(' · ') : p.line)}</span></button>`);
  }

  // How far a run is, in words: "step 2 of 9 · 18% · ~4 min left", and how it ended.
  function etaWords(s) {
    if (s == null) return '';
    if (s < 60) return 'under a minute left';
    const m = Math.round(s / 60);
    return m < 60 ? `~${m} min left` : `~${Math.floor(m / 60)} h ${m % 60} min left`;
  }
  function runProgress(run) {
    const n = run.steps.length, done = run.steps.filter(s => s.state === 'done').length;
    const failed = run.steps.find(s => s.state === 'failed');
    const pos = Math.min(done + (run.current ? 1 : 0), n) || 1;
    const percent = run.percent != null ? run.percent : Math.round(100 * done / Math.max(n, 1));
    const outcome = failed ? 'failed at ' + failed.label : run.finished ? 'done' : run.busy ? (run.current || 'working') : 'interrupted';
    const parts = run.busy ? [run.round, `step ${pos} of ${n}`, percent + '%', etaWords(run.eta_s)] : [run.round, `${done} of ${n} steps`];
    return { pos, n, done, percent, outcome, eta: run.busy ? etaWords(run.eta_s) : '', line: parts.filter(Boolean).join(' · ') };
  }
  function currentRun() { return MG.state && MG.state.runs && MG.state.runs[0] || null; }
  app.currentRun = currentRun;

  // The run's progress as a small card under Hange's message in the chat; it opens the Build stage.
  function progressCard(run) {
    const p = runProgress(run);
    const failed = run.steps.find(s => s.state === 'failed');
    const head = run.busy ? `<span class="live-dot" aria-hidden="true"></span><span class="b5 grow ell">Building ${esc(run.model || run.title)}</span><span class="xs mu">${p.percent}%</span>`
      : `<span class="b5 grow ell">${esc(run.model || run.title)} · ${esc(p.outcome)}</span>`;
    const review = run.steps.find(s => s.label === 'Review' && s.note);
    const sub = run.busy ? [run.current ? 'Now: ' + run.current : '', `step ${p.pos} of ${p.n}`, p.eta].filter(Boolean).join(' · ')
      : failed && failed.note ? failed.note : run.finished && review ? 'Scored ' + review.note : p.line;
    return `<button class="prog-card ${run.busy ? 'busy' : ''}${failed ? ' failed' : ''}" data-act="open-run" data-project="${esc(run.project || '')}" title="Open the Build stage"><span class="row">${head}</span>
      <span class="pbar${run.busy ? ' busy' : ''}${failed ? ' failed' : ''}"><span style="width:${run.busy || failed ? p.percent : 100}%"></span></span>
      <span class="xs mu block" dir="auto">${esc(sub)}</span></button>`;
  }

  function allEvents() {
    const ev = [];
    if (app.studio) app.studio.agents().forEach(w => (w.data.calls || []).forEach(c => ev.push({ at: c.at, cast: w.castId, tool: c.tool, text: c.text, ok: c.ok, live: w.state === 'working' && c === w.data.calls[w.data.calls.length - 1], need: c.tool === 'ask_human' && w.state === 'waiting' })));
    app.feed.forEach(f => ev.push(f));
    return ev.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  }
  function eventRow(e) {
    const tag = e.need ? '<span class="xs st st-waiting">needs you</span>' : e.ok === false ? '<span class="xs st st-failed">failed</span>' : e.live ? '<span class="xs st st-working">live</span>' : e.kind === 'handoff' ? MG.icon('arrow-right', 'mu') : MG.icon('check', 'mu');
    return `<div class="ev">${e.cast ? MG.portrait(e.cast, 22) : '<span class="pt-blank"></span>'}<span class="xs mu tm">${MG.clock(e.at)}</span>${e.tool ? `<code>${esc(e.tool)}</code>` : ''}<span class="grow ell" dir="auto" title="${esc(e.text || '')}">${esc(e.text || '')}</span>${tag}</div>`;
  }
  function renderActivity() { setHtml($('#activity'), `<div class="card-h"><span class="sec-h flat">Live activity</span><span class="xs mu">All agents</span></div><div class="evs">${allEvents().slice(0, 24).map(eventRow).join('') || '<div class="empty">Tool calls from every agent stream in here.</div>'}</div>`); }

  function renderStatus() {
    const ws = app.studio ? app.studio.agents() : [];
    const builders = ws.filter(w => w.role === 'builder' && w.state === 'working').length;
    const live = app.source === 'Live';
    $('#st-live').innerHTML = `<span class="sq ${live ? 's-done' : app.source === 'Demo data' ? 's-idle' : 's-waiting'}"></span>${esc(app.source)}`;
    const run = currentRun(), busy = !!(run && run.busy);
    const bWords = builders ? builders + (builders > 1 ? ' builders' : ' builder') + ' running' : '';
    $('#st-builders').innerHTML = busy ? `<span class="sq s-working"></span>Building ${esc(run.model || run.title)}${run.current ? ' · ' + esc(run.current) : ''}${bWords ? ' · ' + bWords : ''}`
      : esc(bWords || 'No builders running');
    $('#st-chip').innerHTML = `<span class="sq ${live ? 's-done' : 's-working'}"></span>${live ? 'live' : app.source === 'Demo data' ? 'demo' : esc(app.source.toLowerCase())}${busy ? ' · building ' + esc(run.model || run.title) : bWords ? ' · ' + bWords : ''}`;
    const waiting = ws.filter(w => w.state === 'waiting').length;
    $('#st-waiting').textContent = waiting ? waiting + ' waiting on you' : 'Nobody waiting on you';
    const h = app.studio && app.studio.byRole('director');
    const st = h ? h.state : 'idle';
    $('#chat-who').innerHTML = `${MG.portrait('hange', 38)}<div><div class="h">Hange</div><div class="xs mu st st-${st === 'idle' && busy ? 'working' : st}"><span class="sq"></span>Director · ${st === 'working' && h.data.doing ? esc(h.data.doing) : st === 'idle' && busy ? 'team is building ' + esc(run.model || run.title) : st === 'idle' ? 'ready when you are' : MG.stateWord(st).toLowerCase()}</div></div>`;
    const asks = asking();
    setHtml($('#needs'), asks.length ? `<button class="needs-chip" data-act="goto-ask" data-agent="${esc(asks[0].id)}" title="Go to the question"><span class="ask-dot" aria-hidden="true"></span>Needs you${asks.length > 1 ? ' · ' + asks.length : ''}</button>` : '');
    const team = $('#team-free');
    if (team) team.textContent = `${ws.length} agents · ${ws.filter(w => w.state === 'idle').length} free`;
  }

  // Announce changes that matter to the person, once.
  function transitions() {
    if (!app.studio) return;
    app.studio.agents().forEach(w => {
      const before = app.prev[w.id];
      if (before && before !== w.state) {
        const name = MG.displayName(app.studio, w);
        if (w.state === 'waiting') toast(name + ' needs you', 'Answer', () => w.role === 'director' ? gotoAsk(w.id) : openAgent(w.id), w.castId);
        else if (w.state === 'failed') toast(name + ' failed: ' + (w.data.doing || 'see details'), 'Details', () => openAgent(w.id), w.castId);
        else if (w.state === 'done' && w.role === 'critic') toast(name + ' finished the review', 'View review', () => { show('studio'); setTab('review'); }, w.castId);
      }
      app.prev[w.id] = w.state;
    });
  }

  function openAgent(id) {
    const w = app.studio.find(id);
    if (!w) return;
    if (w.role === 'director') { toggleChat(true); return; }
    show('studio');
    setTab('build');
    app.studio.select(w.id);
    app.panel.open(w.id);
  }
  app.openAgent = openAgent;

  // ---- the workspace: one model, four stages (Brief, Build, Review, Export) ---------------
  // The viewer element is made once and kept, so the 3D view survives every re-render. It sits
  // in the Review stage, moves to the Build stage's live preview while a version builds, to the
  // Export stage for colouring, over the whole workspace for the large view (V), or full screen.
  const VIEW_HINT = 'Drag to orbit · scroll to zoom · click a part to select or colour it';
  const TABS = ['brief', 'build', 'review', 'export'];
  const TAB_NAMES = { brief: 'Brief', build: 'Build', review: 'Review', export: 'Export' };
  const PATHS = {
    library: ['Ready-made', 'Made from a free ready-made model, with no build team'],
    remix: ['Car generator', 'Made by the car generator from a free base car'],
    shell: ['Car body', 'The team shapes the car as one body from a sheet'],
    character: ['Character', 'Built as a rigged, animated person from a character sheet, with no build team'],
    team: ['Team build', 'The team builds it part by part']
  };
  const STUCK = ['failed', 'stopped', 'interrupted'];
  // Set an element's HTML only when it changed, so scroll, focus and open menus survive redraws.
  function setHtml(el, html) {
    if (!el || el._html === html) return;
    el.innerHTML = html; el._html = html;
  }
  const modelKey = m => (m ? m.project || m.name : null);
  const buildingVersion = m => ((m && m.versions) || []).find(v => v.state === 'building') || null;
  const acceptedId = m => (m ? m.accepted || ((m.versions || []).find(v => v.accepted) || {}).id || null : null);
  // The open model's run, when the engine's current run is this model's.
  function runOf(m) {
    const run = currentRun();
    return run && m && (!run.project || !m.project || run.project === m.project) ? run : null;
  }
  // The version the 3D view shows: the one being built in the Build stage, else the selected one.
  function shownVersion() {
    const m = app.data.model;
    if (!m) return null;
    return (app.tab === 'build' && buildingVersion(m)) || selectedVersion();
  }
  // Which stage a waiting question belongs to: "accept this version?" is a review; any other is the build's.
  const askTab = w => (w.role === 'director' && w.data.question && w.data.question.kind === 'accept' ? 'review' : 'build');
  function asksOf(m) {
    const p = MG.state && MG.state.project;
    return asking().filter(() => !p || !m.project || p === m.project);
  }

  function viewerSkeleton() {
    if ($('#viewer')) return;
    const tb = (act, icon, title, extra) => `<button class="icon-btn" data-act="${act}" title="${title}" aria-label="${title}"${extra || ''}>${MG.icon(icon)}</button>`;
    const pop = (kind, icon, title) => tb('v-pop', icon, title, ` data-vpop="${kind}" aria-haspopup="true" aria-expanded="false"`);
    const toggle = (act, icon, title) => tb(act, icon, title, ' aria-pressed="false"');
    $('#viewer-slot').innerHTML = `<div class="viewer" id="viewer"><div class="viewer-mount" id="viewer-mount"></div><div class="viewer-still" id="viewer-still"></div>
          <div class="float row viewer-tools" role="toolbar" aria-label="3D view">
            ${pop('views', 'cube', 'Views (1 to 6)')}${pop('display', 'shading', 'Display mode')}${pop('scene', 'sliders', 'Lighting, background, grid')}
            <span class="vsep"></span>
            ${toggle('v-dims', 'ruler', 'Dimensions')}${toggle('v-measure', 'measure', 'Measure')}${pop('section', 'slice', 'Section cut')}${pop('info', 'info', 'Model stats')}${toggle('v-parts', 'list', 'Parts')}
            <span class="vsep"></span>
            ${toggle('v-orbit', 'rotate', 'Spin')}<span class="motion-only">${toggle('v-play', 'pause', 'Pause the motion')}</span>${tb('v-shot', 'camera', 'Save a screenshot')}
            <span class="vsep"></span>
            <span class="live-only">${toggle('v-live', 'eye', 'Live: watch each build step in 3D')}<span class="vsep"></span></span>
            ${tb('v-big', 'expand', 'Large view (V)', ' id="v-big-btn"')}${tb('v-full', 'fullscreen', 'Full screen', ' id="v-full-btn"')}
          </div>
          <div class="vpop" id="vpop" role="dialog" aria-label="View options"></div>
          <aside class="viewer-drawer" id="viewer-drawer" aria-label="Model and parts"></aside>
          <div class="viewer-foot"><div id="viewer-live"></div><div id="viewer-sel"></div><div id="viewer-readout"></div><span class="xs mu viewer-hint" id="viewer-hint">${VIEW_HINT}</span></div>
        </div>`;
    bindViewerUi();
  }
  function viewerHost() {
    if (app.tab === 'build' && buildingVersion(app.data.model)) return $('#live-slot');
    if (app.tab === 'export') return $('#export-slot');
    return $('#viewer-slot');
  }
  function placeViewer() {
    const v = $('#viewer');
    if (!v || vs.big || isFull()) return;
    const host = viewerHost();
    if (v.parentElement !== host) { closePop(); host.appendChild(v); }
    v.classList.toggle('mini', host.id === 'live-slot');
  }

  function reviewOf(m, sel) {
    if (sel && sel.review) return sel.review;
    if (m.review && (!sel || m.review.version === sel.id)) return m.review;   // demo data
    return null;
  }

  // ---- stages ---------------------------------------------------------------------------
  function setTab(tab) {
    if (!TABS.includes(tab)) return;
    app.tab = tab;
    const m = app.data.model, key = modelKey(m);
    if (key) { app.tabs[key] = tab; savePref('tabs', app.tabs); }
    $$('.pane').forEach(p => p.classList.toggle('on', p.dataset.pane === tab));
    if (tab !== 'build' && app.panel && app.panel.isOpen()) app.studio.select(null);
    renderTabs(); renderBuildSide(); placeViewer(); updateComposer();
    if (m) syncViewer(shownVersion(), m);
    if (tab === 'build') requestAnimationFrame(relayout);
  }
  app.setTab = setTab;

  // The stage follows the model by itself: a build that starts opens Build, a version that gets
  // its score opens Review, accepting opens Export. A stage the person picks stays until the next
  // of those. Opening another model goes to the stage it was last left on.
  function autoTab() {
    const m = app.data.model, a = app.auto, key = modelKey(m);
    const run = runOf(m), busy = !!(run && run.busy);
    const scored = m ? m.versions.filter(v => v.state === 'scored').map(v => v.id) : [];
    const accepted = acceptedId(m);
    const was = { key: a.key, busy: a.busy, scored: a.scored || [], accepted: a.accepted };
    Object.assign(a, { key, busy, scored, accepted });
    if (key !== was.key) {
      const first = !m ? 'brief' : busy ? 'build' : app.tabs[key] || (m.versions.length ? 'review' : STUCK.includes(m.status) ? 'build' : 'brief');
      setTab(first);
      return;
    }
    if (!m) return;
    if (accepted && accepted !== was.accepted) setTab('export');
    else if (scored.some(id => !was.scored.includes(id))) setTab('review');
    else if (busy && !was.busy) setTab('build');
  }

  function tabState(tab, m) {
    const run = runOf(m), busy = !!(run && run.busy), asks = asksOf(m);
    if (tab === 'brief') return busy && /^(Ready-made|Research|Spec)/.test(run.current || '') ? 'working' : (m.brief && m.brief.spec) || m.versions.length ? 'done' : '';
    if (tab === 'build') return asks.some(w => askTab(w) === 'build') ? 'waiting' : busy ? 'working' : !m.running && STUCK.includes(m.status) ? 'failed' : m.versions.some(v => v.state === 'scored') ? 'done' : '';
    if (tab === 'review') return asks.some(w => askTab(w) === 'review') ? 'waiting' : acceptedId(m) ? 'done' : '';
    return acceptedId(m) ? 'done' : '';
  }
  function tabMeta(tab, m) {
    const run = runOf(m), sel = selectedVersion();
    if (tab === 'brief') return (m.brief && m.brief.spec && m.brief.spec.family) || 'what was asked for';
    if (tab === 'build') return run && run.busy ? `${run.current || 'working'} · ${run.percent != null ? run.percent : 0}%` : !m.running && STUCK.includes(m.status) ? 'stopped' : 'the team at work';
    if (tab === 'review') return sel ? sel.id + (sel.score != null ? ' · ' + sel.score + ' / 10' : sel.state === 'building' ? ' · building' : '') : 'no version yet';
    return acceptedId(m) ? acceptedId(m) + ' accepted' : 'files to download';
  }
  function renderTabs() {
    const m = app.data.model, el = $('#tabs');
    if (!el) return;
    if (!m) { setHtml(el, ''); return; }
    setHtml(el, TABS.map((t, i) => {
      const st = tabState(t, m), on = app.tab === t;
      return `<button class="stab${on ? ' on' : ''}${st ? ' st-' + st : ''}" role="tab" aria-selected="${on}" data-act="tab" data-tab="${t}" title="${TAB_NAMES[t]} (Alt ${i + 1})">
        <span class="stab-n">${st === 'done' ? MG.icon('check') : i + 1}</span><span class="stab-t"><span class="b5">${TAB_NAMES[t]}</span><span class="xs mu ell">${esc(st === 'waiting' ? 'needs you' : tabMeta(t, m))}</span></span></button>`;
    }).join('<span class="stab-sep" aria-hidden="true"></span>'));
  }

  function statusOf(m) {
    if (m.running || buildingVersion(m)) return ['working', 'Building'];
    const acc = acceptedId(m);
    if (acc) return ['done', 'Accepted ' + acc];
    return { done: ['idle', 'Ready for review'], needs_changes: ['waiting', 'Needs changes'], failed: ['failed', 'Build failed'],
             stopped: ['failed', 'Stopped'], interrupted: ['failed', 'Interrupted'] }[m.status] || ['idle', m.versions.length ? 'Ready for review' : 'New'];
  }
  function renderHead() {
    const m = app.data.model, el = $('#ws-head');
    if (!el) return;
    if (!m) { setHtml(el, ''); return; }
    const cur = selectedVersion(), st = statusOf(m);
    const path = PATHS[(cur && cur.path) || (m.brief && m.brief.path)];
    const picker = cur ? `<span class="menu-host"><button class="ver-pick" data-act="ver-menu" aria-haspopup="true" aria-expanded="false" title="Choose a version">${cur.state === 'building' ? '<span class="sq s-working"></span>' : ''}${esc(cur.id)}${cur.state === 'building' ? ' building' : cur.score != null ? ' · ' + cur.score : ''}${MG.icon('chevron-down', 'mu')}</button>
      <div class="menu" id="ver-menu" role="menu">${m.versions.map(v => `<button class="menu-i${v.id === cur.id ? ' on' : ''}" role="menuitem" data-act="pick-version" data-v="${esc(v.id)}"><span class="b5">${esc(v.id)}</span><span class="xs mu grow">${esc(v.meta || '')}</span><span class="score">${v.score != null ? v.score : '—'}</span></button>`).join('')}</div></span>` : '';
    const stuck = !m.running && STUCK.includes(m.status);
    const acts = (m.running && app.handlers.stop ? `<button class="btn sm" data-act="stop-build" title="Stop this build now">${MG.icon('x')}Stop</button>` : '')
      + (stuck && app.handlers.retry ? `<button class="btn sm" data-act="retry-build" title="Run the last build of this model again">${MG.icon('rotate')}Try again</button>` : '')
      + (stuck && app.handlers.deleteModel ? `<button class="btn sm" data-act="delete-model" title="Delete this model and all its versions">${MG.icon('x')}Delete model</button>` : '');
    setHtml(el, `<button class="icon-btn open-chat" data-act="open-chat" title="Show chat (Ctrl \\)" aria-label="Show chat">${MG.icon('message')}</button>
      <h1 class="ws-title ell" dir="auto" title="${esc(m.name)}">${esc(m.name)}</h1>${picker}
      ${path ? `<span class="badge path" title="${esc(path[1])}">${esc(path[0])}</span>` : ''}
      <span class="xs st st-${st[0]}"><span class="sq"></span>${esc(st[1])}</span>
      <span class="grow"></span>${acts}`);
  }

  // ---- Brief: what was asked for, what the research found, the spec, how it is made --------
  const lines = (label, items) => ((items || []).length ? `<div class="lbl">${esc(label)}</div><ul class="plain">${items.map(i => `<li dir="auto">${esc(typeof i === 'string' ? i : i.name || i.id || JSON.stringify(i))}</li>`).join('')}</ul>` : '');
  const dimsText = d => (d && (d.width || d.depth || d.height) ? ['width', 'depth', 'height'].filter(k => d[k]).map(k => `${k[0].toUpperCase()} ${Number(d[k]).toFixed(2)}`).join(' × ') + ' m' : '');
  const shots = (list, label) => ((list || []).length ? `<div class="lbl">${esc(label)}</div><div class="shots">${list.map(u => `<button class="shot" data-act="lightbox" data-src="${esc(u)}" aria-label="Open the image"><img src="${esc(u)}" alt="" loading="lazy"></button>`).join('')}</div>` : '');
  function renderBrief() {
    const m = app.data.model, el = $('#pane-brief');
    if (!el) return;
    if (!m) { setHtml(el, ''); return; }
    const b = m.brief || {}, spec = b.spec, res = b.research, as = b.asset;
    const size = b.size ? ['width', 'depth', 'height'].filter(k => b.size[k]).map(k => k[0].toUpperCase() + ' ' + b.size[k]).join(' × ') + ' ' + (b.size.unit || 'cm') : '';
    const path = PATHS[b.path];
    const made = !path ? '<p class="mu">Decided when the build starts: a ready-made model when the free catalogue has the object, the car generator for a car, else the team builds it.</p>'
      : `<p><span class="badge path">${esc(path[0])}</span> ${esc(path[1])}.</p>`
        + (as ? `<dl class="kvs">${[['Model', as.name || as.asset], ['Base car', as.base], ['Type', as.type], ['Era', as.era], ['Paint', as.paint_name], ['Source', as.source], ['Licence', as.licence || as.license], ['Note', as.note]].filter(r => r[1]).map(r => `<dt>${r[0]}</dt><dd dir="auto">${esc(r[1])}</dd>`).join('')}</dl>` : '')
        + (b.candidates > 1 ? `<p class="xs mu">${b.candidates - 1} other ready-made candidate${b.candidates > 2 ? 's' : ''}: "Build next version" tries the next one.</p>` : '');
    const specCard = spec ? `<dl class="kvs">${[['Object', spec.object], ['Family', spec.family], ['Style', spec.style], ['Use', spec.use], ['Size', dimsText(spec.dimensions)], ['Parts', spec.parts || null], ['Motion', spec.motion ? 'Yes, it moves' : null]].filter(r => r[1]).map(r => `<dt>${r[0]}</dt><dd dir="auto">${esc(r[1])}</dd>`).join('')}</dl>${spec.summary ? `<p dir="auto">${esc(spec.summary)}</p>` : ''}${Array.isArray(spec.materials) ? lines('Materials', spec.materials) : ''}`
      : `<p class="mu">${m.running ? 'Armin is writing the spec.' : 'No spec yet: Armin writes it when the build starts.'}</p>`;
    const resCard = res ? `${dimsText(res.dimensions) ? `<dl class="kvs"><dt>Real size</dt><dd>${esc(dimsText(res.dimensions))}</dd></dl>` : ''}${lines('Proportions', res.proportions)}${lines('Features every version needs', res.features)}${lines('Colours', res.colours)}${lines('Guessed, not known', res.unverified)}${shots(b.found, 'Photos the research kept')}`
      : shots(b.found, 'Photos the research kept') || `<p class="mu">${b.path === 'library' || b.path === 'remix' ? 'No research: this model was not built from a spec.' : 'Nothing looked up yet. Before the spec, the researcher finds the real object\'s size, proportions and photos.'}</p>`;
    setHtml(el, `<div class="brief-grid">
      <div class="col">
        <section class="card pad"><div class="sec-h">Request</div><p class="req" dir="auto">${esc(b.request || m.request || 'Described in the chat.')}</p>
          ${size || b.budget != null ? `<div class="row wrap chips">${size ? `<span class="badge">${MG.icon('ruler')}Size ${esc(size)}</span>` : ''}${b.budget != null ? `<span class="badge">${MG.icon('coin')}Budget $${Number(b.budget).toFixed(2)}</span>` : ''}</div>` : ''}
          ${shots(b.references, 'Your reference images')}</section>
        <section class="card pad"><div class="sec-h">How it is made</div>${made}</section>
        <section class="card pad"><div class="sec-h">Spec</div>${specCard}</section>
      </div>
      <div class="col"><section class="card pad"><div class="sec-h">Research</div>${resCard}</section></div>
    </div>`);
  }

  // ---- Build: the side column next to the studio floor --------------------------------------
  function renderBuildSide() {
    const m = app.data.model, live = $('#bs-live'), wait = $('#live-wait');
    if (!live) return;
    const run = runOf(m), busy = !!(run && run.busy), bv = buildingVersion(m);
    live.hidden = !(busy || bv);
    wait.hidden = !!bv;
    if (!bv && busy) wait.textContent = `${run.current || 'Getting ready'} · the 3D preview starts at Build`;
  }

  // ---- Review: versions, the critic's review, the decision ----------------------------------
  function versionCard(v, m, sel) {
    const links = (app.handlers.revert && v.state === 'scored' && !v.base && !m.running && m.versions.length > 1 ? `<button class="link xs" data-act="base-version" data-v="${esc(v.id)}">Build changes from ${esc(v.id)}</button>` : '')
      + (app.handlers.deleteVersion && !m.running && v.state !== 'building' ? `<button class="link xs" data-act="delete-version" data-v="${esc(v.id)}">Delete</button>` : '');
    return `<div class="vs-item${sel && v.id === sel.id ? ' on' : ''}"><button class="vs-card" data-act="pick-version" data-v="${esc(v.id)}" aria-pressed="${!!(sel && v.id === sel.id)}"><span class="vthumb">${MG.pic(v, 46, m.kind || 'chair')}</span><span class="grow"><span class="row"><span class="b5">${esc(v.id)}</span>${v.accepted ? '<span class="badge ok">accepted</span>' : ''}${v.base && !v.accepted && m.versions.length > 1 ? '<span class="badge">next starts here</span>' : ''}</span><span class="xs mu block ell">${esc(v.meta || '')}</span>${v.state === 'building' ? `<span class="vbar"><span style="width:${v.progress || 0}%"></span></span>` : ''}</span><span class="score">${v.score != null ? v.score : v.state === 'failed' ? '×' : '—'}</span></button>${links ? `<div class="vs-links row">${links}</div>` : ''}</div>`;
  }
  function renderReview() {
    const m = app.data.model;
    if (!$('#rv-body')) return;
    if (!m) { setHtml($('#vstrip'), ''); setHtml($('#rv-body'), ''); setHtml($('#decide'), ''); return; }
    const sel = selectedVersion(), r = reviewOf(m, sel), building = sel && sel.state === 'building';
    setHtml($('#vstrip'), m.versions.map(v => versionCard(v, m, sel)).join('') || `<div class="xs mu vs-none" dir="auto">${!m.running && STUCK.includes(m.status) ? 'The build stopped before v1' + (m.error ? ': ' + esc(m.error.slice(0, 220)) : '.') : 'No version yet. The first one shows here when it is built.'}</div>`);
    const review = r ? `<section class="sec" id="review"><div class="row between"><div class="row">${MG.portrait('annie', 28)}<div><div class="sec-h flat">Annie's review of ${esc(r.version)}</div>${r.passed != null ? `<div class="xs mu">${r.passed ? 'Passed the checks' : 'Did not pass yet'}</div>` : ''}</div></div><span class="scorebig">${r.score}<span class="mu"> / 10</span></span></div>
        ${r.summary ? `<p class="review-sum" dir="auto">${esc(r.summary)}</p>` : ''}
        <div class="crit">${r.criteria.map(c => `<div class="row"><span class="cn">${esc(c.name)}</span><span class="bar"><span class="${c.score >= 7 ? 'good' : 'meh'}" style="width:${c.score * 10}%"></span></span><span class="mu cs">${c.score}</span></div>`).join('')}</div>
        ${(r.gates || []).length ? `<div class="gates">${r.gates.map(g => `<span class="gate ${g.pass ? 'ok' : 'bad'}" title="${esc(g.detail || '')}">${MG.icon(g.pass ? 'check' : 'x')}${esc(g.name)}</span>`).join('')}</div>` : ''}</section>
      ${(r.renders || []).length ? `<section class="sec"><div class="sec-h">Renders</div><div class="renders">${r.renders.map(n => typeof n === 'string'
          ? `<div><div class="rthumb">${MG.thumb(m.kind || 'chair', 44)}</div><div class="xs mu">${esc(n)}</div></div>`
          : `<button class="rimg" data-act="lightbox" data-src="${esc(n.url)}" title="${esc(n.name)}"><img src="${esc(n.url)}" alt="${esc(n.name)}" loading="lazy"><span class="xs mu">${esc(n.name)}</span></button>`).join('')}</div></section>` : ''}
      ${(r.changes || []).length ? `<section class="sec"><div class="sec-h">Changes Annie asked for</div><ol class="changes">${r.changes.map(c => `<li dir="auto">${esc(c)}</li>`).join('')}</ol></section>` : ''}`
      : sel && sel.state === 'failed' ? `<section class="sec"><div class="mu" dir="auto">${esc(sel.id)} did not build${sel.note ? ': ' + esc(sel.note.slice(0, 220)) : '.'}</div></section>`
      : building ? `<section class="sec"><div class="mu">Annie reviews ${esc(sel.id)} when it is built and rendered. The Build stage shows the team at work.</div></section>`
      : sel ? `<section class="sec"><div class="mu" dir="auto">${esc(sel.id)} has no review${sel.note ? ': ' + esc(sel.note) : '.'}</div></section>`
      : `<section class="sec"><div class="mu">The review shows here once a version is built.</div></section>`;
    setHtml($('#rv-body'), review + nextSection(m.next));
    renderDecide();
  }
  // The decision bar: Hange's "accept, next version or comments?" question when it is about the
  // version on screen, else Request changes and Accept.
  function renderDecide() {
    const m = app.data.model, el = $('#decide');
    if (!el) return;
    if (!m || !m.versions.length) { setHtml(el, ''); return; }
    const sel = selectedVersion(), accepted = sel && sel.accepted;
    const w = asksOf(m).find(x => askTab(x) === 'review');
    const q = w && w.data.question;
    if (q && sel && (q.version == null || q.version === sel.number)) {
      setHtml(el, `<div class="decide-k xs"><span class="ask-dot" aria-hidden="true"></span>Your call on ${esc(sel.id)}</div><div class="decide-btns">${(q.options || []).map(o => `<button class="btn${/^accept\b/i.test(o) ? ' primary' : ''}" data-act="answer" data-agent="${esc(w.id)}" data-choice="${esc(o)}">${esc(o)}</button>`).join('')}</div>`);
      return;
    }
    const canAccept = sel && sel.state === 'scored' && !accepted;
    setHtml(el, `<div class="decide-btns"><button class="btn" data-act="change"${m.running ? ' title="The team is still working; your change starts when this build ends"' : ''}>Request changes<kbd>C</kbd></button><button class="btn primary" data-act="accept"${canAccept ? '' : ' disabled'}>${accepted ? 'Accepted ' + esc(sel.id) : 'Accept ' + esc(sel ? sel.id : '')}${canAccept ? '<kbd>A</kbd>' : ''}</button></div>`);
  }

  // ---- Export: the files of the selected version --------------------------------------------
  const FILES = [['glb', 'GLB', 'Viewers and game engines · carries the motion'], ['fbx', 'FBX', 'Game engines · carries the motion'],
                 ['obj', 'OBJ', 'Any 3D app · no motion'], ['stl', 'STL', '3D printing · the shape only'], ['blend', '.blend', 'The Blender file, with its shader patterns']];
  function fileRow(url, name, note, extra) {
    const body = `<span class="dl-name b5">${esc(name)}</span><span class="xs mu grow ell">${esc(note)}</span>${extra || ''}${MG.icon('download', 'mu')}`;
    return url && url !== '#' ? `<a class="dl-row" href="${esc(url)}" download>${body}</a>` : `<span class="dl-row off" title="Not in the demo">${body}</span>`;
  }
  function renderExport() {
    const m = app.data.model, el = $('#ex-side');
    if (!el) return;
    const sel = m && selectedVersion();
    if (!m || !sel) { setHtml(el, `<section class="sec"><div class="mu">${m ? 'Nothing to download yet. The files show here when the first version is built.' : ''}</div></section>`); return; }
    const d = sel.downloads || {}, g = sel.game, t = (g && g.triangles) || {};
    const std = FILES.filter(f => d[f[0]]).map(f => fileRow(d[f[0]], f[1], f[2])).join('');
    const tri = n => (n != null ? num(n) + ' triangles' : '');
    const game = g ? [['game', 'Game GLB', [tri(t.game), g.budget ? 'limit ' + num(g.budget) : ''].filter(Boolean).join(' · ')], ['lod1', 'LOD1', tri(t.LOD1) || 'Half the detail'],
                      ['lod2', 'LOD2', tri(t.LOD2) || 'A fifth of the detail'], ['collision', 'Collision', 'One box']].filter(f => g.files[f[0]]).map(f => fileRow(g.files[f[0]], f[1], f[2])).join('') : '';
    const size = g && g.size ? g.size.map(n => Number(n).toFixed(2)).join(' × ') + ' m' : '';
    setHtml(el, `<section class="sec"><div class="row between"><div><div class="sec-h flat">${esc(sel.id)}${sel.score != null ? ' · ' + sel.score + ' / 10' : ''}</div><div class="xs mu">${sel.accepted ? 'The accepted version' : sel.state === 'building' ? 'Still building' : 'Not accepted yet: you can still download it'}</div></div>${sel.accepted ? '<span class="badge ok">accepted</span>' : ''}</div></section>
      <section class="sec"><div class="sec-h">Files</div>${std || '<div class="mu">This version has no files yet.</div>'}
        ${d.glb ? `<button class="dl-row" data-act="v-dl-paint"><span class="dl-name b5">Recoloured</span><span class="xs mu grow ell">GLB with the colours you set in the view</span>${MG.icon('download', 'mu')}</button>` : ''}
        ${sel.export && std ? `<a class="btn primary wide dl-all" href="${esc(sel.export)}" download>${MG.icon('download')}All of it · zip</a>` : ''}</section>
      <section class="sec"><div class="sec-h">Game-ready</div>${game ? game + `<p class="xs mu">${[size, 'pivot at the centre of the base', 'real-world scale'].filter(Boolean).join(' · ')}${g.reduced ? ' · detail was reduced to fit the limit' : ''}. These are in the zip too.</p>` : '<div class="mu">This version has no game-ready files.</div>'}</section>
      <section class="sec"><div class="sec-h">Good to know</div><ul class="plain xs mu"><li>GLB and FBX carry the wheel motion; OBJ and STL cannot.</li><li>Patterns such as brick or wood grain are drawn by Blender and are only in the .blend: the other files carry each material's plain colour.</li><li>Click a part in the view to recolour it, then download the GLB with your colours.</li></ul></section>`);
  }

  function updateComposer() {
    const ta = $('#composer');
    if (!ta) return;
    const m = app.data.model, sel = m && selectedVersion();
    ta.placeholder = !m ? 'Describe a model' : app.tab === 'review' && sel ? `Tell Hange what to change in ${sel.id}`
      : app.tab === 'build' && m.running ? 'Message Hange while the team builds' : app.tab === 'export' ? 'Ask Hange for a change or a new model' : 'Describe a model or ask for a change';
  }
  function renderChatScope() {
    const el = $('#chat-scope');
    if (!el) return;
    const m = app.data.model;
    setHtml(el, m && m.project ? `<button class="${app.chatScope === 'model' ? 'on' : ''}" data-act="chat-scope" data-scope="model" title="Only the messages about ${esc(m.name)}">This model</button><button class="${app.chatScope === 'all' ? 'on' : ''}" data-act="chat-scope" data-scope="all" title="Every message, with the model it is about">All models</button>` : '');
  }

  function renderWorkspace() {
    viewerSkeleton();
    const m = app.data.model;
    $('#ws').classList.toggle('no-model', !m);
    autoTab();
    renderHead(); renderTabs(); renderBrief(); renderBuildSide(); renderReview(); renderExport(); renderChatScope(); updateComposer();
    if (!m) { setBig(false); placeViewer(); syncViewer(null); return; }
    placeViewer();
    const shown = shownVersion(), bv = buildingVersion(m);
    syncViewer(shown, m);
    if (shown && bv && bv.id !== shown.id) $('#viewer-hint').textContent = bv.id + ' is building · showing ' + shown.id;
  }
  app.renderInspector = app.renderWorkspace = renderWorkspace;

  // Show the selected version in the viewer: the GLB when there is one and the viewer
  // library is installed, else its render, else the drawing (demo).
  let viewerUrl = null;
  let viewerLiveOf = null;     // the version whose Live step the viewer shows, else null
  let viewerMounting = null;   // shared, so renders that overlap while it starts make one viewer, not two
  // While a version builds, Live shows the newest step the team built in Blender (a part on its
  // own, the assembly, or the model with its materials), or the step picked in the live chip.
  function liveStep(sel) {
    const L = sel && sel.state === 'building' ? sel.live : null;
    if (vs.liveFor !== (sel && sel.id)) { vs.liveFor = sel && sel.id; vs.livePin = null; }
    if (!L || !vs.live) return null;
    return (vs.livePin && L.steps.find(s => s.key === vs.livePin)) || L.latest;
  }
  function renderLiveChip(sel, step) {
    const el = $('#viewer-live');
    if (!el) return;
    const L = sel && sel.state === 'building' ? sel.live : null;
    const html = step && L ? `<div class="vchip row live-chip"><span class="live-dot" aria-hidden="true"></span><span class="b5">Live</span>
      <select class="live-pick xs" data-vlive aria-label="Build step to show">
        <option value=""${vs.livePin ? '' : ' selected'}>Newest · ${esc(L.latest.label)}</option>
        ${L.steps.slice().reverse().map(s => `<option value="${esc(s.key)}"${vs.livePin === s.key ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}
      </select></div>` : '';
    if (el.innerHTML !== html) el.innerHTML = html;   // unchanged: leave it, so an open picker stays open
  }

  async function syncViewer(sel, m) {
    const still = $('#viewer-still'), hint = $('#viewer-hint');
    if (!still) return;
    const building = !!(sel && sel.state === 'building');
    $('#viewer').classList.toggle('building', building);
    pressed('v-live', vs.live);
    const step = liveStep(sel);
    renderLiveChip(sel, step);
    const glb = step ? step.url : sel && sel.glb;
    if (glb && app.handlers.mountViewer && !app.viewerFailed) {
      try {
        if (!app.viewer) app.viewer = await (viewerMounting = viewerMounting || app.handlers.mountViewer($('#viewer-mount')).then(v => { wireViewer(v); return v; }));
        if (viewerUrl !== glb) {
          // the step after a Live step of this same version keeps the camera the person set
          const keepView = !!(viewerLiveOf && viewerLiveOf === sel.id);
          viewerUrl = glb; viewerLiveOf = step ? sel.id : null;
          still.innerHTML = ''; still.classList.add('loading');
          app.viewer.setColourContext([m && m.name, m && m.kind, m && m.project].filter(Boolean).join(' '));
          vs.colourFor = null;
          await app.viewer.load(glb, { keepView });
          if (!step) loadColours(m, sel, glb);
        }
        still.classList.remove('loading');
        $('#viewer').classList.add('has-3d');
        hint.textContent = VIEW_HINT;
        return;
      } catch (e) {
        console.warn('viewer:', e);
        app.viewerFailed = true;
        viewerUrl = null;
      }
    }
    $('#viewer').classList.remove('has-3d');
    if (app.viewer) { app.viewer.clear(); viewerUrl = null; }
    if (vs.stats || vs.measure || vs.section.on) {
      if (vs.measure && app.viewer) app.viewer.setMeasure(false);
      Object.assign(vs, { stats: null, parts: [], sel: null, isolated: null, measure: false, reading: null });
      vs.section.on = false;
      closePop();
      renderViewerUi();
    }
    still.classList.remove('loading');
    still.innerHTML = sel ? MG.pic(sel, 300, m && m.kind) : '';
    hint.textContent = !sel ? '' : building ? (!vs.live ? 'Live is off · the 3D model appears when this version is built'
      : app.viewerFailed ? 'The 3D viewer could not start' : 'Live · the first part shows here as soon as a builder finishes it') : sel.thumb ? (app.viewerFailed ? 'The 3D viewer could not start · showing a render' : 'No 3D file for this version · showing a render') : '';
  }

  // ---- 3D view tools: views, display, scene, dimensions, measure, section, stats, parts,
  // spin, screenshot, the large view and full screen. vs mirrors what the viewer is doing.
  const vs = { mode: 'shaded', ortho: false, lighting: 'studio', exposure: 1, bg: 'theme', grid: true, gizmo: true,
               speed: 2, units: 'auto', dims: false, measure: false, spin: false, motion: false, play: false,
               section: { on: false, axis: 'y', pos: 0.5, flip: false },
               live: loadPref('live', true), livePin: null, liveFor: null,
               big: false, drawer: false, pop: null, stats: null, parts: [], sel: null, isolated: null, reading: null, filter: '',
               paint: null, types: [], painting: false, colourFor: null };
  const has3d = () => !!(app.viewer && $('#viewer') && $('#viewer').classList.contains('has-3d'));
  const num = n => Number(n || 0).toLocaleString();
  const isFull = () => !!document.fullscreenElement && document.fullscreenElement === $('#viewer');
  const drawerShown = () => vs.drawer && (vs.big || isFull());

  function wireViewer(v) {
    v.on('load', d => {
      Object.assign(vs, { stats: d.stats, parts: d.parts, sel: null, isolated: null, reading: null,
                          motion: !!d.motion, play: !!d.playing });
      vs.section.on = false;
      vs.paint = null; vs.types = v.getColourTypes();
      if (vs.pop === 'section' || vs.pop === 'paint') closePop();
      renderViewerUi();
      if (vs.pop === 'info') renderPop();
      if (drawerShown()) buildDrawer();
    });
    v.on('select', p => { vs.sel = p; renderViewerUi(); });
    v.on('paint', p => {
      vs.paint = p;
      if (!p && vs.pop === 'paint') closePop();   // nothing selected any more: the popover goes too
      renderSelChip();
      if (vs.pop === 'paint' && !vs.painting) renderPop();
    });
    v.on('colours', e => {
      vs.types = v.getColourTypes(); vs.parts = v.getParts();
      if (drawerShown()) renderPartList();
      if (e.user) saveColoursSoon(e.colours);
    });
    v.on('parts', list => { vs.parts = list; vs.isolated = v.getIsolated(); if (vs.sel) vs.sel = list[vs.sel.id] || null; renderViewerUi(); });
    v.on('measure', r => { vs.reading = r; renderReadout(); });
  }

  function pressed(act, on) {
    const b = $(`#viewer .viewer-tools [data-act="${act}"]`);
    if (b) { b.classList.toggle('on', !!on); b.setAttribute('aria-pressed', String(!!on)); }
  }
  function renderViewerUi() {
    const v = $('#viewer');
    if (!v) return;
    pressed('v-dims', vs.dims); pressed('v-measure', vs.measure); pressed('v-orbit', vs.spin); pressed('v-parts', drawerShown()); pressed('v-live', vs.live);
    v.classList.toggle('has-motion', vs.motion);
    const play = $('#viewer [data-act="v-play"]');
    if (play) { play.innerHTML = MG.icon(vs.play ? 'pause' : 'play'); play.title = vs.play ? 'Pause the motion' : 'Play the motion'; play.setAttribute('aria-label', play.title); play.setAttribute('aria-pressed', String(vs.play)); }
    const sb = $('#viewer [data-vpop="section"]'); if (sb) sb.classList.toggle('on', vs.section.on);
    const big = $('#v-big-btn');
    if (big) { big.innerHTML = MG.icon(vs.big ? 'collapse' : 'expand'); big.title = vs.big ? 'Back to the panel (V)' : 'Large view (V)'; big.setAttribute('aria-label', big.title); }
    const full = $('#v-full-btn');
    if (full) { full.classList.toggle('on', isFull()); full.title = isFull() ? 'Leave full screen' : 'Full screen'; full.setAttribute('aria-label', full.title); }
    v.classList.toggle('drawer-open', drawerShown());
    renderSelChip(); renderReadout();
    if (drawerShown()) { if (!$('#vd-list')) buildDrawer(); else renderDrawerBody(); }
  }

  function renderSelChip() {
    const el = $('#viewer-sel'), p = vs.sel, c = vs.paint;
    if (!el) return;
    const what = c ? `<span class="b5 ell">${esc(c.label)}</span><span class="xs mu">${c.pieces} piece${c.pieces === 1 ? '' : 's'}</span>`
      : p ? `<span class="b5 ell">${esc(p.name)}</span><span class="xs mu">${num(p.triangles)} triangles</span>` : '';
    el.innerHTML = p || c ? `<div class="vchip row">${what}
      ${c ? `<button class="btn sm${vs.pop === 'paint' ? ' on' : ''}" data-act="v-pop" data-vpop="paint" aria-haspopup="true" aria-expanded="${vs.pop === 'paint'}" title="Colour what is selected"><span class="sw-dot" style="--sw:${swatchBg(c.colour)}"></span>Colour</button>` : ''}
      ${p ? `<button class="btn sm" data-act="v-iso" data-part="${p.id}" title="${vs.isolated === p.id ? 'Show all parts' : 'Show only ' + esc(p.name)}">${vs.isolated === p.id ? 'Show all' : 'Isolate'}</button>
      <button class="btn sm" data-act="v-eye" data-part="${p.id}" title="${p.visible ? 'Hide' : 'Show'} ${esc(p.name)}">${p.visible ? 'Hide' : 'Show'}</button>` : ''}
      <button class="icon-btn sm" data-act="v-unselect" title="Clear the selection" aria-label="Clear the selection">${MG.icon('x')}</button></div>` : '';
  }
  function renderReadout() {
    const el = $('#viewer-readout');
    if (!el) return;
    if (!vs.measure) { el.innerHTML = ''; return; }
    const r = vs.reading;
    const text = !r || !r.points ? 'Click a point on the model' : r.points === 1 ? 'Click a second point' : `<b>${esc(r.text)}</b><span class="mu">· click to measure again</span>`;
    el.innerHTML = `<div class="vchip row">${MG.icon('measure', 'mu')}<span class="row">${text}</span><button class="icon-btn sm" data-act="v-measure" title="Stop measuring" aria-label="Stop measuring">${MG.icon('x')}</button></div>`;
  }

  // ---- the popovers under the toolbar --------------------------------------------------
  function openPop(kind, btn) {
    if (vs.pop === kind) { closePop(); return; }
    vs.pop = kind;
    if (kind === 'section' && !vs.section.on && has3d()) Object.assign(vs.section, app.viewer.setSection({ axis: vs.section.axis, pos: vs.section.pos, flip: vs.section.flip }));
    if (app.viewer) app.viewer.setHighlightStyle(kind === 'paint' ? 'edges' : 'fill');
    const pop = $('#vpop');
    renderPop();
    pop.classList.add('on');
    $$('#viewer [data-vpop]').forEach(b => b.setAttribute('aria-expanded', String(b.dataset.vpop === kind)));
    const vr = $('#viewer').getBoundingClientRect(), br = btn.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(br.left - vr.left, vr.width - pop.offsetWidth - 8)) + 'px';
    renderViewerUi();
  }
  function closePop() {
    if (vs.pop === 'paint' && app.viewer) app.viewer.setHighlightStyle('fill');
    vs.pop = null;
    const pop = $('#vpop');
    if (pop) pop.classList.remove('on');
    $$('#viewer [data-vpop]').forEach(b => b.setAttribute('aria-expanded', 'false'));
  }
  const seg = (key, cur, opts) => `<div class="vp-seg">${opts.map(([val, label, title]) => `<button class="${String(cur) === String(val) ? 'on' : ''}" data-act="v-set" data-key="${key}" data-val="${val}"${title ? ` title="${title}"` : ''}>${label}</button>`).join('')}</div>`;
  const check = (key, on, label) => `<label class="vp-check"><input type="checkbox" data-vin="${key}"${on ? ' checked' : ''}><span>${label}</span></label>`;
  const slider = (key, val, min, max, step, label, shown) => `<label class="vp-slider"><span class="row between"><span>${label}</span><span class="mu" id="vin-${key}">${shown}</span></span><input type="range" data-vin="${key}" min="${min}" max="${max}" step="${step}" value="${val}"></label>`;
  function statsHtml(s) {
    const f = app.viewer ? app.viewer.formatLength : (x => x.toFixed(2) + ' m');
    return `<dl class="vstats"><dt>Triangles</dt><dd>${num(s.triangles)}</dd><dt>Vertices</dt><dd>${num(s.vertices)}</dd><dt>Parts</dt><dd>${num(s.parts)}</dd><dt>Meshes</dt><dd>${num(s.meshes)}</dd><dt>Materials</dt><dd>${num(s.materials)}</dd>
      <dt>Width</dt><dd>${f(s.size.x)}</dd><dt>Depth</dt><dd>${f(s.size.z)}</dd><dt>Height</dt><dd>${f(s.size.y)}</dd></dl>`;
  }
  function renderPop() {
    const pop = $('#vpop');
    if (!pop || !vs.pop) return;
    const k = vs.pop;
    if (k === 'views') {
      pop.innerHTML = `<div class="vp-h">View</div><div class="vp-grid">${[['front', 'Front', 1], ['back', 'Back', 2], ['left', 'Left', 3], ['right', 'Right', 4], ['top', 'Top', 5], ['iso', '3/4', 6]].map(([v, l, n]) => `<button class="vp-b" data-act="v-view" data-view="${v}"><span>${l}</span><kbd>${n}</kbd></button>`).join('')}</div>
        ${check('ortho', vs.ortho, 'Orthographic (no perspective)')}
        <div class="row vp-row"><button class="btn sm grow" data-act="v-fit">Fit to model</button><button class="btn sm grow" data-act="v-reset">Reset view</button></div>`;
    } else if (k === 'display') {
      pop.innerHTML = `<div class="vp-h">Display</div>${[['shaded', 'Shaded', 'Materials and colours'], ['wire', 'Wireframe', 'Only the edges of every face'], ['wire-shaded', 'Wireframe over shaded', 'The surface with its edges'], ['clay', 'Clay', 'Plain grey, to judge the shape'], ['xray', 'X-ray', 'See through to the inside']].map(([v, l, d]) => `<button class="menu-i${vs.mode === v ? ' on' : ''}" data-act="v-set" data-key="mode" data-val="${v}"><span class="vp-tick">${vs.mode === v ? MG.icon('check') : ''}</span><span class="grow"><span class="block">${l}</span><span class="xs mu">${d}</span></span></button>`).join('')}`;
    } else if (k === 'scene') {
      pop.innerHTML = `<div class="vp-h">Lighting</div>${seg('lighting', vs.lighting, [['studio', 'Studio'], ['soft', 'Soft'], ['contrast', 'Contrast']])}
        ${slider('exposure', vs.exposure, 0.2, 3, 0.05, 'Exposure', vs.exposure.toFixed(2))}
        <div class="vp-h">Background</div>${seg('bg', vs.bg, [['theme', 'Match theme'], ['light', 'Light'], ['dark', 'Dark']])}
        ${check('grid', vs.grid, 'Grid')}${check('gizmo', vs.gizmo, 'Axis gizmo')}
        ${slider('speed', vs.speed, 0.5, 10, 0.5, 'Spin speed', vs.speed.toFixed(1))}
        <div class="vp-h">Units</div>${seg('units', vs.units, [['auto', 'Auto'], ['mm', 'mm'], ['cm', 'cm'], ['m', 'm']])}`;
    } else if (k === 'section') {
      const s = vs.section;
      pop.innerHTML = `<div class="vp-h">Section cut</div>${seg('axis', s.axis, [['x', 'X', 'Cut side to side'], ['y', 'Y', 'Cut top to bottom'], ['z', 'Z', 'Cut front to back']])}
        ${slider('secpos', s.pos, 0, 1, 0.005, 'Position', Math.round(s.pos * 100) + '%')}
        ${check('secflip', s.flip, 'Keep the other side')}
        <div class="row vp-row"><button class="btn sm grow" data-act="v-section-off"${s.on ? '' : ' disabled'}>Turn off</button>${s.on ? '' : '<button class="btn sm primary grow" data-act="v-section-on">Turn on</button>'}</div>`;
    } else if (k === 'paint') {
      pop.innerHTML = paintPopHtml();
    } else if (k === 'info') {
      pop.innerHTML = `<div class="vp-h">Model</div>${vs.stats ? statsHtml(vs.stats) : '<div class="xs mu">No 3D model is loaded.</div>'}`;
    }
  }
  function applySetting(key, val) {
    const v = app.viewer;
    if (!v) return;
    if (key === 'mode') { vs.mode = v.setDisplayMode(val); closePop(); }
    else if (key === 'lighting') vs.lighting = v.setLighting(val);
    else if (key === 'bg') vs.bg = v.setBackground(val);
    else if (key === 'units') {
      vs.units = v.setUnits(val);
      if (vs.reading && vs.reading.distance != null) vs.reading.text = v.formatLength(vs.reading.distance);
      renderReadout();
      if (drawerShown()) renderDrawerBody();
    }
    else if (key === 'axis') Object.assign(vs.section, v.setSection({ axis: val, pos: vs.section.pos, flip: vs.section.flip }));
    renderPop();
    renderViewerUi();
  }
  function applyInput(key, el) {
    const v = app.viewer;
    if (!v) return;
    const shown = $('#vin-' + key);
    if (key === 'ortho') vs.ortho = v.setOrtho(el.checked);
    else if (key === 'grid') vs.grid = v.setGrid(el.checked);
    else if (key === 'gizmo') vs.gizmo = v.setGizmo(el.checked);
    else if (key === 'exposure') { vs.exposure = v.setExposure(+el.value); if (shown) shown.textContent = vs.exposure.toFixed(2); }
    else if (key === 'speed') { vs.speed = v.setRotateSpeed(+el.value); if (shown) shown.textContent = vs.speed.toFixed(1); }
    else if (key === 'secpos' || key === 'secflip') {
      if (key === 'secpos') vs.section.pos = +el.value; else vs.section.flip = el.checked;
      Object.assign(vs.section, v.setSection({ axis: vs.section.axis, pos: vs.section.pos, flip: vs.section.flip }));
      if (key === 'secpos' && shown) shown.textContent = Math.round(vs.section.pos * 100) + '%';
      if (key === 'secflip') renderPop();
      renderViewerUi();
    }
  }

  // ---- colouring: the popover, saving per version, the recoloured GLB -------------------
  const MIXED = 'conic-gradient(#e5675c, #f6c700, #7dbb4a, #4c7df7, #e5675c)';
  const swatchBg = hex => (hex && /^#[0-9a-f]{6}$/i.test(hex) ? hex : MIXED);
  function paintPopHtml() {
    const p = vs.paint, v = app.viewer;
    const head = `<div class="vp-head row between"><span class="vp-h">Colour</span><button class="icon-btn sm" data-act="v-pop-close" title="Close (Esc)" aria-label="Close the colour options">${MG.icon('x')}</button></div>`;
    const sw = (hex, title) => `<button class="vp-sw${p && p.colour === hex ? ' on' : ''}" data-act="v-paint" data-hex="${hex}" style="--sw:${hex}" title="${title || hex}" aria-label="Colour ${hex}"></button>`;
    const chips = vs.types.map(t => {
      const on = p && p.whole && p.types.length === 1 && p.types[0] === t.type && p.groups.length === t.count;
      return `<button class="vp-chip${on ? ' on' : ''}" data-act="v-paint-type" data-type="${esc(t.type)}" title="Select all of the ${esc(t.label.toLowerCase())}"><span class="sw-dot" style="--sw:${swatchBg(t.colour)}"></span>${esc(t.label)}</button>`;
    }).join('');
    const tip = '<div class="xs mu vp-tip">Click: its whole colour group · Alt+click: one piece · Ctrl+click: add more</div>';
    if (!p) return `${head}<div class="vp-chips">${chips}</div><div class="xs mu vp-tip">Click a part of the model, or a group above.</div>${tip}`;
    const own = p.suggestions.slice(0, p.groups.length);
    return `${head}<div class="vp-chips">${chips}</div>
      <div class="vp-sel row between"><span class="b5 ell">${esc(p.label)}</span><span class="xs mu">${p.pieces} piece${p.pieces === 1 ? '' : 's'}</span></div>
      <div class="vp-h">Suggested</div><div class="vp-sws">${p.suggestions.map(h => sw(h, own.includes(h) ? h + ' · its own colour' : h)).join('')}</div>
      <div class="vp-h">This model</div><div class="vp-sws">${v.getModelPalette().map(h => sw(h)).join('')}</div>
      <div class="vp-h">Custom</div><div class="row vp-custom"><input type="color" id="vp-picker" data-vpaint="picker" value="${p.colour || '#888888'}" aria-label="Pick any colour"><input class="vp-hex" id="vp-hex" data-vpaint="hex" value="${p.colour || ''}" placeholder="#rrggbb" maxlength="7" spellcheck="false" aria-label="Colour as hex"></div>
      <div class="row vp-row"><button class="btn sm grow" data-act="v-paint-reset"${p.changed ? '' : ' disabled'}>Reset</button><button class="btn sm grow" data-act="v-paint-reset-all"${p.any ? '' : ' disabled'}>Reset all</button></div>${tip}`;
  }

  const coloursUrl = t => `/api/projects/${encodeURIComponent(t.project)}/versions/${t.number}/colors`;
  async function loadColours(m, sel, glb) {
    if (!m || !m.project || !sel || sel.number == null) return;
    const target = { project: m.project, number: sel.number };
    try {
      const r = await fetch(coloursUrl(target));
      const saved = r.ok ? await r.json() : null;
      if (viewerUrl !== glb || !app.viewer) return;   // another version was opened meanwhile
      vs.colourFor = target;
      if (saved && (Object.keys(saved.groups || {}).length || Object.keys(saved.pieces || {}).length)) app.viewer.setColours(saved);
    } catch (e) { console.warn('colours:', e); }
  }
  let colourTimer = null;
  function saveColoursSoon(c) {
    const target = vs.colourFor;
    if (!target) return;
    clearTimeout(colourTimer);
    colourTimer = setTimeout(async () => {
      try {
        const r = await fetch(coloursUrl(target), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      } catch (e) { toast('Could not save the colours: ' + e.message); }
    }, 500);
  }
  async function downloadColoured() {
    const m = app.data.model, sel = selectedVersion();
    const name = ((m && (m.project || m.name)) || 'model').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + (sel ? '-' + sel.id : '') + '-colours.glb';
    try {
      const buf = await app.viewer.exportGlb();
      if (!buf) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([buf], { type: 'model/gltf-binary' }));
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      toast('Saved ' + name + (app.viewer.hasColours() ? '' : ' (no colours changed yet)'));
    } catch (e) { toast('Could not make the GLB: ' + e.message); }
  }

  // ---- the parts drawer (large view and full screen) -----------------------------------
  function buildDrawer() {
    const d = $('#viewer-drawer');
    if (!d) return;
    d.innerHTML = `<div class="vd-head row between"><span class="b5">Model</span><button class="icon-btn sm" data-act="v-parts" title="Close" aria-label="Close the parts list">${MG.icon('x')}</button></div>
      <div id="vd-stats"></div>
      <div class="vd-head row between"><span class="b5">Parts <span class="mu" id="vd-count"></span></span><button class="link xs" data-act="v-showall" id="vd-showall">Show all</button></div>
      <input class="vd-filter" id="vd-filter" placeholder="Filter parts" aria-label="Filter parts" value="${esc(vs.filter)}">
      <div class="vd-list" id="vd-list"></div>`;
    $('#vd-filter').addEventListener('input', ev => { vs.filter = ev.target.value; renderPartList(); });
    renderDrawerBody();
  }
  function renderDrawerBody() {
    if (!$('#vd-list')) return;
    $('#vd-stats').innerHTML = vs.stats ? statsHtml(vs.stats) : '<div class="xs mu">No 3D model is loaded.</div>';
    $('#vd-count').textContent = vs.parts.length || '';
    $('#vd-showall').hidden = !vs.parts.some(p => !p.visible);
    renderPartList();
  }
  function renderPartList() {
    const list = $('#vd-list');
    if (!list) return;
    const q = vs.filter.trim().toLowerCase();
    const rows = vs.parts.filter(p => !q || p.name.toLowerCase().includes(q));
    list.innerHTML = rows.length ? rows.map(p => {
      const iso = vs.isolated === p.id, on = vs.sel && vs.sel.id === p.id;
      return `<div class="vd-row${on ? ' on' : ''}${p.visible ? '' : ' off'}">
        <button class="icon-btn sm" data-act="v-eye" data-part="${p.id}" title="${p.visible ? 'Hide' : 'Show'} this part" aria-label="${p.visible ? 'Hide' : 'Show'} ${esc(p.name)}">${MG.icon(p.visible ? 'eye' : 'eye-off')}</button>
        <button class="sw-dot vd-dot" data-act="v-paint-part" data-vpop="paint" data-part="${p.id}" style="--sw:${swatchBg(p.colour)}" title="Colour ${esc(p.name)}" aria-label="Colour ${esc(p.name)}"></button>
        <button class="vd-name grow ell" data-act="v-pick" data-part="${p.id}" title="${esc(p.name)}">${esc(p.name)}</button>
        <span class="xs mu">${num(p.triangles)}</span>
        <button class="icon-btn sm${iso ? ' on' : ''}" data-act="v-iso" data-part="${p.id}" title="${iso ? 'Show all parts' : 'Show only this part'}" aria-label="${iso ? 'Show all parts' : 'Show only ' + esc(p.name)}">${MG.icon('target')}</button></div>`;
    }).join('') : `<div class="xs mu vd-none">${vs.parts.length ? 'No part matches.' : 'This model has no separate parts.'}</div>`;
    const sel = $('.vd-row.on', list);
    if (sel) sel.scrollIntoView({ block: 'nearest' });
  }

  // ---- large view, full screen, sizes ---------------------------------------------------
  function setBig(on) {
    on = !!on && !!app.data.model;
    const v = $('#viewer');
    if (!v || on === vs.big) return;
    if (isFull()) document.exitFullscreen();
    vs.big = on;
    if (on) { $('#ws').appendChild(v); v.classList.remove('mini'); }
    v.classList.toggle('large', on);
    if (!on) placeViewer();
    if (!on) vs.drawer = false;
    closePop();
    renderViewerUi();
  }
  app.setBigViewer = setBig;
  function toggleFull() {
    const v = $('#viewer');
    if (isFull()) document.exitFullscreen();
    else if (v && v.requestFullscreen) v.requestFullscreen().catch(e => toast('Full screen is not available: ' + e.message));
  }
  function toggleMeasure(on) {
    vs.measure = app.viewer.setMeasure(on == null ? !vs.measure : on);
    vs.reading = null;
    renderViewerUi();
  }
  function screenshot() {
    const m = app.data.model, sel = selectedVersion();
    const name = ((m && m.name) || 'model').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + (sel ? '-' + sel.id : '') + '.png';
    const a = document.createElement('a');
    a.href = app.viewer.screenshot();
    a.download = name;
    a.click();
    toast('Saved ' + name);
  }

  function bindViewerUi() {
    const v = $('#viewer');
    v.addEventListener('input', ev => { const k = ev.target.dataset && ev.target.dataset.vin; if (k && ev.target.type === 'range') applyInput(k, ev.target); });
    v.addEventListener('change', ev => { const k = ev.target.dataset && ev.target.dataset.vin; if (k && ev.target.type === 'checkbox') applyInput(k, ev.target); });
    v.addEventListener('change', ev => { if (ev.target.matches('[data-vlive]')) { vs.livePin = ev.target.value || null; renderWorkspace(); } });
    // the colour picker and the hex box colour as they change, without redrawing the popover under the picker
    v.addEventListener('input', ev => {
      const k = ev.target.dataset && ev.target.dataset.vpaint;
      if (!k || !app.viewer) return;
      let hex = ev.target.value.trim().toLowerCase();
      if (hex && hex[0] !== '#') hex = '#' + hex;
      if (!/^#[0-9a-f]{6}$/.test(hex)) return;
      vs.painting = true;
      try { app.viewer.setColour(hex); } finally { vs.painting = false; }
      const other = $(k === 'picker' ? '#vp-hex' : '#vp-picker');
      if (other) other.value = hex;
    });
    v.addEventListener('change', ev => { if (ev.target.dataset && ev.target.dataset.vpaint && vs.pop === 'paint') renderPop(); });
  }

  // Clicks on the viewer's tools; true when the action was one of them.
  function viewerAct(a, b) {
    if (!/^v-/.test(a)) return false;
    if (a === 'v-big') { setBig(!vs.big); return true; }
    if (a === 'v-dl-paint' && !has3d()) { toast('The 3D view has no model to save yet'); return true; }
    if (a === 'v-full') { toggleFull(); return true; }
    if (a === 'v-pop') { openPop(b.dataset.vpop, b); return true; }
    if (a === 'v-pop-close') { closePop(); renderViewerUi(); return true; }
    if (a === 'v-live') {
      vs.live = !vs.live; vs.livePin = null; savePref('live', vs.live);
      toast(vs.live ? 'Live is on: each build step shows in 3D' : 'Live is off');
      renderWorkspace();
      return true;
    }
    if (!has3d()) { toast('The 3D view appears once a version is built'); return true; }
    const vw = app.viewer, part = b.dataset.part != null ? +b.dataset.part : null;
    if (a === 'v-view') { vw.setView(b.dataset.view, true); closePop(); }
    else if (a === 'v-fit') { vw.fit(true); closePop(); }
    else if (a === 'v-reset') { vw.reset(true); closePop(); }
    else if (a === 'v-set') applySetting(b.dataset.key, b.dataset.val);
    else if (a === 'v-section-on') { Object.assign(vs.section, vw.setSection({ axis: vs.section.axis, pos: vs.section.pos, flip: vs.section.flip })); renderPop(); renderViewerUi(); }
    else if (a === 'v-section-off') { Object.assign(vs.section, vw.setSection(null)); renderPop(); renderViewerUi(); }
    else if (a === 'v-dims') { vs.dims = vw.setDimensions(!vs.dims); renderViewerUi(); }
    else if (a === 'v-measure') toggleMeasure();
    else if (a === 'v-orbit') { vs.spin = vw.setAutoRotate(!vs.spin); renderViewerUi(); }
    else if (a === 'v-play') { vs.play = vw.setPlaying(!vs.play); renderViewerUi(); }
    else if (a === 'v-shot') screenshot();
    else if (a === 'v-parts') {
      if (!vs.big && !isFull()) { setBig(true); vs.drawer = true; }
      else vs.drawer = !vs.drawer;
      if (drawerShown()) buildDrawer();
      renderViewerUi();
    }
    else if (a === 'v-pick') vw.select(vs.sel && vs.sel.id === part ? null : part);
    else if (a === 'v-unselect') vw.select(null);
    else if (a === 'v-eye') { const p = vs.parts[part]; if (p) vw.setPartVisible(part, !p.visible); }
    else if (a === 'v-iso') { vw.isolate(part); vw.fit(true); }
    else if (a === 'v-showall') { vw.showAll(); vw.fit(true); }
    else if (a === 'v-paint') vw.setColour(b.dataset.hex);
    else if (a === 'v-paint-type') vw.selectType(b.dataset.type);
    else if (a === 'v-paint-reset') vw.resetColour();
    else if (a === 'v-paint-reset-all') vw.resetAllColours();
    else if (a === 'v-paint-part') { vw.select(part); if (vs.pop !== 'paint') openPop('paint', b); else renderPop(); }
    else if (a === 'v-dl-paint') { closeMenus(); downloadColoured(); }
    return true;
  }

  // ---- lightbox for renders and attached images ---------------------------------------
  function lightbox(src) {
    const lb = $('#lightbox');
    lb.innerHTML = `<button class="icon-btn lb-x" data-act="lb-close" aria-label="Close">${MG.icon('x')}</button><img src="${esc(src)}" alt="">`;
    lb.classList.add('on');
  }

  // ---- other screens ------------------------------------------------------------------
  function modelCard(m, w, cls) {
    return `<button class="mcard lift${cls || ''}" data-go="studio" data-project="${esc(m.project || '')}"><div class="mthumb">${MG.pic(m, w)}</div><div class="mbody"><div class="b5 ell">${esc(m.name)}</div><div class="xs mu"><span class="sq s-${esc(m.status)}"></span>${esc(m.meta)}</div></div></button>`;
  }
  function renderModels() {
    $('#models-grid').innerHTML = `<button class="mcard new lift" data-go="new">${MG.icon('plus')}<span class="b5">New model</span><span class="xs mu">Describe it and the team starts</span></button>` +
      app.data.models.map(m => modelCard(m, 170)).join('');
  }
  function renderParts() {
    $('#parts-grid').innerHTML = app.data.parts.map(p => `<div class="pcard lift" title="${esc(p.description || '')}"><div class="mthumb">${MG.pic(p, 130)}</div><div class="mbody"><div class="b5 ell"><code>${esc(p.name)}</code></div><div class="xs mu">${p.kind === 'material' ? 'Material · ' : ''}From ${esc(p.from)} · used ${p.uses || 0}×</div><div class="xs mu">Saved by Historia · ${esc(p.saved)}</div></div></div>`).join('') || '<div class="empty">Historia saves reusable parts here.</div>';
  }
  async function renderActivityScreen() {
    const who = $('#f-agent').value;
    const list = $('#activity-list');
    if (app.handlers.loadActivity) {
      if (!list.innerHTML) list.innerHTML = '<div class="empty">Loading…</div>';
      try {
        const rows = await app.handlers.loadActivity(who);
        if (app.screen === 'activity' && $('#f-agent').value === who) list.innerHTML = rows.slice(0, 150).map(eventRow).join('') || '<div class="empty">Nothing yet.</div>';
      } catch (e) { list.innerHTML = `<div class="empty">Could not load the activity: ${esc(e.message)}</div>`; }
      return;
    }
    const rows = allEvents().concat(app.data.activity).filter(e => !who || e.cast === who);
    list.innerHTML = rows.slice(0, 80).map(eventRow).join('') || '<div class="empty">Nothing yet.</div>';
  }
  function renderNew() {
    $('#recent').innerHTML = app.data.models.slice(0, 3).map(m => modelCard(m, 150)).join('') || '<div class="empty">Your models show here.</div>';
    $('#team').innerHTML = Object.values(MG.cast).map(c => `<div class="tm">${MG.portrait(c.id, 48)}<div><div class="b5">${esc(c.name)}</div><div class="xs mu">${esc(MG.cap(c.role))}</div></div></div>`).join('');
    renderNewOptions();
  }
  function renderNewOptions() {
    const n = app.newModel, lim = app.data.limits || {};
    const size = n.size ? ['width', 'depth', 'height'].filter(k => n.size[k]).map(k => k[0].toUpperCase() + ' ' + n.size[k]).join(' × ') + ' ' + (n.size.unit || 'cm') : '';
    $('[data-act="new-size"]').innerHTML = MG.icon('ruler') + (size ? 'Size ' + esc(size) : 'Size');
    const budget = n.budget != null ? n.budget : lim.per_model_usd;
    $('[data-act="new-budget"]').innerHTML = MG.icon('coin') + (budget != null ? 'Budget $' + Number(budget).toFixed(2) : 'Budget');
    $('#new-refs').innerHTML = n.attachments.map((a, i) => `<span class="achip"><img src="${esc(a.url)}" alt=""><button class="icon-btn sm" data-act="new-unref" data-i="${i}" aria-label="Remove this image">${MG.icon('x')}</button></span>`).join('');
  }
  function newPopover(kind) {
    const pop = $('#new-pop'), n = app.newModel, lim = app.data.limits || {};
    if (pop.dataset.kind === kind && pop.classList.contains('on')) { pop.classList.remove('on'); return; }
    pop.dataset.kind = kind;
    if (kind === 'size') {
      const s = n.size || {};
      pop.innerHTML = `<div class="lbl">Overall size</div><div class="row">${['width', 'depth', 'height'].map(k => `<label class="mini"><span class="xs mu">${MG.cap(k)}</span><input type="number" min="0" step="0.1" id="size-${k}" value="${esc(s[k] || '')}"></label>`).join('')}<label class="mini"><span class="xs mu">Unit</span><select id="size-unit">${['mm', 'cm', 'm'].map(u => `<option${(s.unit || 'cm') === u ? ' selected' : ''}>${u}</option>`).join('')}</select></label></div>
        <div class="row between"><button class="link xs" data-act="size-clear">Leave it to the team</button><button class="btn sm primary" data-act="size-ok">Set size</button></div>`;
    } else {
      pop.innerHTML = `<div class="lbl">Most this model may spend</div><label class="row"><span class="mu">$</span><input type="number" min="0.5" step="0.5" id="budget-in" value="${esc(n.budget != null ? n.budget : (lim.per_model_usd || ''))}"></label><p class="xs mu">Before going over it, the run waits and Hange asks you. The default is in Settings.</p>
        <div class="row between"><button class="link xs" data-act="budget-clear">Use the default</button><button class="btn sm primary" data-act="budget-ok">Set budget</button></div>`;
    }
    pop.classList.add('on');
    const first = pop.querySelector('input'); if (first) first.focus();
  }
  function renderSettings() {
    const s = app.data.settings || {};
    if (s.daily_usd != null) $('#set-daily').value = Number(s.daily_usd).toFixed(2);
    if (s.per_model_usd != null) $('#set-model').value = Number(s.per_model_usd).toFixed(2);
    if (document.activeElement !== $('#set-blender-path')) $('#set-blender-path').value = s.blender_path || '';
    $('#set-blender-path').placeholder = s.blender_in_use ? 'Found automatically: ' + s.blender_in_use : 'Found automatically';
    const ok = s.blender_found;
    $('#set-blender').innerHTML = s.blender_found == null ? 'Not checked yet' : ok ? `Blender ${esc(s.blender_version || '')} found` : `Not found. Install it with <code>${esc(s.install || '')}</code>`;
    $('#set-blender-sq').className = 'sq ' + (s.blender_found == null ? 's-idle' : ok ? 's-done' : 's-failed');
    const editable = !!app.handlers.saveSettings;
    $$('#set-daily, #set-model, #set-blender-path').forEach(i => { i.disabled = !editable; });
  }
  // ---- updates ------------------------------------------------------------------------
  function setUpdate(u) { app.update = u || null; renderUpdate(); }
  function renderUpdate() {
    const u = app.update;
    const ready = !!(u && u.installed && u.available);
    const busy = !!(u && (u.state === 'downloading' || u.state === 'installing'));
    setHtml($('#update'), ready ? `<button class="update-chip" data-act="update-now" title="Install ModelGen ${esc(u.latest)}"${busy ? ' disabled' : ''}>${MG.icon('download')}${busy ? 'Updating…' : 'Update to v' + esc(u.latest)}</button>` : '');
    const line = $('#set-update'), sq = $('#set-update-sq');
    if (!line) return;
    let text = 'Not checked yet', cls = 's-idle';
    if (u) {
      const here = `ModelGen ${esc(u.version)}`;
      if (busy) { text = `${here} · ${u.state === 'downloading' ? 'downloading' : 'installing'} ${esc(u.latest)}…`; cls = 's-working'; }
      else if (u.error) { text = `${here} · ${esc(u.error)}`; cls = 's-failed'; }
      else if (u.available) { text = `${here} · version ${esc(u.latest)} is ready`; cls = 's-waiting'; }
      else if (u.checked_at) { text = `${here} · up to date`; cls = 's-done'; }
      else text = here;
      if (!u.installed) text += ' · running from the source folder, so it is not updated automatically';
    }
    line.innerHTML = text;
    sq.className = 'sq ' + cls;
    $('#set-update-now').hidden = !ready || busy;
    $('#set-updates').checked = !u || u.enabled !== false;
  }
  async function checkUpdate() {
    if (!app.handlers.updateCheck) return;
    try { setUpdate(await app.handlers.updateCheck()); toast(app.update.available ? `Version ${app.update.latest} is ready` : app.update.error ? 'Could not check: ' + app.update.error : 'ModelGen is up to date'); }
    catch (e) { toast('Could not check: ' + e.message); }
  }
  async function installUpdate() {
    if (!app.handlers.updateInstall || !app.update) return;
    if (!app.update.installed) { toast('This copy runs from its source folder, so it is not updated automatically.'); return; }
    try {
      let r = await app.handlers.updateInstall(false);
      if (r.action === 'confirm') {
        if (!window.confirm(`A build is running (${r.running.join(', ')}). Stop it and update now? What the team built so far is kept.`)) return;
        r = await app.handlers.updateInstall(true);
      }
      toast(`Updating to ${r.version}. ModelGen closes and starts again in a minute or two; this page reconnects by itself.`);
    } catch (e) { toast('Not updated: ' + e.message); }
  }
  app.setUpdate = setUpdate;

  async function saveSettings(change) {
    if (!app.handlers.saveSettings) return;
    try {
      const s = await app.handlers.saveSettings(change);
      app.data.settings = s;
      app.data.limits = { daily_usd: s.daily_usd, per_model_usd: s.per_model_usd };
      if (app.data.budget) { app.data.budget = app.data.budget.replace(/of \$[\d.]+/, 'of $' + Number(s.daily_usd).toFixed(2)); $('#st-budget').textContent = app.data.budget; }
      renderSettings();
      toast(s.warning || 'Saved');
    } catch (e) { toast('Not saved: ' + e.message); renderSettings(); }
  }

  // ---- command menu -----------------------------------------------------------------
  function baseCommands() {
    const list = [
      { group: 'Actions', icon: 'check', label: 'Accept the selected version', kbd: 'A', run: () => clickAct('accept') },
      { group: 'Actions', icon: 'message', label: 'Ask for a change', kbd: 'C', run: () => clickAct('change') },
      { group: 'Actions', icon: 'x', label: 'Stop the build', run: () => stopBuild() },
      { group: 'Actions', icon: 'plus', label: 'New model', kbd: 'N', run: () => show('new') },
      { group: 'Actions', icon: 'download', label: 'Download all files of the selected version', run: () => exportSelected() },
      ...TABS.map((t, i) => ({ group: 'Stages', icon: 'chevron-right', label: 'Open ' + TAB_NAMES[t], kbd: 'Alt ' + (i + 1), run: () => { show('studio'); setTab(t); } })),
      { group: 'Go to', icon: 'studio', label: 'Studio', run: () => show('studio') },
      { group: 'Go to', icon: 'stack', label: 'Models', run: () => show('models') },
      { group: 'Go to', icon: 'parts', label: 'Parts library', run: () => show('parts') },
      { group: 'Go to', icon: 'history', label: 'Activity', run: () => show('activity') },
      { group: 'Go to', icon: 'settings', label: 'Settings', run: () => show('settings') },
      { group: 'View', icon: 'sidebar', label: 'Expand or collapse the navigation', kbd: 'Ctrl B', run: () => toggleNav() },
      { group: 'View', icon: 'sidebar', label: 'Show or hide the chat', kbd: 'Ctrl \\', run: () => toggleChat() },
      { group: 'View', icon: 'maximize', label: 'Fit the studio floor', kbd: 'F', run: () => { show('studio'); setTab('build'); requestAnimationFrame(() => app.studio.fit()); } },
      { group: 'View', icon: 'tag', label: 'Toggle labels', kbd: 'L', run: () => toggleLabels() },
      { group: 'View', icon: 'walk', label: 'Toggle studio life', run: () => toggleLife() },
      { group: 'View', icon: 'moon', label: 'Toggle dark mode', run: () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark') }
    ];
    app.data.models.slice(0, 8).forEach(m => { if (m.project) list.push({ group: 'Models', icon: 'stack', label: 'Open ' + m.name, hint: m.meta, run: () => selectProject(m.project) }); });
    if (app.studio) app.studio.agents().filter(w => w.role !== 'director').forEach(w => list.push({ group: 'Agents', portrait: w.castId, label: 'Open ' + MG.displayName(app.studio, w), hint: MG.stateWord(w.state) + (w.data.doing ? ' · ' + w.data.doing : ''), run: () => openAgent(w.id) }));
    return list.concat(app.commands);
  }
  app.addCommands = list => { app.commands = app.commands.concat(list); };

  let cmdSel = 0, cmdItems = [];
  function openCmd() {
    $('#cmdk').classList.add('on');
    const i = $('#cmd-input'); i.value = ''; filterCmd(); i.focus();
  }
  function closeCmd() { $('#cmdk').classList.remove('on'); }
  function filterCmd() {
    const q = $('#cmd-input').value.trim().toLowerCase();
    cmdItems = baseCommands().filter(c => !q || (c.label + ' ' + c.group + ' ' + (c.hint || '')).toLowerCase().includes(q));
    cmdSel = Math.min(cmdSel, Math.max(0, cmdItems.length - 1));
    let g = '', html = '';
    cmdItems.forEach((c, i) => {
      if (c.group !== g) { g = c.group; html += `<div class="cmd-g">${esc(g)}</div>`; }
      html += `<button class="cmd-i${i === cmdSel ? ' on' : ''}" data-i="${i}">${c.portrait ? MG.portrait(c.portrait, 22) : MG.icon(c.icon || 'chevron-right', 'mu')}<span class="grow">${esc(c.label)}${c.hint ? `<span class="xs mu"> · ${esc(c.hint)}</span>` : ''}</span>${c.kbd ? `<kbd>${esc(c.kbd)}</kbd>` : ''}</button>`;
    });
    $('#cmd-list').innerHTML = html || '<div class="empty">No matching command.</div>';
    const on = $('#cmd-list .on'); if (on) on.scrollIntoView({ block: 'nearest' });
  }
  function runCmd(i) { const c = cmdItems[i]; closeCmd(); if (c) c.run(); }

  function clickAct(act) { const b = $(`#decide [data-act="${act}"]`); if (b && !b.disabled) b.click(); }
  function toggleLabels() { const on = !app.studio.labels; app.studio.setLabels(on); $('[data-act="labels"]').classList.toggle('on', on); savePref('labels', on); const s = $('#set-labels'); if (s) s.checked = on; }
  function toggleLife(force) { const on = force == null ? !app.studio.life : force; app.studio.setLife(on); $('[data-act="life"]').classList.toggle('on', on); $('[data-act="life"]').innerHTML = MG.icon(on ? 'pause' : 'play'); $('[data-act="life"]').title = on ? 'Pause studio life' : 'Resume studio life'; savePref('life', on); const s = $('#set-life'); if (s) s.checked = on; }
  function savePref(k, v) { try { localStorage.setItem('mg-' + k, JSON.stringify(v)); } catch (e) { /* storage blocked */ } }
  function loadPref(k, d) { try { const v = localStorage.getItem('mg-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }

  function closeMenus(except) { $$('.menu.on').forEach(mn => { if (mn !== except) { mn.classList.remove('on'); const b = mn.parentElement.querySelector('[aria-expanded]'); if (b) b.setAttribute('aria-expanded', 'false'); } }); }
  function toggleMenu(btn, id) {
    const mn = document.getElementById(id);
    if (!mn) return;
    closeMenus(mn);
    const on = !mn.classList.contains('on');
    mn.classList.toggle('on', on);
    btn.setAttribute('aria-expanded', String(on));
  }

  function selectProject(project, version) {
    show('studio');
    if (app.handlers.selectProject && project) app.handlers.selectProject(project, version);
    else if (version && app.data.model) { app.data.model.selected = version; renderWorkspace(); renderCrumb(); }
  }
  app.selectProject = selectProject;

  function pickVersion(id) {
    if (!app.data.model) return;
    app.data.model.selected = id;
    app.data.model.userPicked = true;
    closeMenus();
    renderWorkspace(); renderCrumb();
  }

  function exportSelected() {
    const sel = selectedVersion();
    if (sel && sel.export) { window.location.href = sel.export; return; }
    toast(sel ? 'Nothing to export for ' + sel.id + ' yet' : 'No model to export yet');
  }

  async function accept() {
    const m = app.data.model, sel = selectedVersion();
    if (!m || !sel) return;
    if (!app.handlers.accept) {   // the demo: mark it here, so Export opens as it does with the engine
      m.versions.forEach(v => { v.accepted = v.id === sel.id; });
      m.accepted = sel.id;
      toast('Accepted ' + sel.id + '. Its files are in Export.');
      renderWorkspace();
      return;
    }
    try { await app.handlers.accept(m.project, sel.number); toast('Accepted ' + sel.id + '. Its files are in Export.'); }
    catch (e) { toast('Could not accept: ' + e.message); }
  }

  async function stopBuild() {
    const m = app.data.model;
    if (!m || !m.running || !app.handlers.stop) { toast('Nothing is being built right now'); return; }
    if (!window.confirm(`Stop the build of ${m.name}? What the team built so far is kept.`)) return;
    try { await app.handlers.stop(m.project); toast('Stopped the build of ' + m.name); }
    catch (e) { toast('Could not stop: ' + e.message); }
  }

  async function retryBuild() {
    const m = app.data.model;
    if (!m || m.running || !app.handlers.retry) return;
    try { await app.handlers.retry(m.project); toast('Running the build of ' + m.name + ' again'); }
    catch (e) { toast('Could not try again: ' + e.message); }
  }

  async function deleteModel() {
    const m = app.data.model;
    if (!m || m.running || !app.handlers.deleteModel) return;
    if (!window.confirm(`Delete ${m.name} and all its versions? This cannot be undone.`)) return;
    try { await app.handlers.deleteModel(m.project); toast('Deleted ' + m.name); }
    catch (e) { toast('Could not delete: ' + e.message); }
  }

  async function requestChange() {
    const m = app.data.model, sel = selectedVersion();
    if (m && sel && app.handlers.revert && !sel.base && sel.state === 'scored' && !m.running) {
      try { await app.handlers.revert(m.project, sel.number); toast('Your change will start from ' + sel.id); }
      catch (e) { toast('Could not start from ' + sel.id + ': ' + e.message); }
    }
    app.compose('Change: ');
  }

  // ---- the one entry point -----------------------------------------------------------
  window.applyState = function (snapshot) {
    MG.state = snapshot || {};
    app.studio.update(MG.state);
    app.panel.refresh();
    renderAsks(); renderRun(); renderActivity(); renderStatus(); renderChatRun();
    autoTab(); renderTabs(); renderBuildSide(); renderDecide(); placeViewer();
    transitions();
  };

  app.loadData = function (d) {
    d = d || {};
    const chatChanged = 'chat' in d;
    if (chatChanged) app.chatLoaded = true;
    Object.assign(app.data, d);
    const key = modelKey(app.data.model), moved = key !== app.chatFor;   // another model: the chat shows its messages
    app.chatFor = key;
    if (chatChanged || moved) renderChat();
    renderWorkspace(); renderCrumb(); renderRun();
    $('#spend').textContent = app.data.spend || '';
    $('#st-budget').textContent = app.data.budget || '';
    $('#st-blender').textContent = app.data.blender || '';
    if (app.screen !== 'studio' && app.screen !== 'activity') show(app.screen);
  };

  // ---- wiring -------------------------------------------------------------------------
  function bind() {
    document.addEventListener('click', ev => {
      if (!ev.target.closest('.menu-host') && !ev.target.closest('#new-pop') && !ev.target.closest('[data-act="new-size"],[data-act="new-budget"]')) { closeMenus(); $('#new-pop').classList.remove('on'); }
      if (vs.pop && !ev.target.closest('#vpop, [data-vpop]') && !(vs.pop === 'paint' && ev.target.closest('#viewer-mount, #viewer-sel, #viewer-drawer'))) closePop();
      const go = ev.target.closest('[data-go]');
      if (go) {
        if (go.dataset.project) selectProject(go.dataset.project);
        else show(go.dataset.go);
        return;
      }
      const scr = ev.target.closest('.rail [data-screen]');
      if (scr) { show(scr.dataset.screen); return; }
      const b = ev.target.closest('[data-act]');
      if (!b || b.closest('.apanel')) return;
      const a = b.dataset.act;
      if (a === 'theme') setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
      else if (a === 'cmd') openCmd();
      else if (a === 'nav') toggleNav();
      else if (a === 'collapse-chat') toggleChat(false);
      else if (a === 'open-chat') toggleChat(true);
      else if (a === 'tab') setTab(b.dataset.tab);
      else if (a === 'open-run') {
        const other = b.dataset.project && (!app.data.model || app.data.model.project !== b.dataset.project);
        if (other) { app.tabs[b.dataset.project] = 'build'; selectProject(b.dataset.project); }
        else { show('studio'); setTab('build'); }
      }
      else if (a === 'chat-scope') { app.chatScope = b.dataset.scope; savePref('chat-scope', app.chatScope); renderChatScope(); renderChat(); }
      else if (a === 'send') sendChat();
      else if (a === 'zoom-in') app.studio.zoomBy(1.25);
      else if (a === 'zoom-out') app.studio.zoomBy(.8);
      else if (a === 'fit') app.studio.fit();
      else if (a === 'labels') toggleLabels();
      else if (a === 'life') toggleLife();
      else if (a === 'open-agent') openAgent(b.dataset.agent);
      else if (a === 'answer') document.dispatchEvent(new CustomEvent('studio:answer', { detail: { agent: b.dataset.agent, choice: b.dataset.choice } }));
      else if (a === 'reply-pinned') openAgent(b.dataset.agent);
      else if (a === 'goto-ask') gotoAsk(b.dataset.agent);
      else if (a === 'accept') accept();
      else if (a === 'change') requestChange();
      else if (a === 'stop-build') stopBuild();
      else if (a === 'update-now') installUpdate();
      else if (a === 'update-check') checkUpdate();
      else if (a === 'retry-build') retryBuild();
      else if (a === 'delete-model') deleteModel();
      else if (a === 'export') exportSelected();
      else if (a === 'dl-menu') toggleMenu(b, 'dl-menu');
      else if (a === 'ver-menu') toggleMenu(b, 'ver-menu');
      else if (a === 'pick-version') pickVersion(b.dataset.v);
      else if (a === 'delete-version') {
        const m = app.data.model, v = m && m.versions.find(x => x.id === b.dataset.v);
        if (v && app.handlers.deleteVersion && window.confirm(`Delete ${v.id} of ${m.name}? Its model, renders, exports and review are removed for good.`))
          app.handlers.deleteVersion(m.project, v.number).then(() => toast(`Deleted ${v.id}`)).catch(e => toast('Could not delete: ' + e.message));
      }
      else if (a === 'base-version') { const m = app.data.model, v = m && m.versions.find(x => x.id === b.dataset.v); if (v && app.handlers.revert) app.handlers.revert(m.project, v.number).then(() => toast('Your next change starts from ' + v.id)).catch(e => toast('Could not: ' + e.message)); }
      else if (a === 'open-version') selectProject(b.dataset.project, b.dataset.v);
      else if (a === 'lightbox') lightbox(b.dataset.src);
      else if (a === 'lb-close') $('#lightbox').classList.remove('on');
      else if (viewerAct(a, b)) { /* handled by the 3D view tools */ }
      else if (a === 'attach-image') $('#file-chat').click();
      else if (a === 'unattach') { app.pending.splice(+b.dataset.i, 1); renderAttachChips(); }
      else if (a === 'new-ref') $('#file-new').click();
      else if (a === 'new-unref') { app.newModel.attachments.splice(+b.dataset.i, 1); renderNewOptions(); }
      else if (a === 'new-size') newPopover('size');
      else if (a === 'new-budget') newPopover('budget');
      else if (a === 'size-ok') {
        const s = { unit: $('#size-unit').value };
        ['width', 'depth', 'height'].forEach(k => { const v = parseFloat($('#size-' + k).value); if (v > 0) s[k] = v; });
        app.newModel.size = (s.width || s.depth || s.height) ? s : null; $('#new-pop').classList.remove('on'); renderNewOptions();
      }
      else if (a === 'size-clear') { app.newModel.size = null; $('#new-pop').classList.remove('on'); renderNewOptions(); }
      else if (a === 'budget-ok') { const v = parseFloat($('#budget-in').value); app.newModel.budget = v > 0 ? v : null; $('#new-pop').classList.remove('on'); renderNewOptions(); }
      else if (a === 'budget-clear') { app.newModel.budget = null; $('#new-pop').classList.remove('on'); renderNewOptions(); }
      else if (a === 'start') startModel();
      else if (a === 'example') { $('#prompt').value = b.textContent; $('#prompt').focus(); }
    });
    // Studio events bubble from the map; answers and chats can come from the panel too.
    document.addEventListener('studio:select', ev => app.panel.open(ev.detail.agent));
    document.addEventListener('studio:deselect', () => app.panel.close());
    document.addEventListener('studio:director', ev => {
      const prefill = ev.detail && ev.detail.prefill;
      if (prefill) { toggleChat(true); const ta = $('#composer'); ta.value = prefill; ta.focus(); }
      else toggleChat();
    });
    document.addEventListener('studio:zoom', ev => { $('#zoom-pct').textContent = ev.detail.pct + '%'; });
    document.addEventListener('studio:handoff', ev => {
      const f = app.studio.find(ev.detail.from), t = app.studio.find(ev.detail.to);
      if (!f || !t) return;
      app.feed.push({ at: new Date().toISOString(), cast: f.castId, kind: 'handoff', text: `${MG.displayName(app.studio, f)} → ${MG.displayName(app.studio, t)}: ${ev.detail.label}` });
      app.feed = app.feed.slice(-40);
      renderActivity();
    });
    document.addEventListener('studio:answer', ev => { if (app.handlers.answer) app.handlers.answer(ev.detail); });
    document.addEventListener('studio:chat', ev => { if (ev.detail.agent !== 'director' && app.handlers.chat) app.handlers.chat(ev.detail); });

    const ta = $('#composer');
    ta.addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendChat(); } });
    ta.addEventListener('paste', ev => { const files = [...(ev.clipboardData || {}).files || []].filter(f => /^image\//.test(f.type)); if (files.length) { ev.preventDefault(); attachTo(app.pending, files, renderAttachChips); } });
    $('#file-chat').addEventListener('change', ev => { attachTo(app.pending, ev.target.files, renderAttachChips); ev.target.value = ''; });
    $('#file-new').addEventListener('change', ev => { attachTo(app.newModel.attachments, ev.target.files, renderNewOptions); ev.target.value = ''; });
    $('#cmd-input').addEventListener('input', () => { cmdSel = 0; filterCmd(); });
    $('#cmd-input').addEventListener('keydown', ev => {
      if (ev.key === 'ArrowDown') { cmdSel = Math.min(cmdItems.length - 1, cmdSel + 1); filterCmd(); ev.preventDefault(); }
      else if (ev.key === 'ArrowUp') { cmdSel = Math.max(0, cmdSel - 1); filterCmd(); ev.preventDefault(); }
      else if (ev.key === 'Enter') { runCmd(cmdSel); ev.preventDefault(); }
    });
    $('#cmd-list').addEventListener('click', ev => { const i = ev.target.closest('.cmd-i'); if (i) runCmd(+i.dataset.i); });
    $('#cmdk').addEventListener('click', ev => { if (ev.target.id === 'cmdk') closeCmd(); });
    $('#lightbox').addEventListener('click', ev => { if (ev.target.id === 'lightbox') $('#lightbox').classList.remove('on'); });
    $('#f-agent').addEventListener('change', renderActivityScreen);
    $$('input[name="theme"]').forEach(r => r.addEventListener('change', () => setTheme(r.value)));
    $('#set-labels').addEventListener('change', ev => { if (ev.target.checked !== app.studio.labels) toggleLabels(); });
    $('#set-life').addEventListener('change', ev => toggleLife(ev.target.checked));
    $('#set-daily').addEventListener('change', ev => saveSettings({ daily_usd: ev.target.value }));
    $('#set-model').addEventListener('change', ev => saveSettings({ per_model_usd: ev.target.value }));
    $('#set-blender-path').addEventListener('change', ev => saveSettings({ blender_path: ev.target.value }));
    $('#set-updates').addEventListener('change', ev => saveSettings({ auto_update: ev.target.checked }).then(() => { if (app.update) { app.update.enabled = ev.target.checked; renderUpdate(); } }));
    $('#prompt').addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.ctrlKey) startModel(); });

    document.addEventListener('keydown', ev => {
      const typing = ev.target.matches('input, textarea, select, [contenteditable]');
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') { ev.preventDefault(); $('#cmdk').classList.contains('on') ? closeCmd() : openCmd(); return; }
      if ((ev.ctrlKey || ev.metaKey) && ev.key === '\\') { ev.preventDefault(); toggleChat(); return; }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'b') { ev.preventDefault(); toggleNav(); return; }
      if (ev.altKey && !ev.ctrlKey && !ev.metaKey && /^[1-4]$/.test(ev.key) && app.screen === 'studio' && app.data.model) { ev.preventDefault(); setTab(TABS[+ev.key - 1]); return; }
      if (ev.key === 'Escape') {
        if ($('#lightbox').classList.contains('on')) $('#lightbox').classList.remove('on');
        else if ($('#cmdk').classList.contains('on')) closeCmd();
        else if ($('.menu.on') || $('#new-pop.on')) { closeMenus(); $('#new-pop').classList.remove('on'); }
        else if (vs.pop) closePop();
        else if (vs.measure && app.viewer) toggleMeasure(false);
        else if (vs.big) setBig(false);
        else if (app.panel.isOpen()) app.studio.select(null);
        else if (typing) ev.target.blur();
        return;
      }
      if (typing || ev.ctrlKey || ev.metaKey || ev.altKey || app.screen !== 'studio') return;
      const k = ev.key.toLowerCase();
      if (k === 'f' && app.tab === 'build') app.studio.fit();
      else if (k === 'l' && app.tab === 'build') toggleLabels();
      else if (k === 'a') clickAct('accept');
      else if (k === 'c') clickAct('change');
      else if (k === 'n') show('new');
      else if (k === 'v') setBig(!vs.big);
      else if (/^[1-6]$/.test(k) && has3d()) app.viewer.setView(['front', 'back', 'left', 'right', 'top', 'iso'][+k - 1], true);
    });
    document.addEventListener('fullscreenchange', () => { if (!isFull()) vs.drawer = vs.drawer && vs.big; renderViewerUi(); });
  }

  function startModel() {
    const p = $('#prompt'), text = p.value.trim();
    if (!text) { $('#prompt-err').textContent = 'Describe the object first.'; p.focus(); return; }
    $('#prompt-err').textContent = '';
    const n = app.newModel;
    const options = {};
    if (n.size) options.size = n.size;
    if (n.budget != null) options.budget_usd = n.budget;
    const attachments = n.attachments.slice();
    p.value = '';
    app.newModel = { attachments: [], size: null, budget: null };
    renderNewOptions();
    show('studio');
    toggleChat(true);
    app.say({ from: 'you', text, attachments: attachments.map(a => a.url), pending: true });
    if (app.handlers.chatDirector) app.handlers.chatDirector(text, { attachments: attachments.map(a => a.id), options });
  }

  function initFilters() {
    $('#f-agent').innerHTML = '<option value="">All agents</option>' + Object.values(MG.cast).map(c => `<option value="${c.id}">${c.name} · ${c.role}</option>`).join('');
  }

  document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    const floor = $('#floor');
    app.studio = new MG.Studio($('#map'), { life: loadPref('life', !matchMedia('(prefers-reduced-motion: reduce)').matches), onHover: w => app.panel.hover(w) });
    app.panel = new MG.Panel(floor, app.studio);
    app.chatOpen = loadPref('chat', window.innerWidth >= 1280);   // on a narrow window the chat starts as a button
    app.chatScope = loadPref('chat-scope', 'model') === 'all' ? 'all' : 'model';
    app.tabs = loadPref('tabs', {}) || {};
    $('#work').classList.toggle('chat-closed', !app.chatOpen);
    new ResizeObserver(relayout).observe(floor);
    toggleNav(loadPref('nav', false));
    bind(); initFilters();
    if (!loadPref('labels', true)) toggleLabels();
    toggleLife(app.studio.life);
    $('#set-labels').checked = app.studio.labels;
    show('studio');
    renderWorkspace(); renderRun(); renderActivity(); renderStatus(); renderChat();
    setInterval(() => { renderAsks(); renderStatus(); }, 5000);
    document.dispatchEvent(new CustomEvent('mg:ready'));
  });
})();
