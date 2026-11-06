import type { DesktopBackground, DesktopBackgroundPlayback } from '@ipc/contracts'

interface Acknowledger {
  acknowledge(payload: {
    accountEpoch: number
    revision: number
    playId: string
    status: DesktopBackgroundPlayback['status']
    error?: string
  }): Promise<void>
}

export interface ReceiptLog {
  clear: () => void
  has(playId: string): boolean
  send: (
    media: DesktopBackground,
    status: DesktopBackgroundPlayback['status'],
    error?: string,
    failureLabel?: string
  ) => void
}

/** 播放回执账本：同一 playId 只结算一次，保留最近 64 条防止无限增长。 */
export function createReceiptLog(acknowledger: Acknowledger, isDisposed: () => boolean): ReceiptLog {
  const settled = new Set<string>()

  const send: ReceiptLog['send'] = (media, status, error, failureLabel = 'Desktop background acknowledgment failed') => {
    if (!media.playId || isDisposed()) {
      return
    }

    if (status === 'completed' || status === 'failed' || status === 'interrupted') {
      settled.add(media.playId)

      if (settled.size > 64) {
        const oldest = settled.values().next().value

        if (oldest) {
          settled.delete(oldest)
        }
      }
    }

    void acknowledger
      .acknowledge({
        accountEpoch: media.accountEpoch,
        revision: media.revision,
        playId: media.playId,
        status,
        ...(error ? { error } : {})
      })
      .catch(failure => console.warn(failureLabel, failure))
  }

  return { clear: () => settled.clear(), has: playId => settled.has(playId), send }
}
