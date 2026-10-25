import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { returnToHomePosition } from '@/modules/character'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import { $surfaceOpen } from '@/shared/store/surfaces'

export function useSurfaceSpriteLink(): void {
  const open = useStore($surfaceOpen)

  useEffect(() => {
    returnToHomePosition(true)

    if (open) {
      $whisperOpen.set(false)
      document.documentElement.dataset.spriteHidden = 'true'
    } else {
      delete document.documentElement.dataset.spriteHidden
    }
  }, [open])
}
