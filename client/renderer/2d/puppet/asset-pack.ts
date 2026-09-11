import { currentClearEpoch } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { type EdgePosePack, parseEdgePoses } from './edge-pose'

export interface PuppetAssetSource {
  id: number
  manifest_url: string | null
  layer_urls: Record<string, string>
  content_hash: string | null
}

export interface PuppetAssetPack {
  psdUrl: string
  contentHash: string | null
  poses: EdgePosePack | null
}

export async function loadPuppetAssetPack(source: PuppetAssetSource): Promise<PuppetAssetPack> {
  const psdUrl = source.layer_urls.psd

  if (!source.manifest_url || !psdUrl) {
    throw new Error('Missing puppet asset')
  }

  const bytes = await window.spiritagent.apiAssetBuffer({
    url: source.manifest_url,
    contentHash: source.content_hash ? `${source.content_hash}-manifest` : undefined,
    preferCache: true
  })

  const manifest = JSON.parse(new TextDecoder().decode(bytes)) as { kind?: string; schema?: string; poses?: unknown }

  if (manifest.kind !== 'psd' || manifest.schema !== 'spiritagent.2d.psd/1') {
    throw new Error('Unsupported puppet asset')
  }

  return { psdUrl, contentHash: source.content_hash, poses: parseEdgePoses(manifest.poses, source.layer_urls) }
}

export async function cachePuppetAssetPack(source: PuppetAssetSource): Promise<void> {
  const epoch = currentClearEpoch()
  const pack = await loadPuppetAssetPack(source)

  const assets = [
    { url: pack.psdUrl, hash: pack.contentHash ?? undefined },
    ...Object.values(pack.poses ? { left: pack.poses.left, right: pack.poses.right } : {}).flatMap(pose =>
      Object.values(pose.textures)
    )
  ]

  for (const asset of assets) {
    if ($auth.get().kind !== 'authenticated' || epoch !== currentClearEpoch()) {
      return
    }

    await window.spiritagent.apiAssetBuffer({ url: asset.url, contentHash: asset.hash, preferCache: true })
  }
}
