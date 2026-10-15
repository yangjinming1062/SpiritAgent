import { useState } from 'react'

import { desktopScreenResolution, type DesktopVideoSet, MAX_DESKTOP_VIDEO_DIMENSION } from '@/modules/desktop-videos'
import { BTN_SUBTLE, INPUT_CLASS } from '@/shared/panel'

import { useDesktopLifeStrings } from './desktop-life-strings'

export function resolutionLabel(width: number, height: number): string {
  let a = width
  let b = height

  while (b) {
    const remainder = a % b
    a = b
    b = remainder
  }

  return `${width} × ${height} · ${width / a}:${height / a}`
}

export function ResolutionFields({
  width,
  height,
  disabled = false,
  onChange
}: {
  width: string
  height: string
  disabled?: boolean
  onChange: (width: string, height: string) => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const [unavailable, setUnavailable] = useState(false)

  return (
    <fieldset className="flex flex-col gap-3" disabled={disabled}>
      <legend className="mb-3 text-sm font-medium text-strong">{t.targetResolution}</legend>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-2 text-sm text-body">
          {t.width}
          <input
            className={INPUT_CLASS}
            max={MAX_DESKTOP_VIDEO_DIMENSION}
            min={1}
            onChange={event => onChange(event.target.value, height)}
            required
            step={1}
            type="number"
            value={width}
          />
        </label>
        <label className="flex flex-col gap-2 text-sm text-body">
          {t.height}
          <input
            className={INPUT_CLASS}
            max={MAX_DESKTOP_VIDEO_DIMENSION}
            min={1}
            onChange={event => onChange(width, event.target.value)}
            required
            step={1}
            type="number"
            value={height}
          />
        </label>
      </div>
      <button
        className={`${BTN_SUBTLE} self-start`}
        onClick={() => {
          const screen = desktopScreenResolution()
          setUnavailable(!screen)

          if (screen) {
            onChange(String(screen.target_width), String(screen.target_height))
          }
        }}
        type="button"
      >
        {t.readScreen}
      </button>
      {unavailable && (
        <p className="text-sm text-warning" role="status">
          {t.screenUnavailable}
        </p>
      )}
    </fieldset>
  )
}

export function DesktopLifeResolutionSettings({
  set,
  busy,
  onSave
}: {
  set: DesktopVideoSet
  busy: boolean
  onSave: (width: number, height: number) => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const [size, setSize] = useState({ width: String(set.target_width), height: String(set.target_height) })
  const blocked = set.actions.some(action => Boolean(action.draft))
  const width = Number(size.width)
  const height = Number(size.height)

  const valid =
    Number.isSafeInteger(width) &&
    width > 0 &&
    width <= MAX_DESKTOP_VIDEO_DIMENSION &&
    Number.isSafeInteger(height) &&
    height > 0 &&
    height <= MAX_DESKTOP_VIDEO_DIMENSION

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={event => {
        event.preventDefault()

        if (valid && !blocked && !busy) {
          onSave(width, height)
        }
      }}
    >
      <ResolutionFields
        disabled={busy || blocked}
        height={size.height}
        onChange={(width, height) => setSize({ width, height })}
        width={size.width}
      />
      <p className="text-xs text-muted">{blocked ? t.resolutionDraftHint : t.resolutionHint}</p>
      {!valid && (
        <p className="text-xs text-warning" role="status">
          {t.resolutionRangeHint(MAX_DESKTOP_VIDEO_DIMENSION)}
        </p>
      )}
      <button
        className={`${BTN_SUBTLE} self-start`}
        disabled={busy || blocked || !valid || (width === set.target_width && height === set.target_height)}
        type="submit"
      >
        {t.saveResolution}
      </button>
    </form>
  )
}
