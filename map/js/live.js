// The live link to the ModelGen engine. It opens the websocket, passes every studio snapshot
// to applyState(), loads the page data into MG.app.loadData(), and sends what the person does
// (chat, answers, accept, changes, uploads, settings) back to the engine. demo.js is the
// stand-in when the page runs without the engine (?demo=1).
window.MG = window.MG || {};
(function () {
  'use strict';
  const app = () => MG.app;
  const state = { project: null, version: null, socket: null, retry: 0, loaded: false, refreshTimer: null, lastProject: null };

  async function call(path, options) {
    const res = await fetch(path, options);
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) throw new Error((body && body.detail) || res.statusText || 'request failed');
    return body;
  }
  const post = (path, body) => call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

  // ---- page data ------------------------------------------------------------------------
  async function refresh(withChat) {
    const q = new URLSearchParams();
    if (state.project) q.set('project', state.project);
    const m = app().data.model;
    const keep = m && m.project === state.project && m.userPicked ? m.selected : state.version;
    if (keep) q.set('version', keep);
    let d;
    try { d = await call('/api/page?' + q); } catch (e) { app().toast('Could not load the page data: ' + e.message); return; }
    state.project = d.project;
    state.version = null;
    if (d.model && m && m.project === d.model.project && m.userPicked) d.model.userPicked = true;
    app().viewerAvailable = d.viewer;
    const data = { model: d.model, models: d.models, parts: d.parts, spend: d.spend, budget: d.budget,
                   blender: d.blender, settings: d.settings, limits: d.limits };
    if (withChat) data.chat = d.chat;
    app().loadData(data);
  }
  function refreshSoon(ms) {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => refresh(false), ms || 400);
  }

  // ---- the websocket --------------------------------------------------------------------
  function setSource(s) { app().source = s; window.applyState(MG.state || {}); }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    state.socket = ws;
    ws.addEventListener('open', () => {
      state.retry = 0;
      setSource('Live');
      refresh(!state.loaded);
      state.loaded = true;
      call('/api/update').then(u => app().setUpdate(u)).catch(() => {});
    });
    ws.addEventListener('message', ev => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'studio') onSnapshot(msg.snapshot);
      else if (msg.type === 'event') onEvent(msg.item);
      else if (msg.type === 'error') app().toast(msg.text);
      else if (msg.type === 'update') app().setUpdate(msg.update);
    });
    ws.addEventListener('close', () => {
      state.socket = null;
      setSource('Reconnecting');
      state.retry = Math.min(state.retry + 1, 6);
      setTimeout(connect, 500 * Math.pow(2, state.retry - 1));
    });
  }

  function onSnapshot(snap) {
    window.applyState(snap);
    // A build started on another model: follow it, even when the person had picked a model
    // themselves, and offer the way back. Picking a model again while it builds is not undone.
    if (snap.project && snap.project !== state.lastProject) {
      const run = (snap.runs || [])[0];
      const building = !!(run && run.busy && run.project === snap.project);
      const followed = state.lastProject === null || !state.userProject || building;
      state.lastProject = snap.project;
      if (followed && snap.project !== state.project) {
        const back = state.userProject ? state.project : null;
        const backName = back && app().data.model && app().data.model.project === back ? app().data.model.name : back;
        state.project = snap.project; state.version = null; state.userProject = false;
        refreshSoon(50);
        if (back) app().toast(`Showing ${run.model || 'the new model'} while the team builds it`, 'Back to ' + backName, () => app().selectProject(back), 'hange');
      }
    }
  }

  const REFRESH_KINDS = new Set(['project_created', 'build_started', 'version_started', 'version_done', 'version_failed',
    'finished', 'accepted', 'reverted', 'version_deleted', 'limit_raised', 'phase', 'live_model']);

  function onEvent(item) {
    if (!item) return;
    if (item.kind === 'message') return onMessage(item.data, item.project);
    if (REFRESH_KINDS.has(item.kind)) refreshSoon(item.kind === 'phase' ? 800 : 300);
    if (item.kind === 'version_done' && item.data) {
      app().toast(`v${item.data.version} scored ${(item.data.score / 10).toFixed(1)} / 10${item.data.passed ? ' and passed' : ''}`, 'Open', () => app().selectProject(item.project, 'v' + item.data.version), 'annie');
    }
    if (item.kind === 'tool_done' && item.data && item.data.tool === 'add_part') refreshSoon(500);
  }

  function onMessage(m, project) {
    project = m.project || project || null;   // the model the message is about: the chat shows one model at a time
    const chat = app().data.chat;
    if (m.role === 'user') {
      // The page showed the message as soon as it was sent; swap in the stored one.
      const i = chat.findIndex(x => x.pending && x.from === 'you' && (x.text === m.text || (!x.text && m.text === '(see the attached image)')));
      const item = { id: m.id, from: 'you', text: m.text, at: m.created, time: m.created.slice(11, 16), project,
                     attachments: ((m.meta || {}).attachments || []).map(a => '/uploads/' + a) };
      if (i >= 0) chat.splice(i, 1, item); else chat.push(item);
    } else {
      chat.push({ id: m.id, from: 'hange', text: m.text, at: m.created, time: m.created.slice(11, 16), project,
                  version: (m.meta || {}).version || null });
      if (!app().chatOpen) app().toast('Hange: ' + m.text.slice(0, 80) + (m.text.length > 80 ? '…' : ''), 'Open chat', () => app().toggleChat(true), 'hange');
    }
    app().renderChat();
  }

  // ---- what the person does --------------------------------------------------------------
  function readFile(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(new Error('could not read the file'));
      r.readAsDataURL(file);
    });
  }

  const handlers = {
    chatDirector(text, extra) {
      const body = { type: 'chat', text, project: state.project, attachments: (extra && extra.attachments) || [],
                     options: (extra && extra.options) || {} };
      if (state.socket && state.socket.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify(body));
      else post('/api/chat', body).catch(e => app().toast('Not sent: ' + e.message));
    },
    async chat(detail) {
      try { await post(`/api/agents/${encodeURIComponent(detail.agent)}/chat`, { text: detail.text }); }
      catch (e) { app().toast('Not sent: ' + e.message); }
    },
    async answer(detail) {
      try {
        const r = await post('/api/answer', { agent: detail.agent, choice: detail.choice });
        if (r.action === 'compose') app().compose(r.prefill || 'Change: ');
        else if (r.action === 'accepted') { app().toast(`Accepted v${r.version}. Its files are in Export.`); refreshSoon(50); }
        else app().toast('Sent: ' + detail.choice);
        if (state.socket && state.socket.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify({ type: 'snapshot' }));
      } catch (e) { app().toast('Not sent: ' + e.message); }
    },
    async accept(project, number) {
      await post(`/api/projects/${encodeURIComponent(project)}/accept`, { version: number });
      refreshSoon(50);
    },
    async stop(project) {
      await post(`/api/projects/${encodeURIComponent(project)}/stop`, {});
      refreshSoon(50);
    },
    async retry(project) {
      await post(`/api/projects/${encodeURIComponent(project)}/retry`, {});
      refreshSoon(50);
    },
    async deleteModel(project) {
      await post(`/api/projects/${encodeURIComponent(project)}/delete`, {});
      // the page falls back to the most recent model left; follow it from now on
      if (state.project === project) { state.project = null; state.version = null; state.userProject = false; }
      refresh(true);
    },
    async revert(project, number) {
      await post(`/api/projects/${encodeURIComponent(project)}/revert`, { version: number });
      refreshSoon(50);
    },
    async deleteVersion(project, number) {
      await post(`/api/projects/${encodeURIComponent(project)}/versions/${number}/delete`, {});
      const m = app().data.model;  // stop asking the page for the version that is gone
      if (m && m.project === project && m.selected === `v${number}`) m.userPicked = false;
      if (String(state.version || '').replace(/^v/, '') === String(number)) state.version = null;
      refreshSoon(50);
    },
    selectProject(project, version) {
      state.userProject = true;
      state.project = project;
      state.version = version || null;
      if (app().data.model && app().data.model.project !== project) app().data.model.userPicked = false;
      refresh(false).then(() => {
        const m = app().data.model;
        if (m && version) { m.selected = version; m.userPicked = true; app().renderWorkspace(); }
      });
    },
    async upload(file) {
      const data = await readFile(file);
      return post('/api/uploads', { data });
    },
    saveSettings(change) { return post('/api/settings', change); },
    updateCheck() { return post('/api/update/check'); },
    updateInstall(force) { return post('/api/update/install', { force: !!force }); },
    restart(force) { return post('/api/restart', { force: !!force }); },
    loadActivity(agent) { return call('/api/activity' + (agent ? '?agent=' + encodeURIComponent(agent) : '')); },
    async mountViewer(el) {
      if (app().viewerAvailable === false) throw new Error('the viewer library is not installed (run Setup-ModelGen.bat)');
      const mod = await import('/static/viewer.js?v=17');  // bump with index.html so a cached copy is not used
      return mod.createViewer(el);
    }
  };

  function start() {
    Object.assign(app().handlers, handlers);
    app().source = 'Connecting';
    connect();
  }

  if (MG.app && MG.app.studio) start();
  else document.addEventListener('mg:ready', start);
})();
