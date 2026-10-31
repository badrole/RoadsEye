/**
 * LineEditor — interactive counting line & Per-ID counting zone editor
 * over the live CCTV canvas.
 *
 * Supports:
 *   - "line" mode : edit tripwire lines (drag endpoints or line body, add/delete lines)
 *   - "id" mode   : edit obstacle-free Counting Zone rectangle/polygon path (drag corners,
 *                   drag sides, drag whole zone, add/remove path points, reset rectangle)
 */
import { useEffect, useRef, useState, useCallback } from 'react'

const API = '/api'
const CYAN = '#22d3ee'
const YELLOW = '#FFD100'

const DEFAULT_ZONE = [
  { x: 160, y: 140 },
  { x: 1120, y: 140 },
  { x: 1120, y: 620 },
  { x: 160, y: 620 },
]

function pointInPolygon(px, py, pts) {
  let inside = false
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i].x, yi = pts[i].y
    const xj = pts[j].x, yj = pts[j].y
    const intersect = (yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / ((yj - yi) || 1e-9) + xi
    if (intersect) inside = !inside
  }
  return inside
}

export default function LineEditor({ canvasRef, countMode = 'line', onCountModeChange }) {
  const overlayRef = useRef(null)
  const [editing, setEditing] = useState(false)
  const [lines, setLines] = useState({ counting: [] })
  const [zone, setZone] = useState(DEFAULT_ZONE)
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [selectedZonePt, setSelectedZonePt] = useState(null)
  const [savedNotice, setSavedNotice] = useState(false)
  const [switchingMode, setSwitchingMode] = useState(false)

  const dragRef = useRef(null)
  const frameRef = useRef({ w: 1280, h: 720 })
  const [overlayTick, setOverlayTick] = useState(0)

  const idMode = countMode === 'id'

  const handleModeSwitch = useCallback(async (nextMode) => {
    if (nextMode === countMode || switchingMode) return
    setSwitchingMode(true)
    setSelectedIdx(null)
    setSelectedZonePt(null)
    try {
      const res = await fetch(`${API}/pipeline/count-mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: nextMode }),
      })
      if (res.ok) {
        onCountModeChange?.(nextMode)
      }
    } catch {
      /* ignore network hiccup */
    } finally {
      setSwitchingMode(false)
    }
  }, [countMode, switchingMode, onCountModeChange])

  // ── Hydration ──────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/pipeline/lines`)
      if (!res.ok) return
      const d = await res.json()
      const arr = d.counting_lines ?? []
      if (arr.length > 0) {
        setLines({ counting: arr })
      } else {
        setLines({ counting: [{ name: 'line1', x1: 0, y1: 380, x2: 1280, y2: 380 }] })
      }
      const z = d.counting_zone ?? []
      if (z.length >= 3) {
        setZone(z.map((p) => ({ x: Number(p.x), y: Number(p.y) })))
      }
    } catch {
      setLines((prev) =>
        prev.counting.length > 0
          ? prev
          : { counting: [{ name: 'line1', x1: 0, y1: 380, x2: 1280, y2: 380 }] }
      )
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const flashSaved = useCallback(() => {
    setSavedNotice(true)
    setTimeout(() => setSavedNotice(false), 2000)
  }, [])

  const commitLines = useCallback(async (nextLines) => {
    setLines(nextLines)
    const map = (l) => ({
      name: l.name || 'line1',
      x1: Math.round(l.x1),
      y1: Math.round(l.y1),
      x2: Math.round(l.x2),
      y2: Math.round(l.y2),
    })
    try {
      const res = await fetch(`${API}/pipeline/lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ counting_lines: nextLines.counting.map(map) }),
      })
      if (res.ok) flashSaved()
    } catch {
      /* retry on next action */
    }
  }, [flashSaved])

  const commitZone = useCallback(async (nextZone) => {
    setZone(nextZone)
    const pts = nextZone.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }))
    try {
      const res = await fetch(`${API}/pipeline/zone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ counting_zone: pts }),
      })
      if (res.ok) flashSaved()
    } catch {
      /* retry on next action */
    }
  }, [flashSaved])

  // ── Line Mutations ─────────────────────────────────────────────────────────
  const addLine = useCallback(() => {
    const fr = frameRef.current
    const n = lines.counting.length + 1
    const y = Math.round(fr.h * (0.35 + ((n % 4) * 0.12)))
    const nl = { name: `line${n}`, x1: 50, y1: y, x2: fr.w - 50, y2: y }
    const next = { counting: [...lines.counting, nl] }
    setSelectedIdx(next.counting.length - 1)
    commitLines(next)
  }, [lines, commitLines])

  const deleteSelectedLine = useCallback(() => {
    if (selectedIdx === null || lines.counting.length <= 1) return
    const arr = lines.counting.filter((_, i) => i !== selectedIdx)
    setSelectedIdx(null)
    commitLines({ counting: arr })
  }, [selectedIdx, lines, commitLines])

  // ── Zone Mutations (Rectangle & Path points) ───────────────────────────────
  const resetZoneRectangle = useCallback(() => {
    const fr = frameRef.current
    const padX = Math.round(fr.w * 0.125)
    const topY = Math.round(fr.h * 0.2)
    const botY = Math.round(fr.h * 0.86)
    const rect = [
      { x: padX, y: topY },
      { x: fr.w - padX, y: topY },
      { x: fr.w - padX, y: botY },
      { x: padX, y: botY },
    ]
    setSelectedZonePt(null)
    commitZone(rect)
  }, [commitZone])

  const addZonePoint = useCallback(() => {
    if (zone.length < 3) return
    // Split the edge after selectedZonePt, or split the longest edge
    let splitIdx = selectedZonePt
    if (splitIdx === null || splitIdx < 0 || splitIdx >= zone.length) {
      let maxLen = -1
      splitIdx = 0
      for (let i = 0; i < zone.length; i++) {
        const a = zone[i]
        const b = zone[(i + 1) % zone.length]
        const d = Math.hypot(b.x - a.x, b.y - a.y)
        if (d > maxLen) {
          maxLen = d
          splitIdx = i
        }
      }
    }
    const a = zone[splitIdx]
    const b = zone[(splitIdx + 1) % zone.length]
    const mid = { x: Math.round((a.x + b.x) / 2), y: Math.round((a.y + b.y) / 2) }
    const next = [...zone.slice(0, splitIdx + 1), mid, ...zone.slice(splitIdx + 1)]
    setSelectedZonePt(splitIdx + 1)
    commitZone(next)
  }, [zone, selectedZonePt, commitZone])

  const deleteZonePoint = useCallback(() => {
    if (selectedZonePt === null || zone.length <= 3) return
    const next = zone.filter((_, i) => i !== selectedZonePt)
    setSelectedZonePt(null)
    commitZone(next)
  }, [selectedZonePt, zone, commitZone])

  // ── Coordinate conversion ──────────────────────────────────────────────────
  const toFrame = useCallback((e) => {
    const ov = overlayRef.current
    if (!ov) return { fx: 0, fy: 0, rect: null }
    const rect = ov.getBoundingClientRect()
    const fr = frameRef.current
    const fx = Math.max(0, Math.min(fr.w, ((e.clientX - rect.left) * fr.w) / (rect.width || 1)))
    const fy = Math.max(0, Math.min(fr.h, ((e.clientY - rect.top) * fr.h) / (rect.height || 1)))
    return { fx, fy, rect }
  }, [])

  // ── Hit tests ──────────────────────────────────────────────────────────────
  const hitTestLine = useCallback((fx, fy, rect) => {
    const scale = rect.width / (frameRef.current.w || 1)
    const arr = lines.counting

    for (let i = arr.length - 1; i >= 0; i--) {
      const l = arr[i]
      if (Math.hypot(fx - l.x1, fy - l.y1) * scale <= 24) {
        return { idx: i, part: 'p1' }
      }
      if (Math.hypot(fx - l.x2, fy - l.y2) * scale <= 24) {
        return { idx: i, part: 'p2' }
      }
      const ax = l.x1, ay = l.y1, bx = l.x2, by = l.y2
      const segLenSq = (bx - ax) ** 2 + (by - ay) ** 2 || 1
      const t = Math.max(0, Math.min(1, ((fx - ax) * (bx - ax) + (fy - ay) * (by - ay)) / segLenSq))
      const projX = ax + t * (bx - ax)
      const projY = ay + t * (by - ay)
      const dist = Math.hypot(fx - projX, fy - projY) * scale
      if (dist <= 16) {
        return { idx: i, part: 'body' }
      }
    }
    return null
  }, [lines])

  const hitTestZone = useCallback((fx, fy, rect) => {
    if (zone.length < 3) return null
    const scale = rect.width / (frameRef.current.w || 1)

    // 1. Corner vertices (highest priority)
    for (let i = 0; i < zone.length; i++) {
      if (Math.hypot(fx - zone[i].x, fy - zone[i].y) * scale <= 24) {
        return { type: 'vertex', idx: i }
      }
    }

    // 2. Edges (drag side of rectangle/polygon)
    for (let i = 0; i < zone.length; i++) {
      const a = zone[i]
      const b = zone[(i + 1) % zone.length]
      const segLenSq = (b.x - a.x) ** 2 + (b.y - a.y) ** 2 || 1
      const t = Math.max(0, Math.min(1, ((fx - a.x) * (b.x - a.x) + (fy - a.y) * (b.y - a.y)) / segLenSq))
      const projX = a.x + t * (b.x - a.x)
      const projY = a.y + t * (b.y - a.y)
      if (Math.hypot(fx - projX, fy - projY) * scale <= 16) {
        return { type: 'edge', idx: i }
      }
    }

    // 3. Inside polygon body (move whole zone)
    if (pointInPolygon(fx, fy, zone)) {
      return { type: 'body' }
    }

    return null
  }, [zone])

  // ── Pointer events ─────────────────────────────────────────────────────────
  const onPointerDown = useCallback((e) => {
    if (!editing) return
    const c = canvasRef?.current
    if (c && c.width > 0) {
      frameRef.current = { w: c.width, h: c.height }
      const ov = overlayRef.current
      if (ov && (ov.width !== c.width || ov.height !== c.height)) {
        ov.width = c.width
        ov.height = c.height
      }
    }
    const { fx, fy, rect } = toFrame(e)

    if (idMode) {
      const hit = hitTestZone(fx, fy, rect)
      if (hit) {
        overlayRef.current?.setPointerCapture?.(e.pointerId)
        setSelectedZonePt(hit.type === 'vertex' || hit.type === 'edge' ? hit.idx : null)
        dragRef.current = {
          mode: 'zone',
          hit,
          grabX: fx,
          grabY: fy,
          origZone: zone.map((p) => ({ ...p })),
          latestZone: zone,
        }
      } else {
        setSelectedZonePt(null)
        dragRef.current = null
      }
      return
    }

    const hit = hitTestLine(fx, fy, rect)
    if (hit) {
      overlayRef.current?.setPointerCapture?.(e.pointerId)
      setSelectedIdx(hit.idx)
      const l = lines.counting[hit.idx]
      dragRef.current = {
        mode: 'line',
        hit,
        grabX: fx,
        grabY: fy,
        orig: { x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2 },
        latestLines: lines,
      }
    } else {
      setSelectedIdx(null)
      dragRef.current = null
    }
  }, [editing, idMode, canvasRef, toFrame, hitTestZone, hitTestLine, zone, lines])

  const onPointerMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    const { fx, fy } = toFrame(e)
    const dx = fx - drag.grabX
    const dy = fy - drag.grabY
    const fr = frameRef.current

    if (drag.mode === 'zone') {
      const orig = drag.origZone
      let nextZone = orig.map((p) => ({ ...p }))
      if (drag.hit.type === 'vertex') {
        const i = drag.hit.idx
        nextZone[i] = {
          x: Math.max(0, Math.min(fr.w, fx)),
          y: Math.max(0, Math.min(fr.h, fy)),
        }
      } else if (drag.hit.type === 'edge') {
        const i = drag.hit.idx
        const j = (i + 1) % orig.length
        nextZone[i] = {
          x: Math.max(0, Math.min(fr.w, orig[i].x + dx)),
          y: Math.max(0, Math.min(fr.h, orig[i].y + dy)),
        }
        nextZone[j] = {
          x: Math.max(0, Math.min(fr.w, orig[j].x + dx)),
          y: Math.max(0, Math.min(fr.h, orig[j].y + dy)),
        }
      } else if (drag.hit.type === 'body') {
        nextZone = orig.map((p) => ({
          x: Math.max(0, Math.min(fr.w, p.x + dx)),
          y: Math.max(0, Math.min(fr.h, p.y + dy)),
        }))
      }
      drag.latestZone = nextZone
      drag.moved = true
      setZone(nextZone)
      return
    }

    const o = drag.orig
    let x1 = o.x1, y1 = o.y1, x2 = o.x2, y2 = o.y2
    if (drag.hit.part === 'p1') {
      x1 = Math.max(0, Math.min(fr.w, fx))
      y1 = Math.max(0, Math.min(fr.h, fy))
    } else if (drag.hit.part === 'p2') {
      x2 = Math.max(0, Math.min(fr.w, fx))
      y2 = Math.max(0, Math.min(fr.h, fy))
    } else if (drag.hit.part === 'body') {
      x1 = Math.max(0, Math.min(fr.w, o.x1 + dx))
      y1 = Math.max(0, Math.min(fr.h, o.y1 + dy))
      x2 = Math.max(0, Math.min(fr.w, o.x2 + dx))
      y2 = Math.max(0, Math.min(fr.h, o.y2 + dy))
    }

    const arr = lines.counting.map((it, i) => (i === drag.hit.idx ? { ...it, x1, y1, x2, y2 } : it))
    const nextState = { counting: arr }
    drag.latestLines = nextState
    drag.moved = true
    setLines(nextState)
  }, [toFrame, lines])

  const onPointerUp = useCallback((e) => {
    e.currentTarget?.releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null
    if (!drag || !drag.moved) return
    if (drag.mode === 'zone' && drag.latestZone) {
      commitZone(drag.latestZone)
    } else if (drag.mode === 'line' && drag.latestLines) {
      commitLines(drag.latestLines)
    }
  }, [commitZone, commitLines])

  // ── Sync frame dimensions with canvas ──────────────────────────────────────
  useEffect(() => {
    const cv = overlayRef.current
    if (!cv) return
    const apply = () => {
      const c = canvasRef?.current
      if (c && c.width > 0 && (c.width !== frameRef.current.w || c.height !== frameRef.current.h)) {
        frameRef.current = { w: c.width, h: c.height }
        if (cv.width !== frameRef.current.w) cv.width = frameRef.current.w
        if (cv.height !== frameRef.current.h) cv.height = frameRef.current.h
        setOverlayTick((t) => t + 1)
      }
    }
    apply()
    const id = setInterval(apply, 300)
    return () => clearInterval(id)
  }, [canvasRef])

  // ── Overlay rendering ──────────────────────────────────────────────────────
  useEffect(() => {
    const cv = overlayRef.current
    if (!cv) return
    const fr = frameRef.current
    if (cv.width !== fr.w) cv.width = fr.w
    if (cv.height !== fr.h) cv.height = fr.h
    const ctx = cv.getContext('2d')
    ctx.clearRect(0, 0, cv.width, cv.height)
    if (!editing) return

    if (idMode) {
      if (zone.length < 3) return
      // Draw translucent obstacle-free zone polygon
      ctx.fillStyle = 'rgba(34, 211, 238, 0.16)'
      ctx.beginPath()
      ctx.moveTo(zone[0].x, zone[0].y)
      for (let i = 1; i < zone.length; i++) {
        ctx.lineTo(zone[i].x, zone[i].y)
      }
      ctx.closePath()
      ctx.fill()

      ctx.strokeStyle = YELLOW
      ctx.lineWidth = 3
      ctx.stroke()

      // Draw edge midpoint handles for easy side resizing
      for (let i = 0; i < zone.length; i++) {
        const a = zone[i]
        const b = zone[(i + 1) % zone.length]
        const mx = (a.x + b.x) / 2
        const my = (a.y + b.y) / 2
        ctx.fillStyle = '#0f172a'
        ctx.fillRect(mx - 6, my - 6, 12, 12)
        ctx.strokeStyle = CYAN
        ctx.lineWidth = 2
        ctx.strokeRect(mx - 6, my - 6, 12, 12)
      }

      // Draw corner handles
      zone.forEach((p, idx) => {
        const isSel = selectedZonePt === idx
        const c = isSel ? YELLOW : CYAN
        ctx.fillStyle = '#0f172a'
        ctx.beginPath()
        ctx.arc(p.x, p.y, isSel ? 12 : 9, 0, Math.PI * 2)
        ctx.fill()
        ctx.strokeStyle = c
        ctx.lineWidth = isSel ? 3 : 2
        ctx.stroke()

        ctx.fillStyle = c
        ctx.beginPath()
        ctx.arc(p.x, p.y, isSel ? 5 : 4, 0, Math.PI * 2)
        ctx.fill()
      })

      const cx = zone.reduce((acc, p) => acc + p.x, 0) / zone.length
      const cy = zone.reduce((acc, p) => acc + p.y, 0) / zone.length
      const label = 'ZONA HITUNG BEBAS OBSTACLE (drag sudut / sisi / tengah)'
      ctx.font = 'bold 14px monospace'
      const tw = ctx.measureText(label).width
      ctx.fillStyle = 'rgba(15, 23, 42, 0.88)'
      ctx.fillRect(cx - tw / 2 - 8, cy - 12, tw + 16, 24)
      ctx.strokeStyle = YELLOW
      ctx.lineWidth = 1
      ctx.strokeRect(cx - tw / 2 - 8, cy - 12, tw + 16, 24)
      ctx.fillStyle = YELLOW
      ctx.fillText(label, cx - tw / 2, cy + 5)
      return
    }

    lines.counting.forEach((l, i) => {
      const isSel = selectedIdx === i
      const color = isSel ? YELLOW : CYAN

      if (isSel) {
        ctx.strokeStyle = 'rgba(255, 209, 0, 0.3)'
        ctx.lineWidth = 10
        ctx.beginPath()
        ctx.moveTo(l.x1, l.y1)
        ctx.lineTo(l.x2, l.y2)
        ctx.stroke()
      }

      ctx.strokeStyle = color
      ctx.lineWidth = isSel ? 4 : 3
      ctx.beginPath()
      ctx.moveTo(l.x1, l.y1)
      ctx.lineTo(l.x2, l.y2)
      ctx.stroke()

      for (const [ex, ey] of [[l.x1, l.y1], [l.x2, l.y2]]) {
        ctx.fillStyle = '#0f172a'
        ctx.beginPath()
        ctx.arc(ex, ey, isSel ? 11 : 8, 0, Math.PI * 2)
        ctx.fill()
        ctx.strokeStyle = color
        ctx.lineWidth = isSel ? 3 : 2
        ctx.stroke()

        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(ex, ey, isSel ? 5 : 4, 0, Math.PI * 2)
        ctx.fill()
      }

      const midX = (l.x1 + l.x2) / 2
      const midY = (l.y1 + l.y2) / 2
      const label = `${l.name} (drag endpoint)`

      ctx.font = 'bold 15px monospace'
      const textWidth = ctx.measureText(label).width
      ctx.fillStyle = 'rgba(15, 23, 42, 0.85)'
      ctx.fillRect(midX - textWidth / 2 - 6, midY - 22, textWidth + 12, 20)
      ctx.strokeStyle = color
      ctx.lineWidth = 1
      ctx.strokeRect(midX - textWidth / 2 - 6, midY - 22, textWidth + 12, 20)

      ctx.fillStyle = color
      ctx.fillText(label, midX - textWidth / 2, midY - 7)
    })
  }, [editing, idMode, lines, zone, selectedIdx, selectedZonePt, overlayTick])

  return (
    <>
      {/* Top Toolbar */}
      <div className="absolute top-3 right-3 z-30 flex flex-wrap items-center justify-end gap-2">
        {savedNotice && (
          <span className="px-2.5 py-1 rounded bg-go/20 border border-go text-go font-code text-[11px] font-bold shadow-md">
            ✓ Saved (counts preserved)
          </span>
        )}

        {/* 2-Option Counting Mode Toggle: Per Line vs Per ID */}
        <div
          role="group"
          aria-label="Vehicle counting mode"
          className="flex items-center p-1 rounded bg-asphalt-deep/90 border border-lane/40 backdrop-blur-md shadow-xl font-code text-xs"
        >
          <button
            type="button"
            disabled={switchingMode}
            onClick={() => handleModeSwitch('line')}
            title="Count vehicles when they cross the counting line"
            className={`px-2.5 py-1 rounded-sm tracking-wider uppercase transition-all cursor-pointer font-bold ${
              !idMode
                ? 'bg-lane text-asphalt shadow-glowlane'
                : 'text-pip hover:text-chalk'
            }`}
          >
            Per Line
          </button>
          <button
            type="button"
            disabled={switchingMode}
            onClick={() => handleModeSwitch('id')}
            title="Count vehicles once their bounding box is 100% inside the obstacle-free Counting Zone"
            className={`px-2.5 py-1 rounded-sm tracking-wider uppercase transition-all cursor-pointer font-bold ${
              idMode
                ? 'bg-lane text-asphalt shadow-glowlane'
                : 'text-pip hover:text-chalk'
            }`}
          >
            Per ID
          </button>
        </div>

        {/* Editor Controls: Edit Lines (in Line mode) OR Edit Counting Zone (in Per ID mode) */}
        <div className="flex items-center gap-1.5 p-1 rounded bg-asphalt-deep/90 border border-lane/40 backdrop-blur-md shadow-xl">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className={`px-3 py-1 font-code text-xs tracking-wider uppercase rounded-sm transition-all cursor-pointer font-bold ${
              editing
                ? 'bg-lane text-asphalt shadow-glowlane'
                : 'bg-asphalt-deep/80 text-chalk hover:text-lane border border-lane-dim/50'
            }`}
          >
            {editing ? '✓ Done' : idMode ? '✎ Edit Zone' : '✎ Edit Lines'}
          </button>

          {editing && !idMode && (
            <>
              <button
                type="button"
                onClick={addLine}
                title="Add new counting line"
                className="px-2.5 py-1 font-code text-xs rounded-sm border border-lane/50 text-lane hover:bg-lane/20 transition-colors cursor-pointer"
              >
                + Add Line
              </button>
              {lines.counting.length > 1 && selectedIdx !== null && (
                <button
                  type="button"
                  onClick={deleteSelectedLine}
                  title="Delete selected line"
                  className="px-2.5 py-1 font-code text-xs rounded-sm border border-stop/50 text-stop hover:bg-stop hover:text-white transition-colors cursor-pointer"
                >
                  ✕ Delete
                </button>
              )}
            </>
          )}

          {editing && idMode && (
            <>
              <button
                type="button"
                onClick={addZonePoint}
                title="Tambah titik sudut untuk membentuk path mengikuti area bebas obstacle"
                className="px-2.5 py-1 font-code text-xs rounded-sm border border-lane/50 text-lane hover:bg-lane/20 transition-colors cursor-pointer"
              >
                + Titik Path
              </button>
              {zone.length > 3 && selectedZonePt !== null && (
                <button
                  type="button"
                  onClick={deleteZonePoint}
                  title="Hapus titik sudut yang dipilih"
                  className="px-2.5 py-1 font-code text-xs rounded-sm border border-stop/50 text-stop hover:bg-stop hover:text-white transition-colors cursor-pointer"
                >
                  ✕ Hapus Titik
                </button>
              )}
              <button
                type="button"
                onClick={resetZoneRectangle}
                title="Reset kembali ke bentuk rectangle"
                className="px-2.5 py-1 font-code text-xs rounded-sm border border-pip/40 text-chalk hover:border-lane hover:text-lane transition-colors cursor-pointer"
              >
                ↺ Reset Box
              </button>
            </>
          )}
        </div>
      </div>

      {/* Overlay Canvas for Freeform Endpoint / Zone Dragging */}
      <canvas
        ref={overlayRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          dragRef.current = null
        }}
        className={`absolute inset-0 w-full h-full ${editing ? 'cursor-crosshair' : 'pointer-events-none'}`}
      />
    </>
  )
}
