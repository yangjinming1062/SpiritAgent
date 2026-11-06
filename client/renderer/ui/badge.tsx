import type { ReactNode } from 'react'

import { cn } from '@/shared/lib/utils'

import { BADGE, BADGE_ACCENT, BADGE_COUNT } from './palette'

export type BadgeTone = 'muted' | 'count' | 'accent'

/** 列表标题旁的计数或状态小徽标。计数是中性填充，状态用强调色。 */
export function Badge({
  children,
  className,
  tone = 'muted'
}: {
  children: ReactNode
  className?: string
  tone?: BadgeTone
}): React.JSX.Element {
  return (
    <span className={cn(tone === 'accent' ? BADGE_ACCENT : tone === 'count' ? BADGE_COUNT : BADGE, className)}>
      {children}
    </span>
  )
}
