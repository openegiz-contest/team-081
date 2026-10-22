// Aktogay haulage sector: 3D scene and dispatcher panel.
// Everything shown comes from the twins in Eclipse Ditto: the scene geometry
// from twin attributes, the live state from an SSE stream of twin changes.
// What-if scenarios write desiredProperties; the simulator applies them and
// reports the value back in properties.

import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from './vendor/CSS2DRenderer.js';

const API = 'ditto/api/2';
const NS = 'kz.aktogay';
const id = (name) => `${NS}:${name}`;
const things = new Map();

const css = getComputedStyle(document.documentElement);
const C = (v) => new THREE.Color(css.getPropertyValue(v).trim());
const COLORS = {
  empty: C('--truck-empty'), sulphide: C('--sulphide'), oxide: C('--oxide'),
  waste: C('--waste'), down: C('--down'),
};
const MATERIAL_RU = { sulphide: 'сульфидная руда', oxide: 'окисленная руда', waste: 'вскрыша', '': '—' };
const STATE_RU = {
  idle: 'ожидает наряд', to_shovel: 'порожний к забою', queued_shovel: 'в очереди у забоя', loading: 'погрузка',
  hauling: 'гружёный рейс', queued_dump: 'в очереди на разгрузку', dumping: 'разгрузка', to_fuel: 'на заправку',
  refueling: 'заправка', blocked: 'нет проезда', down: 'сход с линии',
  waiting_for_trucks: 'ждёт самосвалы', crushing: 'дробит', starved: 'нет питания',
};
const fmt = (n, d = 0) => (n ?? 0).toLocaleString('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: d });
const f = (thing, feature, prop = 'value') => thing?.features?.[feature]?.properties?.[prop];
const desired = (thing, feature) => thing?.features?.[feature]?.desiredProperties?.value;

// --------------------------------------------------------------- data
function deepMerge(target, patch) {
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete target[k];
    else if (typeof v === 'object' && !Array.isArray(v) && typeof target[k] === 'object' && target[k] && !Array.isArray(target[k])) deepMerge(target[k], v);
    else target[k] = v;
  }
}

async function loadThings() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const r = await fetch(`${API}/search/things?namespaces=${NS}&option=size(200)`);
      if (r.ok) {
        const { items } = await r.json();
        if (items.length >= 40) { items.forEach((t) => things.set(t.thingId, t)); return; }
      }
    } catch (e) { /* retry */ }
    await new Promise((res) => setTimeout(res, 2000));
  }
  throw new Error('twins not found in Ditto');
}

function connectStream() {
  const conn = document.getElementById('conn');
  const es = new EventSource(`${API}/things?namespaces=${NS}`);
  es.onopen = () => { conn.textContent = 'Ditto: поток двойников подключён'; conn.classList.add('ok'); };
  es.onerror = () => { conn.textContent = 'Ditto: переподключение…'; conn.classList.remove('ok'); };
  es.onmessage = (ev) => {
    if (!ev.data) return;
    const patch = JSON.parse(ev.data);
    const t = things.get(patch.thingId);
    if (t) deepMerge(t, patch); else things.set(patch.thingId, patch);
    dirty = true;
  };
}

async function setDesired(thingId, feature, value) {
  const t = things.get(thingId);
  if (t) { t.features[feature] = t.features[feature] || {}; t.features[feature].desiredProperties = { value }; }
  dirty = true;
  await fetch(`${API}/things/${thingId}/features/${feature}/desiredProperties/value`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  });
}

// --------------------------------------------------------------- scene
const host = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
host.appendChild(renderer.domElement);
const labels = new CSS2DRenderer();
labels.domElement.style.position = 'absolute';
labels.domElement.style.inset = '0';
labels.domElement.style.pointerEvents = 'none';
host.appendChild(labels.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#1b2430');
scene.fog = new THREE.Fog('#1b2430', 4500, 9000);
const camera = new THREE.PerspectiveCamera(45, 1, 10, 20000);
camera.position.set(1900, 1900, 2600);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, -120, 0);
controls.maxPolarAngle = Math.PI * 0.47;
scene.add(new THREE.HemisphereLight('#dfe8f0', '#3a2f25', 1.1));
const sun = new THREE.DirectionalLight('#fff3e0', 1.4);
sun.position.set(-2000, 3000, 1500);
scene.add(sun);

// Ditto/model frame (x east, y north, z up) -> three.js (y up).
const V = (p) => new THREE.Vector3(p[0], p[2], -p[1]);

