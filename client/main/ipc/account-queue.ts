import fsp from 'node:fs/promises'

export interface AccountQueue {
  /** 递增账户代次，等待该账户在途任务与上次清理结束后删除 dir；清理期间的新任务排在其后。 */
  clear: (accountId: string, dir: string) => Promise<void>
  /** 该账户最近一次清理；读取方先等待它，避免读到待删数据。 */
  clearing: (accountId: string) => Promise<void> | undefined
  /** 在 `账户:会话` 队列尾部串行执行；isCurrent 在账户被 clear 后变为 false，任务据此放弃落盘。 */
  enqueue: <T>(accountId: string, sessionId: string, task: (isCurrent: () => boolean) => Promise<T>) => Promise<T>
  /** 等待全部在途任务与清理。 */
  flush: () => Promise<void>
  /** 捕获账户当前代次；账户被 clear 后返回 false。 */
  generation: (accountId: string) => () => boolean
}

// 磁盘缓存按账户代次隔离、按会话串行写入：旧代次任务自行放弃，清理等待在途任务收尾后再删目录。
export function createAccountQueue(): AccountQueue {
  const tails = new Map<string, Promise<unknown>>()
  const epochs = new Map<string, number>()
  const clearing = new Map<string, Promise<void>>()

  function generation(accountId: string): () => boolean {
    const captured = epochs.get(accountId)

    return () => captured === epochs.get(accountId)
  }

  async function enqueue<T>(
    accountId: string,
    sessionId: string,
    task: (isCurrent: () => boolean) => Promise<T>
  ): Promise<T> {
    const key = `${accountId}:${sessionId}`
    const isCurrent = generation(accountId)
    const run = Promise.all([tails.get(key), clearing.get(accountId)]).then(() => task(isCurrent))
    // 队列只保存吞掉失败的尾部，保证后续任务照常排队；调用方等待原任务并收到失败。
    const tail = run.catch(() => {})
    tails.set(key, tail)

    try {
      return await run
    } finally {
      if (tails.get(key) === tail) {
        tails.delete(key)
      }
    }
  }

  function clear(accountId: string, dir: string): Promise<void> {
    epochs.set(accountId, (epochs.get(accountId) ?? 0) + 1)
    const pending = [...tails].filter(([key]) => key.startsWith(`${accountId}:`)).map(([, tail]) => tail)

    const task = Promise.allSettled([...pending, clearing.get(accountId)]).then(() =>
      fsp.rm(dir, { force: true, recursive: true })
    )

    clearing.set(accountId, task)

    return task
  }

  return {
    clear,
    clearing: accountId => clearing.get(accountId),
    enqueue,
    flush: async () => {
      await Promise.all([...tails.values(), ...clearing.values()])
    },
    generation
  }
}
