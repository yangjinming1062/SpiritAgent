import type { ReactNode } from 'react'
import { useState } from 'react'

import { PortraitLightbox } from '@/shared/components/portrait-lightbox'

/**
 * 可放大的图片缩略图：点击打开灯箱。灯箱状态由本组件持有，
 * `onOpenChange` 让外层在灯箱打开时停用自己的 Esc（例如外层向导）。
 */
export function ImageLightboxButton({
  ariaLabel,
  children,
  className,
  name,
  onOpenChange,
  url
}: {
  /** 放大按钮的无障碍名称。 */
  ariaLabel?: string
  children: ReactNode
  className?: string
  /** 灯箱标题。 */
  name: string
  onOpenChange?: (open: boolean) => void
  url: string
}): React.JSX.Element {
  const [open, setOpen] = useState(false)

  const change = (next: boolean): void => {
    setOpen(next)
    onOpenChange?.(next)
  }

  return (
    <>
      <button aria-label={ariaLabel} className={className} onClick={() => change(true)} type="button">
        {children}
      </button>
      {open && <PortraitLightbox name={name} onClose={() => change(false)} url={url} />}
    </>
  )
}
