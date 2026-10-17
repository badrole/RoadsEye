/**
 * ClassBreakdown — pie / donut chart + legend showing vehicle class split.
 */
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

const CLASS_COLORS = {
  car:        '#3b82f6',
  bus:        '#f59e0b',
  truck:      '#10b981',
  motorcycle: '#ef4444',
}

const FALLBACK_COLORS = ['#8b5cf6', '#06b6d4', '#f97316', '#84cc16']

export default function ClassBreakdown({ latest }) {
  const raw   = latest?.count_per_class ?? {}
  const data  = Object.entries(raw).map(([name, value]) => ({ name, value }))
  const total = data.reduce((s, d) => s + d.value, 0)

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Class Breakdown</h3>

      {data.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-gray-500 text-sm">
          No detections yet
        </div>
      ) : (
        <div className="flex items-center gap-4">
          <ResponsiveContainer width="50%" height={160}>
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={70}
                paddingAngle={3}
                dataKey="value"
                isAnimationActive={false}
              >
                {data.map((entry, i) => (
                  <Cell
                    key={entry.name}
                    fill={CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                formatter={(v, name) => [`${v} (${total ? ((v / total) * 100).toFixed(1) : 0}%)`, name]}
              />
            </PieChart>
          </ResponsiveContainer>

          {/* Legend */}
          <div className="flex flex-col gap-2 text-sm">
            {data.map((entry, i) => (
              <div key={entry.name} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-full flex-shrink-0"
                  style={{ backgroundColor: CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length] }}
                />
                <span className="text-gray-300 capitalize">{entry.name}</span>
                <span className="ml-auto text-gray-400 font-mono">{entry.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
