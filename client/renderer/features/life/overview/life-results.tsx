import type React from 'react'
import { useState } from 'react'

import { openCreation } from '@/app/navigation/app-navigation'
import { InlineMedia } from '@/features/media'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, ErrorBanner, LoadingBlock } from '@/ui'

import { type LifeEpisode, lifeResultKey, lifeResults } from '../../../../../shared/life-trajectory'

function VisualVersionPreview({ id, versionId }: { id: string; versionId: string }): React.JSX.Element {
  const strings = useStrings()

  const { data, isLoading, reload } = useAsyncLoader(() =>
    requestResource('companion_visuals.get_visual_version', { path: { visual_id: id, version_id: versionId } })
  )

  if (isLoading) {
    return <LoadingBlock label={strings.common.loading} />
  }

  if (!data) {
    return <ErrorBanner message={strings.chat.media.loadFailed} onRetry={reload} retryLabel={strings.common.retry} />
  }

  return (
    <InlineMedia
      alt={data.description || data.title}
      audioUrl={null}
      mediaType={data.media_kind}
      url={data.media_url}
    />
  )
}

function LifeVisualResult({ id, versionId }: { id: string; versionId: string }): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const { life: t } = useStrings()

  return (
    <details className="life-notes" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>{t.openVisual}</summary>
      {open && <VisualVersionPreview id={id} versionId={versionId} />}
    </details>
  )
}

export function LifeResultLinks({ reference }: { reference: LifeEpisode['result_ref'] }): React.JSX.Element {
  const { life: t } = useStrings()

  const targets = {
    outfit: { section: 'wardrobe', target: 'outfitId', label: t.openOutfit },
    scene: { section: 'environment', target: 'sceneId', label: t.openScene },
    action: { section: 'actions', target: 'actionId', label: t.openAction }
  } as const

  return (
    <>
      {lifeResults(reference).map(link => {
        if (link.kind === 'visual') {
          return <LifeVisualResult id={link.id} key={lifeResultKey(link)} versionId={link.versionId} />
        }

        const { id, kind } = link
        const { section, target, label } = targets[kind]

        return (
          <button
            className={BTN_SUBTLE}
            key={lifeResultKey(link)}
            onClick={() => void openCreation(section, { [target]: id })}
            type="button"
          >
            {label}
          </button>
        )
      })}
    </>
  )
}
