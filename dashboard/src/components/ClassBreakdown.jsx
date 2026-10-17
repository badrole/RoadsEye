/**
 * ClassBreakdown — pie / donut chart + legend showing vehicle class split.
 */
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

const CLASS_COLORS = {
  car:        '#FFD100',   // lane yellow
  bus:        '#EDEDEA',   // chalk white
  truck:      '#9ca3af',   // concrete grey
  motorcycle: '#FF6B35',   // hazard orange
}

const FALLBACK_COLORS = ['#22d3ee', '#A78BFA', '#34d399', '#F472B6']

export default function ClassBreakdown({ latest }) {
  const raw   = latest?.count_per_class ?? {}
  const data  = Object.entries(raw).map(([name, value]) => ({ name, value }))
  const total = data.reduce((s, d) => s + d.value, 0)

  return (
    <div className="panel relative p-4 overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <h3 className="road-label mt-2 mb-3">Komposisi Kelas</h3>

      {data.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-pip font-code text-xs tracking-widest uppercase">
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
                contentStyle={{ background: '#101012', border: '1px solid #5c4f00', borderRadius: 2 }}
                formatter={(v, name) => [`${v} (${total ? ((v / total) * 100).toFixed(1) : 0}%)`, name]}
              />
            </PieChart>
          </ResponsiveContainer>

          {/* Legend */}
          <div className="flex-1 flex flex-col gap-2 text-sm">
            {data.map((entry, i) => (
              <div key={entry.name} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-[2px] flex-shrink-0"
                  style={{ backgroundColor: CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length] }}
                />
                <span className="text-chalk capitalize font-code text-xs">{entry.name}</span>
                <span className="ml-auto font-code text-lane">{entry.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
