import { useClipboard } from '@/shared/hooks/use-clipboard'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Check, Copy, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

/**
 * 复制按钮。复制成功的反馈文案统一为「已复制」，失败统一为「复制失败，请重试」；
 * 名词属于动作标签（「复制提示词」「复制链接」），不写进结果里。
 */
export function CopyButton({
  className,
  label,
  onError,
  text,
  variant = 'icon'
}: {
  className?: string
  /** 复制动作的文案。 */
  label: string
  /** 复制失败时的回调；不传则只保留按钮上的失败状态。 */
  onError?: (error: unknown) => void
  text: string
  variant?: 'icon' | 'text'
}): React.JSX.Element {
  const t = useStrings()
  const { status, copy } = useClipboard()
  const copied = status === 'copied'
  const failed = status === 'failed'

  const onClick = async (): Promise<void> => {
    try {
      await copy(text)
      triggerHaptic('selection')
    } catch (error) {
      // 失败同时给出就地状态与统一通知，两处不再各走各的通道。
      notifyError(error, t.common.copyFailed)
      onError?.(error)
    }
  }

  const Icon = copied ? Check : failed ? X : Copy

  if (variant === 'icon') {
    return (
      <button
        aria-label={copied ? t.common.copied : failed ? t.common.copyFailed : label}
        className={className}
        onClick={() => void onClick()}
        title={copied ? t.common.copied : failed ? t.common.copyFailed : label}
        type="button"
      >
        <Icon className={cn('size-3.5', copied && 'text-emerald-400')} />
      </button>
    )
  }

  return (
    <button className={className} onClick={() => void onClick()} type="button">
      <Icon className="size-3" />
      {copied ? t.common.copied : failed ? t.common.copyFailed : label}
    </button>
  )
}
