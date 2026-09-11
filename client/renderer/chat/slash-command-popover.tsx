import { Terminal } from '@/shared/lib/icons'
import { fuzzyFilterCommands, type ScoredSlashCommand, type SlashCommandMeta } from '@/shared/lib/slash-commands'
import { useStrings } from '@/shared/strings'

export interface SlashCommandPopoverProps {
  /** 当前输入框文本（已 trim，过滤掉空字符串）。 */
  query: string
  /** 当前高亮项索引。 */
  highlightedIndex: number
  /** 选中某条命令时回调（参数：选中的命令 + 触发键 Enter 或 Tab）。 */
  onSelect: (cmd: SlashCommandMeta, source: 'enter' | 'tab' | 'click') => void
  /** 鼠标 hover 高亮项时回调。 */
  onHighlight: (index: number) => void
}

/**
 * 输入框下方浮起的命令自动补全弹层：
 * - 显示 fuzzyFilterCommands(query) 结果
 * - 键盘流：方向键改 highlight（父组件控制），Tab/Enter 选中（父组件决定 source）
 * - 鼠标 hover / click 同样回调
 *
 * 弹层不直接控制选中状态 —— ``highlightedIndex`` 与 ``onSelect`` 由父组件管理，便于在弹层外
 * 监听 Tab / Enter 等快捷键。
 */
export function SlashCommandPopover({
  query,
  highlightedIndex,
  onSelect,
  onHighlight
}: SlashCommandPopoverProps): React.JSX.Element | null {
  const dict = useStrings()
  const items: ScoredSlashCommand[] = fuzzyFilterCommands(query, 8)

  if (items.length === 0) {
    return null
  }

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
            className={
              'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition ' +
              (isHighlighted ? 'bg-fill-hover text-strong' : 'text-body hover:bg-fill-hover')
            }
            key={item.cmd.name}
            onClick={() => onSelect(item.cmd, 'click')}
            onMouseEnter={() => onHighlight(idx)}
            role="option"
            type="button"
          >
            <Terminal className={'mt-0.5 size-3.5 shrink-0 ' + (isHighlighted ? 'text-accent' : 'text-faint')} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-baseline gap-1.5">
                <span className="font-mono text-xs font-semibold">/{item.cmd.name}</span>
                {item.cmd.aliases.length > 0 && (
                  <span className="text-[10px] text-faint">/{item.cmd.aliases.join(' · /')}</span>
                )}
                {item.cmd.requiresConfirmation && (
                  <span className="ml-auto text-[9px] text-amber-400/80">{dict.chat.slash.popoverConfirm}</span>
                )}
              </div>
              <span className="truncate text-[10px] text-muted">{item.cmd.description}</span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
