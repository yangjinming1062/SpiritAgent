import { BrowserWindow } from 'electron'

import { errorMessage } from '@runtime'

const WARMUP_MS = 1200
const PREPARATION_TIMEOUT_MS = 30000

function silentAudioUrl(): string {
  const sampleRate = 16000
  const samples = Math.round((sampleRate * WARMUP_MS) / 1000)
  const bytes = Buffer.alloc(44 + samples * 2)
  bytes.write('RIFF', 0)
  bytes.writeUInt32LE(bytes.length - 8, 4)
  bytes.write('WAVEfmt ', 8)
  bytes.writeUInt32LE(16, 16)
  bytes.writeUInt16LE(1, 20)
  bytes.writeUInt16LE(1, 22)
  bytes.writeUInt32LE(sampleRate, 24)
  bytes.writeUInt32LE(sampleRate * 2, 28)
  bytes.writeUInt16LE(2, 32)
  bytes.writeUInt16LE(16, 34)
  bytes.write('data', 36)
  bytes.writeUInt32LE(samples * 2, 40)

  return `data:audio/wav;base64,${bytes.toString('base64')}`
}

interface OutputSession {
  ready: Promise<void>
  close: (error: Error) => void
}

/** 一条应用级静音流保持输出设备唤醒；独立于账户、会话视图与桌面模式。 */
export function createAudioOutput(log: (message: string) => void) {
  let current: OutputSession | null = null
  let disposed = false

  async function prepare(): Promise<void> {
    if (disposed) {
      return Promise.reject(new Error('Audio output has been disposed'))
    }

    if (current) {
      return current.ready
    }

    // 此窗口只载入本地静音，无脚本、preload、账户存储或可见界面。
    const win = new BrowserWindow({
      width: 1,
      height: 1,
      show: false,
      focusable: false,
      skipTaskbar: true,
      webPreferences: {
        autoplayPolicy: 'no-user-gesture-required',
        backgroundThrottling: false,
        contextIsolation: true,
        devTools: false,
        javascript: false,
        nodeIntegration: false,
        partition: 'spiritagent-audio-output',
        sandbox: true,
        spellcheck: false
      }
    })

    let resolveReady!: () => void
    let rejectReady!: (error: Error) => void
    let warmed = false
    let warmupTimer: ReturnType<typeof setTimeout> | undefined

    const ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve
      rejectReady = reject
    })

    const close = (error: Error): void => {
      if (current !== owner) {
        return
      }

      current = null
      clearTimeout(warmupTimer)
      clearTimeout(preparationTimer)
      rejectReady(error)

      if (!win.isDestroyed()) {
        win.destroy()
      }

      if (warmed && !disposed) {
        log(`[audio-output] Output stopped: ${errorMessage(error)}`)
      }
    }

    const owner = { ready, close }
    current = owner

    const preparationTimer = setTimeout(
      () => close(new Error('Audio output preparation timed out')),
      PREPARATION_TIMEOUT_MS
    )

    win.webContents.once('media-started-playing', () => {
      warmupTimer = setTimeout(() => {
        if (current === owner) {
          warmed = true
          clearTimeout(preparationTimer)
          resolveReady()
        }
      }, WARMUP_MS)
    })
    win.webContents.on('media-paused', () => close(new Error('Audio output was paused')))
    win.webContents.once('render-process-gone', (_event, details) =>
      close(new Error(`Audio output renderer stopped: ${details.reason}`))
    )
    win.once('closed', () => close(new Error('Audio output window closed')))
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    win.webContents.on('will-navigate', event => event.preventDefault())

    const html =
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src data:">` +
      `<audio autoplay loop src="${silentAudioUrl()}"></audio>`

    void win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`).catch(error => {
      close(new Error(`Audio output load failed: ${errorMessage(error)}`))
    })

    return ready
  }

  function dispose(): void {
    disposed = true
    current?.close(new Error('Audio output closed during application exit'))
  }

  return { prepare, dispose }
}