function label(text, cls = '') {
  const div = document.createElement('div');
  div.className = `lbl ${cls}`;
  div.textContent = text;
  return new CSS2DObject(div);
}

const pickable = [];
function pick(mesh, thingId) { mesh.userData.thingId = thingId; pickable.push(mesh); return mesh; }

function ellipseRing(a, b, z, n = 128) {
  const pts = [];
  for (let i = 0; i <= n; i++) { const t = (i / n) * Math.PI * 2; pts.push(new THREE.Vector3(a * Math.cos(t), z, -b * Math.sin(t))); }
  return pts;
}

function strip(outer, inner, color) {
  // triangles between two rings with the same point count
  const pos = [];
  for (let i = 0; i < outer.length - 1; i++) {
    const a = outer[i], b = outer[i + 1], c = inner[i], d = inner[i + 1];
    pos.push(a.x, a.y, a.z, c.x, c.y, c.z, b.x, b.y, b.z, b.x, b.y, b.z, c.x, c.y, c.z, d.x, d.y, d.z);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color, side: THREE.DoubleSide, flatShading: true }));
}

function buildPit(pit) {
  const A = pit.attributes;
  const berm = A.bench_step_m * 0.45;
  const group = new THREE.Group();
  // ground with the pit opening
  const ground = new THREE.Shape();
  ground.moveTo(-4500, -4000); ground.lineTo(4500, -4000); ground.lineTo(4500, 4000); ground.lineTo(-4500, 4000);
  const hole = new THREE.Path();
  ellipseRing(A.rim_a_m, A.rim_b_m, 0, 128).forEach((p, i) => (i ? hole.lineTo(p.x, -p.z) : hole.moveTo(p.x, -p.z)));
  ground.holes.push(hole);
  const gm = new THREE.Mesh(new THREE.ShapeGeometry(ground, 4), new THREE.MeshLambertMaterial({ color: '#8a7a62' }));
  gm.rotation.x = -Math.PI / 2;
  group.add(gm);
  for (let k = 0; k < A.benches; k++) {
    const a = A.rim_a_m - A.bench_step_m * k, b = A.rim_b_m - A.bench_step_m * k;
    const z = -A.bench_height_m * k;
    const shade = new THREE.Color('#a08c70').lerp(new THREE.Color('#4f5b63'), k / A.benches);
    const top = ellipseRing(a, b, z), bermIn = ellipseRing(a - berm, b - berm, z);
    const below = ellipseRing(a - A.bench_step_m, b - A.bench_step_m, z - A.bench_height_m);
    if (k) group.add(strip(top, bermIn, shade));
    group.add(strip(k ? bermIn : top, below, shade.clone().multiplyScalar(0.75)));
  }
  const kb = A.benches;
  const floor = new THREE.Shape();
  ellipseRing(A.rim_a_m - A.bench_step_m * kb, A.rim_b_m - A.bench_step_m * kb, 0, 96)
    .forEach((p, i) => (i ? floor.lineTo(p.x, -p.z) : floor.moveTo(p.x, -p.z)));
  const fm = new THREE.Mesh(new THREE.ShapeGeometry(floor), new THREE.MeshLambertMaterial({ color: '#4a555c' }));
  fm.rotation.x = -Math.PI / 2;
  fm.position.y = -A.bench_height_m * kb;
  group.add(fm);
  scene.add(group);
}

const roadMeshes = new Map();
function buildRoad(t) {
  const group = new THREE.Group();
  const mat = new THREE.MeshLambertMaterial({ color: '#d8cfae' });
  for (const s of t.attributes.segments) {
    const pts = s.pts.map((p) => V([p[0], p[1], p[2] + 2]));
    const curve = new THREE.CatmullRomCurve3(pts, false, 'centripetal');
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, Math.max(4, pts.length * 2), 7, 4, false), mat);
    group.add(pick(tube, t.thingId));
  }
  const mid = t.attributes.segments[Math.floor(t.attributes.segments.length / 2)];
  const lbl = label('', 'alert');
  lbl.position.copy(V(mid.pts[Math.floor(mid.pts.length / 2)])).add(new THREE.Vector3(0, 30, 0));
  lbl.visible = false;
  group.add(lbl);
  scene.add(group);
  roadMeshes.set(t.thingId, { mat, lbl });
}

const siteLabels = new Map();
function addSite(thingId, mesh, pos, text) {
  mesh.position.copy(V(pos));
  scene.add(pick(mesh, thingId));
  const l = label(text, 'site');
  l.position.copy(V(pos)).add(new THREE.Vector3(0, 110, 0));
  scene.add(l);
  siteLabels.set(thingId, l);
}

