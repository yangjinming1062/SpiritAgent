import { log } from '@/shared/lib/log'
import { fileApi } from '@/shared/platform/desktop'
import { getStrings } from '@/shared/strings'
import type { ChatAttachment } from '@protocol'

import type { PendingAttachment } from './chat-runtime-types'

interface ChatAttachmentContent {
  displayText: string
  promptText: string
  displayFallback?: string
  promptFallback?: string
  attachments?: ChatAttachment[]
  displayAttachments?: ChatAttachment[]
}

export const appendLine = (text: string, line: string): string => (text ? `${text}\n${line}` : line)

// 展示文本与模型输入在此分开组装；图片读盘失败仍保留预览，并以文件指令交给工具读取。
export async function prepareChatAttachmentContent(
  text: string,
  pending: PendingAttachment | null
): Promise<ChatAttachmentContent> {
  const submit = getStrings().chat.submit

  switch (pending?.type) {
    case 'image': {
      let dataUrl = pending.value.startsWith('data:') ? pending.value : null

      if (!dataUrl) {
        try {
          dataUrl = await fileApi().readImageForAttach(pending.value)
        } catch (error) {
          log.warn('chat-attachment-content', 'readImageForAttach failed', error)
        }
      }

      if (dataUrl) {
        const attachments: ChatAttachment[] = [{ type: 'image', url: dataUrl }]

        return {
          displayText: text,
          promptText: text,
          promptFallback: submit.promptImage,
          attachments,
          displayAttachments: attachments
        }
      }

      const content = appendLine(text, `@file:${pending.value}`).trim()

      return { displayText: content, promptText: content, displayAttachments: [{ type: 'image', url: pending.value }] }
    }

    case 'video': {
      const attachments: ChatAttachment[] | undefined = pending.url ? [{ type: 'video', url: pending.url }] : undefined

      return {
        displayText: text,
        promptText: text,
        displayFallback: attachments ? '' : submit.displayVideo,
        promptFallback: submit.promptVideo,
        attachments,
        displayAttachments: attachments
      }
    }

    case 'file':
      return {
        displayText: appendLine(text, submit.fileInlinePrefix(pending.fileName, pending.path)),
        promptText: appendLine(text, `@file:${pending.path}`)
      }

    case 'folder':
      return {
        displayText: appendLine(text, submit.folderInlinePrefix(pending.folderName, pending.path)),
        promptText: appendLine(text, `@folder:${pending.path}`)
      }

    default:
      return { displayText: text, promptText: text }
  }
}
