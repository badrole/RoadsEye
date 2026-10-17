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
    <div className="panel relative p-4 overflow-hidden">
      <div className="lane-stripe-h absolute top-0 left-0 right-0 opacity-80" />
      <h3 className="road-label mt-2 mb-3">Distribusi Kecepatan (km/h)</h3>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2e" />
          <XAxis dataKey="range" tick={{ fill: '#8f8f8a', fontSize: 10 }} />
          <YAxis allowDecimals={false} tick={{ fill: '#8f8f8a', fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: '#101012', border: '1px solid #5c4f00', borderRadius: 2 }}
            formatter={(v) => [v, 'vehicles']}
          />
          <Bar dataKey="count" fill="#FFD100" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
