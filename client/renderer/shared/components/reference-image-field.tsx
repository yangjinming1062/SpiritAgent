import { BTN_SUBTLE } from '@/shared/panel/palette'

/**
 * 参考图字段：缩略图、选择或更换、移除，以及选择失败的行内说明。
 * 只呈现已选状态与回调，选图流程由调用方持有。
 */
export function ReferenceImageField({
  alt,
  busy = false,
  error,
  imageUrl,
  onPick,
  onRemove,
  pickLabel,
  removeLabel,
  replaceLabel,
  thumbClassName = 'h-16 w-16 rounded-lg border border-line-hairline'
}: {
  alt: string
  busy?: boolean
  error?: string | null
  imageUrl: string | null
  onPick: () => void
  onRemove?: () => void
  /** 没有已选参考图时的按钮文案。 */
  pickLabel: string
  removeLabel: string
  /** 已有参考图时的按钮文案。 */
  replaceLabel?: string
  /** 缩略图容器样式。 */
  thumbClassName?: string
}): React.JSX.Element {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {imageUrl && (
          <div className={thumbClassName}>
            <img alt={alt} className="h-full w-full object-contain" src={imageUrl} />
          </div>
        )}
        <button className={BTN_SUBTLE} disabled={busy} onClick={onPick} type="button">
          {imageUrl ? (replaceLabel ?? pickLabel) : pickLabel}
        </button>
        {imageUrl && onRemove ? (
          <button className={BTN_SUBTLE} disabled={busy} onClick={onRemove} type="button">
            {removeLabel}
          </button>
        ) : null}
      </div>
      {error ? (
        <p className="text-xs text-danger-fg" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}
