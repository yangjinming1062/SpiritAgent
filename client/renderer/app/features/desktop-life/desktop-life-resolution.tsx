import { useStore } from '@nanostores/react'

import { desktopDisplayResolution, desktopVideoMatchesDisplay, type DesktopVideoSet } from '@/modules/desktop-videos'
import { PanelSelect } from '@/shared/panel'
import { $presentation } from '@/shared/store/presentation'
import type { PresentationDisplay } from '@ipc/contracts'

import { resolveDesktopLifeRecommendationDisplay } from './desktop-life-display'
import { useDesktopLifeStrings } from './desktop-life-strings'
import styles from './desktop-life.module.css'

export function resolutionLabel(width: number, height: number): string {
  return `${width} × ${height}`
}

export function DesktopLifeMetadata({ set }: { set: DesktopVideoSet }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const display = resolveDesktopLifeRecommendationDisplay(useStore($presentation))

  return (
    <div className={styles.metadata}>
      <span>
        {t.targetResolution}: {resolutionLabel(set.target_width, set.target_height)}
      </span>
      <DesktopLifeRecommendation display={display} set={set} />
      <span>{display ? `${t.recommendationDisplay}: ${display.label}` : t.recommendationUnavailable}</span>
    </div>
  )
}

export function DesktopLifeDisplayPicker({
  displays,
  displayId,
  disabled,
  onChange
}: {
  displays: PresentationDisplay[]
  displayId: number | null
  disabled: boolean
  onChange: (displayId: number) => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const selected = displays.find(display => display.id === displayId)
  const resolution = desktopDisplayResolution(selected)

  const options = displays.map(display => {
    const size = desktopDisplayResolution(display)

    return {
      value: String(display.id),
      label: `${display.label}${display.isPrimary ? ` · ${t.primaryDisplay}` : ''} · ${size ? `${size.width} × ${size.height}` : t.readingScreen}`
    }
  })

  if (!selected) {
    options.unshift({
      value: displayId === null ? '' : String(displayId),
      label: displayId === null ? t.chooseDisplay : t.displayDisconnected
    })
  }

  return (
    <section className="flex flex-col gap-3">
      <span className="text-sm font-medium text-strong">{t.creationDisplay}</span>
      <PanelSelect
        ariaLabel={t.creationDisplay}
        disabled={disabled || !displays.length}
        onChange={value => {
          if (value && displays.some(display => String(display.id) === value)) {
            onChange(Number(value))
          }
        }}
        options={options}
        value={displayId === null ? '' : String(displayId)}
        widthClass="w-full"
      />
      {resolution ? (
        <p className="text-sm text-muted">
          {t.targetResolution}: {resolutionLabel(resolution.width, resolution.height)}
        </p>
      ) : (
        <p className="text-sm text-warning" role="status">
          {selected ? t.screenUnavailable : t.chooseDisplayHint}
        </p>
      )}
      <p className="text-xs text-muted">{t.fixedResolutionHint}</p>
    </section>
  )
}

export function DesktopLifeRecommendation({
  set,
  display
}: {
  set: Pick<DesktopVideoSet, 'target_width' | 'target_height'>
  display: PresentationDisplay | undefined
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const matches = desktopVideoMatchesDisplay(set, display)

  return (
    <span
      className={`rounded-full px-2 py-1 text-xs ${matches === null ? 'bg-black/5 text-muted' : matches ? 'bg-accent/10 text-accent' : 'bg-warning/10 text-warning'}`}
      title={matches === null ? t.recommendationUnavailable : matches ? t.recommendedHint : t.notRecommendedHint}
    >
      {matches === null ? t.recommendationUnknown : matches ? t.recommended : t.notRecommended}
    </span>
  )
}
