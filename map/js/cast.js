// The cast: nine characters on one shared rig. Each character is data (colours, hair,
// clothes); the rig functions turn that data into front, side and back SVG.
// Every view is drawn around the feet at (0, 0), facing the viewer (front), facing
// right (side; mirrored for left) or facing away (back). Joints are groups with a
// transform-origin, so the CSS in studio.css can pose and animate them.
window.MG = window.MG || {};
(function () {
  'use strict';

  const PUFF = '<g fill="#2A1C16"><circle cx="-16" cy="-184" r="13"/><circle cx="0" cy="-192" r="14"/><circle cx="16" cy="-184" r="13"/><circle cx="-22" cy="-170" r="10"/><circle cx="22" cy="-170" r="10"/><circle cx="-9" cy="-198" r="9"/><circle cx="9" cy="-198" r="9"/></g>';
  const BAND = '<path d="M-22 -166 Q-24 -201 0 -201 Q24 -201 22 -166" fill="none" stroke="#3A3D42" stroke-width="3"/><rect x="-26" y="-170" width="8" height="16" rx="3" fill="#3A3D42"/><rect x="18" y="-170" width="8" height="16" rx="3" fill="#3A3D42"/>';
  const ears = c => `<circle cx="-20.5" cy="-161" r="4.5" fill="${c.skinD}"/><circle cx="20.5" cy="-161" r="4.5" fill="${c.skinD}"/>`;
  const stripes = (x, w) => [-124, -114, -104, -94, -84].map(y => `<rect x="${x}" y="${y}" width="${w}" height="2.5" fill="#3E4A5E"/>`).join('');
  const torso = (fill, h, x) => `<rect x="${x || -25}" y="-134" width="${x ? 26 : 50}" height="${h || 68}" rx="${x ? 11 : 12}" fill="${fill}"/>`;

  const CAST = {
    hange: {
      name: 'Hange', role: 'director', station: 'Director desk', colour: '#6F8FB5',
      almond: 1, bw: 2.2, frx: 20, earS: false,
      browF: 'M-13 -170 Q-8 -173 -3 -171 M3 -171 Q8 -173 13 -170', browS: 'M10 -171 Q14 -174 19 -171',
      skin: '#C58C64', skinD: '#B07650', cheek: '#D49A78', hair: '#1F1A1D', mouth: '#5A2A20',
      top: '#6F8FB5', topD: '#5E7CA0', topFar: '#5A779A', pants: '#2A2B30', pantsD: '#202126', pantsFar: '#202126', hem: '#202126', shoe: '#2B2C33', sole: '#EDEBE6',
      torsoF: torso('#6F8FB5') + '<rect x="-8" y="-133" width="16" height="62" fill="#F2EFE8"/><path d="M-8 -133 L-15 -133 L-4 -106 Z M8 -133 L15 -133 L4 -106 Z" fill="#5E7CA0"/><rect x="-20" y="-118" width="9" height="7" rx="1.5" fill="#5E7CA0"/><rect x="11" y="-118" width="9" height="7" rx="1.5" fill="#5E7CA0"/><rect x="-25" y="-72" width="50" height="7" rx="3" fill="#5E7CA0"/>',
      torsoB: torso('#6F8FB5') + '<path d="M-14 -96 H14" stroke="#5E7CA0" stroke-width="1.5"/><rect x="-25" y="-72" width="50" height="7" rx="3" fill="#5E7CA0"/>',
      torsoS: torso('#6F8FB5', 68, -13) + '<rect x="8" y="-132" width="5" height="60" fill="#F2EFE8"/><rect x="-13" y="-72" width="26" height="7" rx="3" fill="#5E7CA0"/>',
      hbF: '<ellipse cx="0" cy="-197" rx="8" ry="6" fill="#1F1A1D"/><rect x="-5" y="-194" width="10" height="4" rx="1.5" fill="#C8553D"/>',
      htF: '<path d="M-21 -163 Q-24 -192 0 -192 Q24 -192 21 -163 Q17 -181 0 -183 Q-17 -181 -21 -163 Z" fill="#1F1A1D"/><path d="M-18 -176 Q-22 -160 -17 -148 M18 -176 Q22 -160 17 -148" fill="none" stroke="#1F1A1D" stroke-width="2" stroke-linecap="round"/>',
      xF: BAND + '<path d="M-22 -157 Q-19 -147 -9 -148" fill="none" stroke="#3A3D42" stroke-width="1.8"/><circle cx="-8" cy="-148" r="2.2" fill="#2A2C30"/>',
      hbS: '<path d="M-12 -194 Q-30 -204 -32 -178 Q-33 -156 -25 -136 Q-22 -160 -20 -176 Q-18 -188 -12 -194 Z" fill="#2B2428"/>',
      htS: '<path d="M23 -176 Q20 -196 2 -196 Q-18 -196 -19 -166 Q-12 -160 -5 -164 Q4 -180 23 -176 Z" fill="#1F1A1D"/><rect x="-17" y="-197" width="7" height="8" rx="2" fill="#C8553D"/><path d="M7 -174 Q5 -160 8 -150" fill="none" stroke="#1F1A1D" stroke-width="2" stroke-linecap="round"/>',
      xS: '<path d="M-3 -170 Q-3 -194 3 -199" fill="none" stroke="#3A3D42" stroke-width="3"/><rect x="-9" y="-170" width="12" height="16" rx="3" fill="#3A3D42"/><path d="M3 -158 Q10 -147 17 -148" fill="none" stroke="#3A3D42" stroke-width="1.8"/><circle cx="18" cy="-148" r="2.2" fill="#2A2C30"/>',
      headB: '<ellipse cx="0" cy="-197" rx="7" ry="5" fill="#1F1A1D"/><ellipse cx="0" cy="-168" rx="21" ry="23" fill="#1F1A1D"/><path d="M-7 -190 Q-13 -165 -6 -138 Q0 -132 6 -138 Q13 -165 7 -190 Z" fill="#2B2428"/><rect x="-5" y="-194" width="10" height="5" rx="1.5" fill="#C8553D"/>' + BAND
    },
    armin: {
      name: 'Armin', role: 'brief', station: 'Brief desk', colour: '#6B7A4B',
      bw: 2.6,
      skin: '#A86B48', skinD: '#93593A', cheek: '#B87A56', hair: '#1A1412', mouth: '#4E2416',
      top: '#6B7A4B', topD: '#5A6840', topFar: '#5E6C42', pants: '#C2A878', pantsD: '#A88F62', pantsFar: '#B09868', shoe: '#F3F1EB', sole: '#D9D4C9',
      torsoF: torso('#6B7A4B') + '<rect x="-8" y="-133" width="16" height="61" fill="#EDE9E0"/>' + stripes(-8, 16) + '<path d="M-8 -133 L-15 -133 L-5 -108 Z M8 -133 L15 -133 L5 -108 Z" fill="#5A6840"/><rect x="-21" y="-120" width="10" height="3" rx="1" fill="#5A6840"/><rect x="11" y="-120" width="10" height="3" rx="1" fill="#5A6840"/><rect x="-25" y="-72" width="50" height="6" rx="3" fill="#5A6840"/>',
      torsoB: torso('#6B7A4B') + '<path d="M-22 -118 Q0 -114 22 -118" fill="none" stroke="#5A6840" stroke-width="1.5"/><rect x="-25" y="-72" width="50" height="6" rx="3" fill="#5A6840"/>',
      torsoS: torso('#6B7A4B', 68, -13) + '<rect x="8" y="-132" width="5" height="60" fill="#EDE9E0"/>' + stripes(8, 5) + '<rect x="-13" y="-72" width="26" height="6" rx="3" fill="#5A6840"/>',
      fxF: '<path d="M-19 -158 Q-18 -142 0 -140 Q18 -142 19 -158 Q14 -148 0 -146 Q-14 -148 -19 -158 Z" fill="#8C5638"/>',
      htF: '<path d="M-20.5 -166 Q-21 -182 -12 -186 L12 -186 Q21 -182 20.5 -166 Q18 -176 12 -178 L-12 -178 Q-18 -176 -20.5 -166 Z" fill="#2E2420"/><g fill="#1A1412"><circle cx="-13" cy="-185" r="7"/><circle cx="-5" cy="-190" r="8"/><circle cx="4" cy="-191" r="8"/><circle cx="12" cy="-186" r="7"/><circle cx="0" cy="-184" r="7"/><circle cx="-8" cy="-183" r="6"/><circle cx="8" cy="-183" r="6"/></g>',
      fxS: '<path d="M-2 -156 Q4 -141 18 -142 Q22 -146 21 -152 Q12 -148 -2 -156 Z" fill="#8C5638"/>',
      htS: '<path d="M23 -177 Q19 -186 4 -186 Q-15 -186 -16 -166 Q-16 -158 -12 -152 L-8 -160 Q-6 -168 -4 -168 Q6 -180 23 -177 Z" fill="#2E2420"/><g fill="#1A1412"><circle cx="-8" cy="-187" r="8"/><circle cx="1" cy="-191" r="8"/><circle cx="10" cy="-189" r="7"/><circle cx="17" cy="-184" r="6"/><circle cx="-14" cy="-182" r="6"/></g>',
      headB: '<ellipse cx="0" cy="-168" rx="21" ry="21" fill="#2E2420"/><g fill="#1A1412"><circle cx="-12" cy="-186" r="7"/><circle cx="-4" cy="-191" r="8"/><circle cx="5" cy="-191" r="8"/><circle cx="13" cy="-186" r="7"/><circle cx="0" cy="-185" r="7"/></g>', earsB: 1
    },
    pieck: {
      name: 'Pieck', role: 'architect', station: 'Planning station', colour: '#4A78A8',
      almond: 1, frx: 20, ears: false, earS: false, wide: 1,
      skin: '#D8A880', skinD: '#C4926A', cheek: '#E2AE8E', hair: '#141214', mouth: '#6A3226',
      top: '#26262B', topD: '#1C1C20', topFar: '#202024', pants: '#8A8C90', pantsD: '#75777B', pantsFar: '#7A7C80', shoe: '#F3F1EB', sole: '#D9D4C9',
      torsoF: torso('#26262B') + '<rect x="-25" y="-72" width="50" height="6" rx="3" fill="#1C1C20"/>', torsoB: torso('#26262B') + '<rect x="-25" y="-72" width="50" height="6" rx="3" fill="#1C1C20"/>',
      overF: '<rect x="-9" y="-143" width="18" height="13" rx="4" fill="#26262B"/><path d="M-8 -137 H8" stroke="#1C1C20" stroke-width="1.2"/>', overB: '<rect x="-9" y="-143" width="18" height="13" rx="4" fill="#26262B"/>',
      torsoS: torso('#26262B', 68, -13) + '<rect x="-13" y="-72" width="26" height="6" rx="3" fill="#1C1C20"/>', overS: '<rect x="-6" y="-143" width="13" height="13" rx="4" fill="#26262B"/>',
      hbF: '<path d="M-25 -150 Q-28 -197 0 -197 Q28 -197 25 -150 Z" fill="#141214"/>',
      htF: '<path d="M-21 -174 Q-22 -194 0 -194 Q22 -194 21 -174 Z" fill="#141214"/><path d="M-21 -176 L-25 -150 L-17 -150 Q-18 -164 -17 -176 Z M21 -176 L25 -150 L17 -150 Q18 -164 17 -176 Z" fill="#141214"/>',
      xF: '<circle cx="-18.5" cy="-147" r="1.6" fill="#D9B86A"/><circle cx="18.5" cy="-147" r="1.6" fill="#D9B86A"/>',
      htS: '<path d="M23 -174 L23 -178 Q20 -199 0 -199 Q-25 -198 -27 -172 Q-28 -155 -22 -148 L5 -148 Q3 -162 8 -174 Z" fill="#141214"/>', xS: '<circle cx="4" cy="-146" r="1.6" fill="#D9B86A"/>',
      headB: '<path d="M-25 -149 Q-28 -198 0 -198 Q28 -198 25 -149 Q0 -145 -25 -149 Z" fill="#141214"/>'
    },
    eren: {
      name: 'Eren', role: 'builder', station: 'Modeling bay', colour: '#C2643A',
      bw: 2.6, shoeH: 14,
      skin: '#6B4630', skinD: '#5C3B28', cheek: '#7A5039', hair: '#17110E', mouth: '#3A1A10',
      top: '#C2643A', topD: '#A8532F', topFar: '#AF5A34', pants: '#2E3A4E', pantsD: '#243042', pantsFar: '#263244', shoe: '#A0743F', sole: '#5A4126',
      torsoF: torso('#C2643A') + '<rect x="-14" y="-98" width="28" height="15" rx="3" fill="#A8532F"/><rect x="-25" y="-73" width="50" height="9" rx="4" fill="#A8532F"/>',
      overF: '<path d="M-15 -138 Q0 -124 15 -138 Q16 -131 0 -127 Q-16 -131 -15 -138 Z" fill="#A8532F"/><path d="M-4 -129 V-114 M4 -129 V-114" stroke="#EFE6D2" stroke-width="1.6" stroke-linecap="round"/>',
      torsoB: torso('#C2643A') + '<path d="M-16 -136 Q0 -110 16 -136 Q0 -128 -16 -136 Z" fill="#A8532F"/><rect x="-25" y="-73" width="50" height="9" rx="4" fill="#A8532F"/>',
      torsoS: torso('#C2643A', 68, -13) + '<path d="M-13 -136 Q-21 -122 -12 -110 L-8 -134 Z" fill="#A8532F"/><rect x="4" y="-98" width="10" height="15" rx="3" fill="#A8532F"/><rect x="-13" y="-73" width="26" height="9" rx="4" fill="#A8532F"/>',
      hbF: '<g fill="#17110E"><circle cx="-21" cy="-169" r="4.5"/><circle cx="-21.5" cy="-163" r="4"/><circle cx="21" cy="-169" r="4.5"/><circle cx="21.5" cy="-163" r="4"/></g>',
      htF: '<path d="M-22 -169 Q-23 -198 0 -198 Q23 -198 22 -169 Q0 -176 -22 -169 Z" fill="#2F5F57"/><rect x="-8" y="-179" width="16" height="4" rx="2" fill="#244A44"/><circle cx="0" cy="-198" r="2" fill="#244A44"/>',
      htS: '<g fill="#17110E"><circle cx="-14" cy="-166" r="4.5"/><circle cx="-16" cy="-160" r="4"/><circle cx="-12" cy="-155" r="3.5"/></g><path d="M23 -172 Q21 -199 0 -199 Q-21 -198 -21 -170 Q0 -176 23 -172 Z" fill="#2F5F57"/><path d="M-17 -172 L-36 -170 Q-38 -165 -33 -165 L-17 -167 Z" fill="#244A44"/>',
      headB: '<ellipse cx="0" cy="-166" rx="21" ry="21" fill="#17110E"/><g fill="#17110E"><circle cx="-9" cy="-147" r="4"/><circle cx="0" cy="-146" r="4"/><circle cx="9" cy="-147" r="4"/></g><path d="M-22 -169 Q-23 -198 0 -198 Q23 -198 22 -169 Q0 -163 -22 -169 Z" fill="#2F5F57"/><path d="M-16 -170 Q0 -157 16 -170 Q0 -164 -16 -170 Z" fill="#244A44"/>', earsB: 1
    },
    mikasa: {
      name: 'Mikasa', role: 'assembler', station: 'Assembly bay', colour: '#7F9A6A',
      short: 1,
      skin: '#B98058', skinD: '#A36E4A', cheek: '#C98C66', hair: '#3A2418', mouth: '#5A2A1C',
      top: '#F2EFE8', topD: '#DCD8CE', topFar: '#E2DED4', pants: '#8DA27A', pantsD: '#7A8E68', pantsFar: '#7F9370', hem: '#A9BB98', shoe: '#D3A03C', sole: '#F0EDE6',
      torsoF: torso('#F2EFE8') + '<rect x="-25" y="-86" width="50" height="22" rx="6" fill="#8DA27A"/><rect x="-15" y="-118" width="30" height="34" rx="3" fill="#8DA27A"/><rect x="-7" y="-112" width="14" height="9" rx="1.5" fill="#7A8E68"/><path d="M-12 -117 L-17 -134 M12 -117 L17 -134" stroke="#8DA27A" stroke-width="5" stroke-linecap="round"/><circle cx="-12" cy="-115" r="1.8" fill="#D3A03C"/><circle cx="12" cy="-115" r="1.8" fill="#D3A03C"/>',
      torsoB: torso('#F2EFE8') + '<rect x="-25" y="-86" width="50" height="22" rx="6" fill="#8DA27A"/><path d="M-17 -134 L12 -88 M17 -134 L-12 -88" stroke="#8DA27A" stroke-width="5" stroke-linecap="round"/>',
      torsoS: torso('#F2EFE8', 68, -13) + '<rect x="-13" y="-86" width="26" height="22" rx="6" fill="#8DA27A"/><rect x="4" y="-118" width="9" height="34" rx="2" fill="#8DA27A"/><path d="M7 -117 L1 -134" stroke="#8DA27A" stroke-width="5" stroke-linecap="round"/>',
      hbF: '<circle cx="-16" cy="-193" r="9" fill="#3A2418"/><circle cx="16" cy="-193" r="9" fill="#3A2418"/>',
      fxF: '<g fill="#8F5A3A"><circle cx="-14" cy="-156" r=".9"/><circle cx="-11" cy="-157.5" r=".9"/><circle cx="-12" cy="-154" r=".9"/><circle cx="14" cy="-156" r=".9"/><circle cx="11" cy="-157.5" r=".9"/><circle cx="12" cy="-154" r=".9"/></g>',
      htF: '<path d="M-21 -164 Q-23 -192 0 -192 Q23 -192 21 -164 Q16 -181 0 -183 Q-16 -181 -21 -164 Z" fill="#3A2418"/><path d="M0 -191 L0 -183" stroke="#5A3A28" stroke-width="1.4"/>',
      hbS: '<circle cx="-2" cy="-197" r="9" fill="#3A2418"/>', fxS: '<g fill="#8F5A3A"><circle cx="8" cy="-157" r=".9"/><circle cx="11" cy="-155" r=".9"/><circle cx="7" cy="-154" r=".9"/></g>',
      htS: '<path d="M24 -175 Q20 -197 2 -197 Q-20 -197 -22 -172 Q-12 -168 -4 -166 Q6 -180 24 -175 Z" fill="#3A2418"/>',
      headB: '<ellipse cx="0" cy="-169" rx="21.5" ry="22" fill="#3A2418"/><circle cx="-16" cy="-193" r="9" fill="#3A2418"/><circle cx="16" cy="-193" r="9" fill="#3A2418"/><path d="M0 -191 L0 -176" stroke="#5A3A28" stroke-width="1.4"/>', earsB: 1
    },
    reiner: {
      name: 'Reiner', role: 'materials', station: 'Material lab', colour: '#2F7F7A',
      almond: 1, bw: 2.4, short: 1, ears: false, earS: false,
      skin: '#9C6644', skinD: '#87563A', cheek: '#AD7652', hair: '#2A1E18', mouth: '#4A2416',
      top: '#2F7F7A', topD: '#256764', topFar: '#2A716D', pants: '#8FA6C1', pantsD: '#7A91AD', pantsFar: '#7E95B1', shoe: '#B0413E', sole: '#F0EDE6',
      torsoF: torso('#2F7F7A') + '<rect x="-16" y="-133" width="5" height="61" fill="#EFE6D2"/><rect x="11" y="-133" width="5" height="61" fill="#EFE6D2"/><path d="M0 -128 V-72" stroke="#256764" stroke-width="1.2"/>' + [-120, -106, -92, -78].map(y => `<circle cx="2.5" cy="${y}" r="1.2" fill="#EFE6D2"/>`).join('') + '<rect x="-25" y="-72" width="50" height="6" rx="3" fill="#256764"/>',
      overF: '<path d="M-10 -135 L-2 -124 L0 -134 Z M10 -135 L2 -124 L0 -134 Z" fill="#256764"/>',
      torsoB: torso('#2F7F7A') + '<rect x="-16" y="-133" width="5" height="61" fill="#EFE6D2"/><rect x="11" y="-133" width="5" height="61" fill="#EFE6D2"/><rect x="-25" y="-72" width="50" height="6" rx="3" fill="#256764"/>', overB: '<rect x="-9" y="-137" width="18" height="5" rx="2" fill="#256764"/>',
      torsoS: torso('#2F7F7A', 68, -13) + '<rect x="-2" y="-133" width="5" height="61" fill="#EFE6D2"/><rect x="-13" y="-72" width="26" height="6" rx="3" fill="#256764"/>', overS: '<path d="M-4 -136 L9 -136 L6 -126 Z" fill="#256764"/>',
      hbF: '<path d="M-24 -150 Q-30 -196 0 -197 Q30 -196 24 -150 Q22 -158 19 -160 L-19 -160 Q-22 -158 -24 -150 Z" fill="#2A1E18"/>',
      fxF: '<path d="M-20 -162 Q-20 -140 0 -137 Q20 -140 20 -162 Q16 -150 8 -147 Q0 -149 -8 -147 Q-16 -150 -20 -162 Z" fill="#2A1E18"/><path d="M-7 -152 Q0 -156 7 -152 Q3 -151 0 -152 Q-3 -151 -7 -152 Z" fill="#2A1E18"/>',
      htF: '<path d="M-21 -166 Q-24 -195 2 -196 Q26 -194 21 -166 Q19 -182 8 -184 Q0 -178 -8 -184 Q-17 -180 -21 -166 Z" fill="#2A1E18"/>',
      fxS: '<path d="M-6 -162 Q-2 -142 16 -139 Q23 -143 22 -150 Q14 -147 6 -152 Q0 -156 -6 -162 Z" fill="#2A1E18"/><path d="M13 -153 Q18 -155 21 -152 Q17 -151 13 -153 Z" fill="#2A1E18"/>',
      htS: '<path d="M23 -176 Q21 -199 0 -199 Q-26 -198 -27 -168 Q-27 -152 -20 -146 Q-14 -150 -10 -158 Q-6 -168 -2 -168 Q8 -182 23 -176 Z" fill="#2A1E18"/>',
      headB: '<path d="M-24 -150 Q-30 -197 0 -197 Q30 -197 24 -150 Q0 -144 -24 -150 Z" fill="#2A1E18"/><path d="M-12 -170 Q0 -164 12 -170" fill="none" stroke="#3A2A22" stroke-width="1.4"/>'
    },
    jean: {
      name: 'Jean', role: 'animator', station: 'Animation suite', colour: '#B99A62',
      bw: 2.2,
      skin: '#E0B48F', skinD: '#C99C76', cheek: '#EAB99A', hair: '#8A5A32', mouth: '#7A3A30',
      top: '#D8C8A6', topD: '#C4B48F', topFar: '#CBBB97', pants: '#3A3F47', pantsD: '#2E3238', pantsFar: '#33373E', shoe: '#F3F1EB', sole: '#D9D4C9',
      // a sand hoodie with a kangaroo pocket and drawstrings; headphones round the neck
      torsoF: torso('#D8C8A6') + '<rect x="-15" y="-94" width="30" height="18" rx="4" fill="#C4B48F"/><path d="M-4 -130 V-112 M4 -130 V-112" stroke="#8A7A58" stroke-width="1.6" stroke-linecap="round"/><rect x="-25" y="-72" width="50" height="6" rx="3" fill="#C4B48F"/>',
      overF: '<path d="M-15 -139 Q0 -125 15 -139" fill="none" stroke="#2E3238" stroke-width="3.5"/><rect x="-19" y="-144" width="8" height="11" rx="3" fill="#2E3238"/><rect x="11" y="-144" width="8" height="11" rx="3" fill="#2E3238"/>',
      torsoB: torso('#D8C8A6') + '<path d="M-17 -136 Q0 -116 17 -136 Q0 -128 -17 -136 Z" fill="#C4B48F"/><rect x="-25" y="-72" width="50" height="6" rx="3" fill="#C4B48F"/>',
      overB: '<path d="M-14 -141 Q0 -131 14 -141" fill="none" stroke="#2E3238" stroke-width="3"/>',
      torsoS: torso('#D8C8A6', 68, -13) + '<rect x="-8" y="-94" width="18" height="18" rx="4" fill="#C4B48F"/><rect x="-13" y="-72" width="26" height="6" rx="3" fill="#C4B48F"/>', overS: '<rect x="-3" y="-144" width="9" height="10" rx="3" fill="#2E3238"/>',
      htF: '<path d="M-21 -165 Q-23 -194 0 -194 Q23 -194 21 -165 Q18 -181 6 -181 Q-8 -176 -21 -165 Z" fill="#8A5A32"/>',
      htS: '<path d="M23 -174 Q20 -197 0 -197 Q-21 -197 -22 -168 Q-14 -177 -4 -176 Q8 -183 23 -174 Z" fill="#8A5A32"/>',
      headB: '<ellipse cx="0" cy="-169" rx="21" ry="22" fill="#8A5A32"/><path d="M-21 -165 Q-23 -194 0 -194 Q23 -194 21 -165 Q0 -160 -21 -165 Z" fill="#8A5A32"/><path d="M-14 -140 Q0 -130 14 -140" fill="none" stroke="#2E3238" stroke-width="3"/>', earsB: 1
    },
    annie: {
      name: 'Annie', role: 'critic', station: 'Critic desk', colour: '#D9A33A',
      frx: 21,
      skin: '#8D5A3B', skinD: '#7E4F33', cheek: '#9E6646', hair: '#2A1C16', mouth: '#4A2618',
      top: '#D9A33A', topD: '#C38D2C', topFar: '#C3922F', pants: '#3B4A63', pantsD: '#2F3C52', pantsFar: '#2F3C52', shoe: '#F3F1EB', sole: '#D9D4C9',
      torsoF: torso('#D9A33A') + '<rect x="-25" y="-73" width="50" height="9" rx="4" fill="#C38D2C"/><ellipse cx="0" cy="-132" rx="9" ry="4.5" fill="#F2EFE8"/>',
      torsoB: torso('#D9A33A') + '<rect x="-25" y="-73" width="50" height="9" rx="4" fill="#C38D2C"/>',
      torsoS: torso('#D9A33A', 68, -13) + '<rect x="-13" y="-73" width="26" height="9" rx="4" fill="#C38D2C"/>', overS: '<ellipse cx="3" cy="-132" rx="5" ry="3" fill="#F2EFE8"/>',
      hbF: PUFF, htF: '<path d="M-21 -168 Q-20 -191 0 -190 Q20 -191 21 -168 Q12 -179 0 -178 Q-12 -179 -21 -168 Z" fill="#2A1C16"/>',
      xF: '<g fill="none" stroke="#2B2B2B" stroke-width="1.7"><circle cx="-8" cy="-162" r="7.5"/><circle cx="8" cy="-162" r="7.5"/><path d="M-0.5 -163 H0.5"/></g>',
      hbS: '<g fill="#2A1C16"><circle cx="-12" cy="-186" r="13"/><circle cx="2" cy="-193" r="14"/><circle cx="14" cy="-186" r="11"/><circle cx="-20" cy="-172" r="10"/><circle cx="-8" cy="-198" r="9"/><circle cx="10" cy="-198" r="9"/></g>',
      htS: '<path d="M24 -175 Q20 -197 2 -197 Q-20 -197 -22 -172 Q-12 -168 -4 -166 Q6 -180 24 -175 Z" fill="#2A1C16"/>',
      xS: '<g fill="none" stroke="#2B2B2B" stroke-width="1.7"><circle cx="14" cy="-163" r="7"/><path d="M7 -164 L0 -162"/></g>',
      headB: '<ellipse cx="0" cy="-170" rx="21.5" ry="23" fill="#2A1C16"/>' + PUFF + '<path d="M-17 -163 L-23 -161 M17 -163 L23 -161" stroke="#2B2B2B" stroke-width="1.5" stroke-linecap="round"/>', earsB: 1
    },
    historia: {
      name: 'Historia', role: 'librarian', station: 'Asset library', colour: '#C98A8F',
      ears: false, earS: false, frx: 18, fry: 22, fcy: -163,
      skin: '#D9A983', skinD: '#C4946E', cheek: '#E4B394', hair: '#3A2A22', mouth: '#7A3A30',
      top: '#E8DCC4', topD: '#D6C8AC', topFar: '#DCCFB4', pants: '#4A4A52', pantsD: '#3E3E45', pantsFar: '#3F3F46', shoe: '#6B4A34', sole: '#4A3324',
      torsoF: torso('#E8DCC4', 78) + '<rect x="-8" y="-132" width="16" height="64" fill="#9C8FC4"/><rect x="-11" y="-134" width="3" height="76" fill="#D6C8AC"/><rect x="8" y="-134" width="3" height="76" fill="#D6C8AC"/><rect x="-21" y="-82" width="10" height="10" rx="2" fill="#D6C8AC"/><rect x="11" y="-82" width="10" height="10" rx="2" fill="#D6C8AC"/>',
      torsoB: torso('#E8DCC4', 78) + '<path d="M-20 -60 H20" stroke="#D6C8AC" stroke-width="2"/>',
      torsoS: torso('#E8DCC4', 78, -13) + '<rect x="9" y="-132" width="4" height="64" fill="#9C8FC4"/><rect x="0" y="-82" width="9" height="10" rx="2" fill="#D6C8AC"/>',
      hbF: '<path d="M-27 -150 Q-30 -201 0 -201 Q30 -201 27 -150 Q26 -134 18 -126 L-18 -126 Q-26 -134 -27 -150 Z" fill="#C98A8F"/>',
      htF: '<path d="M-19 -164 Q-20 -190 0 -191 Q20 -190 19 -164 Q17 -184 0 -186 Q-17 -184 -19 -164 Z" fill="#B57479"/><path d="M-19 -155 Q-17 -139 0 -137 Q17 -139 19 -155 Q24 -130 16 -122 L-16 -122 Q-24 -130 -19 -155 Z" fill="#C98A8F"/><path d="M-12 -131 Q0 -126 12 -131" fill="none" stroke="#B57479" stroke-width="1.2"/><circle cx="13" cy="-140" r="1.6" fill="#D9B86A"/>',
      htS: '<path d="M24 -171 Q22 -201 0 -202 Q-29 -200 -30 -160 Q-29 -134 -14 -122 L16 -122 Q21 -130 19 -140 Q9 -139 7 -150 Q5 -161 9 -172 Q15 -180 24 -171 Z" fill="#C98A8F"/><path d="M9 -172 Q15 -181 24 -171" fill="none" stroke="#B57479" stroke-width="3"/>',
      headB: '<path d="M-27 -150 Q-30 -201 0 -201 Q30 -201 27 -150 Q26 -126 16 -118 L-16 -118 Q-26 -126 -27 -150 Z" fill="#C98A8F"/><path d="M-14 -140 Q0 -132 14 -140" fill="none" stroke="#B57479" stroke-width="1.4"/>'
    }
  };
  Object.keys(CAST).forEach(k => { CAST[k].id = k; });

  // ---- the rig ------------------------------------------------------------------------
  const mouths = m => `<path class="p m-smile" d="M-6 -151 Q0 -147 6 -151" fill="none" stroke="${m}" stroke-width="1.8" stroke-linecap="round"/><path class="p m-foc" d="M-4 -150 H4" fill="none" stroke="${m}" stroke-width="1.8" stroke-linecap="round"/><ellipse class="p m-talk" cx="0" cy="-150" rx="3.5" ry="2.4" fill="${m}" style="transform-origin:0 -150px"/><path class="p m-open" d="M-7 -152 Q0 -142 7 -152 Z" fill="${m}"/><path class="p m-flat" d="M-5 -148 Q0 -151 5 -148" fill="none" stroke="${m}" stroke-width="1.8" stroke-linecap="round"/>`;
  const eye = (c, x) => c.almond ? `<ellipse cx="${x}" cy="-162" rx="2.6" ry="2.1" fill="#1E1A18"/>` : `<circle cx="${x}" cy="-162" r="2.3" fill="#1E1A18"/>`;
  const faceFront = c => `<circle cx="-12.5" cy="-153" r="3" fill="${c.cheek}"/><circle cx="12.5" cy="-153" r="3" fill="${c.cheek}"/><g class="p eyes"><g class="blink" style="transform-origin:0 -162px">${eye(c, -8)}${eye(c, 8)}</g></g><path class="p e-hap" d="M-11 -161 Q-8 -165 -5 -161 M5 -161 Q8 -165 11 -161" fill="none" stroke="#1E1A18" stroke-width="1.8" stroke-linecap="round"/><g class="p brows" fill="none" stroke="${c.hair}" stroke-width="${c.bw || 2}" stroke-linecap="round"><path d="${c.browF || 'M-13 -172 L-4 -173 M4 -173 L13 -172'}"/></g><path d="M0 -161 q2.5 4 -1 5" fill="none" stroke="${c.skinD}" stroke-width="1.3" stroke-linecap="round"/>` + mouths(c.mouth);
  const faceSide = c => `<circle cx="9" cy="-153" r="3" fill="${c.cheek}"/><g class="blink" style="transform-origin:14px -163px">${c.almond ? '<ellipse cx="14" cy="-163" rx="2.4" ry="2" fill="#1E1A18"/>' : '<circle cx="14" cy="-163" r="2.3" fill="#1E1A18"/>'}</g><path d="${c.browS || 'M10 -173 L19 -172'}" fill="none" stroke="${c.hair}" stroke-width="${c.bw || 2}" stroke-linecap="round"/><path d="M14 -151 Q17 -149 19 -152" fill="none" stroke="${c.mouth}" stroke-width="1.8" stroke-linecap="round"/>`;
  const BUBBLE = '<g class="p bub"><rect x="22" y="-238" width="26" height="26" rx="6" fill="#FFFFFF" stroke="#CFCAC0"/><path d="M28 -212.5 L34 -212.5 L26 -204 Z" fill="#FFFFFF" stroke="#CFCAC0" stroke-linejoin="round"/><path d="M28.5 -213 H33.5" stroke="#FFFFFF" stroke-width="1.5"/><text x="35" y="-219" text-anchor="middle" style="font:600 16px system-ui,sans-serif;fill:#2B2B2B">?</text></g>';
  const CLOUD = '<g class="p cloud"><rect x="-18" y="-228" width="38" height="9" rx="4.5" fill="#A9A7A0"/><circle cx="-8" cy="-228" r="8" fill="#A9A7A0"/><circle cx="4" cy="-232" r="10" fill="#A9A7A0"/><circle cx="14" cy="-227" r="6" fill="#A9A7A0"/><path d="M-8 -214 l-2 7 M3 -214 l-2 7 M13 -214 l-2 7" stroke="#8E9AA6" stroke-width="1.5" stroke-linecap="round"/></g>';
  const BADGE = '<g class="p badge"><circle cx="0" cy="-226" r="11" fill="#5E8F2A"/><path d="M-5 -226 L-1 -222 L6 -230" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></g>';
  // The VR headset, worn only in the VR test zone.
  const VR = '<g class="p vrh"><path d="M-22 -166 H-27 M22 -166 H27" stroke="#2B2F35" stroke-width="4"/><rect x="-23" y="-175" width="46" height="20" rx="7" fill="#2B2F35"/><rect x="-19" y="-171" width="38" height="8" rx="3" fill="#4C9BF7" opacity=".85"/></g>';
  const CONFETTI = '<g>' + [[-40, -215, '#378ADD', 0], [-20, -232, '#D85A30', .15], [6, -238, '#1D9E75', .05], [30, -222, '#EF9F27', .25], [44, -205, '#378ADD', .1], [-52, -198, '#1D9E75', .3]]
    .map(([x, y, f, d]) => `<rect class="conf" x="${x}" y="${y}" width="5" height="4" fill="${f}" style="animation-delay:${d}s"/>`).join('') + '</g>';

  function arm(cls, fcls, x, c, fill, skin) {
    const upper = c.short
      ? `<rect x="${x - 7}" y="-132" width="14" height="32" rx="7" fill="${skin}"/><rect x="${x - 7.5}" y="-133" width="15" height="21" rx="7" fill="${fill}"/><rect x="${x - 7.5}" y="-116" width="15" height="4" rx="2" fill="${c.topD}"/>`
      : `<rect x="${x - 7}" y="-132" width="14" height="32" rx="7" fill="${fill}"/>`;
    const lower = c.short
      ? `<rect x="${x - 6.5}" y="-106" width="13" height="26" rx="6.5" fill="${skin}"/>`
      : `<rect x="${x - 6.5}" y="-106" width="13" height="26" rx="6.5" fill="${fill}"/><rect x="${x - 7}" y="-88" width="14" height="7" rx="3" fill="${c.topD}"/>`;
    return `<g class="p ${cls}" style="transform-origin:${x}px -126px">${upper}<g class="p ${fcls}" style="transform-origin:${x}px -102px">${lower}<circle cx="${x}" cy="-76" r="6" fill="${skin}"/></g></g>`;
  }

  function legs(c, pockets) {
    const w = c.wide ? 3 : 0, h = c.shoeH || 12;
    return [[-1, 'lgL', -12], [1, 'lgR', 12]].map(([s, cls, ox]) => {
      const X = v => v * s;
      return `<g class="${cls}" style="transform-origin:${ox}px -72px"><path d="M${X(22)} -72 L${X(25 + w)} -12 L${X(3 - w / 2)} -12 L${X(1)} -48 L0 -72 Z" fill="${c.pants}"/>` +
        (c.hem ? `<rect x="${s < 0 ? -25 - w : 3}" y="-17" width="${22 + w}" height="6" rx="2" fill="${c.hem}"/>` : '') +
        (pockets ? `<rect x="${s < 0 ? -18 : 8}" y="-66" width="10" height="8" rx="2" fill="none" stroke="${c.pantsD}"/>` : '') +
        `<rect x="${s < 0 ? -27 : 4}" y="${-h}" width="23" height="${h}" rx="4" fill="${c.shoe}"/><rect x="${s < 0 ? -27 : 4}" y="-4" width="23" height="4" rx="1.5" fill="${c.sole}"/></g>`;
    }).join('');
  }

  const neck = (c, w) => `<rect x="${-w / 2}" y="-146" width="${w}" height="15" rx="3" fill="${c.skinD}"/>`;

  function front(c) {
    return `<g class="p jump">${legs(c)}<g class="p upper">${c.torsoF}${neck(c, 12)}${c.overF || ''}` +
      arm('uaL', 'faL', -23, c, c.top, c.skin) + arm('uaR', 'faR', 23, c, c.top, c.skin) +
      `<g class="p head" style="transform-origin:0 -142px">${c.hbF || ''}${c.ears !== false ? ears(c) : ''}<ellipse cx="0" cy="${c.fcy || -164}" rx="${c.frx || 20.5}" ry="${c.fry || 24}" fill="${c.skin}"/>${c.fxF || ''}${faceFront(c)}${c.htF || ''}${c.xF || ''}${VR}${BUBBLE}</g>${CLOUD}${BADGE}</g>${CONFETTI}</g>`;
  }

  const sideLeg = (t, s, fill, c) => `<g class="${t}" style="transform-origin:0 -70px"><rect x="-7" y="-74" width="14" height="36" rx="7" fill="${fill}"/><g class="${s}" style="transform-origin:0 -40px"><rect x="-6.5" y="-44" width="13" height="34" rx="6" fill="${fill}"/>${c.hem ? `<rect x="-7" y="-17" width="14" height="6" rx="2" fill="${c.hem}"/>` : ''}<path d="M-7 ${-(c.shoeH || 12)} H8 Q17 ${-(c.shoeH || 12)} 17 -5 V0 H-7 Z" fill="${c.shoe}"/><path d="M-7 -4 H17 V0 H-7 Z" fill="${c.sole}"/></g></g>`;

  function side(c) {
    return `<g class="lean"><g class="bob">` +
      arm('uaB', 'faB', 0, c, c.topFar, c.skinD) +
      sideLeg('thB', 'shB', c.pantsFar, c) + sideLeg('thA', 'shA', c.pants, c) +
      `${c.torsoS}${neck(c, 10)}${c.overS || ''}${c.hbS || ''}<ellipse cx="3" cy="${c.fcy || -164}" rx="19" ry="24" fill="${c.skin}"/><path d="M20 -170 Q27 -161 21 -156 Z" fill="${c.skin}"/>${c.fxS || ''}${c.htS || ''}` +
      (c.earS !== false ? `<circle cx="-3" cy="-161" r="4.5" fill="${c.skinD}"/>` : '') + faceSide(c) + (c.xS || '') +
      arm('uaF', 'faF', 0, c, c.top, c.skin) + `</g></g>`;
  }

  function back(c) {
    // From behind, the character's own left hand is on the viewer's left (bL).
    return legs(c, 1) + `<g class="p bup">${c.torsoB}` +
      arm('bL', 'bLf', -23, c, c.top, c.skin) + arm('bR', 'bRf', 23, c, c.top, c.skin) +
      `${neck(c, 12)}${c.overB || ''}<g class="p bhead" style="transform-origin:0 -142px">${c.earsB ? ears(c) : ''}${c.headB}</g></g>`;
  }

  MG.cast = CAST;
  MG.roleCast = { director: 'hange', brief: 'armin', architect: 'pieck', builder: 'eren', assembler: 'mikasa', materials: 'reiner', animator: 'jean', critic: 'annie', librarian: 'historia' };
  MG.rig = { front, side, back };
  MG.rigClass = c => ' c-' + c.id;

  // A head-and-shoulders portrait for panels, chat and lists, on a soft tint of the
  // character's colour.
  MG.portrait = function (castId, size) {
    const c = CAST[castId];
    return `<svg class="portrait" width="${size}" height="${size}" viewBox="-38 -214 76 76" role="img" aria-label="${c.name}" style="background:color-mix(in srgb, ${c.colour} 18%, var(--panel))"><g class="rig st-still${MG.rigClass(c)}">${front(c)}</g></svg>`;
  };
})();
