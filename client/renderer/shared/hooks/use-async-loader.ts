import { type Dispatch, type SetStateAction, useCallback, useEffect, useRef, useState } from 'react'

// 规范的「挂载时拉取、持有结果、暴露错误」循环：调用方传入 resolve 出数据（或抛出）的 load，hook 负责挂载、卸载时取消、错误状态以及手动 reload。

export type AsyncLoader<T> = {
  data: T | null
  isLoading: boolean
  error: unknown
  reload: () => void
  /** 本地改写已加载的数据（如保存后直接采用返回值）。 */
  setData: Dispatch<SetStateAction<T | null>>
}

export function useAsyncLoader<T>(load: () => Promise<T>): AsyncLoader<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  const loadRef = useRef(load)

  // 镜像最新的 load 闭包，让 effect 体始终读取最新状态，又不必依赖函数引用稳定性。
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
    // `version` 是手动 reload 触发器（通过 `reload()` 自增）。
  }, [version])

  const reload = useCallback(() => {
    setVersion(v => v + 1)
  }, [])

  return { data, isLoading, error, reload, setData }
}
