/**
 * HistoryPanel — persistent per-day history chart.
 * Donut counts are filtered to TODAY (WIB) and reset to 0 at each midnight
 * rollover; historical totals live in the Database viewer window.
 */
import { useEffect, useState, useCallback } from 'react'
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Sector,
} from 'recharts'

const CLASS_COLORS = {
  motorcycle: '#FF6B35', // hazard orange
  car:        '#FFD100', // lane yellow
  bus:        '#EDEDEA', // chalk white
  truck:      '#9ca3af', // concrete grey
}

const CLASS_LABELS = {
  motorcycle: 'Motorcycle',
  car:        'Car',
  bus:        'Bus',
  truck:      'Truck',
}

const FALLBACK_COLORS = ['#22d3ee', '#A78BFA', '#34d399', '#F472B6']
const API = '/api'

/* WIB calendar date (YYYY-MM-DD) n real days ago */
function wibDateNdaysAgo(n = 0) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Jakarta',
  }).format(new Date(Date.now() - n * 86400000))
}

const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
function shortDate(ymd) {
  if (!ymd) return '—'
  const [y, m, d] = ymd.split('-').map(Number)
  if (!y || !m || !d) return ymd
  return `${d} ${_MONTHS[m - 1]}`
}

function formatPeakHourRange(rawHour) {
  if (!rawHour) return '—'
  let h = -1
  if (rawHour.includes(' ') || rawHour.includes('T')) {
    const timePart = rawHour.split(/[ T]/)[1] || ''
    h = parseInt(timePart.slice(0, 2), 10)
  } else if (/^\d+$/.test(rawHour)) {
    h = parseInt(rawHour, 10)
  }
  if (isNaN(h) || h < 0 || h > 23) return rawHour
  const nextH = (h + 1) % 24
  return `${String(h).padStart(2, '0')}.00-${String(nextH).padStart(2, '0')}.00`
}

// High-contrast custom active shape for hover / click
const renderActiveShape = (props) => {
  const {
    cx, cy, innerRadius, outerRadius, startAngle, endAngle,
    fill,
  } = props

  return (
    <g>
      {/* Expanded outer ring */}
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius - 2}
        outerRadius={outerRadius + 6}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
        style={{ filter: 'drop-shadow(0px 0px 6px rgba(255, 209, 0, 0.4))' }}
      />
      {/* Inner highlight ring */}
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius - 4}
        outerRadius={innerRadius - 2}
        startAngle={startAngle}
        endAngle={endAngle}
        fill="#FFFFFF"
      />
    </g>
  )
}

