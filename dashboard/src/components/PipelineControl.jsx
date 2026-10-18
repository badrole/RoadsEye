/**
 * PipelineControl — start / stop pipeline form.
 *
 * Features:
 *   - Free-form source input (webcam index, file path, RTSP URL)
 *   - "Demo" quick-start button — synthetic scene, no model needed
 *   - Optional counting line configuration (up to 3 named lines)
 *   - Confidence threshold
 */
import { useState } from 'react'

const API = '/api'

// Default counting line for demo mode: vertical centre line on a 1280×720 frame
const DEMO_LINE = { name: 'main', x1: 640, y1: 252, x2: 640, y2: 612 }

export default function PipelineControl({ status, onStatusChange, vertical = false }) {
  const [source,    setSource]    = useState('0')
  const [conf,      setConf]      = useState('35')   // percent (0-100)
  const [showLines, setShowLines] = useState(false)
  const [lines,     setLines]     = useState([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)
  const [open,      setOpen]      = useState(vertical ? false : true)   // rail: collapsed by default

  const isRunning = status?.running ?? false

  // ── helpers ──────────────────────────────────────────────────────────────

  function addLine() {
    if (lines.length >= 3) return
    setLines(prev => [
      ...prev,
      { name: `line${prev.length + 1}`, x1: 0, y1: 360, x2: 1280, y2: 360 },
    ])
  }

  function removeLine(i) {
    setLines(prev => prev.filter((_, idx) => idx !== i))
  }

  function updateLine(i, field, value) {
    setLines(prev =>
      prev.map((l, idx) =>
        idx === i ? { ...l, [field]: field === 'name' ? value : Number(value) } : l
      )
    )
  }

  async function startPipeline(src, linesCfg) {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/pipeline/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: src,
          confidence_threshold: parseFloat(conf) / 100,
          counting_lines: linesCfg,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? `HTTP ${res.status}`)
      }
      onStatusChange?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleStart(e) {
    e.preventDefault()
    await startPipeline(source, lines)
  }

  async function handleDemo() {
    await startPipeline('demo', [DEMO_LINE])
  }

  async function handleStop() {
    setLoading(true)
    setError(null)
    try {
      await fetch(`${API}/pipeline/stop`, { method: 'POST' })
      onStatusChange?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="bg-asphalt-panel border border-lane-dim/40 rounded-sm">
      {/* Collapse header */}
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-lane-wash transition-colors"
      >
        <span className="road-label">Source Setup</span>
        <span className="flex items-center gap-2">
          {isRunning && <span className="w-1.5 h-1.5 rounded-full bg-go" />}
          <span className="font-code text-xs text-lane">{open ? '▾' : '▸'}</span>
        </span>
      </button>

      {open && (
      <div className="px-3 pb-3">
      <form onSubmit={handleStart} className="flex flex-col gap-3">
        {/* Source */}
        <div className="flex flex-col gap-1">
          <label className="font-code text-[10px] uppercase tracking-[0.2em] text-pip">Source</label>
          <input
            className="bg-asphalt-deep border border-lane-dim/50 rounded-sm px-3 py-2 text-sm font-code text-chalk w-full focus:outline-none focus:border-lane transition-colors placeholder:text-pip/60"
            placeholder="0 / file / rtsp://…"
            value={source}
            onChange={e => setSource(e.target.value)}
            disabled={isRunning}
          />
        </div>

        {/* Confidence — percent, converted for the 0-1 wire value */}
        <div className="flex flex-col gap-1">
          <label className="font-code text-[10px] uppercase tracking-[0.2em] text-pip">Confidence (%)</label>
          <input
            type="number"
            step="5"
            min="5"
            max="95"
            className="bg-asphalt-deep border border-lane-dim/50 rounded-sm px-3 py-2 text-sm font-code text-chalk w-full focus:outline-none focus:border-lane transition-colors"
            value={conf}
            onChange={e => setConf(e.target.value)}
            disabled={isRunning}
          />
        </div>

        {/* Counting lines toggle */}
        {!isRunning && (
          <button
            type="button"
            onClick={() => setShowLines(v => !v)}
            className="px-3 py-2 rounded-sm border border-lane-dim/50 font-code text-[10px] uppercase tracking-widest text-pip hover:text-lane hover:border-lane transition-colors"
          >
            {showLines ? 'Hide lines' : `Lines (${lines.length})`}
          </button>
        )}

        {/* Start / Stop buttons */}
        {!isRunning ? (
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={loading}
              className="flex-1 px-4 py-2 rounded-sm bg-lane hover:bg-lane-bright text-asphalt font-code text-sm font-bold tracking-wider disabled:opacity-50 transition-colors shadow-glowlane"
            >
              {loading ? 'Starting…' : 'START'}
            </button>
            <button
              type="button"
              onClick={handleDemo}
              disabled={loading}
              className="flex-1 px-4 py-2 rounded-sm border border-lane-dim/60 hover:border-lane hover:text-lane text-pip font-code text-sm font-bold tracking-wider disabled:opacity-50 transition-colors"
              title="Run synthetic demo — no ONNX model or camera required"
            >
              DEMO
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={handleStop}
            disabled={loading}
            className="px-4 py-2 rounded-sm bg-stop hover:bg-red-500 text-white font-code text-sm font-bold tracking-wider disabled:opacity-50 transition-colors"
          >
            {loading ? 'Stopping…' : 'STOP'}
          </button>
        )}
      </form>

      {/* Counting lines editor */}
      {showLines && !isRunning && (
        <div className="mt-3 space-y-2">
          <div className="font-code text-[10px] text-pip uppercase tracking-widest mb-1">
            Counting lines — count on line crossing
          </div>
          {lines.map((line, i) => (
            <div key={i} className="flex gap-1.5">
              <input
                className="bg-asphalt-deep border border-lane-dim/40 rounded-sm px-2 py-1 font-code text-xs text-chalk w-16"
                placeholder="name"
                value={line.name}
                onChange={e => updateLine(i, 'name', e.target.value)}
              />
              {['x1', 'y1', 'x2', 'y2'].map(f => (
                <input
                  key={f}
                  type="number"
                  className="bg-asphalt-deep border border-lane-dim/40 rounded-sm px-1.5 py-1 font-code text-xs text-chalk w-12"
                  placeholder={f}
                  value={line[f]}
                  onChange={e => updateLine(i, f, e.target.value)}
                />
              ))}
              <button
                type="button"
                onClick={() => removeLine(i)}
                className="font-code text-xs text-stop hover:text-red-400 px-1"
              >
                X
              </button>
            </div>
          ))}
          {lines.length < 3 && (
            <button
              type="button"
              onClick={addLine}
              className="font-code text-xs text-lane hover:text-lane-bright transition-colors"
            >
              + Add line
            </button>
          )}
        </div>
      )}

      {error && <p className="mt-2 text-xs text-stop">{error}</p>}

      {status && (
        <p className="mt-2 font-code text-[10px] text-pip leading-relaxed break-all">
          {isRunning
            ? `${status.demo_mode ? '[DEMO] ' : ''}RUNNING · frame ${status.frame_index} · ${status.fps} fps · ${String(status.source).slice(0, 46)}…`
            : 'Pipeline stopped'}
        </p>
      )}
      </div>
      )}
    </div>
  )
}
