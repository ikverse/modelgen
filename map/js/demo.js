// Demo driver. Loads mock.json (a model with its brief, versions, files and review), plays its
// script as a run through the four stages by building full snapshots and
// passing them to applyState(), and answers chat and questions with canned replies.
// The engine replaces this file: it calls applyState() with real snapshots instead.
window.MG = window.MG || {};
(function () {
  'use strict';
  const IDS = ['director', 'brief', 'architect', 'builder-1', 'builder-2', 'builder-3', 'assembler', 'materials', 'animator', 'critic', 'librarian'];
  const now = () => new Date().toISOString();
  const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const clone = o => JSON.parse(JSON.stringify(o));
  let mock = null, agents = {}, runs = [], handoffs = [], meetings = [], step = 0, timer = null, playing = true, builders = 2, seq = 0;

  function reset() {
    agents = {};
    IDS.forEach(id => {
      agents[id] = { id, role: id.startsWith('builder') ? 'builder' : id, state: 'idle', doing: '', since: now(), calls: [], chat: id === 'critic', messages: [], model: mock.models_by_role[id.split('-')[0]] || null, cost: 0 };
    });
    runs = [];
  }

  function snapshot() {
    const list = Object.values(agents).filter(a => a.role !== 'builder' || +a.id.split('-')[1] <= builders);
    return { agents: clone(list), handoffs: handoffs.slice(-30), runs: clone(runs).map(withProgress), meetings: meetings.slice(-10) };
  }
  // The fields the engine adds to a run: building or not, the step it is on, how far along and
  // the time left (about 20 s a step at the demo's pace), and which model it builds.
  function withProgress(r) {
    const m = MG.app.data.model, n = r.steps.length;
    const done = r.steps.filter(s => s.state === 'done').length;
    const cur = r.steps.find(s => ['active', 'waiting', 'failed'].includes(s.state));
    const failed = r.steps.some(s => s.state === 'failed');
    return Object.assign(r, {
      busy: done < n && !failed, finished: done === n || failed, current: cur ? cur.label : null,
      percent: Math.round(100 * done / Math.max(n, 1)), eta_s: done < n && !failed ? (n - done) * 20 : null,
      model: m ? m.name : r.title, project: m ? m.project : null
    });
  }
  const apply = () => window.applyState(snapshot());

  function set(id, p) {
    const a = agents[id];
    if (!a) return;
    if (p.state && p.state !== a.state) a.since = now();
    Object.assign(a, p);
    if (p.state && p.state !== 'waiting') delete a.question;
    if (p.tool && p.state !== 'idle') {
      a.calls.push({ tool: p.tool, text: p.doing || '', at: now(), ok: p.state !== 'failed' });
      a.calls = a.calls.slice(-12);
      a.cost = +(a.cost + .01 + Math.random() * .02).toFixed(2);
    }
    if (!p.tool && p.state === 'idle') { a.tool = null; a.doing = ''; }
  }

  function play(s) {
    if (s.reset) { reset(); MG.app.loadData(clone(mock.data)); }
    Object.entries(s.set || {}).forEach(([id, p]) => set(id, p));
    (s.handoff || []).forEach(h => handoffs.push(Object.assign({ id: 'h' + (++seq) }, h)));
    if (s.meeting) meetings.push(Object.assign({ id: 'm' + (++seq) }, s.meeting));
    if (s.run) {
      if (s.run.steps) runs = [{ id: 'run-' + (++seq), title: s.run.title, round: s.run.round, started: now(), cost: 0, steps: clone(s.run.steps) }];
      if (runs[0]) {
        Object.entries(s.run.mark || {}).forEach(([i, st]) => { const x = runs[0].steps[i]; x.state = st; if (st === 'done') x.at = now(); });
        Object.entries(s.run.notes || {}).forEach(([i, n]) => { runs[0].steps[i].note = n; });
        runs[0].cost = +Object.values(agents).reduce((t, a) => t + (a.cost || 0), 0).toFixed(2);
      }
    }
    if (s.model) { MG.app.data.model = clone(mock[s.model]); MG.app.loadData({}); }
    if (s.say) MG.app.say({ from: 'hange', text: s.say, time: clock(), version: s.version });
    apply();
  }

  function tick() {
    clearTimeout(timer);
    if (!playing) return;
    const s = mock.script[step];
    play(s);
    step = (step + 1) % mock.script.length;
    timer = setTimeout(tick, s.wait || 6000);
  }

  function setPlaying(on) {
    playing = on;
    if (on) tick(); else clearTimeout(timer);
    MG.app.toast(on ? 'Demo playing' : 'Demo paused');
  }

  function forceAll(state) {
    setPlaying(false);
    const words = { working: 'working on the demo', waiting: 'waiting for you', done: 'finished', failed: 'something broke', idle: '' };
    Object.keys(agents).forEach(id => set(id, { state, doing: words[state], tool: state === 'working' ? null : agents[id].tool, question: state === 'waiting' ? { text: 'Demo question from ' + id + '?', options: ['Yes', 'No'] } : undefined }));
    apply();
  }

  function handoffRun() {
    const chain = [['director', 'brief', 'your request'], ['brief', 'architect', 'spec v3'], ['architect', 'builder-1', 'part list: 6 parts'], ['builder-1', 'assembler', '6 built parts'], ['assembler', 'materials', 'assembled model'], ['materials', 'animator', 'model with materials'], ['animator', 'critic', 'model with motion'], ['critic', 'director', 'scores and change list'], ['builder-1', 'librarian', 'rail_curved to save']];
    setPlaying(false);
    chain.forEach(([from, to, label], i) => setTimeout(() => { handoffs.push({ id: 'h' + (++seq), from, to, label }); apply(); }, i * 5500));
  }

  function meeting(kind) {
    meetings.push({ id: 'm' + (++seq), kind, agents: kind === 'kickoff' ? ['director', 'brief', 'architect'] : ['director', 'critic', 'assembler', 'materials'] });
    apply();
  }

  function onAnswer(d) {
    const a = agents[d.agent];
    if (!a || a.state !== 'waiting') return;
    const name = MG.cast[MG.roleCast[a.role]].name;
    if (a.role === 'director' && a.question && a.question.kind === 'accept') {   // Hange's "accept, or comments?" at the end of a build
      const m = MG.app.data.model, id = 'v' + a.question.version;
      set(d.agent, { state: 'idle' });
      MG.app.say({ from: 'you', text: d.choice });
      if (/^accept/i.test(d.choice)) {
        m.versions.forEach(v => { v.accepted = v.id === id; });
        m.accepted = id;
        MG.app.renderWorkspace();
        setTimeout(() => MG.app.say({ from: 'hange', text: `Accepted ${id}. Its files are in Export.`, time: clock() }), 700);
      } else {
        MG.app.compose('Change: ');
      }
      apply();
      return;
    }
    set(d.agent, { state: 'working', doing: 'using your answer: ' + d.choice, tool: a.role === 'builder' ? 'run_blender' : 'reply' });
    if (runs[0]) runs[0].steps.forEach(s => { if (s.state === 'waiting') s.state = 'active'; });
    MG.app.say({ from: 'you', text: d.choice });
    setTimeout(() => MG.app.say({ from: 'hange', text: `Got it: ${d.choice}. I've told ${name}.`, time: clock() }), 700);
    apply();
  }

  function onChat(d) {
    const a = agents[d.agent];
    if (!a || !a.chat) return;
    a.messages.push({ from: 'you', text: d.text, at: now() });
    apply();
    setTimeout(() => {
      a.messages.push({ from: 'agent', text: mock.chat_replies[a.role] || 'Noted.', at: now() });
      apply();
    }, 1200);
  }

  function onDirector(text) {
    setTimeout(() => MG.app.say({ from: 'hange', text: /^@/.test(text) ? 'I\'ll ask them and bring the answer back here.' : 'Got it. I\'ll pass that to the team and tell you when the next version is ready.', time: clock() }), 900);
  }

  async function start() {
    try {
      const r = await fetch('mock.json', { cache: 'no-store' });
      mock = await r.json();
    } catch (e) {
      MG.app.toast('Could not load mock.json. Serve this folder over http (see README).');
      return;
    }
    MG.app.source = 'Demo data';
    MG.app.handlers.answer = onAnswer;
    MG.app.handlers.chat = onChat;
    MG.app.handlers.chatDirector = onDirector;
    MG.app.addCommands([
      { group: 'Demo', icon: 'pause', label: 'Pause or resume the demo run', run: () => setPlaying(!playing) },
      { group: 'Demo', icon: 'step', label: 'Next demo step', run: () => { setPlaying(false); play(mock.script[step]); step = (step + 1) % mock.script.length; } },
      { group: 'Demo', icon: 'reset', label: 'Restart the demo run', run: () => { step = 0; setPlaying(true); } },
      { group: 'Demo', icon: 'arrow-right', label: 'Play a hand-off run', run: handoffRun },
      { group: 'Demo', icon: 'message', label: 'Kickoff meeting', run: () => meeting('kickoff') },
      { group: 'Demo', icon: 'message', label: 'Review meeting', run: () => meeting('review') },
      ...['idle', 'working', 'waiting', 'done', 'failed'].map(s => ({ group: 'Demo', icon: 'walk', label: 'Everyone: ' + MG.stateWord(s).toLowerCase(), hint: 'pauses the run', run: () => forceAll(s) })),
      ...[1, 2, 3].map(n => ({ group: 'Demo', icon: 'parts', label: n + (n > 1 ? ' builders' : ' builder') + ' running', run: () => { builders = n; apply(); } }))
    ]);
    reset();
    MG.app.loadData(clone(mock.data));
    apply();
    step = 0;
    timer = setTimeout(tick, 2500);
  }

  if (MG.app && MG.app.studio) start();
  else document.addEventListener('mg:ready', start);
})();
