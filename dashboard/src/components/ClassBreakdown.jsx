/**
 * ClassBreakdown — interactive round donut chart with high-contrast tooltip
 * and center statistics indicator.
 */
import { useState } from 'react'
import { PieChart, Pie, Cell, ResponsiveContainer, Sector } from 'recharts'

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

const FALLBACK_COLORS = ['#22d3ee', '#A78BFA', '#34d399', '#F472B6']

// High-contrast custom active shape for hover / click
const renderActiveShape = (props) => {
  const {
    cx, cy, innerRadius, outerRadius, startAngle, endAngle,
    fill, payload, value
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

export default function ClassBreakdown({ latest }) {
  const [activeIndex, setActiveIndex] = useState(null)

  const raw   = latest?.count_per_class ?? {}
  const data  = Object.entries(raw).map(([name, value]) => ({
    name,
    label: CLASS_LABELS[name] || name,
    value: Number(value) || 0,
  }))
  const total = data.reduce((s, d) => s + d.value, 0)

  const activeItem = activeIndex !== null ? data[activeIndex] : null

  const onPieEnter = (_, index) => {
    setActiveIndex(index)
  }

  const onPieLeave = () => {
    setActiveIndex(null)
  }

  return (
    <div className="panel relative p-4 overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <div className="flex items-center justify-between mt-2 mb-3">
        <h3 className="road-label">Class Breakdown</h3>
        {total > 0 && (
          <span className="font-code text-[11px] text-lane bg-asphalt-deep border border-lane-dim/50 px-2 py-0.5 rounded">
            {total} Total
          </span>
        )}
      </div>

      {data.length === 0 || total === 0 ? (
        <div className="flex items-center justify-center h-44 text-pip font-code text-xs tracking-widest uppercase">
          No vehicles detected yet
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {/* Donut Chart with Center Stats Indicator */}
          <div className="relative w-full h-[180px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  activeIndex={activeIndex !== null ? activeIndex : undefined}
                  activeShape={renderActiveShape}
                  data={data}
                  cx="50%"
                  cy="50%"
                  innerRadius={52}
                  outerRadius={76}
                  paddingAngle={3}
                  dataKey="value"
                  isAnimationActive={false}
                  onMouseEnter={onPieEnter}
                  onMouseLeave={onPieLeave}
                  onClick={(_, index) => setActiveIndex(index === activeIndex ? null : index)}
                  cursor="pointer"
                  stroke="#101012"
                  strokeWidth={2}
                >
                  {data.map((entry, i) => (
                    <Cell
                      key={entry.name}
                      fill={CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
                    />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>

            {/* Center Dynamic Label (Interactive HUD) */}
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              {activeItem ? (
                <>
                  <span className="font-code text-[10px] uppercase tracking-wider text-chalk font-semibold truncate max-w-[80px]">
                    {activeItem.label}
                  </span>
                  <span className="font-code text-xl font-bold text-lane leading-tight">
                    {activeItem.value}
                  </span>
                  <span className="font-code text-[11px] text-chalk/90 font-medium">
                    {total ? ((activeItem.value / total) * 100).toFixed(1) : 0}%
                  </span>
                </>
              ) : (
                <>
                  <span className="font-code text-[10px] text-pip uppercase tracking-widest">
                    TOTAL
                  </span>
                  <span className="font-code text-2xl font-bold text-lane leading-tight">
                    {total}
                  </span>
                  <span className="font-code text-[10px] text-chalk/60">
                    Vehicles
                  </span>
                </>
              )}
            </div>
          </div>

          {/* Interactive Legend with Progress Bars */}
          <div className="flex flex-col gap-2 pt-1 border-t border-lane-dim/30">
            {data.map((entry, i) => {
              const color = CLASS_COLORS[entry.name] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]
              const pct = total ? ((entry.value / total) * 100).toFixed(1) : 0
              const isSelected = activeIndex === i

              return (
                <div
                  key={entry.name}
                  onMouseEnter={() => setActiveIndex(i)}
                  onMouseLeave={() => setActiveIndex(null)}
                  onClick={() => setActiveIndex(isSelected ? null : i)}
                  className={`p-1.5 rounded transition-all cursor-pointer flex flex-col gap-1 ${
                    isSelected
                      ? 'bg-lane-wash/50 border border-lane/60'
                      : 'hover:bg-asphalt-deep/80 border border-transparent'
                  }`}
                >
                  <div className="flex items-center justify-between text-xs font-code">
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
                        style={{ backgroundColor: color }}
                      />
                      <span className="text-chalk font-medium capitalize">
                        {entry.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-lane font-bold">{entry.value}</span>
                      <span className="text-pip text-[11px] w-12 text-right">({pct}%)</span>
                    </div>
                  </div>

                  {/* Horizontal mini progress bar */}
                  <div className="w-full h-1 bg-asphalt-deep rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: color,
                      }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
