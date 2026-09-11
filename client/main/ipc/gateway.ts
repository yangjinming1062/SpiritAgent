import { type DesktopGatewayEvent, type DesktopGatewayRpcResponse, type DesktopGatewayState, IPC } from '@ipc/contracts'
import { BrowserWindow, type IpcMain, type IpcMainInvokeEvent } from 'electron'

import { broadcastToAllWindows, sendToMain } from '../shared/utils'

export interface GatewayIpcDeps {
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  rememberLog?: (chunk: string) => void
}

export function registerGatewayIpc({ getMainWindow, ipcMain, rememberLog }: GatewayIpcDeps): void {
  let currentGatewayState: DesktopGatewayState = 'idle'
  let nextRequestId = 0

  const pendingRequests = new Map<
    number,
    {
      reject: (err: Error) => void
      resolve: (value: unknown) => void
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

  // 1. 获取当前网关状态
  ipcMain.handle(IPC.invoke.gatewayGetState, () => currentGatewayState)

  // 2. 主窗口上报网关状态并广播给所有窗口
  ipcMain.on(IPC.send.gatewayBroadcastState, (_event, payload?: { state: DesktopGatewayState }) => {
    const next = payload?.state ?? 'closed'
    currentGatewayState = next
    rememberLog?.(`[gateway-ipc] state changed: ${next}`)

    if (next === 'closed' || next === 'error') {
      rejectAllPending(`Gateway connection ${next}`)
    }

    broadcastToAllWindows(IPC.event.gatewayStateChanged, { state: next })
  })

  // 3. 主窗口收到 WS 业务事件并广播给 Surface 窗口（排除发送方本身）
  ipcMain.on(IPC.send.gatewayBroadcastEvent, (event, payload?: { event: DesktopGatewayEvent }) => {
    if (!payload?.event) {
      return
    }

    for (const win of BrowserWindow.getAllWindows()) {
      if (win.webContents.id !== event.sender.id) {
        sendToMain(win, IPC.event.gatewayEvent, { event: payload.event })
      }
    }
  })

  // 4. 任意 Surface 窗口发送 RPC 请求，经由主窗口的 WebSocket 发出并等待结果
  ipcMain.handle(
    IPC.invoke.gatewayRequest,
    async (_event: IpcMainInvokeEvent, payload?: { method: string; params?: Record<string, unknown> }) => {
      const mainWin = getMainWindow()

      if (!mainWin || mainWin.isDestroyed() || mainWin.webContents.isDestroyed()) {
        throw new Error('SpiritAgent gateway host window is unavailable')
      }

      const method = String(payload?.method ?? '')

      if (!method) {
        throw new Error('Method is required for gateway request')
      }

      const id = ++nextRequestId

      return new Promise<unknown>((resolve, reject) => {
        const timeout = setTimeout(() => {
          pendingRequests.delete(id)
          reject(new Error(`Gateway request timed out: ${method}`))
        }, 45_000)

        pendingRequests.set(id, { reject, resolve, timeout })

        sendToMain(mainWin, IPC.event.gatewayRpcDispatch, {
          id,
          method,
          params: payload?.params
        })
      })
    }
  )

  // 5. 主窗口处理完 RPC 请求后回复结果
  ipcMain.on(IPC.send.gatewayRpcReply, (_event, payload?: DesktopGatewayRpcResponse) => {
    if (!payload || typeof payload.id !== 'number') {
      return
    }

    const pending = pendingRequests.get(payload.id)

    if (!pending) {
      return
    }

    pendingRequests.delete(payload.id)
    clearTimeout(pending.timeout)

    if (payload.ok) {
      pending.resolve(payload.result)
    } else {
      pending.reject(new Error(payload.error || 'Gateway RPC failed'))
    }
  })
}
