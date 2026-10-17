/**
 * VideoFeed — renders live JPEG frames from the /ws/video WebSocket
 * onto a <canvas> element, preserving the server-side aspect ratio.
 */
import { useRef } from 'react'
import { useVideoStream } from '../hooks/useWebSocket'

export default function VideoFeed({ enabled }) {
  const canvasRef = useRef(null)
  const connected = useVideoStream(canvasRef, enabled)

  return (
    <div className="relative w-full bg-gray-900 rounded-xl overflow-hidden border border-gray-700">
      {/* Status badge */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full ${
            connected ? 'bg-green-400 animate-pulse' : 'bg-red-500'
          }`}
        />
        <span className="text-xs font-medium text-gray-200 bg-black/50 px-2 py-0.5 rounded">
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>

      {/* Canvas — fills container, keeps aspect ratio via object-fit */}
      <canvas
        ref={canvasRef}
        className="w-full h-full object-contain"
        style={{ minHeight: 360 }}
      />

      {/* Placeholder when no frame has arrived yet */}
      {!connected && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-gray-500">
          <svg className="w-14 h-14 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" />
          </svg>
          <p className="text-sm">Waiting for pipeline…</p>
        </div>
      )}
    </div>
  )
}
