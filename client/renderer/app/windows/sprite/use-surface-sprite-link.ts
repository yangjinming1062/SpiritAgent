import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { setSpatialLocale } from '@/modules/character'
import { $surfaceOpen } from '@/shared/store/surfaces'

export function useSurfaceSpriteLink(): void {
  const open = useStore($surfaceOpen)

  useEffect(() => {
    setSpatialLocale(open === 'workbench' ? 'workbench' : 'home', { instant: true })

    // 生活空间由场景呈现伙伴，隐藏桌面精灵。
    if (open === 'living') {
      document.documentElement.dataset.spriteHidden = 'true'
    } else {
      delete document.documentElement.dataset.spriteHidden
    }
  }, [open])
}
