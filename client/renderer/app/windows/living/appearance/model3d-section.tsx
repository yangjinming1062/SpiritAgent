import { useStore } from '@nanostores/react'
import type React from 'react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { resolvePortraitUrl } from '@/modules/character'
import { Seed3dWizard } from '@/modules/character/rendering/2d'
import { $modelGenState, $modelInfo, hydrateModel, rebuildModel } from '@/modules/character/rendering/3d'
import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, ConfirmDialog, HINT_TEXT } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

// 懒加载：生活空间其余视图不需要拖上 three.js 体积。
const Companion3D = lazy(() => import('@/modules/character/rendering/3d').then(m => ({ default: m.Companion3D })))

interface Model3dSeeds {
  avatarId: number | null
  supportsMultiview: boolean
  frontUrl: string | null
  backUrl: string | null
}

type Model3dStatus = 'ready' | 'generating' | 'failed' | 'missing'

// 外观页 3D 区：左侧舞台实时预览当前 3D 伙伴（生成中/失败态由 Companion3D 自带浮层展示），
// 右侧信息栏提供建模状态、种子图与形象更新入口。3D 形象的穿着在建模时固定
// （后端没有 3D 换装管线），更换穿着需切回 2D 设计。
export function Model3dSection(): React.JSX.Element {
  const modelInfo = useStore($modelInfo)
  const genState = useStore($modelGenState)
  const authKind = useStore($auth).kind
  const t = useStrings().living.appearance

  const [seeds, setSeeds] = useState<Model3dSeeds>({
    avatarId: null,
    supportsMultiview: false,
    frontUrl: null,
    backUrl: null
  })

  const [wizard, setWizard] = useState<{ avatarId: number; supportsMultiview: boolean } | null>(null)
  const [rebuildConfirmOpen, setRebuildConfirmOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [seedsLoading, setSeedsLoading] = useState(false)
  const seedRequestRef = useRef(0)
  const lifetimeRef = useRef(0)

  // 事件驱动的生成状态优先于持久化的模型行——重建进行中即使旧行仍是 succeeded 也按生成中展示。
  const status: Model3dStatus =
    genState === 'generating'
      ? 'generating'
      : genState === 'failed'
        ? 'failed'
        : modelInfo.status === 'succeeded' || genState === 'succeeded'
          ? 'ready'
          : 'missing'

  const statusText =
    status === 'ready'
      ? t.model3dStatusReady
      : status === 'generating'
        ? t.model3dStatusGenerating
        : status === 'failed'
          ? t.model3dStatusFailed
          : t.model3dStatusMissing

  // 拉取建模种子图与头像行；失败返回 null，由调用方决定是否兜底。
  const refreshSeeds = useCallback(async (): Promise<Model3dSeeds | null> => {
    const request = ++seedRequestRef.current
    const epoch = currentClearEpoch()

    const isCurrent = (): boolean =>
      request === seedRequestRef.current && epoch === currentClearEpoch() && $auth.get().kind === 'authenticated'

    setSeedsLoading(true)
    setActionError(null)

    try {
      const result = await authedApi<{
        id?: number
        seed_front_3d_url?: string | null
        seed_back_url?: string | null
        supports_multiview?: boolean
      }>({ path: '/api/companion/avatar' })

      if (!isCurrent() || (!result.ok && result.reason === 'unauth')) {
        return null
      }

      if (!result.ok) {
        throw result.error
      }

      const res = result.value

      if (res?.id == null) {
        throw new Error(t.model3dSeedsLoadFailed)
      }

      const [frontUrl, backUrl] = await Promise.all([
        resolvePortraitUrl(res.seed_front_3d_url),
        resolvePortraitUrl(res.seed_back_url)
      ])

      const next: Model3dSeeds = {
        avatarId: res.id ?? null,
        supportsMultiview: res.supports_multiview === true,
        frontUrl,
        backUrl
      }

      if (!isCurrent()) {
        return null
      }

      setSeeds(next)

      return next
    } catch (err) {
      if (!isCurrent()) {
        return null
      }

      log.warn('appearance-3d', 'load avatar seeds failed', err)
      setActionError(t.model3dSeedsLoadFailed)

      return null
    } finally {
      if (isCurrent()) {
        setSeedsLoading(false)
      }
    }
  }, [t.model3dSeedsLoadFailed])

  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateModel()
      void refreshSeeds()
    }

    return () => {
      seedRequestRef.current += 1
      lifetimeRef.current += 1
    }
  }, [authKind, refreshSeeds])

  // 以当前种子图强制重建模型；生成进度与结果经 WS 事件回流到状态栏与舞台。
  const onRebuild = async (): Promise<void> => {
    if ($modelGenState.get() === 'generating') {
      return
    }

    const epoch = currentClearEpoch()
    const lifetime = lifetimeRef.current
    setActionError(null)

    try {
      await rebuildModel()
    } catch (err) {
      log.warn('appearance-3d', 'rebuild model failed', err)

      if (epoch === currentClearEpoch() && lifetime === lifetimeRef.current) {
        setActionError(err instanceof Error ? err.message : String(err))
      }
    }
  }

  const openWizard = (): void => {
    if (seedsLoading || $modelGenState.get() === 'generating') {
      return
    }

    setActionError(null)

    if (seeds.avatarId != null) {
      setWizard({ avatarId: seeds.avatarId, supportsMultiview: seeds.supportsMultiview })

      return
    }

    // avatarId 缺失（首拉失败或头像行尚未建立）时先补拉一次再尝试进向导；
    // 必须使用本次拉取的结果，此时 React 尚未把新 seeds 渲染进闭包。
    void refreshSeeds().then(next => {
      if (next?.avatarId != null) {
        setWizard({ avatarId: next.avatarId, supportsMultiview: next.supportsMultiview })
      } else {
        log.warn('appearance-3d', 'avatar row unavailable, seed wizard not opened')
      }
    })
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* 左：舞台。Companion3D 占满容器，生成中/失败浮层由组件内部处理。 */}
      <div className="min-h-0 flex-1 p-4">
        <div
          aria-label={t.model3dStageAria}
          className="relative h-full w-full overflow-hidden rounded-xl border border-line-hairline bg-fill-trough"
        >
          <Suspense fallback={null}>
            <Companion3D />
          </Suspense>
        </div>
      </div>

      {/* 右：信息栏 */}
      <div className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-line-hairline px-3.5 py-3">
        <section>
          <h3 className="text-xs font-medium text-strong">{statusText}</h3>
          <p className={cn(HINT_TEXT, 'mt-1')}>{t.mode3dHint}</p>
          {actionError && <p className="mt-2 text-xs text-danger-fg">{actionError}</p>}
        </section>

        <section>
          <h3 className="text-xs font-medium text-strong">{t.model3dSeeds}</h3>
          <div className="mt-2 flex gap-2">
            {(['frontUrl', 'backUrl'] as const).map((key, idx) => {
              const url = seeds[key]
              const label = idx === 0 ? t.model3dSeedFront : t.model3dSeedBack

              return (
                <div className="min-w-0 flex-1" key={key}>
                  <div className="grid aspect-square w-full place-items-center overflow-hidden rounded-lg border border-line-hairline bg-fill-trough">
                    {url ? (
                      <img alt={label} className="h-full w-full object-contain" src={url} />
                    ) : (
                      <span className="px-1 text-center text-[10px] text-faint">{t.model3dSeedMissing}</span>
                    )}
                  </div>
                  <p className="mt-1 truncate text-center text-[10px] text-muted">{label}</p>
                </div>
              )
            })}
          </div>
        </section>

        <section className="space-y-2">
          <button
            className={cn(BTN_PRIMARY, 'w-full')}
            disabled={seedsLoading || status === 'generating' || authKind !== 'authenticated'}
            onClick={openWizard}
            type="button"
          >
            {t.model3dUpdateAction}
          </button>
          <p className={HINT_TEXT}>{t.model3dUpdateDesc}</p>
          {status !== 'generating' ? (
            <button
              className={cn(BTN_SUBTLE, 'w-full')}
              disabled={authKind !== 'authenticated'}
              onClick={() => setRebuildConfirmOpen(true)}
              type="button"
            >
              {t.model3dRebuildAction}
            </button>
          ) : null}
        </section>

        <p className="mt-auto rounded-xl border border-line-hairline bg-surface-card px-3 py-2.5 text-[11px] leading-relaxed text-muted">
          {t.model3dDressingNote}
        </p>
      </div>

      {wizard != null && (
        <Seed3dWizard
          avatarId={wizard.avatarId}
          confirmLabel={t.model3dWizardConfirm}
          onCancel={() => {
            setWizard(null)
            void refreshSeeds()
          }}
          onConfirm={() => {
            setWizard(null)
            void onRebuild()
            void refreshSeeds()
          }}
          supportsMultiview={wizard.supportsMultiview}
        />
      )}

      <ConfirmDialog
        confirmLabel={t.model3dRebuildAction}
        description={t.model3dRebuildBody}
        onConfirm={onRebuild}
        onOpenChange={setRebuildConfirmOpen}
        open={rebuildConfirmOpen}
        title={t.model3dRebuildTitle}
      />
    </div>
  )
}
