import { cn } from '@/shared/lib/utils'

/** 演出资格徽标：强文字配状态色点与淡色底，深浅主题下都读得出来，也靠颜色区分可/不可。 */
export function PlayBadge({ label, playable }: { label: string; playable: boolean }): React.JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium text-strong',
        playable ? 'bg-success/15' : 'bg-warning/15'
      )}
    >
      <span
        aria-hidden="true"
        className={cn('size-1.5 shrink-0 rounded-full', playable ? 'bg-success' : 'bg-warning')}
      />
      {label}
    </span>
  )
}
