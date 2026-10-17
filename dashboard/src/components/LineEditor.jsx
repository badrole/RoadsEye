/**
 * LineEditor — interactive counting line editor over the live CCTV canvas.
 *   counting (cyan) — crossing counts a vehicle
 *
 * Drag endpoints / drag body, click to select, add/delete. Committed via
 * POST /api/pipeline/lines (backend redraws lines in annotated frames).
 */
import { useEffect, useRef, useState, useCallback } from 'react'

const API  = '/api'
const CYAN = '#22d3ee'

export default function LineEditor({ canvasRef }) {
  const overlayRef              = useRef(null)
  const [editing, setEditing]   = useState(false)
  const [lines, setLines]       = useState({ counting: [], lock: [] })
  const [selected, setSelected] = useState(null)   // {kind, idx}
  const dragRef  = useRef(null)
  const frameRef = useRef({ w: 1280, h: 720 })

  // ── Hydration ──────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/pipeline/lines`)
      if (!res.ok) return
      const d = await res.json()
      setLines({ counting: d.counting_lines ?? [], lock: [] })
    } catch { /* backend down */ }
  }, [])

  useEffect(() => { load() }, [load])

  const commit = useCallback(async (next) => {
    setLines(next)
    const map  = l => ({ name: l.name, x1: l.x1 | 0, y1: l.y1 | 0, x2: l.x2 | 0, y2: l.y2 | 0 })
    const body = { counting_lines: next.counting.map(map) }
    try {
      await fetch(`${API}/pipeline/lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    } catch { /* next change retries */ }
  }, [])

  // ── Mutations ───────────────────────────────────────────────────────────────
  const addLine = useCallback(() => {
    const fr = frameRef.current
    const y  = Math.round(fr.h * 0.55)
    const n  = lines.counting.length + 1
    const nl  = { name: `line${n}`, x1: 0, y1: y, x2: fr.w, y2: y }
    const next = { counting: [...lines.counting, nl], lock: [] }
    setSelected({ kind: 'counting', idx: next.counting.length - 1 })
    commit(next)
  }, [lines, commit])

  const deleteSelected = useCallback(() => {
    if (!selected || selected.kind !== 'counting') return
    const arr = lines.counting.filter((_, i) => i !== selected.idx)
    setSelected(null)
    commit({ counting: arr, lock: [] })
  }, [selected, lines, commit])

  // ── Pointer interactions (frame coords via rect scaling) ───────────────────
  const toFrame = useCallback((e) => {
    const ov = overlayRef.current
    if (!ov) return { fx: 0, fy: 0, rect: null }
    const rect = ov.getBoundingClientRect()
    const fr = frameRef.current
    return {
      fx: (e.clientX - rect.left) * fr.w / rect.width,
      fy: (e.clientY - rect.top)  * fr.h / rect.height,
      rect,
    }
  }, [])

  const hitTest = useCallback((fx, fy, rect) => {
    const scale = rect.width / frameRef.current.w   // css px per frame px
    const arr = lines.counting
    for (let i = arr.length - 1; i >= 0; i--) {
      const l = arr[i]
      if (Math.hypot(fx - l.x1, fy - l.y1) * scale <= 12) return { kind: 'counting', idx: i, part: 'p1' }
      if (Math.hypot(fx - l.x2, fy - l.y2) * scale <= 12) return { kind: 'counting', idx: i, part: 'p2' }
      const ax = l.x1, ay = l.y1, bx = l.x2, by = l.y2
      const t = Math.max(0, Math.min(1, ((fx - ax) * (bx - ax) + (fy - ay) * (by - ay)) /
               ((bx - ax) ** 2 + (by - ay) ** 2 || 1)))
      const dist = Math.hypot(fx - (ax + t * (bx - ax)), fy - (ay + t * (by - ay))) * scale
      if (dist <= 8) return { kind: 'counting', idx: i, part: 'body' }
    }
    return null
  }, [lines])

  const onPointerDown = useCallback((e) => {
    if (!editing) return
    const { fx, fy, rect } = toFrame(e)
    const hit = hitTest(fx, fy, rect)
    overlayRef.current?.setPointerCapture?.(e.pointerId)
    if (hit) {
      setSelected({ kind: hit.kind, idx: hit.idx })
      const l = lines[hit.kind][hit.idx]
      dragRef.current = {
        hit,
        grabX: fx, grabY: fy,
        orig: { x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2 },
      }
    } else {
      setSelected(null)
      dragRef.current = null
    }
  }, [editing, toFrame, hitTest, lines])

  const onPointerMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    const { fx, fy } = toFrame(e)
    const dx = fx - drag.grabX, dy = fy - drag.grabY
    const fr = frameRef.current
    const o  = drag.orig
    let x1 = o.x1, y1 = o.y1, x2 = o.x2, y2 = o.y2
    if (drag.hit.part === 'body') {
      x1 = Math.max(0, Math.min(fr.w, o.x1 + dx)); y1 = Math.max(0, Math.min(fr.h, o.y1 + dy))
      x2 = Math.max(0, Math.min(fr.w, o.x2 + dx)); y2 = Math.max(0, Math.min(fr.h, o.y2 + dy))
    } else if (drag.hit.part === 'p1') {
      x1 = Math.max(0, Math.min(fr.w, fx)); y1 = Math.max(0, Math.min(fr.h, fy))
    } else {
      x2 = Math.max(0, Math.min(fr.w, fx)); y2 = Math.max(0, Math.min(fr.h, fy))
    }
    // local preview only — no commit during drag (optimistic line state)
    const arr = lines.counting.map((it, i) => i === drag.hit.idx ? { ...it, x1, y1, x2, y2 } : it)
    setLines(prev => ({ counting: arr, lock: [] }))
    drag.moved = true
  }, [toFrame, lines])

  const onPointerUp = useCallback((e) => {
    e.currentTarget?.releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null
    if (!drag || !drag.moved) return
    commit(lines)
  }, [commit, lines])

  // ── Keep overlay geometry in sync with the video canvas ────────────────────
  useEffect(() => {
    const id = setInterval(() => {
      const c = canvasRef?.current
      if (c && c.width > 0 && c.width !== frameRef.current.w) {
        frameRef.current = { w: c.width, h: c.height }
      }
    }, 1000)
    return () => clearInterval(id)
  }, [canvasRef])

  // ── Overlay drawing ────────────────────────────────────────────────────────
  useEffect(() => {
    const cv = overlayRef.current
    if (!cv) return
    const fr = frameRef.current
    cv.width = fr.w; cv.height = fr.h
    const ctx = cv.getContext('2d')
    ctx.clearRect(0, 0, fr.w, fr.h)
    if (!editing) return

    for (const [k, arr] of [['counting', lines.counting]]) {
      const color = CYAN
      arr.forEach((l, i) => {
        const isSel = selected?.kind === 'counting' && selected?.idx === i
        ctx.strokeStyle = color
        ctx.lineWidth = isSel ? 6 : 3
        ctx.beginPath(); ctx.moveTo(l.x1, l.y1); ctx.lineTo(l.x2, l.y2); ctx.stroke()
        // endpoints
        ctx.fillStyle = color
        for (const [ex, ey] of [[l.x1, l.y1], [l.x2, l.y2]]) {
          ctx.beginPath(); ctx.arc(ex, ey, isSel ? 9 : 6, 0, Math.PI * 2); ctx.fill()
        }
        // label
        ctx.font = '20px monospace'
        ctx.fillStyle = color
        ctx.fillText(l.name, l.x1 + 6, l.y1 - 10)
      })
    }
  }, [editing, lines, selected])

  return (
    <>
      {/* Toolbar — atas kanan video */}
      <div className="absolute top-3 right-3 z-20 flex flex-wrap items-center gap-1.5 p-1.5 rounded-sm bg-asphalt/85 border border-lane-dim/50 backdrop-blur">
        <button
          onClick={() => setEditing(v => !v)}
          className={`px-2 py-1.5 font-code text-[10px] tracking-widest uppercase rounded-sm transition-colors ${
            editing ? 'bg-lane text-asphalt font-bold' : 'text-chalk hover:text-lane border border-lane-dim/40'
          }`}
        >
          {editing ? 'Done' : 'Edit Garis'}
        </button>
        {editing && (
          <>
            <button onClick={addLine} className="px-2 py-1.5 font-code text-[10px] tracking-widest uppercase rounded-sm border border-lane-dim/40 text-chalk hover:text-lane transition-colors">Tambah</button>
            <button onClick={deleteSelected} className="px-2 py-1.5 font-code text-[10px] tracking-widest uppercase rounded-sm border border-stop/50 text-stop hover:bg-stop hover:text-white transition-colors">Hapus</button>
          </>
        )}
      </div>

      {/* Overlay canvas — hanya aktif saat editing */}
      <canvas
        ref={overlayRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { dragRef.current = null }}
        className={`absolute inset-0 w-full h-full ${editing ? 'cursor-crosshair' : 'pointer-events-none'}`}
        style={{ minHeight: 360 }}
      />
    </>
  )
}

