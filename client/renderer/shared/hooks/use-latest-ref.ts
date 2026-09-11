import { useEffect, useRef } from 'react'

// 异步回调 / 计时器 / Promise 引用若直接捕获 value 会读到陈旧值——每次渲染同步到 ref.current。
export function useLatestRef<T>(value: T): { readonly current: T } {
  const ref = useRef(value)

  useEffect(() => {
    ref.current = value
  }, [value])

  return ref
}
