import type { IpcMain, WebContents } from 'electron'

import { type DesktopPresenceRequest, IPC } from '@ipc/contracts'

import type { DesktopGateway } from '../backend/gateway'

export function registerGatewayIpc({
  ipcMain,
  gateway,
  isPresenceSender
}: {
  ipcMain: IpcMain
  gateway: DesktopGateway
  isPresenceSender: (sender: WebContents) => boolean
}): void {
  ipcMain.handle(IPC.invoke.gatewayGetState, (_event, authSessionId: string) => gateway.getState(authSessionId))
  ipcMain.handle(IPC.invoke.gatewayPresence, (event, request: DesktopPresenceRequest) => {
    if (!isPresenceSender(event.sender) || typeof request?.authSessionId !== 'string') {
      throw new Error('Desktop presence sender is not authorized')
    }

    return gateway.presence(request.authSessionId, request.signal)
  })
}
