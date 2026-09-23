import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { setSpatialLocale } from '@/modules/character'
import { $surfaceOpen } from '@/shared/store/surfaces'

export function useSurfaceSpriteLink(): void {
  const open = useStore($surfaceOpen)

  useEffect(() => {
    if (open === 'living') {
      // 生活空间由场景呈现伙伴，隐藏桌面精灵。
      setSpatialLocale('home', { instant: true })
      document.documentElement.dataset.spriteHidden = 'true'

      return
    }

    delete document.documentElement.dataset.spriteHidden

    if (open === 'workbench') {
      setSpatialLocale('workbench', { instant: true })

      return
    }

    setSpatialLocale('home', { instant: true })
  }, [open])
}