function buildSites() {
  const box = (w, h, d, color) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshLambertMaterial({ color }));
  const cone = (r, h, color) => new THREE.Mesh(new THREE.ConeGeometry(r, h, 24), new THREE.MeshLambertMaterial({ color }));
  const pos = (n) => things.get(id(n)).attributes.position;
  const crusher = box(90, 70, 70, '#6b7f8f'); crusher.geometry.translate(0, 35, 0);
  addSite(id('crusher'), crusher, pos('crusher'), 'ЗКД');
  const sp = cone(130, 45, '#2a8f84'); sp.geometry.translate(0, 22, 0);
  addSite(id('stockpile'), sp, pos('stockpile'), 'Рудный склад');
  const leach = box(700, 25, 450, '#b8693a'); leach.geometry.translate(0, 12, 0);
  addSite(id('leach'), leach, pos('leach'), 'Кучное выщелачивание');
  const dump = cone(520, 90, '#7d858c'); dump.geometry.translate(0, 0, 0);
  addSite(id('dump'), dump, pos('dump'), 'Отвал вскрыши');
  const fuel = box(80, 30, 50, '#c9a227'); fuel.geometry.translate(0, 15, 0);
  addSite(id('fuel'), fuel, pos('fuel'), 'АЗС');
}

const shovelObjs = new Map();
function buildShovels() {
  for (const t of things.values()) {
    if (t.attributes?.kind !== 'loading-unit') continue;
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(50, 30, 40), new THREE.MeshLambertMaterial({ color: '#f0a020' }));
    body.position.y = 22;
    const boom = new THREE.Mesh(new THREE.BoxGeometry(70, 8, 8), new THREE.MeshLambertMaterial({ color: '#3a3a3a' }));
    boom.position.set(45, 35, 0); boom.rotation.z = -0.5;
    g.add(pick(body, t.thingId), boom);
    g.position.copy(V(t.attributes.position));
    const face = things.get(t.attributes.face);
    const mat = face.attributes.material;
    const patch = new THREE.Mesh(new THREE.CylinderGeometry(70, 70, 4, 24), new THREE.MeshLambertMaterial({ color: COLORS[mat] }));
    patch.position.copy(V(face.attributes.position)).add(new THREE.Vector3(0, 1, 0));
    scene.add(pick(patch, face.thingId));
    const l = label('', 'site');
    l.position.set(0, 90, 0);
    g.add(l);
    scene.add(g);
    shovelObjs.set(t.thingId, { g, body, l });
  }
}

const truckObjs = new Map();
function buildTrucks() {
  const geo = new THREE.BoxGeometry(45, 20, 24);
  geo.translate(0, 12, 0);
  for (const t of things.values()) {
    if (t.attributes?.kind !== 'haul-truck') continue;
    const mat = new THREE.MeshLambertMaterial({ color: COLORS.empty });
    const mesh = pick(new THREE.Mesh(geo, mat), t.thingId);
    const l = label(t.thingId.split(':')[1].toUpperCase(), 'truck');
    l.position.set(0, 45, 0);
    mesh.add(l);
    scene.add(mesh);
    truckObjs.set(t.thingId, { mesh, mat, target: null, l });
  }
}

