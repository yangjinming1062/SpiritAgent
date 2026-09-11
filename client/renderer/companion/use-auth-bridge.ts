import { useEffect } from 'react'

import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'

// 任何 renderer 进程挂载时都得跑一遍：把 $auth 从 { kind: 'pending' } 推进到
// authenticated/unauthenticated，并订阅 onAuthChanged / onSessionExpired 让
// 跨窗口登出与 token 过期即时落地。
//
// 早期只在伴工精灵的 CompanionRoot 里挂这套；bootstrapSurface 接管的窗口
//（工作台 / 生活空间）漏了这层，回归成兜底蛋形象。
export function useAuthBridge(): void {
  useEffect(() => {
    void hydrateAuth()
  }, [])

  useMainProcessListener('onAuthChanged', payload => void applyAuthBroadcast(payload), [])
  useMainProcessListener('onSessionExpired', () => void logout(), [])
}
