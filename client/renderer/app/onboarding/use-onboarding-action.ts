import { useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { useStrings } from '@/shared/strings'

// 同一步只提交一次；离页或换号后，迟到的回执不能推进下一步。
export function useOnboardingAction() {
  const begin = useAsyncGuard()
  const fallback = useStrings().onboarding.saveFailed
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)

  async function run(action: (isLive: () => boolean) => Promise<void>): Promise<void> {
    if (submitting.current) {
      return
    }

    const isLive = begin()
    submitting.current = true
    setBusy(true)
    setError('')

    try {
      await action(isLive)
    } catch (cause) {
      if (isLive()) {
        setError(backendDetailMessage(cause, fallback))
      }
    } finally {
      submitting.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return { busy, error, setError, run }
}
