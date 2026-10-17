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

export default function PipelineControl({ status, onStatusChange }) {
  const [source,    setSource]    = useState('0')
  const [conf,      setConf]      = useState('0.35')
  const [showLines, setShowLines] = useState(false)
  const [lines,     setLines]     = useState([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)

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
          confidence_threshold: parseFloat(conf),
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
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Pipeline Control</h3>

      <form onSubmit={handleStart} className="flex flex-wrap gap-3 items-end">
        {/* Source */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400">Source</label>
          <input
            className="bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 w-52 focus:outline-none focus:border-blue-500"
            placeholder="0  /path/to/video  rtsp://…"
            value={source}
            onChange={e => setSource(e.target.value)}
            disabled={isRunning}
          />
        </div>

        {/* Confidence */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400">Confidence</label>
          <input
            type="number"
            step="0.05"
            min="0.05"
            max="0.95"
            className="bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 w-24 focus:outline-none focus:border-blue-500"
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
            className="px-3 py-1.5 rounded border border-gray-600 text-xs text-gray-400 hover:text-gray-200 hover:border-gray-400 transition-colors"
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
              className="px-4 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-sm font-medium disabled:opacity-50 transition-colors"
            >
              {loading ? 'Starting…' : 'Start'}
            </button>
            <button
              type="button"
              onClick={handleDemo}
              disabled={loading}
              className="px-4 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-sm font-medium disabled:opacity-50 transition-colors"
              title="Run synthetic demo — no ONNX model or camera required"
            >
              Demo
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={handleStop}
            disabled={loading}
            className="px-4 py-1.5 rounded bg-red-600 hover:bg-red-500 text-sm font-medium disabled:opacity-50 transition-colors"
          >
            {loading ? 'Stopping…' : 'Stop'}
          </button>
        )}
      </form>

      {/* Counting lines editor */}
      {showLines && !isRunning && (
        <div className="mt-3 space-y-2">
          <div className="text-xs text-gray-500 mb-1">
            Counting lines — vehicles are counted when they cross a line
          </div>
          {lines.map((line, i) => (
            <div key={i} className="flex flex-wrap gap-2 items-center">
              <input
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 w-20"
                placeholder="name"
                value={line.name}
                onChange={e => updateLine(i, 'name', e.target.value)}
              />
              {['x1', 'y1', 'x2', 'y2'].map(f => (
                <input
                  key={f}
                  type="number"
                  className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 w-16"
                  placeholder={f}
                  value={line[f]}
                  onChange={e => updateLine(i, f, e.target.value)}
                />
              ))}
              <button
                type="button"
                onClick={() => removeLine(i)}
                className="text-xs text-red-500 hover:text-red-400"
              >
                ✕
              </button>
            </div>
          ))}
          {lines.length < 3 && (
            <button
              type="button"
              onClick={addLine}
              className="text-xs text-blue-400 hover:text-blue-300"
            >
              + Add line
            </button>
          )}
        </div>
      )}

      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

      {status && (
        <p className="mt-2 text-xs text-gray-500">
          {isRunning
            ? `${status.demo_mode ? '[DEMO] ' : ''}Running — frame ${status.frame_index} · ${status.fps} fps · source: ${status.source}`
            : 'Pipeline stopped'}
        </p>
      )}
    </div>
  )
}
