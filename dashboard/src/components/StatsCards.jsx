import { useEffect, useState } from 'react'

/**
 * KpiStrip — compact stat row above the CCTV feed.
 * Shows total vehicles (by active mode), motorcycles counted, in-frame vehicles,
 * per-minute rate, average speed, and live uptime clock.
 */
export default function StatsCards({ latest, perMinute = [], pipelineStatus }) {
  const running  = pipelineStatus?.running ?? false
  const upSec    = pipelineStatus?.uptime_seconds ?? null
  const thisMinute = perMinute.length ? perMinute[perMinute.length - 1].vehicles : null
  const inFrameMotors = Array.isArray(latest?.tracks)
    ? latest.tracks.filter(t => t.class_name === 'motorcycle').length
    : 0

  const cards = [
    {
      label: 'IN FRAME',
      value: latest != null
        ? `${latest.vehicles_in_frame ?? 0}${inFrameMotors > 0 ? ` (${inFrameMotors} mtr)` : ''}`
        : '—',
      color: 'text-chalk',
      icon: 'frame',
    },
    { label: 'PER MINUTE',      value: thisMinute ?? '—',           color: 'text-chalk', icon: 'radar' },
    { label: 'AVG KM/H',        value: latest?.avg_speed_kmh != null ? latest.avg_speed_kmh.toFixed(1) : '—', color: 'text-lane', icon: 'speed' },
    { label: 'START → NOW',     value: null, color: 'text-chalk', icon: 'clock', clock: true },
  ]

  const ICONS = {
    frame: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M4 8V5a1 1 0 011-1h3M20 8V5a1 1 0 00-1-1h-3M4 16v3a1 1 0 001 1h3M20 16v3a1 1 0 01-1 1h-3" strokeLinecap="round" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    ),
    radar: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <circle cx="12" cy="10" r="3" />
        <path d="M4 14a8 8 0 000-8M20 6a8 8 0 000 8M4.5 18a11 11 0 000-15M19.5 3a11 11 0 000 15" strokeLinecap="round" />
      </svg>
    ),
    speed: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <circle cx="12" cy="13" r="8" />
        <path d="M12 13l3.5-4" strokeLinecap="round" />
      </svg>
    ),
    clock: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" strokeLinecap="round" />
      </svg>
    ),
  }

  return (
    <div className="panel px-4 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-1.5">
      {cards.map((c) => (
        <div key={c.label} className="flex items-center gap-2 min-w-fit">
          <span className="text-lane/90">{ICONS[c.icon]}</span>
          <span className="road-label">{c.label}</span>
          {c.clock
            ? <RunClock uptimeSeconds={upSec} running={running} />
            : (
              <span className={`font-code text-lg font-bold leading-none ${c.color}`}>
                {c.value}
              </span>
            )}
        </div>
      ))}
    </div>
  )
}

const _p2 = (n) => String(n).padStart(2, '0')
const _hhmmss = (d) => `${_p2(d.getHours())}:${_p2(d.getMinutes())}:${_p2(d.getSeconds())}`

/** start→now wallclock; live ticking seconds, re-renders every second */
function RunClock({ uptimeSeconds, running }) {
  const [anchor, setAnchor] = useState(null)   // pipeline start wallclock (ms)
  const [now, setNow]       = useState(() => new Date())

  useEffect(() => {
    if (!running || uptimeSeconds == null) { setAnchor(null); return }
    const candidate = Date.now() - uptimeSeconds * 1000
    // polling every 3s produces shifted start estimates — lock to earliest
    setAnchor(prev => (prev == null || candidate < prev) ? candidate : prev)
  }, [running, uptimeSeconds])

  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [running])

  if (!running || anchor == null) {
    return <span className="font-code text-lg text-pip leading-none">—</span>
  }
  return (
    <span className="font-code text-lg leading-none text-chalk">
      <span className="text-pip">{_hhmmss(new Date(anchor))}</span>
      <span className="text-lane mx-1.5">→</span>
      {_hhmmss(now)}
    </span>
  )
}
