import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'

import {
  type ExternalFocusAuthorizationStatus,
  parseExternalActivation,
  parseExternalFocusAuthorization,
  parseMessage
} from './protocol'

interface ExternalFocusRequest {
  operationId: string
  activationId: string
  revision: number
}

type ForegroundGrantResult = 'allowed' | 'deferred' | 'cancelled'

interface ExternalActivation {
  windowId: string
  continuation: () => boolean
  generation: number
  host: ChildProcessWithoutNullStreams
  nativeOperationId: string | null
  authorizationRevision: number
  bufferedFocus: ExternalFocusRequest | null
  pendingFocus: ExternalFocusRequest | null
  latestFocus: ExternalFocusRequest | null
  authorizing: boolean
  cancelGrant: (() => void) | null
}

interface ActivationRuntime {
  helperPath: string
  request: <T>(command: Record<string, unknown>, timeoutMs: number) => Promise<T>
  log: (message: string) => void
  onWarning?: (reason: string) => void
  isCurrentHost: (host: ChildProcessWithoutNullStreams, generation: number) => boolean
  exited: (process: ChildProcessWithoutNullStreams) => boolean
  waitForExit: (process: ChildProcessWithoutNullStreams, budgetMs: number) => Promise<boolean>
}

/**
 * 外部激活与前台授权：原生窗口激活后，只有同一激活令牌的新规划才能继续取得前台权限。
 * 授权辅助进程与宿主命令通道分开，失败只影响本次点击。
 */
