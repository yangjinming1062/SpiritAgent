import './style.css'

import { createRoot } from 'react-dom/client'

import { App } from './App'

const root = document.getElementById('root')

if (!root) {
  throw new Error('找不到页面入口')
}

createRoot(root).render(<App />)
