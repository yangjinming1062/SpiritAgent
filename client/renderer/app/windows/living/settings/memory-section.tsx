import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $memoryBrowserTab, type MemoryTab, setMemoryBrowserTab } from '@/modules/character'
import { useGatewayRequest } from '@/shared'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, CapsuleTabs, CHIP, HINT_TEXT, INPUT_CLASS } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

const MAX_AUTO_INJECT_CONTENT_CHARS = 500

interface MemoryRow {
  id: number
  context: string | null
  tags: string | null
  content: string | null
  created_at: string | null
  updated_at: string | null
}

interface MemoryCounts {
  recall: number
  auto_inject: number
  user_profile: number
  interaction_stats: number
  other: number
}

interface ListResponse {
  memories: MemoryRow[]
  counts: MemoryCounts
}

// context 后缀到字典 hint 键的映射；新增槽位时在这里追加。
const AUTO_INJECT_SLOT_HINT_KEYS: ReadonlyArray<{ context: string; label: string; hintKey: string }> = [
  {
    context: 'auto_inject:communication_style',
    label: 'communication style',
    hintKey: 'communicationStyle'
  },
  {
    context: 'auto_inject:rapport_state',
    label: 'rapport state',
    hintKey: 'rapportState'
  },
  {
    context: 'auto_inject:interaction_pattern',
    label: 'interaction pattern',
    hintKey: 'interactionPattern'
  },
  {
    context: 'auto_inject:mood_pattern',
    label: 'mood pattern',
    hintKey: 'moodPattern'
  },
  {
    context: 'auto_inject:relationship_signal',
    label: 'relationship signal',
    hintKey: 'relationshipSignal'
  }
]