export default function HistoryPanel({ refreshKey }) {
  // Reporting day = TODAY (WIB). Every re-render recomputes it, so the first
  // refresh after midnight (<=30s, driven by refreshKey) naturally resets the
  // filter to the new day and today's counters read 0 again.
  const todayKey = wibDateNdaysAgo(0)
  const [summary, setSummary] = useState(null)
  const [activeIndex, setActiveIndex] = useState(null)

  const loadSummary = useCallback(async () => {
    const qs = new URLSearchParams()
    qs.set('date_from', todayKey)
    qs.set('date_to', todayKey)
    try {
      const res = await fetch(`${API}/analytics/history/summary?${qs}`)
      if (res.ok) setSummary(await res.json())
    } catch {
      /* backend offline */
    }
  }, [todayKey])

  useEffect(() => {
    loadSummary()
  }, [loadSummary, refreshKey])

  const openDatabaseWindow = () => {
    window.open('?view=database', '_blank', 'width=1280,height=820,menubar=no,toolbar=no')
  }

  // Prepare circular donut data from today's summary
  const rawClassCounts = summary?.count_per_class ?? {}
  const donutData = Object.entries(rawClassCounts).map(([name, value]) => ({
    name,
    label: CLASS_LABELS[name] || name,
    value: Number(value) || 0,
  }))
  const totalDonut = donutData.reduce((s, d) => s + d.value, 0)
  const activeItem = activeIndex !== null ? donutData[activeIndex] : null

  return (
    <div className="panel relative p-4 flex flex-col gap-4 overflow-hidden rounded-xl bg-asphalt-deep/85 border border-lane-dim/50 shadow-xl">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />

      {/* Header: peak hour badge + database window action */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <h3 className="road-label text-sm">Persistent History</h3>
          {summary?.peak_hour && (
            <span className="font-code text-[11px] bg-amber-500/20 text-amber-300 border border-amber-500/40 px-2.5 py-0.5 rounded">
              ⭐ Peak Hour: <span className="font-bold">{formatPeakHourRange(summary.peak_hour)}</span> ({summary.peak_count} vehicles)
            </span>
          )}
        </div>

        <button
          onClick={openDatabaseWindow}
          className="px-3.5 py-1.5 rounded bg-lane text-asphalt hover:bg-yellow-400 font-code text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 shadow-glowlane tracking-wider"
        >
          <span>DATABASE</span>
          <span>↗</span>
        </button>
      </div>

      {/* ── Circular Donut Chart for Persistent History ─────────────────────── */}
      <div className="bg-[#0b101c]/90 border border-[#243048] rounded-xl p-4 shadow-inner flex flex-col md:flex-row items-center gap-6">
        {donutData.length === 0 || totalDonut === 0 ? (
          <div className="flex items-center justify-center w-full h-44 text-pip font-code text-xs tracking-widest uppercase">
            No vehicles recorded today ({shortDate(todayKey)})
          </div>
        ) : (
          <>
            {/* Round Donut Chart with Dynamic Center HUD */}
            <div className="relative w-[200px] h-[200px] shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    activeIndex={activeIndex !== null ? activeIndex : undefined}
                    activeShape={renderActiveShape}
                    data={donutData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={88}
                    paddingAngle={3}
                    dataKey="value"
                    isAnimationActive={false}
                    onMouseEnter={(_, index) => setActiveIndex(index)}
                    onMouseLeave={() => setActiveIndex(null)}
                    onClick={(_, index) => setActiveIndex(index === activeIndex ? null : index)}
                    cursor="pointer"
                    stroke="#101012"
                    strokeWidth={2}
                  >
                    {donutData.map((entry, i) => (
                      <Cell
                        key={entry.name}
                        fill={CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
                      />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>

              {/* Dynamic Center Label */}
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                {activeItem ? (
                  <>
                    <span className="font-code text-[11px] uppercase tracking-wider text-chalk font-semibold truncate max-w-[85px]">
                      {activeItem.label}
                    </span>
                    <span className="font-code text-2xl font-bold text-lane leading-tight">
                      {activeItem.value.toLocaleString('id-ID')}
                    </span>
                    <span className="font-code text-xs text-chalk/90 font-medium">
                      {totalDonut ? ((activeItem.value / totalDonut) * 100).toFixed(1) : 0}%
                    </span>
                  </>
                ) : (
                  <>
                    <span className="font-code text-[10px] text-pip uppercase tracking-widest">
                      TODAY
                    </span>
                    <span className="font-code text-2xl font-extrabold text-lane leading-tight">
                      {totalDonut.toLocaleString('en-US')}
                    </span>
                    <span className="font-code text-[10px] text-pip uppercase tracking-wider">
                      VEHICLES
                    </span>
                  </>
                )}
              </div>
            </div>

            {/* Class Breakdown Details Grid */}
            <div className="flex-1 w-full grid grid-cols-2 sm:grid-cols-2 gap-3 font-code">
              {donutData.map((item, idx) => {
                const pct = totalDonut > 0 ? ((item.value / totalDonut) * 100).toFixed(1) : '0'
                const isSelected = activeIndex === idx
                return (
                  <div
                    key={item.name}
                    onMouseEnter={() => setActiveIndex(idx)}
                    onMouseLeave={() => setActiveIndex(null)}
                    onClick={() => setActiveIndex(isSelected ? null : idx)}
                    className={`p-2.5 rounded-lg border transition-all cursor-pointer flex flex-col gap-1 ${
                      isSelected
                        ? 'bg-lane/10 border-lane shadow-md'
                        : 'bg-[#121929] border-[#243048] hover:border-lane/40'
                    }`}
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="flex items-center gap-1.5 text-chalk font-semibold">
                        <span
                          className="w-2.5 h-2.5 rounded-sm"
                          style={{ backgroundColor: CLASS_COLORS[item.name] ?? '#22d3ee' }}
                        />
                        {item.label}
                      </span>
                      <span className="text-lane font-bold">{pct}%</span>
                    </div>
                    <div className="text-sm font-bold text-chalk">
                      {item.value.toLocaleString('en-US')}{' '}
                      <span className="text-[10px] font-normal text-pip">units</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
