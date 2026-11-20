// The GLB viewer: three.js from modelgen/web/vendor (installed by Setup-ModelGen.bat).
// Drag to rotate, scroll or pinch to zoom, right-drag to pan. On top of showing the model it
// has view presets, perspective or orthographic, display modes (shaded, wireframe, wireframe
// over shaded, clay, X-ray), a grid, an axis gizmo, background, lighting and exposure,
// dimensions, model stats, the model's parts (select, hide, isolate), a measure tool, a
// section cut, screenshots, and the motion a moving model carries (the animator's loop of
// turning wheels, smoke and steam puffs at its markers, and the ground sliding under a
// vehicle). createViewer(container) returns the API at the bottom; the
// first few calls (load, clear, reset, setAutoRotate, setWireframe, setLighting,
// nextLighting, refreshBackground, hasModel) are what the older page in this folder uses.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// Where the camera looks from for each view. glTF is Y-up with the front facing +Z.
const VIEWS = {
  front: [0, 0, 1], back: [0, 0, -1], left: [-1, 0, 0], right: [1, 0, 0],
  top: [0, 1, 0.0001], bottom: [0, -1, 0.0001], iso: [-0.55, 0.45, 1],
};
// Exposures measured against the Blender renders of a can, a canal house and a car: at 1.0 dark
// and mid colours came out about a stop darker than the renders.
const LIGHTS = {
  studio: { exposure: 2.4, sun: 1.2, fill: 0 },
  soft: { exposure: 2.75, sun: 0.35, fill: 0.9 },
  contrast: { exposure: 2.05, sun: 2.6, fill: 0 },
};
// Blender's renders use AgX with its "Punchy" look (rig._true_colour_exposure); three.js's AgX has
// no look, so colours came out washed. Add the same look (power 1.35, saturation 1.4) after AgX's
// curve, where Blender applies it.
{
  const curve = 'color = agxDefaultContrastApprox( color );';
  const chunk = THREE.ShaderChunk.tonemapping_pars_fragment;
  if (chunk.includes(curve) && !chunk.includes('mg-punchy')) {
    THREE.ShaderChunk.tonemapping_pars_fragment = chunk.replace(curve, curve +
      '\n\t// mg-punchy\n\tcolor = pow( color, vec3( 1.35 ) );' +
      '\n\t{ float luma = dot( color, vec3( 0.2126, 0.7152, 0.0722 ) ); color = luma + 1.4 * ( color - luma ); }');
  }
}
// A direction at this azimuth (0 = in front, +Z; positive turns towards +X) and elevation, as
// rig._orbit places Blender's lights (glTF is Y-up with the front on +Z).
function orbit(az, el) {
  az *= Math.PI / 180; el *= Math.PI / 180;
  return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
}
// What shiny surfaces reflect: Blender's photo studio (rig.add_lights) rather than a bright room, a
// dim grey world with its three soft lights (key front-left above, fill front-right low, rim
// behind) as glowing panels. Metal then reads as grey metal with bright streaks, as in the renders.
function studioEnvironment() {
  const env = new THREE.Scene();
  env.add(new THREE.Mesh(new THREE.SphereGeometry(10, 32, 16),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0.2, 0.2, 0.208), side: THREE.BackSide })));
  for (const [az, el, glow, size] of [[-45, 40, 4, 4], [50, 15, 1.5, 6.4], [160, 45, 3, 3.2]]) {
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(size, size),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(glow, glow, glow), side: THREE.DoubleSide }));
    panel.position.copy(orbit(az, el).multiplyScalar(6));
    panel.lookAt(0, 0, 0);
    env.add(panel);
  }
  return env;
}
const BACKGROUNDS = { light: '#EDEDEA', dark: '#1F1F1F' };
const MODES = ['shaded', 'wire', 'wire-shaded', 'clay', 'xray'];
const HIDDEN = 1 << 31;   // a camera layer nothing renders: hidden parts go there
const GIZMO = 84;         // axis gizmo size in CSS pixels

// ---- colouring: a model is split into colour groups (one per material), each with a type, so a
// click on the bonnet recolours the whole body and leaves the windows, lights and tyres alone.
const GROUP_TYPES = { body: 'Body', paint: 'Paint', glass: 'Glass', headlight: 'Headlights', indicator: 'Indicators',
  taillight: 'Tail lights', reverse: 'Reversing lights', platelight: 'Plate lights', light: 'Lights', tyre: 'Tyres', trim: 'Trim',
  metal: 'Metal', wood: 'Wood', masonry: 'Stone and brick', ceramic: 'Ceramic', fabric: 'Fabric', scenery: 'Scenery' };
const KEYWORDS = [   // tried in this order, on the material name first and on the part names last
  ['paint', /paint|livery|clearcoat|lacquer|enamel/],
  ['light', /lens|light|lamp|indicator|beacon|reflector/],
  ['glass', /glass|window|windscreen|windshield|glazing|pane(?!l)|visor/],
  ['tyre', /tyre|tire|rubber/],
  ['metal', /chrome|metal|steel|alloy|alumin|brass|copper|iron|gunmetal|silver|gold|bronze|nickel/],
  ['trim', /trim|satin_black|grille|seal|gasket|plastic|seam|handle|intake|diffuser|sill|vinyl/],
  ['wood', /wood|oak|pine|walnut|plank|timber|bamboo/],
  ['masonry', /brick|stone|slate|concrete|tile|marble|granite|plaster|stucco|mortar/],
  ['ceramic', /ceramic|porcelain|glaze|terracotta/],
  ['fabric', /fabric|cloth|leather|textile|cushion|upholster|felt|canvas/],
];
// parts that are the setting, not the object: never the body
const SCENERY = /ground|floor|ballast|sleeper|rail_track|track_bed|lawn|grass|terrain|backdrop|plinth|display_base|base_disc|base_slab|pour_pad/;
const CONTEXTS = [   // what the model is, from its name: picks the body colours
  ['car', /\bcars?\b|suv|coupe|sedan|hatchback|vehicle|truck|\bbus\b|\bvan\b|jeep|civic|jdm|taxi|racer/],
  ['train', /locomotive|train|steam|tender|tram|railway/],
  ['building', /house|building|tower|villa|office|cabin|shop|church|canal|apartment|skyscraper|cottage/],
  ['furniture', /chair|table|bench|desk|sofa|stool|shelf|cabinet|\bbed\b|wardrobe/],
  ['ceramic', /mug|\bcup\b|vase|bowl|teapot|plate|\bjug\b/],
  ['toy', /toy|figure|chibi|character|robot|doll/],
];
const PALETTES = {
  car: ['#c8102e', '#0b3d91', '#1f6feb', '#f2f2f0', '#c0c4c8', '#5a5e63', '#111214', '#1e4d2b', '#f6c700', '#ff6a13', '#6b2c91', '#7a1f2b'],
  train: ['#1f4d2e', '#2e6b3f', '#7b1e1e', '#6e1423', '#0e2a47', '#1d3f73', '#141414', '#3b3b3b', '#b5651d', '#c9a227'],
  building: ['#f3efe6', '#e8d7a9', '#d9cbb0', '#b85c38', '#9e4a3a', '#8c9a9e', '#5b7553', '#3e4a52', '#7a4e2d', '#2b2b2b'],
  furniture: ['#c69c6d', '#a0522d', '#5c4033', '#3b2a1e', '#f2f2f0', '#2b2b2b', '#4a6b5a', '#2e3a4b', '#b5654a'],
  toy: ['#e63946', '#f4a261', '#e9c46a', '#2a9d8f', '#457b9d', '#8338ec', '#ff70a6', '#f2f2f0', '#1b1b1b'],
  paint: ['#c8102e', '#ff6a13', '#f6c700', '#2e7d32', '#1f6feb', '#6b2c91', '#f2f2f0', '#7a7f85', '#111214'],
  glass: ['#e8f1f5', '#c9dbe3', '#5b6770', '#2b3238', '#6e5a3e', '#4c6e8a', '#5e7e62'],
  light: ['#ffffff', '#fff4d6', '#ffb000', '#ff7a00', '#e0161b', '#3d7bff', '#9aa0a6'],
  headlight: ['#ffffff', '#f4f8ff', '#fff4d6', '#dce9ff', '#9fc3ff', '#9aa0a6', '#4a4e54'],
  indicator: ['#ffb000', '#ff9500', '#ff7a00', '#ffd27a', '#f2f2f0', '#9aa0a6', '#4a4e54'],
  taillight: ['#e0161b', '#b0000a', '#7a0008', '#ff3b30', '#5a1a1e', '#3a2a2c', '#f2f2f0'],
  reverse: ['#ffffff', '#f4f8ff', '#fff4d6', '#9aa0a6', '#4a4e54'],
  platelight: ['#ffffff', '#fff4d6', '#dce9ff', '#9aa0a6'],
  tyre: ['#141414', '#1e1e1e', '#2a2a2a', '#3a3a3a', '#5a4632', '#e8e4da'],
  trim: ['#0e0e0e', '#1a1a1a', '#2b2b2b', '#4a4d50', '#8a8d90', '#c0c4c8', '#f2f2f0'],
  metal: ['#d6d8db', '#a9adb2', '#7c8086', '#4a4e54', '#2a2c2f', '#b08d57', '#b87333', '#d4af37'],
  wood: ['#e0c9a6', '#c69c6d', '#a0522d', '#8b5a2b', '#5c4033', '#3b2a1e'],
  masonry: ['#9e4a3a', '#b5654a', '#d8cfc0', '#a89f91', '#7d7d7d', '#4a5058'],
  ceramic: ['#f5f3ee', '#1b1b1b', '#2f5d8a', '#c1440e', '#7fa88a', '#e8c547', '#d9a6a1', '#6c4f8a'],
  fabric: ['#2e3a4b', '#7b2d26', '#c2b59b', '#3f5e4c', '#1c1c1c', '#8e6e53', '#d9d5cc'],
  scenery: ['#7a7a72', '#5e7d4a', '#a89f91', '#d9d5cc', '#3b3b3b', '#8b6b4a'],
};
// Lights are told apart, so the headlights, indicators and tail lights each take their own colour:
// by the material's name, then the part names, then the lens colour.
const LIGHT_TYPES = new Set(['headlight', 'indicator', 'taillight', 'reverse', 'platelight', 'light']);
const LIGHT_KINDS = [
  ['platelight', /plate|licen[cs]e|regist/],
  ['reverse', /revers/],
  ['indicator', /amber|indicator|turn|signal|blinker|marker|orange|yellow/],
  ['taillight', /tail|rear|brake|stop|red/],
  ['headlight', /head|front|fog|drl|clear|white/],
];
function lightKind(matName, partNames, mat) {
  for (const text of [matName, partNames]) {
    const k = LIGHT_KINDS.find(([, re]) => re.test(text));
    if (k) return k[0];
  }
  const hsl = {};
  mat.color.getHSL(hsl, THREE.SRGBColorSpace);
  if (hsl.s > 0.4 && hsl.l > 0.08) {
    if (hsl.h < 0.04 || hsl.h > 0.94) return 'taillight';
    if (hsl.h < 0.17) return 'indicator';
  }
  return hsl.l > 0.5 ? 'headlight' : 'light';
}
const HEX = /^#[0-9a-f]{6}$/i;
// what this page adds to userData, left out of an exported file
const RUNTIME_KEY = /^mg[A-Z]|^mg_(wire|offset|xray)$/;

