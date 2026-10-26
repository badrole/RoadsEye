/**
 * VehicleTimestampFeed — displays real-time timestamps, tracker IDs (#id),
 * vehicle types, counting mode, and live STOPPED FOR ... SEC duration for
 * vehicles with speed < 5 km/h.
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

export default function VehicleTimestampFeed({ latest, countMode = 'line' }) {
  const [feed, setFeed] = useState([])
  const containerRef = useRef(null)

  useEffect(() => {
    if (!latest) return
    const crossings = Array.isArray(latest.crossings) ? latest.crossings : []
    const tracks = Array.isArray(latest.tracks) ? latest.tracks : []
    const frameTs = latest.timestamp || Date.now() / 1000

    const liveTrackMap = new Map()
    for (const t of tracks) {
      if (t.id != null) {
        const isStopped = t.speed_kmh != null && t.speed_kmh < 5 && (t.stopped_sec ?? 0) >= 1
        liveTrackMap.set(t.id, {
          class_name: t.class_name,
          speed_kmh: t.speed_kmh,
          stopped_sec: isStopped ? t.stopped_sec : 0,
          isStopped,
        })
      }
    }

    if (crossings.length === 0 && liveTrackMap.size === 0) return

    setFeed((prev) => {
      let updated = prev.map((item) => {
        const live = item.track_id != null ? liveTrackMap.get(item.track_id) : null
        if (!live) {
          return item.currently_stopped ? { ...item, currently_stopped: false } : item
        }
        return {
          ...item,
          class_name: live.class_name || item.class_name,
          stopped_sec: live.isStopped
            ? Math.max(item.stopped_sec || 0, live.stopped_sec)
            : item.stopped_sec || 0,
          currently_stopped: live.isStopped,
        }
      })

      const existingIds = new Set(updated.map((it) => it.track_id).filter((id) => id != null))

      const newCrossingItems = []
      for (let i = 0; i < crossings.length; i++) {
        const c = crossings[i]
        const live = c.track_id != null ? liveTrackMap.get(c.track_id) : null
        if (c.track_id != null && existingIds.has(c.track_id)) {
          updated = updated.map((it) =>
            it.track_id === c.track_id
              ? {
                  ...it,
                  timestamp: c.timestamp || it.timestamp,
                  class_name: live?.class_name || c.class_name || it.class_name,
                  line_name: c.line_name || (countMode === 'id' ? 'ID' : 'line1'),
                }
              : it
          )
          continue
        }
        if (c.track_id != null) existingIds.add(c.track_id)
        newCrossingItems.push({
          id: `${c.timestamp}-${c.track_id ?? i}-${Math.random()}`,
          timestamp: c.timestamp || frameTs,
          class_name: live?.class_name || c.class_name || 'vehicle',
          track_id: c.track_id,
          line_name: c.line_name || (countMode === 'id' ? 'ID' : 'line1'),
          stopped_sec: live?.isStopped ? live.stopped_sec : 0,
          currently_stopped: Boolean(live?.isStopped),
        })
      }

      const newStoppedItems = []
      for (const [tid, live] of liveTrackMap.entries()) {
        if (live.isStopped && !existingIds.has(tid)) {
          existingIds.add(tid)
          newStoppedItems.push({
            id: `${frameTs}-${tid}-stopped`,
            timestamp: frameTs,
            class_name: live.class_name || 'vehicle',
            track_id: tid,
            line_name: countMode === 'id' ? 'ID' : 'IN FRAME',
            stopped_sec: live.stopped_sec,
            currently_stopped: true,
          })
        }
      }

      return [...newCrossingItems, ...newStoppedItems, ...updated].slice(0, 50)
    })
  }, [latest, countMode])

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
            <span className="tracking-widest uppercase">
              {countMode === 'id'
                ? 'Waiting for vehicles in frame (Per ID mode)...'
                : 'Waiting for vehicles to cross counting line...'}
            </span>
          </div>
        ) : (
          feed.map((item, idx) => {
            const color = CLASS_COLORS[item.class_name] ?? '#22d3ee'
            const label = CLASS_LABELS[item.class_name] ?? item.class_name
            const isLatest = idx === 0
            const stoppedSec = item.stopped_sec ? Math.max(1, Math.floor(item.stopped_sec)) : 0

            return (
              <div
                key={item.id}
                className={`flex items-center justify-between p-2 rounded border transition-all ${
                  isLatest
                    ? 'bg-lane-wash/40 border-lane/60 text-chalk'
                    : 'bg-asphalt-deep/60 border-lane-dim/20 text-chalk/80'
                }`}
              >
                <div className="flex items-center gap-3 flex-wrap">
                  <span
                    className="w-2.5 h-2.5 rounded-sm shrink-0"
                    style={{ backgroundColor: color }}
                  />
                  <div className="font-code text-xs font-semibold flex items-center gap-2 flex-wrap">
                    {item.track_id != null && (
                      <span className="text-[11px] font-bold text-lane bg-asphalt-deep border border-lane-dim/50 px-1.5 py-0.5 rounded">
                        #{item.track_id}
                      </span>
                    )}
                    <span className="capitalize">{label}</span>
                    <span className="text-[10px] text-pip border border-lane-dim/30 px-1.5 py-0.5 rounded uppercase">
                      {item.line_name}
                    </span>
                    {stoppedSec >= 1 && (
                      <span
                        className={`text-[10px] font-bold px-1.5 py-0.5 rounded border tracking-wider ${
                          item.currently_stopped
                            ? 'bg-stop/25 text-stop border-stop/60 animate-pulse'
                            : 'bg-stop/15 text-stop/90 border-stop/40'
                        }`}
                      >
                        STOPPED FOR {stoppedSec} SEC
                      </span>
                    )}
                    {isLatest && (
                      <span className="text-[10px] bg-go/20 text-go px-1.5 py-0.2 rounded border border-go/40">
                        NEW
                      </span>
                    )}
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
