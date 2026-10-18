/**
 * VehicleTimestampFeed — displays real-time timestamps of vehicles crossing the detection line.
 * Replaces the old anomaly alerts panel with a clean live timestamp event feed.
 */
import { useState, useEffect, useRef } from 'react'

const CLASS_COLORS = {
  car:        '#FFD100',   // lane yellow
  bus:        '#EDEDEA',   // chalk white
  truck:      '#9ca3af',   // concrete grey
  motorcycle: '#FF6B35',   // hazard orange
}

const CLASS_LABELS = {
  car:        'Car',
  bus:        'Bus',
  truck:      'Truck',
  motorcycle: 'Motorcycle',
}

function formatWibTimestamp(epochSec) {
  if (!epochSec) return '—'
  try {
    const d = new Date(epochSec * 1000)
    const timeStr = d.toLocaleTimeString('en-US', {
      timeZone: 'Asia/Jakarta',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
    const ms = String(d.getMilliseconds()).padStart(3, '0')
    const dateStr = d.toLocaleDateString('en-US', {
      timeZone: 'Asia/Jakarta',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
    return `${dateStr} ${timeStr}.${ms} WIB`
  } catch {
    return String(epochSec)
  }
}

export default function VehicleTimestampFeed({ latest }) {
  const [feed, setFeed] = useState([])
  const containerRef = useRef(null)

  useEffect(() => {
    const crossings = latest?.crossings
    if (crossings && Array.isArray(crossings) && crossings.length > 0) {
      setFeed((prev) => {
        const newItems = crossings.map((c, i) => ({
          id: `${c.timestamp}-${c.track_id ?? i}-${Math.random()}`,
          timestamp: c.timestamp,
          class_name: c.class_name || 'vehicle',
          track_id: c.track_id,
        }))
        // Prepend newest items, keep max 50
        return [...newItems, ...prev].slice(0, 50)
      })
    }
  }, [latest?.crossings])

  return (
    <div className="panel relative p-4 flex flex-col overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <div className="flex items-center justify-between mt-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-go animate-ping" />
          <h3 className="road-label">Live Vehicle Timestamp Feed</h3>
        </div>
        <span className="font-code text-xs text-lane border border-lane-dim/50 px-2 py-0.5 rounded">
          {feed.length} Detected
        </span>
      </div>

      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto max-h-56 flex flex-col gap-2 pr-1"
      >
        {feed.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-pip font-code text-xs">
            <span className="w-6 h-6 border-2 border-lane-dim border-t-lane rounded-full animate-spin mb-2" />
            <span className="tracking-widest uppercase">Waiting for vehicles to cross counting line...</span>
          </div>
        ) : (
          feed.map((item, idx) => {
            const color = CLASS_COLORS[item.class_name] ?? '#22d3ee'
            const label = CLASS_LABELS[item.class_name] ?? item.class_name
            const isLatest = idx === 0

            return (
              <div
                key={item.id}
                className={`flex items-center justify-between p-2 rounded border transition-all ${
                  isLatest
                    ? 'bg-lane-wash/40 border-lane/60 text-chalk'
                    : 'bg-asphalt-deep/60 border-lane-dim/20 text-chalk/80'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span
                    className="w-2.5 h-2.5 rounded-sm shrink-0"
                    style={{ backgroundColor: color }}
                  />
                  <div>
                    <div className="font-code text-xs font-semibold flex items-center gap-2">
                      <span className="capitalize">{label}</span>
                      {isLatest && (
                        <span className="text-[10px] bg-go/20 text-go px-1.5 py-0.2 rounded border border-go/40">
                          NEW
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="font-code text-xs text-lane tracking-wider font-medium">
                  {formatWibTimestamp(item.timestamp)}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