// ---- surface patterns: Blender draws brick, slate and the like from node patterns a GLB cannot
// carry, so the file arrives plain. The viewer draws a matching pattern from each point's place
// in the world (no UVs needed) on top of the material's own colour, so a new colour keeps it.
// w, h: block size in metres; m: joint width; vary: how much blocks differ; joint/jmix: the joint
// colour and how strongly it replaces the block's; grain: wood grain.
const PATTERNS = {
  brick: { w: 0.215, h: 0.065, m: 0.010, vary: 0.28, joint: 0.13, jmix: 0.75, grain: 0, colour: '#9e4a3a' },
  tiles: { w: 0.30, h: 0.18, m: 0.008, vary: 0.30, joint: 0.0, jmix: 0.55, grain: 0, colour: '#4a5058' },
  stone: { w: 0.60, h: 0.30, m: 0.008, vary: 0.10, joint: 0.25, jmix: 0.2, grain: 0, colour: '#d8cfc0' },
  planks: { w: 2.40, h: 0.14, m: 0.004, vary: 0.20, joint: 0.0, jmix: 0.50, grain: 1, colour: '#a0522d' },
  wood: { w: 1.0, h: 1.0, m: 0.0, vary: 0.0, joint: 0.0, jmix: 0.0, grain: 1, colour: '#8b5a2b' },
};
const PATTERN_OF = [   // by material name
  ['brick', /brick/],
  ['tiles', /slate|tile|shingle/],
  ['stone', /stone|ashlar|masonry|block/],
  ['planks', /plank|board|deck|floorboard|timber|parquet/],
  ['wood', /wood|oak|pine|walnut/],
];
const f1 = (x) => (Number.isInteger(x) ? x.toFixed(1) : String(x));
function patternFor(mat) {
  if (!mat || !mat.isMeshStandardMaterial || mat.map) return null;   // a real texture wins
  const name = (mat.name || '').toLowerCase();
  const hit = PATTERN_OF.find(([, re]) => re.test(name));
  return hit ? hit[0] : null;
}
// Each point's place and facing in the world, for the pattern and toon shaders.
function worldVaryings(sh) {
  sh.vertexShader = 'varying vec3 vMgPos;\nvarying vec3 vMgNrm;\n' + sh.vertexShader.replace('#include <project_vertex>',
    '#include <project_vertex>\n  vMgPos = (modelMatrix * vec4(transformed, 1.0)).xyz;\n  vMgNrm = normalize(mat3(modelMatrix) * objectNormal);');
}
function applyPattern(mat, kind) {
  const p = PATTERNS[kind];
  if (!p) return mat;
  mat.userData.mgPattern = kind;
  mat.customProgramCacheKey = () => 'mg-pattern-' + kind;
  mat.onBeforeCompile = (sh) => {
    worldVaryings(sh);
    sh.fragmentShader = `varying vec3 vMgPos;
varying vec3 vMgNrm;
float mgHash(vec2 q) { return fract(sin(dot(q, vec2(127.1, 311.7))) * 43758.5453); }
vec3 mgPattern(vec3 base) {
  vec3 n = abs(normalize(vMgNrm));
  vec2 uv = (n.x >= n.y && n.x >= n.z) ? vMgPos.zy : (n.z >= n.y ? vMgPos.xy : vMgPos.xz);
  float row = floor(uv.y / ${f1(p.h)});
  float x = uv.x / ${f1(p.w)} + mod(row, 2.0) * 0.5;
  float col = floor(x);
  vec2 f = vec2(fract(x) * ${f1(p.w)}, fract(uv.y / ${f1(p.h)}) * ${f1(p.h)});
  float edge = min(min(f.x, ${f1(p.w)} - f.x), min(f.y, ${f1(p.h)} - f.y));
  float aa = max(fwidth(edge), 1e-5);
  float joint = ${f1(p.m)} > 0.0 ? 1.0 - smoothstep(${f1(p.m / 2)} - aa, ${f1(p.m / 2)} + aa, edge) : 0.0;
  vec3 c = base * (1.0 + (mgHash(vec2(col, row)) - 0.5) * ${f1(p.vary)});
  if (${f1(p.grain)} > 0.0) {
    c *= 0.93 + 0.07 * sin(uv.y * 140.0 + sin(uv.x * 2.3 + col * 7.0) * 3.0 + mgHash(vec2(col, row)) * 6.0);
  }
  return mix(c, vec3(${f1(p.joint)}), joint * ${f1(p.jmix)});
}
` + sh.fragmentShader.replace('#include <color_fragment>', '#include <color_fragment>\n  diffuseColor.rgb = mgPattern(diffuseColor.rgb);');
  };
  mat.needsUpdate = true;
  return mat;
}

// ---- toon: Blender's flat bands (lit, mid, shadow, by how a face turns to its light), from the
// material's extras that rig.py writes on export. The material's own colour is the lit tone, so a
// new colour keeps the bands, each darker than it in the same proportion as before.
const fx = (x) => (+x).toFixed(5);
function toonOf(mat) {
  const u = mat && mat.userData;
  const c = u && u.mg_toon_colors, st = u && u.mg_toon_stops;
  if (!Array.isArray(c) || !Array.isArray(st) || !st.length || st.length > 8 || c.length !== st.length * 3) return null;
  const lum = (i) => 0.2126 * c[i * 3] + 0.7152 * c[i * 3 + 1] + 0.0722 * c[i * 3 + 2];
  const top = Math.max(lum(st.length - 1), 1e-4);
  const l = Array.isArray(u.mg_toon_light) && u.mg_toon_light.length === 3 ? u.mg_toon_light : [0.35, -0.45, 0.82];
  const dir = new THREE.Vector3(+l[0] || 0, +l[2] || 0, -(+l[1] || 0)).normalize();   // Blender axes to glTF
  return { stops: st.map(Number), ratios: st.map((_, i) => Math.min(lum(i) / top, 1.5)), dir };
}
function applyToon(mat, t) {
  if (!t) return mat;
  mat.userData.mgToon = true;
  mat.toneMapped = false;   // flat colours, as Blender drew them
  const key = t.stops.map(fx).join(',') + '|' + t.ratios.map(fx).join(',') + '|' + t.dir.toArray().map(fx).join(',');
  mat.customProgramCacheKey = () => 'mg-toon-' + key;
  mat.onBeforeCompile = (sh) => {
    worldVaryings(sh);
    let bands = `float k = ${fx(t.ratios[0])};\n`;
    for (let i = 1; i < t.stops.length; i++) bands += `  if (f >= ${fx(t.stops[i])}) k = ${fx(t.ratios[i])};\n`;
    sh.fragmentShader = 'varying vec3 vMgPos;\nvarying vec3 vMgNrm;\n' + sh.fragmentShader.replace('#include <opaque_fragment>',
      `{
  float f = dot(normalize(vMgNrm), vec3(${t.dir.toArray().map(fx).join(', ')})) * 0.5 + 0.5;
  ${bands}
  outgoingLight = diffuseColor.rgb * k;
}
#include <opaque_fragment>`);
  };
  mat.needsUpdate = true;
  return mat;
}
// A copy of a material keeps its pattern or toon drawing (the shader hook is not copied by clone()).
function restyle(copy, src) {
  if (src.userData.mgPattern) applyPattern(copy, src.userData.mgPattern);
  else if (src.userData.mgToon) applyToon(copy, toonOf(src));
  return copy;
}
// An older file's toon material: black, with all its colour in a glow.
function glowOnly(mat) {
  return !!(mat && mat.emissive && !mat.emissiveMap && Math.max(mat.color.r, mat.color.g, mat.color.b) < 0.02
    && Math.max(mat.emissive.r, mat.emissive.g, mat.emissive.b) > 0.1);
}

