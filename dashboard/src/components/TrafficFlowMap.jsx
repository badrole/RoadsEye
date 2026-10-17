/**
 * TrafficFlowMap — canvas overlay showing bounding boxes, track IDs,
 * and counting lines from the latest MetricsMessage + pipeline status.
 *
 * Bounding boxes from the server are in the original frame's pixel coords
 * (e.g. 1280×720).  The canvas is sized to CANVAS_W×CANVAS_H, so all
 * coordinates are scaled down proportionally before drawing.
 */
import { useRef, useEffect } from 'react'

const CLASS_COLORS = {
  car:        '#3b82f6',
  bus:        '#f59e0b',
  truck:      '#10b981',
  motorcycle: '#ef4444',
}

// Colours for counting lines — cycled by index
const LINE_COLORS = ['#22d3ee', '#facc15', '#c084fc', '#4ade80']

// Source frame dimensions assumed when no better info is available.
// The demo generator emits 1280×720; live sources vary but 1280×720 is typical.
const SRC_W = 1280
const SRC_H = 720

// Display canvas dimensions
const CANVAS_W = 640
const CANVAS_H = 360

export default function TrafficFlowMap({ latest, pipelineStatus }) {
  const canvasRef = useRef(null)
  const tracks        = latest?.tracks            ?? []
  const countPerLine  = latest?.count_per_line    ?? {}
  const countingLines = pipelineStatus?.counting_lines ?? []

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)

    // Scale factors from source frame → display canvas
    const scaleX = CANVAS_W / SRC_W
    const scaleY = CANVAS_H / SRC_H

    // ── Background grid ──────────────────────────────────────────────────────
    ctx.strokeStyle = '#1f2937'
    ctx.lineWidth   = 1
    for (let x = 0; x <= CANVAS_W; x += 64) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, CANVAS_H); ctx.stroke()
    }
    for (let y = 0; y <= CANVAS_H; y += 36) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(CANVAS_W, y); ctx.stroke()
    }

    // ── Counting lines ───────────────────────────────────────────────────────
    countingLines.forEach((line, i) => {
      const color = LINE_COLORS[i % LINE_COLORS.length]
      const x1 = line.x1 * scaleX
      const y1 = line.y1 * scaleY
      const x2 = line.x2 * scaleX
      const y2 = line.y2 * scaleY

      ctx.save()
      ctx.strokeStyle = color
      ctx.lineWidth   = 2
      ctx.setLineDash([6, 4])
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()
      ctx.setLineDash([])

      // Label + count badge
      const count = countPerLine[line.name] ?? 0
      const label = `${line.name}: ${count}`
      ctx.font      = 'bold 10px monospace'
      const tw      = ctx.measureText(label).width
      const lx      = x1 + 4
      const ly      = Math.max(14, y1 - 4)
      ctx.fillStyle = color + 'cc'
      ctx.fillRect(lx - 2, ly - 12, tw + 8, 15)
      ctx.fillStyle = '#000'
      ctx.fillText(label, lx + 2, ly)
      ctx.restore()
    })

    // ── Track bounding boxes ─────────────────────────────────────────────────
    for (const track of tracks) {
      const [sx1, sy1, sx2, sy2] = track.bbox
      const x1 = sx1 * scaleX
      const y1 = sy1 * scaleY
      const x2 = sx2 * scaleX
      const y2 = sy2 * scaleY
      const color = CLASS_COLORS[track.class_name] ?? '#8b5cf6'

      ctx.strokeStyle = color
      ctx.lineWidth   = 2
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)

      const label = `#${track.id} ${track.class_name}${
        track.speed_kmh != null ? ` ${track.speed_kmh.toFixed(0)}km/h` : ''
      }`
      ctx.font = '10px monospace'
      const tw = ctx.measureText(label).width
      ctx.fillStyle = color + 'cc'
      ctx.fillRect(x1, y1 - 15, tw + 8, 15)
      ctx.fillStyle = '#fff'
      ctx.fillText(label, x1 + 4, y1 - 3)
    }
  }, [tracks, countingLines, countPerLine])

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">
        Track Overlay
        <span className="ml-2 text-gray-500 font-normal text-xs">
          ({tracks.length} tracks
          {countingLines.length > 0 && ` · ${countingLines.length} line${countingLines.length > 1 ? 's' : ''}`}
          )
        </span>
      </h3>
      <canvas
        ref={canvasRef}
        width={CANVAS_W}
        height={CANVAS_H}
        className="w-full rounded bg-gray-950"
      />
    </div>
  )
}
