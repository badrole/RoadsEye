/**
 * DatabaseViewer — Dedicated web interface for browsing, filtering, and exporting
 * persisted traffic records from SQLite.
 *
 * Implements /ui-ux-pro-max guidelines:
 * - High-contrast CCTV dark theme (asphalt, lane yellow, hazard orange).
 * - Minimum 44px accessible touch targets.
 * - Monospace accents for timestamps and IDs.
 * - Robust filtering (class, date range, session).
 * - Real-time statistics cards.
 * - Instant CSV export & WAL database backup.
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import HistoryCalendar from './HistoryCalendar'

const API = '/api'
const PAGE_SIZE_OPTIONS = [15, 25, 50, 100]

const CLASS_CONFIG = {
  motorcycle: { label: 'Motorcycle', color: '#f97316', bg: 'rgba(249, 115, 22, 0.15)', border: 'rgba(249, 115, 22, 0.4)' },
  car:        { label: 'Car', color: '#facc15', bg: 'rgba(250, 204, 21, 0.15)', border: 'rgba(250, 204, 21, 0.4)' },
  bus:        { label: 'Bus', color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.15)', border: 'rgba(56, 189, 248, 0.4)' },
  truck:      { label: 'Truck', color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.15)', border: 'rgba(148, 163, 184, 0.4)' },
}

function formatWib(isoUtc) {
  if (!isoUtc) return '—'
  try {
    const d = new Date(isoUtc)
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Jakarta',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d) + ' WIB'
  } catch {
    return isoUtc
  }
}

const _MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
function shortDate(ymd) {
  if (!ymd) return '—'
  const [y, m, d] = ymd.split('-').map(Number)
  if (!y || !m || !d) return ymd
  return `${d} ${_MONTHS_SHORT[m - 1]}`
}



export default function DatabaseViewer({ onClose }) {
  const [events, setEvents] = useState({ total: 0, items: [] })
  const [summary, setSummary] = useState(null)
  const [allDaily, setAllDaily] = useState([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)

  // Filters
  const [vehicleType, setVehicleType] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [activePreset, setActivePreset] = useState('all')

  // Feedback notifications
  const [actionNotice, setActionNotice] = useState(null)
  const [isExporting, setIsExporting] = useState(false)

  // ── Load summary stats ─────────────────────────────────────────────────────
  const loadSummary = useCallback(async () => {
    try {
      const qs = new URLSearchParams()
      if (vehicleType) qs.set('vehicle_type', vehicleType)
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)

      const res = await fetch(`${API}/analytics/history/summary?${qs}`)
      if (res.ok) {
        setSummary(await res.json())
      }
    } catch {
      /* ignore offline */
    }
  }, [vehicleType, dateFrom, dateTo])

  // ── Load paginated events ──────────────────────────────────────────────────
  const loadEvents = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      qs.set('page', String(page))
      qs.set('page_size', String(pageSize))
      if (vehicleType) qs.set('vehicle_type', vehicleType)
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)

      const res = await fetch(`${API}/analytics/events?${qs}`)
      if (res.ok) {
        const data = await res.json()
        setEvents(data)
      }
    } catch {
      /* ignore offline */
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, vehicleType, dateFrom, dateTo])

  useEffect(() => {
    loadSummary()
    loadEvents()
  }, [loadSummary, loadEvents])

  /* Unfiltered per-day totals: calendar + today/yesterday chips */
  const loadAllDaily = useCallback(async () => {
    try {
      const res = await fetch(`${API}/analytics/history/summary`)
      if (res.ok) setAllDaily((await res.json()).daily ?? [])
    } catch {
      /* ignore offline */
    }
  }, [])

  useEffect(() => {
    loadAllDaily()
  }, [loadAllDaily])

  // Filter change resets page to 1
  useEffect(() => {
    setPage(1)
  }, [vehicleType, dateFrom, dateTo, pageSize])

  // ── Date Presets ───────────────────────────────────────────────────────────
  const applyPreset = (preset) => {
    setActivePreset(preset)
    const today = new Date().toISOString().slice(0, 10)
    if (preset === 'today') {
      setDateFrom(today)
      setDateTo(today)
    } else if (preset === 'yesterday') {
      const d = new Date()
      d.setDate(d.getDate() - 1)
      const yday = d.toISOString().slice(0, 10)
      setDateFrom(yday)
      setDateTo(yday)
    } else if (preset === '7days') {
      const d = new Date()
      d.setDate(d.getDate() - 7)
      setDateFrom(d.toISOString().slice(0, 10))
      setDateTo(today)
    } else {
      setDateFrom('')
      setDateTo('')
    }
  }

  // ── CSV Export ─────────────────────────────────────────────────────────────
  const exportCsv = async () => {
    setIsExporting(true)
    try {
      const qs = new URLSearchParams()
      if (vehicleType) qs.set('vehicle_type', vehicleType)
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)

      // Direct download trigger from streaming endpoint
      const link = document.createElement('a')
      link.href = `${API}/analytics/events/export.csv?${qs}`
      link.setAttribute('download', `roadseye_traffic_${new Date().toISOString().slice(0, 10)}.csv`)
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)

      setActionNotice({ type: 'success', text: 'CSV file successfully downloaded to your computer.' })
    } catch {
      setActionNotice({ type: 'error', text: 'Failed to export CSV file.' })
    } finally {
      setIsExporting(false)
      setTimeout(() => setActionNotice(null), 4000)
    }
  }

  // ── Clicked calendar day → filter the table to that single date ──────────
  const applyDayFilter = (date) => {
    setDateFrom(date)
    setDateTo(date)
    setActivePreset('custom')
  }

  // ── Delete the selected calendar day's history (DB + Google Sheet) ────────
  const deleteDayHistory = async () => {
    if (!dateFrom) return
    const ok = window.confirm(
      `Delete ALL recorded history for ${dateFrom}? This removes database records and the matching Google Sheet rows. Cannot be undone.`
    )
    if (!ok) return
    setLoading(true)
    try {
      const res = await fetch(`${API}/analytics/events/by-date?date=${dateFrom}`, { method: 'DELETE' })
      if (res.ok) {
        const d = await res.json()
        setActionNotice({
          type: 'success',
          text: `History ${dateFrom} deleted — ${d.deleted_db} database records removed${d.sheet === 'success' ? ', Google Sheet rows removed and MAIN rebuilt' : ''}.`,
        })
      } else {
        const err = await res.json().catch(() => ({}))
        setActionNotice({ type: 'error', text: err.detail || 'Delete failed.' })
      }
    } catch {
      setActionNotice({ type: 'error', text: 'Failed to connect to database server.' })
    } finally {
      loadSummary()
      loadEvents()
      loadAllDaily()
      setTimeout(() => setActionNotice(null), 6000)
    }
  }

  // Peak + data span are computed per DAY (Asia/Jakarta), never all-time:
  // single-date filter → that exact day; range → the busiest day inside it.
  const peakDay = useMemo(() => {
    const list = summary?.daily ?? []
    if (!list.length) return null
    if (dateFrom && dateFrom === dateTo && dateTo === dateFrom) {
      return list.find((d) => d.date === dateFrom) ?? null
    }
    return list.reduce((best, d) => (!best || d.total > best.total ? d : best), null)
  }, [summary, dateFrom, dateTo])

  const totalPages = Math.max(1, Math.ceil(events.total / pageSize))

  // Find dominant class
  let dominantClass = '—'
  let dominantCount = 0
  if (summary?.count_per_class) {
    for (const [cls, count] of Object.entries(summary.count_per_class)) {
      if (count > dominantCount) {
        dominantCount = count
        dominantClass = cls
      }
    }
  }
  const dominantPct = summary?.total_events > 0 ? Math.round((dominantCount / summary.total_events) * 100) : 0

  return (
    <div className="w-full min-h-screen bg-[#090d16] text-[#f8fafc] flex flex-col font-sans">
      {/* Top Header Strip */}
      <header className="sticky top-0 z-40 bg-[#0d1424]/95 backdrop-blur-md border-b border-[#243048] px-4 lg:px-8 py-3.5 flex flex-wrap items-center justify-between gap-4 shadow-xl">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-lane/10 border border-lane/40 flex items-center justify-center text-lane">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2 1.5 3 3.5 3h9c2 0 3.5-1 3.5-3V7M4 7c0-2 1.5-3 3.5-3h9c2 0 3.5 1 3.5 3M4 7h16m-8 4v6m-4-3h8" />
            </svg>
          </div>
          <div>
            <h1 className="font-code text-lg font-bold text-lane tracking-wider uppercase">
              Vehicle Traffic Database
            </h1>
          </div>
        </div>

        {/* Global Action Buttons */}
        <div className="flex items-center gap-2.5 flex-wrap">
          <button
            onClick={exportCsv}
            disabled={isExporting}
            className="h-10 px-4 rounded-md font-code text-xs font-bold tracking-wider uppercase bg-[#1e293b] text-chalk border border-lane-dim/50 hover:border-lane hover:text-lane hover:bg-lane/10 transition-all cursor-pointer flex items-center gap-2 shadow"
          >
            <svg className="w-4 h-4 text-lane" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            {isExporting ? 'Exporting…' : 'Export CSV'}
          </button>

          <button
            onClick={() => {
              if (window.opener) {
                window.close()
              } else if (onClose) {
                onClose()
              } else {
                window.location.href = window.location.origin
              }
            }}
            className="h-10 px-4 rounded-md font-code text-xs font-bold tracking-wider uppercase bg-lane text-asphalt hover:bg-yellow-400 transition-all cursor-pointer shadow-glowlane flex items-center gap-1.5"
          >
            <span>{typeof window !== 'undefined' && window.opener ? '✕ Close Window' : '← Back to Live CCTV'}</span>
          </button>
        </div>
      </header>

      {/* Main Content Body */}
      <main className="flex-1 px-4 lg:px-8 py-6 flex flex-col gap-6 max-w-7xl mx-auto w-full">
        {/* Toast Alert */}
        {actionNotice && (
          <div
            className={`px-4 py-3 rounded-lg border font-code text-xs flex items-center justify-between transition-all ${
              actionNotice.type === 'success'
                ? 'bg-emerald-950/70 border-emerald-500/60 text-emerald-200'
                : 'bg-rose-950/70 border-rose-500/60 text-rose-200'
            }`}
          >
            <span>{actionNotice.text}</span>
            <button onClick={() => setActionNotice(null)} className="text-pip hover:text-white ml-4">✕</button>
          </div>
        )}

        {/* ── KPI Summary Cards ────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Card 1: Total Records */}
          <div className="p-4 rounded-xl bg-[#121929]/80 border border-[#243048] backdrop-blur relative overflow-hidden group hover:border-lane/40 transition-colors">
            <div className="flex items-center justify-between">
              <span className="font-code text-xs text-pip uppercase tracking-wider">Total Recorded</span>
              <span className="p-1.5 rounded-md bg-lane/10 text-lane">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </span>
            </div>
            <div className="font-code text-3xl font-extrabold text-lane mt-2">
              {summary?.total_events?.toLocaleString('en-US') ?? '0'}
            </div>
          </div>

          {/* Card 2: Uptime — recording start → stop (per selected day), with
              the per-day peak hour + recorded volume on the line BELOW it */}
          <div className="p-4 rounded-xl bg-[#121929]/80 border border-[#243048] backdrop-blur relative overflow-hidden group hover:border-lane/40 transition-colors">
            <div className="flex items-center justify-between">
              <span className="font-code text-xs text-pip uppercase tracking-wider">Uptime</span>
              <span className="p-1.5 rounded-md bg-amber-500/10 text-amber-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </span>
            </div>
            <div className="font-code text-2xl font-bold text-chalk mt-2 whitespace-nowrap">
              {peakDay?.first_time || '—'}<span className="text-amber-400">-</span>{peakDay?.last_time || '—'}
            </div>
            <p className="font-code text-[11px] text-pip mt-1">
              {peakDay ? (
                <>
                  <span className="text-amber-300 font-bold">⭐ Peak Hour {peakDay.peak_hour || '—'}</span>
                  {' · '}{peakDay.peak_count.toLocaleString('en-US')} vehicles · {shortDate(peakDay.date)}
                </>
              ) : (
                'Not detected yet'
              )}
            </p>
          </div>

          {/* Card 3: Dominant Class */}
          <div className="p-4 rounded-xl bg-[#121929]/80 border border-[#243048] backdrop-blur relative overflow-hidden group hover:border-lane/40 transition-colors">
            <div className="flex items-center justify-between">
              <span className="font-code text-xs text-pip uppercase tracking-wider">Dominant Class</span>
              <span className="p-1.5 rounded-md bg-cyan-500/10 text-cyan-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </span>
            </div>
            <div className="font-code text-2xl font-bold text-cyan-300 capitalize mt-2">
              {CLASS_CONFIG[dominantClass]?.label || dominantClass}
            </div>
            <p className="font-code text-[11px] text-pip mt-1">
              Share {dominantPct}% · {dominantCount.toLocaleString('en-US')} units
            </p>
          </div>
        </div>

        {/* ── Daily History: calendar of recorded days + today/yesterday totals ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 p-4 rounded-xl bg-[#121929]/80 border border-[#243048] flex flex-col gap-3 shadow-lg">
            <h2 className="font-code text-xs text-pip uppercase tracking-wider flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-lane" />
              Recorded Days
            </h2>
            <HistoryCalendar
              daily={allDaily}
              selectedDate={dateFrom && dateFrom === dateTo ? dateFrom : ''}
              onSelect={applyDayFilter}
              onDelete={deleteDayHistory}
            />
          </div>

          {/* Busiest days inside the current filter range, newest first */}
          <div className="p-4 rounded-xl bg-[#121929]/80 border border-[#243048] flex flex-col shadow-lg">
            <h2 className="font-code text-xs text-pip uppercase tracking-wider flex items-center gap-2 pb-2 border-b border-[#243048]">
              <span className="w-1.5 h-1.5 rounded-full bg-lane" />
              Day Breakdown
            </h2>
            {(summary?.daily ?? []).length === 0 ? (
              <div className="flex-1 flex items-center justify-center font-code text-[11px] text-pip">
                No days with records in this range
              </div>
            ) : (
              <div className="flex flex-col divide-y divide-[#243048]/50 overflow-y-auto max-h-72">
                {[...(summary?.daily ?? [])].reverse().slice(0, 14).map((d) => (
                  <button
                    key={d.date}
                    onClick={() => applyDayFilter(d.date)}
                    className="flex items-center justify-between gap-3 py-2 text-left font-code text-[11px] hover:bg-lane/5 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-lane focus:outline-none"
                  >
                    <span className="text-chalk font-bold whitespace-nowrap w-16 shrink-0">{shortDate(d.date)}</span>
                    <span className="text-pip whitespace-nowrap">{d.first_time}-{d.last_time}</span>
                    <span className="text-amber-300 whitespace-nowrap">{d.peak_hour || '—'}</span>
                    <span className="text-lane font-bold whitespace-nowrap">{d.total.toLocaleString('en-US')}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Filter & Search Bar ──────────────────────────────────────────── */}
        <div className="p-4 rounded-xl bg-[#121929]/80 border border-[#243048] flex flex-wrap items-center gap-3 shadow-lg">
          {/* Preset Buttons */}
          <div className="flex items-center gap-1 bg-[#090d16] p-1 rounded-lg border border-[#243048]">
            {[
              { id: 'all', label: 'All Time' },
              { id: 'today', label: 'Today' },
              { id: 'yesterday', label: 'Yesterday' },
              { id: '7days', label: 'Last 7 Days' },
            ].map((p) => (
              <button
                key={p.id}
                onClick={() => applyPreset(p.id)}
                className={`px-3 py-1.5 rounded-md font-code text-xs font-medium transition-all cursor-pointer ${
                  activePreset === p.id
                    ? 'bg-lane text-asphalt font-bold shadow'
                    : 'text-pip hover:text-white'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="h-6 w-px bg-[#243048] hidden lg:block" />

          {/* Vehicle Type Filter */}
          <div className="flex items-center gap-1.5">
            <label className="font-code text-xs text-pip">Class:</label>
            <select
              value={vehicleType}
              onChange={(e) => {
                setVehicleType(e.target.value)
                setActivePreset('custom')
              }}
              className="bg-[#090d16] border border-[#243048] text-chalk font-code text-xs px-3 py-1.5 rounded-md focus:border-lane focus:outline-none cursor-pointer"
            >
              <option value="">All Classes</option>
              <option value="motorcycle">Motorcycle</option>
              <option value="car">Car</option>
              <option value="bus">Bus</option>
              <option value="truck">Truck</option>
            </select>
          </div>

          {/* Date Range Inputs */}
          <div className="flex items-center gap-2">
            <span className="font-code text-xs text-pip">Range:</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => {
                setDateFrom(e.target.value)
                setActivePreset('custom')
              }}
              className="bg-[#090d16] border border-[#243048] text-chalk font-code text-xs px-2.5 py-1.5 rounded-md focus:border-lane focus:outline-none [color-scheme:dark] cursor-pointer"
            />
            <span className="text-pip text-xs font-code">to</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => {
                setDateTo(e.target.value)
                setActivePreset('custom')
              }}
              className="bg-[#090d16] border border-[#243048] text-chalk font-code text-xs px-2.5 py-1.5 rounded-md focus:border-lane focus:outline-none [color-scheme:dark] cursor-pointer"
            />
          </div>

          {/* Delete a selected day directly from the calendar */}

          {/* Refresh / Reset */}
          <button
            onClick={() => {
              loadSummary()
              loadEvents()
              loadAllDaily()
            }}
            title="Reload data"
            className="ml-auto px-3 py-1.5 rounded-md font-code text-xs font-bold bg-[#1e293b] text-chalk hover:text-lane border border-[#243048] hover:border-lane/50 transition-colors cursor-pointer flex items-center gap-1.5"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>

        {/* ── Data Table Section ───────────────────────────────────────────── */}
        <div className="rounded-xl bg-[#121929]/80 border border-[#243048] overflow-hidden shadow-xl flex flex-col">
          {/* Table Header Bar */}
          <div className="px-5 py-3.5 border-b border-[#243048] flex items-center justify-between bg-[#0e1422]">
            <div className="font-code text-xs text-pip flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-lane" />
              Showing <span className="text-chalk font-bold">{events.items.length}</span> of{' '}
              <span className="text-lane font-bold">{events.total.toLocaleString('en-US')}</span> records
            </div>

            <div className="flex items-center gap-2">
              <span className="font-code text-xs text-pip">Rows:</span>
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
                className="bg-[#090d16] border border-[#243048] text-chalk font-code text-xs px-2 py-1 rounded focus:border-lane focus:outline-none cursor-pointer"
              >
                {PAGE_SIZE_OPTIONS.map((sz) => (
                  <option key={sz} value={sz}>{sz}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Table Body */}
          <div className="overflow-x-auto min-h-[360px]">
            {loading ? (
              <div className="flex flex-col items-center justify-center h-72 gap-3 text-pip">
                <div className="w-8 h-8 border-2 border-lane border-t-transparent rounded-full animate-spin" />
                <span className="font-code text-xs tracking-wider uppercase">Loading records from database…</span>
              </div>
            ) : events.items.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-72 gap-2 text-pip font-code text-xs">
                <svg className="w-12 h-12 text-[#243048]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <span>No records found matching current filter</span>
                <button
                  onClick={() => applyPreset('all')}
                  className="mt-2 text-lane hover:underline cursor-pointer"
                >
                  Reset all filters
                </button>
              </div>
            ) : (
              <table className="w-full text-left font-code text-xs border-collapse">
                <thead>
                  <tr className="bg-[#0e1422] text-pip uppercase tracking-wider text-[11px] border-b border-[#243048]">
                    <th className="py-3 px-4 w-16">#</th>
                    <th className="py-3 px-4">Crossing Time (WIB)</th>
                    <th className="py-3 px-4">Vehicle Classification</th>
                    <th className="py-3 px-4 text-center w-28">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#243048]/50">
                  {events.items.map((it, idx) => {
                    const rowNumber = (page - 1) * pageSize + idx + 1
                    const cfg = CLASS_CONFIG[it.vehicle_type] || {
                      label: it.vehicle_type,
                      color: '#22d3ee',
                      bg: 'rgba(34, 211, 238, 0.15)',
                      border: 'rgba(34, 211, 238, 0.4)',
                    }

                    return (
                      <tr
                        key={it.event_id || idx}
                        className="hover:bg-lane/5 transition-colors group"
                      >
                        <td className="py-3 px-4 text-pip font-mono">{rowNumber}</td>
                        <td className="py-3 px-4 text-chalk whitespace-nowrap font-medium">
                          {formatWib(it.timestamp_utc)}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold capitalize"
                            style={{
                              backgroundColor: cfg.bg,
                              color: cfg.color,
                              border: `1px solid ${cfg.border}`,
                            }}
                          >
                            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: cfg.color }} />
                            {cfg.label}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-center">
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                            RECORDED
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* ── Pagination Footer ────────────────────────────────────────── */}
          <div className="px-5 py-3.5 border-t border-[#243048] flex flex-wrap items-center justify-between gap-4 bg-[#0e1422]">
            <div className="font-code text-xs text-pip">
              Page <span className="text-chalk font-bold">{page}</span> of{' '}
              <span className="text-chalk font-bold">{totalPages}</span>
            </div>

            <div className="flex items-center gap-1.5 font-code text-xs">
              <button
                disabled={page <= 1}
                onClick={() => setPage(1)}
                className="px-2.5 py-1.5 rounded border border-[#243048] text-chalk hover:border-lane hover:text-lane disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
              >
                ⇤ First
              </button>
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="px-3 py-1.5 rounded border border-[#243048] text-chalk hover:border-lane hover:text-lane disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
              >
                ← Prev
              </button>

              <span className="px-3 py-1.5 rounded bg-lane/10 text-lane font-bold border border-lane/40">
                {page}
              </span>

              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="px-3 py-1.5 rounded border border-[#243048] text-chalk hover:border-lane hover:text-lane disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
              >
                Next →
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage(totalPages)}
                className="px-2.5 py-1.5 rounded border border-[#243048] text-chalk hover:border-lane hover:text-lane disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
              >
                Last ⇥
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
