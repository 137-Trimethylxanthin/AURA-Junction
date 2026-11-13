#!/usr/bin/env node
// Builds src/data/drawing.swiss-46639.json from design/assets/mock.json and the mockup's inline ROOMS
// (for the what-if sizes), and copies the drawing images to public/drawings/swiss-46639/.
// Run once from anywhere: node frontend/scripts/build-drawing.mjs
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const HERE = import.meta.dirname
const FRONTEND = resolve(HERE, '..')
const DESIGN = resolve(FRONTEND, '../design')
const ID = 'swiss-46639'

const mock = JSON.parse(readFileSync(join(DESIGN, 'assets/mock.json'), 'utf8'))
const html = readFileSync(join(DESIGN, 'mockup/Main.dc.html'), 'utf8')

const roomsLine = html.split('\n').find((l) => l.startsWith('const ROOMS=['))
if (!roomsLine) throw new Error('const ROOMS=[...] not found in the mockup')
const mockupRooms = JSON.parse(roomsLine.slice('const ROOMS='.length, roomsLine.lastIndexOf(']') + 1))
const sizeByNum = new Map(mockupRooms.filter((r) => r.rw != null).map((r) => [r.num, r]))

function assert(cond, msg) {
  if (!cond) throw new Error('build-drawing: ' + msg)
}

