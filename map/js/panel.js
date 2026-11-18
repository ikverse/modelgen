// The agent panel that slides in over the map when a character is clicked, and the
// small hover card. Both read the latest snapshot; neither computes state.
window.MG = window.MG || {};
(function () {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const STATE_WORD = { idle: 'Free', working: 'Working', waiting: 'Needs you', done: 'Done', failed: 'Failed' };
  MG.stateWord = s => STATE_WORD[s] || 'Free';
  MG.esc = esc;

  function ago(iso) {
    if (!iso) return '';
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (s < 60) return s + ' s';
    if (s < 3600) return Math.floor(s / 60) + ' min';
    return Math.floor(s / 3600) + ' h ' + Math.floor(s % 3600 / 60) + ' min';
  }
  const clock = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
  MG.ago = ago; MG.clock = clock;

  // The name a walker goes by: builders are numbered when more than one is running.
  MG.displayName = function (studio, w) {
    const builders = studio.agents().filter(x => x.role === 'builder').length;
    return w.cast.name + (w.role === 'builder' && builders > 1 ? ' · builder ' + (w.seat + 1) : '');
  };

  class Panel {
    constructor(host, studio) {
      this.host = host;
      this.studio = studio;
      this.tab = 'overview';
      this.id = null;
      this.card = document.createElement('div');
      this.card.className = 'hcard';
      this.card.setAttribute('role', 'tooltip');
      this.el = document.createElement('aside');
      this.el.className = 'apanel';
      this.el.setAttribute('aria-label', 'Agent');
      host.append(this.card, this.el);
      this.el.addEventListener('click', ev => this.onClick(ev));
      this.el.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' && ev.target.matches('.reply-input, .chat-input') && ev.target.value.trim()) { ev.preventDefault(); this.send(ev.target); }
      });
      setInterval(() => { if (this.id) this.refreshNow(); }, 1000);
    }

    hover(w) {
      if (!w) { this.card.classList.remove('on'); return; }
      const d = w.data, r = this.host.getBoundingClientRect(), p = this.studio.screenPoint(w);
      this.card.innerHTML = `<div class="row">${MG.portrait(w.castId, 28)}<div><div class="b5">${esc(MG.displayName(this.studio, w))}</div><div class="xs mu">${esc(w.cast.role)} · <span class="st st-${w.state}">${MG.stateWord(w.state)}</span></div></div></div>` +
        (d.doing ? `<div class="xs mu hc-doing">${esc(d.doing)}${d.since ? ' · ' + ago(d.since) : ''}</div>` : '') +
        `<div class="xs hc-hint">${w.role === 'director' ? 'Click to open or close the chat' : 'Click for details'}</div>`;
      const x = Math.min(Math.max(8, p.x - r.left - 110), r.width - 236);
      const y = Math.max(8, p.y - r.top - 96);
      this.card.style.transform = `translate(${x}px, ${y}px)`;
      this.card.classList.add('on');
    }

    open(id) { this.id = id; this.tab = 'overview'; this.render(); this.el.classList.add('open'); }
    close() { this.id = null; this.el.classList.remove('open'); }
    isOpen() { return !!this.id; }

    walker() { return this.id ? this.studio.walkers.get(this.id) : null; }

    render() {
      const w = this.walker();
      if (!w) { this.close(); return; }
      const d = w.data, name = MG.displayName(this.studio, w);
      const tabs = ['overview', 'activity', 'chat'].map(t => `<button class="tab${this.tab === t ? ' on' : ''}" data-tab="${t}" role="tab" aria-selected="${this.tab === t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('');
      this.el.innerHTML = `
        <header class="ap-head">${MG.portrait(w.castId, 44)}
          <div class="grow"><div class="h">${esc(name)}</div><div class="xs mu">${esc(cap(w.cast.role))} · ${esc(w.cast.station)}</div>
          <div class="xs st st-${w.state} ap-state"><span class="sq"></span>${MG.stateWord(w.state)}<span class="ap-since">${d.since ? ' · ' + ago(d.since) : ''}</span></div></div>
          <button class="icon-btn" data-act="close" aria-label="Close">${MG.icon('x')}</button></header>
        <nav class="ap-tabs" role="tablist">${tabs}</nav>
        <div class="ap-body">${this.tab === 'overview' ? this.overview(w) : this.tab === 'activity' ? this.activity(w) : this.chat(w)}</div>
        ${this.tab !== 'chat' && !d.chat ? `<footer class="ap-foot"><div class="xs mu">${esc(w.cast.name)} talks to you through Hange</div><button class="btn wide" data-act="ask">${MG.icon('message')}Ask Hange about ${esc(w.cast.name)}</button></footer>` : ''}`;
    }

    overview(w) {
      const d = w.data;
      let out = '';
      if (w.state === 'waiting' && d.question) {
        out += `<section class="sec"><div class="needs"><div class="xs needs-k">Needs you</div><div class="needs-q" dir="auto">${esc(d.question.text)}</div>
          <div class="row wrap">${(d.question.options || []).map(o => `<button class="btn" data-act="answer" data-choice="${esc(o)}">${esc(o)}</button>`).join('')}</div>
          <div class="row reply"><input class="reply-input" dir="auto" placeholder="Reply to ${esc(w.cast.name)}" aria-label="Reply"><button class="btn" data-act="reply">Send</button></div></div></section>`;
      }
      const rows = [['Doing', d.doing || (w.state === 'idle' ? 'Nothing right now' : '—')], ['Tool', d.tool ? `<code>${esc(d.tool)}</code>` : '—', 1],
        ['Since', d.since ? clock(d.since) + ' · <span class="ap-ago">' + ago(d.since) + '</span>' : '—', 1]];
      if (d.model) rows.push(['Model', esc(d.model), 1]);
      if (d.cost != null) rows.push(['Cost so far', '$' + Number(d.cost).toFixed(2), 1]);
      out += `<section class="sec"><div class="lbl">Now</div>${rows.map(([k, v, raw]) => `<div class="kv"><span class="mu">${k}</span><span>${raw ? v : esc(v)}</span></div>`).join('')}</section>`;
      const run = this.runFor(w);
      if (run) {
        const mine = s => s.agent === w.id || s.agent === w.role;
        out += `<section class="sec"><div class="lbl">Workflow · ${esc(run.title)}</div><ol class="wf">${run.steps.map(s => `<li class="wf-${s.state || 'todo'}${mine(s) ? ' mine' : ''}"><span class="dot"></span><span>${esc(s.label)}</span><span class="xs mu">${esc(this.agentName(s.agent))}</span></li>`).join('')}</ol>${this.flow(w, run)}</section>`;
      }
      const calls = (d.calls || []).slice(-5).reverse();
      if (calls.length) out += `<section class="sec"><div class="lbl">Recent activity</div>${calls.map(c => `<div class="call"><span class="xs mu">${clock(c.at)}</span><code>${esc(c.tool)}</code><span class="xs mu grow ell">${esc(c.text || '')}</span>${c.ok === false ? '<span class="xs st st-failed">failed</span>' : ''}</div>`).join('')}</section>`;
      return out;
    }

    flow(w, run) {
      const i = run.steps.findIndex(s => s.agent === w.id || s.agent === w.role);
      if (i < 0) return '';
      const prev = run.steps.slice(0, i).reverse().find(s => s.agent !== w.id && s.agent !== w.role);
      const next = run.steps.slice(i + 1).find(s => s.agent !== w.id && s.agent !== w.role);
      return `<div class="xs mu flow">${prev ? 'From ' + esc(this.agentName(prev.agent)) + ': ' + esc(prev.label) : 'Starts the run'}${next ? ' · next to ' + esc(this.agentName(next.agent)) : ''}</div>`;
    }

    activity(w) {
      const calls = (w.data.calls || []).slice().reverse();
      if (!calls.length) return `<div class="empty">No tool calls yet. They appear here as ${esc(w.cast.name)} works.</div>`;
      return `<section class="sec">${calls.map(c => `<div class="call"><span class="xs mu">${clock(c.at)}</span><code>${esc(c.tool)}</code><span class="xs grow">${esc(c.text || '')}</span>${c.ok === false ? '<span class="xs st st-failed">failed</span>' : MG.icon('check', 'ok')}</div>`).join('')}</section>`;
    }

    chat(w) {
      const d = w.data;
      if (!d.chat) return `<div class="empty"><p>${esc(w.cast.name)} doesn't take messages directly. Hange passes your questions on and brings the answer back.</p><button class="btn" data-act="ask">${MG.icon('message')}Ask Hange about ${esc(w.cast.name)}</button></div>`;
      const msgs = d.messages || [];
      return `<div class="ap-chat"><div class="msgs">${msgs.length ? msgs.map(m => m.from === 'you'
        ? `<div class="m me" dir="auto">${esc(m.text)}</div>`
        : `<div class="m"><span class="xs mu">${esc(w.cast.name)} · ${clock(m.at)}</span><div dir="auto">${esc(m.text)}</div></div>`).join('') : `<div class="empty">Ask ${esc(w.cast.name)} anything about their work.</div>`}</div>
        <div class="row reply"><input class="chat-input" dir="auto" placeholder="Message ${esc(w.cast.name)}" aria-label="Message"><button class="btn primary" data-act="chat">Send</button></div></div>`;
    }

    runFor(w) {
      const runs = (MG.state && MG.state.runs) || [];
      return runs.find(r => r.steps.some(s => s.agent === w.id || s.agent === w.role)) || runs[0] || null;
    }
    agentName(id) {
      const w = this.studio.find(id);
      if (w) return MG.displayName(this.studio, w);
      if (Array.isArray(id)) return id.map(x => this.agentName(x)).join(', ');
      return id || '';
    }

    refreshNow() {
      const w = this.walker(); if (!w) return;
      const s = this.el.querySelector('.ap-since'), a = this.el.querySelector('.ap-ago');
      if (s) s.textContent = w.data.since ? ' · ' + ago(w.data.since) : '';
      if (a) a.textContent = ago(w.data.since);
    }

    // Re-render after a snapshot, but don't throw away what the user is typing.
    refresh() {
      if (!this.id) return;
      const focused = this.el.querySelector('.reply-input:focus, .chat-input:focus');
      if (focused) {
        // Keep the box the user is in; only bring the conversation up to date.
        const w = this.walker(), box = this.el.querySelector('.ap-chat .msgs');
        if (w && box && this.tab === 'chat') { const tmp = document.createElement('div'); tmp.innerHTML = this.chat(w); const fresh = tmp.querySelector('.msgs'); if (fresh) { box.innerHTML = fresh.innerHTML; box.scrollTop = box.scrollHeight; } }
        return;
      }
      this.render();
    }

    send(input) {
      const w = this.walker(); if (!w) return;
      const text = input.value.trim(); if (!text) return;
      input.value = '';
      if (input.classList.contains('chat-input')) this.emit('studio:chat', { agent: w.id, text });
      else this.emit('studio:answer', { agent: w.id, choice: text });
    }

    onClick(ev) {
      const b = ev.target.closest('[data-act],[data-tab]');
      if (!b) return;
      const w = this.walker();
      if (b.dataset.tab) { this.tab = b.dataset.tab; this.render(); return; }
      const act = b.dataset.act;
      if (act === 'close') { this.studio.select(null); }
      else if (act === 'answer' && w) this.emit('studio:answer', { agent: w.id, choice: b.dataset.choice });
      else if (act === 'reply' || act === 'chat') { const i = this.el.querySelector(act === 'chat' ? '.chat-input' : '.reply-input'); if (i) this.send(i); }
      else if (act === 'ask' && w) this.emit('studio:director', { agent: 'director', prefill: '@' + w.cast.name + ' ' });
    }

    emit(name, detail) { this.host.dispatchEvent(new CustomEvent(name, { bubbles: true, detail })); }
  }
  const cap = s => s ? s[0].toUpperCase() + s.slice(1) : '';
  MG.cap = cap;
  MG.Panel = Panel;
})();
