/**
 * VehicleCountChart — area chart of vehicles-in-frame over the last 60 frames.
 */
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

export default function VehicleCountChart({ history }) {
  const data = history.map((m, i) => ({
    t: i,
    count: m.vehicles_in_frame ?? 0,
  }))

  return (
        <div className="panel relative p-4 overflow-hidden">
          <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
          <h3 className="road-label mt-2 mb-3">Kendaraan Di Frame</h3>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="countGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#FFD100" stopOpacity={0.45} />
                  <stop offset="95%" stopColor="#FFD100" stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2e" />
              <XAxis dataKey="t" hide />
              <YAxis allowDecimals={false} tick={{ fill: '#8f8f8a', fontSize: 11 }} />
              <Tooltip
                contentStyle={{ background: '#101012', border: '1px solid #5c4f00', borderRadius: 2 }}
                labelFormatter={() => ''}
                formatter={(v) => [v, 'vehicles']}
              />
              <Area
                type="monotone"
                dataKey="count"
                stroke="#FFD100"
                strokeWidth={2}
                fill="url(#countGrad)"
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
  )
}
