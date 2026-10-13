import { useEffect } from 'react'

import { createAccountLifecycleRuntime } from '@/app/runtime/account-runtime'

export function useAccountLifecycle(): void {
  useEffect(() => {
    const runtime = createAccountLifecycleRuntime()
    runtime.start()

    return runtime.dispose
  }, [])
}
