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

    // Low-latency render: keeps video synchronized with metrics feed (<50ms latency)
    // so vehicle crossings on video match the count metrics at the exact same instant.
    let currentBmp = null
    let rafId = 0
    let stale = false
    let pendingBlob = null
    let isDecoding = false

    const drawFrame = (blob) => {
      if (!blob || stale || isDecoding) return
      isDecoding = true
      createImageBitmap(blob)
        .then((bmp) => {
          isDecoding = false
          if (stale) { bmp.close(); return }
          if (currentBmp) currentBmp.close()
          currentBmp = bmp
          const canvas = canvasRef.current
          if (canvas) {
            if (canvas.width !== bmp.width) canvas.width = bmp.width
            if (canvas.height !== bmp.height) canvas.height = bmp.height
            const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true })
            if (ctx) ctx.drawImage(bmp, 0, 0)
          }
          // If a newer frame arrived while decoding, draw it immediately
          if (pendingBlob) {
            const next = pendingBlob
            pendingBlob = null
            drawFrame(next)
          }
        })
        .catch(() => {
          isDecoding = false
        })
    }

    ws.onopen = () => { setConnected(true); onOpen() }
    ws.onclose = () => {
      setConnected(false)
      stale = true
      cancelAnimationFrame(rafId)
      pendingBlob = null
      if (currentBmp) { currentBmp.close(); currentBmp = null }
      onClose()
    }
    ws.onerror = () => { ws.close() }

    ws.onmessage = (e) => {
      if (canvasRef.current && e.data instanceof Blob) {
        if (!isDecoding) {
          drawFrame(e.data)
        } else {
          // Keep newest frame, drop intermediate to stay at real-time live edge
          pendingBlob = e.data
        }
      }
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
        if (msg.type === 'ping') return   // filter heartbeat pings
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
