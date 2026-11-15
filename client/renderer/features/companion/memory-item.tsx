import { useStore } from '@nanostores/react'
import type React from 'react'
import { type Ref, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import {
  Badge,
  BTN_GHOST,
  BTN_SUBTLE,
  CHIP,
  Disclosure,
  ErrorBanner,
  HINT_TEXT,
  INPUT_CLASS,
  OverflowMenu,
  OverflowMenuAction
} from '@/ui'
import type { CompanionMemoryDraft } from '@client-shared/ipc/ui-state'
import type { MemoryDetail, MemoryItem } from '@protocol'

/** 记忆的计数分桶：与服务端 memory_counts 一致；判定不了时返回 null，调用方回源确认。 */
export function memoryCountBucket(item: { expires_at: null | string; status: string }): null | string {
  const expired = item.expires_at !== null && new Date(item.expires_at).getTime() <= Date.now()

  if (item.status === 'active') {
    return expired ? 'expired' : 'active'
  }

  if (item.status === 'candidate') {
    return expired ? 'expired' : 'candidate'
  }

  if (item.status === 'invalidated') {
    return 'invalidated'
  }

  return null
}

/**
 * 记忆行卡：折叠时只留正文与元信息，展开后在卡内就地编辑。
 * 忘记属于危险动作，折叠态只放在溢出菜单里，展开态单独一行，和保存保持距离。
 */
export function MemoryItemCard({
  busy,
  cardRef,
  draft,
  editing,
  filterStatus,
  onChange,
  onClose,
  onForget,
  onKeepMine,
  onReset,
  onSave,
  onSelect,
  presetId,
  row
}: {
  busy: boolean
  cardRef?: Ref<HTMLElement>
  draft?: CompanionMemoryDraft
  editing: boolean
  filterStatus: string
  onChange: (value: string) => void
  onClose: () => void
  onForget: () => void
  onKeepMine: () => void
  onReset: () => void
  onSave: () => void
  onSelect: () => void
  presetId: string
  row: MemoryItem
}): React.ReactElement {
  const dict = useStrings()
  const persona = dict.settings.persona
  const t = dict.settings.memory
  const locale = useStore($locale)
  const conflict = Boolean(draft && row.content_version !== draft.baseVersion)
  const bucket = memoryCountBucket(row)
  const staleLabel = bucket && bucket !== filterStatus ? t.statusLabels[bucket as keyof typeof t.statusLabels] : null
  const date = row.updated_at ? new Date(row.updated_at).toLocaleDateString(locale) : '—'

  if (!editing) {
    return (
      <article className="liquid-glass-card relative rounded-xl" ref={cardRef}>
        <div className="flex items-start gap-1 py-1 pl-3 pr-1.5">
          <button
            aria-expanded={false}
            className="min-w-0 flex-1 rounded-lg py-1.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
            data-memory-id={row.id}
            onClick={onSelect}
            type="button"
          >
            <p className="line-clamp-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-body">
              {row.content}
            </p>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] leading-4 text-muted">
              <span className="break-words">
                {row.context || '—'} · {date}
              </span>
              {staleLabel ? <Badge>{staleLabel}</Badge> : null}
              {draft ? <span className="text-accent">{dict.common.unsavedChanges}</span> : null}
            </p>
          </button>
          <OverflowMenu label={dict.common.moreActions}>
            <OverflowMenuAction label={persona.memoryEdit} onClick={onSelect} />
            <OverflowMenuAction danger label={persona.memoryForgetAction} onClick={onForget} />
          </OverflowMenu>
        </div>
      </article>
    )
  }

  return (
    <article className="liquid-glass-card rounded-xl outline-1 -outline-offset-1 outline-accent" ref={cardRef}>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="text-[13px] font-medium text-strong" htmlFor={`memory-${row.id}`}>
            {persona.memoryEdit}
          </label>
          <div className="flex items-center gap-2">
            {draft ? <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span> : null}
            <button className={BTN_GHOST} onClick={onClose} type="button">
              {persona.memoryCollapse}
            </button>
          </div>
        </div>
        <textarea
          className={cn(INPUT_CLASS, 'min-h-40 resize-y')}
          disabled={busy}
          id={`memory-${row.id}`}
          onChange={event => onChange(event.target.value)}
          rows={7}
          value={draft?.content ?? row.content ?? ''}
        />
        {filterStatus !== 'active' ? <p className={HINT_TEXT}>{persona.memorySaveHint}</p> : null}
        {conflict ? (
          <div className="space-y-2 rounded-xl border border-line-hairline p-3">
            <p className="text-[11px] text-warning">{persona.memoryLatestContent}</p>
            <p className="whitespace-pre-wrap break-words text-[13px] text-body">{row.content}</p>
            <button className={BTN_SUBTLE} disabled={busy} onClick={onKeepMine} type="button">
              {dict.common.keepMyEdits}
            </button>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            className={BTN_SUBTLE}
            disabled={busy || !draft?.content.trim() || conflict}
            onClick={onSave}
            type="button"
          >
            {busy ? dict.common.saving : filterStatus === 'active' ? dict.common.saveChanges : persona.memorySaveAgain}
          </button>
          <button className={BTN_GHOST} disabled={busy || !draft} onClick={onReset} type="button">
            {dict.common.cancel}
          </button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-hairline pt-2">
          <p className={cn(HINT_TEXT, 'min-w-0 break-words')}>
            {row.context || '—'} · {t.updated} {row.updated_at ?? '—'}
          </p>
          <button className={cn(BTN_GHOST, 'text-danger-fg')} disabled={busy} onClick={onForget} type="button">
            {persona.memoryForgetAction}
          </button>
        </div>
        <MemoryDetails key={`${row.id}:${row.content_version}`} presetId={presetId} row={row} />
      </div>
    </article>
  )
}

function MemoryDetails({ row, presetId }: { row: MemoryItem; presetId: string }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const begin = useAsyncGuard()

  const load = async (): Promise<void> => {
    if (detail || loading) {
      return
    }

    const isLive = begin()
    setLoading(true)
    setFailed(false)

    try {
      const result = await requestResource('resource_catalog.get_memory', {
        path: { memory_id: row.id },
        query: { system_preset_id: presetId }
      })

      if (isLive()) {
        setDetail(result)
      }
    } catch (error) {
      if (isLive()) {
        setFailed(true)
        notifyError(error, t.loadFailedToast)
      }
    } finally {
      if (isLive()) {
        setLoading(false)
      }
    }
  }

  return (
    <Disclosure
      bodyClassName="space-y-2"
      className="border-t border-line-hairline pt-3 text-[11px] text-muted"
      onOpenChange={open => {
        if (open) {
          void load()
        }
      }}
      title={t.evidence}
      variant="inline"
    >
      <p>
        {t.basis[row.basis]} · {t.usage[row.usage]}
      </p>
      {row.reason ? <p className="whitespace-pre-wrap break-words">{row.reason}</p> : null}
      {row.expires_at ? (
        <p>
          {t.expires} {row.expires_at}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-1">
        {row.tags.map(tag => (
          <span className={CHIP} key={tag}>
            {tag}
          </span>
        ))}
      </div>
      {loading ? <p>{t.loading}</p> : null}
      {failed ? (
        <ErrorBanner message={t.loadFailedHint} onRetry={() => void load()} retryLabel={dict.common.retry} />
      ) : null}
      {detail?.evidence.map((entry, index) => (
        <blockquote className="border-l border-line-hairline pl-2" key={`${entry.fingerprint}-${index}`}>
          {entry.stance ? t.stance[entry.stance] : ''} · {entry.created_at}
          <br />
          {entry.quote}
        </blockquote>
      ))}
    </Disclosure>
  )
}
