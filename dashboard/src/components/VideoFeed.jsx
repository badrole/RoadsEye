/**
 * VideoFeed — renders live JPEG frames from the /ws/video WebSocket
 * onto a <canvas> element, preserving the server-side aspect ratio.
 */
import { useRef } from 'react'
import { useVideoStream } from '../hooks/useWebSocket'
import LineEditor from './LineEditor'

export default function VideoFeed({ enabled }) {
  const canvasRef = useRef(null)
  const connected = useVideoStream(canvasRef, enabled)

  return (
    <div className="relative w-full bg-asphalt-deep rounded-sm overflow-hidden border-2 border-lane-dim/60 shadow-glowlane">
      <LineEditor canvasRef={canvasRef} />
      {/* Plate — camera housing label */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
        <div
          className={`flex items-center gap-1.5 font-code text-[11px] tracking-widest px-2 py-1 rounded-sm ${
            connected ? 'bg-lane text-asphalt font-bold' : 'bg-stop/90 text-white'
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'animate-pulse bg-asphalt' : 'bg-white'}`} />
          {connected ? 'LIVE' : 'OFFLINE'}
        </div>
        <span className="hidden sm:inline font-code text-[10px] tracking-[0.25em] text-pip bg-asphalt/70 px-2 py-1 rounded-sm">
          CAM-01
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
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-pip">
          <svg className="w-14 h-14 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" />
          </svg>
          <p className="font-code text-xs tracking-widest uppercase">Waiting for pipeline…</p>
        </div>
      )}
    </div>
  )
}
