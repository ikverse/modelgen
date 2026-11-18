// Runs the page's own app.js in Node with a small stand-in for the browser, and checks that a
// waiting question shows once as a card in the chat, after Hange's report, and once in the
// pinned strip, which points to that card. Run by tests/test_web.py; prints JSON.
const vm = require('vm');
const fs = require('fs');
const path = require('path');

const els = {};
const el = id => (els[id] = els[id] || { id, innerHTML: '', scrollTop: 0, scrollHeight: 1000, clientHeight: 400 });
const document = {
  addEventListener() {},
  getElementById: () => null,
  querySelector: sel => (sel === '#messages' ? el('messages') : sel === '#pinned' ? el('pinned') : null),
  querySelectorAll: () => [],
};
const ctx = { document, console, setTimeout, clearTimeout, matchMedia: () => ({ matches: false }) };
ctx.window = ctx;
ctx.MG = {
  esc: s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
  ago: () => '16 min', clock: () => '', cap: s => s, portrait: () => '', pic: () => '',
  displayName: (studio, w) => w.cast.name,
  cast: { hange: { name: 'Hange' }, reiner: { name: 'Reiner' }, eren: { name: 'Eren' } },
  roleCast: { director: 'hange', materials: 'reiner', builder: 'eren' },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'map', 'js', 'app.js'), 'utf8'), ctx);
const app = ctx.MG.app;

const hange = {
  id: 'director', role: 'director', state: 'waiting', castId: 'hange', cast: { name: 'Hange' },
  data: { since: '2026-11-07T12:00:00', question: {
    id: 'accept-car-4', kind: 'accept', text: "v4 is ready with 4.8 / 10. Accept it, build the next version with Annie's 2 changes below, or add your comments first?",
    options: ['Accept v4', 'Build next version', 'Add my comments'],
    items: [{ target: 'materials', role: 'materials', short: 'Darker trim, opaque glass', text: 'trim: base colour #151515', auto: false },
            { target: 'assembly', role: 'assembler', short: 'Put it on the floor', text: 'Hard gate on_floor failed', auto: true }] } },
};
app.studio = { agents: () => [hange] };
app.renderChat();  // before the chat has loaded: no card yet, no strip
const early = { cards: el('messages').innerHTML.split('id="ask-director"').length - 1, strip: el('pinned').innerHTML };
app.chatLoaded = true;
app.data.chat = [
  { from: 'you', text: 'a red sports car', at: '2026-11-07T11:00:00' },
  { from: 'hange', text: 'v4 is ready', at: '2026-11-07T12:00:05' },  // the report, just after the question
  { from: 'you', text: 'build a phone', at: '2026-11-07T12:10:00' },
  { from: 'hange', text: 'Sure', at: '2026-11-07T12:10:05' },
];
app.renderChat();
const chat = el('messages').innerHTML, strip = el('pinned').innerHTML;
const at = s => chat.indexOf(s);
console.log(JSON.stringify({
  cards: chat.split('id="ask-director"').length - 1,
  afterReport: at('id="ask-director"') > at('v4 is ready</') && at('id="ask-director"') < at('build a phone'),
  buttons: (chat.match(/data-act="answer"/g) || []).length,
  changes: chat.includes("Annie&#39;s 1 change + 1 from the checks") || chat.includes("Annie's 1 change + 1 from the checks"),
  stripLines: (strip.match(/data-act="goto-ask"/g) || []).length,
  stripPoints: strip.includes('data-agent="director"'),
  stripText: strip.includes('v4 is ready with 4.8 / 10<'),
  firstSentence: app.asks.firstSentence('v2 is ready with 7.1 / 10. Accept it?'),
  noQuestionAt: app.asks.askAt(app.data.chat, 'not a time'),
  earlyCards: early.cards, earlyStrip: early.strip,
  acceptIsMain: /class="btn primary" data-act="answer" data-agent="director" data-choice="Accept v4"/.test(chat),
  othersPlain: (chat.match(/class="btn primary"/g) || []).length === 1,
}));
