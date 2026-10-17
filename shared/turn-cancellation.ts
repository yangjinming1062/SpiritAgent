import type { ApiResponse } from './api'

export interface TurnReference {
  sessionId: string
  requestId: string
}

type TurnReceipt = ApiResponse<'sessions.get_turn'>
type CancellationReceipt = ApiResponse<'sessions.cancel_turn'>

interface CancellationEntry {
  turn: TurnReference
  controller: AbortController
  task: Promise<void>
  receipt?: TurnReceipt
  cancelling?: Promise<void>
  timer?: ReturnType<typeof setTimeout>
  wake?: () => void
  reported: boolean
  cancelAccepted: boolean
  rejection?: { error: unknown }
}

export function createTurnCancellation(options: {
  isCurrent: () => boolean
  read: (turn: TurnReference, signal: AbortSignal) => Promise<TurnReceipt | null>
  cancel: (turn: TurnReference, signal: AbortSignal) => Promise<CancellationReceipt>
  onSettled: (receipt: TurnReceipt, turn: TurnReference) => void | Promise<void>
  onRejected: (error: unknown, turn: TurnReference) => void | Promise<void>
  onError: (error: unknown, turn: TurnReference) => void
}) {
  const entries = new Map<string, CancellationEntry>()
  let closed = false

  const key = (turn: TurnReference): string => JSON.stringify([turn.sessionId, turn.requestId])
  const current = (entry: CancellationEntry): boolean =>
    !closed && options.isCurrent() && entries.get(key(entry.turn)) === entry

  function report(error: unknown, entry: CancellationEntry, settled = false): void {
    const eligible = settled ? !closed && options.isCurrent() : current(entry)
    if (eligible && (settled || !entry.reported)) {
      entry.reported = true
      try {
        options.onError(error, entry.turn)
      } catch (failure) {
        console.error('Turn cancellation error handler failed', failure)
      }
    }
  }

  async function cancel(entry: CancellationEntry): Promise<void> {
    if (!current(entry) || entry.cancelAccepted) {
      return
    }
    if (entry.cancelling) {
      return entry.cancelling
    }
    const task = Promise.resolve().then(async () => {
      try {
        const result = await options.cancel(entry.turn, entry.controller.signal)
        if (result.request_id !== entry.turn.requestId) {
          throw new Error('Cancellation receipt belongs to another turn')
        }
        entry.cancelAccepted = result.cancelled
      } catch (error) {
        report(error, entry)
      }
    })
    entry.cancelling = task
    try {
      await task
    } finally {
      if (entry.cancelling === task) {
        entry.cancelling = undefined
      }
    }
  }

  function wake(entry: CancellationEntry): void {
    clearTimeout(entry.timer)
    entry.timer = undefined
    entry.wake?.()
    entry.wake = undefined
  }

  async function reconcile(entry: CancellationEntry): Promise<void> {
    await cancel(entry)
    while (current(entry)) {
      try {
        let receipt = entry.receipt
        entry.receipt = undefined
        receipt ??= (await options.read(entry.turn, entry.controller.signal)) ?? undefined
        receipt = entry.receipt ?? receipt
        entry.receipt = undefined
        if (!current(entry)) {
          return
        }
        if (receipt) {
          if (receipt.request_id !== entry.turn.requestId) {
            throw new Error('Submission receipt belongs to another turn')
          }
          if (receipt.status !== 'accepted' && receipt.status !== 'running') {
            entries.delete(key(entry.turn))
            wake(entry)
            try {
              await options.onSettled(receipt, entry.turn)
            } catch (error) {
              report(error, entry, true)
            }
            return
          }
          await cancel(entry)
        } else if (entry.rejection !== undefined) {
          entries.delete(key(entry.turn))
          wake(entry)
          try {
            await options.onRejected(entry.rejection.error, entry.turn)
          } catch (error) {
            report(error, entry, true)
          }
          return
        }
      } catch (error) {
        report(error, entry)
      }
      if (current(entry) && !entry.receipt) {
        await new Promise<void>(resolve => {
          entry.wake = resolve
          entry.timer = setTimeout(() => wake(entry), 1000)
        })
      }
    }
  }

  return {
    requested: (turn: TurnReference): boolean => entries.has(key(turn)),
    request: (turn: TurnReference): Promise<void> => {
      const pending = entries.get(key(turn))
      if (pending) {
        wake(pending)
        return pending.task
      }
      if (closed || !options.isCurrent()) {
        return Promise.resolve()
      }
      const entry: CancellationEntry = {
        turn,
        controller: new AbortController(),
        task: Promise.resolve(),
        reported: false,
        cancelAccepted: false
      }
      entries.set(key(turn), entry)
      entry.task = reconcile(entry)
      return entry.task
    },
    observe: (turn: TurnReference, receipt: TurnReceipt): void => {
      const entry = entries.get(key(turn))
      if (!entry || !current(entry) || receipt.request_id !== turn.requestId) {
        return
      }
      entry.receipt = receipt
      wake(entry)
      if (receipt.status === 'accepted' || receipt.status === 'running') {
        void cancel(entry)
      }
    },
    rejected: (turn: TurnReference, error: unknown): void => {
      const entry = entries.get(key(turn))
      if (entry && current(entry)) {
        entry.rejection = { error }
        wake(entry)
      }
    },
    settled: (turn: TurnReference): boolean => {
      const entry = entries.get(key(turn))
      if (!entry) {
        return false
      }
      entries.delete(key(turn))
      entry.controller.abort()
      wake(entry)
      return true
    },
    close: (): void => {
      closed = true
      for (const entry of entries.values()) {
        entry.controller.abort()
        wake(entry)
      }
      entries.clear()
    }
  }
}
