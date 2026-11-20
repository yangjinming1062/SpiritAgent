import './style.css'

import { createRoot } from 'react-dom/client'

import { App } from './App'
import { initTheme } from './theme'

const root = document.getElementById('root')

if (!root) {
  throw new Error('找不到页面入口')
}

const pairingToken = new URLSearchParams(location.hash.slice(1)).get('token')
if (pairingToken !== null) {
  history.replaceState(null, '', location.pathname + location.search)
}

initTheme()
createRoot(root).render(<App pairingToken={pairingToken} />)
