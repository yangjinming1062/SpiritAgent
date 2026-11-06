import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { $outfits, deleteOutfit, hydrateWardrobe, retryOutfitDescription } from '@/modules/character'
import { PortraitLightbox } from '@/shared'
import { ArrowLeft } from '@/shared/lib/icons'
import { BTN_GHOST, BTN_SUBTLE, confirmAction, SettingCard, SettingsContent } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

export function OutfitDetail({ outfitId, onBack }: { outfitId: string; onBack: () => void }): React.JSX.Element {
  const outfit = useStore($outfits).find(item => item.id === outfitId)
  const dict = useStrings()
  const t = dict.living.outfit
  const [busy, setBusy] = useState(false)
  const [zoom, setZoom] = useState(false)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const [previewRevision, setPreviewRevision] = useState(0)

  const run = (operation: () => Promise<void>): void => {
    setBusy(true)
    void operation()
      .catch(error => notifyError(error, t.confirmFailed))
      .finally(() => setBusy(false))
  }

  // 有地址时以图片加载结果为准，尚无地址但有存储路径时提示重新取图。
  const previewFailed = outfit?.fullbodyUrl ? failedUrl === outfit.fullbodyUrl : Boolean(outfit?.fullbodyPath)

  return (
    <SettingsContent>
      <button className={BTN_GHOST} onClick={onBack} type="button">
        <ArrowLeft className="size-4" />
        {dict.common.back}
      </button>
      {outfit && (
        <SettingCard>
          <div className="flex flex-col gap-4 p-4">
            <h2 className="text-sm font-medium">{outfit.name}</h2>
            {outfit.fullbodyUrl && (
              <button className="flex h-80 justify-center" onClick={() => setZoom(true)} type="button">
                <img
                  alt={outfit.name}
                  className="h-full w-full object-contain"
                  key={previewRevision}
                  onError={() => setFailedUrl(outfit.fullbodyUrl)}
                  onLoad={() => setFailedUrl(null)}
                  src={outfit.fullbodyUrl}
                />
              </button>
            )}
            {previewFailed && (
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                onClick={() => {
                  setPreviewRevision(value => value + 1)
                  run(() => hydrateWardrobe({ revalidateAssets: true }))
                }}
                type="button"
              >
                {dict.companion.egg.failed} · {dict.common.retry}
              </button>
            )}
            <p className="whitespace-pre-wrap text-xs text-muted">
              {outfit.description ||
                (outfit.status === 'ready' &&
                (outfit.descriptionStatus === 'pending' || outfit.descriptionStatus === 'processing')
                  ? t.descriptionPending
                  : t.noDescription)}
            </p>
            {outfit.descriptionError && <p className="text-xs text-danger-fg">{outfit.descriptionError}</p>}
            <div className="flex flex-wrap gap-2">
              {outfit.descriptionStatus === 'failed' && (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() => run(() => retryOutfitDescription(outfit.id))}
                  type="button"
                >
                  {t.retryDescription}
                </button>
              )}
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                onClick={() => {
                  void confirmAction({
                    title: t.actions.deleteTitle,
                    description: outfit.name,
                    confirmLabel: t.actions.delete,
                    variant: 'destructive'
                  }).then(confirmed => {
                    if (confirmed) {
                      run(async () => {
                        await deleteOutfit(outfit.id)
                        onBack()
                      })
                    }
                  })
                }}
                type="button"
              >
                {t.actions.delete}
              </button>
            </div>
          </div>
        </SettingCard>
      )}
      {zoom && outfit?.fullbodyUrl && (
        <PortraitLightbox name={outfit.name} onClose={() => setZoom(false)} url={outfit.fullbodyUrl} />
      )}
    </SettingsContent>
  )
}
