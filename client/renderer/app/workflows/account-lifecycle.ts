import { useEffect } from 'react'

import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { applyAuthBroadcast, expireSession, hydrateAuth } from '@/shared/store/auth'

// 各窗口独立水合认证并订阅变更，让连接与操作使用当前会话。
export function useAccountLifecycle(): void {
  useEffect(() => {
    void hydrateAuth()
  }, [])

  useMainProcessListener('onAuthChanged', payload => void applyAuthBroadcast(payload), [])
  useMainProcessListener('onSessionExpired', sessionId => void expireSession(sessionId), [])
}
