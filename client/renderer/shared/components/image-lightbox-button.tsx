import type { ReactNode } from 'react'
import { useState } from 'react'

import { PortraitLightbox } from '@/shared/components/portrait-lightbox'

// 灯箱打开时通过 onOpenChange 停用外层向导的 Esc。
export function ImageLightboxButton({
  ariaLabel,
  children,
  className,
  name,
  onOpenChange,
  url
}: {
  ariaLabel?: string
  children: ReactNode
  className?: string
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
