import type { IpcMain } from 'electron'

import { type DesktopGatewayRpcResult, IPC, type IpcInvokeContract } from '@ipc/contracts'

import { SpiritAgentRpcError } from '../../../shared/gateway'
import type { DesktopGateway } from '../backend/gateway'

export function registerGatewayIpc({ ipcMain, gateway }: { ipcMain: IpcMain; gateway: DesktopGateway }): void {
  ipcMain.handle(IPC.invoke.gatewayGetState, (_event, authSessionId: string) => gateway.getState(authSessionId))
  ipcMain.handle(
    IPC.invoke.gatewayRequest,
    async (
      _event,
      payload: Parameters<IpcInvokeContract['spiritagent:gateway:request']>[0]
    ): Promise<DesktopGatewayRpcResult> => {
      try {
        if (typeof payload?.method !== 'string' || !payload.method || typeof payload.authSessionId !== 'string') {
          throw new Error('An authenticated gateway operation is required')
        }

        return { ok: true, result: await gateway.request(payload.authSessionId, payload.method, payload.params) }
      } catch (error) {
        return {
          ok: false,
          error:
            error instanceof SpiritAgentRpcError
              ? { code: error.code, message: error.message, data: error.data }
              : { message: error instanceof Error ? error.message : 'Gateway request failed' }
        }
      }
    }
  )
}
