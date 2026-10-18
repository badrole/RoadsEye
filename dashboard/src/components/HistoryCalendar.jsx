/**
 * HistoryCalendar — month calendar of recorded days.
 * Days that have persisted events are circled in lane yellow with their
 * per-day total printed under the circle. Click a recorded day to filter
 * the database table to that date. Month arrows navigate real months.
 */
import { useMemo, useState } from 'react'

const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']
const _DOW = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

const _pad = (n) => String(n).padStart(2, '0')

function _wibToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Jakarta' }).format(new Date())
}

export default function HistoryCalendar({ daily = [], selectedDate = '', onSelect, onDelete }) {
  const [viewMonth, setViewMonth] = useState(() => _wibToday().slice(0, 7)) // YYYY-MM
  const totalsByDate = useMemo(() => {
    const map = {}
    for (const d of daily) map[d.date] = d.total ?? 0
    return map
  }, [daily])

  const year = Number(viewMonth.slice(0, 4))
  const month = Number(viewMonth.slice(5, 7))
  const firstDow = new Date(year, month - 1, 1).getDay()
  const daysInMonth = new Date(year, month, 0).getDate()
  const todayKey = _wibToday()

  const shiftMonth = (delta) => {
    const d = new Date(year, month - 1 + delta, 1)
    setViewMonth(`${d.getFullYear()}-${_pad(d.getMonth() + 1)}`)
  }

  /* Trailing blanks so the grid still renders full weeks */
  const leading = Array.from({ length: firstDow }, (_, i) => i)
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1)
  const selectedHasData = Boolean(selectedDate && (totalsByDate[selectedDate] ?? 0) > 0)

  return (
    <div className="flex flex-col gap-2.5">
      {/* Month navigation + delete action for the selected day */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => shiftMonth(-1)}
          aria-label="Previous month"
          className="w-9 h-9 rounded-md font-code text-chalk hover:text-lane border border-[#243048] hover:border-lane/50 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-lane focus:outline-none"
        >
          ‹
        </button>
        <span className="font-code text-xs font-bold text-chalk tracking-wider uppercase">
          {_MONTHS[month - 1]} {year} <span className="text-pip font-normal">· WIB</span>
        </span>
        <div className="flex items-center gap-1.5">
          {selectedHasData && (
            <button
              onClick={() => onDelete?.(selectedDate)}
              aria-label={`Delete history for ${selectedDate}`}
              title={`Delete all history for ${selectedDate} (database + Google Sheet)`}
              className="h-9 px-3 rounded-md font-code text-[11px] font-bold tracking-wider uppercase text-stop border border-stop/50 bg-stop/10 hover:bg-stop hover:text-white transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-stop focus:outline-none flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              <span className="hidden sm:inline">Delete</span>
            </button>
          )}
          <button
            onClick={() => shiftMonth(1)}
            aria-label="Next month"
            className="w-9 h-9 rounded-md font-code text-chalk hover:text-lane border border-[#243048] hover:border-lane/50 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-lane focus:outline-none"
          >
            ›
          </button>
        </div>
      </div>

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-1">
        {_DOW.map((dw) => (
          <span key={dw} className="text-center font-code text-[10px] text-pip uppercase tracking-wider py-1">
            {dw}
          </span>
        ))}

        {leading.map((i) => <span key={`lead-${i}`} className="h-11" />)}

        {days.map((day) => {
          const dateKey = `${viewMonth}-${_pad(day)}`
          const total = totalsByDate[dateKey]
          const hasData = Number(total) > 0
          const isSelected = selectedDate === dateKey
          const isToday = dateKey === todayKey
          return (
            <button
              key={dateKey}
              onClick={() => hasData && onSelect?.(dateKey)}
              disabled={!hasData}
              title={hasData ? `${dateKey}: ${total.toLocaleString('en-US')} vehicles` : `${dateKey}: no records`}
              aria-pressed={isSelected}
              className={`h-11 rounded-lg flex flex-col items-center justify-center cursor-pointer transition-colors focus-visible:ring-2 focus-visible:ring-lane focus:outline-none ${
                isSelected
                  ? 'bg-lane/25 border-2 border-lane shadow-glowlane'
                  : hasData
                    ? 'border border-transparent hover:bg-lane/10'
                    : isToday
                      ? 'border border-lane-dim/30'
                      : 'border border-transparent'
              }`}
            >
              <span
                className={`w-7 h-7 rounded-full flex items-center justify-center font-code text-xs font-bold shrink-0 ${
                  isSelected && hasData
                    ? 'bg-lane text-asphalt'
                    : hasData
                      ? 'border-2 border-lane text-lane'
                      : isToday
                        ? 'border border-dashed border-lane-dim/50 text-pip'
                        : 'text-pip/40'
                }`}
              >
                {day}
              </span>
              {hasData ? (
                <span className="font-code text-[9px] text-lane leading-none mt-0.5">
                  {total > 999 ? `${(total / 1000).toFixed(1)}k` : total}
                </span>
              ) : (
                <span className="text-[9px] leading-none mt-0.5"> </span>
              )}
            </button>
          )
        })}
      </div>

      {daily.length === 0 && (
        <div className="text-center font-code text-[11px] text-pip py-3">
          No recorded days yet — tap a circled day to select it
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 font-code text-[10px] text-pip">
        <span className="flex items-center gap-1.5">
          <span className="w-4 h-4 rounded-full border-2 border-lane inline-flex items-center justify-center text-[8px] text-lane">.</span>
          day with records
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3.5 h-3.5 rounded-full bg-lane inline-block" />
          selected
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3.5 h-3.5 rounded-full border border-dashed border-lane-dim/50 inline-block" />
          today
        </span>
      </div>
    </div>
  )
}