// ----------------------------------------------------------------- update
function updateScene() {
  for (const [tid, o] of truckObjs) {
    const t = things.get(tid);
    const p = t.features?.position?.properties;
    if (!p) continue;
    o.target = V([p.x, p.y, p.z]);
    o.heading = -THREE.MathUtils.degToRad(p.heading || 0);
    if (!o.placed) { o.mesh.position.copy(o.target); o.placed = true; }
    const down = f(t, 'status') === 'down';
    const mat = f(t, 'payload', 'material');
    o.mat.color.copy(down ? COLORS.down : (f(t, 'payload') > 0 ? COLORS[mat] || COLORS.empty : COLORS.empty));
    o.l.element.textContent = `${tid.split(':')[1].toUpperCase()}${down ? ' ✕' : ''}`;
  }
  for (const [tid, o] of roadMeshes) {
    const t = things.get(tid);
    const closed = f(t, 'status') === 'closed' || desired(t, 'status') === 'closed';
    o.mat.color.set(closed ? COLORS.down : '#d8cfae');
    o.lbl.visible = closed;
    o.lbl.element.textContent = `ЗАКРЫТО: ${t.attributes.name}`;
  }
  for (const [tid, o] of shovelObjs) {
    const t = things.get(tid);
    const down = f(t, 'status') === 'down';
    o.body.material.color.set(down ? COLORS.down : '#f0a020');
    const q = f(t, 'queue') || 0;
    o.l.element.textContent = `${tid.split(':')[1].toUpperCase()} · ${down ? 'ОСТАНОВЛЕН' : `${f(t, 'production', 'plan_pct') ?? 0}% плана`}${q ? ` · очередь ${q}` : ''}`;
    o.l.element.className = `lbl site${down ? ' alert' : ''}`;
  }
  const cr = things.get(id('crusher'));
  const crDown = f(cr, 'status') === 'down';
  siteLabels.get(id('crusher')).element.textContent =
    `ЗКД · ${crDown ? 'ОСТАНОВЛЕНА' : `${fmt(f(cr, 'throughput'))} т/ч`} · бункер ${f(cr, 'bin', 'pct') ?? 0}%${f(cr, 'queue') ? ` · очередь ${f(cr, 'queue')}` : ''}`;
  siteLabels.get(id('crusher')).element.className = `lbl site${crDown ? ' alert' : ''}`;
  siteLabels.get(id('stockpile')).element.textContent = `Рудный склад · ${fmt(f(things.get(id('stockpile')), 'received'))} т`;
  siteLabels.get(id('leach')).element.textContent = `Кучное выщелачивание · ${fmt(f(things.get(id('leach')), 'received'))} т`;
  siteLabels.get(id('dump')).element.textContent = `Отвал · ${fmt(f(things.get(id('dump')), 'received'))} т`;
}

function kpi(v, l) { return `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`; }

function activeScenarios() {
  const out = [];
  for (const t of things.values()) {
    const k = t.attributes?.kind;
    if (k === 'haul-road' && desired(t, 'status') === 'closed') out.push(`закрыта: ${t.attributes.name}`);
    if (['loading-unit', 'haul-truck', 'primary-crusher'].includes(k) && desired(t, 'status') === 'down') out.push(`остановлен: ${t.attributes.name}`);
    if (k === 'dispatcher' && desired(t, 'mode') === 'fixed') out.push('диспетчеризация отключена (жёсткое закрепление)');
  }
  return out;
}

