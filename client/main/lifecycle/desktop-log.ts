import fs from 'node:fs'
import path from 'node:path'

const DESKTOP_LOG_FLUSH_MS = 120
const DESKTOP_LOG_BUFFER_MAX_CHARS = 64 * 1024
const DESKTOP_LOG_MAX_BYTES = 10 * 1024 * 1024
const DESKTOP_LOG_BACKUP_COUNT = 3
const DESKTOP_LOG_DISCARD_BYTES = DESKTOP_LOG_MAX_BYTES * 4

interface DesktopLoggerOptions {
  spiritagentHome: string
  isPackaged?: boolean
}

interface DesktopLogger {
  flushSync: () => void
  rememberLog: (chunk: unknown) => void
}

type RotationStep = { from: string; op: 'mv'; to: string } | { op: 'rm'; path: string }

export function createDesktopLogger({ spiritagentHome, isPackaged = true }: DesktopLoggerOptions): DesktopLogger {
  const logPath = path.join(spiritagentHome, 'logs', 'desktop.log')
  const logBackupPath = (n: number) => `${logPath}.${n}`

  let buffer = ''
  let flushTimer: NodeJS.Timeout | null = null
  let flushPromise = Promise.resolve()

  function planRotation(size: number): RotationStep[] {
    if (size < DESKTOP_LOG_MAX_BYTES) {
      return []
    }

    const backups = (n: number) => Array.from({ length: n }, (_, i) => logBackupPath(i + 1))

    if (size > DESKTOP_LOG_DISCARD_BYTES) {
      return [logPath, ...backups(DESKTOP_LOG_BACKUP_COUNT)].map(p => ({ op: 'rm', path: p }))
    }

    const ops: RotationStep[] = [{ op: 'rm', path: logBackupPath(DESKTOP_LOG_BACKUP_COUNT) }]

    for (let i = DESKTOP_LOG_BACKUP_COUNT - 1; i >= 1; i--) {
      ops.push({ from: logBackupPath(i), op: 'mv', to: logBackupPath(i + 1) })
    }

    ops.push({ from: logPath, op: 'mv', to: logBackupPath(1) })

    return ops
  }

  function rotateSync(): void {
    let size: number

    try {
      size = fs.statSync(logPath).size
    } catch {
      return
    }

    for (const step of planRotation(size)) {
      try {
        if (step.op === 'rm') {
          fs.rmSync(step.path, { force: true })
        } else {
          fs.renameSync(step.from, step.to)
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

    for (const step of planRotation(size)) {
      try {
        if (step.op === 'rm') {
          await fs.promises.rm(step.path, { force: true })
        } else {
          await fs.promises.rename(step.from, step.to)
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
    flushSync,
    rememberLog
  }
}
