import { useCallback, useEffect, useRef, useState } from 'react'

// 规范的"挂载时拉取、持有结果、暴露错误"循环。取代每个设置页里重复出现的
// `useEffect` + `useRef('cancelled')` + try/catch/finally 大约 12 行的样板。
// 调用方传入一个 resolve 出数据（或抛出）的 `load` 函数；hook 负责挂载、
// 卸载时取消、错误状态以及手动 `reload` 触发。
//
// `reloadKey` 参数让调用方能在上游输入变化时重新触发加载（如 `errorKey` prop
// 变化，或用户按下重试）。传入空串 / 0 / null 则仅挂载时加载一次。

export type AsyncLoader<T> = {
  data: T | null
  isLoading: boolean
  error: unknown
  reload: () => void
}

export function useAsyncLoader<T>(load: () => Promise<T>, reloadKey?: unknown): AsyncLoader<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  const loadRef = useRef(load)

  // 镜像最新的 `load` 闭包，让 effect 体始终读取最新状态，
  // 又不必依赖函数引用稳定性。
  loadRef.current = load

  useEffect(() => {
    let cancelled = false

    setIsLoading(true)
    setError(null)

    const promise = loadRef.current()

    promise
      .then(result => {
        if (!cancelled) {
          setData(result)
          setIsLoading(false)
        }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err)
          setIsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
    // `reloadKey` 是调用方提供的判别值；`version` 是手动 reload 触发器
    // （通过 `reload()` 自增）。
  }, [reloadKey, version])

  const reload = useCallback(() => {
    setVersion(v => v + 1)
  }, [])

  return { data, isLoading, error, reload }
}
