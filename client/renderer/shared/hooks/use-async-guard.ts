import { useCallback, useEffect, useRef } from 'react'

import { currentClearEpoch } from '@/shared/lib/storage'

// 异步回写守卫：发起操作时调用 begin() 捕获账户状态代次，回写前用返回的 isLive() 判断组件仍挂载且代次未变（换号、登出会递增代次）。
export function useAsyncGuard(): () => () => boolean {
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true

    return () => {
      mounted.current = false
    }
  }, [])

  return useCallback(() => {
    const epoch = currentClearEpoch()

    return () => mounted.current && epoch === currentClearEpoch()
  }, [])
}
