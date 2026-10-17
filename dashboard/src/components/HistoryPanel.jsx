/**
 * HistoryPanel — persistent event history (SQLite, Phase 1).
 *
 * Top: historical cumulative totals (survive refresh / stop / restart).
 * Bottom: paginated recent events with class + Jakarta-date filters,
 * and a one-click safe DB backup button.
 */
import { useEffect, useState, useCallback } from 'react'

const CLASS_COLORS = {
  car:        '#FFD100',
  bus:        '#EDEDEA',
  truck:      '#9ca3af',
  motorcycle: '#FF6B35',
}

const API = '/api'
const PAGE_SIZE = 10

/* UTC ISO → Asia/Jakarta local string */
function jakartaTime(isoUtc) {
  if (!isoUtc) return '—'
  try {
    return new Intl.DateTimeFormat('id-ID', {
      timeZone: 'Asia/Jakarta',
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    }).format(new Date(isoUtc))
  } catch { return isoUtc }
}

export default function HistoryPanel({ refreshKey, onBackupDone }) {
  const [summary, setSummary] = useState(null)
  const [events, setEvents]   = useState({ total: 0, items: [] })
  const [page, setPage]       = useState(1)
  const [vtype, setVtype]     = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo]     = useState('')
  const [backupMsg, setBackupMsg] = useState(null)

  const loadEvents = useCallback(async () => {
    const qs = new URLSearchParams()
    qs.set('page', String(page))
    qs.set('page_size', String(PAGE_SIZE))
    if (vtype)    qs.set('vehicle_type', vtype)
    if (dateFrom) qs.set('date_from', dateFrom)
    if (dateTo)   qs.set('date_to', dateTo)
    try {
      const res = await fetch(`${API}/analytics/events?${qs}`)
      if (res.ok) setEvents(await res.json())
    } catch { /* backend offline */ }
  }, [page, vtype, dateFrom, dateTo])

  const loadSummary = useCallback(async () => {
    const qs = new URLSearchParams()
    if (vtype)    qs.set('vehicle_type', vtype)
    if (dateFrom) qs.set('date_from', dateFrom)
    if (dateTo)   qs.set('date_to', dateTo)
    try {
      const res = await fetch(`${API}/analytics/history/summary?${qs}`)
      if (res.ok) setSummary(await res.json())
    } catch { /* backend offline */ }
  }, [vtype, dateFrom, dateTo])

  useEffect(() => { loadSummary(); loadEvents() }, [loadSummary, loadEvents, refreshKey])

  /* Filter change → reset page */
  useEffect(() => { setPage(1) }, [vtype, dateFrom, dateTo])

  const doBackup = async () => {
    setBackupMsg(null)
    try {
      const res = await fetch(`${API}/analytics/history/backup`, { method: 'POST' })
      if (res.ok) {
        const data = await res.json()
        setBackupMsg(`Backup OK → ${data.path}`)
      } else {
        setBackupMsg('Backup gagal')
      }
    } catch { setBackupMsg('Backup gagal') }
    onBackupDone?.()
  }

  const totalPages = Math.max(1, Math.ceil(events.total / PAGE_SIZE))

  return (
    <div className="panel relative p-4 overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <h3 className="road-label mt-2 mb-3">Riwayat Persisten (SQLite)</h3>

      {/* Historical totals — separate from live session counts */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mb-3">
        <div>
          <div className="road-label">Total Historis</div>
          <div className="font-code text-2xl font-bold text-lane leading-none">
            {summary?.total_events ?? '—'}
          </div>
        </div>
        {(summary ? Object.entries(summary.count_per_class) : []).map(([cls, n]) => (
          <div key={cls} className="flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 rounded-[2px]"
              style={{ backgroundColor: CLASS_COLORS[cls] ?? '#22d3ee' }}
            />
            <span className="font-code text-xs text-chalk capitalize">{cls}</span>
            <span className="font-code text-sm text-chalk">{n}</span>
          </div>
        ))}
        <div className="ml-auto font-code text-[10px] text-pip">
          {summary?.first_event_utc ? `sejak ${jakartaTime(summary.first_event_utc)} WIB` : 'belum ada data'}
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={vtype}
          onChange={(e) => setVtype(e.target.value)}
          className="bg-asphalt-deep border border-lane-dim/40 text-chalk font-code text-xs px-2 py-1"
        >
          <option value="">Semua kelas</option>
          <option value="car">mobil</option>
          <option value="motorcycle">motor</option>
          <option value="truck">truk</option>
          <option value="bus">bus</option>
        </select>
        <label className="font-code text-[10px] text-pip">dari</label>
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
          className="bg-asphalt-deep border border-lane-dim/40 text-chalk font-code text-xs px-2 py-1 [color-scheme:dark]" />
        <label className="font-code text-[10px] text-pip">sampai</label>
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
          className="bg-asphalt-deep border border-lane-dim/40 text-chalk font-code text-xs px-2 py-1 [color-scheme:dark]" />
        <button
          onClick={doBackup}
          className="ml-auto font-code text-[10px] tracking-widest uppercase text-lane border border-lane-dim/60 px-2.5 py-1 hover:bg-lane-wash"
        >
          Backup DB
        </button>
      </div>
      {backupMsg && (
        <div className="font-code text-[10px] text-go mb-2 break-all">{backupMsg}</div>
      )}

      {/* Recent events table */}
      {events.items.length === 0 ? (
        <div className="flex items-center justify-center h-16 text-pip font-code text-xs tracking-widest uppercase">
          Belum ada event tersimpan
        </div>
      ) : (
        <table className="w-full font-code text-xs">
          <thead>
            <tr className="text-pip text-left road-label">
              <th className="py-1 pr-2">Waktu (WIB)</th>
              <th className="py-1 pr-2">Kelas</th>
              <th className="py-1 pr-2">Dir</th>
              <th className="py-1 pr-2">Garis</th>
              <th className="py-1 pr-2">Track</th>
              <th className="py-1">Conf</th>
            </tr>
          </thead>
          <tbody>
            {events.items.map((ev) => (
              <tr key={ev.event_id} className="border-t border-lane-dim/20">
                <td className="py-1 pr-2 text-chalk whitespace-nowrap">{jakartaTime(ev.timestamp_utc)}</td>
                <td className="py-1 pr-2">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-[2px]"
                      style={{ backgroundColor: CLASS_COLORS[ev.vehicle_type] ?? '#22d3ee' }}
                    />
                    <span className="text-chalk capitalize">{ev.vehicle_type}</span>
                  </span>
                </td>
                <td className="py-1 pr-2 text-pip">{ev.direction ?? '—'}</td>
                <td className="py-1 pr-2 text-pip">{ev.counting_line_id}</td>
                <td className="py-1 pr-2 text-lane">#{ev.track_id}</td>
                <td className="py-1 text-pip">
                  {ev.confidence != null ? ev.confidence.toFixed(2) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Pagination */}
      <div className="flex items-center justify-between mt-3 font-code text-[11px]">
        <span className="text-pip">
          {events.total} event · hal {page}/{totalPages}
        </span>
        <div className="flex gap-2">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="text-lane disabled:text-pip hover:underline"
          >← prev</button>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            className="text-lane disabled:text-pip hover:underline"
          >next →</button>
        </div>
      </div>
    </div>
  )
}
