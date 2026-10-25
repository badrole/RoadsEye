/**
 * useWebSocket — manages separate WebSocket connections for video and metrics.
 *
 * Video WS  → binary JPEG frames  → ImageBitmap on a canvas ref
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

    let readyBmp = null
    let rafId = 0
    let stale = false
    let pendingBlob = null
    let isDecoding = false
    let cachedCanvas = null
    let cachedCtx = null

    const paintToCanvas = () => {
      rafId = 0
      if (stale || !readyBmp) return
      const bmp = readyBmp
      readyBmp = null
      const canvas = canvasRef.current
      if (canvas) {
        if (canvas !== cachedCanvas) {
          cachedCanvas = canvas
          cachedCtx = canvas.getContext('2d', { alpha: false, desynchronized: true })
        }
        if (canvas.width !== bmp.width) canvas.width = bmp.width
        if (canvas.height !== bmp.height) canvas.height = bmp.height
        if (cachedCtx) cachedCtx.drawImage(bmp, 0, 0)
      }
      bmp.close()
    }

    const decodeNext = (blob) => {
      if (!blob || stale || isDecoding) return
      isDecoding = true
      createImageBitmap(blob)
        .then((bmp) => {
          isDecoding = false
          if (stale) { bmp.close(); return }
          if (readyBmp) readyBmp.close()
          readyBmp = bmp
          if (!rafId) {
            rafId = requestAnimationFrame(paintToCanvas)
          }
          if (pendingBlob) {
            const next = pendingBlob
            pendingBlob = null
            decodeNext(next)
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
      if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
      pendingBlob = null
      if (readyBmp) { readyBmp.close(); readyBmp = null }
      onClose()
    }
    ws.onerror = () => { ws.close() }

    ws.onmessage = (e) => {
      if (canvasRef.current && e.data instanceof Blob) {
        if (!isDecoding) {
          decodeNext(e.data)
        } else {
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
const UI_THROTTLE_MS  = 180  // throttle non-crossing UI state updates to ~5.5 Hz

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
    let lastCommitTime = 0
    let throttleTimer = null
    let pendingMsg = null
    let queuedCrossings = []
    let queuedAlerts = []

    const flushPending = () => {
      if (throttleTimer) {
        clearTimeout(throttleTimer)
        throttleTimer = null
      }
      if (!pendingMsg) return
      const msg = pendingMsg
      const mergedCrossings = queuedCrossings
      const mergedAlerts = queuedAlerts
      pendingMsg = null
      queuedCrossings = []
      queuedAlerts = []
      lastCommitTime = performance.now()

      const finalMsg = mergedCrossings.length !== (msg.crossings?.length ?? 0)
        ? { ...msg, crossings: mergedCrossings }
        : msg

      setMetrics(prev => {
        const history = [...prev.history, finalMsg].slice(-METRICS_HISTORY)
        const newAlerts = mergedAlerts.length
          ? [...prev.alerts, ...mergedAlerts].slice(-50)
          : prev.alerts
        return { latest: finalMsg, history, alerts: newAlerts }
      })
    }

    ws.onopen = () => { setConnected(true); onOpen() }
    ws.onclose = (e) => {
      if (throttleTimer) { clearTimeout(throttleTimer); throttleTimer = null }
      setConnected(false)
      onClose(e.wasClean)
    }
    ws.onerror = () => { ws.close() }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'ping') return
        pendingMsg = msg
        if (msg.crossings?.length) {
          queuedCrossings.push(...msg.crossings)
        }
        if (msg.alerts?.length) {
          queuedAlerts.push(...msg.alerts)
        }

        const now = performance.now()
        if (msg.crossings?.length > 0 || now - lastCommitTime >= UI_THROTTLE_MS) {
          flushPending()
        } else if (!throttleTimer) {
          throttleTimer = setTimeout(flushPending, UI_THROTTLE_MS - (now - lastCommitTime))
        }
      } catch {
        // ignore malformed frames
      }
    }

    return ws
  }, [])

  useExponentialBackoff(connectFn, enabled)
  return { connected, metrics }
}
