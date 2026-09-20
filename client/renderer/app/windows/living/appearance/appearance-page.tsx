import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $defaultScale,
  $renderMode,
  type CharacterRenderMode,
  hydrateAvatarSeeds,
  hydrateWardrobe,
  setDefaultScale,
  switchRenderMode
} from '@/modules/character'
import { Segmented, Slider } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { ModelSection } from './model-section'
import { ModelSeedWizard } from './model-seed-wizard'
import { VideoSection } from './video-section'

interface ModelSeedWizardState {
  avatarId: number
  supportsMultiview: boolean
}

// 外观页（DESIGN §5.5 / §6.1）：顶栏左侧切换形象分区（模型 / 视频），
// 右侧形象大小为跨分区共享的桌面显示设置。模型分区提供建模状态与形象更新（ModelSection）；
// 视频分区承载逐动作预览、重做与外观参考（着装）管理。外观列表与种子图在页级水合，两种分区共用。
export function AppearancePage(): React.JSX.Element {
  const renderMode = useStore($renderMode)
  const defaultScale = useStore($defaultScale)
  const authKind = useStore($auth).kind
  const t = useStrings().living.appearance
  const [seedWizard, setSeedWizard] = useState<ModelSeedWizardState | null>(null)

  // 外观列表在 auth 就绪后再水合——冷启动直接进入本页时 hydrateAuth 的 IPC 往返
  // 尚未完成，提前调用会因 pending 静默跳过。种子图走本地缓存，缺失时补拉。
  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateWardrobe()
      void hydrateAvatarSeeds()
    }
  }, [authKind])

  // 切模型前先补建模种子图：外观参考立绘的自然站姿不满足建模（A-pose），
  // 且只在这一刻才值得付生图。模型正面缺或（多视角时）背面缺则出向导；
  // 非多视角供应商已有模型正面时直接切换。
  const onRenderModeClick = async (m: CharacterRenderMode): Promise<void> => {
    if (m === 'model' && renderMode !== 'model') {
      try {
        const res = await window.spiritagent.api<{
          id?: number
          model_seed_front_url?: string | null
          model_seed_back_url?: string | null
          supports_multiview?: boolean
        }>({ path: '/api/companion/avatar' })

        if (res.id != null && (!res?.model_seed_front_url || (res?.supports_multiview && !res?.model_seed_back_url))) {
          setSeedWizard({ avatarId: res.id, supportsMultiview: res.supports_multiview === true })

          return
        }
      } catch {
        // 头像行拉取失败时按直接切换处理；缺种子输入会在模型派发处以后端报错暴露，用户可重试。
      }
    }

    void switchRenderMode(m)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 顶栏：形象分区切换 + 形象大小（跨分区共享） */}
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line-hairline px-4 py-2.5">
        <Segmented<CharacterRenderMode>
          onChange={m => void onRenderModeClick(m)}
          options={[
            { value: 'video', label: t.modeVideo },
            { value: 'model', label: t.modeModel }
          ]}
          value={renderMode}
        />
        <div className="flex items-center gap-2.5" title={t.companionSizeHint}>
          <span className="text-[11px] font-medium text-strong">{t.companionSize}</span>
          <div className="w-28">
            <Slider
              ariaLabel={t.scaleAria}
              max={3}
              min={0.3}
              onChange={setDefaultScale}
              step={0.05}
              value={defaultScale}
            />
          </div>
          <span className="w-9 shrink-0 text-right text-xs tabular-nums text-body">
            {String(Number(defaultScale.toFixed(2)))}×
          </span>
        </div>
      </div>

      {renderMode === 'model' ? <ModelSection /> : <VideoSection />}

      {seedWizard != null && (
        <ModelSeedWizard
          avatarId={seedWizard.avatarId}
          onCancel={() => setSeedWizard(null)}
          onConfirm={() => {
            setSeedWizard(null)
            void switchRenderMode('model')
          }}
          supportsMultiview={seedWizard.supportsMultiview}
        />
      )}
    </div>
  )
}
