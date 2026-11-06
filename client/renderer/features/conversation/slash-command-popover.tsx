import { Terminal } from '@/shared/lib/icons'
import type { SlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

export interface SlashCommandPopoverProps {
  items: SlashCommandMeta[]
  highlightedIndex: number
  onSelect: (cmd: SlashCommandMeta) => void
  onHighlight: (index: number) => void
}

// 键盘操作和高亮状态由输入框管理。
export function SlashCommandPopover({
  items,
  highlightedIndex,
  onSelect,
  onHighlight
}: SlashCommandPopoverProps): React.JSX.Element {
  const dict = useStrings()

  return (
    <div
      className="absolute bottom-full left-0 right-0 z-40 mb-1 max-h-72 overflow-y-auto rounded-xl border border-line-standard bg-surface-card p-1 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-100"
      role="listbox"
    >
      <div className="px-2 pt-1 pb-1 text-[9px] uppercase tracking-wider text-faint">
        {dict.chat.slash.popoverTitle}
      </div>
      {items.map((item, idx) => {
        const isHighlighted = idx === highlightedIndex

        return (
          <button
            aria-selected={isHighlighted}
            className={cn(
              'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition',
              isHighlighted ? 'bg-fill-hover text-strong' : 'text-body hover:bg-fill-hover'
            )}
            key={item.name}
            onClick={() => onSelect(item)}
            onMouseEnter={() => onHighlight(idx)}
            role="option"
            type="button"
          >
            <Terminal className={cn('mt-0.5 size-3.5 shrink-0', isHighlighted ? 'text-accent' : 'text-faint')} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-baseline gap-1.5">
                <span className="font-mono text-xs font-semibold">/{item.name}</span>
                {item.aliases.length > 0 && (
                  <span className="text-[10px] text-faint">/{item.aliases.join(' · /')}</span>
                )}
                {item.requiresConfirmation && (
                  <span className="ml-auto text-[9px] text-amber-400/80">{dict.chat.slash.popoverConfirm}</span>
                )}
              </div>
              <span className="truncate text-[10px] text-muted">{item.description}</span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
