/**
 * SpeedDistribution — bar chart showing the distribution of speed samples
 * from the most recent metrics frame.
 */
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

const BINS  = [0, 20, 40, 60, 80, 100, 120]  // km/h bucket edges

function buildBins(samples) {
  const counts = Array(BINS.length - 1).fill(0)
  for (const s of samples) {
    for (let i = 0; i < BINS.length - 1; i++) {
      if (s >= BINS[i] && s < BINS[i + 1]) { counts[i]++; break }
    }
  }
  return counts.map((c, i) => ({ range: `${BINS[i]}–${BINS[i + 1]}`, count: c }))
}

export default function SpeedDistribution({ history }) {
  // Collect speed samples from the last 30 frames
  const samples = history.slice(-30).flatMap(m => m.speed_samples ?? [])
  const data    = buildBins(samples)

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Speed Distribution (km/h)</h3>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="range" tick={{ fill: '#9ca3af', fontSize: 10 }} />
          <YAxis allowDecimals={false} tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
            formatter={(v) => [v, 'vehicles']}
          />
          <Bar dataKey="count" fill="#f59e0b" radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
