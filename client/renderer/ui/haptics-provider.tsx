import { type ReactNode, useEffect } from 'react'
import { useWebHaptics } from 'web-haptics/react'

import { registerHapticTrigger } from '@/shared/lib/haptics'

export function HapticsProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const { trigger } = useWebHaptics({ debug: true, showSwitch: false })

  useEffect(() => {
    registerHapticTrigger(trigger)

    return () => registerHapticTrigger(null)
  }, [trigger])

  return <>{children}</>
}
