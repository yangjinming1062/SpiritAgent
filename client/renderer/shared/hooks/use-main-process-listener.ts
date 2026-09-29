import { type DependencyList, useEffect } from 'react'

// 预加载桥顶层的主进程事件订阅：on… 成员接收回调并返回退订函数。
type ListenerName = Extract<keyof Window['spiritagent'], `on${string}`>

type ListenerCallback<K extends ListenerName> = Parameters<Window['spiritagent'][K]>[0]

type ListenerSubscriptions = { [K in ListenerName]: (callback: ListenerCallback<K>) => () => void }

// Caller-managed deps: the deps array is supplied by the caller and intentionally
// escapes both `react-hooks/exhaustive-deps` and React Compiler's static analysis.
/* eslint-disable react-hooks/exhaustive-deps, react-compiler/react-compiler */
export function useMainProcessListener<K extends ListenerName>(
  name: K,
  fn: ListenerCallback<K>,
  deps: DependencyList
): void {
  useEffect(() => {
    const subscriptions: ListenerSubscriptions | undefined = window.spiritagent
    const off = subscriptions?.[name]?.(fn)

    return () => {
      off?.()
    }
  }, deps)
}
/* eslint-enable react-hooks/exhaustive-deps, react-compiler/react-compiler */
