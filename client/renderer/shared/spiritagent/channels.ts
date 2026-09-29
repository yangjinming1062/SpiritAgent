import type {
  ChannelListResponse,
  ChannelLoginState,
  ChannelPeerAction,
  ChannelPeersResponse
} from '@/shared/types/spiritagent'

/** IM 通道桥 REST（PROTOCOL「IM 通道」）：设置页以此为状态来源；网关 channel.* 事件只驱动桌面提醒。 */
export function listChannels(): Promise<ChannelListResponse> {
  return window.spiritagent.api<ChannelListResponse>({ path: '/api/channels' })
}

export function startWeixinLogin(): Promise<ChannelLoginState> {
  return window.spiritagent.api<ChannelLoginState>({
    method: 'POST',
    path: '/api/channels/weixin/login'
  })
}

export function getWeixinLoginState(): Promise<ChannelLoginState> {
  return window.spiritagent.api<ChannelLoginState>({ path: '/api/channels/weixin/login' })
}

export function logoutChannel(channel: string): Promise<void> {
  return window.spiritagent.api<void>({
    method: 'POST',
    path: `/api/channels/${channel}/logout`
  })
}

export function listChannelPeers(channel: string): Promise<ChannelPeersResponse> {
  return window.spiritagent.api<ChannelPeersResponse>({ path: `/api/channels/${channel}/peers` })
}

export function actOnChannelPeer(
  channel: string,
  peerId: string,
  action: ChannelPeerAction
): Promise<ChannelPeersResponse> {
  return window.spiritagent.api<ChannelPeersResponse>({
    body: { action },
    method: 'POST',
    path: `/api/channels/${channel}/peers/${encodeURIComponent(peerId)}`
  })
}
