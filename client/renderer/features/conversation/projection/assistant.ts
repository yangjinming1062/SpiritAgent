import { isResourceId } from '../../../../../shared/resource-id'

import type { ProjectionContext } from './context'

/** 助手回复投影：流式增量、工具状态与完成帧收尾。 */
export function installAssistant(ctx: ProjectionContext): void {
  const { $chatMessageBodies, $chatMessageList, $lastAssistantStreaming, $turnHadBubbleBreak } = ctx.state.stores

  function bindTrailingAssistantMessageId(messageId: string): void {
    // bubble.break 会拆出多段助手气泡，但 DB 只有一行；同一 id 挂到上次用户之后所有未绑定的普通助手行。
    if (!isResourceId(messageId)) {
      return
    }

    const list = $chatMessageList.get()
    const lastUserIndex = list.findLastIndex(item => item.role === 'user')
    const next = list.slice()
    const boundIds = new Set<string>()

    for (let i = lastUserIndex + 1; i < next.length; i++) {
      const item = next[i]

      // 压缩卡片等 subtype 行不是终端助手气泡，不能挂上同一条 message_id。
      if (item.role === 'assistant' && item.backendMessageId === undefined && !item.subtype) {
        next[i] = { ...item, backendMessageId: messageId }
        boundIds.add(item.id)
      }
    }

    if (boundIds.size > 0) {
      ctx.reconcilePersistedMessages(next, boundIds)
    }
  }

  /** 追加一行本地状态行（如 `status_command_result`、`compress_summary`），渲染层按 subtype 显示为居中 pill 或摘要卡片。 */
  function pushStatusPill(subtype: string, text: string, backendMessageId?: string): void {
    if (
      backendMessageId !== undefined &&
      $chatMessageList.get().some(item => item.backendMessageId === backendMessageId)
    ) {
      return
    }

    ctx.appendMessage({ role: 'assistant', subtype, backendMessageId }, { text, streaming: false, toolName: null })
  }

  function setTurnHadBubbleBreak(value: boolean): void {
    $turnHadBubbleBreak.set(value)
  }

  function beginAssistantMessage(): void {
    ctx.inflight.value = null
    const last = ctx.lastAssistantMessage()

    if (last?.body.error && last.body.retryMessageId) {
      $chatMessageBodies.setKey(last.item.id, {
        text: '',
        streaming: true,
        toolName: null,
        requestId: ctx.state.submission.activeRequestId ?? undefined
      })
      $lastAssistantStreaming.set(true)

      return
    }

    if (last?.body.streaming) {
      const { body } = last

      if (!body.text.trim() && !body.toolName && !body.error && !body.cancelled) {
        return
      }

      finalizeAssistantMessage()
    }

    ctx.appendMessage(
      { role: 'assistant' },
      { text: '', streaming: true, toolName: null, requestId: ctx.state.submission.activeRequestId ?? undefined }
    )
    $lastAssistantStreaming.set(true)
  }

  function appendAssistantDelta(text: string): void {
    // 仅更新流式 body 不动 list 引用；首个 delta 过滤前导空行，避免撑大气泡上方。
    ctx.patchLastAssistant(body => {
      const streamingText = (body.streamingText ?? body.text) + text

      return {
        ...body,
        streamingText,
        text: streamingText.trimStart()
      }
    })
  }

  function appendAssistantReasoningDelta(text: string): void {
    ctx.patchLastAssistant(body => ({
      ...body,
      reasoning: !body.reasoning ? text.trimStart() : body.reasoning + text
    }))
  }

  function setAssistantTool(name: string | null): void {
    const last = ctx.ensureAssistantMessage()

    if (!last) {
      return
    }

    const { body, item } = last
    const tools = name && name !== body.tools?.at(-1) ? [...(body.tools ?? []), name] : body.tools

    $chatMessageBodies.setKey(item.id, { ...body, toolName: name, tools })
  }

  function finalizeAssistantMessage(
    text?: string,
    media?: Parameters<ProjectionContext['finalizeAssistantMessage']>[1],
    reasoning?: string
  ): void {
    const last = ctx.lastAssistantMessage()

    if (!last) {
      return
    }

    const { list, item, body } = last

    const rawStr = typeof text === 'string' ? text : (body.streamingText ?? body.text)
    const finalStr = rawStr.trim()
    const finalMedia = media ?? body.media

    const finalReasoning =
      (typeof reasoning === 'string' && reasoning.trim() ? reasoning : body.reasoning)?.trim() || undefined

    const isEmpty =
      !finalStr &&
      !finalReasoning &&
      !body.toolName &&
      !body.error &&
      !body.cancelled &&
      !body.attachments?.length &&
      !finalMedia?.length

    if (isEmpty) {
      $chatMessageList.set(list.slice(0, -1))
      $chatMessageBodies.setKey(item.id, undefined)
      $lastAssistantStreaming.set(false)

      return
    }

    $chatMessageBodies.setKey(item.id, {
      ...body,
      text: finalStr,
      streamingText: undefined,
      reasoning: finalReasoning,
      media: finalMedia,
      streaming: false,
      toolName: null
    })
    $lastAssistantStreaming.set(false)
  }

  Object.assign(ctx, {
    bindTrailingAssistantMessageId,
    pushStatusPill,
    setTurnHadBubbleBreak,
    beginAssistantMessage,
    appendAssistantDelta,
    appendAssistantReasoningDelta,
    setAssistantTool,
    finalizeAssistantMessage
  })
}
