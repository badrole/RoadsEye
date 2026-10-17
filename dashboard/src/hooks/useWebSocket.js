/**
 * useWebSocket — manages separate WebSocket connections for video and metrics.
 *
 * Video WS  → binary JPEG frames  → objectURL on a canvas ref
 * Metrics WS → JSON text frames   → parsed MetricsMessage state
 *
 * Reconnects automatically with exponential backoff on unexpected close.
 */
import { useEffect, useRef, useState, useCallback } from 'react'

// Derive WS base from the current page origin so the same build works in
// local dev (Vite proxy: /api → localhost:8000), Docker (nginx proxy:
// /api → api:8000), and any other deployment without rebuild.
const _proto  = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
const BASE_WS = import.meta.env.VITE_API_WS_URL
  || `${_proto}//${window.location.host}/api`

const INITIAL_DELAY = 1_000   // ms
const MAX_DELAY     = 30_000  // ms

function useExponentialBackoff(connectFn, enabled) {
  const delayRef    = useRef(INITIAL_DELAY)
  const timerRef    = useRef(null)
  const socketRef   = useRef(null)
  const enabledRef  = useRef(enabled)

  useEffect(() => { enabledRef.current = enabled }, [enabled])

  const connect = useCallback(() => {
    if (!enabledRef.current) return
    const ws = connectFn({
      onClose: (_wasClean) => {
        if (!enabledRef.current) return
        timerRef.current = setTimeout(() => {
          delayRef.current = Math.min(delayRef.current * 2, MAX_DELAY)
          connect()
        }, delayRef.current)
      },
      onOpen: () => { delayRef.current = INITIAL_DELAY },
    })
    socketRef.current = ws
  }, [connectFn])

  useEffect(() => {
    if (enabled) connect()
    return () => {
      enabledRef.current = false
      clearTimeout(timerRef.current)
      socketRef.current?.close()
    }
  }, [enabled, connect])
}


// ── Video WebSocket ────────────────────────────────────────────────────────────

export function useVideoStream(canvasRef, enabled = true) {
  const [connected, setConnected] = useState(false)

  const connectFn = useCallback(({ onClose, onOpen }) => {
    const ws = new WebSocket(`${BASE_WS}/ws/video`)
    ws.binaryType = 'blob'

    ws.onopen = () => { setConnected(true); onOpen() }
    ws.onclose = (e) => { setConnected(false); onClose(e.wasClean) }
    ws.onerror = () => { ws.close() }

    ws.onmessage = (e) => {
      const canvas = canvasRef.current
      if (!canvas || !(e.data instanceof Blob)) return
      const url = URL.createObjectURL(e.data)
      const img  = new Image()
      img.onload = () => {
        const ctx = canvas.getContext('2d')
        canvas.width  = img.width
        canvas.height = img.height
        ctx.drawImage(img, 0, 0)
        URL.revokeObjectURL(url)
      }
      img.src = url
    }

    return ws
  }, [canvasRef])

  useExponentialBackoff(connectFn, enabled)
  return connected
}


// ── Metrics WebSocket ──────────────────────────────────────────────────────────

const METRICS_HISTORY = 60   // keep last 60 data points for charts

const DEFAULT_METRICS = {
  latest: null,               // most recent MetricsMessage
  history: [],                // array of MetricsMessage, capped at METRICS_HISTORY
  alerts: [],                 // accumulated alerts (cleared on new pipeline start)
}

export function useMetricsStream(enabled = true) {
  const [connected, setConnected]   = useState(false)
  const [metrics, setMetrics]       = useState(DEFAULT_METRICS)

  const connectFn = useCallback(({ onClose, onOpen }) => {
    const ws = new WebSocket(`${BASE_WS}/ws/metrics`)

    ws.onopen = () => { setConnected(true); onOpen() }
    ws.onclose = (e) => { setConnected(false); onClose(e.wasClean) }
    ws.onerror = () => { ws.close() }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        setMetrics(prev => {
          const history = [...prev.history, msg].slice(-METRICS_HISTORY)
          const newAlerts = msg.alerts?.length
            ? [...prev.alerts, ...msg.alerts].slice(-50)
            : prev.alerts
          return { latest: msg, history, alerts: newAlerts }
        })
      } catch {
        // ignore malformed frames
      }
    }

    return ws
  }, [])

  useExponentialBackoff(connectFn, enabled)
  return { connected, metrics }
}
