import { app, type IpcMain, net, powerMonitor, type WebContents } from 'electron'

import { IPC, type VoiceConnectionRequest } from '@ipc/contracts'

import type { DesktopGateway } from '../backend/gateway'
import { broadcastToAllWindows, sendToSender } from '../shared/utils'

interface OwnedCall extends VoiceConnectionRequest {
  owner: WebContents
  socket: VoiceSocket | null
  timer: ReturnType<typeof setInterval>
  destroyed: () => void
  reject: (error: Error) => void
}

interface VoiceSocket {
  readyState: number
  bufferedAmount: number
  binaryType: string
  send(data: string | Uint8Array): void
  close(code?: number): void
  addEventListener(type: string, listener: (event: { data?: unknown }) => void): void
}

/** 主进程独占媒体连接；票据不离开主进程，帧只交给创建它的窗口。 */
export function registerVoiceCallIpc({
  ipcMain,
  gateway,
  isSender
}: {
  ipcMain: IpcMain
  gateway: DesktopGateway
  isSender: (sender: WebContents) => boolean
}): void {
  let active: OwnedCall | null = null

  function close(failed = false): void {
    const call = active

    if (!call) {
      return
    }

    active = null
    clearInterval(call.timer)
    call.owner.removeListener('destroyed', call.destroyed)
    call.socket?.close(1000)
    call.reject(new Error('Voice connection closed'))
    sendToSender(call.owner, IPC.event.voiceEvent, { kind: 'closed', callKey: call.callKey, failed })
    broadcastToAllWindows(IPC.event.voiceEvent, { kind: 'busy', busy: false })
  }

  ipcMain.handle(IPC.invoke.voiceBusy, () => active !== null)
  ipcMain.handle(IPC.invoke.voiceConnect, async (event, request: VoiceConnectionRequest) => {
    if (
      !isSender(event.sender) ||
      event.senderFrame !== event.sender.mainFrame ||
      typeof request?.authSessionId !== 'string' ||
      !gateway.matchesAuth(request.authSessionId) ||
      typeof request?.sessionId !== 'string' ||
      !/^[1-9]\d{0,18}$/.test(request.sessionId) ||
      typeof request?.callKey !== 'string' ||
      !/^[a-f0-9-]{36}$/.test(request.callKey)
    ) {
      throw new Error('Voice connection is not authorized')
    }

    if (active) {
      throw new Error('A voice call is already active')
    }

    let resolve!: () => void
    let reject!: (error: Error) => void

    const connected = new Promise<void>((yes, no) => {
      resolve = yes
      reject = no
    })

    // URL/token preparation can be cancelled too; attach rejection handling immediately.
    void connected.catch(() => {})
    const started = Date.now()

    const call: OwnedCall = {
      ...request,
      owner: event.sender,
      socket: null,
      reject,
      destroyed: () => close(),
      timer: setInterval(() => {
        if (!gateway.matchesAuth(request.authSessionId) || call.owner.isDestroyed()) {
          close()

          return
        }

        if ((!call.socket || call.socket.readyState === 0) && Date.now() - started > 15000) {
          close(true)
        }
      }, 1000)
    }

    active = call
    event.sender.once('destroyed', call.destroyed)
    broadcastToAllWindows(IPC.event.voiceEvent, { kind: 'busy', busy: true })

    try {
      const url = await gateway.realtimeUrl(request.authSessionId, request.sessionId)

      if (active !== call || !gateway.matchesAuth(request.authSessionId)) {
        throw new Error('Voice connection cancelled')
      }

      const socket = new net.WebSocket(url) as unknown as VoiceSocket
      call.socket = socket
      socket.binaryType = 'arraybuffer'
      socket.addEventListener('open', () => {
        if (active === call) {
          resolve()
        }
      })
      socket.addEventListener('error', () => {
        if (active === call) {
          close(true)
        }
      })
      socket.addEventListener('close', () => {
        if (active === call) {
          close()
        }
      })
      socket.addEventListener('message', event => {
        if (active !== call || !gateway.matchesAuth(call.authSessionId)) {
          return
        }

        const data: unknown = event.data

        if (typeof data !== 'string' && !(data instanceof ArrayBuffer)) {
          close(true)

          return
        }

        if ((typeof data === 'string' ? data.length : data.byteLength) > 65536) {
          close(true)

          return
        }

        sendToSender(call.owner, IPC.event.voiceEvent, {
          kind: 'message',
          callKey: call.callKey,
          data: typeof data === 'string' ? data : new Uint8Array(data)
        })
      })
      await connected
    } catch (error) {
      if (active === call) {
        close(true)
      }

      throw error
    }
  })
  ipcMain.handle(IPC.invoke.voiceSend, (event, callKey: string, data: string | Uint8Array) => {
    const call = active

    if (
      !call ||
      call.owner !== event.sender ||
      event.senderFrame !== event.sender.mainFrame ||
      call.callKey !== callKey ||
      !gateway.matchesAuth(call.authSessionId)
    ) {
      throw new Error('Voice owner changed')
    }

    if (
      (typeof data !== 'string' && !(data instanceof Uint8Array)) ||
      (typeof data === 'string' ? data.length > 4096 : !data.length || data.length > 9600 || data.length % 2 !== 0) ||
      !call.socket ||
      call.socket.readyState !== 1 ||
      call.socket.bufferedAmount > 96000
    ) {
      close(true)
      throw new Error('Voice transport unavailable or overloaded')
    }

    call.socket.send(data)
  })
  ipcMain.handle(IPC.invoke.voiceClose, (event, callKey: string) => {
    if (
      isSender(event.sender) &&
      event.senderFrame === event.sender.mainFrame &&
      active?.owner === event.sender &&
      active.callKey === callKey
    ) {
      close()
    }
  })
  powerMonitor.on('lock-screen', () => close())
  powerMonitor.on('suspend', () => close())
  app.on('before-quit', () => close())
}
