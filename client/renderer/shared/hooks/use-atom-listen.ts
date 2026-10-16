import type { ReadableAtom } from 'nanostores'
import { useEffect } from 'react'

import { useLatestRef } from './use-latest-ref'

// handler 经 latest ref 注入，避免订阅回调捕获过期值。
export function useAtomListen<T>($atom: ReadableAtom<T>, handler: (value: T) => void): void {
  const handlerRef = useLatestRef(handler)

  useEffect(() => {
    return $atom.listen(value => {
      handlerRef.current(value)
    })
  }, [$atom, handlerRef])
}
