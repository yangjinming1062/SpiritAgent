import { type DependencyList, useEffect } from 'react'

import { desktopEvents } from '@/shared/platform/desktop'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

// 预加载桥顶层的主进程事件订阅：on…成员接收回调并返回退订函数。
type ListenerName = Extract<keyof DesktopBridge, `on${string}`>

type ListenerCallback<K extends ListenerName> = Parameters<DesktopBridge[K]>[0]

type ListenerSubscriptions = { [K in ListenerName]: (callback: ListenerCallback<K>) => () => void }

// Caller-managed deps: the deps array is supplied by the caller and intentionally escapes both `react-hooks/exhaustive-deps` and React Compiler's static analysis.
/* eslint-disable react-hooks/exhaustive-deps, react-compiler/react-compiler */
export function useMainProcessListener<K extends ListenerName>(
  name: K,
  fn: ListenerCallback<K>,
  deps: DependencyList
): void {
  useEffect(() => {
    const subscriptions: ListenerSubscriptions = desktopEvents()

    return subscriptions[name](fn)
  }, deps)
}
/* eslint-enable react-hooks/exhaustive-deps, react-compiler/react-compiler */
