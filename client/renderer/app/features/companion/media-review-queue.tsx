import { useEffect, useRef, useState } from 'react'

import { $videoPacks, systemActionNames } from '@/modules/character'
import { ChatMediaCard } from '@/modules/conversation'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { createFocusCycleHandler } from '@/shared/lib/focus-cycle'
import { requestResource } from '@/shared/lib/resource-request'
import { BTN_COMPACT, BTN_SUBTLE, SECTION_TITLE, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import type { ApiSchemas } from '../../../../../shared/api.generated'

type PendingMediaReview = ApiSchemas['MediaReviewResponse']

const EMPTY_REVIEWS: PendingMediaReview[] = []

export function MediaReviewQueue(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona
  const slotNames = systemActionNames(dict.living.appearance)
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const modal = useRef<HTMLDivElement>(null)

  const cycleFocus = createFocusCycleHandler(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), video[controls]'
  )

  const { data, error, reload, setData } = useAsyncLoader(() =>
    requestResource('companion.get_pending_visual_media_reviews')
  )

  const rows = data ?? EMPTY_REVIEWS
  const failed = error !== null

  useEffect(() => {
    window.addEventListener('focus', reload)
    const unsubscribe = $videoPacks.listen(reload)

    return () => {
      window.removeEventListener('focus', reload)
      unsubscribe()
    }
  }, [reload])

  useEffect(() => {
    if (open && (document.activeElement === document.body || document.activeElement === trigger.current)) {
      modal.current?.querySelector<HTMLButtonElement>('button')?.focus()
    }
  }, [open, rows])

  const close = (): void => {
    setOpen(false)
    requestAnimationFrame(() => {
      const target =
        trigger.current ??
        document.querySelector<HTMLButtonElement>('[data-directory-navigation] button[aria-current="page"]')

      target?.focus()
    })
  }

  return (
    <>
      {failed ? (
        <button className={BTN_SUBTLE} onClick={reload} type="button">
          {t.mediaReviewLoadFailed}
        </button>
      ) : rows.length > 0 ? (
        <button className={BTN_SUBTLE} onClick={() => setOpen(true)} ref={trigger} type="button">
          {t.mediaReviewPending} · {rows.length}
        </button>
      ) : null}
      {open && (
        <div onKeyDownCapture={event => cycleFocus(event)} ref={modal}>
          <WizardModal
            onClose={close}
            regionId="companion-media-review"
            title={t.mediaReviewTitle}
            widthClass="max-w-2xl"
          >
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <p className={SECTION_TITLE}>{t.mediaReviewTitle}</p>
                <button className={BTN_COMPACT} onClick={reload} type="button">
                  {t.mediaReviewRefresh}
                </button>
              </div>
              {failed && (
                <p className="text-xs text-danger-fg" role="alert">
                  {t.mediaReviewLoadError}
                </p>
              )}
              {!failed && rows.length === 0 && <p className="text-xs text-faint">{t.mediaReviewEmpty}</p>}
              {rows.map(row => {
                const title = Object.entries(slotNames).find(([slot]) => slot === row.system_slot)?.[1] ?? row.title

                return (
                  <div className="space-y-1" key={row.id}>
                    {title && <p className="text-sm text-fg">{title}</p>}
                    <ChatMediaCard
                      item={{ type: row.media_type, url: row.media_url, review_id: String(row.id) }}
                      onReviewed={() => setData(current => current?.filter(item => item.id !== row.id) ?? null)}
                    />
                  </div>
                )
              })}
            </section>
          </WizardModal>
        </div>
      )}
    </>
  )
}
