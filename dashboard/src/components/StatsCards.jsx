/**
 * StatsCards — KPI summary row: total vehicles, in-frame count, avg speed, uptime.
 */
export default function StatsCards({ latest, pipelineStatus }) {
  const cards = [
    {
      label: 'Total Detected',
      value: latest?.total_count ?? '—',
      unit: 'vehicles',
      icon: '🚗',
      color: 'text-blue-400',
    },
    {
      label: 'In Frame',
      value: latest?.vehicles_in_frame ?? '—',
      unit: 'active',
      icon: '📍',
      color: 'text-emerald-400',
    },
    {
      label: 'Avg Speed',
      value: latest?.avg_speed_kmh != null
        ? latest.avg_speed_kmh.toFixed(1)
        : '—',
      unit: 'km/h',
      icon: '⚡',
      color: 'text-yellow-400',
    },
    {
      label: 'Uptime',
      value: pipelineStatus?.uptime_seconds != null
        ? formatUptime(pipelineStatus.uptime_seconds)
        : '—',
      unit: '',
      icon: '🕒',
      color: 'text-purple-400',
    },
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div
          key={c.label}
          className="bg-gray-800 border border-gray-700 rounded-xl p-4 flex flex-col gap-1"
        >
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <span>{c.icon}</span>
            <span>{c.label}</span>
          </div>
          <div className={`text-3xl font-bold tracking-tight ${c.color}`}>
            {c.value}
          </div>
          {c.unit && (
            <div className="text-xs text-gray-500">{c.unit}</div>
          )}
        </div>
      ))}
    </div>
  )
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}