function clipToPoints(clip) {
  const raw = (clip.match(/-?[\d.]+% -?[\d.]+%/g) || []).map((p) => p.split(' ').map((v) => parseFloat(v)))
  const pts = []
  for (const p of raw) {
    const last = pts[pts.length - 1]
    if (!last || last[0] !== p[0] || last[1] !== p[1]) pts.push(p)
  }
  // The closing point repeats the first one; the polygon is closed implicitly.
  while (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop()
  return pts
}

const rooms = mock.rooms.map((r) => {
  const s = sizeByNum.get(r.num)
  const room = {
    room_id: r.num,
    num: r.num,
    name: r.name,
    unit: r.unit,
    area_m2: r.area,
    outdoor: r.outdoor,
    points: clipToPoints(r.clip),
    label_at: [r.lx, r.ly],
  }
  if (s) room.rect = { x: s.bx, y: s.by, w: s.bw, h: s.bh, w_m: s.rw, h_m: s.rh }
  return room
})

function roomIdOf(label) {
  const m = String(label).match(/\b\d\.\d\d\b/)
  return m ? m[0] : undefined
}

function boxes(list, subject, prefix) {
  // Reading order: top to bottom, then left to right (Array.prototype.sort is stable).
  const sorted = list.map((b, i) => ({ b, i })).sort((p, q) => p.b.y - q.b.y || p.b.x - q.b.x || p.i - q.i)
  const out = sorted.map(({ b }, k) => {
    const box = { id: `${prefix}-${String(k + 1).padStart(2, '0')}`, x: b.x, y: b.y, w: b.w, h: b.h, subject }
    const rid = roomIdOf(b.room)
    if (rid) box.room_id = rid
    if (b.unit) box.unit = b.unit
    return box
  })
  for (const box of out) {
    const twins = out.filter((o) => o.x === box.x && o.y === box.y && o.w === box.w && o.h === box.h)
    if (twins.length > 1) box.note = 'Double door: two leaves drawn at the same spot, each counted.'
  }
  return out
}

const evidence = {
  window: boxes(mock.windows, 'window', 'win'),
  door: boxes(mock.doors, 'door', 'door'),
  washbasin: boxes(mock.washbasins, 'washbasin', 'basin'),
}

const q = (line) => `questions.jsonl:${line}`
const count = (subject, scope, value, en, question_en) => ({
  dataset_qid: q(en),
  dataset_qid_fr: q(en + 1),
  question_en,
  answer: { kind: 'count', subject },
  scope,
  value,
  tolerance: 'exact',
})
const area = (scope, value, en, question_en) => ({
  dataset_qid: q(en),
  dataset_qid_fr: q(en + 1),
  question_en,
  answer: { kind: 'area' },
  scope,
  value,
  tolerance: '2pct',
})
const FLOOR = { kind: 'floor' }
const label = (l) => ({ kind: 'label', label: l })
const gold = [
  count('apartment', FLOOR, 2, 103409, 'How many apartments are on this floor plan'),
  count('room', label('Chambre à coucher'), 6, 103411, 'How many rooms are labelled Chambre à coucher'),
  count('room', label('Séjour/repas'), 2, 103413, 'How many rooms are labelled Séjour/repas'),
  count('room', label('Cuisine'), 2, 103415, 'How many rooms are labelled Cuisine'),
  count('room', label('Bain'), 5, 103417, 'How many rooms are labelled Bain'),
  count('room', label('Réduit'), 1, 103419, 'How many rooms are labelled Réduit'),
  count('room', label('Couloir'), 2, 103421, 'How many rooms are labelled Couloir'),
  count('door', FLOOR, 22, 103423, 'How many doors are drawn on this floor plan'),
  count('window', FLOOR, 12, 103425, 'How many windows are drawn on this floor plan'),
  count('wc', FLOOR, 5, 103427, 'How many WCs are drawn on this floor plan'),
  count('washbasin', FLOOR, 5, 103429, 'How many washbasins are drawn on this floor plan'),
  count('bathtub', FLOOR, 2, 103431, 'How many bathtubs are drawn on this floor plan'),
  count('shower', FLOOR, 2, 103433, 'How many showers are drawn on this floor plan'),
  area({ kind: 'room', room_id: '3.04' }, 17.04, 103435, 'What is the net floor area of room 3.04'),
  area({ kind: 'room', room_id: '3.17' }, 31.11, 103437, 'What is the net floor area of room 3.17'),
  area({ kind: 'unit', unit: 'B' }, 90.54, 103439, 'What is the net floor area of apartment B'),
  area({ kind: 'unit', unit: 'A' }, 139.34, 103441, 'What is the net floor area of apartment A'),
  area(label('Chambre à coucher'), 88.09, 103443, 'What is the total area of rooms labelled Chambre à coucher'),
  area(FLOOR, 258.0, 103445, 'What is the net floor area of all numbered indoor rooms'),
]

// Extents of the DXF (metres) and the 0.6 m margin used by tools/build_mock.py.
const dxf_min = [-20.166657, -16.868711]
const dxf_max = [9.146366, 12.430481]
const margin = 0.6
const round = (n, d) => Math.round(n * 10 ** d) / 10 ** d

const drawing = {
  contract_version: '1.0',
  id: ID,
  title: 'Plan 3e étage',
  subtitle: 'Site 11420 · 1:200',
  frame: { dxf_min, dxf_max, margin_m: margin, m_per_unit: 1, y_flip: true, px: [mock.w, mock.h] },
  m_per_pct_x: round((dxf_max[0] - dxf_min[0] + 2 * margin) / 100, 5),
  m_per_pct_y: round((dxf_max[1] - dxf_min[1] + 2 * margin) / 100, 5),
  images: {
    rebuilt: `/drawings/${ID}/plan.svg`,
    original: `/drawings/${ID}/original-crop.png`,
    original_alignment: 'approximate',
  },
  rooms,
  units: [
    { id: 'A', label: 'Apartment A' },
    { id: 'B', label: 'Apartment B' },
  ],
  credit: {
    text: 'Swiss Dwellings v3.0.0 by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, CC BY 4.0. Rendered from the dataset geometry; labels and numbering are synthetic.',
    license: 'CC BY 4.0',
    url: 'https://doi.org/10.5281/zenodo.7788422',
  },
  evidence,
  gold,
}

// Sanity checks against the verified facts.
const sum = (rs) => round(rs.reduce((s, r) => s + r.area_m2, 0), 2)
assert(rooms.length === 22, `22 rooms expected, got ${rooms.length}`)
assert(rooms.filter((r) => r.outdoor).length === 4, '4 outdoor rooms expected')
assert(evidence.window.length === 12 && evidence.door.length === 22 && evidence.washbasin.length === 5, 'box counts')
assert(rooms.filter((r) => r.rect).length === 14, '14 what-if rooms expected')
assert(evidence.door.filter((d) => d.note).length === 4, 'two double-door pairs expected')
assert(sum(rooms.filter((r) => r.unit === 'A' && !r.outdoor)) === 139.34, 'A = 139.34')
assert(sum(rooms.filter((r) => r.unit === 'B' && !r.outdoor)) === 90.55, 'B = 90.55')
assert(sum(rooms.filter((r) => !r.outdoor)) === 229.89, 'indoor = 229.89')
assert(sum(rooms.filter((r) => r.name === 'Chambre à coucher')) === 88.09, 'bedrooms = 88.09')
assert(drawing.m_per_pct_x === 0.30513 && drawing.m_per_pct_y === 0.30499, 'scale')
for (const list of Object.values(evidence))
  for (const b of list) assert(b.x >= 0 && b.y >= 0 && b.x + b.w <= 100 && b.y + b.h <= 100, `box ${b.id} in frame`)

const outJson = join(FRONTEND, `src/data/drawing.${ID}.json`)
mkdirSync(join(FRONTEND, 'src/data'), { recursive: true })
writeFileSync(outJson, JSON.stringify(drawing, null, 2) + '\n')

const pub = join(FRONTEND, `public/drawings/${ID}`)
mkdirSync(pub, { recursive: true })
copyFileSync(join(DESIGN, 'assets/plan.svg'), join(pub, 'plan.svg'))
copyFileSync(join(DESIGN, 'assets/original-crop.png'), join(pub, 'original-crop.png'))

console.log(
  `wrote ${outJson}: ${rooms.length} rooms, ${evidence.window.length} windows, ${evidence.door.length} doors, ${evidence.washbasin.length} washbasins, ${gold.length} gold rows`,
)
