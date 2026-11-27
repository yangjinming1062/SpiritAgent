import './style.css'

import { createRoot } from 'react-dom/client'

import { App } from './App'
import { initTheme } from './theme'

const root = document.getElementById('root')

if (!root) {
  throw new Error('找不到页面入口')
}

initTheme()
createRoot(root).render(<App />)
