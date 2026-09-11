import type { DesktopUpdateEvent } from '@ipc/contracts'

import { setUpdateStatus } from '@/shared/store/update'

type UpdateHandlerMap = {
  [E in DesktopUpdateEvent as E['type']]: (event: E) => void
}

const UPDATE_HANDLERS: UpdateHandlerMap = {
  checking: () => setUpdateStatus({ status: 'checking' }),
  available: p =>
    setUpdateStatus({
      status: 'available',
      version: p.info?.version ?? '',
      releaseDate: p.info?.releaseDate
    }),
  none: p => setUpdateStatus({ status: 'none', version: p.info?.version }),
  progress: p =>
    setUpdateStatus({
      status: 'downloading',
      percent: p.progress.percent,
      transferred: p.progress.transferred,
      total: p.progress.total
    }),
  downloaded: p => setUpdateStatus({ status: 'downloaded', version: p.info?.version ?? '' }),
  error: p => setUpdateStatus({ status: 'error', message: p.message ?? 'Unknown error' })
}

export function installUpdateBridge(): () => void {
  const off = window.spiritagent?.update?.onEvent?.(payload => {
    const handler = UPDATE_HANDLERS[payload.type] as ((p: DesktopUpdateEvent) => void) | undefined
    handler?.(payload)
  })

  return () => {
    off?.()
  }
}
