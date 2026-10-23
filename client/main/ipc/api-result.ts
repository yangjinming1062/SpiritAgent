import crypto from 'node:crypto'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { DesktopApiResult, IPC, IpcInvokeContract } from '@ipc/contracts'

import { ApiError } from '../../../shared/errors'

type ApiResultChannel =
  | typeof IPC.invoke.api
  | typeof IPC.invoke.mediaStt
  | typeof IPC.invoke.mediaTts
  | typeof IPC.invoke.mediaAudioUpload
  | typeof IPC.invoke.mediaVoiceSampleUpload
  | typeof IPC.invoke.mediaVideoUpload

type ApiValue<C extends ApiResultChannel> = Extract<Awaited<ReturnType<IpcInvokeContract[C]>>, { ok: true }>['value']

/** HTTP 结果跨 Electron 保持为普通数据，错误实例由 renderer 恢复。 */
export function registerApiResult<C extends ApiResultChannel>(
  ipcMain: IpcMain,
  channel: C,
  handler: (event: IpcMainInvokeEvent, ...args: Parameters<IpcInvokeContract[C]>) => ApiValue<C> | Promise<ApiValue<C>>
): void {
  ipcMain.handle(
    channel,
    async (event, ...args: Parameters<IpcInvokeContract[C]>): Promise<DesktopApiResult<ApiValue<C>>> => {
      try {
        return { ok: true, value: await handler(event, ...args) }
      } catch (error) {
        const business = error instanceof ApiError ? error.business : undefined

        return {
          ok: false,
          status: error instanceof ApiError ? error.status : null,
          error: business ?? {
            code: 'desktop_request_failed',
            message: error instanceof Error ? error.message : 'Request failed',
            details: null,
            request_id: crypto.randomUUID()
          }
        }
      }
    }
  )
}
