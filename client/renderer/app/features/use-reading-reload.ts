import { useCallback, useEffect, useState } from 'react'

import { $gatewayState } from '@/shared/store/gateway'

// 阅读页重载计数：手动 retry 与网关重连共用；listen 不在挂载时立即触发，避免已连接状态下误重载。
export function useReadingReload(): { reloadKey: number; reload: () => void } {
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey(key => key + 1), [])

  useEffect(
    () =>
      $gatewayState.listen(state => {
        if (state === 'open') {
          setReloadKey(key => key + 1)
        }
      }),
    []
  )

  return { reloadKey, reload }
}
