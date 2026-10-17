/**
 * RoadsEye Dashboard — main layout.
 *
 * Structure (desktop): control rail on the left (brand, live indicators,
 * KPI board) + monitoring deck on the right (video hero, charts, overlay).
 * Same feature set as before — re-arranged into a rail layout.
 */
import { useState, useEffect, useCallback } from 'react'
import { useMetricsStream } from './hooks/useWebSocket'

import VideoFeed        from './components/VideoFeed'
import StatsCards       from './components/StatsCards'
import VehicleCountChart from './components/VehicleCountChart'
import SpeedDistribution from './components/SpeedDistribution'
import ClassBreakdown   from './components/ClassBreakdown'
import AlertsPanel      from './components/AlertsPanel'
import TrafficFlowMap   from './components/TrafficFlowMap'
import PipelineControl  from './components/PipelineControl'
import HistoryPanel     from './components/HistoryPanel'

const API = '/api'

function usePipelineStatus() {
  const [status, setStatus] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API}/pipeline/status`)
      if (res.ok) setStatus(await res.json())
    } catch { /* backend not up yet */ }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000)
    return () => clearInterval(id)
  }, [refresh])

  return { status, refresh }
}

/* ── Left rail: brand + live indicators + pipeline control ───────────────── */
function ControlRail({ status, connected, onRefresh, latest }) {
  const running = status?.running ?? false

  return (
    <aside className="w-[268px] shrink-0 hidden lg:flex flex-col border-r border-lane-dim/40 bg-asphalt-deep/70">
      {/* Brand — compact plate */}
      <div className="px-4 pt-4 pb-3 border-b border-lane-dim/30">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 24 24" className="w-7 h-7" fill="none" aria-hidden="true">
            <rect x="1" y="1" width="22" height="22" rx="4" stroke="#FFD100" strokeWidth="2" />
            <path d="M12 21 L12 14M12 13 L8 5M12 13 L16 5" stroke="#FFD100" strokeWidth="2" strokeDasharray="3 2.5" />
          </svg>
          <div>
            <div className="font-code text-base font-bold tracking-[0.14em] text-lane leading-none">RoadsEye</div>
            <div className="road-label mt-1">Traffic Analytics</div>
          </div>
        </div>
        {/* status — satu baris kecil */}
        <div className="mt-3 flex items-center gap-3 font-code text-[11px]">
          <span className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-go' : 'bg-stop'}`} />
            <span className="text-chalk">WS</span>
          </span>
          <span className={running ? 'text-lane' : 'text-pip'}>{running ? 'RUN' : 'IDLE'}</span>
          <span className="text-chalk">{(status?.fps ?? 0).toFixed(0)}<span className="text-pip"> fps</span></span>
        </div>
      </div>

      {/* Pipeline control (collapsible) + breakdown */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-3 pt-4">
          <PipelineControl status={status} onStatusChange={onRefresh} vertical />
        </div>
        <div className="px-3 pt-4">
          <div className="road-label mb-2 px-1">Komposisi Kelas</div>
          <ClassBreakdown latest={latest} />
        </div>
      </div>

      <div className="px-5 py-4 border-t border-lane-dim/30">
        <p className="font-code text-[10px] text-pip tracking-widest uppercase">
          Kab. Tangerang - Dishub
        </p>
      </div>
    </aside>
  )
}

/* ── Mobile brand strip (rail hidden below lg) ────────────────────────────── */
function MobileHeader({ status, connected }) {
  return (
    <header className="lg:hidden sticky top-0 z-30 bg-asphalt-deep/90 backdrop-blur border-b border-lane-dim/40 px-4 py-3 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <svg viewBox="0 0 24 24" className="w-7 h-7" fill="none" aria-hidden="true">
          <rect x="1" y="1" width="22" height="22" rx="4" stroke="#FFD100" strokeWidth="2" />
          <path d="M12 21 L8 5 M8 8 L10 5 M12 21 L16 5 M16 8 L14 5" stroke="#FFD100" strokeWidth="1.8" strokeDasharray="3 2.5" />
        </svg>
        <span className="font-code font-bold tracking-[0.14em] text-lane">RoadsEye</span>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span className={`w-2 h-2 rounded-full ${connected ? 'bg-go' : 'bg-stop'}`} />
        <span className="font-code text-lane">{(status?.fps ?? 0).toFixed(0)} fps</span>
      </div>
    </header>
  )
}

/* ── Deck heading with lane stripe ────────────────────────────────────────── */
function RailTitle({ children }) {
  return (
    <div className="flex items-center gap-3 mb-3">
      <h2 className="road-label">{children}</h2>
      <div className="lane-stripe-h flex-1 opacity-60" />
    </div>
  )
}

export default function App() {
  const { status, refresh }        = usePipelineStatus()
  const { connected, metrics }     = useMetricsStream(true)
  const { latest, history, alerts } = metrics

  const pipelineRunning = status?.running ?? false

  // Persistent history (SQLite) — refreshed on mount, every 30 s,
  // and whenever detection stops (session close) — never resets.
  const [historyKey, setHistoryKey] = useState(0)
  useEffect(() => {
    setHistoryKey((k) => k + 1)
    const id = setInterval(() => setHistoryKey((k) => k + 1), 30_000)
    return () => clearInterval(id)
  }, [pipelineRunning])

  return (
    <div className="min-h-screen flex flex-col lg:flex-row">
      <ControlRail status={status} connected={connected} onRefresh={refresh} latest={latest} />
      <MobileHeader status={status} connected={connected} />

      {/* Monitoring deck */}
      <main className="flex-1 min-w-0 p-4 lg:p-6 flex flex-col gap-5">
        {/* CCTV hero — KPI mini strip di atas video */}
        <section>
          <RailTitle>Cctv Live</RailTitle>
          <div className="mb-2">
            <StatsCards latest={latest} pipelineStatus={status} />
          </div>
          <VideoFeed enabled={pipelineRunning} />
        </section>

        {/* Charts */}
        <section className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          <div>
            <RailTitle>Vehicles In Frame</RailTitle>
            <VehicleCountChart history={history} />
          </div>
          <div>
            <RailTitle>Speed Distribution</RailTitle>
            <SpeedDistribution history={history} />
          </div>
        </section>

        {/* Track overlay — full width */}
        <section>
          <RailTitle>Track Overlay</RailTitle>
          <TrafficFlowMap latest={latest} pipelineStatus={status} />
        </section>

        {/* Persistent history (SQLite) — historical totals + event table */}
        <section>
          <RailTitle>Riwayat Persisten</RailTitle>
          <HistoryPanel refreshKey={historyKey} />
        </section>

        {/* Alerts — full-width strip */}
        <section>
          <RailTitle>Alerts</RailTitle>
          <AlertsPanel alerts={alerts} />
        </section>
      </main>
    </div>
  )
}
