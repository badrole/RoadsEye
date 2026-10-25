/**
 * VideoFeed — renders live JPEG frames from the /ws/video WebSocket
 * onto a <canvas> element, preserving the server-side aspect ratio.
 */
import { useRef, memo } from 'react'
import { useVideoStream } from '../hooks/useWebSocket'
import LineEditor from './LineEditor'

function VideoFeed({ enabled, prerollPct = null, countMode = 'line', onCountModeChange }) {
  const canvasRef = useRef(null)
  const connected = useVideoStream(canvasRef, enabled)
  const loading = prerollPct != null && prerollPct < 100

  return (
    <div className="relative w-full bg-asphalt-deep rounded-sm overflow-hidden border-2 border-lane-dim/60 shadow-glowlane">
      <LineEditor
        canvasRef={canvasRef}
        countMode={countMode}
        onCountModeChange={onCountModeChange}
      />

      {/* Timeshift loading overlay — progress only */}
      {loading && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-asphalt-deep/85 backdrop-blur-sm px-6">
          <span className="font-code text-3xl font-extrabold text-lane">{prerollPct}%</span>
          <div className="w-full max-w-md h-2.5 rounded bg-asphalt border border-lane-dim/40 overflow-hidden">
            <div
              className="h-full bg-lane transition-all duration-500"
              style={{ width: `${prerollPct}%` }}
            />
          </div>
        </div>
      )}

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
        <span className="font-code text-[10px] tracking-wider uppercase px-2 py-1 rounded-sm bg-asphalt-deep/85 border border-lane/40 text-lane font-bold">
          {countMode === 'id' ? 'MODE: PER ID' : 'MODE: PER LINE'}
        </span>
      </div>

      {/* Canvas — element keeps the video's own aspect (w/h auto), so the
          overlay editor maps 1:1 with no letterbox offsets */}
      <canvas
        ref={canvasRef}
        className="w-full h-auto block"
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

export default memo(VideoFeed)
