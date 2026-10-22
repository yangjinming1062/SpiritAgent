import { useEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'

import { useForegroundRestored } from './use-reading-foreground'

interface ReadingReceiptState {
  scope: string
  refreshKey: number
  confirmed: ReadonlySet<string>
  blocked: boolean
}

export function useReadingReceipt({
  scope,
  refreshKey,
  foreground,
  snapshot,
  displayedIds,
  ready = true,
  markRead
}: {
  scope: string
  refreshKey: number
  foreground: boolean
  snapshot: readonly string[] | null
  displayedIds: readonly string[]
  ready?: boolean
  markRead: (ids: string[]) => Promise<boolean>
}): void {
  const [state, setState] = useState<ReadingReceiptState>(() => ({
    scope,
    refreshKey,
    confirmed: new Set(),
    blocked: false
  }))

  const requestRef = useRef<Promise<boolean> | undefined>(undefined)
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    requestRef.current = undefined
    setState(current => ({
      scope,
      refreshKey,
      confirmed: current.scope === scope ? current.confirmed : new Set(),
      blocked: false
    }))
  }, [scope, refreshKey])

  useForegroundRestored(foreground, () => setState(current => ({ ...current, blocked: false })))

  useEffect(() => {
    if (
      state.scope !== scope ||
      state.refreshKey !== refreshKey ||
      !ready ||
      !foreground ||
      snapshot === null ||
      state.blocked ||
      requestRef.current !== undefined ||
      document.visibilityState !== 'visible' ||
      !document.hasFocus()
    ) {
      return
    }

    // 进入前的提醒统一确认；后续内容只确认当前实际展示的条目。
    const ids = [...new Set([...snapshot, ...displayedIds])].filter(id => !state.confirmed.has(id))

    if (ids.length === 0) {
      return
    }

    const isLive = beginAsync()
    const request = markRead(ids)
    requestRef.current = request
    void request.then(ok => {
      if (!isLive() || requestRef.current !== request) {
        return
      }

      requestRef.current = undefined
      setState(current =>
        ok ? { ...current, confirmed: new Set([...current.confirmed, ...ids]) } : { ...current, blocked: true }
      )
    })
  }, [state, scope, refreshKey, ready, foreground, snapshot, displayedIds, markRead, beginAsync])
}
