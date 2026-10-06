import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $systemPresets, fetchSystemPresets, presetDisplayName } from '@/modules/conversation'
import { requestGateway } from '@/shared'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, CapsuleTabs, CHIP, HINT_TEXT, INPUT_CLASS, PanelSelect } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { UserProfileSection } from './user-profile-section'

type MemoryTab = 'active' | 'candidate' | 'invalidated' | 'expired'

// 召回记忆的 context 形如 recall:< 主题 >（后端 memory_namespaces.py），界面只展示主题。
const RECALL_CONTEXT_PREFIX = /^recall:/

const $memoryBrowserTab = atom<MemoryTab>('active')

interface MemoryRow {
  id: number
  basis: 'explicit' | 'inferred' | 'observed' | 'system'
  usage: 'contextual' | 'background'
  reason: string
  expires_at: string | null
  evidence: Array<{ message_id: number; quote: string; stance: 'supports' | 'opposes'; created_at: string }>
  context: string | null
  tags: string | null
  content: string | null
  created_at: string | null
  updated_at: string | null
}

interface MemoryCounts {
  active: number
  candidate: number
  invalidated: number
  expired: number
  user_profile: number
}

interface ListResponse {
  system_preset_id: string
  memories: MemoryRow[]
  counts: MemoryCounts
}

export function MemorySection(): React.ReactElement {
  const presets = useStore($systemPresets)
  const [presetId, setPresetId] = useState('companion')
  const dict = useStrings()
  useEffect(() => {
    void fetchSystemPresets()
  }, [])

  return (
    <section>
      <label className="mb-3 block">
        {dict.settings.memory.presetLabel}
        <PanelSelect
          ariaLabel={dict.settings.memory.presetLabel}
          onChange={next => {
            $memoryBrowserTab.set('active')
            setPresetId(next)
          }}
          options={presets.map(preset => ({ value: preset.id, label: presetDisplayName(dict, preset) }))}
          value={presetId}
          widthClass="w-full"
        />
      </label>
      <ScopedMemorySection key={presetId} presetId={presetId} />
    </section>
  )
}