export function createExternalActivation(runtime: ActivationRuntime) {
  let externalActivation: ExternalActivation | null = null
  let foregroundGrantProcess: ChildProcessWithoutNullStreams | null = null

  const current = (activation: ExternalActivation, focus?: ExternalFocusRequest): boolean =>
    externalActivation === activation &&
    runtime.isCurrentHost(activation.host, activation.generation) &&
    activation.continuation() &&
    (focus === undefined || (activation.latestFocus === focus && activation.nativeOperationId === focus.operationId))

  const clear = (): void => {
    const previous = externalActivation
    externalActivation = null
    previous?.cancelGrant?.()
  }

  const requestFocus = async (
    focus: ExternalFocusRequest,
    granted: boolean
  ): Promise<ExternalFocusAuthorizationStatus> => {
    const result = await runtime.request<unknown>(
      { command: 'authorize_external_focus', activation_id: focus.activationId, granted },
      3_000
    )

    return parseExternalFocusAuthorization(result)
  }

  const waitForForegroundGrant = async (
    activation: ExternalActivation,
    focus: ExternalFocusRequest
  ): Promise<boolean> => {
    while (current(activation, focus)) {
      const previous = foregroundGrantProcess

      if (!previous || runtime.exited(previous)) {
        return true
      }

      await new Promise<void>(resolve => {
        const finished = (): void => {
          previous.removeListener('exit', finished)
          previous.removeListener('close', finished)

          if (activation.cancelGrant === finished) {
            activation.cancelGrant = null
          }

          resolve()
        }

        activation.cancelGrant = finished
        previous.once('exit', finished)
        previous.once('close', finished)

        if (runtime.exited(previous) || !current(activation, focus)) {
          finished()
        }
      })
    }

    return false
  }

  const runForegroundGrant = (activation: ExternalActivation): Promise<ForegroundGrantResult> => {
    if (foregroundGrantProcess && !runtime.exited(foregroundGrantProcess)) {
      return Promise.reject(new Error('上次前台授权辅助进程尚未退出。'))
    }

    return new Promise<ForegroundGrantResult>((resolve, reject) => {
      const grant = spawn(runtime.helperPath, ['allow-foreground', String(process.pid), String(activation.host.pid)], {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
      })

      foregroundGrantProcess = grant

      let stdout = ''
      let stderr = ''
      let settled = false

      const finishFailure = (reason: string): void => {
        if (settled) {
          return
        }

        settled = true
        activation.cancelGrant = null

        if (!grant.killed && !runtime.exited(grant)) {
          try {
            grant.kill()
          } catch (error) {
            runtime.log(`[desktop] foreground grant helper termination: ${String(error)}`)
          }
        }

        void runtime
          .waitForExit(grant, 2_000)
          .then(stopped =>
            reject(new Error(stopped ? reason : `${reason}; foreground grant helper could not be stopped`))
          )
      }

      activation.cancelGrant = () => finishFailure('前台授权请求已失效。')
      grant.stdout.setEncoding('utf8')
      grant.stderr.setEncoding('utf8')
      grant.stdout.on('data', (chunk: string) => {
        if (!settled) {
          stdout += chunk

          if (Buffer.byteLength(stdout) > 8192) {
            finishFailure('前台授权辅助进程输出过长。')
          }
        }
      })
      grant.stderr.on('data', (chunk: string) => {
        if (!settled) {
          stderr += chunk

          if (Buffer.byteLength(stderr) > 4096) {
            finishFailure('前台授权辅助进程诊断输出过长。')
          }
        }
      })
      grant.stdin.on('error', error => finishFailure(`前台授权命令通道失败：${error.message}`))
      grant.once('error', error => finishFailure(`前台授权辅助进程无法启动：${error.message}`))
      grant.once('close', code => {
        if (foregroundGrantProcess === grant) {
          foregroundGrantProcess = null
        }

        if (settled) {
          return
        }

        try {
          const event = code === 0 ? parseMessage(stdout.trim()).event : undefined

          if (
            code !== 0 ||
            (event !== 'foreground_allowed' && event !== 'foreground_deferred' && event !== 'foreground_cancelled')
          ) {
            throw new Error(`前台授权辅助进程未确认成功 (${String(code)})${stderr ? `: ${stderr.trim()}` : ''}`)
          }

          settled = true
          activation.cancelGrant = null
          resolve(
            event === 'foreground_allowed' ? 'allowed' : event === 'foreground_deferred' ? 'deferred' : 'cancelled'
          )
        } catch (error) {
          finishFailure(error instanceof Error ? error.message : String(error))
        }
      })
      grant.stdin.end()
    })
  }

  const cancelAuthorization = (activation: ExternalActivation, focus: ExternalFocusRequest): void => {
    if (!current(activation, focus)) {
      return
    }

    clear()
    void runtime
      .request({ command: 'cancel_external_interaction' }, 3_000)
      .catch(failure => runtime.log(`[desktop] cancelling foreground authorization: ${String(failure)}`))
  }

  const failAuthorization = (activation: ExternalActivation, focus: ExternalFocusRequest, error: unknown): void => {
    runtime.log(`[desktop] foreground authorization failed: ${error instanceof Error ? error.message : String(error)}`)

    if (!current(activation, focus)) {
      return
    }

    runtime.onWarning?.('窗口前台授权未确认，请重新点击程序。')
    cancelAuthorization(activation, focus)
  }

  const authorizeFocus = async (activation: ExternalActivation): Promise<void> => {
    if (activation.authorizing) {
      return
    }

    activation.authorizing = true

    try {
      while (current(activation) && activation.pendingFocus) {
        const focus = activation.pendingFocus
        activation.pendingFocus = null

        if (!(await waitForForegroundGrant(activation, focus)) || !current(activation, focus)) {
          continue
        }

        let grant: ForegroundGrantResult

        try {
          grant = await runForegroundGrant(activation)
        } catch (error) {
          if (current(activation, focus)) {
            failAuthorization(activation, focus, error)
          }

          continue
        }

        if (!current(activation, focus)) {
          continue
        }

        if (grant === 'cancelled') {
          cancelAuthorization(activation, focus)

          continue
        }

        try {
          const status = await requestFocus(focus, grant === 'allowed')

          if (!current(activation, focus)) {
            continue
          }

          if (status === 'completed') {
            clear()
          } else if (status === 'deferred' || status === 'stale') {
            // 本轮授权已消费，后续重规划必须由新令牌重新取得权限。
            activation.latestFocus = null
          }
        } catch (error) {
          failAuthorization(activation, focus, error)
        }
      }
    } finally {
      activation.authorizing = false

      if (externalActivation === activation && !current(activation)) {
        clear()
      }
    }
  }

  const queueFocus = (activation: ExternalActivation, focus: ExternalFocusRequest): void => {
    if (
      !current(activation) ||
      activation.nativeOperationId !== focus.operationId ||
      focus.revision <= activation.authorizationRevision
    ) {
      return
    }

    activation.authorizationRevision = focus.revision
    activation.pendingFocus = focus
    activation.latestFocus = focus
    activation.cancelGrant?.()
    void authorizeFocus(activation).catch(error =>
      runtime.log(`[desktop] foreground authorization failed: ${String(error)}`)
    )
  }

  const message = (raw: Record<string, unknown>): void => {
    const activation = externalActivation
    const token = raw.activation_id
    const operationId = raw.operation_id
    const revision = raw.authorization_revision

    if (
      !activation ||
      !current(activation) ||
      raw.window_id !== activation.windowId ||
      typeof token !== 'string' ||
      !token ||
      token.length > 256 ||
      typeof operationId !== 'string' ||
      !operationId ||
      operationId.length > 256 ||
      typeof revision !== 'number' ||
      !Number.isSafeInteger(revision) ||
      revision < 1
    ) {
      return
    }

    const focus: ExternalFocusRequest = { operationId, activationId: token, revision }

    if (activation.nativeOperationId === null) {
      if (
        !activation.bufferedFocus ||
        activation.bufferedFocus.operationId !== operationId ||
        activation.bufferedFocus.revision < revision
      ) {
        activation.bufferedFocus = focus
      }

      return
    }

    queueFocus(activation, focus)
  }

  const activate = async (
    windowId: string,
    allowRemote: boolean,
    continuation: () => boolean,
    generation: number,
    host: ChildProcessWithoutNullStreams | null
  ): Promise<void> => {
    clear()

    if (!host || runtime.exited(host) || host.killed) {
      throw new Error('Desktop helper is not running')
    }

    const activation: ExternalActivation = {
      windowId,
      continuation,
      generation,
      host,
      nativeOperationId: null,
      authorizationRevision: 0,
      bufferedFocus: null,
      pendingFocus: null,
      latestFocus: null,
      authorizing: false,
      cancelGrant: null
    }

    externalActivation = activation

    try {
      const result = await runtime.request<unknown>(
        { command: 'activate_external', window_id: windowId, allow_remote: allowRemote },
        3_000
      )

      if (!current(activation)) {
        if (externalActivation === activation) {
          clear()
        }

        return
      }

      activation.nativeOperationId = parseExternalActivation(result)
      const buffered = activation.bufferedFocus
      activation.bufferedFocus = null

      if (buffered) {
        queueFocus(activation, buffered)
      }
    } catch (error) {
      if (externalActivation === activation) {
        clear()
      }

      throw error
    }
  }

  return {
    activate,
    clear,
    dispose: clear,
    message
  }
}
