import { SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $defaultScale,
  $outfits,
  hydrateAvatarSeeds,
  hydrateWardrobe,
  setDefaultScale,
  syncDefaultScale
} from '@/modules/character'
import { Slider } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { OutfitSection } from './outfit-section'
import { VideoSection } from './video-section'

// 外观页按着装 → 动作组织；顶栏只保留桌面显示设置（形象大小）。
export function AppearancePage(): React.JSX.Element {
  const defaultScale = useStore($defaultScale)
  const authKind = useStore($auth).kind
  const outfits = useStore($outfits)
  const t = useStrings().living.appearance
  const [selectedOutfitId, setSelectedOutfitId] = useState<number | null>(null)

  // 外观列表在 auth 就绪后再水合——冷启动直接进入本页时 hydrateAuth 的 IPC 往返
  // 尚未完成，提前调用会因 pending 静默跳过。种子图走本地缓存，缺失时补拉。
  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateWardrobe()
      void hydrateAvatarSeeds()
    }
  }, [authKind])

  useEffect(() => window.spiritagent.sprite.onDefaultScaleChanged(syncDefaultScale), [])

  useEffect(() => {
    if (selectedOutfitId !== null && !outfits.some(outfit => outfit.id === selectedOutfitId)) {
      setSelectedOutfitId(null)
    }
  }, [outfits, selectedOutfitId])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-end gap-4 border-b border-line-hairline px-4 py-2.5">
        <div className="flex items-center gap-2.5" title={t.companionSizeHint}>
          <span className="text-[11px] font-medium text-strong">{t.companionSize}</span>
          <div className="w-28">
            <Slider
              ariaLabel={t.scaleAria}
              max={SPRITE_SCALE_LIMITS.max}
              min={SPRITE_SCALE_LIMITS.min}
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

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {selectedOutfitId === null ? (
          <OutfitSection onSelectOutfit={setSelectedOutfitId} />
        ) : (
          <VideoSection onBack={() => setSelectedOutfitId(null)} outfitId={selectedOutfitId} />
        )}
      </div>
    </div>
  )
}