// 长期记忆浏览与修正（DESIGN §8）：主动召回 / 自动注入两 tab。
export function MemorySection(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory

  const tab = useStore($memoryBrowserTab)
  const { requestGateway } = useGatewayRequest()

  const [rows, setRows] = useState<MemoryRow[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [draftById, setDraftById] = useState<Record<number, string>>({})
  const [savingById, setSavingById] = useState<Record<number, boolean>>({})
  // 每次调用 ``load`` 时递增；``load`` 发起更新版本之后才返回的旧响应被丢弃，
  // 防止慢响应覆盖已切换到新 tab 的快响应。
  const loadIdRef = useRef(0)

  const load = useCallback(
    async (nextTab: MemoryTab) => {
      const id = ++loadIdRef.current
      setLoading(true)
      setHint(null)

      try {
        const res = await requestGateway<ListResponse>('memory.list', { kind: nextTab })

        if (loadIdRef.current !== id) {
          return
        }

        const list = res?.memories ?? []
        setRows(list)
        setCounts(res?.counts ?? null)
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
    [requestGateway, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    void load(tab)
  }, [tab, load])

  // 函数式 setState 无需镜像 ref 也能拿到上一次的 rows；
  // 回滚分支从点击时闭包捕获的 `rows` 快照里同时还原 rows[i].content 与 draftById[i]。
  const saveRecall = useCallback(
    async (id: number) => {
      const draft = draftById[id] ?? ''
      const prevContent = rows.find(r => r.id === id)?.content ?? ''
      setSavingById(s => ({ ...s, [id]: true }))

      try {
        await requestGateway('memory.update', { memory_id: id, content: draft })
        setRows(prev => prev.map(r => (r.id === id ? { ...r, content: draft } : r)))
      } catch (err) {
        setRows(prev => prev.map(r => (r.id === id ? { ...r, content: prevContent } : r)))
        setDraftById(d => ({ ...d, [id]: prevContent }))
        setHint(t.saveFailedHint)
        notifyError(err, t.saveFailedToast)
      } finally {
        setSavingById(s => {
          const next = { ...s }
          delete next[id]

          return next
        })
      }
    },
    [draftById, requestGateway, rows, t.saveFailedHint, t.saveFailedToast]
  )

  const del = useCallback(
    async (id: number) => {
      const prevRows = rows
      setRows(prev => prev.filter(r => r.id !== id))

      try {
        await requestGateway('memory.delete', { memory_id: id })
      } catch (err) {
        setRows(prevRows)
        setHint(t.deleteFailedHint)
        notifyError(err, t.deleteFailedToast)
      }
    },
    [requestGateway, rows, t.deleteFailedHint, t.deleteFailedToast]
  )

  const switchTab = (next: MemoryTab): void => {
    if (next !== tab) {
      setMemoryBrowserTab(next)
    }
  }

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <CapsuleTabs
          ariaLabel={t.tabAriaLabel}
          onChange={switchTab}
          options={[
            { label: t.tabRecall(counts?.recall ?? '…'), value: 'recall' },
            { label: t.tabAutoInject(counts?.auto_inject ?? '…'), value: 'auto_inject' }
          ]}
          size="sm"
          value={tab}
        />
        <span className={cn(HINT_TEXT, 'ml-auto')}>{t.userProfileHint(counts?.user_profile ?? '…')}</span>
      </div>

      {hint && <p className="mb-2 text-xs text-amber-300/90">{hint}</p>}

      {loading ? (
        <p className="text-xs text-muted">{t.loading}</p>
      ) : tab === 'recall' ? (
        rows.length === 0 ? (
          <p className="text-xs text-muted">{t.emptyRecall}</p>
        ) : (
          <div className="space-y-2.5">
            {rows.map(r => {
              const tags = parseTags(r.tags)
              const draft = draftById[r.id] ?? ''
              const dirty = draft !== (r.content ?? '')
              const saving = !!savingById[r.id]

              return (
                <div className="liquid-glass-card rounded-2xl p-3.5" key={r.id}>
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
                    {r.context ?? '—'} · {t.autoInjectUpdated} {r.updated_at ?? '—'} · {draft.length} chars
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
        )
      ) : (
        <div className="space-y-2.5">
          <p className={HINT_TEXT}>{t.autoInjectIntro(MAX_AUTO_INJECT_CONTENT_CHARS)}</p>
          {AUTO_INJECT_SLOT_HINT_KEYS.map(slot => {
            const row = rows.find(r => r.context === slot.context)
            const draft = row ? (draftById[row.id] ?? row.content ?? '') : ''
            const dirty = !!row && draft !== (row.content ?? '')
            const overLimit = draft.length > MAX_AUTO_INJECT_CONTENT_CHARS
            const saving = !!row && !!savingById[row.id]
            const hintText = t.autoInjectSlotHints[slot.hintKey] ?? ''

            return (
              <div className="liquid-glass-card rounded-2xl p-3.5" key={slot.context}>
                <p className="text-[11px] font-medium text-strong">{slot.label}</p>
                <p className="mb-1.5 mt-0.5 text-[10px] text-muted">{hintText}</p>
                {row ? (
                  <>
                    <textarea
                      className={cn(INPUT_CLASS, 'resize-none')}
                      disabled={saving}
                      onChange={e => setDraftById(d => ({ ...d, [row.id]: e.target.value }))}
                      rows={2}
                      value={draft}
                    />
                    <p className={cn(HINT_TEXT, 'mt-1')}>
                      {t.autoInjectChars(draft.length, MAX_AUTO_INJECT_CONTENT_CHARS)} · {t.autoInjectUpdated}{' '}
                      {row.updated_at ?? '—'}
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        className={BTN_SUBTLE}
                        disabled={saving || !dirty || overLimit}
                        onClick={() => void saveRecall(row.id)}
                        type="button"
                      >
                        {saving ? t.saving : dict.common.save}
                      </button>
                      <button className={BTN_GHOST} disabled={saving} onClick={() => void del(row.id)} type="button">
                        {t.delete}
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="text-[10px] text-faint">{t.autoInjectEmpty}</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function parseTags(raw: string | null): string[] {
  if (!raw) {
    return []
  }

  try {
    const parsed = JSON.parse(raw)

    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}
