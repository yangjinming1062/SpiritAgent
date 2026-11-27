import { useEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'

import { useForegroundRestored } from './use-reading-foreground'

interface ReadingReceiptState {
  scope: string
  refreshKey: number
  confirmed: ReadonlySet<string>
  blockedId?: string
}

export function useReadingReceipt({
  scope,
  refreshKey,
  foreground,
  displayedId,
  ready,
  markRead
}: {
  scope: string
  refreshKey: number
  foreground: boolean
  displayedId?: string
  ready: boolean
  markRead: (ids: string[]) => Promise<boolean>
}): void {
  const [state, setState] = useState<ReadingReceiptState>(() => ({
    scope,
    refreshKey,
    confirmed: new Set()
  }))

  const requestRef = useRef<Promise<boolean> | undefined>(undefined)
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    requestRef.current = undefined
    setState(current => ({
      scope,
      refreshKey,
      confirmed: current.scope === scope ? current.confirmed : new Set()
    }))
  }, [scope, refreshKey])

  useForegroundRestored(foreground, () => setState(current => ({ ...current, blockedId: undefined })))

  useEffect(() => {
    if (state.blockedId && state.blockedId !== displayedId) {
      setState(current => ({ ...current, blockedId: undefined }))
    }
  }, [displayedId, state.blockedId])

  useEffect(() => {
    if (
      state.scope !== scope ||
      state.refreshKey !== refreshKey ||
      !ready ||
      !foreground ||
      !displayedId ||
      state.confirmed.has(displayedId) ||
      state.blockedId === displayedId ||
      requestRef.current !== undefined ||
      document.visibilityState !== 'visible' ||
      !document.hasFocus()
    ) {
      return
    }

    const isLive = beginAsync()
    const request = markRead([displayedId])
    requestRef.current = request
    void request.then(ok => {
      if (!isLive() || requestRef.current !== request) {
        return
      }

      requestRef.current = undefined
      setState(current =>
        ok
          ? { ...current, confirmed: new Set([...current.confirmed, displayedId]) }
          : { ...current, blockedId: displayedId }
      )
    })
  }, [state, scope, refreshKey, ready, foreground, displayedId, markRead, beginAsync])
}
