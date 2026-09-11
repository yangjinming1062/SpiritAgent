import type { ReadableAtom } from 'nanostores'
import { useEffect } from 'react'

import { useLatestRef } from './use-latest-ref'

// 收拢"挂载时订阅 nanostores atom，卸载时取消"这一反复出现的样板。
// 签名取 `ReadableAtom<T>`——`.listen()` 是只读端点，写端点（`Atom`）也能向下兼容。
// handler 经 latest ref 注入，避免调用方漏传 deps 导致陈旧闭包。
export function useAtomListen<T>($atom: ReadableAtom<T>, handler: (value: T) => void): void {
  const handlerRef = useLatestRef(handler)

  useEffect(() => {
    return $atom.listen(value => {
      handlerRef.current(value)
    })
  }, [$atom, handlerRef])
}
