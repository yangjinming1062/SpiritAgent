import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './app.tsx'
import './styles.css'

// 安装器仅随浅色主题发布；运行时主题切换由桌面端承载，不在此处提供 provider。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
