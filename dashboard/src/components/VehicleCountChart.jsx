/**
 * VehicleCountChart — vehicles counted per minute over the last 30 minutes.
 * Horizontal axis = time (HH:MM, WIB), vertical axis = vehicles.
 */
import { memo } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

function VehicleCountChart({ data = [] }) {
  const hasAny = data.some((d) => d.vehicles > 0)

  return (
    <div className="panel relative p-4 overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <div className="flex items-center justify-between mt-2 mb-3">
        <h3 className="road-label">Vehicles per Minute</h3>
        <span className="font-code text-[10px] text-pip tracking-wider">LAST 30 MIN · WIB</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -22, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2e" />
          <XAxis
            dataKey="minute"
            interval={4}
            tick={{ fill: '#8f8f8a', fontSize: 10 }}
            tickLine={false}
          />
          <YAxis allowDecimals={false} tick={{ fill: '#8f8f8a', fontSize: 11 }} />
          <Tooltip
            cursor={{ fill: 'rgba(255, 209, 0, 0.08)' }}
            contentStyle={{ background: '#101012', border: '1px solid #5c4f00', borderRadius: 2 }}
            labelFormatter={(l) => `${l} WIB`}
            formatter={(v) => [v, 'vehicles']}
          />
          <Bar
            dataKey="vehicles"
            fill="#FFD100"
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
      {!hasAny && (
        <div className="absolute inset-x-0 top-[52%] -translate-y-1/2 flex justify-center pointer-events-none">
          <span className="font-code text-[11px] text-pip tracking-widest uppercase bg-asphalt-deep/80 border border-lane-dim/30 px-3 py-1.5 rounded">
            No vehicles counted yet
          </span>
        </div>
      )}
    </div>
  )
}

export default memo(VehicleCountChart)
