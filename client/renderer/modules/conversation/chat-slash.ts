import { requestResource } from '@/shared/lib/resource-request'
import type { SlashCommandMeta } from '@/shared/lib/slash-commands'
import { confirmAction } from '@/shared/panel'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { ApiError } from '../../../../shared/errors'

import type { ConversationRuntime } from './chat-runtime'
import type { PendingAttachment } from './chat-runtime-types'
import { rememberFullHistory } from './session-history-cache'
import { ensureChatSession } from './session-list-store'
import { removeVoicePlayback } from './voice-playback'

function slashErrorToMessage(err: unknown): string {
  const dict = getStrings().chat.slash

  if (err instanceof ApiError) {
    if (err.code === 'confirmation_required') {
      return dict.confirmRequired
    }

    if (err.code === 'turn_busy') {
      return dict.busyStop
    }

    const details = err.business.details

    if (typeof details === 'object' && details !== null && 'suggestions' in details) {
      const suggestions = details.suggestions

      if (Array.isArray(suggestions) && suggestions.every(item => typeof item === 'string') && suggestions.length) {
        return dict.unknownWithSuggestions(suggestions)
      }
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

/** 单条 slash 命令的执行入口（由 send() 调用）：confirm → HTTP → hydrate/pill → 错误映射。需确认的命令每次调用都会弹确认弹窗（confirmAction），以取得 PROTOCOL「Slash 命令」要求的确认。 */
async function executeSlashCommand(
  cmd: SlashCommandMeta,
  args: string[],
  opts: {
    runtime: ConversationRuntime
    onFinish?: () => void
    onStart?: () => void
  }
): Promise<void> {
  const { onStart, onFinish, runtime } = opts
  const { $chatSessionId, hydrateChatMessages, markAssistantTerminal, pushStatusPill } = runtime

  if (cmd.requiresConfirmation) {
    const confirmStrings = getStrings().chat.slash

    if (
      !(await confirmAction({
        confirmLabel: confirmStrings.confirmLabel,
        description: confirmStrings.confirmDescription,
        title: confirmStrings.confirmTitle(cmd.name)
      }))
    ) {
      return
    }
  }

  let sid: string | null = null
  // 结果只写回发起命令的会话；已切走时仅更新该会话的历史缓存。
  const isCurrentSession = (): boolean => runtime.isCurrent() && (sid === null || $chatSessionId.get() === sid)

  try {
    onStart?.()

    sid = await ensureChatSession(runtime)

    const result = await requestResource('sessions.command', {
      path: { session_id: sid },
      body: { args, command: cmd.name, confirmed: true }
    })

    const r = result.result

    if (r.status === 'ok') {
      // 历史修改直接替换消息列表；清空时数组为空，不追加成功提示。
      if (r.hydrate && r.payload && 'messages' in r.payload) {
        const messages = r.payload.messages

        if (messages) {
          rememberFullHistory(sid, messages)

          if (isCurrentSession()) {
            hydrateChatMessages(messages)
          }

          if ('cleared_count' in r.payload) {
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
