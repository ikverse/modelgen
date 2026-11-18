// The studio as data: the room shell, every piece of furniture with its depth key (the
// y where it meets the floor, so characters sort in front of or behind it), the rooms
// with their doors, the walkways, every spot a character can stand on, and which tool
// sends which character where. World units: 1680 x 680.
//
// It is a 3D-model studio: every agent has a computer workstation. Each workstation's
// screens carry data-o="<owner>" (a cast id, or seat0 to seat3 for the builders) and
// studio.js shows on them what that owner is doing. A spot is [x, y, facing, door, tag]:
// facing f front, b back (facing the furniture), l / r side; the tag 'sit' seats the
// character in the office chair there, back to us, and 'vr' puts the headset on.
window.MG = window.MG || {};
(function () {
  'use strict';

  // The studio's perspective: how big a character is drawn when standing at depth y.
  // Furniture that characters sit at is scaled by K, so a front-row desk fits a front-row character.
  const scaleAt = y => .40 + (y - 214) / (540 - 214) * .11;
  const K = y => scaleAt(y) / scaleAt(226);
  const n = v => Math.round(v * 10) / 10;   // keep the SVG short
  const R = (x, y, w, h, f, extra) => `<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" fill="${f}"${extra || ''}/>`;

  const T = (dx, dy, s) => `<g transform="translate(${dx} ${dy})">${s}</g>`;
  const OX = 260;                       // the lounge pieces are drawn from here, then shifted into place

  const MATS = ['#B5824E', '#6B4A34', '#9AA3AB', '#C9A44A', '#E3DCCB', '#EEECE8', '#8A4E34', '#BFD8E0', '#3A3B3F'];
  const LEDC = ['#39D98A', '#4C9BF7', '#39D98A', '#EF9F27'];
  const FRAME = (x, y, h) => `<rect x="${x}" y="${y}" width="3" height="${h}" fill="var(--frame)" opacity=".7"/>`;

  // The model the studio is making, as line art: the rocking chair from the front, the side or the top.
  const art = (x, y, v, c) => v === 'side'
    ? `<path d="M${x - 16} ${y} Q${x} ${y + 6} ${x + 16} ${y} M${x - 8} ${y + 2} V${y - 16} M${x + 8} ${y + 2} L${x + 11} ${y - 40} M${x - 10} ${y - 16} H${x + 10}" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`
    : v === 'top'
      ? `<rect x="${x - 12}" y="${y - 28}" width="24" height="22" fill="none" stroke="${c}" stroke-width="2"/><path d="M${x - 16} ${y - 32} V${y - 2} M${x + 16} ${y - 32} V${y - 2}" stroke="${c}" stroke-width="2"/>`
      : `<path d="M${x - 12} ${y} V${y - 40} M${x + 12} ${y} V${y - 40} M${x - 12} ${y - 16} H${x + 12} M${x - 6} ${y - 18} V${y - 38} M${x} ${y - 18} V${y - 38} M${x + 6} ${y - 18} V${y - 38}" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`;
  const plant = (x, y, k) => `<rect x="${x - 8 * k}" y="${y - 16 * k}" width="${16 * k}" height="${16 * k}" rx="2" fill="#C98A5A"/><circle cx="${x - 5 * k}" cy="${y - 24 * k}" r="${8 * k}" fill="#6E9A6A"/><circle cx="${x + 5 * k}" cy="${y - 28 * k}" r="${9 * k}" fill="#5E8A5A"/><circle cx="${x}" cy="${y - 38 * k}" r="${7 * k}" fill="#6E9A6A"/>`;

  // A glass wall seen from the front: tinted panes with a frame, mullions, and gaps for doors.
  function pane(x1, x2, y1, y2, gaps) {
    let segs = [[x1, x2]];
    (gaps || []).forEach(([a, b]) => { segs = segs.flatMap(([s, e]) => a > s && b < e ? [[s, a], [b, e]] : [[s, e]]); });
    return segs.map(([a, b]) => {
      let m = '';
      for (let x = a; x <= b - 20; x += 56) m += `<rect x="${x}" y="${y1}" width="2.5" height="${y2 - y1}" fill="var(--frame)"/>`;
      return `<rect x="${a}" y="${y1}" width="${b - a}" height="${y2 - y1}" fill="var(--glass)" opacity=".26"/><rect x="${a}" y="${y1}" width="${b - a}" height="2.5" fill="var(--frame)"/><rect x="${a}" y="${y2 - 3}" width="${b - a}" height="3" fill="var(--frame)"/>${m}<rect x="${b - 2.5}" y="${y1}" width="2.5" height="${y2 - y1}" fill="var(--frame)"/>`;
    }).join('');
  }

  // ---- screens --------------------------------------------------------------------------
  // What a screen shows while its owner works: one drawing per kind, filling the box (x, y, w, h).
  const LINE = ['#C792EA', '#82AAFF', '#C3E88D', '#F78C6C'];
  const KINDS = {
    code(x, y, w, h) {   // code being written
      let o = R(x, y, w, h, '#1A1F27');
      for (let i = 0; i < 4; i++) {
        const ind = i % 2 ? w * .12 : 0;
        o += R(x + w * .08 + ind, y + h * (.16 + i * .2), w * [.55, .58, .4, .5][i], h * .09, LINE[i], i === 3 ? ' class="typ"' : '');
      }
      return o;
    },
    wire(x, y, w, h) {   // a part in the viewport: a wireframe chair turning over the grid
      const s = Math.min(w, h) * .66, cx = x + w / 2, cy = y + h / 2;
      const p = `M${n(cx + .22 * s)} ${n(cy - .45 * s)} V${n(cy + .45 * s)} M${n(cx - .28 * s)} ${n(cy + .05 * s)} H${n(cx + .22 * s)} M${n(cx - .28 * s)} ${n(cy + .05 * s)} V${n(cy + .45 * s)} M${n(cx - .28 * s)} ${n(cy + .25 * s)} H${n(cx + .22 * s)} M${n(cx + .04 * s)} ${n(cy - .45 * s)} V${n(cy + .05 * s)} M${n(cx + .04 * s)} ${n(cy - .45 * s)} H${n(cx + .22 * s)}`;
      return R(x, y, w, h, '#122238') + `<path d="M${n(x)} ${n(y + h * .8)} H${n(x + w)} M${n(x + w * .25)} ${n(y + h * .6)} V${n(y + h)} M${n(x + w * .75)} ${n(y + h * .6)} V${n(y + h)}" stroke="#24406A" stroke-width="${n(h * .03)}"/>` +
        `<g class="spin"><path d="${p}" fill="none" stroke="#7FB2F0" stroke-width="${n(s * .07)}" stroke-linecap="round"/></g>`;
    },
    sphere(x, y, w, h) {   // a material on its preview sphere, changing as it is tuned
      const r = Math.min(w, h) * .34, cx = x + w / 2, cy = y + h / 2 - r * .1;
      return R(x, y, w, h, '#22272E') + `<ellipse cx="${n(cx)}" cy="${n(cy + r * 1.05)}" rx="${n(r * .9)}" ry="${n(r * .18)}" fill="#000" opacity=".35"/><circle class="mat" cx="${n(cx)}" cy="${n(cy)}" r="${n(r)}" fill="#B5824E"/><circle cx="${n(cx - r * .35)}" cy="${n(cy - r * .35)}" r="${n(r * .28)}" fill="#FFF" opacity=".45"/>`;
    },
    nodes(x, y, w, h) {   // the material's node graph
      const b = (bx, by, c) => R(x + w * bx, y + h * by, w * .22, h * .22, c, ' rx="1"');
      return R(x, y, w, h, '#22272E') +
        `<path d="M${n(x + w * .3)} ${n(y + h * .3)} C${n(x + w * .45)} ${n(y + h * .3)} ${n(x + w * .42)} ${n(y + h * .55)} ${n(x + w * .58)} ${n(y + h * .55)} M${n(x + w * .3)} ${n(y + h * .75)} C${n(x + w * .45)} ${n(y + h * .75)} ${n(x + w * .42)} ${n(y + h * .6)} ${n(x + w * .58)} ${n(y + h * .6)}" fill="none" stroke="#9AA3AB" stroke-width="${n(h * .04)}"/>` +
        b(.08, .2, '#C98A5A') + b(.08, .65, '#6E9A6A') + b(.58, .45, '#7C9CB5') + R(x + w * .84, y + h * .5, w * .08, h * .12, '#E3DCCB', ' class="tick"');
    },
    render(x, y, w, h) {   // a render of the model, its score filling in
      const k = h / 54;
      return R(x, y, w, h, '#2B3038') + `<g transform="translate(${n(x + w * .4)} ${n(y + h * .84)}) scale(${n(k)})">${art(0, 0, 'side', '#D9C3A0')}</g>` +
        R(x + w * .74, y + h * .18, w * .14, h * .64, '#3A404A') + R(x + w * .74, y + h * .18, w * .14, h * .64, '#6FD39A', ' class="fill"');
    },
    check(x, y, w, h) {   // the review checklist, ticked off one criterion at a time
      let o = R(x, y, w, h, '#F2F0EB');
      for (let i = 0; i < 4; i++) {
        const yy = y + h * (.12 + i * .2);
        o += R(x + w * .1, yy, h * .14, h * .14, '#DCD8CE') + R(x + w * .1 + h * .03, yy + h * .03, h * .08, h * .08, i === 2 ? '#E0605A' : '#3FAF6E', ` class="tick" style="animation-delay:${i * .6}s"`) +
          R(x + w * .1 + h * .22, yy + h * .04, w * [.5, .4, .55, .35][i], h * .06, '#B4B2A9');
      }
      return o;
    },
    tree(x, y, w, h) {   // the architect's parts tree
      const nodes = [[.5, .1], [.27, .42], [.73, .42], [.17, .74], [.5, .74], [.83, .74]];
      const bw = w * .28, bh = h * .13;
      const P = (i, low) => `${n(x + w * nodes[i][0])} ${n(y + h * nodes[i][1] + (low ? bh : 0))}`;
      return R(x, y, w, h, '#14263A') + `<path d="${[[0, 1], [0, 2], [1, 3], [1, 4], [2, 5]].map(([a, b]) => `M${P(a, 1)} L${P(b, 0)}`).join(' ')}" stroke="#4E7DA8" stroke-width="${n(w * .04)}"/>` +
        nodes.map(([u, v], i) => R(x + w * u - bw / 2, y + h * v, bw, bh, '#BFD8E0', ` rx="1" class="tile" style="animation-delay:${i * .5}s"`)).join('');
    },
    blue(x, y, w, h) {   // a pen display: a blueprint being drawn
      let o = R(x, y, w, h, '#1F4E7A');
      for (let i = 1; i < 4; i++) o += `<path d="M${n(x + w * i / 4)} ${n(y)} V${n(y + h)} M${n(x)} ${n(y + h * i / 4)} H${n(x + w)}" stroke="#3C6E9C" stroke-width="${n(h * .03)}"/>`;
      return o + `<path class="draw" pathLength="1" d="M${n(x + w * .2)} ${n(y + h * .8)} H${n(x + w * .8)} V${n(y + h * .35)} L${n(x + w * .5)} ${n(y + h * .15)} L${n(x + w * .2)} ${n(y + h * .35)} Z" fill="none" stroke="#FFFFFF" stroke-width="${n(h * .06)}" stroke-linejoin="round"/>`;
    },
    doc(x, y, w, h) {   // the spec being typed
      let o = R(x, y, w, h, '#F4F2EC') + R(x + w * .1, y + h * .12, w * .45, h * .1, '#4F6B7C');
      for (let i = 0; i < 4; i++) o += R(x + w * .1, y + h * (.34 + i * .15), w * [.78, .7, .8, .5][i], h * .06, '#B4B2A9', i === 3 ? ' class="typ"' : '');
      return o;
    },
    grid(x, y, w, h) {   // the parts library: a grid of thumbnails
      let o = R(x, y, w, h, '#1C2129');
      const tw = w * .26, th = h * .36;
      for (let r = 0; r < 2; r++) for (let q = 0; q < 3; q++) {
        const tx = x + w * (.06 + q * .31), ty = y + h * (.1 + r * .45), i = r * 3 + q;
        o += R(tx, ty, tw, th, '#3A4250', ` class="tile" style="animation-delay:${i * .4}s"`) + `<circle cx="${n(tx + tw / 2)}" cy="${n(ty + th / 2)}" r="${n(th * .28)}" fill="${MATS[i]}"/>`;
      }
      return o;
    },
    timeline(x, y, w, h) {   // the animator's timeline: a wheel turning over its keyframes
      const r = h * .22, cx = x + w * .5, cy = y + h * .36, ty = y + h * .8;
      return R(x, y, w, h, '#161B22') + `<g class="rot"><circle cx="${n(cx)}" cy="${n(cy)}" r="${n(r)}" fill="none" stroke="#E6EEF6" stroke-width="${n(h * .05)}"/><path d="M${n(cx)} ${n(cy - r)} V${n(cy + r)} M${n(cx - r)} ${n(cy)} H${n(cx + r)}" stroke="#E6EEF6" stroke-width="${n(h * .04)}"/></g>` +
        `<path d="M${n(x + w * .06)} ${n(ty)} H${n(x + w * .94)}" stroke="#4C7DF7" stroke-width="${n(h * .05)}"/>` +
        [.2, .45, .7, .88].map(u => `<rect x="${n(x + w * u - h * .05)}" y="${n(ty - h * .05)}" width="${n(h * .1)}" height="${n(h * .1)}" transform="rotate(45 ${n(x + w * u)} ${n(ty)})" fill="#EF9F27"/>`).join('') +
        R(x + w * .06, y + h * .62, h * .05, h * .32, '#F2EFE8', ` class="ph" style="--d:${n(w * .86)}px"`);
    },
    fit(x, y, w, h) {   // the assembled model, its joints checked one by one
      const k = h / 54, X = x + w * .42, Y = y + h * .86;
      return R(x, y, w, h, '#1E2329') + `<g transform="translate(${n(X)} ${n(Y)}) scale(${n(k)})">${art(0, 0, 'side', '#C9CDD2')}</g>` +
        [[-8, -16], [8, -16], [0, 2], [10, -36]].map(([u, v], i) => `<circle class="tick" style="animation-delay:${i * .5}s" cx="${n(X + u * k)}" cy="${n(Y + v * k)}" r="${n(3.2 * k)}" fill="none" stroke="#6FD39A" stroke-width="${n(1.4 * k)}"/>`).join('') +
        R(x + w * .74, y + h * .2, w * .18, h * .1, '#6FD39A') + R(x + w * .74, y + h * .38, w * .14, h * .1, '#6FD39A') + R(x + w * .74, y + h * .56, w * .16, h * .1, '#EF9F27');
    },
    dash(x, y, w, h) {   // the director's dashboard
      let o = R(x, y, w, h, '#1A1F27');
      [.5, .8, .35, .65, .9].forEach((v, i) => { o += R(x + w * (.1 + i * .16), y + h * (.85 - v * .65), w * .1, h * v * .65, i === 4 ? '#4C9BF7' : '#3E6FA3', ` class="bar" style="animation-delay:-${i * .35}s"`); });
      return o + `<path d="M${n(x + w * .08)} ${n(y + h * .87)} H${n(x + w * .92)}" stroke="#5B6168" stroke-width="${n(h * .03)}"/>`;
    },
    chat(x, y, w, h) {   // a chat, messages coming in
      let o = R(x, y, w, h, '#F4F2EC');
      [[.08, .12, .5, '#DDE3EB'], [.4, .38, .52, '#4C7DF7'], [.08, .64, .62, '#DDE3EB']].forEach(([u, v, bw, c], i) => { o += R(x + w * u, y + h * v, w * bw, h * .18, c, ` rx="${n(h * .06)}" class="tick" style="animation-delay:${i * .7}s"`); });
      return o;
    },
    gpu(x, y, w, h) {   // the GPU console: a load bar per card
      let o = R(x, y, w, h, '#10161D');
      for (let i = 0; i < 4; i++) o += R(x + w * (.1 + i * .21), y + h * .15, w * .14, h * .7, '#1F2A36') + R(x + w * (.1 + i * .21), y + h * .15, w * .14, h * .7, '#39D98A', ` class="bar" style="animation-delay:-${i * .4}s"`);
      return o;
    },
    parts(x, y, w, h) {   // the build board: one row per part with its progress
      let o = R(x, y, w, h, '#1A1F27');
      for (let i = 0; i < 5; i++) {
        const yy = y + h * (.08 + i * .18);
        o += R(x + w * .08, yy, w * .2, h * .1, '#7FB2F0') + R(x + w * .34, yy + h * .02, w * .58, h * .06, '#2E3640') +
          R(x + w * .34, yy + h * .02, w * .58 * [1, 1, .7, .4, .1][i], h * .06, i < 2 ? '#39D98A' : '#4C9BF7', i === 2 || i === 3 ? ' class="typ"' : '');
      }
      return o;
    },
    notes(x, y, w, h) {   // the brief's board: notes on a touch screen
      let o = R(x, y, w, h, '#20252C');
      const c = ['#F5E08A', '#F2B8A0', '#A8D8C8', '#F5E08A', '#BFD8E0', '#F2B8A0'];
      for (let i = 0; i < 6; i++) o += R(x + w * (.08 + (i % 2) * .48), y + h * (.06 + Math.floor(i / 2) * .32), w * .38, h * .25, c[i], i === 5 ? ' class="tick"' : '');
      return o;
    },
    steps(x, y, w, h) {   // the team dashboard: the run's steps, one lighting up after another
      let o = R(x, y, w, h, '#1A1F27') + `<path d="M${n(x + w * .1)} ${n(y + h * .48)} H${n(x + w * .9)}" stroke="#3A404A" stroke-width="${n(h * .04)}"/>`;
      for (let i = 0; i < 6; i++) {
        const cx = n(x + w * (.1 + i * .16)), cy = n(y + h * .48), r = n(h * .1);
        o += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#3A404A"/><circle class="tick" style="animation-delay:${i * .8}s" cx="${cx}" cy="${cy}" r="${r}" fill="#39D98A"/>`;
      }
      return o + R(x + w * .1, y + h * .74, w * .5, h * .08, '#5B6168') + R(x + w * .1, y + h * .14, w * .3, h * .1, '#9AA3AB');
    },
    show(x, y, w, h) {   // the showcase: the model turning on a turntable
      const k = h / 64;
      return R(x, y, w, h, '#20262E') + R(x, y + h * .66, w, h * .34, '#2A313B') + `<ellipse cx="${n(x + w / 2)}" cy="${n(y + h * .82)}" rx="${n(w * .2)}" ry="${n(h * .06)}" fill="#3A4250"/>` +
        `<g class="spin"><g transform="translate(${n(x + w / 2)} ${n(y + h * .8)}) scale(${n(k)})">${art(0, 0, 'side', '#D9B07A')}</g></g>` + R(x + w * .05, y + h * .08, w * .2, h * .07, '#9AA3AB');
    },
    vr(x, y, w, h) {   // the VR view: the model once for each eye
      const k = h / 72, eye = u => `<ellipse cx="${n(x + w * u)}" cy="${n(y + h / 2)}" rx="${n(w * .22)}" ry="${n(h * .42)}" fill="#253040"/><g transform="translate(${n(x + w * u)} ${n(y + h * .78)}) scale(${n(k)})">${art(0, 0, 'side', '#D9B07A')}</g>`;
      return R(x, y, w, h, '#151A20') + eye(.27) + eye(.73);
    }
  };

  // The screensaver: the ModelGen cube, breathing.
  function idle(x, y, w, h) {
    const c = Math.min(w, h) * .24, cx = x + w / 2, cy = y + h / 2;
    return R(x, y, w, h, '#18202A') + `<path class="ss" d="M${n(cx)} ${n(cy - c)} L${n(cx + c)} ${n(cy - c / 2)} V${n(cy + c / 2)} L${n(cx)} ${n(cy + c)} L${n(cx - c)} ${n(cy + c / 2)} V${n(cy - c / 2)} Z M${n(cx - c)} ${n(cy - c / 2)} L${n(cx)} ${n(cy)} L${n(cx + c)} ${n(cy - c / 2)} M${n(cx)} ${n(cy)} V${n(cy + c)}" fill="none" stroke="#4C7DF7" stroke-width="${n(c * .16)}" stroke-linejoin="round"/>`;
  }
  // A full-screen sign: ? (needs you), x (failed) or a check (done).
  function sign(x, y, w, h, bg, fg, mark) {
    const c = Math.min(w, h) * .28, cx = x + w / 2, cy = y + h / 2;
    const d = mark === '?'
      ? `M${n(cx - c * .6)} ${n(cy - c * .45)} Q${n(cx - c * .6)} ${n(cy - c)} ${n(cx)} ${n(cy - c)} Q${n(cx + c * .65)} ${n(cy - c)} ${n(cx + c * .6)} ${n(cy - c * .4)} Q${n(cx + c * .55)} ${n(cy)} ${n(cx)} ${n(cy + c * .1)} V${n(cy + c * .45)} M${n(cx)} ${n(cy + c * .85)} V${n(cy + c * .9)}`
      : mark === 'x'
        ? `M${n(cx - c)} ${n(cy - c)} L${n(cx + c)} ${n(cy + c)} M${n(cx + c)} ${n(cy - c)} L${n(cx - c)} ${n(cy + c)}`
        : `M${n(cx - c)} ${n(cy)} L${n(cx - c * .25)} ${n(cy + c * .75)} L${n(cx + c)} ${n(cy - c * .8)}`;
    return R(x, y, w, h, bg) + `<path d="${d}" fill="none" stroke="${fg}" stroke-width="${n(c * .3)}" stroke-linecap="round" stroke-linejoin="round"/>`;
  }
  // A screen for a box: an owner's screen holds a layer for each state and studio.js shows
  // one of them; a screen without an owner always shows its kind.
  const screen = (owner, kind) => (x, y, w, h) => owner
    ? `<g class="scr s-idle" data-o="${owner}"><g class="l-idle">${idle(x, y, w, h)}</g><g class="l-working">${KINDS[kind](x, y, w, h)}</g>` +
      `<g class="l-waiting">${sign(x, y, w, h, '#3B2C10', '#F0BE6C', '?')}</g><g class="l-failed">${sign(x, y, w, h, '#3A1614', '#F0948B', 'x')}</g><g class="l-done">${sign(x, y, w, h, '#132E1F', '#6FD39A', 'v')}</g></g>`
    : `<g class="deco">${KINDS[kind](x, y, w, h)}</g>`;

  // ---- desks, monitors, chairs ---------------------------------------------------------
  // A monitor standing on a desk top at y.
  function monitor(cx, y, w, h, k, scr) {
    const top = y - 5 * k - h, b = 1.4 * k;
    return R(cx - 1.8 * k, y - 5 * k - 1, 3.6 * k, 5 * k + 1, '#4A5058') + R(cx - 7 * k, y - 1.4 * k, 14 * k, 1.4 * k, '#4A5058', ` rx="${n(.7 * k)}"`) +
      R(cx - w / 2, top, w, h, '#1E2228', ` rx="${n(1.4 * k)}"`) + scr(cx - w / 2 + b, top + b, w - 2 * b, h - 2 * b);
  }
  // A desk w wide whose legs stand on y; onTop(top, k) draws what stands on it.
  function desk(x, y, w, k, onTop) {
    const top = y - 22 * k, t = 3.2 * k;
    return R(x - w / 2 + 3 * k, top + t, 2.6 * k, y - top - t, '#6E747B') + R(x + w / 2 - 5.6 * k, top + t, 2.6 * k, y - top - t, '#6E747B') +
      R(x - w / 2 + 3 * k, top + t + 8 * k, w - 6 * k, 1.6 * k, '#8A9097') +
      R(x - w / 2, top, w, t, '#E4DED3', ` rx="${n(.8 * k)}"`) + R(x - w / 2, top + t, w, 1.4 * k, '#C9C2B4') + (onTop ? onTop(top, k) : '');
  }
  // An office chair from behind; its back hides a seated character's lower back.
  function officeChair(x, y, k) {
    return `<path d="M${n(x - 13 * k)} ${n(y)} H${n(x + 13 * k)} M${n(x)} ${n(y - 1)} V${n(y - 10 * k)}" stroke="#2B2F35" stroke-width="${n(2.2 * k)}" stroke-linecap="round"/>` +
      [-13, 0, 13].map(u => `<circle cx="${n(x + u * k)}" cy="${n(y + .8 * k)}" r="${n(1.6 * k)}" fill="#1E2228"/>`).join('') +
      R(x - 14 * k, y - 12.5 * k, 28 * k, 4 * k, '#2B2F35', ` rx="${n(2 * k)}"`) +
      R(x - 11 * k, y - 30 * k, 22 * k, 19 * k, '#3A3F47', ` rx="${n(5 * k)}"`) + R(x - 8 * k, y - 27 * k, 16 * k, 2 * k, '#FFFFFF', ` opacity=".08" rx="${n(k)}"`);
  }
  // A workstation for someone seated at (x, y): the desk behind them with its monitors
  // ([dx, w, h, screen] in back-row units), and the chair in front of them.
  function workstation(x, y, w, mons) {
    const k = K(y), fy = y - 10 * k;
    return [
      [fy, desk(x, fy, w * k, k, top => mons.map(([dx, mw, mh, s]) => monitor(x + dx * k, top, mw * k, mh * k, k, s)).join(''))],
      [y + 1, officeChair(x, y, k)]
    ];
  }
  // The modeling bay: one long desk with two monitors per builder seat.
  const BAY = [920, 840, 1000, 1080];   // seat 0 to 3
  function bay(y) {
    const k = K(y), fy = y - 10 * k;
    return [[fy, desk(960, fy, 340, k, top => BAY.map((x, i) => monitor(x - 15 * k, top, 28 * k, 22 * k, k, screen('seat' + i, 'wire')) + monitor(x + 15 * k, top, 28 * k, 22 * k, k, screen('seat' + i, 'code'))).join(''))]]
      .concat(BAY.map(x => [y + 1, officeChair(x, y, k)]));
  }
  // A screen on a pole: the foot on y, the screen leg units above it.
  const kiosk = (x, y, w, h, leg, scr) => R(x - 1.6, y - leg, 3.2, leg, '#6E747B') + R(x - w * .3, y - 2, w * .6, 2, '#6E747B', ' rx="1"') +
    R(x - w / 2, y - leg - h, w, h, '#1E2228', ' rx="2"') + scr(x - w / 2 + 2, y - leg - h + 2, w - 4, h - 4);

  // ---- the room shell: walls, floors, and the screens mounted on the back wall ---------------
  function reviewWall() {
    return R(58, 30, 232, 112, '#1E2228', ' rx="4"') + R(64, 36, 220, 100, '#2A3038') + R(64, 104, 220, 32, '#323945') +
      `<ellipse cx="140" cy="124" rx="44" ry="6" fill="#3A4250"/><g transform="translate(140 120) scale(1.8)">${art(0, 0, 'side', '#D9B07A')}</g>` +
      R(206, 60, 64, 4, '#3A404A') + R(206, 60, 46, 4, '#6FD39A') + R(206, 72, 64, 4, '#3A404A') + R(206, 72, 30, 4, '#EF9F27') + R(206, 84, 64, 4, '#3A404A') + R(206, 84, 52, 4, '#6FD39A') +
      R(70, 42, 26, 10, '#4C7DF7', ' rx="2"') + R(64, 36, 220, 2, '#BFD8E0', ' class="scan" style="--d:98px" opacity=".3"');
  }
  function materialWall() {
    let o = R(620, 34, 160, 104, '#1E2228', ' rx="4"') + R(625, 39, 150, 94, '#22272E');
    MATS.forEach((c, i) => {
      const cx = 650 + (i % 3) * 50, cy = 55 + Math.floor(i / 3) * 31;
      o += `<circle cx="${cx}" cy="${cy}" r="11" fill="${c}"/><circle cx="${cx - 4}" cy="${cy - 4}" r="3.2" fill="#FFF" opacity=".45"/><circle class="hl" style="animation-delay:${n(i * .45)}s" cx="${cx}" cy="${cy}" r="14" fill="none" stroke="#4C9BF7" stroke-width="1.6"/>`;
    });
    return o;
  }
  function renderWall() {
    let o = R(880, 30, 160, 110, '#1E2228', ' rx="4"');
    ['front', 'side', 'top'].forEach((v, i) => { const X = 886 + i * 50; o += R(X, 36, 48, 80, '#2B3038') + T(X + 24, 104, art(0, 0, v, '#D9C3A0')); });
    return o + R(886, 120, 148, 14, '#22272E') + R(890, 125, 100, 4, '#3A404A') + R(890, 125, 100, 4, '#6FD39A', ' class="grow"') + R(1000, 123, 30, 8, '#4C7DF7', ' rx="2"');
  }

  function roomShell() {
    let planks = '';
    for (let y = 204; y < 680; y += y < 300 ? 36 : 44) planks += `<line x1="0" y1="${y}" x2="1680" y2="${y}" stroke="var(--plank)" stroke-width="1"/>`;
    let grid = '';
    for (let x = 1340; x < 1664; x += 24) grid += `<line x1="${x}" y1="172" x2="${x}" y2="285" stroke="var(--tile)"/>`;
    for (let y = 196; y < 285; y += 24) grid += `<line x1="1340" y1="${y}" x2="1664" y2="${y}" stroke="var(--tile)"/>`;
    const win = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="2" fill="var(--frame)"/><rect x="${x + 4}" y="${y + 4}" width="${(w - 12) / 2}" height="${h - 8}" fill="var(--sky)"/><rect x="${x + 8 + (w - 12) / 2}" y="${y + 4}" width="${(w - 12) / 2}" height="${h - 8}" fill="var(--sky)"/>`;
    return `<rect width="1680" height="172" fill="var(--wall)"/><rect y="172" width="1680" height="508" fill="var(--floor)"/>${planks}<rect y="166" width="1680" height="6" fill="var(--base)"/>
<rect x="16" y="172" width="314" height="113" fill="var(--carpet)"/><rect x="1340" y="172" width="324" height="113" fill="var(--tilefloor)"/>${grid}
<rect x="20" y="492" width="310" height="158" fill="var(--carpet)"/><rect x="1348" y="492" width="316" height="158" fill="var(--tilefloor)"/>
${reviewWall()}${materialWall()}${renderWall()}${R(1288, 100, 42, 36, '#1E2228', ' rx="2"')}${KINDS.render(1290, 102, 38, 32)}
${win(1135, 16, 146, 108)}
<rect x="642" y="336" width="330" height="76" rx="8" fill="var(--rug)"/><rect x="650" y="343" width="314" height="62" rx="6" fill="none" stroke="var(--rugline)" stroke-width="2"/>
<rect x="1450" y="372" width="140" height="30" rx="6" fill="var(--mat)"/><rect x="1456" y="376" width="128" height="22" rx="4" fill="none" stroke="var(--matline)" stroke-width="1.5" stroke-dasharray="6 4"/>
<g class="sun"><path d="M1155 250 L1285 250 L1335 330 L1205 330 Z"/></g>
<rect x="334" y="62" width="4" height="223" fill="var(--glass)" opacity=".4"/><rect x="334" y="62" width="4" height="3" fill="var(--frame)"/><rect x="1338" y="62" width="4" height="223" fill="var(--glass)" opacity=".4"/><rect x="1338" y="62" width="4" height="3" fill="var(--frame)"/>
${FRAME(20, 402, 248)}${FRAME(120, 402, 248)}${FRAME(225, 402, 248)}${FRAME(328, 402, 248)}${FRAME(1346, 402, 248)}${FRAME(1662, 402, 248)}`;
  }

  // ---- machines and props ----------------------------------------------------------------
  // The asset library's storage tower: drive bays with their lights.
  const nas = R(362, 132, 32, 84, '#2B2F35', ' rx="2"') + R(365, 136, 26, 76, '#1E2228') + Array.from({ length: 8 }, (_, i) => {
    const X = 367 + (i % 2) * 12, Y = 140 + Math.floor(i / 2) * 18;
    return R(X, Y, 10, 15, '#3A3F47', ' rx="1"') + R(X + 3, Y + 11, 4, 1.6, i % 3 ? '#39D98A' : '#4C9BF7', ` class="led" style="animation-delay:-${n(i * .37)}s"`);
  }).join('');
  // The GPU room: four GPU servers (a glowing strip and two fans per card) and two drive racks.
  const racks = [1358, 1410, 1462, 1514, 1566, 1618].map((X, i) => {
    let o = R(X, 70, 46, 162, '#23272D', ' rx="2"') + R(X + 4, 76, 38, 150, '#16191E');
    if (i < 4) for (let r = 0; r < 6; r++) {
      const Y = 80 + r * 24;
      o += R(X + 6, Y, 34, 19, '#2B3037', ' rx="1"') + R(X + 9, Y + 3, 18, 2.5, LEDC[(r + i) % 2], ' class="glow"') +
        `<circle cx="${X + 14}" cy="${Y + 12}" r="4" fill="none" stroke="#454C56" stroke-width="1.5"/><circle cx="${X + 26}" cy="${Y + 12}" r="4" fill="none" stroke="#454C56" stroke-width="1.5"/>` +
        R(X + 34, Y + 3, 3, 3, LEDC[(r + i) % 4], ` class="led" style="animation-delay:-${((r * 7 + i * 3) % 10) / 5}s"`);
    } else for (let r = 0; r < 12; r++) {
      const Y = 80 + r * 12;
      o += R(X + 6, Y, 34, 9, '#2B3037', ' rx="1"') + R(X + 9, Y + 3.5, 14, 1.6, '#454C56') + R(X + 32, Y + 3, 3, 3, r % 3 ? '#39D98A' : '#EF9F27', ` class="led" style="animation-delay:-${((r * 5 + i) % 10) / 5}s"`);
    }
    return o;
  }).join('');
  // The lab: enclosed 3D printers with a part growing layer by layer, the scanner, the prototypes.
  const printer = (X, d) => R(X, 486, 50, 66, '#2B2F35', ' rx="3"') + R(X + 4, 494, 42, 44, '#BFD8E0', ' opacity=".28"') + R(X + 4, 498, 42, 1.5, '#6E747B') +
    R(X + 8, 532, 34, 3, '#6E747B') + R(X + 17, 516, 16, 16, '#EF9F27', ` class="print" style="animation-delay:-${d}s"`) +
    `<g class="noz" style="--d:26px;animation-delay:-${d / 4}s">${R(X + 8, 499, 8, 6, '#C9CDD2')}${R(X + 11, 505, 2, 3, '#9AA3AB')}</g>` +
    R(X + 6, 542, 14, 6, '#1D9E75') + `<circle class="led" cx="${X + 42}" cy="545" r="1.8" fill="#39D98A"/>`;
  const scanner = R(1532, 528, 60, 4, '#E4DED3') + R(1536, 532, 3, 24, '#6E747B') + R(1585, 532, 3, 24, '#6E747B') +
    '<ellipse cx="1561" cy="527" rx="20" ry="4.5" fill="#9AA3AB"/><ellipse cx="1561" cy="525.5" rx="18" ry="3.5" fill="#C9CDD2"/>' +
    `<g class="spin">${T(1561, 523, `<g transform="scale(.45)">${art(0, 0, 'side', '#E8E2D6')}</g>`)}</g>` +
    R(1604, 478, 3, 78, '#6E747B') + R(1590, 478, 17, 3, '#6E747B') + R(1580, 474, 14, 10, '#2B2F35', ' rx="2"') + '<circle cx="1583" cy="479" r="2.6" fill="#4C9BF7"/>' +
    '<path class="sweep" d="M1581 479 L1546 506 L1576 506 Z" fill="#E0605A" opacity=".2"/>';
  const shelf = R(1614, 470, 2.5, 82, '#8A9097') + R(1655, 470, 2.5, 82, '#8A9097') + [494, 520, 548].map(Y => R(1614, Y, 43.5, 2.5, '#8A9097')).join('') +
    T(1627, 493, `<g transform="scale(.3)">${art(0, 0, 'front', '#EF9F27')}</g>`) + R(1637, 484, 15, 6, '#4C9BF7', ' rx="2"') + '<circle cx="1641" cy="491" r="2.4" fill="#2B2F35"/><circle cx="1649" cy="491" r="2.4" fill="#2B2F35"/>' +
    R(1620, 510, 9, 10, '#E8E2D6') + '<path d="M1636 520 Q1632 512 1636 506 H1642 Q1646 512 1642 520 Z" fill="#6FD39A"/>' + R(1648, 512, 6, 8, '#C98A5A', ' rx="1"') +
    '<circle cx="1626" cy="542" r="5" fill="#B5824E"/>' + R(1636, 538, 16, 10, '#9C7FB0', ' rx="2"');
  // The VR zone: tracking beacons, the headset on its stand.
  const beacon = X => R(X - 1.2, 316, 2.4, 72, '#6E747B') + R(X - 8, 386, 16, 2, '#6E747B', ' rx="1"') + R(X - 6, 306, 12, 10, '#2B2F35', ' rx="2"') + `<circle class="led" cx="${X}" cy="311" r="2" fill="#E0605A"/>`;
  const headset = R(1368, 356, 14, 32, '#D9D4C9') + R(1362, 352, 26, 4, '#E8E2D6', ' rx="1"') + R(1364, 342, 22, 10, '#2B2F35', ' rx="4"') +
    R(1367, 345, 16, 4, '#4C9BF7', ' rx="2" opacity=".85"') + '<path d="M1364 347 Q1360 336 1375 335 Q1390 336 1386 347" fill="none" stroke="#3A3F47" stroke-width="2"/>';
  const stool = X => `<ellipse cx="${X}" cy="384" rx="9" ry="3" fill="#4F6B7C"/>${R(X - 1.5, 385, 3, 13, '#6E747B')}<ellipse cx="${X}" cy="398" rx="7" ry="2" fill="#6E747B"/>`;
  const armchair = x => `<rect x="${x}" y="350" width="40" height="26" rx="6" fill="#6E9A8A"/><rect x="${x}" y="370" width="40" height="16" rx="4" fill="#5E8A7A"/><rect x="${x - 4}" y="362" width="8" height="24" rx="3" fill="#5E8A7A"/><rect x="${x + 36}" y="362" width="8" height="24" rx="3" fill="#5E8A7A"/>`;
  const laptop = x => R(x - 9, 228, 18, 12, '#C9CDD2', ' rx="1.5"') + R(x - 10, 239, 20, 2, '#9AA3AB') + `<circle cx="${x}" cy="234" r="1.6" fill="#FFFFFF" opacity=".7"/>`;

  // [depth key, svg]
  const FURNITURE = [
    // back row: asset library, material lab, critic desk, planning station
    [216, nas],
    ...workstation(450, 226, 90, [[-17, 32, 22, screen('historia', 'grid')], [17, 32, 22, screen('historia', 'code')]]),
    [232, plant(592, 232, 1)],
    ...workstation(700, 226, 90, [[-17, 32, 22, screen('reiner', 'sphere')], [17, 32, 22, screen('reiner', 'nodes')]]),
    ...workstation(960, 226, 90, [[-17, 32, 22, screen('annie', 'render')], [17, 32, 22, screen('annie', 'check')]]),
    ...workstation(1205, 226, 96, [[-20, 20, 32, screen('pieck', 'tree')], [17, 34, 18, screen('pieck', 'blue')]]),
    // review room: chairs along the far side, the table with laptops, then the glass front with its door
    [240, [76, 121, 166, 211, 256].map(x => R(x - 11, 220, 22, 20, '#3A3F47', ' rx="5"') + R(x - 8, 223, 16, 2, '#FFFFFF', ' opacity=".08"')).join('')],
    [262, R(46, 240, 254, 8, '#E8E4DC') + R(46, 248, 254, 12, '#B9B4AA') + [52, 170, 289].map(x => R(x, 260, 5, 12, '#8A9097')).join('') + laptop(96) + laptop(196) + laptop(262) + '<ellipse cx="146" cy="238" rx="9" ry="3" fill="#3A3F47"/>'],
    [285, pane(16, 330, 180, 285, [[272, 314]])],
    // GPU room: the racks, the console, the glass front
    [232, `<g class="farm">${racks}</g>`],
    [250, `<g class="gpuc">${desk(1512, 250, 70, K(262), (top, k) => monitor(1512, top, 34 * k, 22 * k, k, screen(null, 'gpu')))}</g>`],
    [285, pane(1340, 1664, 180, 285, [[1344, 1386]])],
    // middle: coffee bar, lounge with three armchairs and a high table, water cooler, team dashboard
    [396, T(OX + 120, 0, `<rect x="30" y="352" width="120" height="8" fill="#E8E2D6"/><rect x="32" y="360" width="116" height="36" fill="#B8A48C"/><rect x="32" y="366" width="116" height="3" fill="#A8947C"/><rect x="48" y="324" width="30" height="28" rx="2" fill="#9AA3AB"/><rect x="48" y="324" width="30" height="7" rx="2" fill="#7B8188"/><rect x="58" y="338" width="10" height="3" fill="#3A3D42"/><rect x="59" y="345" width="7" height="7" rx="1" fill="#F2EFE8"/><rect x="90" y="344" width="7" height="8" rx="1" fill="#B85C5C"/><rect x="100" y="344" width="7" height="8" rx="1" fill="#4F6B7C"/><rect x="116" y="336" width="22" height="16" rx="2" fill="#D9D4C9"/>`)],
    [386, T(OX + 160, 0, armchair(245) + armchair(395) + armchair(490))],
    [395, T(OX + 160, 0, `<ellipse cx="340" cy="393" rx="12" ry="3" fill="#6E747B"/><rect x="337" y="364" width="6" height="29" fill="#6E747B"/><ellipse cx="340" cy="362" rx="26" ry="7" fill="#E8E2D6"/><rect x="330" y="355" width="6" height="7" rx="1" fill="#F2EFE8"/><rect x="346" y="356" width="6" height="6" rx="1" fill="#C98A5A"/>`)],
    [396, T(OX + 100, 0, `<rect x="640" y="358" width="20" height="38" rx="2" fill="#E6E3DD"/><rect x="642" y="334" width="16" height="25" rx="5" fill="#BFD8E0"/><rect x="646" y="370" width="8" height="4" rx="1" fill="#5B8BB0"/>`)],
    [396, R(1108, 384, 3, 12, '#6E747B') + R(1189, 384, 3, 12, '#6E747B') + R(1100, 394, 16, 2, '#6E747B') + R(1182, 394, 16, 2, '#6E747B') + R(1098, 326, 104, 60, '#1E2228', ' rx="3"') + screen(null, 'steps')(1102, 330, 96, 52)],
    [408, plant(1235, 408, 1.1)],
    // showcase in the left wing: the latest model turning on a big screen, two stools
    [400, R(106, 388, 3, 12, '#6E747B') + R(211, 388, 3, 12, '#6E747B') + R(98, 398, 18, 2, '#6E747B') + R(203, 398, 18, 2, '#6E747B') + R(90, 312, 140, 78, '#1E2228', ' rx="4"') + screen(null, 'show')(95, 317, 130, 68)],
    [398, stool(46) + stool(274)],
    // VR test zone in the right wing
    [388, beacon(1436) + beacon(1606) + headset + kiosk(1640, 388, 40, 30, 38, screen(null, 'vr'))],
    // focus pods: three pods behind glass, each with its own door; the right one is the animation suite
    [492, pane(20, 330, 402, 492, [[48, 88], [153, 193], [258, 298]])],
    ...workstation(68, 556, 66, [[0, 34, 22, screen(null, 'code')]]),
    ...workstation(173, 556, 66, [[0, 34, 22, screen(null, 'grid')]]),
    ...workstation(278, 556, 74, [[-6, 46, 22, screen('jean', 'timeline')], [28, 16, 22, screen('jean', 'render')]]),
    [650, pane(20, 330, 545, 650)],
    // scan & print lab
    [492, pane(1348, 1664, 402, 492, [[1355, 1395]])],
    [552, printer(1408, 0) + printer(1466, 5)],
    [556, scanner],
    [552, shelf],
    [650, pane(1348, 1664, 545, 650)],
    // front row: director desk, the brief's touch board and desk, the modeling bay with its build board, assembly
    [552, plant(392, 552, .8)],
    ...workstation(470, 540, 96, [[-17, 32, 22, screen('hange', 'dash')], [17, 32, 22, screen('hange', 'chat')]]),
    [548, kiosk(610, 548, 46, 58, 50, screen(null, 'notes'))],
    ...workstation(700, 540, 90, [[-17, 32, 22, screen('armin', 'doc')], [17, 32, 22, screen('armin', 'render')]]),
    ...bay(540),
    [528, kiosk(1160, 528, 40, 54, 30, screen(null, 'parts'))],
    ...workstation(1250, 540, 100, [[0, 64, 24, screen('mikasa', 'fit')]]),
    [612, plant(1318, 612, 1)]
  ];

  const LABELS = [
    [173, 160, 'Review room'], [1502, 52, 'GPU room'],
    [450, 292, 'Asset library'], [700, 292, 'Material lab'], [960, 292, 'Critic desk'], [1205, 292, 'Planning'],
    [160, 432, 'Showcase'], [470, 432, 'Coffee bar'], [800, 432, 'Lounge'], [1150, 432, 'Team dashboard'], [1520, 424, 'VR test zone'],
    [470, 606, 'Director'], [690, 606, 'Brief desk'], [960, 606, 'Modeling bay'], [1250, 606, 'Assembly bay'],
    [120, 668, 'Focus pods'], [278, 668, 'Animation suite'], [1506, 668, 'Scan &amp; print lab']
  ];

  // Rooms: the door each room is entered by, and the aisle just inside it.
  const DOORS = { meet: { x: 293, ai: 278 }, farm: { x: 1365, ai: 276 }, lab: { x: 1375, ai: 566 }, bA: { x: 68, ai: 584 }, bB: { x: 173, ai: 584 }, bC: { x: 278, ai: 584 } };
  // Bounds used when a room is double-clicked to zoom in.
  const ROOMS = [
    { name: 'Review room', x: 10, y: 20, w: 330, h: 280 }, { name: 'GPU room', x: 1330, y: 30, w: 344, h: 270 },
    { name: 'Focus pods', x: 10, y: 380, w: 330, h: 290 }, { name: 'Scan & print lab', x: 1340, y: 380, w: 334, h: 290 },
    { name: 'Open studio', x: 340, y: 20, w: 1000, h: 650 }
  ];
  // Where the walkways meet: gaps between the middle-zone furniture.
  const CORRIDORS = [295, 565, 1045, 1300];
  const lane = y => y < 386 ? 300 : 470;          // the two walkways

  // A character's spots around their workstation: seated at it (work), standing in front of
  // it (home), and where a visitor stands. Working sends a character to work, unless their
  // tool has a spot of its own below.
  function desked(x, y, door, tools) {
    const s = { work: [x, y, 'b', door || null, 'sit'], home: [x, y + 22, 'f', door || null], visit: [x + 44, y + 12, 'f', door || null] };
    s.tools = tools(s.work);
    return s;
  }
  const STATIONS = {
    historia: desked(450, 226, null, w => ({ search_library: w, read_part: [378, 228, 'b'], save_part: [1590, 262, 'b', 'farm'], archive_version: [1642, 272, 'b', 'farm'] })),
    reiner: desked(700, 226, null, w => ({ make_material: w, pick_material: [640, 230, 'b'] })),
    annie: desked(960, 226, null, () => ({ view_renders: [150, 268, 'b', 'meet'], render_views: [1512, 262, 'b', 'farm'] })),
    pieck: desked(1205, 226, null, w => ({ plan_parts: w, fetch_plans: [1309, 232, 'b'], print_plan: [1433, 566, 'b', 'lab'] })),
    hange: desked(470, 540, null, w => ({ check_queue: w })),
    armin: desked(700, 540, null, () => ({ pin_note: [610, 556, 'b'] })),
    mikasa: desked(1250, 540, null, w => ({ check_fit: w })),
    jean: desked(278, 556, 'bC', w => ({ motion_desk: w, preview_frames: w }))
  };
  // Modeling bay seats for parallel builders: they read parts and model at their own computer.
  const SEATS = BAY.map(x => desked(x, 540, null, w => ({ read_parts: w, run_blender: w })));
  // Spots everyone shares between tasks.
  const SHARED = [
    [458, 410, 'b'], [498, 410, 'b'], [720, 410, 'f'], [800, 410, 'f'], [965, 410, 'f'], [1000, 412, 'b'], [1150, 410, 'b'],
    [70, 392, 'r'], [250, 392, 'l'],                                              // watching the showcase
    [1500, 388, 'f', null, 'vr'], [1548, 388, 'f', null, 'vr'], [1640, 400, 'b'],  // trying the model in VR, or watching
    [68, 556, 'b', 'bA', 'sit'], [173, 556, 'b', 'bB', 'sit'],                    // the two quiet pods; the third is the animator's
    [1491, 566, 'b', 'lab'], [1561, 566, 'b', 'lab']                              // the second printer and the scanner
  ];
  const MEETING = [[87, 278, 'b', 'meet'], [132, 278, 'b', 'meet'], [177, 278, 'b', 'meet'], [222, 278, 'b', 'meet'], [267, 278, 'b', 'meet'], [26, 258, 'f', 'meet'], [320, 258, 'f', 'meet']];

  MG.world = { roomShell, FURNITURE, LABELS, DOORS, ROOMS, CORRIDORS, lane, STATIONS, SEATS, SHARED, MEETING, scaleAt, W: 1680, H: 680 };
})();
