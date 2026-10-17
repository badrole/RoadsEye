/**
 * TrafficVision Dashboard — main layout.
 *
 * Layout (desktop):
 *   ┌─────────────────────────────┬──────────────────┐
 *   │  Video feed (large)         │  Stats cards      │
 *   │                             │  Class breakdown  │
 *   │                             │  Alerts panel     │
 *   ├─────────────────────────────┴──────────────────┤
 *   │  Vehicle count chart │ Speed distribution       │
 *   ├────────────────────────────────────────────────┤
 *   │  Track overlay (full width)                     │
 *   └────────────────────────────────────────────────┘
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

const API = '/api'

function usePipelineStatus() {
  const [status, setStatus] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API}/pipeline/status`)
      if (res.ok) setStatus(await res.json())
    } catch { /* backend not up yet */ }
  }, [])

  // Poll every 3 s
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000)
    return () => clearInterval(id)
  }, [refresh])

  return { status, refresh }
}

export default function App() {
  const { status, refresh }        = usePipelineStatus()
  const { connected, metrics }     = useMetricsStream(true)
  const { latest, history, alerts } = metrics

  const pipelineRunning = status?.running ?? false

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="text-blue-400 text-xl font-bold tracking-tight">TrafficVision</div>
          <span className="text-gray-600 text-sm hidden sm:block">Real-Time Traffic Analytics</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span
            className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-gray-600'}`}
          />
          <span className="text-gray-400">
            {connected ? 'Metrics connected' : 'Metrics disconnected'}
          </span>
        </div>
      </header>

      <main className="flex-1 p-4 lg:p-6 flex flex-col gap-4">
        {/* Pipeline control bar */}
        <PipelineControl status={status} onStatusChange={refresh} />

        {/* KPI cards */}
        <StatsCards latest={latest} pipelineStatus={status} />

        {/* Video + sidebar */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2">
            <VideoFeed enabled={pipelineRunning} />
          </div>
          <div className="flex flex-col gap-4">
            <ClassBreakdown latest={latest} />
            <AlertsPanel alerts={alerts} />
          </div>
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <VehicleCountChart history={history} />
          <SpeedDistribution history={history} />
        </div>

        {/* Track overlay */}
        <TrafficFlowMap latest={latest} pipelineStatus={status} />
      </main>
    </div>
  )
}
