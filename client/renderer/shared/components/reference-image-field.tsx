import { BTN_SUBTLE } from '@/shared/panel/palette'

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
  pickLabel: string
  removeLabel: string
  replaceLabel?: string
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
