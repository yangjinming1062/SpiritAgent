// 监听 surface 状态变化，调整精灵 locale + 显隐：
// - workbench 打开：locale → 'workbench'，桌面独立精灵收起，工作台内嵌一体化伴工
// - living 打开：locale → 'home' 但隐藏精灵（她在房间里画里）
// - 都关闭：恢复 locale = 'home'，桌面精灵可见

import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $surfaceOpen } from '@/shared/store/surfaces'

import { setLocale } from '../spatial'

export function useSurfaceSpriteLink(): void {
  const open = useStore($surfaceOpen)

  useEffect(() => {
    if (open === 'living') {
      // 生活空间打开：精灵在房间里画里，不出现在桌面；locale 回 home 但模型隐藏。
      setLocale('home', { instant: true })
      document.documentElement.dataset.spriteHidden = 'true'

      return
    }

    delete document.documentElement.dataset.spriteHidden

    if (open === 'workbench') {
      setLocale('workbench', { instant: true })

      return
    }

    setLocale('home', { instant: true })
  }, [open])
}
