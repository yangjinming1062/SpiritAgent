import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { $outfits, hydrateAvatarSeeds, hydrateWardrobe } from '@/modules/character'
import { $auth } from '@/shared/store/auth'

import { OutfitSection } from './outfit-section'
import { VideoSection } from './video-section'

// 衣柜页按着装 → 动作组织；窗口模式显示设置归设置页统一管理。
export function AppearancePage(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const outfits = useStore($outfits)
  const [selectedOutfitId, setSelectedOutfitId] = useState<number | null>(null)

  // 衣柜列表在 auth 就绪后再水合——冷启动直接进入本页时 hydrateAuth 的 IPC 往返尚未完成，提前调用会因 pending 静默跳过。种子图走本地缓存，缺失时补拉。
  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateWardrobe()
      void hydrateAvatarSeeds()
    }
  }, [authKind])

  useEffect(() => {
    if (selectedOutfitId !== null && !outfits.some(outfit => outfit.id === selectedOutfitId)) {
      setSelectedOutfitId(null)
    }
  }, [outfits, selectedOutfitId])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
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