function updatePanel() {
  const m = things.get(id('mine'));
  const sh = m.features.shift?.properties || {};
  document.getElementById('shift-no').textContent = sh.number ?? '–';
  document.getElementById('clock').textContent = sh.clock ?? '--:--';
  document.getElementById('elapsed').textContent = fmt(sh.elapsed_h, 1);
  const fl = things.get(id('fleet'));
  document.getElementById('kpis').innerHTML = [
    kpi(`${fmt(f(m, 'sulphide_to_crusher'))} т`, 'сульфидная руда на ЗКД'),
    kpi(`${fmt(f(m, 'sulphide_to_stockpile'))} т`, 'сульфидная руда на склад'),
    kpi(`${fmt(f(m, 'oxide_to_leach'))} т`, 'окисленная руда на КВ'),
    kpi(`${fmt(f(m, 'waste_to_dump'))} т`, 'вскрыша в отвал'),
    kpi(`${fmt(f(m, 'copper_in_ore'), 1)} т`, 'меди в добытой руде'),
    kpi(`${fmt(f(m, 'ore_rate'))} т/ч`, 'темп добычи руды'),
    kpi(`${f(fl, 'trucks_up') ?? '–'} из ${(f(fl, 'trucks_up') ?? 0) + (f(fl, 'trucks_down') ?? 0)}`, 'самосвалов на линии'),
    kpi(`${fmt(f(fl, 'avg_cycle'), 1)} мин`, 'средний цикл самосвала'),
    kpi(`${fmt(f(fl, 'queue_time'), 1)} мин`, 'простой в очередях на самосвал'),
    kpi(`${fmt(f(m, 'fuel_per_tonne'), 3)} л/т`, 'расход топлива'),
  ].join('');

  const d = things.get(id('dispatcher'));
  const fc = d.features.forecast?.properties;
  const scen = activeScenarios();
  const fcEl = document.getElementById('forecast');
  if (fc) {
    const loss = fc.loss_cu_t;
    const rows = [
      ['Сульфидная руда на ЗКД, т', fc.sulphide_crusher_t, fc.baseline_sulphide_crusher_t],
      ['Сульфидная руда на склад, т', fc.sulphide_stockpile_t, fc.baseline_sulphide_stockpile_t],
      ['Окисленная руда на КВ, т', fc.oxide_t, fc.baseline_oxide_t],
      ['Вскрыша, т', fc.waste_t, fc.baseline_waste_t],
      ['Медь в руде, т', fc.cu_t, fc.baseline_cu_t],
    ];
    fcEl.innerHTML = `
      <div class="forecast-num">${fmt(fc.cu_t, 1)} т меди</div>
      <div class="muted">в руде к концу смены (расчёт на ${fc.made_at}, прогон симуляции до 20:00)</div>
      ${scen.length ? `<div>Без сценариев было бы ${fmt(fc.baseline_cu_t, 1)} т:
        <span class="${loss > 0 ? 'loss' : 'gain'}">${loss > 0 ? '−' : '+'}${fmt(Math.abs(loss), 1)} т Cu</span></div>` : ''}
      <table><tr><td></td><td>прогноз</td><td>без сценариев</td></tr>
      ${rows.map(([n, a, b]) => `<tr><td>${n}</td><td>${fmt(a, n.includes('Медь') ? 1 : 0)}</td><td>${fmt(b, n.includes('Медь') ? 1 : 0)}</td></tr>`).join('')}
      </table>`;
  }
  const banner = document.getElementById('banner');
  banner.hidden = !scen.length;
  if (scen.length) banner.textContent = `What-if: ${scen.join('; ')}${fc ? ` · прогноз смены ${fc.loss_cu_t > 0 ? '−' : '+'}${fmt(Math.abs(fc.loss_cu_t), 1)} т Cu` : ''}`;

  const mode = desired(d, 'mode') || f(d, 'mode');
  document.querySelectorAll('#mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  const ld = d.features.last_decision?.properties || {};
  document.getElementById('decision').innerHTML = f(d, 'mode') === 'fixed'
    ? '<span class="muted">Самосвалы закреплены за экскаваторами по плану смены; диспетчер решений не принимает.</span>'
    : `Последний наряд: <b>${ld.value || '—'}</b><br><span class="muted">${ld.reason || ''}</span><br>
       <span class="muted">Решений за смену: ${fmt(f(d, 'decisions'))}, объездов: ${fmt(f(d, 'reroutes'))}</span>`;
  const alloc = d.features.allocation?.properties || {};
  const mf = d.features.match_factor?.properties || {};
  const rows = Object.keys(alloc).map((sid) => {
    const s = things.get(id(sid));
    const pr = s.features.production?.properties || {};
    const pct = Math.min(130, pr.plan_pct || 0);
    return `<tr><td>${sid.toUpperCase()} <span class="muted">${MATERIAL_RU[things.get(s.attributes.face).attributes.material]}</span>
      <div class="bar"><i style="width:${pct / 1.3}%;background:${pct < 90 ? 'var(--down)' : 'var(--ok)'}"></i></div></td>
      <td>${alloc[sid]}</td><td>${f(s, 'queue') ?? 0}</td><td>${fmt(pr.rate_tph)}/${fmt(pr.plan_tph)}</td><td>${pr.plan_pct ?? 0}%</td><td>${mf[sid] ?? '—'}</td></tr>`;
  });
  document.getElementById('alloc').innerHTML =
    `<tr><th>Погрузка</th><th>Сам.</th><th>Очер.</th><th>т/ч факт/план</th><th>План</th><th>MF</th></tr>${rows.join('')}`;

  const log = (f(d, 'log') || '').split(' | ').filter(Boolean);
  document.getElementById('log').innerHTML = log.map((x) => `<li>${x}</li>`).join('') || '<li class="muted">событий нет</li>';
  renderWhatIf();
  if (selected) showTwin(selected);
}

function chip(thingId, feature, label, onValue, offValue) {
  const t = things.get(thingId);
  const want = desired(t, feature);
  const have = f(t, feature);
  const active = want === onValue;
  return `<button class="chip${active ? ' active' : ''}${want !== have ? ' pending' : ''}"
    data-thing="${thingId}" data-feature="${feature}" data-value="${active ? offValue : onValue}"
    title="${want !== have ? 'ожидает подтверждения двойника' : ''}">${label}</button>`;
}

function renderWhatIf() {
  const by = (kind) => [...things.values()].filter((t) => t.attributes?.kind === kind).sort((a, b) => a.thingId.localeCompare(b.thingId));
  const short = (t) => t.thingId.split(':')[1].toUpperCase();
  document.getElementById('whatif').innerHTML = `
    <div class="grp">Закрыть дорогу</div>
    <div class="chips">${by('haul-road').map((t) => chip(t.thingId, 'status', t.attributes.name.replace('Транспортная берма', 'Берма').replace('Автодорога', 'Дорога'), 'closed', 'open')).join('')}</div>
    <div class="grp">Остановить погрузку / дробилку</div>
    <div class="chips">${by('loading-unit').map((t) => chip(t.thingId, 'status', short(t), 'down', 'up')).join('')}
      ${chip(id('crusher'), 'status', 'ЗКД', 'down', 'up')}</div>
    <div class="grp">Сход самосвала с линии</div>
    <div class="chips">${by('haul-truck').map((t) => chip(t.thingId, 'status', short(t), 'down', 'up')).join('')}</div>
    <div class="grp">Активный сценарий подсвечен красным; повторный клик отменяет его.</div>`;
}

document.getElementById('whatif').addEventListener('click', (e) => {
  const b = e.target.closest('button.chip');
  if (b) setDesired(b.dataset.thing, b.dataset.feature, b.dataset.value);
});
document.getElementById('twin-actions').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-thing]');
  if (b) setDesired(b.dataset.thing, b.dataset.feature, b.dataset.value);
});
document.getElementById('mode').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b) setDesired(id('dispatcher'), 'mode', b.dataset.mode);
});

