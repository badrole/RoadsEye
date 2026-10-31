import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Surface any runtime error on-page — blank screen made console-only errors invisible
function showFatal(msg) {
  let box = document.getElementById('fatal-err')
  if (!box) {
    box = document.createElement('pre')
    box.id = 'fatal-err'
    box.style.cssText = 'position:fixed;inset:auto 8px 8px 8px;z-index:9999;background:#2b0d0d;color:#ffb4b4;padding:10px;font:12px monospace;white-space:pre-wrap;max-height:45vh;overflow:auto;border:1px solid #ff6b6b;border-radius:4px'
    document.body.appendChild(box)
  }
  box.textContent += msg + '\n\n'
}
window.addEventListener('error', e => showFatal('ERROR: ' + e.message + '\n' + (e.error?.stack ?? '')))
window.addEventListener('unhandledrejection', e => showFatal('REJEKSI: ' + (e.reason?.stack ?? e.reason)))

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
