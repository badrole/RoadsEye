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

export default function PipelineControl({ status, onStatusChange, vertical = false }) {
  const [source,    setSource]    = useState('0')
  const [conf,      setConf]      = useState('35')   // percent (0-100)
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)
  const [open,      setOpen]      = useState(vertical ? false : true)   // rail: collapsed by default

  const isRunning = status?.running ?? false

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
    await startPipeline(source, [])
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

        {/* Confidence — volume-style slider, percent */}
        <div className="flex flex-col gap-1">
          <label className="font-code text-[10px] uppercase tracking-[0.2em] text-pip">Confidence (%)</label>
          <div className="flex items-center gap-2.5">
            <input
              type="range"
              min="5"
              max="95"
              step="5"
              className="flex-1 accent-[#FFD100] cursor-pointer"
              value={conf}
              onChange={e => setConf(e.target.value)}
              disabled={isRunning}
            />
            <span className="font-code text-sm font-bold text-lane w-10 text-right">{conf}%</span>
          </div>
        </div>

        {/* Counting lines: drag endpoints on the live video (LineEditor) */}

        {/* Start / Stop buttons */}
        {!isRunning ? (
          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 rounded-sm bg-lane hover:bg-lane-bright text-asphalt font-code text-sm font-bold tracking-wider disabled:opacity-50 transition-colors shadow-glowlane"
          >
            {loading ? 'Starting…' : 'START PIPELINE'}
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStop}
            disabled={loading}
            className="w-full px-4 py-2 rounded-sm bg-stop hover:bg-red-500 text-white font-code text-sm font-bold tracking-wider disabled:opacity-50 transition-colors"
          >
            {loading ? 'Stopping…' : 'STOP PIPELINE'}
          </button>
        )}
      </form>

      {error && <p className="mt-2 text-xs text-stop">{error}</p>}

      {status && (
        <p className="mt-2 font-code text-[10px] text-pip leading-relaxed break-all">
          {isRunning
            ? `RUNNING · frame ${status.frame_index} · ${status.fps} fps · ${String(status.source).slice(0, 46)}…`
            : 'Pipeline stopped'}
        </p>
      )}
      </div>
      )}
    </div>
  )
}