export function createViewer(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;   // as Blender's renders (AgX), so colours match them
  renderer.localClippingEnabled = true;
  renderer.autoClear = false;   // the gizmo draws over the scene in the same frame
  const canvas = renderer.domElement;
  container.appendChild(canvas);
  if (getComputedStyle(container).position === 'static') container.style.position = 'relative';

  // Labels drawn over the 3D view (dimensions, measurements) are plain elements here.
  const overlay = document.createElement('div');
  overlay.className = 'v3-overlay';
  overlay.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden';
  container.appendChild(overlay);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(studioEnvironment(), 0.02).texture;
  // Blender's studio (rig.add_lights): key front-left above, fill front-right low, rim behind,
  // at the same angles and the same 3 : 1 : 2 balance.
  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.position.copy(orbit(-45, 40));
  const keyFill = new THREE.DirectionalLight(0xffffff, 0.4);
  keyFill.position.copy(orbit(50, 15));
  const rim = new THREE.DirectionalLight(0xffffff, 0.8);
  rim.position.copy(orbit(160, 45));
  scene.add(sun, keyFill, rim);
  const fill = new THREE.HemisphereLight(0xffffff, 0x444444, 0);
  scene.add(fill);

  const persp = new THREE.PerspectiveCamera(40, 1, 0.001, 1000);
  persp.position.set(1, 0.8, 1.5);
  const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, 1000);
  let orthoHalf = 1;        // half the height the orthographic camera shows at zoom 1
  let camera = persp;
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.autoRotateSpeed = 2.0;

  const v1 = new THREE.Vector3(), v2 = new THREE.Vector3(), ndc = new THREE.Vector2();
  const raycaster = new THREE.Raycaster();
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const listeners = {};
  const emit = (name, data) => (listeners[name] || []).forEach((f) => f(data));

  let model = null;
  let meshes = [];          // every mesh of the model; mesh.userData.mgOriginal keeps its own material
  let parts = [];           // { id, name, meshes, triangles, visible }
  let modelBox = new THREE.Box3();
  let radius = 1;
  let grid = null;
  let loads = 0;
  let disposables = [];     // geometries and materials made for the current model

  // ---- motion: the animator's loop, puffs at the smoke and steam markers, the sliding ground
  let mixer = null, clips = [], playing = false, fx = [], travel = null, currentClip = null;
  const clock = new THREE.Clock();
  let puffTexture = null;
  const FX_COLOURS = { smoke: 0x4a4a4a, steam: 0xf4f4f4 };
  const FX_LIFE = 1.6;      // seconds a puff lives
  // Blender is Z-up with the front on -Y; glTF is Y-up with the front on +Z: (x, y, z) -> (x, z, -y).
  const fromBlender = (v) => new THREE.Vector3(+v[0] || 0, +v[2] || 0, -(+v[1] || 0));

  function softDisc() {
    if (puffTexture) return puffTexture;
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.45, 'rgba(255,255,255,.55)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    puffTexture = new THREE.CanvasTexture(c);
    puffTexture.colorSpace = THREE.SRGBColorSpace;
    return puffTexture;
  }

  // A marker from the file (an empty with mg_fx in its extras) becomes a pool of sprites that
  // rise, grow, fade and are born again at the marker.
  function makeFx(node, u) {
    const size = +u.mg_fx_size || radius * 0.08, rate = +u.mg_fx_rate || 12;
    const group = new THREE.Group();
    scene.add(group);
    const puffs = [];
    for (let i = 0; i < Math.max(8, Math.round(rate * FX_LIFE * 1.3)); i++) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: softDisc(), color: FX_COLOURS[u.mg_fx],
        transparent: true, depthWrite: false, opacity: 0 }));
      s.visible = false;
      group.add(s);
      puffs.push({ s, age: 0, life: 1, vel: new THREE.Vector3() });
    }
    return { node, kind: u.mg_fx, size, rate, group, puffs, next: 0, origin: new THREE.Vector3() };
  }

  // A rigged character carries named clips (idle, walk...): one plays at a time and the page picks
  // it. Any other model's clips are parts of one motion and all play together.
  function isCharacter() {
    let skinned = false;
    if (model) model.traverse((o) => { if (o.isSkinnedMesh) skinned = true; });
    return skinned;
  }

  function setupMotion(gltf) {
    clips = gltf.animations || [];
    currentClip = null;
    if (clips.length) {
      mixer = new THREE.AnimationMixer(model);
      if (isCharacter()) playClip(clips[0].name);
      else clips.forEach((c) => mixer.clipAction(c).play());
    }
    model.traverse((o) => {
      const u = o.userData || {};
      if (u.mg_motion === 'motion') {
        travel = { speed: +u.mg_speed_mps || 0, forward: fromBlender(u.mg_forward || [0, -1, 0]).normalize() };
      }
      if (FX_COLOURS[u.mg_fx]) fx.push(makeFx(o, u));
    });
    playing = hasMotion() && !reduceMotion;
    clock.getDelta();
  }

  function clearMotion() {
    if (mixer) { mixer.stopAllAction(); if (model) mixer.uncacheRoot(model); mixer = null; }
    clips = []; playing = false; travel = null;
    fx.forEach((f) => { scene.remove(f.group); f.puffs.forEach((p) => p.s.material.dispose()); });
    fx = [];
  }

  function hasMotion() { return !!(clips.length || fx.length); }
  // The named clips a character can play ([] for any other model), and the one playing.
  function clipNames() { return isCharacter() ? clips.map((c) => c.name) : []; }
  function playClip(name) {
    const clip = clips.find((c) => c.name === name);
    if (!clip || !mixer) return false;
    mixer.stopAllAction();
    mixer.clipAction(clip).reset().play();
    currentClip = name;
    if (clipPick && clipPick.value !== name) clipPick.value = name;
    if (!playing) setPlaying(true);
    return true;
  }

  // The clip picker the viewer draws itself over the view's bottom-left corner: shown only for a
  // character with more than one clip.
  let clipPick = null;
  function showClipPicker() {
    if (clipPick) { clipPick.remove(); clipPick = null; }
    const names = clipNames();
    if (names.length < 2) return;
    clipPick = document.createElement('select');
    clipPick.className = 'v3-clip-pick';
    clipPick.title = 'Choose the animation clip to play';
    clipPick.setAttribute('aria-label', clipPick.title);
    clipPick.style.cssText = 'position:absolute;left:8px;bottom:8px;z-index:2;max-width:180px;padding:3px 6px;'
      + 'border:1px solid rgba(128,128,128,.45);border-radius:6px;background:rgba(255,255,255,.88);color:#222;font:12px system-ui,sans-serif';
    names.forEach((n) => { const o = document.createElement('option'); o.value = n; o.textContent = n.replace(/_/g, ' '); clipPick.appendChild(o); });
    clipPick.value = currentClip || names[0];
    clipPick.addEventListener('change', () => { playClip(clipPick.value); emit('clip', { clip: clipPick.value }); });
    container.appendChild(clipPick);
  }
  function setPlaying(on) { playing = hasMotion() && !!on; clock.getDelta(); return playing; }
  function isPlaying() { return playing; }

  function stepMotion() {
    const dt = Math.min(clock.getDelta(), 0.1);
    if (!playing || !model) return;
    if (mixer) mixer.update(dt);
    fx.forEach((f) => {
      f.node.getWorldPosition(f.origin);
      f.next -= dt;
      while (f.next <= 0) {
        f.next += 1 / f.rate;
        const p = f.puffs.find((q) => !q.s.visible);
        if (!p) break;
        p.age = 0;
        p.life = FX_LIFE * (0.8 + Math.random() * 0.4);
        p.s.position.copy(f.origin);
        p.s.position.x += (Math.random() - 0.5) * f.size * 0.4;
        p.s.position.z += (Math.random() - 0.5) * f.size * 0.4;
        const rise = f.size * (f.kind === 'steam' ? 2.2 : 1.6) * (0.8 + Math.random() * 0.4);
        p.vel.set((Math.random() - 0.5) * f.size * 0.5, rise, (Math.random() - 0.5) * f.size * 0.5);
        if (travel && travel.speed > 0) p.vel.addScaledVector(travel.forward, -travel.speed * 0.6);  // trails behind
        p.s.scale.set(f.size, f.size, 1);
        p.s.visible = true;
      }
      f.puffs.forEach((p) => {
        if (!p.s.visible) return;
        p.age += dt;
        const k = p.age / p.life;
        if (k >= 1) { p.s.visible = false; return; }
        p.s.position.addScaledVector(p.vel, dt);
        p.vel.y += f.size * 0.3 * dt;   // hot gas keeps rising a little faster
        const grow = f.size * (1 + 2.2 * k);
        p.s.scale.set(grow, grow, 1);
        p.s.material.opacity = (k < 0.15 ? k / 0.15 : 1 - (k - 0.15) / 0.85) * (f.kind === 'steam' ? 0.75 : 0.55);
      });
    });
    if (grid && travel && travel.speed > 0) {
      // the ground runs backwards under the vehicle; a shift of whole cells is invisible, so it never drifts away
      const cell = grid.userData.cell || 1;
      grid.position.addScaledVector(travel.forward, -travel.speed * dt);
      grid.position.x -= cell * Math.round(grid.position.x / cell);
      grid.position.z -= cell * Math.round(grid.position.z / cell);
    }
  }

  // ---- background, grid and theme-dependent colours --------------------------------
  let bgMode = 'theme';
  const bgColor = new THREE.Color();
  const wireLine = new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.3, toneMapped: false });
  function isDarkBg() { const hsl = {}; bgColor.getHSL(hsl, THREE.SRGBColorSpace); return hsl.l < 0.5; }
  function background() {
    const value = bgMode === 'theme'
      ? (getComputedStyle(container).getPropertyValue('--viewer-bg').trim() || '#e9e9e6')
      : BACKGROUNDS[bgMode];
    bgColor.set(value);
    scene.background = bgColor;
    renderer.setClearColor(bgColor);
    const dark = isDarkBg();
    wireLine.color.set(dark ? 0xffffff : 0x000000);
    wireLine.opacity = dark ? 0.22 : 0.3;
    makeGrid();
  }
  function setBackground(name) {
    bgMode = name === 'light' || name === 'dark' ? name : 'theme';
    background();
    return bgMode;
  }

  let showGrid = true;
  function makeGrid() {
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); grid = null; }
    if (!model) return;
    const dark = isDarkBg();
    const span = Math.pow(10, Math.ceil(Math.log10(radius * 6)));
    grid = new THREE.GridHelper(span, 20, dark ? 0x6a6a6a : 0x888888, dark ? 0x3a3a3a : 0xbbbbbb);
    grid.material.transparent = true;
    grid.material.toneMapped = false;   // the grid keeps its own shade whatever the exposure
    grid.material.opacity = dark ? 0.7 : 0.35;
    grid.userData.cell = span / 20;
    grid.position.y = modelBox.min.y;
    grid.visible = showGrid;
    scene.add(grid);
    makeShadow();
  }

  // A soft shadow on the floor under the model, as the renders show on the studio floor.
  let shadow = null;
  function makeShadow() {
    if (shadow) { scene.remove(shadow); shadow.geometry.dispose(); shadow.material.map.dispose(); shadow.material.dispose(); shadow = null; }
    if (!model) return;
    const size = modelBox.getSize(v1);
    const blob = document.createElement('canvas');
    blob.width = blob.height = 128;
    const g = blob.getContext('2d');
    const fade = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    fade.addColorStop(0, 'rgba(0,0,0,0.55)');
    fade.addColorStop(0.45, 'rgba(0,0,0,0.35)');
    fade.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = fade;
    g.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(blob);
    tex.colorSpace = THREE.SRGBColorSpace;
    shadow = new THREE.Mesh(new THREE.PlaneGeometry(size.x * 1.6, size.z * 1.6),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.set((modelBox.min.x + modelBox.max.x) / 2, modelBox.min.y + radius * 0.002, (modelBox.min.z + modelBox.max.z) / 2);
    shadow.renderOrder = -1;
    scene.add(shadow);
  }
  function setGrid(on) { showGrid = !!on; if (grid) grid.visible = showGrid; return showGrid; }

  function accentHex() { return getComputedStyle(container).getPropertyValue('--accent').trim() || '#4C7DF7'; }

  // ---- size and cameras ---------------------------------------------------------------
  function updateOrtho() {
    ortho.top = orthoHalf; ortho.bottom = -orthoHalf;
    ortho.left = -orthoHalf * persp.aspect; ortho.right = orthoHalf * persp.aspect;
    ortho.updateProjectionMatrix();
  }
  function resize() {
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    persp.aspect = w / h;
    persp.updateProjectionMatrix();
    updateOrtho();
  }
  new ResizeObserver(resize).observe(container);

  // How far the camera stands to fit a sphere of radius r, in the narrower of the two fields of view.
  function fitDistance(r) {
    const v = THREE.MathUtils.degToRad(persp.fov / 2);
    const h = Math.atan(Math.tan(v) * persp.aspect);
    return r / Math.sin(Math.min(v, h)) * 1.15;
  }

  function isOrtho() { return camera === ortho; }
  function setOrtho(on) {
    on = !!on;
    if (on === isOrtho()) return on;
    const tanV = Math.tan(THREE.MathUtils.degToRad(persp.fov / 2));
    if (on) {
      orthoHalf = camera.position.distanceTo(controls.target) * tanV;
      ortho.zoom = 1;
      ortho.position.copy(persp.position);
      ortho.quaternion.copy(persp.quaternion);
      updateOrtho();
      camera = ortho;
    } else {
      const dist = (orthoHalf / ortho.zoom) / tanV;
      const dir = v1.copy(ortho.position).sub(controls.target).normalize();
      persp.position.copy(controls.target).addScaledVector(dir, dist);
      camera = persp;
    }
    controls.object = camera;
    controls.update();
    return on;
  }

  // ---- camera moves: views, fit and reset, eased unless motion is reduced ----------------
  let tween = null;
  function moveTo(pos, target, half, animate) {
    tween = {
      from: { pos: camera.position.clone(), target: controls.target.clone(), half: orthoHalf / ortho.zoom },
      to: { pos, target, half },
      t0: performance.now(),
      dur: animate === false || reduceMotion ? 0 : 450,
    };
    stepTween(tween.t0);
  }
  const qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
  function stepTween(now) {
    if (!tween) return;
    const { from, to } = tween;
    const k = tween.dur ? Math.min(1, (now - tween.t0) / tween.dur) : 1;
    const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
    controls.target.lerpVectors(from.target, to.target, e);
    // swing around the target instead of cutting through the model
    const d0 = v1.copy(from.pos).sub(from.target), d1 = v2.copy(to.pos).sub(to.target);
    const len = THREE.MathUtils.lerp(d0.length(), d1.length(), e);
    d0.normalize(); d1.normalize();
    qb.setFromUnitVectors(d0, d1);
    qa.identity().slerp(qb, e);
    camera.position.copy(controls.target).addScaledVector(d0.applyQuaternion(qa), len);
    if (isOrtho()) {
      orthoHalf = THREE.MathUtils.lerp(from.half, to.half, e);
      ortho.zoom = 1;
      updateOrtho();
    }
    controls.update();
    if (k >= 1) tween = null;
  }

  function shownBox() {
    const box = new THREE.Box3(), b = new THREE.Box3();
    meshes.forEach((m) => {
      if (m.layers.mask === HIDDEN) return;
      if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
      box.union(b.copy(m.geometry.boundingBox).applyMatrix4(m.matrixWorld));
    });
    return box.isEmpty() ? modelBox.clone() : box;
  }

  function lookFrom(dir, animate) {
    if (!model) return;
    const box = shownBox();
    const center = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.01);
    const d = new THREE.Vector3().copy(dir).normalize();
    const dist = fitDistance(r);
    const tanV = Math.tan(THREE.MathUtils.degToRad(persp.fov / 2));
    moveTo(center.clone().addScaledVector(d, dist), center, dist * tanV, animate);
  }
  function setView(name, animate) {
    if (!VIEWS[name]) return null;
    lookFrom(new THREE.Vector3(...VIEWS[name]), animate);
    return name;
  }
  function fit(animate) { lookFrom(new THREE.Vector3().copy(camera.position).sub(controls.target), animate); }
  function reset(animate) { setView('iso', animate === true); }

  function setAutoRotate(on) { controls.autoRotate = !!on; return controls.autoRotate; }
  function setRotateSpeed(speed) { controls.autoRotateSpeed = THREE.MathUtils.clamp(+speed || 2, 0.2, 20); return controls.autoRotateSpeed; }

  // ---- lighting ---------------------------------------------------------------------------
  let lighting = 'studio';
  let exposure = 1;
  function setLighting(name) {
    lighting = LIGHTS[name] ? name : 'studio';
    const l = LIGHTS[lighting];
    renderer.toneMappingExposure = l.exposure * exposure;
    sun.intensity = l.sun;
    keyFill.intensity = l.sun / 3;   // Blender's key : fill : rim is 360 : 120 : 240
    rim.intensity = l.sun * 2 / 3;
    fill.intensity = l.fill;
    return lighting;
  }
  function nextLighting() {
    const names = Object.keys(LIGHTS);
    return setLighting(names[(names.indexOf(lighting) + 1) % names.length]);
  }
  function setExposure(x) { exposure = THREE.MathUtils.clamp(+x || 1, 0.2, 3); setLighting(lighting); return exposure; }

  // ---- materials: display modes, section clipping, selection tint --------------------------
  const clay = new THREE.MeshStandardMaterial({ color: 0x9C9993, roughness: 0.85, metalness: 0, side: THREE.DoubleSide });
  const hiMat = new THREE.MeshBasicMaterial({ color: 0x4C7DF7, transparent: true, opacity: 0.35, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1, toneMapped: false });
  // while a colour is being chosen the selection is outlined, so the tint does not hide the colour
  const hiLine = new THREE.LineBasicMaterial({ color: 0x4C7DF7, toneMapped: false });
  const shared = [clay, hiMat, hiLine, wireLine];
  let allMats = new Set(shared);
  let mode = 'shaded';

  const section = { axis: null, pos: 0.5, flip: false, on: false };
  const sectionPlane = new THREE.Plane();
  function clipTo(m) {
    const planes = section.on ? [sectionPlane] : null;
    m.clippingPlanes = planes;
    // a cut-open model shows its insides, which are back faces
    if (!m.isLineBasicMaterial && m !== hiMat) {
      if (planes) { if (m.userData.mgSide0 == null) m.userData.mgSide0 = m.side; m.side = THREE.DoubleSide; }
      else if (m.userData.mgSide0 != null) { m.side = m.userData.mgSide0; delete m.userData.mgSide0; }
    }
    m.needsUpdate = true;
  }
  function track(m) { allMats.add(m); clipTo(m); return m; }

  function variant(mesh, kind) {
    const key = 'mg_' + kind;
    if (!mesh.userData[key]) {
      const make = (src) => {
        const c = src.clone();
        if (kind === 'offset') restyle(c, src);
        if (kind === 'wire') c.wireframe = true;
        if (kind === 'offset') { c.polygonOffset = true; c.polygonOffsetFactor = 1; c.polygonOffsetUnits = 1; }
        if (kind === 'xray') { c.transparent = true; c.opacity = 0.22; c.depthWrite = false; c.side = THREE.DoubleSide; delete c.userData.mgSide0; }
        disposables.push(c);
        return track(c);
      };
      const orig = mesh.userData.mgOriginal;
      mesh.userData[key] = Array.isArray(orig) ? orig.map(make) : make(orig);
    }
    return mesh.userData[key];
  }

  function applyMode() {
    meshes.forEach((mesh) => {
      const orig = mesh.userData.mgOriginal;
      if (mesh.userData.mgInk) { mesh.visible = mode === 'shaded'; return; }   // the outline only goes with the shaded look
      mesh.material = mode === 'shaded' ? orig
        : mode === 'clay' ? clay
        : variant(mesh, mode === 'wire-shaded' ? 'offset' : mode);
      let lines = mesh.children.find((c) => c.userData.mgHelper === 'wire');
      if (mode === 'wire-shaded' && !lines) {
        lines = new THREE.LineSegments(new THREE.WireframeGeometry(mesh.geometry), wireLine);
        lines.userData.mgHelper = 'wire';
        lines.layers.mask = mesh.layers.mask;
        disposables.push(lines.geometry);
        mesh.add(lines);
      }
      if (lines) lines.visible = mode === 'wire-shaded';
    });
  }
  function setDisplayMode(name) {
    mode = MODES.includes(name) ? name : 'shaded';
    applyMode();
    return mode;
  }
  function setWireframe(on) { return setDisplayMode(on ? 'wire' : 'shaded') === 'wire'; }

  // ---- section cut -----------------------------------------------------------------------------
  let sectionOutline = null;
  function placeSection() {
    const i = 'xyz'.indexOf(section.axis);
    const lo = modelBox.min.getComponent(i), hi = modelBox.max.getComponent(i);
    const at = lo + (hi - lo) * section.pos;
    sectionPlane.normal.set(0, 0, 0).setComponent(i, section.flip ? 1 : -1);
    sectionPlane.constant = section.flip ? -at : at;   // keeps the side at or below the cut, or above when flipped
    return at;
  }
  function drawSectionOutline(at) {
    if (sectionOutline) { scene.remove(sectionOutline); sectionOutline.geometry.dispose(); sectionOutline.material.dispose(); sectionOutline = null; }
    if (!section.on || !model) return;
    const i = 'xyz'.indexOf(section.axis);
    const [a, b] = [0, 1, 2].filter((k) => k !== i);
    const pad = radius * 0.06;
    const lo = modelBox.min, hi = modelBox.max;
    const corner = (ua, ub) => { const p = new THREE.Vector3(); p.setComponent(i, at); p.setComponent(a, ua); p.setComponent(b, ub); return p; };
    const pts = [
      corner(lo.getComponent(a) - pad, lo.getComponent(b) - pad), corner(hi.getComponent(a) + pad, lo.getComponent(b) - pad),
      corner(hi.getComponent(a) + pad, hi.getComponent(b) + pad), corner(lo.getComponent(a) - pad, hi.getComponent(b) + pad),
    ];
    sectionOutline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: accentHex(), transparent: true, opacity: 0.85, depthTest: false, toneMapped: false }));
    sectionOutline.renderOrder = 9;
    scene.add(sectionOutline);
  }
  // setSection({ axis: 'x' | 'y' | 'z', pos: 0..1, flip }) cuts the model; setSection(null) stops.
  function setSection(opts) {
    const was = section.on;
    if (!opts || !opts.axis || !model) section.on = false;
    else {
      section.axis = opts.axis;
      if (opts.pos != null) section.pos = THREE.MathUtils.clamp(+opts.pos, 0, 1);
      if (opts.flip != null) section.flip = !!opts.flip;
      section.on = true;
    }
    if (was !== section.on) allMats.forEach(clipTo);
    drawSectionOutline(section.on ? placeSection() : 0);
    return { on: section.on, axis: section.axis, pos: section.pos, flip: section.flip };
  }

  // ---- labels over the view --------------------------------------------------------------------
  const labels = [];
  function addLabel(group, pos, text) {
    const el = document.createElement('div');
    el.className = 'v3-label v3-' + group;
    el.textContent = text;
    el.style.cssText = 'position:absolute;left:0;top:0;white-space:nowrap;font:600 11px/1.5 system-ui,sans-serif;'
      + 'padding:1px 7px;border-radius:6px;color:#fff;background:' + (group === 'measure' ? accentHex() : 'rgba(20,20,20,.8)');
    overlay.appendChild(el);
    labels.push({ group, el, pos: pos.clone() });
  }
  function removeLabels(group) {
    for (let i = labels.length - 1; i >= 0; i--) if (labels[i].group === group) { labels[i].el.remove(); labels.splice(i, 1); }
  }
  function placeLabels() {
    if (!labels.length) return;
    const w = container.clientWidth, h = container.clientHeight;
    labels.forEach((l) => {
      v1.copy(l.pos).project(camera);
      const off = v1.z > 1 || v1.z < -1;
      l.el.style.display = off ? 'none' : '';
      if (!off) l.el.style.transform = `translate(${((v1.x + 1) / 2) * w}px, ${((1 - v1.y) / 2) * h}px) translate(-50%, -50%)`;
    });
  }

  // ---- lengths ----------------------------------------------------------------------------
  let units = 'auto';
  function setUnits(u) { units = ['mm', 'cm', 'm'].includes(u) ? u : 'auto'; updateDims(); redrawMeasure(); return units; }
  function formatLength(m) {
    const u = units === 'auto' ? (m < 1 ? 'mm' : 'm') : units;
    const n = u === 'mm' ? m * 1000 : u === 'cm' ? m * 100 : m;
    const s = n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2);
    return s + ' ' + u;
  }

  // ---- dimensions --------------------------------------------------------------------------
  let dimsOn = false;
  let dimsBox = null;
  function updateDims() {
    if (dimsBox) { scene.remove(dimsBox); dimsBox.geometry.dispose(); dimsBox.material.dispose(); dimsBox = null; }
    removeLabels('dims');
    if (!dimsOn || !model) return;
    const box = shownBox();
    dimsBox = new THREE.Box3Helper(box, new THREE.Color(isDarkBg() ? 0xdddddd : 0x333333));
    dimsBox.material.transparent = true;
    dimsBox.material.opacity = 0.6;
    dimsBox.material.toneMapped = false;
    scene.add(dimsBox);
    const s = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
    addLabel('dims', new THREE.Vector3(c.x, box.min.y, box.max.z), 'W ' + formatLength(s.x));
    addLabel('dims', new THREE.Vector3(box.max.x, box.min.y, c.z), 'D ' + formatLength(s.z));
    addLabel('dims', new THREE.Vector3(box.max.x, c.y, box.max.z), 'H ' + formatLength(s.y));
  }
  function setDimensions(on) { dimsOn = !!on; updateDims(); return dimsOn; }
  function getDimensions() { const s = shownBox().getSize(new THREE.Vector3()); return { x: s.x, y: s.y, z: s.z }; }

  // ---- parts: select, hide, isolate ---------------------------------------------------------------
  // A piece is one mesh: a part's share of one material. selMeshes is what a colour goes on;
  // selected is the part last clicked, for hide and isolate.
  let selected = null;
  let selMeshes = new Set();
  let hiStyle = 'fill';
  let isolated = null;
  function partInfo(p) {
    const own = p.meshes.filter((m) => !m.userData.mgInk);
    const main = own.reduce((a, m) => ((m.userData.mgArea || 0) > (a.userData.mgArea || 0) ? m : a), own[0]);
    return { id: p.id, name: p.name, triangles: p.triangles, visible: p.visible, colour: main ? currentHex(main) : null };
  }
  function getParts() { return parts.map(partInfo); }
  function drawHighlight() {
    meshes.forEach((m) => { m.children.filter((c) => c.userData.mgHelper === 'hi').forEach((c) => m.remove(c)); });
    if (!selMeshes.size) return;
    hiMat.color.set(accentHex());
    hiLine.color.set(accentHex());
    selMeshes.forEach((m) => {
      let h;
      if (hiStyle === 'edges') {
        if (!m.userData.mgEdges) { m.userData.mgEdges = new THREE.EdgesGeometry(m.geometry, 30); disposables.push(m.userData.mgEdges); }
        h = new THREE.LineSegments(m.userData.mgEdges, hiLine);
      } else h = new THREE.Mesh(m.geometry, hiMat);
      h.userData.mgHelper = 'hi';
      h.layers.mask = m.layers.mask;
      h.renderOrder = 2;
      m.add(h);
    });
  }
  function setHighlightStyle(style) { hiStyle = style === 'edges' ? 'edges' : 'fill'; drawHighlight(); return hiStyle; }
  function selectionChanged() {
    drawHighlight();
    emit('select', selected == null ? null : partInfo(parts[selected]));
    emit('paint', paintInfo());
  }
  // A whole part, as the parts list picks it.
  function select(id) {
    selected = parts[id] ? id : null;
    selMeshes = new Set(selected == null ? [] : parts[selected].meshes.filter((m) => !m.userData.mgInk));
    selectionChanged();
    return selected;
  }
  // A click on the model: the clicked piece's whole colour group, or only the piece (single);
  // add puts it in or takes it out of what is already picked.
  function pick(mesh, how) {
    how = how || {};
    if (!mesh) { if (!how.add) { selected = null; selMeshes = new Set(); } }
    else {
      const set = how.single ? [mesh] : groups[mesh.userData.mgGroup].meshes;
      if (how.add) {
        const all = set.every((m) => selMeshes.has(m));
        set.forEach((m) => (all ? selMeshes.delete(m) : selMeshes.add(m)));
      } else selMeshes = new Set(set);
      selected = mesh.userData.mgPart;
    }
    selectionChanged();
  }
  function selectGroups(keys, add) {
    if (!add) selMeshes = new Set();
    groups.filter((g) => keys.includes(g.key)).forEach((g) => g.meshes.forEach((m) => selMeshes.add(m)));
    selected = null;
    selectionChanged();
    return paintInfo();
  }
  function selectType(type) { return selectGroups(groups.filter((g) => g.type === type).map((g) => g.key)); }

  // ---- colour groups and colours -----------------------------------------------------------------
  let groups = [];          // { id, key, name, mat, meshes, area, type, kind, label }
  let glowModel = false;    // most of the surface glows (an older toon file)
  let colours = { groups: {}, pieces: {} };   // '#rrggbb' by group key, and by 'part|group' for one piece
  let context = '';
  const hexOf = (mat) => { const m = [].concat(mat)[0]; return '#' + (glowOnly(m) ? m.emissive : m.color).getHexString(); };
  const currentHex = (m) => hexOf(m.userData.mgOriginal);
  const baseHex = (g) => hexOf(g.mat);
  const pieceKey = (m) => parts[m.userData.mgPart].name + '|' + groups[m.userData.mgGroup].key;
  const nice = (s) => s.replace(/(\.\d+|#\d+)$/, '').replace(/[_-]+/g, ' ').trim().replace(/^./, (c) => c.toUpperCase());

  function meshArea(m) {
    const g = m.geometry, pos = g.attributes.position;
    if (!pos) return 0;
    const idx = g.index, n = idx ? idx.count : pos.count;
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
    let area = 0;
    for (let i = 0; i + 2 < n; i += 3) {
      a.fromBufferAttribute(pos, idx ? idx.getX(i) : i);
      b.fromBufferAttribute(pos, idx ? idx.getX(i + 1) : i + 1);
      c.fromBufferAttribute(pos, idx ? idx.getX(i + 2) : i + 2);
      area += b.sub(a).cross(c.sub(a)).length() / 2;
    }
    return area * Math.pow(Math.abs(m.matrixWorld.determinant()) || 1, 2 / 3);
  }

  function classify(g) {
    const names = [...new Set(g.meshes.map((m) => parts[m.userData.mgPart].name.toLowerCase()))];
    const words = (s) => { const k = KEYWORDS.find(([, re]) => re.test(s)); return k ? k[0] : null; };
    const mat = [].concat(g.mat)[0];
    let kind = names.every((n) => SCENERY.test(n)) ? 'scenery' : words(g.name.toLowerCase());
    if (!kind && !(glowModel && glowOnly(mat))) {   // a glow-coloured toon surface says nothing by its shine
      const glow = mat.emissive ? Math.max(mat.emissive.r, mat.emissive.g, mat.emissive.b) * (mat.emissiveIntensity ?? 1) : 0;
      if ((mat.transmission || 0) > 0.3 || (mat.transparent && mat.opacity < 0.9)) kind = 'glass';
      else if (glow > 0.1 && !glowModel) kind = 'light';   // when most of the model glows, glow is its colour, not a lamp
      else if ((mat.metalness || 0) > 0.6) kind = 'metal';
    }
    kind = kind || words(names.join(' ')) || 'paint';
    if (kind === 'light') kind = lightKind(g.name.toLowerCase(), names.join(' '), mat);
    g.kind = g.type = kind;
  }

  function buildGroups() {
    const byMat = new Map(), seen = {};
    meshes.forEach((m) => {
      if (m.userData.mgInk) return;   // outlines are not coloured
      const mat = [].concat(m.userData.mgBase)[0];
      let g = byMat.get(mat);
      if (!g) {
        g = { id: groups.length, mat: m.userData.mgBase, name: mat.name || 'material ' + (groups.length + 1), meshes: [], area: 0, partArea: {} };
        byMat.set(mat, g);
        groups.push(g);
      }
      m.userData.mgGroup = g.id;
      m.userData.mgArea = meshArea(m);
      g.meshes.push(m);
      g.area += m.userData.mgArea;
      const pn = parts[m.userData.mgPart].name;
      g.partArea[pn] = (g.partArea[pn] || 0) + m.userData.mgArea;
    });
    const total = groups.reduce((t, g) => t + g.area, 0) || 1;
    glowModel = groups.filter((g) => glowOnly([].concat(g.mat)[0])).reduce((t, g) => t + g.area, 0) / total > 0.5;
    groups.forEach((g) => {
      seen[g.name] = (seen[g.name] || 0) + 1;
      g.key = seen[g.name] > 1 ? g.name + '#' + seen[g.name] : g.name;
      classify(g);
    });
    // the body: the paint with the most surface; with no paint, the largest plain surface
    const bySize = (a, b) => b.area - a.area;
    // the body: the biggest surface people see as the object itself (a car's paint, a house's facade)
    const body = groups.filter((g) => !LIGHT_TYPES.has(g.type) && !['scenery', 'glass', 'tyre', 'trim', 'metal'].includes(g.type)).sort(bySize)[0];
    if (body) body.type = 'body';
    const count = {};
    groups.forEach((g) => { count[g.type] = (count[g.type] || 0) + 1; });
    const biggestPart = (g) => nice(Object.entries(g.partArea).sort((a, b) => b[1] - a[1])[0][0]);
    groups.forEach((g) => {
      g.nice = nice(g.name);
      if (/^(paint(ed)?|material( \d+)?|matte|mat|default|metal|glass|plastic)$/i.test(g.nice)) g.nice = biggestPart(g);
    });
    groups.forEach((g) => {
      // two groups that would read the same (polished_metal, polished_metal.001) are named after their biggest part
      const n = groups.some((o) => o !== g && o.type === g.type && o.nice === g.nice) ? biggestPart(g) : g.nice;
      g.label = g.type === 'body' ? 'Body' : count[g.type] > 1 || g.type === 'paint' ? GROUP_TYPES[g.type] + ' · ' + n : GROUP_TYPES[g.type];
    });
  }

  function getColourGroups() {
    return groups.map((g) => ({ key: g.key, label: g.label, type: g.type, colour: currentHex(g.meshes[0]),
      original: baseHex(g), pieces: g.meshes.length, changed: g.meshes.some((m) => wanted(m)) }));
  }
  // One entry per type present, in the order of GROUP_TYPES: what the group chips show.
  function getColourTypes() {
    return Object.keys(GROUP_TYPES).map((type) => {
      const gs = groups.filter((g) => g.type === type).sort((a, b) => b.area - a.area);
      return gs.length ? { type, label: GROUP_TYPES[type], colour: currentHex(gs[0].meshes[0]), count: gs.length } : null;
    }).filter(Boolean);
  }
  // The colours the model wears now, the biggest surfaces first, without the scenery.
  function getModelPalette() {
    const out = [];
    groups.filter((g) => g.type !== 'scenery').sort((a, b) => b.area - a.area)
      .forEach((g) => g.meshes.forEach((m) => { const h = currentHex(m); if (!out.includes(h)) out.push(h); }));
    return out.slice(0, 12);
  }
  function setColourContext(text) { context = String(text || '').toLowerCase(); }
  function contextKind() { const c = CONTEXTS.find(([, re]) => re.test(context)); return c ? c[0] : null; }
  function shades(hex) {
    const c = new THREE.Color(hex), hsl = {};
    c.getHSL(hsl, THREE.SRGBColorSpace);
    return [0.18, -0.18].map((d) => '#' + new THREE.Color().setHSL(hsl.h, hsl.s, THREE.MathUtils.clamp(hsl.l + d, 0.04, 0.96), THREE.SRGBColorSpace).getHexString());
  }
  function suggestionsFor(gs) {
    const main = gs.slice().sort((a, b) => b.area - a.area)[0];
    const painted = main.type === 'body' || main.type === 'paint';
    const pal = (painted && PALETTES[contextKind()]) || (main.type === 'body' && PALETTES[main.kind]) || PALETTES[main.type] || PALETTES.paint;
    const out = [];
    [...gs.map(baseHex), ...pal, ...shades(baseHex(main))].forEach((h) => { h = h.toLowerCase(); if (!out.includes(h)) out.push(h); });
    return out.slice(0, 18);
  }

  function groupsOf(set) { return [...new Set([...set].map((m) => m.userData.mgGroup))].map((i) => groups[i]); }
  function wanted(m) { return colours.pieces[pieceKey(m)] || colours.groups[groups[m.userData.mgGroup].key] || null; }
  function hasColours() { return Object.keys(colours.groups).length + Object.keys(colours.pieces).length > 0; }

  function paintInfo() {
    if (!selMeshes.size) return null;
    const ms = [...selMeshes], gs = groupsOf(selMeshes);
    const whole = gs.every((g) => g.meshes.every((m) => selMeshes.has(m)));
    const types = [...new Set(gs.map((g) => g.type))];
    const main = gs.slice().sort((a, b) => b.area - a.area)[0];
    let label;
    if (whole && types.length === 1 && gs.length === groups.filter((g) => g.type === types[0]).length) label = GROUP_TYPES[types[0]];
    else if (whole && gs.length === 1) label = main.label;
    else {
      const ps = [...new Set(ms.map((m) => m.userData.mgPart))];
      label = ps.length === 1 ? parts[ps[0]].name : ms.length + ' pieces';
    }
    const cs = [...new Set(ms.map(currentHex))];
    return { label, pieces: ms.length, groups: gs.map((g) => g.key), types, whole, colour: cs.length === 1 ? cs[0] : null,
             changed: ms.some((m) => wanted(m)), any: hasColours(), suggestions: suggestionsFor(gs) };
  }

  // The material a colour is worn with: a copy of the piece's own material with only the colour
  // changed, so shine, metal and see-through stay as they were. Pieces of one material wearing
  // the same colour share one copy (an exported file then has one material for them, not one each).
  let tints = new Map();    // 'material uuid|#hex' -> the recoloured copy
  function tintOf(base, hex, fresh) {
    const key = base.uuid + '|' + hex;
    let t = fresh.get(key) || tints.get(key);
    if (!t) {
      t = track(restyle(base.clone(), base));
      if (glowOnly(base)) t.emissive.set(hex);   // an older toon file: its colour is its glow
      else {
        t.color.set(hex);
        const glow = base.emissive ? Math.max(base.emissive.r, base.emissive.g, base.emissive.b) : 0;
        if (glow > 0) t.emissive.set(hex).multiplyScalar(glow);   // a lamp glows in its new colour
      }
    }
    fresh.set(key, t);
    return t;
  }
  function recolour(user) {
    const fresh = new Map();
    meshes.forEach((m) => {
      if (m.userData.mgInk) return;
      const u = m.userData, want = wanted(m);
      ['mg_wire', 'mg_offset', 'mg_xray'].forEach((k) => { if (u[k]) { [].concat(u[k]).forEach((x) => x.dispose()); delete u[k]; } });
      u.mgOriginal = !want ? u.mgBase : Array.isArray(u.mgBase) ? u.mgBase.map((b) => tintOf(b, want, fresh)) : tintOf(u.mgBase, want, fresh);
    });
    tints.forEach((t, key) => { if (!fresh.has(key)) t.dispose(); });
    tints = fresh;
    applyMode();
    emit('paint', paintInfo());
    emit('colours', { colours: getColours(), user: !!user });
  }
  // Colours what is selected: a whole group keeps its colour by group, a few pieces by piece.
  function setColour(hex) {
    if (!selMeshes.size || !HEX.test(hex)) return null;
    hex = hex.toLowerCase();
    groupsOf(selMeshes).forEach((g) => {
      if (g.meshes.every((m) => selMeshes.has(m))) {
        if (hex === baseHex(g)) delete colours.groups[g.key]; else colours.groups[g.key] = hex;
        g.meshes.forEach((m) => delete colours.pieces[pieceKey(m)]);
      } else g.meshes.forEach((m) => { if (selMeshes.has(m)) colours.pieces[pieceKey(m)] = hex; });
    });
    recolour(true);
    return hex;
  }
  // Back to the colour the team gave it.
  function resetColour() {
    groupsOf(selMeshes).forEach((g) => {
      if (g.meshes.every((m) => selMeshes.has(m))) { delete colours.groups[g.key]; g.meshes.forEach((m) => delete colours.pieces[pieceKey(m)]); }
      else g.meshes.forEach((m) => {
        if (!selMeshes.has(m)) return;
        if (colours.groups[g.key]) colours.pieces[pieceKey(m)] = baseHex(g); else delete colours.pieces[pieceKey(m)];
      });
    });
    recolour(true);
  }
  function resetAllColours() { colours = { groups: {}, pieces: {} }; recolour(true); }
  function getColours() { return { groups: { ...colours.groups }, pieces: { ...colours.pieces } }; }
  // Colours saved earlier for this model; names it no longer has are dropped.
  function setColours(saved) {
    const keys = new Set(groups.map((g) => g.key));
    const pieces = new Set(meshes.filter((m) => !m.userData.mgInk).map(pieceKey));
    const keep = (obj, ok) => Object.fromEntries(Object.entries(obj || {}).filter(([k, v]) => ok.has(k) && HEX.test(v)).map(([k, v]) => [k, v.toLowerCase()]));
    colours = { groups: keep(saved && saved.groups, keys), pieces: keep(saved && saved.pieces, pieces) };
    recolour(false);
    return getColours();
  }

  // The model as a GLB, wearing the colours chosen here, with its motion.
  async function exportGlb() {
    if (!model) return null;
    const { GLTFExporter } = await import('three/addons/exporters/GLTFExporter.js');
    const saved = [];
    model.traverse((o) => {
      const u = o.userData;
      saved.push([o, u, o.visible, o.material]);
      o.userData = Object.fromEntries(Object.entries(u).filter(([k]) => !RUNTIME_KEY.test(k)));
      if (u.mgHelper) o.visible = false;
      if (o.isMesh && u.mgOriginal) o.material = u.mgOriginal;
    });
    try {
      return await new GLTFExporter().parseAsync(model, { binary: true, animations: clips, onlyVisible: true });
    } finally {
      saved.forEach(([o, u, vis, mat]) => { o.userData = u; o.visible = vis; if (mat) o.material = mat; });
    }
  }
  function showPart(p, on) {
    p.visible = !!on;
    const mask = on ? 1 : HIDDEN;
    p.meshes.forEach((m) => { m.layers.mask = mask; m.children.forEach((c) => { if (c.userData.mgHelper) c.layers.mask = mask; }); });
  }
  function changedVisibility() { updateDims(); emit('parts', getParts()); }
  function setPartVisible(id, on) {
    if (!parts[id]) return null;
    showPart(parts[id], on);
    isolated = null;
    changedVisibility();
    return parts[id].visible;
  }
  // Show only one part; asking again for the same part (or for none) shows everything.
  function isolate(id) {
    if (id == null || !parts[id] || isolated === id) { isolated = null; parts.forEach((p) => showPart(p, true)); }
    else { isolated = id; parts.forEach((p) => showPart(p, p.id === id)); }
    changedVisibility();
    return isolated;
  }
  function showAll() { return isolate(null); }

  // ---- picking and measuring ----------------------------------------------------------------------
  function hitAt(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(meshes.filter((m) => m.layers.mask !== HIDDEN && !m.userData.mgInk), false);
    return hits.find((h) => !section.on || sectionPlane.distanceToPoint(h.point) >= 0) || null;
  }

  let measureOn = false;
  const mpts = [];
  let mgroup = null;
  const dotGeo = new THREE.SphereGeometry(1, 16, 12);
  const mDot = new THREE.MeshBasicMaterial({ color: 0x4C7DF7, depthTest: false, toneMapped: false });
  const mLine = new THREE.LineBasicMaterial({ color: 0x4C7DF7, depthTest: false, toneMapped: false });
  function redrawMeasure() {
    if (mgroup) { scene.remove(mgroup); mgroup.children.forEach((c) => { if (c.isLine) c.geometry.dispose(); }); mgroup = null; }
    removeLabels('measure');
    if (!mpts.length) return;
    mDot.color.set(accentHex()); mLine.color.set(accentHex());
    mgroup = new THREE.Group();
    mpts.forEach((p) => {
      const s = new THREE.Mesh(dotGeo, mDot);
      s.scale.setScalar(radius * 0.012);
      s.position.copy(p);
      s.renderOrder = 10;
      mgroup.add(s);
    });
    if (mpts.length === 2) {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(mpts), mLine);
      line.renderOrder = 10;
      mgroup.add(line);
      addLabel('measure', v1.copy(mpts[0]).add(mpts[1]).multiplyScalar(0.5), formatLength(mpts[0].distanceTo(mpts[1])));
    }
    scene.add(mgroup);
  }
  function clearMeasure() { mpts.length = 0; redrawMeasure(); emit('measure', null); }
  function setMeasure(on) {
    measureOn = !!on;
    canvas.style.cursor = measureOn ? 'crosshair' : '';
    clearMeasure();
    return measureOn;
  }
  function addMeasurePoint(p) {
    if (mpts.length >= 2) mpts.length = 0;
    mpts.push(p.clone());
    redrawMeasure();
    const d = mpts.length === 2 ? mpts[0].distanceTo(mpts[1]) : null;
    emit('measure', { points: mpts.length, distance: d, text: d == null ? '' : formatLength(d) });
  }

  // ---- axis gizmo -------------------------------------------------------------------------------
  let showGizmo = true;
  const gizmo = makeGizmo();
  function gizmoRect() {
    return { x: (container.clientWidth || 1) - GIZMO - 6, y: 6, size: GIZMO };   // y counts from the bottom
  }
  function gizmoHit(clientX, clientY) {
    if (!showGizmo || !model) return null;
    const r = canvas.getBoundingClientRect(), g = gizmoRect();
    const x = clientX - r.left, y = r.height - (clientY - r.top);
    if (x < g.x || x > g.x + g.size || y < g.y || y > g.y + g.size) return null;
    ndc.set(((x - g.x) / g.size) * 2 - 1, ((y - g.y) / g.size) * 2 - 1);
    raycaster.setFromCamera(ndc, gizmo.cam);
    const hit = raycaster.intersectObjects(gizmo.targets, false)[0];
    return hit ? hit.object.userData.view : 'none';
  }
  function setGizmo(on) { showGizmo = !!on; return showGizmo; }

  // ---- clicks on the view -------------------------------------------------------------------
  let down = null;
  canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, b: e.button }; });
  canvas.addEventListener('pointerup', (e) => {
    const d = down; down = null;
    if (!d || d.b !== 0 || e.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) return;
    const g = gizmoHit(e.clientX, e.clientY);
    if (g) { if (g !== 'none') { setView(g, true); emit('view', g); } return; }
    if (!model) return;
    const hit = hitAt(e.clientX, e.clientY);
    if (measureOn) { if (hit) addMeasurePoint(hit.point); return; }
    pick(hit ? hit.object : null, { single: e.altKey, add: e.ctrlKey || e.shiftKey || e.metaKey });
  });

  // ---- loading ---------------------------------------------------------------------------------
  function collect(gltf) {
    const parser = gltf.parser;
    const assoc = parser && parser.associations;
    const nodeIndex = (o) => { const a = assoc && assoc.get(o); return a && a.nodes !== undefined ? a.nodes : null; };
    model.traverse((o) => { if (o.isMesh) { o.userData.mgOriginal = o.userData.mgBase = o.material; [].concat(o.material).forEach(track); meshes.push(o); } });
    const addPart = (name, own) => {
      const id = parts.length;
      let tris = 0;
      own.forEach((m) => {
        m.userData.mgPart = id;
        const g = m.geometry, pos = g.attributes.position;
        if (pos) tris += (g.index ? g.index.count : pos.count) / 3;
      });
      parts.push({ id, name: name || 'Part ' + (id + 1), meshes: own, triangles: Math.round(tris), visible: true });
    };
    // A part is a node from the file that carries geometry of its own; a node whose mesh has
    // several materials arrives as a group of meshes, one per material.
    // The loader keeps each node's own name in userData.name (its association record can be
    // shared between copies of a reused mesh, so the node index there is not reliable).
    model.traverse((o) => {
      if (nodeIndex(o) == null) return;
      const own = o.isMesh ? [o] : o.children.filter((c) => c.isMesh && nodeIndex(c) == null);
      if (own.length) addPart(o.userData.name || o.name, own);
    });
    meshes.forEach((m) => { if (m.userData.mgPart == null) addPart(m.name || (m.parent && m.parent.name), [m]); });
    // a toon model's outline: a black shell drawn from its inside; not a part anyone colours
    let ink = null;
    meshes.forEach((m) => {
      if (![].concat(m.userData.mgBase).some((mat) => mat.userData && mat.userData.mg_ink)) return;
      if (!ink) { ink = track(new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false })); disposables.push(ink); }
      m.material = m.userData.mgBase = m.userData.mgOriginal = ink;
      m.userData.mgInk = true;
    });
    // toon materials drawn in their bands; patterns for brick, slate and the like (one the file gave
    // no colour at all starts in its usual colour)
    const seen = new Set();
    meshes.forEach((m) => [].concat(m.userData.mgBase).forEach((mat) => {
      if (seen.has(mat) || m.userData.mgInk) return;
      seen.add(mat);
      // brushed metal is drawn plain: its streaks need surface directions along the brushing, and a
      // file's directions follow its picture layout instead (no directions turned whole bands black,
      // a can lid's rim; layout directions made white blotches). The baked roughness picture
      // already carries the brushed look.
      if (mat.anisotropy > 0) { mat.anisotropy = 0; mat.needsUpdate = true; }
      // water and clear glass the file marks with a see-through strength: drawn as see-through at
      // that strength, not as refracting glass (small drops of that come out near black here)
      const alpha = mat.userData && mat.userData.mg_alpha;
      if (mat.transmission > 0 && typeof alpha === 'number') {
        mat.transmission = 0;
        mat.transparent = true;
        mat.opacity = alpha;
        mat.depthWrite = false;
        mat.needsUpdate = true;
      }
      const toon = toonOf(mat);
      if (toon) { applyToon(mat, toon); return; }
      if (mat.map) return;   // the file carries the pattern as a baked picture: draw that, not ours
      const kind = patternFor(mat);
      if (!kind) return;
      const a = assoc && assoc.get(mat);
      const def = a && a.materials !== undefined && parser.json.materials ? parser.json.materials[a.materials] : null;
      if (def && !(def.pbrMetallicRoughness && def.pbrMetallicRoughness.baseColorFactor)) mat.color.set(PATTERNS[kind].colour);
      applyPattern(mat, kind);
    }));
    // a drawn character's ink outline: the mesh again, pushed out along its normals by the width
    // the file gives, its inside drawn black; skinned like the mesh, so it follows every clip
    meshes.slice().forEach((m) => {
      const width = Math.max(0, ...[].concat(m.userData.mgBase).map((mat) => +(mat.userData && mat.userData.mg_outline) || 0));
      if (!width || !m.isSkinnedMesh || m.userData.mgInk) return;
      const ink = new THREE.MeshBasicMaterial({ color: 0x050505, side: THREE.BackSide, toneMapped: false });
      ink.onBeforeCompile = (sh) => {
        sh.vertexShader = sh.vertexShader.replace('#include <begin_vertex>',
          `#include <begin_vertex>\n  transformed += normalize(normal) * ${fx(width)};`);
      };
      ink.customProgramCacheKey = () => 'mg-outline-' + fx(width);
      disposables.push(ink);
      const line = new THREE.SkinnedMesh(m.geometry, ink);
      line.bind(m.skeleton, m.bindMatrix);
      line.userData.mgOutlineOf = m.uuid;
      line.raycast = () => {};   // never picked: clicking selects the part itself
      m.parent.add(line);
      line.position.copy(m.position); line.quaternion.copy(m.quaternion); line.scale.copy(m.scale);
    });
    buildGroups();
  }

  function getStats() {
    let triangles = 0, vertices = 0;
    const mats = new Set();
    meshes.forEach((m) => {
      const g = m.geometry, pos = g.attributes.position;
      if (!pos) return;
      vertices += pos.count;
      triangles += (g.index ? g.index.count : pos.count) / 3;
      [].concat(m.userData.mgBase).forEach((x) => mats.add(x));
    });
    const s = modelBox.getSize(new THREE.Vector3());
    return { triangles: Math.round(triangles), vertices, meshes: meshes.length, parts: parts.length, materials: mats.size,
             size: { x: s.x, y: s.y, z: s.z } };
  }

  function disposeObject(object) {
    object.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) [].concat(o.material).forEach((m) => m.dispose());
    });
  }

  // Two boxes close enough in place and size that the same camera still frames both (within 25%).
  function sameFrame(a, b) {
    const ra = a.getSize(new THREE.Vector3()).length() / 2, rb = b.getSize(new THREE.Vector3()).length() / 2;
    const r = Math.max(ra, rb, 1e-6);
    return Math.abs(ra - rb) <= 0.25 * r && a.getCenter(new THREE.Vector3()).distanceTo(b.getCenter(new THREE.Vector3())) <= 0.25 * r;
  }

  // opts.keepView: keep the camera the person set (the next step of a build replacing the last).
  // A model about the same size and place keeps the camera exactly; a very different one keeps
  // the viewing angle and is refitted. Without it the camera goes to the 3/4 view.
  async function load(url, opts) {
    const ticket = ++loads;
    const gltf = await new GLTFLoader().loadAsync(url);
    if (ticket !== loads) { disposeObject(gltf.scene); return; }  // a newer load was asked for meanwhile
    const before = opts && opts.keepView && model ? modelBox.clone() : null;
    clear();
    model = gltf.scene;
    scene.add(model);
    model.updateMatrixWorld(true);
    collect(gltf);
    modelBox = new THREE.Box3().setFromObject(model);
    radius = Math.max(modelBox.getSize(v1).length() / 2, 0.01);
    for (const c of [persp, ortho]) { c.near = radius / 100; c.far = radius * 100; c.updateProjectionMatrix(); }
    applyMode();
    makeGrid();
    setupMotion(gltf);
    showClipPicker();
    if (!before) setView('iso', false);
    else if (!sameFrame(before, modelBox)) fit(false);
    updateDims();
    emit('load', { stats: getStats(), parts: getParts(), motion: hasMotion(), playing, clips: clipNames() });
  }

  function clear() {
    tween = null;
    clearMotion();
    if (clipPick) { clipPick.remove(); clipPick = null; }
    if (measureOn || mpts.length) clearMeasure();
    if (section.on) setSection(null);
    if (selected != null || selMeshes.size) select(null);
    isolated = null;
    if (model) {
      scene.remove(model);
      meshes.forEach((m) => { m.geometry.dispose(); [].concat(m.userData.mgBase, m.userData.mgOriginal).forEach((x) => x.dispose()); });
      disposables.forEach((d) => d.dispose());
      model = null;
    }
    meshes = []; parts = []; groups = []; disposables = [];
    colours = { groups: {}, pieces: {} };
    tints = new Map();
    allMats = new Set(shared);
    makeGrid();
    updateDims();
  }

  // ---- drawing -------------------------------------------------------------------------------
  const savedViewport = new THREE.Vector4();
  function draw(withGizmo) {
    renderer.clear();
    renderer.render(scene, camera);
    if (withGizmo && showGizmo && model) {
      gizmo.group.quaternion.copy(camera.quaternion).invert();
      const g = gizmoRect();
      renderer.clearDepth();
      renderer.getViewport(savedViewport);
      renderer.setViewport(g.x, g.y, g.size, g.size);
      renderer.render(gizmo.scene, gizmo.cam);
      renderer.setViewport(savedViewport);
    }
  }

  // A PNG of the view as it is, at up to twice the screen size, with the labels drawn in.
  function screenshot() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    const pr = renderer.getPixelRatio();
    const scale = Math.min(2, 4096 / Math.max(w, h));
    renderer.setPixelRatio(scale);
    renderer.setSize(w, h, false);
    draw(false);
    const out = document.createElement('canvas');
    out.width = canvas.width; out.height = canvas.height;
    const g = out.getContext('2d');
    g.drawImage(canvas, 0, 0);
    renderer.setPixelRatio(pr);
    renderer.setSize(w, h, false);
    draw(true);
    g.scale(out.width / w, out.height / h);
    g.font = '600 11px system-ui, sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    labels.forEach((l) => {
      v1.copy(l.pos).project(camera);
      if (v1.z > 1 || v1.z < -1) return;
      const x = ((v1.x + 1) / 2) * w, y = ((1 - v1.y) / 2) * h;
      const text = l.el.textContent, tw = g.measureText(text).width + 14;
      g.fillStyle = l.group === 'measure' ? accentHex() : 'rgba(20,20,20,.8)';
      g.beginPath();
      g.roundRect(x - tw / 2, y - 9, tw, 18, 6);
      g.fill();
      g.fillStyle = '#fff';
      g.fillText(text, x, y + 0.5);
    });
    return out.toDataURL('image/png');
  }

  background();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', background);
  resize();
  setLighting('studio');

  renderer.setAnimationLoop((now) => {
    stepTween(now);
    stepMotion();
    controls.update();
    draw(true);
    placeLabels();
  });

  return {
    load, clear, reset, setAutoRotate, setWireframe, setLighting, nextLighting,
    refreshBackground: background, hasModel: () => !!model,
    hasMotion, setPlaying, isPlaying, clipNames, playClip, getClip: () => currentClip,
    on(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    fit, setView, setOrtho, isOrtho, setRotateSpeed,
    setDisplayMode, getDisplayMode: () => mode,
    getLighting: () => lighting, setExposure, setBackground, setGrid, setGizmo,
    setDimensions, getDimensions, setUnits, formatLength,
    getStats, getParts, select, setPartVisible, isolate, showAll, getIsolated: () => isolated,
    setMeasure, clearMeasure, setSection, screenshot,
    selectGroups, selectType, getPaint: paintInfo, setHighlightStyle,
    getColourGroups, getColourTypes, getModelPalette, setColourContext,
    setColour, resetColour, resetAllColours, getColours, setColours, hasColours, exportGlb,
  };
}

