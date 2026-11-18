// The motion engine. It owns one walker per agent, turns agent state into where each
// character goes and how it is posed, plays hand-offs and meetings, keeps everyone
// depth-sorted against the furniture, and handles hover, click, zoom and pan.
// The page never computes agent state: it passes snapshots in through update().
window.MG = window.MG || {};
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const W = MG.world;
  const STATES = new Set(['idle', 'working', 'waiting', 'done', 'failed']);
  const WALK_H = 70, WALK_V = 34, RUN = 2.3;
  const SHADOW = '<ellipse cx="0" cy="1" rx="40" ry="5" fill="#000" opacity=".12"/>';
  const PARCEL = '<g><rect x="-13" y="-118" width="26" height="20" rx="2" fill="#C9A46A"/><rect x="-13" y="-111" width="26" height="4" fill="#E8DCC4"/><rect x="-2" y="-118" width="4" height="20" fill="#B08A52"/></g>';
  const scaleAt = W.scaleAt;
  const el = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };
  const pick = l => l[Math.floor(Math.random() * l.length)];
  const rand = (a, b) => a + Math.random() * (b - a);

  class Studio {
    constructor(svg, opts) {
      this.svg = svg;
      this.opts = opts || {};
      this.life = this.opts.life !== false;
      this.labels = true;
      this.selected = null;
      this.walkers = new Map();
      this.items = [];
      this.seen = new Set();
      this.shared = W.SHARED.map(sp => ({ sp, busy: false, here: null }));
      // The view: (x, y) is the world point at the svg's top-left, k is screen pixels per
      // world unit. Insets are the screen space the floating panels cover.
      this.view = { x: 0, y: 0, k: 1 };
      this.insets = { l: 0, r: 0, t: 0, b: 0 };
      this.fitted = true;
      svg.innerHTML = '';
      // The room carries on past the studio's edges, so the map fills the background
      // at any window shape.
      let ext = `<rect x="-3000" y="-3000" width="7200" height="3172" fill="var(--wall)"/><rect x="-3000" y="172" width="7200" height="3508" fill="var(--floor)"/><rect x="-3000" y="166" width="7200" height="6" fill="var(--base)"/>`;
      for (let y = 204; y < 3600; y += y < 300 ? 36 : 44) ext += `<line x1="-3000" y1="${y}" x2="4200" y2="${y}" stroke="var(--plank)" stroke-width="1"/>`;
      this.gRoom = el('g', { class: 'room' }); this.gRoom.innerHTML = ext + W.roomShell();
      this.gWorld = el('g', { class: 'world' });
      this.gCaps = el('g', { class: 'captions' });
      this.gLabels = el('g', { class: 'labels' });
      svg.append(this.gRoom, this.gWorld, this.gCaps, this.gLabels);
      this.gLabels.innerHTML = W.LABELS.map(([x, y, t]) => `<text x="${x}" y="${y}" text-anchor="middle">${t}</text>`).join('');
      W.FURNITURE.forEach(([y, s]) => {
        const g = el('g', {}); g.innerHTML = s; this.gWorld.appendChild(g);
        this.items.push({ y, g });
      });
      // Screens that follow their owner's state, and the GPU room's racks and console.
      this.screens = [...svg.querySelectorAll('.scr[data-o]')].map(e => ({ e, o: e.dataset.o, cls: '' }));
      this.gpus = [...svg.querySelectorAll('.farm, .gpuc')];
      Object.keys(MG.roleCast).forEach(role => { if (role !== 'builder') this.addWalker(role, role, 0); });
      this.addWalker('builder', 'builder', 0);
      this.bindPointer();
      this.last = performance.now();
      requestAnimationFrame(t => this.loop(t));
    }

    // ---- walkers ------------------------------------------------------------------
    addWalker(id, role, seat) {
      const castId = MG.roleCast[role], c = MG.cast[castId];
      const st = role === 'builder' ? W.SEATS[seat] : W.STATIONS[castId];
      const g = el('g', { class: 'walker', 'data-id': id, tabindex: '-1' });
      g.innerHTML = `<ellipse class="ring" cx="0" cy="2" rx="46" ry="10" fill="none" stroke="${c.colour}" stroke-width="5"/>${SHADOW}<g></g><g style="display:none"></g><g style="display:none"></g><g style="display:none">${PARCEL}</g>`;
      const v = { f: g.children[2], s: g.children[3], b: g.children[4] };
      v.f.innerHTML = MG.rig.front(c); v.s.innerHTML = MG.rig.side(c); v.b.innerHTML = MG.rig.back(c);
      this.gWorld.appendChild(g);
      const cap = el('text', { class: 'cap', 'text-anchor': 'middle' }); this.gCaps.appendChild(cap);
      const w = {
        id, role, castId, cast: c, seat, st, g, v, pc: g.children[5], cap,
        x: st.home[0], y: st.home[1], dir: 1, view: 'f', q: [], cur: null, pts: null, until: performance.now() + rand(2000, 7000),
        room: null, hold: null, at: st.home, busy: 0, hq: [], carrying: false,
        state: 'idle', data: { id, role, state: 'idle' }, doneAt: 0
      };
      const item = { get y() { return w.y; }, g };
      w.item = item; this.items.push(item);
      this.walkers.set(id, w);
      this.stance(w);
      return w;
    }

    removeWalker(w) {
      this.release(w);
      w.g.remove(); w.cap.remove();
      this.items = this.items.filter(i => i !== w.item);
      this.walkers.delete(w.id);
      if (this.selected === w.id) this.select(null);
    }

    byRole(role) { for (const w of this.walkers.values()) if (w.role === role) return w; return null; }
    find(idOrRole) { return this.walkers.get(idOrRole) || this.byRole(idOrRole); }

    // ---- snapshots ----------------------------------------------------------------
    update(snap) {
      snap = snap || {};
      const agents = Array.isArray(snap.agents) ? snap.agents : [];
      // Builders: one walker per builder entry, in id order, one seat each.
      const builders = agents.filter(a => a.role === 'builder').sort((a, b) => String(a.id).localeCompare(String(b.id))).slice(0, W.SEATS.length);
      const current = [...this.walkers.values()].filter(w => w.role === 'builder').sort((a, b) => a.seat - b.seat);
      if (builders.length) {
        builders.forEach((a, i) => {
          let w = current.find(x => x.seat === i);
          if (!w) w = this.addWalker(a.id, 'builder', i);
          else if (w.id !== a.id) { this.walkers.delete(w.id); w.id = a.id; w.g.dataset.id = a.id; this.walkers.set(a.id, w); }
        });
        current.filter(w => w.seat >= builders.length).forEach(w => this.removeWalker(w));
      }
      agents.forEach(a => {
        if (!a || !a.role || !MG.roleCast[a.role]) { if (a) console.warn('studio: unknown role', a.role); return; }
        let w = this.walkers.get(a.id);
        if (!w && a.role !== 'builder') {
          w = this.byRole(a.role);
          if (w && w.id !== a.id) { this.walkers.delete(w.id); w.id = a.id; w.g.dataset.id = a.id; this.walkers.set(a.id, w); }
        }
        if (!w) return;
        let state = a.state;
        if (!STATES.has(state)) { console.warn('studio: unknown state', state, 'for', a.id); state = 'idle'; }
        const changed = state !== w.state || (a.spot || a.tool || null) !== (w.data.spot || w.data.tool || null);
        w.data = Object.assign({}, a, { state });
        if (changed) { w.state = state; if (state === 'done') w.doneShown = false; this.retarget(w); }
      });
      (snap.handoffs || []).forEach(h => {
        const key = h.id || [h.from, h.to, h.label].join('|');
        if (this.seen.has('h:' + key)) return;
        this.seen.add('h:' + key);
        const from = this.find(h.from), to = this.find(h.to);
        if (!from || !to) { console.warn('studio: hand-off with unknown agent', h); return; }
        from.hq.push({ to, label: h.label || '' });
        this.pump(from);
      });
      (snap.meetings || []).forEach(m => {
        const key = m.id || [m.kind, (m.agents || []).join(',')].join('|');
        if (this.seen.has('m:' + key)) return;
        this.seen.add('m:' + key);
        this.meeting(m.kind, (m.agents || []).map(id => this.find(id)).filter(Boolean));
      });
    }

    target(w) {
      // spot: where the engine says this tool is used; tool: the demo's own tool names.
      // Working with any other tool means sitting at their own computer.
      const t = w.data.spot || w.data.tool;
      if (w.state !== 'working') return w.st.home;
      return (t && w.st.tools && w.st.tools[t]) || w.st.work || w.st.home;
    }

    // Send a walker to where its current state belongs, unless a script owns it.
    retarget(w) {
      if (w.busy) return;
      this.release(w);
      w.q = [this.go(this.target(w))];
      w.cur = null; w.pts = null;
      this.next(w);
    }

    // ---- scripts: hand-offs and meetings --------------------------------------------
    pump(w) {
      if (w.busy || !w.hq.length) return;
      const h = w.hq.shift(), to = h.to;
      w.busy++;
      this.release(w);
      const run = to.state === 'waiting';
      const spot = to.st.visit || to.st.home;
      w.q = [
        this.fn(x => { x.carrying = true; x.pc.style.display = ''; this.emit('studio:handoff', { from: x.id, to: to.id, label: h.label }); }),
        this.go(spot, run),
        this.fn(x => { x.carrying = false; x.pc.style.display = 'none'; to.pc.style.display = ''; setTimeout(() => { if (!to.carrying) to.pc.style.display = 'none'; }, 1800); }),
        this.stay(1100),
        this.fn(x => { x.busy--; }),
        this.go(null)
      ];
      w.cur = null; w.pts = null;
      this.next(w);
    }

    // Everyone waits in the room until the last one arrives (or a minute passes, in case
    // someone never makes it), then the meeting runs for nine seconds.
    meeting(kind, list) {
      const group = list.slice(0, W.MEETING.length), m = { here: 0, start: performance.now() };
      const all = () => m.here >= group.length || performance.now() - m.start > 60000;
      group.forEach((w, i) => {
        w.busy++;
        this.release(w);
        w.q = [this.go(W.MEETING[i]), this.fn(() => { m.here++; }), this.until(all), this.stay(9000), this.fn(x => { x.busy--; }), this.go(null)];
        w.cur = null; w.pts = null;
        this.next(w);
      });
      this.emit('studio:meeting', { kind, agents: list.map(w => w.id) });
    }

    // ---- free time ------------------------------------------------------------------
    plan(w) {
      const r = Math.random(), tools = Object.values(w.st.tools || {});
      if (r < .34 && tools.length) {
        w.q = [this.go(pick(tools)), this.stay(rand(2500, 6000)), this.go(w.st.home), this.stay(rand(2000, 5000))];
      } else if (r < .7 && w.role !== 'builder' || (r < .7 && w.seat === 0)) {
        const free = this.shared.filter(s => !s.busy);
        if (!free.length) { w.q = [this.stay(2000)]; return; }
        const s = pick(free); s.busy = true; w.hold = s;
        w.q = [this.go(s.sp), this.stay(rand(4000, 8000)), this.fn(x => this.release(x)), this.go(w.st.home), this.stay(rand(2500, 5000))];
      } else if (r < .86) {
        const others = [...this.walkers.values()].filter(o => o !== w && o.st.visit && o.role !== 'builder');
        if (!others.length) { w.q = [this.stay(2000)]; return; }
        const o = pick(others);
        w.q = [this.go(o.st.visit), this.stay(rand(3000, 5500)), this.go(w.st.home), this.stay(rand(2500, 5000))];
      } else {
        w.q = [this.stay(rand(3000, 6000))];
      }
    }

    release(w) { if (w.hold) { w.hold.busy = false; w.hold.here = null; w.hold = null; } }

    // ---- the action queue -----------------------------------------------------------
    go(spot, run) { return { t: 'go', spot, run }; }       // spot null: wherever the state says
    stay(ms) { return { t: 'stay', ms }; }
    fn(f) { return { t: 'fn', f }; }
    until(f) { return { t: 'until', f }; }             // stand still until f() is true

    next(w) {
      const a = w.q.shift();
      w.cur = a || null;
      if (!a) return;
      if (a.t === 'go') {
        if (!a.spot) a.spot = this.target(w);
        const raw = this.near(w, a.spot) ? this.local(w, a.spot) : this.route(w, a.spot);
        let px = w.x, py = w.y;
        w.pts = raw.filter(p => { const keep = Math.hypot(p[0] - px, p[1] - py) > .5; if (keep) { px = p[0]; py = p[1]; } return keep; });
        w.room = null;
        if (w.pts.length) this.startSeg(w); else this.arrive(w);
      } else if (a.t === 'stay') {
        w.until = performance.now() + a.ms;
      } else if (a.t === 'until') {
        if (a.f()) this.next(w);
      } else {
        a.f(w); this.next(w);
      }
    }

    near(w, sp) { return !sp[3] && !w.room && Math.abs(w.y - sp[1]) < 40 && Math.abs(w.x - sp[0]) < 200; }
    local(w, sp) { return sp[1] < w.y ? [[w.x, sp[1]], [sp[0], sp[1]]] : [[sp[0], w.y], [sp[0], sp[1]]]; }
    route(w, sp) {
      const pts = [];
      let x = w.x, y = w.y;
      if (w.room) { const d = W.DOORS[w.room]; pts.push([x, d.ai], [d.x, d.ai], [d.x, W.lane(d.ai)]); x = d.x; y = W.lane(d.ai); }
      const d = sp[3] ? W.DOORS[sp[3]] : null, tx = d ? d.x : sp[0], L1 = W.lane(y), L2 = W.lane(d ? d.ai : sp[1]);
      pts.push([x, L1]);
      if (L1 !== L2) {
        const c = W.CORRIDORS.slice().sort((u, v) => (Math.abs(x - u) + Math.abs(tx - u)) - (Math.abs(x - v) + Math.abs(tx - v)))[0];
        pts.push([c, L1], [c, L2]);
      }
      if (d) pts.push([d.x, L2], [d.x, d.ai], [sp[0], d.ai], [sp[0], sp[1]]);
      else pts.push([sp[0], L2], [sp[0], sp[1]]);
      return pts;
    }

    startSeg(w) {
      const [tx, ty] = w.pts[0], dx = tx - w.x, dy = ty - w.y, run = w.cur && w.cur.run;
      if (Math.abs(dx) > Math.abs(dy)) { w.dir = dx > 0 ? 1 : -1; this.show(w, 's', 'sd ' + (run ? 'mv-run' : 'mv-walk')); }
      else if (dy > 0) this.show(w, 'f', 'rig ' + (run ? 'fr' : 'fw'));
      else this.show(w, 'b', 'bk ' + (run ? 'brn' : 'bw'));
    }

    arrive(w) {
      const sp = w.cur.spot;
      w.room = sp[3] || null;
      w.at = sp;
      if (w.hold && w.hold.sp === sp) w.hold.here = w;
      this.stance(w);
      this.next(w);
    }

    // ---- poses ----------------------------------------------------------------------
    show(w, view, cls) {
      ['f', 's', 'b'].forEach(k => { w.v[k].style.display = k === view ? '' : 'none'; });
      w.v[view].setAttribute('class', cls + MG.rigClass(w.cast) + (w.carrying ? ' carry' : ''));
      w.view = view;
    }

    stance(w) {
      const face = w.at ? w.at[2] : 'f', tag = w.at ? w.at[4] : null;
      const scripted = w.busy > 0, st = scripted ? 'idle' : w.state;
      // A 'sit' spot seats them at the computer; a 'vr' spot puts the headset on.
      if (face === 'b') return this.show(w, 'b', 'bk' + (tag === 'sit' ? ' sit' : '') + (st === 'working' ? ' bk-work' : ''));
      if (face === 'l' || face === 'r') { w.dir = face === 'r' ? 1 : -1; return this.show(w, 's', 'sd'); }
      let cls = 'rig st-' + st + (tag === 'vr' ? ' vr' : '');
      if (st === 'done') {
        // The celebration plays once, when the character is back where they can be seen.
        if (!w.doneShown) { w.doneShown = true; w.doneAt = performance.now(); }
        else if (performance.now() - w.doneAt > 2600) cls = 'rig st-idle is-done';
      }
      this.show(w, 'f', cls);
    }

    // ---- frame loop -----------------------------------------------------------------
    loop(t) {
      const dt = Math.min(.05, (t - this.last) / 1000);
      this.last = t;
      let gpu = false;
      for (const w of this.walkers.values()) {
        const a = w.cur;
        if (a && a.t === 'go' && w.pts && w.pts.length) {
          const [tx, ty] = w.pts[0], dx = tx - w.x, dy = ty - w.y, d = Math.hypot(dx, dy);
          const v = (Math.abs(dx) > Math.abs(dy) ? WALK_H : WALK_V) * (a.run ? RUN : 1) * dt;
          if (d <= v) { w.x = tx; w.y = ty; w.pts.shift(); if (w.pts.length) this.startSeg(w); else this.arrive(w); }
          else { w.x += dx / d * v; w.y += dy / d * v; }
        } else if (a && a.t === 'stay') {
          if (t > w.until) this.next(w);
        } else if (a && a.t === 'until') {
          if (a.f()) this.next(w);
        } else if (!a) {
          if (w.q.length) this.next(w);
          else if (w.hq.length) this.pump(w);
          else if (!w.busy && w.state === 'idle' && this.life && t > w.until) { this.plan(w); this.next(w); }
          else if (!w.busy && !this.life && w.state === 'idle' && w.at !== w.st.home) { w.q = [this.go(w.st.home)]; this.next(w); }
          else if (w.state === 'done' && w.view === 'f' && t - w.doneAt > 2600 && w.v.f.getAttribute('class').indexOf('st-done') >= 0) this.stance(w);
        }
        // The GPU room works hard while Blender runs: building, assembling, rendering, or anyone in there.
        if (w.room === 'farm' || (w.state === 'working' && (w.role === 'builder' || w.role === 'assembler' || (w.data.spot || w.data.tool) === 'render_views'))) gpu = true;
        const s = scaleAt(w.y);
        w.g.setAttribute('transform', `translate(${w.x.toFixed(1)} ${w.y.toFixed(1)}) scale(${s.toFixed(3)})`);
        w.v.s.setAttribute('transform', `scale(${w.dir} 1)`);
        w.pc.setAttribute('transform', `translate(${w.view === 's' ? w.dir * 9 : 0} 0)`);
        w.g.classList.toggle('sel', this.selected === w.id);
        this.caption(w);
      }
      this.gpus.forEach(g => g.classList.toggle('busy', gpu));
      this.paintScreens();
      const sorted = this.items.slice().sort((p, q) => p.y - q.y);
      const kids = this.gWorld.children;
      for (let i = 0; i < sorted.length; i++) if (kids[i] !== sorted[i].g) this.gWorld.insertBefore(sorted[i].g, kids[i]);
      requestAnimationFrame(tt => this.loop(tt));
    }

    // Each screen shows its owner's state: s-working, s-waiting and so on, or s-off for a
    // builder seat nobody sits at.
    paintScreens() {
      const st = {};
      for (const w of this.walkers.values()) st[w.role === 'builder' ? 'seat' + w.seat : w.castId] = w.state;
      this.screens.forEach(s => {
        const cls = 'scr s-' + (st[s.o] || 'off');
        if (s.cls !== cls) { s.cls = cls; s.e.setAttribute('class', cls); }
      });
    }

    caption(w) {
      let txt = '';
      if (w.state === 'working' && w.data.doing) txt = w.data.doing;
      else if (w.state === 'waiting') txt = 'needs you';
      else if (w.state === 'failed') txt = 'failed';
      if (txt.length > 30) txt = txt.slice(0, 29) + '…';
      if (w.cap.textContent !== txt) w.cap.textContent = txt;
      w.cap.setAttribute('x', w.x.toFixed(1));
      w.cap.setAttribute('y', (w.y + 15 + (w.role === 'builder' ? (w.seat % 2) * 14 : 0)).toFixed(1));
      w.cap.setAttribute('class', 'cap s-' + w.state);
      w.cap.style.display = txt && this.labels !== 'none' ? '' : 'none';
    }

    // ---- selection, labels, life ----------------------------------------------------
    select(id) {
      this.selected = id;
      if (!id) this.emit('studio:deselect', {});
    }
    setLabels(on) { this.labels = on; this.gLabels.style.display = on ? '' : 'none'; }
    setLife(on) { this.life = on; }
    agents() { return [...this.walkers.values()]; }

    // Where a walker's head is on screen, for hover cards.
    screenPoint(w) {
      const m = this.svg.getScreenCTM();
      if (!m) return { x: 0, y: 0 };
      const p = this.svg.createSVGPoint();
      p.x = w.x; p.y = w.y - 210 * scaleAt(w.y);
      const s = p.matrixTransform(m);
      return { x: s.x, y: s.y };
    }

    emit(name, detail) { this.svg.dispatchEvent(new CustomEvent(name, { bubbles: true, detail })); }

    // ---- pointer: hover, click, zoom, pan -------------------------------------------
    toWorld(ev) {
      const p = this.svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
      return p.matrixTransform(this.svg.getScreenCTM().inverse());
    }
    walkerAt(ev) { const g = ev.target.closest && ev.target.closest('.walker'); return g ? this.walkers.get(g.dataset.id) : null; }

    bindPointer() {
      const svg = this.svg;
      let drag = null, hover = null;
      svg.addEventListener('pointermove', ev => {
        if (drag) {
          const dx = ev.clientX - drag.cx, dy = ev.clientY - drag.cy;
          if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
          if (drag.moved) { this.view.x = drag.vx - dx / this.view.k; this.view.y = drag.vy - dy / this.view.k; this.fitted = false; this.apply(); }
          return;
        }
        const w = this.walkerAt(ev);
        if (w !== hover) {
          if (hover) hover.g.classList.remove('hover');
          hover = w;
          if (w) w.g.classList.add('hover');
          if (this.opts.onHover) this.opts.onHover(w);
        }
      });
      svg.addEventListener('pointerleave', () => { if (hover) hover.g.classList.remove('hover'); hover = null; if (this.opts.onHover) this.opts.onHover(null); });
      svg.addEventListener('pointerdown', ev => {
        drag = { cx: ev.clientX, cy: ev.clientY, vx: this.view.x, vy: this.view.y, moved: false, w: this.walkerAt(ev) };
        svg.setPointerCapture(ev.pointerId);
      });
      svg.addEventListener('pointerup', ev => {
        const d = drag; drag = null;
        if (!d || d.moved) return;
        if (d.w) {
          if (d.w.role === 'director') this.emit('studio:director', { agent: d.w.id });
          else if (this.selected === d.w.id) this.select(null);
          else { this.select(d.w.id); this.emit('studio:select', { agent: d.w.id }); }
        } else if (this.selected) this.select(null);
      });
      svg.addEventListener('wheel', ev => {
        ev.preventDefault();
        const p = this.toWorld(ev);
        this.zoomAround(p.x, p.y, this.view.k * Math.exp(-ev.deltaY * .0015));
      }, { passive: false });
      new ResizeObserver(() => { if (this.fitted) this.fit(); else this.apply(); }).observe(svg);
      svg.addEventListener('dblclick', ev => {
        if (this.walkerAt(ev)) return;
        const p = this.toWorld(ev);
        const r = W.ROOMS.find(r => p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h);
        if (r) this.zoomTo(r);
      });
    }

    // ---- the view ---------------------------------------------------------------------
    size() { const r = this.svg.getBoundingClientRect(); return { w: Math.max(1, r.width), h: Math.max(1, r.height) }; }
    free() { const s = this.size(), i = this.insets; return { x: i.l, y: i.t, w: Math.max(80, s.w - i.l - i.r), h: Math.max(80, s.h - i.t - i.b) }; }
    fitK() { const f = this.free(); return Math.min(f.w / W.W, f.h / W.H); }

    apply() {
      const s = this.size(), v = this.view, fk = this.fitK();
      // Keep at least a corner of the studio on screen.
      const vw = s.w / v.k, vh = s.h / v.k;
      v.x = Math.min(Math.max(v.x, -vw + 160), W.W - 160);
      v.y = Math.min(Math.max(v.y, -vh + 120), W.H - 120);
      this.svg.setAttribute('viewBox', `${v.x.toFixed(2)} ${v.y.toFixed(2)} ${vw.toFixed(2)} ${vh.toFixed(2)}`);
      this.svg.classList.toggle('zoomed', v.k > fk * 1.02);
      this.emit('studio:zoom', { pct: Math.round(v.k / fk * 100) });
    }
    // Frame a world rectangle in the open space between the panels.
    frame(r) {
      const f = this.free(), k = Math.min(f.w / r.w, f.h / r.h, this.fitK() * 3);
      this.view = { k, x: r.x - (f.x + (f.w - r.w * k) / 2) / k, y: r.y - (f.y + (f.h - r.h * k) / 2) / k };
      this.apply();
    }
    setInsets(i) { this.insets = i; if (this.fitted) this.fit(); else this.apply(); }
    fit() { this.fitted = true; this.frame({ x: 0, y: 0, w: W.W, h: W.H }); }
    zoomAround(px, py, k) {
      const fk = this.fitK(); k = Math.min(fk * 3, Math.max(fk * .8, k));
      const v = this.view;
      this.view = { k, x: px - (px - v.x) * v.k / k, y: py - (py - v.y) * v.k / k };
      this.fitted = false;
      this.apply();
    }
    zoomBy(f) {
      const fr = this.free(), v = this.view;
      this.zoomAround(v.x + (fr.x + fr.w / 2) / v.k, v.y + (fr.y + fr.h / 2) / v.k, v.k * f);
    }
    zoomTo(r) { this.fitted = false; const pad = 20; this.frame({ x: r.x - pad, y: r.y - pad, w: r.w + 2 * pad, h: r.h + 2 * pad }); }
  }

  MG.Studio = Studio;
})();
