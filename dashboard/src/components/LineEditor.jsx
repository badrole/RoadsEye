/**
 * LineEditor — interactive counting line editor & counting mode switcher
 * over the live CCTV canvas.
 *
 * Supports switching between:
 *   - "line" mode (count vehicles crossing tripwire lines)
 *   - "id" mode   (count every tracked vehicle/motorcycle in frame by ID)
 */
import { useEffect, useRef, useState, useCallback } from 'react'

const API = '/api'
const CYAN = '#22d3ee'
const YELLOW = '#FFD100'

export default function LineEditor({ canvasRef, countMode = 'line', onCountModeChange }) {
  const overlayRef = useRef(null)
  const [editing, setEditing] = useState(false)
  const [lines, setLines] = useState({ counting: [] })
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [savedNotice, setSavedNotice] = useState(false)
  const [switchingMode, setSwitchingMode] = useState(false)

  const dragRef = useRef(null)
  const frameRef = useRef({ w: 1280, h: 720 })
  const [overlayTick, setOverlayTick] = useState(0)

  const idMode = countMode === 'id'

  useEffect(() => {
    if (idMode && editing) {
      setEditing(false)
      setSelectedIdx(null)
    }
  }, [idMode, editing])

  const handleModeSwitch = useCallback(async (nextMode) => {
    if (nextMode === countMode || switchingMode) return
    setSwitchingMode(true)
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
    } catch {
      setLines(prev => (prev.counting.length > 0 ? prev : { counting: [{ name: 'line1', x1: 0, y1: 380, x2: 1280, y2: 380 }] }))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const commit = useCallback(async (nextLines) => {
    setLines(nextLines)
    const map = (l) => ({
      name: l.name || 'line1',
      x1: Math.round(l.x1),
      y1: Math.round(l.y1),
      x2: Math.round(l.x2),
      y2: Math.round(l.y2),
    })
    const body = { counting_lines: nextLines.counting.map(map) }
    try {
      const res = await fetch(`${API}/pipeline/lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        setSavedNotice(true)
        setTimeout(() => setSavedNotice(false), 2000)
      }
    } catch {
      /* retry on next action */
    }
  }, [])

  // ── Mutations ───────────────────────────────────────────────────────────────
  const addLine = useCallback(() => {
    const fr = frameRef.current
    const n = lines.counting.length + 1
    const y = Math.round(fr.h * (0.35 + ((n % 4) * 0.12)))
    const nl = { name: `line${n}`, x1: 50, y1: y, x2: fr.w - 50, y2: y }
    const next = { counting: [...lines.counting, nl] }
    setSelectedIdx(next.counting.length - 1)
    commit(next)
  }, [lines, commit])

  const deleteSelected = useCallback(() => {
    if (selectedIdx === null || lines.counting.length <= 1) return
    const arr = lines.counting.filter((_, i) => i !== selectedIdx)
    setSelectedIdx(null)
    commit({ counting: arr })
  }, [selectedIdx, lines, commit])

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

  // ── Hit test (endpoints have priority and generous touch radius) ───────────
  const hitTest = useCallback((fx, fy, rect) => {
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

  // ── Pointer events ─────────────────────────────────────────────────────────
  const onPointerDown = useCallback((e) => {
    if (!editing || idMode) return
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
    const hit = hitTest(fx, fy, rect)

    if (hit) {
      overlayRef.current?.setPointerCapture?.(e.pointerId)
      setSelectedIdx(hit.idx)
      const l = lines.counting[hit.idx]
      dragRef.current = {
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
  }, [editing, idMode, canvasRef, toFrame, hitTest, lines])

  const onPointerMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    const { fx, fy } = toFrame(e)
    const dx = fx - drag.grabX
    const dy = fy - drag.grabY
    const fr = frameRef.current
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
    if (drag && drag.moved && drag.latestLines) {
      commit(drag.latestLines)
    }
  }, [commit])

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
    if (!editing || idMode) return

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
  }, [editing, idMode, lines, selectedIdx, overlayTick])

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
            title="Count every vehicle & motorcycle in frame directly by tracker ID"
            className={`px-2.5 py-1 rounded-sm tracking-wider uppercase transition-all cursor-pointer font-bold ${
              idMode
                ? 'bg-lane text-asphalt shadow-glowlane'
                : 'text-pip hover:text-chalk'
            }`}
          >
            Per ID
          </button>
        </div>

        {/* Line Editor Controls (shown when in Per Line mode) */}
        {!idMode && (
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
              {editing ? '✓ Done' : '✎ Edit Lines'}
            </button>

            {editing && (
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
                    onClick={deleteSelected}
                    title="Delete selected line"
                    className="px-2.5 py-1 font-code text-xs rounded-sm border border-stop/50 text-stop hover:bg-stop hover:text-white transition-colors cursor-pointer"
                  >
                    ✕ Delete
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Overlay Canvas for Freeform Endpoint Dragging */}
      <canvas
        ref={overlayRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          dragRef.current = null
        }}
        className={`absolute inset-0 w-full h-full ${editing && !idMode ? 'cursor-crosshair' : 'pointer-events-none'}`}
      />
    </>
  )
}
