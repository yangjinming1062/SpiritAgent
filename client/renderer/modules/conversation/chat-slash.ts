import {
  type SlashCommandResultPayload,
  SpiritAgentRpcError,
  SpiritAgentRpcErrorCode
} from '@/shared/lib/gateway-protocol'
import { errorMessage } from '@/shared/lib/ipc-error'
import type { SlashCommandMeta } from '@/shared/lib/slash-commands'
import { getStrings } from '@/shared/strings'
import type { SessionMessage } from '@/shared/types/spiritagent'

import {
  $chatSessionId,
  hydrateChatMessages,
  markAssistantTerminal,
  type PendingAttachment,
  pushStatusPill
} from './chat-store'
import { rememberFullHistory } from './session-history-cache'
import { ensureChatSession } from './session-list-store'
import { removeVoicePlayback } from './voice-playback'

function slashErrorToMessage(err: unknown): string {
  const dict = getStrings().chat.slash

  if (err instanceof SpiritAgentRpcError) {
    if (err.code === SpiritAgentRpcErrorCode.SlashConfirmRequired) {
      return dict.confirmRequired
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashBusy) {
      return dict.busyStop
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashGeneric) {
      return dict.genericFailed
    }

    if (err.code === SpiritAgentRpcErrorCode.InvalidParams) {
      const suggestions = (err.data as { suggestions?: string[] } | undefined)?.suggestions

      if (suggestions?.length) {
        return dict.unknownWithSuggestions(suggestions)
      }

      return dict.unknown
    }
  }

  return errorMessage(err) || dict.genericFailed
}

/** 命令发送前的拦截：发送中或挂着附件时不执行，避免「边发图片边清空」歧义。 */
function slashPreCheck(pending: PendingAttachment | null, sending: boolean): string | null {
  const dict = getStrings().chat.slash

  if (sending) {
    return dict.inFlight
  }

  if (pending) {
    return dict.pendingAttachment
  }

  return null
}

/** 单条 slash 命令的执行入口（由 send() 调用）：confirm → RPC → hydrate/pill → 错误映射。需确认的命令每次调用都会弹 window.confirm，以取得 PROTOCOL「Slash 命令」要求的确认。 */
async function executeSlashCommand(
  cmd: SlashCommandMeta,
  args: string[],
  opts: {
    onFinish?: () => void
    onStart?: () => void
    requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  }
): Promise<void> {
  const { requestGateway, onStart, onFinish } = opts

  if (cmd.requiresConfirmation && !window.confirm(getStrings().chat.slash.confirmMessage(cmd.name))) {
    return
  }

  let sid: string | null = null
  // 结果只写回发起命令的会话；已切走时仅更新该会话的历史缓存。
  const isCurrentSession = (): boolean => sid === null || $chatSessionId.get() === sid

  try {
    onStart?.()

    sid = await ensureChatSession()

    const result = await requestGateway<SlashCommandResultPayload>('command.dispatch', {
      args,
      command: cmd.name,
      confirmed: true,
      session_id: sid
    })

    const r = result.result

    if (r.status === 'ok') {
      // hydrate=true 时，payload.messages 已包含服务端写入的 status_cleared / compress_summary marker 行——前端 hydrateChatMessages 后再 pushStatusPill 会产生重复 pill，所以 hydrate 路径只更新消息列表，不再追加 status_command_result。
      if (r.hydrate && r.payload) {
        const raw = r.payload.messages

        if (Array.isArray(raw)) {
          rememberFullHistory(sid, raw as SessionMessage[])

          if (isCurrentSession()) {
            hydrateChatMessages(raw as SessionMessage[])
          }

          if (typeof r.payload.cleared_count === 'number') {
            removeVoicePlayback(sid)
          }
        }
      } else if (isCurrentSession()) {
        pushStatusPill('status_command_result', r.message)
      }
    } else if (isCurrentSession()) {
      markAssistantTerminal({ error: r.message })
    }
  } catch (err) {
    if (isCurrentSession()) {
      markAssistantTerminal({ error: slashErrorToMessage(err) })
    }
  } finally {
    onFinish?.()
  }
}

export { executeSlashCommand, slashPreCheck }
