/**
 * AlertsPanel — scrollable list of anomaly alerts from the pipeline.
 * Alerts arrive in MetricsMessage.alerts as plain strings.
 */
import { useRef, useEffect } from 'react'

const SEVERITY_CLASSES = {
  HIGH:   'border-red-500 bg-red-900/20 text-red-300',
  MEDIUM: 'border-yellow-500 bg-yellow-900/20 text-yellow-300',
  LOW:    'border-blue-500 bg-blue-900/20 text-blue-300',
}

function parseSeverity(text) {
  const upper = text.toUpperCase()
  if (upper.includes('HIGH') || upper.includes('STOPPED') || upper.includes('SPIKE')) return 'HIGH'
  if (upper.includes('MEDIUM') || upper.includes('SLOW')) return 'MEDIUM'
  return 'LOW'
}

export default function AlertsPanel({ alerts }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [alerts.length])

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300">Alerts</h3>
        {alerts.length > 0 && (
          <span className="text-xs bg-red-600 text-white rounded-full px-2 py-0.5">
            {alerts.length}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto max-h-48 flex flex-col gap-2 pr-1">
        {alerts.length === 0 ? (
          <div className="text-gray-500 text-sm text-center py-6">No alerts</div>
        ) : (
          alerts.map((a, i) => {
            const sev = parseSeverity(typeof a === 'string' ? a : a.message ?? '')
            const text = typeof a === 'string' ? a : a.message
            return (
              <div
                key={i}
                className={`border-l-2 pl-3 py-1.5 rounded-r text-xs ${SEVERITY_CLASSES[sev]}`}
              >
                <div className="font-medium">{sev}</div>
                <div className="opacity-80 mt-0.5">{text}</div>
              </div>
            )
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