function makeGizmo() {
  const scene = new THREE.Scene();
  const cam = new THREE.OrthographicCamera(-1.55, 1.55, 1.55, -1.55, 0, 10);
  cam.position.set(0, 0, 5);
  const group = new THREE.Group();
  scene.add(group);
  const targets = [];
  const bar = new THREE.BoxGeometry(0.85, 0.07, 0.07).translate(0.425, 0, 0);
  const axes = [
    ['x', 0xE5675C, [1, 0, 0], 'right', 'left'],
    ['y', 0x7DBB4A, [0, 1, 0], 'top', 'bottom'],
    ['z', 0x4C7DF7, [0, 0, 1], 'front', 'back'],
  ];
  axes.forEach(([name, color, dir, pos, neg]) => {
    const m = new THREE.Mesh(bar, new THREE.MeshBasicMaterial({ color, toneMapped: false }));
    if (name === 'y') m.rotation.z = Math.PI / 2;
    if (name === 'z') m.rotation.y = -Math.PI / 2;
    group.add(m);
    [[1, pos], [-1, neg]].forEach(([sign, view]) => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: axisLabel(color, sign > 0 ? name.toUpperCase() : ''),
        toneMapped: false, transparent: true, opacity: sign > 0 ? 1 : 0.6 }));
      s.position.set(dir[0] * sign * 1.08, dir[1] * sign * 1.08, dir[2] * sign * 1.08);
      s.scale.setScalar(sign > 0 ? 0.6 : 0.42);
      s.userData.view = view;
      group.add(s);
      targets.push(s);
    });
  });
  return { scene, cam, group, targets };
}

function axisLabel(color, text) {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d');
  g.fillStyle = '#' + new THREE.Color(color).getHexString();
  g.beginPath();
  g.arc(32, 32, text ? 30 : 22, 0, Math.PI * 2);
  g.fill();
  if (text) {
    g.fillStyle = '#fff';
    g.font = 'bold 36px system-ui, sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, 32, 35);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
