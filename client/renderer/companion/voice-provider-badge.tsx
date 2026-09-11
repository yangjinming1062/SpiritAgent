import type React from 'react'

import { voiceProviderLabel } from './voice'

interface VoiceProviderBadgeProps {
  provider: string
}

export function VoiceProviderBadge({ provider }: VoiceProviderBadgeProps): React.ReactElement {
  return (
    <span className="rounded-full border border-line-hairline px-1.5 py-0.5 text-[10px] font-normal text-muted">
      {voiceProviderLabel(provider)}
    </span>
  )
}
