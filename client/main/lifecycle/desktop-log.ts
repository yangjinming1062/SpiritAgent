import fs from 'node:fs'
import path from 'node:path'

const DESKTOP_LOG_FLUSH_MS = 120
const DESKTOP_LOG_BUFFER_MAX_CHARS = 64 * 1024
const DESKTOP_LOG_MAX_BYTES = 10 * 1024 * 1024
const DESKTOP_LOG_BACKUP_COUNT = 3
const DESKTOP_LOG_DISCARD_BYTES = DESKTOP_LOG_MAX_BYTES * 4
const MAX_IN_MEMORY_LOGS = 300

interface DesktopLoggerOptions {
  spiritagentHome: string
  isPackaged?: boolean
}

interface DesktopLogger {
  flushAsync: () => Promise<void>
  flushSync: () => void
  logPath: string
  rememberLog: (chunk: unknown) => void
}

export function createDesktopLogger({ spiritagentHome, isPackaged = true }: DesktopLoggerOptions): DesktopLogger {
  const logPath = path.join(spiritagentHome, 'logs', 'desktop.log')
  const logBackupPath = (n: number) => `${logPath}.${n}`

  const inMemoryLogs: string[] = []
  let buffer = ''
  let flushTimer: NodeJS.Timeout | null = null
  let flushPromise = Promise.resolve()

  function planRotation(size: number): Array<[string, string, string?]> {
    if (size < DESKTOP_LOG_MAX_BYTES) {
      return []
    }

    const backups = (n: number) => Array.from({ length: n }, (_, i) => logBackupPath(i + 1))

    if (size > DESKTOP_LOG_DISCARD_BYTES) {
      return [logPath, ...backups(DESKTOP_LOG_BACKUP_COUNT)].map(p => ['rm', p])
    }

    const ops: Array<[string, string, string?]> = [['rm', logBackupPath(DESKTOP_LOG_BACKUP_COUNT)]]

    for (let i = DESKTOP_LOG_BACKUP_COUNT - 1; i >= 1; i--) {
      ops.push(['mv', logBackupPath(i), logBackupPath(i + 1)])
    }

    ops.push(['mv', logPath, logBackupPath(1)])

    return ops
  }

  function rotateSync(): void {
    let size: number

    try {
      size = fs.statSync(logPath).size
    } catch {
      return
    }

    for (const [op, src, dst] of planRotation(size)) {
      try {
        if (op === 'rm') {
          fs.rmSync(src, { force: true })
        } else {
          fs.renameSync(src, dst!)
        }
      } catch {
        // 尽力而为
      }
    }
  }

  async function rotateAsync(): Promise<void> {
    let size: number

    try {
      size = (await fs.promises.stat(logPath)).size
    } catch {
      return
    }

    for (const [op, src, dst] of planRotation(size)) {
      try {
        if (op === 'rm') {
          await fs.promises.rm(src, { force: true })
        } else {
          await fs.promises.rename(src, dst!)
        }
      } catch {
        // 尽力而为
      }
    }
  }

  function flushSync(): void {
    if (!buffer) {
      return
    }

    const chunk = buffer
    buffer = ''

    try {
      fs.mkdirSync(path.dirname(logPath), { recursive: true })
      rotateSync()
      fs.appendFileSync(logPath, chunk)
    } catch {
      // 尽力而为
    }
  }

  function flushAsync(): Promise<void> {
    if (!buffer) {
      return flushPromise
    }

    const chunk = buffer
    buffer = ''

    flushPromise = flushPromise
      .then(async () => {
        await fs.promises.mkdir(path.dirname(logPath), { recursive: true })
        await rotateAsync()
        await fs.promises.appendFile(logPath, chunk)
      })
      .catch(() => {
        // 尽力而为
      })

    return flushPromise
  }

  function scheduleFlush(): void {
    if (flushTimer) {
      return
    }

    flushTimer = setTimeout(() => {
      flushTimer = null
      void flushAsync()
    }, DESKTOP_LOG_FLUSH_MS)
  }

  function rememberLog(chunk: unknown): void {
    const text = String(chunk || '').trim()

    if (!text) {
      return
    }

    if (!isPackaged) {
      const colored = process.stdout.isTTY

      if (colored) {
        process.stdout.write(`\x1b[2m[spiritagent]\x1b[0m ${text}\n`)
      } else {
        process.stdout.write(`[spiritagent] ${text}\n`)
      }
    }

    const lines = text.split(/\r?\n/).map(line => `[spiritagent] ${line}`)
    inMemoryLogs.push(...lines)

    if (inMemoryLogs.length > MAX_IN_MEMORY_LOGS) {
      inMemoryLogs.splice(0, inMemoryLogs.length - MAX_IN_MEMORY_LOGS)
    }

    buffer += `${lines.join('\n')}\n`

    if (buffer.length >= DESKTOP_LOG_BUFFER_MAX_CHARS) {
      if (flushTimer) {
        clearTimeout(flushTimer)
        flushTimer = null
      }

      void flushAsync()

      return
    }

    scheduleFlush()
  }

  return {
    flushAsync,
    flushSync,
    logPath,
    rememberLog
  }
}
