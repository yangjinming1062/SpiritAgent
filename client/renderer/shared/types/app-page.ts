import type React from 'react'

export interface AppPageProps {
  containerRef: React.RefObject<HTMLElement | null>
  foreground: boolean
  visible: boolean
  mediaViewId: string
}