function ScopedMemorySection({ presetId }: { presetId: string }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory

  const tab = useStore($memoryBrowserTab)

  const [rows, setRows] = useState<MemoryRow[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [userProfileCount, setUserProfileCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [draftById, setDraftById] = useState<Record<number, string>>({})
  const [savingById, setSavingById] = useState<Record<number, boolean>>({})
  // 每次调用 ``load`` 时递增；``load`` 发起更新版本之后才返回的旧响应被丢弃，防止慢响应覆盖已切换到新 tab 的快响应。
  const loadIdRef = useRef(0)

  const load = useCallback(
    async (nextTab: MemoryTab) => {
      const id = ++loadIdRef.current
      setLoading(true)
      setRows([])
      setDraftById({})
      setSavingById({})
      setHint(null)

      try {
        const res = await requestGateway<ListResponse>('memory.list', {
          kind: 'recall',
          status: nextTab,
          system_preset_id: presetId
        })

        if (loadIdRef.current !== id || res.system_preset_id !== presetId) {
          return
        }

        const list = res.memories ?? []
        setRows(list)
        setCounts(res.counts ?? null)
        setUserProfileCount(current => current ?? res.counts?.user_profile ?? null)
        setDraftById(Object.fromEntries(list.map(r => [r.id, r.content ?? ''])))
      } catch (err) {
        if (loadIdRef.current !== id) {
          return
        }

        setHint(t.loadFailedHint)
        notifyError(err, t.loadFailedToast)
      } finally {
        if (loadIdRef.current === id) {
          setLoading(false)
        }
      }
    },
    [presetId, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    void load(tab)

    return () => {
      loadIdRef.current += 1
    }
  }, [tab, load])

  // rows 只在成功后改写；失败回滚只还原 draftById[id]，取点击时闭包捕获的 `rows` 快照里的已存内容。
  const saveRecall = useCallback(
    async (id: number) => {
      const requestId = loadIdRef.current
      const draft = draftById[id] ?? ''
      const prevContent = rows.find(r => r.id === id)?.content ?? ''
      setSavingById(s => ({ ...s, [id]: true }))

      try {
        const updated = await requestGateway<MemoryRow>('memory.update', {
          memory_id: id,
          content: draft,
          system_preset_id: presetId
        })

        if (requestId !== loadIdRef.current) {
          return
        }

        setRows(prev => (tab === 'active' ? prev.map(r => (r.id === id ? updated : r)) : prev.filter(r => r.id !== id)))
        setDraftById(prev => ({ ...prev, [id]: updated.content ?? '' }))

        if (tab !== 'active') {
          setCounts(prev => (prev ? { ...prev, [tab]: Math.max(0, prev[tab] - 1), active: prev.active + 1 } : prev))
        }
      } catch (err) {
        if (requestId !== loadIdRef.current) {
          return
        }

        setDraftById(d => ({ ...d, [id]: prevContent }))
        setHint(t.saveFailedHint)
        notifyError(err, t.saveFailedToast)
      } finally {
        if (requestId === loadIdRef.current) {
          setSavingById(s => {
            const next = { ...s }
            delete next[id]

            return next
          })
        }
      }
    },
    [presetId, draftById, rows, t.saveFailedHint, t.saveFailedToast, tab]
  )

  const del = useCallback(
    async (id: number) => {
      const requestId = loadIdRef.current
      setSavingById(prev => ({ ...prev, [id]: true }))

      try {
        await requestGateway('memory.delete', { memory_id: id, system_preset_id: presetId })

        if (requestId === loadIdRef.current) {
          setRows(prev => prev.filter(r => r.id !== id))
          setCounts(prev => (prev ? { ...prev, [tab]: Math.max(0, prev[tab] - 1) } : prev))
        }
      } catch (err) {
        if (requestId !== loadIdRef.current) {
          return
        }

        setHint(t.deleteFailedHint)
        notifyError(err, t.deleteFailedToast)
      } finally {
        if (requestId === loadIdRef.current) {
          setSavingById(prev => ({ ...prev, [id]: false }))
        }
      }
    },
    [presetId, t.deleteFailedHint, t.deleteFailedToast, tab]
  )

  const switchTab = (next: MemoryTab): void => {
    if (next !== tab) {
      $memoryBrowserTab.set(next)
    }
  }

  return (
    <section>
      {presetId === 'companion' && <UserProfileSection onCount={setUserProfileCount} />}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <CapsuleTabs
          ariaLabel={t.tabAriaLabel}
          onChange={switchTab}
          options={[
            { label: t.tabActive(counts?.active ?? '…'), value: 'active' },
            { label: t.tabCandidate(counts?.candidate ?? '…'), value: 'candidate' },
            { label: t.tabInvalidated(counts?.invalidated ?? '…'), value: 'invalidated' },
            { label: t.tabExpired(counts?.expired ?? '…'), value: 'expired' }
          ]}
          size="sm"
          value={tab}
        />
        <span className={cn(HINT_TEXT, 'ml-auto')}>{t.userProfileHint(userProfileCount ?? '…')}</span>
      </div>

      <p className={cn(HINT_TEXT, 'mb-3')}>{t.maintenanceHint}</p>
      {hint && <p className="mb-2 text-xs text-amber-300/90">{hint}</p>}

      {loading ? (
        <p className="text-xs text-muted">{t.loading}</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted">{t.empty}</p>
      ) : (
        <div className="space-y-2.5">
          {rows.map(r => {
            const tags = parseTags(r.tags)
            const draft = draftById[r.id] ?? ''
            const dirty = draft !== (r.content ?? '')
            const saving = !!savingById[r.id]

            return (
              <div className="liquid-glass-card rounded-2xl p-3.5" key={r.id}>
                <p className={cn(HINT_TEXT, 'mb-2')}>
                  {t.basis[r.basis]} · {t.usage[r.usage]}
                </p>
                {r.reason && <p className={cn(HINT_TEXT, 'mb-2')}>{r.reason}</p>}
                {r.expires_at && (
                  <p className={HINT_TEXT}>
                    {t.expires} {r.expires_at}
                  </p>
                )}
                {r.evidence.length > 0 && (
                  <details className="mb-2 text-xs text-muted">
                    <summary>{t.evidence}</summary>
                    {r.evidence.map((e, i) => (
                      <blockquote className="mt-1 border-l pl-2" key={`${e.message_id}-${i}`}>
                        {t.stance[e.stance]} · {e.created_at}
                        <br />
                        {e.quote}
                      </blockquote>
                    ))}
                  </details>
                )}
                <textarea
                  className={cn(INPUT_CLASS, 'resize-none')}
                  disabled={saving}
                  onChange={e => setDraftById(d => ({ ...d, [r.id]: e.target.value }))}
                  rows={3}
                  value={draft}
                />
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {tags.map(tg => (
                    <span className={CHIP} key={tg}>
                      {tg}
                    </span>
                  ))}
                </div>
                <p className={cn(HINT_TEXT, 'mt-1')}>
                  {r.context?.replace(RECALL_CONTEXT_PREFIX, '') || '—'} · {t.updated} {r.updated_at ?? '—'} ·{' '}
                  {t.charCount(draft.length)}
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    className={BTN_SUBTLE}
                    disabled={saving || !dirty}
                    onClick={() => void saveRecall(r.id)}
                    type="button"
                  >
                    {saving ? t.saving : dict.common.save}
                  </button>
                  <button className={BTN_GHOST} disabled={saving} onClick={() => void del(r.id)} type="button">
                    {t.delete}
                  </button>
                  {!dirty && <span className={cn(HINT_TEXT, 'ml-1')}>{t.saved}</span>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function parseTags(raw: string | null): string[] {
  const parsed = safeJsonParse<unknown>(raw, [])

  return Array.isArray(parsed) ? parsed.map(String) : []
}
