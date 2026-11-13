import { useCallConnection } from '../shared/realtime-call-connection'

import { errorText, type RemoteApi } from './api'

function startupError(error: unknown): string {
  return errorText(error) || '无法开始通话，请检查麦克风权限和连接'
}

function cleanupError(error: unknown): void {
  console.warn('通话音频清理失败', errorText(error))
}

export function useRemoteRealtimeCall({
  api,
  sessionId,
  eligible
}: {
  api: RemoteApi
  sessionId: string | null
  eligible: boolean
}) {
  return useCallConnection({
    scope: eligible ? sessionId : null,
    startupError,
    cleanupError,
    transport: ({ signal, receive, closed }) => {
      let socket: WebSocket | null = null

      return {
        connect: async () => {
          if (!sessionId) {
            throw new Error('未选择通话会话')
          }
          const url = await api.realtimeUrl(sessionId, { signal, timeoutMs: 15_000 })
          signal.throwIfAborted()
          await new Promise<void>((resolve, reject) => {
            const next = new WebSocket(url)
            socket = next
            next.binaryType = 'arraybuffer'
            next.onopen = () => resolve()
            next.onerror = () => reject(new Error('通话连接失败'))
            next.onmessage = (event: MessageEvent<unknown>) => {
              if (typeof event.data === 'string' || event.data instanceof ArrayBuffer) {
                receive(event.data)
              } else {
                closed(true)
              }
            }
            next.onclose = () => {
              reject(new Error('通话连接已关闭'))
              closed()
            }
          })
        },
        send: async data => {
          if (!socket || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 96000) {
            throw new Error('通话连接尚未打开或音频传输积压')
          }
          socket.send(typeof data === 'string' ? data : data.slice().buffer)
        },
        close: async () => {
          socket?.close(1000)
          socket = null
        }
      }
    }
  })
}
