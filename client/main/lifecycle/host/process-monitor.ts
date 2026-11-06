import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'

import { parseMessage } from './protocol'

interface HelperProcessOptions {
  helperPath: string
  journalPath: string
}

interface HelperHandlers {
  message: (message: Record<string, unknown>) => void
  failure: (reason: string) => void
  close: (code: number | null, signal: NodeJS.Signals | null, stderr: string) => void
}

/** 桌面辅助进程的生命周期：启动、超时回收与恢复 helper 单飞。 */
export function createHelperProcesses({ helperPath, journalPath }: HelperProcessOptions) {
  let recoveryProcess: ChildProcessWithoutNullStreams | null = null

  function exited(process: ChildProcessWithoutNullStreams): boolean {
    return process.exitCode !== null || process.signalCode !== null || process.pid === undefined
  }

  function waitForExit(process: ChildProcessWithoutNullStreams, budgetMs: number): Promise<boolean> {
    if (exited(process)) {
      return Promise.resolve(true)
    }

    return new Promise<boolean>(resolve => {
      const complete = (done: boolean): void => {
        clearTimeout(timer)
        process.removeListener('exit', onExit)
        resolve(done)
      }

      const onExit = (): void => complete(true)
      const timer = setTimeout(() => complete(false), budgetMs)
      timer.unref()
      process.once('exit', onExit)
    })
  }

  function launch(mode: 'host' | 'recover', handlers: HelperHandlers): ChildProcessWithoutNullStreams {
    const process = spawn(helperPath, [mode, '--journal', journalPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true
    })

    let buffer = ''
    let stderr = ''
    let failed = false

    const fail = (reason: string): void => {
      if (failed) {
        return
      }

      failed = true
      process.kill()
      handlers.failure(reason)
    }

    process.stdout.setEncoding('utf8')
    process.stderr.setEncoding('utf8')
    process.stderr.on('data', (chunk: string) => {
      stderr = (stderr + chunk).slice(-4096)
    })
    process.stdin.on('error', error => fail(`Desktop command channel failed: ${error.message}`))
    process.on('error', error => fail(`Desktop helper could not start: ${error.message}`))
    process.stdout.on('data', (chunk: string) => {
      if (failed) {
        return
      }

      buffer += chunk

      try {
        for (;;) {
          const newline = buffer.indexOf('\n')

          if (newline < 0) {
            break
          }

          const line = buffer.slice(0, newline)
          buffer = buffer.slice(newline + 1)

          if (Buffer.byteLength(line) > 65_536) {
            throw new Error('Desktop helper output line exceeds 64 KiB')
          }

          handlers.message(parseMessage(line))
        }

        if (Buffer.byteLength(buffer) > 65_536) {
          throw new Error('Desktop helper output line exceeds 64 KiB')
        }
      } catch (error) {
        fail(error instanceof Error ? error.message : String(error))
      }
    })
    process.once('close', (code, signal) => {
      if (!failed && buffer.length > 0) {
        fail('Desktop helper output is incomplete')
      }

      if (!failed) {
        handlers.close(code, signal, stderr.trim())
      }
    })

    return process
  }

  function recoverJournal(): Promise<boolean> {
    if (process.platform !== 'win32') {
      return Promise.resolve(false)
    }

    if (recoveryProcess && !exited(recoveryProcess)) {
      return Promise.reject(new Error('Previous desktop recovery helper has not stopped'))
    }

    return new Promise<boolean>((resolve, reject) => {
      let restored: boolean | undefined
      let failing = false

      const fail = (reason: string): void => {
        if (failing) {
          return
        }

        failing = true
        clearTimeout(timer)

        if (!recovery.killed && !exited(recovery)) {
          recovery.kill()
        }

        void waitForExit(recovery, 2_000).then(stopped => {
          if (stopped) {
            recoveryProcess = null
          }

          reject(new Error(stopped ? reason : `${reason}; recovery helper could not be stopped`))
        })
      }

      const timer = setTimeout(() => {
        fail('Desktop shell recovery timed out')
      }, 8_000)

      timer.unref()

      const recovery = launch('recover', {
        failure: fail,
        message: message => {
          if (message.event === 'failure' && typeof message.reason === 'string') {
            throw new Error(message.reason)
          }

          if (message.event !== 'recovered' || typeof message.restored !== 'boolean' || restored !== undefined) {
            throw new Error('Desktop helper returned an invalid recovery result')
          }

          restored = message.restored
        },
        close: (code, _signal, stderr) => {
          if (failing) {
            return
          }

          clearTimeout(timer)
          recoveryProcess = null

          if (code === 0 && restored !== undefined) {
            resolve(restored)
          } else {
            reject(new Error(`Desktop shell recovery failed (${String(code)})${stderr ? `: ${stderr}` : ''}`))
          }
        }
      })

      recoveryProcess = recovery
      recovery.stdin.end()
    })
  }

  return {
    exited,
    launch,
    recoveryRunning: (): boolean => recoveryProcess !== null && !exited(recoveryProcess),
    recoverJournal,
    waitForExit
  }
}
