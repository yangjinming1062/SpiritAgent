import type React from 'react'

import { parseSlashInput } from '@/shared/lib/slash-commands'
import type { SlashCommandMeta } from '@/shared/lib/slash-commands'

interface SlashPopoverItem {
  cmd: SlashCommandMeta
}

interface UseSlashPopoverKeyboardOptions {
  highlightedIndex: number
  isOpen: boolean
  items: ReadonlyArray<SlashPopoverItem>
  onDismiss: () => void
  onHighlightIndexChange: (updater: (i: number) => number) => void
  onSelect: (cmd: SlashCommandMeta, args: string[]) => void
  onSend: () => void
  text: string
}

// 抽象 SlashCommandPopover 弹层打开时的 textarea 键盘交互：
// 方向键改高亮、Tab/Enter 选中、Esc 关闭；弹层未开或无候选时把 Enter 透传给 onSend。
export function useSlashPopoverKeyboard({
  highlightedIndex,
  isOpen,
  items,
  onDismiss,
  onHighlightIndexChange,
  onSelect,
  onSend,
  text
}: UseSlashPopoverKeyboardOptions): (e: React.KeyboardEvent<HTMLElement>) => void {
  return (e: React.KeyboardEvent<HTMLElement>): void => {
    if (isOpen && items.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        onHighlightIndexChange(i => (i + 1) % items.length)

        return
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault()
        onHighlightIndexChange(i => (i - 1 + items.length) % items.length)

        return
      }

      if (e.key === 'Tab') {
        e.preventDefault()
        const item = items[highlightedIndex]

        if (item) {
          onSelect(item.cmd, [])
        }

        return
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        const item = items[highlightedIndex]

        if (item) {
          const args = parseSlashInput(text.trim())?.args ?? []

          onSelect(item.cmd, args)
        }

        return
      }

      if (e.key === 'Escape') {
        e.preventDefault()
        // 仅关闭弹层，保留用户输入文本——Esc 不应该丢弃未发送的草稿。
        onDismiss()

        return
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }
}
