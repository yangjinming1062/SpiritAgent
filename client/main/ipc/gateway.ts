import {
  type DesktopGatewayEvent,
  type DesktopGatewayRpcResponse,
  type DesktopGatewayRpcResult,
  type DesktopGatewayState,
  IPC,
  type IpcEventChannel,
  type IpcEventContract
} from '@ipc/contracts'
import { BrowserWindow, type IpcMain, type IpcMainInvokeEvent } from 'electron'

import { isSenderWindow } from '../security/ipc-trust'
import { sendToWindow } from '../shared/utils'

export interface GatewayIpcDeps {
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  rememberLog?: (chunk: string) => void
}

export function registerGatewayIpc({ getMainWindow, ipcMain, rememberLog }: GatewayIpcDeps): void {
  let currentGatewayState: DesktopGatewayState = 'idle'
  let nextRequestId = 0
  let gatewayHost: BrowserWindow | null = null
  let detachGatewayHost: (() => void) | null = null

  const pendingRequests = new Map<
    number,
    {
      reject: (err: Error) => void
      resolve: (value: DesktopGatewayRpcResult) => void
      timeout: ReturnType<typeof setTimeout>
    }
  >()

  const rejectAllPending = (reason: string): void => {
    for (const [, req] of pendingRequests) {
      clearTimeout(req.timeout)
      req.reject(new Error(reason))
    }

    pendingRequests.clear()
  }

  const broadcastToProxyWindows = <C extends IpcEventChannel>(
    hostId: number,
    channel: C,
    ...payload: IpcEventContract[C]
  ): void => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed() && !win.webContents.isDestroyed() && win.webContents.id !== hostId) {
        sendToWindow(win, channel, ...payload)
      }
    }
  }

  const setGatewayState = (next: DesktopGatewayState, hostId: number): void => {
    if (next === 'closed' || next === 'error') {
      rejectAllPending(`Gateway connection ${next}`)
    }

    if (next === currentGatewayState) {
      return
    }

    currentGatewayState = next
    rememberLog?.(`[gateway-ipc] state changed: ${next}`)
    broadcastToProxyWindows(hostId, IPC.event.gatewayStateChanged, { state: next })
  }

  const syncGatewayHost = (): BrowserWindow | null => {
    const mainWin = getMainWindow()
    const next = mainWin && !mainWin.isDestroyed() && !mainWin.webContents.isDestroyed() ? mainWin : null

    if (gatewayHost === next) {
      return next
    }

    const previous = gatewayHost
    detachGatewayHost?.()
    detachGatewayHost = null
    gatewayHost = next

    if (previous) {
      setGatewayState('closed', next?.webContents.id ?? -1)
    }

    if (next) {
      const contents = next.webContents
      const hostId = contents.id

      // 崩溃与重载不保证执行渲染层 cleanup，主进程及时终止代理等待。
      const invalidate = (): void => {
        if (gatewayHost === next) {
          setGatewayState('closed', hostId)
        }
      }

      const onDestroyed = (): void => {
        invalidate()

        if (gatewayHost === next) {
          detachGatewayHost?.()
          detachGatewayHost = null
          gatewayHost = null
        }
      }

      contents.on('did-start-loading', invalidate)
      contents.on('render-process-gone', invalidate)
      contents.on('destroyed', onDestroyed)

      detachGatewayHost = () => {
        contents.removeListener('did-start-loading', invalidate)
        contents.removeListener('render-process-gone', invalidate)
        contents.removeListener('destroyed', onDestroyed)
      }
    }

    return next
  }

  // 网关宿主—代理：仅宿主窗（精灵/主窗口）可改状态、灌事件、抢答 RPC。
  const isGatewayHost = (event: { sender: { id: number } }): boolean => {
    return isSenderWindow(event.sender, syncGatewayHost())
  }

  ipcMain.handle(IPC.invoke.gatewayGetState, () => {
    syncGatewayHost()

    return currentGatewayState
  })

  ipcMain.on(IPC.send.gatewayBroadcastState, (event, payload?: { state: DesktopGatewayState }) => {
    if (!isGatewayHost(event)) {
      rememberLog?.(`[gateway-ipc] rejected state broadcast from non-host webContents=${event.sender.id}`)

      return
    }

    setGatewayState(payload?.state ?? 'closed', event.sender.id)
  })

  ipcMain.on(IPC.send.gatewayBroadcastEvent, (event, payload?: { event: DesktopGatewayEvent }) => {
    if (!isGatewayHost(event) || !payload?.event) {
      return
    }

    broadcastToProxyWindows(event.sender.id, IPC.event.gatewayEvent, { event: payload.event })
  })

  ipcMain.handle(
    IPC.invoke.gatewayRequest,
    async (_event: IpcMainInvokeEvent, payload?: { method: string; params?: Record<string, unknown> }) => {
      const mainWin = syncGatewayHost()

      if (!mainWin || mainWin.isDestroyed() || mainWin.webContents.isDestroyed()) {
        throw new Error('SpiritAgent gateway host window is unavailable')
      }

      const method = String(payload?.method ?? '')

      if (!method) {
        throw new Error('Method is required for gateway request')
      }

      const id = ++nextRequestId

      return new Promise<DesktopGatewayRpcResult>((resolve, reject) => {
        const timeout = setTimeout(() => {
          pendingRequests.delete(id)
          reject(new Error(`Gateway request timed out: ${method}`))
        }, 45_000)

        pendingRequests.set(id, { reject, resolve, timeout })

        sendToWindow(mainWin, IPC.event.gatewayRpcDispatch, {
          id,
          method,
          params: payload?.params
        })
      })
    }
  )

  ipcMain.on(IPC.send.gatewayRpcReply, (event, payload?: DesktopGatewayRpcResponse) => {
    if (
      !isGatewayHost(event) ||
      !payload ||
      typeof payload !== 'object' ||
      !Number.isSafeInteger(payload.id) ||
      payload.id <= 0
    ) {
      return
    }

    const pending = pendingRequests.get(payload.id)

    if (!pending) {
      return
    }

    pendingRequests.delete(payload.id)
    clearTimeout(pending.timeout)

    if (payload.ok === true) {
      pending.resolve({ ok: true, result: payload.result })
    } else if (
      payload.ok === false &&
      payload.error &&
      typeof payload.error === 'object' &&
      !Array.isArray(payload.error) &&
      typeof payload.error.message === 'string' &&
      (payload.error.code === undefined ||
        (typeof payload.error.code === 'number' && Number.isFinite(payload.error.code)))
    ) {
      pending.resolve({ error: payload.error, ok: false })
    } else {
      pending.reject(new Error('Gateway host returned an invalid RPC response'))
    }
  })
}
