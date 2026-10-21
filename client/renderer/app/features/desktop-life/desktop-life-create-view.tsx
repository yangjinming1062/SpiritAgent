import type { ComponentProps, ReactNode } from 'react'

import type { DesktopVideoSource } from '@/modules/desktop-videos'
import { CreationActionBar, CreationWorkspace, INPUT_CLASS } from '@/shared/panel'
import type { DesktopLifeUiSnapshot, PresentationState } from '@ipc/contracts'

import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE } from './desktop-life-buttons'
import { DesktopLifeHeader } from './desktop-life-header'
import { DesktopLifeReferences } from './desktop-life-materials'
import { DesktopLifeDisplayPicker } from './desktop-life-resolution'
import { DesktopLifeSteps } from './desktop-life-steps'
import { useDesktopLifeStrings } from './desktop-life-strings'
import styles from './desktop-life.module.css'

type Creation = DesktopLifeUiSnapshot['create']
type References = ComponentProps<typeof DesktopLifeReferences>

interface DesktopLifeCreateViewProps {
  create: Creation
  busy: boolean
  status: ReactNode
  materialReady: boolean
  resolutionReady: boolean
  presentation: PresentationState
  selectedScene: References['scene']
  selectedOutfit: References['outfit']
  onBack: () => void
  onSubmit: () => void
  onChange: (change: Partial<Creation>) => void
  onChangeDisplay: (displayId: number) => void
  onPickScene: References['onPickScene']
  onPrepareMaterial: References['onPrepareMaterial']
}

export function DesktopLifeCreateView({
  create,
  busy,
  status,
  materialReady,
  resolutionReady,
  presentation,
  selectedScene,
  selectedOutfit,
  onBack,
  onSubmit,
  onChange,
  onChangeDisplay,
  onPickScene,
  onPrepareMaterial
}: DesktopLifeCreateViewProps): React.JSX.Element {
  const t = useDesktopLifeStrings()

  const creationNameField = (
    <label className={styles.field}>
      {t.nameOptional}
      <input
        className={INPUT_CLASS}
        disabled={busy}
        maxLength={160}
        onChange={event => onChange({ name: event.target.value })}
        placeholder={t.autoNamePlaceholder}
        value={create.name}
      />
    </label>
  )

  const footer = (
    <div className={styles.fixedRegion}>
      <CreationActionBar
        description={
          status ??
          (!resolutionReady
            ? t.screenUnavailable
            : !materialReady
              ? t.creationMissingMaterials
              : create.source === 'ai'
                ? t.creationReady
                : t.uploadReady)
        }
      >
        <button
          aria-atomic="true"
          aria-live="polite"
          className={BTN_PRIMARY}
          disabled={busy || !materialReady || !resolutionReady}
          form="desktop-life-create"
          type="submit"
        >
          {busy
            ? t.preparing
            : create.source === 'ai'
              ? t.generateImage
              : create.source === 'image_upload'
                ? t.uploadImage
                : t.selectVideo}
        </button>
      </CreationActionBar>
    </div>
  )

  return (
    <CreationWorkspace footer={footer} header={<DesktopLifeHeader onBack={onBack} title={t.createSet} />}>
      <div className={styles.content}>
        <p className={styles.intro}>{t.createHint}</p>
        <DesktopLifeSteps step={0} />
        <form
          className={styles.form}
          id="desktop-life-create"
          onSubmit={event => {
            event.preventDefault()
            onSubmit()
          }}
        >
          <section className={styles.section}>
            <h3 className="text-sm font-medium text-strong">{t.creationSource}</h3>
            <div aria-label={t.creationSource} className={styles.sourceChoice} role="group">
              {(['ai', 'image_upload', 'video_upload'] as DesktopVideoSource[]).map(source => (
                <button
                  aria-pressed={create.source === source}
                  className={BTN_SUBTLE}
                  disabled={busy}
                  key={source}
                  onClick={() => onChange({ source })}
                  type="button"
                >
                  {source === 'ai' ? t.useAI : source === 'image_upload' ? t.imageSource : t.videoSource}
                </button>
              ))}
            </div>
            {create.source !== 'ai' && <p className="text-sm text-body">{t.ownMediaHint}</p>}
          </section>
          <DesktopLifeDisplayPicker
            disabled={busy}
            displayId={create.targetDisplayId}
            displays={presentation.displays}
            onChange={onChangeDisplay}
          />
          {create.source === 'ai' && (
            <section className={styles.section}>
              <div>
                <h3 className="text-sm font-medium text-strong">{t.referencesTitle}</h3>
              </div>
              <DesktopLifeReferences
                disabled={busy}
                onPickOutfit={outfitId => onChange({ outfitId })}
                onPickScene={onPickScene}
                onPrepareMaterial={onPrepareMaterial}
                outfit={selectedOutfit}
                outfitId={create.outfitId}
                scene={selectedScene}
                sceneId={create.sceneId}
                sceneLabel={create.sceneLabel}
              />
            </section>
          )}
          {create.source !== 'ai' && creationNameField}
          {create.source === 'ai' && (
            <details
              className="rounded-xl border border-line-hairline p-4"
              open={Boolean(create.name.trim() || create.feedback.trim()) || undefined}
            >
              <summary className="cursor-pointer text-sm font-medium text-strong">{t.advanced}</summary>
              <div className="mt-4 flex flex-col gap-4">
                {creationNameField}
                <label className={styles.field}>
                  {t.idea}
                  <textarea
                    className={`${INPUT_CLASS} min-h-28 resize-y font-normal`}
                    disabled={busy}
                    maxLength={1000}
                    onChange={event => onChange({ feedback: event.target.value })}
                    placeholder={t.ideaExample}
                    rows={4}
                    value={create.feedback}
                  />
                </label>
                {(create.name.trim() || create.feedback.trim()) && (
                  <button
                    className={`${BTN_GHOST} self-start`}
                    disabled={busy}
                    onClick={() => onChange({ name: '', feedback: '' })}
                    type="button"
                  >
                    {t.clearCustomization}
                  </button>
                )}
              </div>
            </details>
          )}
        </form>
      </div>
    </CreationWorkspace>
  )
}
