/**
 * PipelineControl — start / stop pipeline form & counting mode selector.
 *
 * Features:
 *   - Free-form source input (webcam index, file path, RTSP/HLS URL)
 *   - 2-option Counting Mode: Per Line vs Per ID (can be switched before or during run)
 *   - Confidence threshold
 */
import { useState, useEffect } from 'react'

const API = '/api'

export default function PipelineControl({ status, onStatusChange, vertical = false }) {
  const [source,    setSource]    = useState('0')
  const [conf,      setConf]      = useState('25')   // percent (0-100)
  const [countMode, setCountMode] = useState(status?.count_mode || 'line')
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)
  const [open,      setOpen]      = useState(vertical ? false : true)

  const isRunning = status?.running ?? false

  useEffect(() => {
    if (status?.count_mode) {
      setCountMode(status.count_mode)
    }
  }, [status?.count_mode])

  async function handleModeChange(nextMode) {
    setCountMode(nextMode)
    try {
      await fetch(`${API}/pipeline/count-mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: nextMode }),
      })
      onStatusChange?.()
    } catch {
      /* applied on next start if offline */
    }
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
          count_mode: countMode,
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
            placeholder="0 / file / rtsp:// / .m3u8…"
            value={source}
            onChange={e => setSource(e.target.value)}
            disabled={isRunning}
          />
        </div>

        {/* Counting Mode: Per Line vs Per ID (always active, even while running) */}
        <div className="flex flex-col gap-1">
          <label className="font-code text-[10px] uppercase tracking-[0.2em] text-pip">
            Count Mode
          </label>
          <div className="grid grid-cols-2 gap-1.5 bg-asphalt-deep p-1 rounded-sm border border-lane-dim/40">
            <button
              type="button"
              onClick={() => handleModeChange('line')}
              className={`py-1.5 px-2 rounded-sm font-code text-xs font-bold uppercase tracking-wider transition-colors cursor-pointer ${
                countMode === 'line'
                  ? 'bg-lane text-asphalt shadow-glowlane'
                  : 'text-pip hover:text-chalk'
              }`}
            >
              Per Line
            </button>
            <button
              type="button"
              onClick={() => handleModeChange('id')}
              className={`py-1.5 px-2 rounded-sm font-code text-xs font-bold uppercase tracking-wider transition-colors cursor-pointer ${
                countMode === 'id'
                  ? 'bg-lane text-asphalt shadow-glowlane'
                  : 'text-pip hover:text-chalk'
              }`}
            >
              Per ID
            </button>
          </div>
          <span className="font-code text-[10px] text-pip">
            {countMode === 'id'
              ? 'Semua motor & kendaraan di frame langsung dihitung sesuai ID.'
              : 'Hitung kendaraan saat melewati garis hitung.'}
          </span>
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
            ? `RUNNING (${(status.count_mode || 'line').toUpperCase()}) · frame ${status.frame_index} · ${status.fps} fps · ${String(status.source).slice(0, 40)}`
            : 'Pipeline stopped'}
        </p>
      )}
      </div>
      )}
    </div>
  )
}
