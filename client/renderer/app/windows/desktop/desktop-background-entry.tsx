import '../../../styles.css'

import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

// 副屏不装配鉴权、会话或通用 preload，只消费主屏已经取得的图片。
function DesktopBackground() {
  const [image, setImage] = useState<string | null>(null)

  useEffect(() => {
    const bridge = window.desktopBackground

    if (!bridge) {
      throw new Error('Desktop background bridge is unavailable')
    }

    let generation = 0
    let pendingImage: HTMLImageElement | null = null

    const cancelImage = (): void => {
      if (pendingImage) {
        pendingImage.onload = null
        pendingImage.onerror = null
        pendingImage.src = ''
        pendingImage = null
      }
    }

    const off = bridge.onImage(payload => {
      const current = ++generation
      cancelImage()

      const apply = (): void => {
        if (current !== generation) {
          return
        }

        document.documentElement.dataset.theme = payload.theme
        document.documentElement.dataset.palette = payload.theme.startsWith('night') ? 'night' : 'day'
        setImage(payload.image)
      }

      if (!payload.image) {
        apply()

        return
      }

      const next = new Image()
      pendingImage = next

      next.onload = () => {
        apply()
        cancelImage()
      }

      next.onerror = () => {
        console.warn('Desktop background image could not be decoded')
        cancelImage()
      }

      next.src = payload.image
    })

    void bridge.ready().catch(error => console.warn('Desktop background readiness failed', error))

    return () => {
      generation += 1
      cancelImage()
      off()
    }
  }, [])

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'var(--ui-app-bg)',
        backgroundImage: image ? `url(${image})` : undefined,
        backgroundPosition: 'center',
        backgroundSize: 'cover'
      }}
    />
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('desktop-background: missing root element')
}

createRoot(root).render(<DesktopBackground />)