let selected = null;
function showTwin(thingId) {
  selected = thingId;
  const t = things.get(thingId);
  const card = document.getElementById('twin-card');
  card.hidden = false;
  document.getElementById('twin-title').textContent = `${t.attributes.name} · ${thingId}`;
  const k = t.attributes.kind;
  let actions = '';
  if (k === 'haul-road') {
    const closed = desired(t, 'status') === 'closed';
    actions = `<button class="btn ${closed ? '' : 'danger'}" data-thing="${thingId}" data-feature="status" data-value="${closed ? 'open' : 'closed'}">${closed ? 'Открыть дорогу' : 'Закрыть дорогу'}</button>`;
  } else if (['loading-unit', 'haul-truck', 'primary-crusher'].includes(k)) {
    const down = desired(t, 'status') === 'down';
    actions = `<button class="btn ${down ? '' : 'danger'}" data-thing="${thingId}" data-feature="status" data-value="${down ? 'up' : 'down'}">${down ? 'Вернуть в работу' : 'Остановить'}</button>`;
  }
  if (k === 'haul-truck') {
    actions += `<div class="muted">Состояние: ${STATE_RU[f(t, 'state')] || f(t, 'state')}, груз: ${fmt(f(t, 'payload'), 0)} т (${MATERIAL_RU[f(t, 'payload', 'material') || '']}), скорость ${fmt(f(t, 'speed'))} км/ч, мощность ${fmt(f(t, 'engine_power'))} кВт</div>`;
  }
  document.getElementById('twin-actions').innerHTML = actions;
  const view = { attributes: { ...t.attributes }, features: t.features };
  if (view.attributes.segments) view.attributes.segments = `${view.attributes.segments.length} участков`;
  document.getElementById('twin-json').textContent = JSON.stringify(view, null, 1);
}

const ray = new THREE.Raycaster();
renderer.domElement.addEventListener('pointerdown', (e) => { renderer.domElement._down = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', (e) => {
  const d = renderer.domElement._down;
  if (!d || Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 4) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects(pickable, false)[0];
  if (hit) showTwin(hit.object.userData.thingId);
});

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  renderer.setSize(w, h);
  labels.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);

let dirty = true;
let lastPanel = 0;
function frame(now) {
  requestAnimationFrame(frame);
  if (dirty) { updateScene(); dirty = false; }
  if (now - lastPanel > 1000) { updatePanel(); lastPanel = now; }
  for (const o of truckObjs.values()) {
    if (!o.target) continue;
    o.mesh.position.lerp(o.target, 0.08);
    o.mesh.rotation.y += (((o.heading - o.mesh.rotation.y + Math.PI * 3) % (Math.PI * 2)) - Math.PI) * 0.15;
  }
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
}

(async function main() {
  resize();
  await loadThings();
  buildPit(things.get(id('pit')));
  for (const t of things.values()) if (t.attributes?.kind === 'haul-road') buildRoad(t);
  buildSites();
  buildShovels();
  buildTrucks();
  connectStream();
  updatePanel();
  window.__aktogayReady = true;
  requestAnimationFrame(frame);
})();
