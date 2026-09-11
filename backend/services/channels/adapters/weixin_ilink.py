import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import secrets
import struct
import time

import httpx
from components import SETTINGS, get_file_path, get_logger, save_file, session_scope
from modules.channels import ChannelBinding, ChannelPeer
from sqlalchemy import select

from ..base import (
    ChannelAdapter,
    ChannelBindingSnapshot,
    ChannelError,
    InboundAttachment,
    InboundMessage,
    OnInbound,
)
from ..registry import register
from ..state import update_binding_status

logger = get_logger(__name__)

# 微信 CDN 媒体下载与上传的基址。
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
# 渠道绑定的会话 ID（marker / session_id）用于 temp-media 所有权校验：每 (binding_id, peer_id) 一段稳定标识。
_CHANNEL_MARKER_PREFIX = "weixin_ilink"

_MEDIA_TYPE_IMAGE = 1
_MEDIA_TYPE_VOICE = 3
_MEDIA_TYPE_FILE = 4
_MEDIA_TYPE_VIDEO = 5

# 微信个人号 Bot API（iLink / ClawBot 协议）基址；确认登录后返回的 baseurl 可能不同，优先用返回值。
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com/"
# 通用请求头与 base_info：channel_version 需跟随官方 iLink SDK 演进（omp-wechat 同源值 2.2.0）。
CHANNEL_VERSION = "2.2.0"
BOT_AGENT = "SpiritAgent/1.0.0"
# 会话过期错误码（getupdates / sendmessage / getconfig 均可能返回；恢复手段只有重新扫码）。
SESSION_EXPIRED = -14

QR_POLL_INTERVAL_SECONDS = 3.0
QR_LOGIN_TIMEOUT_SECONDS = 300.0
# 常规请求（sendmessage/getconfig/登录）默认超时；getupdates 长轮询按配置放宽。
REQUEST_TIMEOUT_SECONDS = 15.0
# typing_ticket 由 getconfig 下发，缓存 ~20h（略短于服务端 24h TTL，避免临界过期）。
TYPING_TICKET_TTL_SECONDS = 20 * 3600


class IlinkSessionExpired(Exception):
    """iLink 会话过期（-14）的内部信号：run 循环据此清凭据转 login_required，不算适配器故障。"""


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def _random_wechat_uin() -> str:
    uint32 = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(uint32).encode()).decode()


def _decode_aes_key(raw: str) -> bytes | None:
    """iLink aes_key 支持三种编码（hex 32 字符 / base64-of-hex / base64-of-raw-bytes）；base64-of-hex 是
    omp-wechat 验证可解密的唯一稳定形态（base64-of-raw 会导致 CDN 403/丢文件），hex 形式尝试兜底。"""
    if not raw:
        return None
    candidates: list[bytes] = []
    for val in (raw, raw.strip()):
        # hex 32
        if len(val) == 32:
            with contextlib.suppress(binascii.Error, ValueError):
                candidates.append(binascii.unhexlify(val))
        try:
            decoded = base64.b64decode(val, validate=True)
            if len(decoded) == 16:
                candidates.append(decoded)
                continue
            if len(decoded) == 32:
                with contextlib.suppress(binascii.Error, ValueError):
                    candidates.append(binascii.unhexlify(decoded.decode()))
        except (binascii.Error, ValueError):
            pass
    return candidates[0] if candidates else None


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密 + PKCS7 拆对齐（iLink CDN 媒体传输加密方案）。"""
    from Crypto.Cipher import AES  # type: ignore[import-not-found]  # pycryptodome 已是 LLM 依赖

    padded = AES.new(key, AES.MODE_ECB).decrypt(ciphertext)
    pad = padded[-1]
    if 1 <= pad <= 16 and padded[-pad:] == bytes([pad]) * pad:
        return padded[:-pad]
    return padded


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES  # type: ignore[import-not-found]

    pad = 16 - len(plaintext) % 16
    return AES.new(key, AES.MODE_ECB).encrypt(plaintext + bytes([pad]) * pad)


def _parse_inbound_item(item: dict) -> dict | None:
    """提取可下载的媒体描述：type=image/voice/file/video 时返 {kind, cdn_url, aes_key, file_name}。"""
    item_type = item.get("type")
    inner = (
        item.get("image_item")
        if item_type == _MEDIA_TYPE_IMAGE
        else item.get("voice_item")
        if item_type == _MEDIA_TYPE_VOICE
        else item.get("file_item")
        if item_type == _MEDIA_TYPE_FILE
        else item.get("video_item")
        if item_type == _MEDIA_TYPE_VIDEO
        else None
    )
    if not isinstance(inner, dict):
        return None
    cdn_url = inner.get("media") or inner.get("aeskey")
    aes_key = inner.get("aes_key")
    if not isinstance(cdn_url, str) or not cdn_url:
        return None
    kind = {2: "image", 3: "voice", 4: "file", 5: "video"}.get(item_type)
    if not kind:
        return None
    return {
        "kind": kind,
        "cdn_url": cdn_url,
        "aes_key": aes_key if isinstance(aes_key, str) else "",
        "file_name": inner.get("file_name") or "",
    }


def _split_text_and_media(item_list: list) -> tuple[str, list[dict]]:
    """把入站 item_list 拆成 (text, media_descs)；媒体描述供后续下载。"""
    parts: list[str] = []
    media: list[dict] = []
    for item in item_list or []:
        item_type = item.get("type")
        if item_type == 1:
            text = (item.get("text_item") or {}).get("text")
            if text:
                parts.append(text)
        elif item_type == _MEDIA_TYPE_IMAGE:
            parts.append("[图片]")
            desc = _parse_inbound_item(item)
            if desc:
                media.append(desc)
        elif item_type == _MEDIA_TYPE_VOICE:
            parts.append((item.get("voice_item") or {}).get("text") or "[语音]")
            desc = _parse_inbound_item(item)
            if desc:
                media.append(desc)
        elif item_type == _MEDIA_TYPE_FILE:
            file_name = (item.get("file_item") or {}).get("file_name") or ""
            parts.append(f"[文件 {file_name}]" if file_name else "[文件]")
            desc = _parse_inbound_item(item)
            if desc:
                media.append(desc)
        elif item_type == _MEDIA_TYPE_VIDEO:
            parts.append("[视频]")
            desc = _parse_inbound_item(item)
            if desc:
                media.append(desc)
    return "\n".join(p for p in parts if p).strip(), media


def _mime_for_kind(kind: str) -> tuple[str, str]:
    if kind == "image":
        return "image/jpeg", "jpg"
    if kind == "voice":
        return "audio/ogg", "ogg"
    if kind == "video":
        return "video/mp4", "mp4"
    return "application/octet-stream", "bin"


async def _materialize_inbound_attachments(
    binding_id: int,
    peer_id: str,
    media_descs: list[dict],
) -> tuple[InboundAttachment, ...]:
    """把 iLink 媒体项下载 + AES-ECB 解密 → temp-media 公网 URL → 转 InboundAttachment。"""
    if not media_descs:
        return ()
    out: list[InboundAttachment] = []
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    try:
        for desc in media_descs:
            cdn_url = desc["cdn_url"]
            key = _decode_aes_key(desc["aes_key"])
            if key is None:
                logger.warning("iLink media missing aes_key", extra={"binding": binding_id, "kind": desc["kind"]})
                continue
            try:
                resp = await client.get(cdn_url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("iLink media download failed", extra={"binding": binding_id, "error": str(e)})
                continue
            ciphertext = resp.content
            try:
                plaintext = _aes_ecb_decrypt(ciphertext, key)
            except Exception as e:
                logger.warning("iLink media decrypt failed", extra={"binding": binding_id, "error": str(e)})
                continue
            content_type, ext = _mime_for_kind(desc["kind"])
            # 微信图片为 JPEG，ISO 媒体魔数识别；其他按 kind 推 MIME。
            if plaintext[:3] == b"\xff\xd8\xff":
                content_type = "image/jpeg"
            elif plaintext[:8] == b"\x89PNG\r\n\x1a\n":
                content_type, ext = "image/png", "png"
            elif plaintext[:4] == b"GIF8":
                content_type, ext = "image/gif", "gif"
            file_id, public_url = save_file(
                plaintext,
                session_id=f"weixin_{binding_id}",
                content_type=content_type,
                ext=ext,
                meta_marker=f"{_CHANNEL_MARKER_PREFIX}:{binding_id}:{peer_id}",
            )
            out.append(
                InboundAttachment(
                    type="image" if desc["kind"] in ("image", "video", "voice") else "file",
                    url=public_url,
                    name=desc.get("file_name") or "",
                ),
            )
    finally:
        await client.aclose()
    return tuple(out)


class WeixinIlinkAdapter(ChannelAdapter):
    """微信 iLink（ClawBot 个人号 Bot API）适配器：QR 扫码登录 + getupdates 长轮询 + reply-only 回复。

    硬约束（协议决定）：回复必须回显入站消息的 context_token（每 peer 缓存最新值并持久化，
    重启免重扫、断线不丢回复凭据）；不能主动发起会话（can_initiate=False）。
    凭据 JSON 形如 {bot_token, baseurl, ilink_user_id, ilink_bot_id, context_tokens{peer→token},
    get_updates_buf, typing_ticket, typing_ticket_ts}。
    """

    channel_name = "weixin_ilink"
    conversation_title = "微信对话"
    supports_typing = True
    can_initiate = False
    requires_login = True

    def __init__(self, snapshot: ChannelBindingSnapshot, on_inbound: OnInbound) -> None:
        super().__init__(snapshot, on_inbound)
        self._creds: dict = _load_credentials(snapshot.credentials)
        self._login_gate = asyncio.Event()
        if self.has_credentials():
            self._login_gate.set()
        self._login_task: asyncio.Task | None = None
        self._login_state: dict = {"state": "connected" if self.has_credentials() else "login_required"}
        self._client: httpx.AsyncClient | None = None

    # ---- 基础 HTTP 层 ---------------------------------------------------------------

    def has_credentials(self) -> bool:
        return bool(self._creds.get("bot_token"))

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS))
        return self._client

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        token = self._creds.get("bot_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _base_url(self) -> str:
        return self._creds.get("baseurl") or DEFAULT_BASE_URL

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        payload: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        url = path if path.startswith("http") else self._base_url().rstrip("/") + "/" + path
        try:
            resp = await self._ensure_client().request(
                method,
                url,
                params=params,
                json=payload,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as e:
            raise ChannelError(f"iLink transport error: {e}", fatal=False) from e
        if resp.status_code >= 500:
            raise ChannelError(f"iLink server error {resp.status_code}", fatal=False)
        if resp.status_code >= 400:
            raise ChannelError(f"iLink auth/request error {resp.status_code}", fatal=False)
        data = resp.json() if resp.content else {}
        ret = data.get("ret")
        errcode = data.get("errcode")
        if ret == SESSION_EXPIRED or errcode == SESSION_EXPIRED:
            raise IlinkSessionExpired()
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            raise ChannelError(f"iLink API error: {data.get('errmsg') or data}", fatal=False)
        return data

    # ---- 登录流 ---------------------------------------------------------------------

    async def start_login(self) -> None:
        if self._login_task is not None and not self._login_task.done():
            return
        self._login_task = self.create_task(self._login_flow(), name=f"channels.weixin.login.{self.snapshot.id}")

    async def login_state(self) -> dict:
        return dict(self._login_state)

    async def _login_flow(self) -> None:
        """QR 登录状态机：取码 → 3s 轮询 wait→scaned→confirmed|expired（5 分钟总超时）。
        confirmed 返回 bot_token/baseurl/ilink_user_id——凭据与游标清零重建（新会话旧 token/对端回复凭据全部失效），
        登录账号本人自动加入白名单（微信侧自聊文传入站即本人 id，omp-wechat 同款语义）。
        """
        try:
            data = await self._request("GET", "ilink/bot/get_bot_qrcode", params={"bot_type": 3})
            qrcode = data.get("qrcode")
            qr_image = data.get("qrcode_img_content")
            if not qrcode:
                self._login_state = {"state": "error", "error": "二维码获取失败"}
                return
            self._login_state = {"state": "wait", "qr_image": qr_image or qrcode or ""}
            deadline = time.monotonic() + QR_LOGIN_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(QR_POLL_INTERVAL_SECONDS)
                data = await self._request("GET", "ilink/bot/get_qrcode_status", params={"qrcode": qrcode})
                status = data.get("status")
                if status == "wait":
                    continue
                if status == "scaned":
                    self._login_state = {"state": "scaned"}
                    continue
                if status == "expired":
                    self._login_state = {"state": "expired"}
                    return
                if status == "confirmed":
                    bot_token = data.get("bot_token")
                    if not bot_token:
                        self._login_state = {"state": "error", "error": "登录响应缺少 bot_token"}
                        return
                    self._creds = {
                        "bot_token": bot_token,
                        "baseurl": data.get("baseurl") or DEFAULT_BASE_URL,
                        "ilink_user_id": data.get("ilink_user_id") or "",
                        "ilink_bot_id": data.get("ilink_bot_id") or "",
                        "context_tokens": {},
                        "get_updates_buf": "",
                    }
                    await self._persist_credentials()
                    await self._auto_allow_owner()
                    await update_binding_status(
                        self.snapshot.id,
                        "connected",
                        account_ref=data.get("ilink_bot_id") or "",
                        account_name="微信",
                    )
                    self._login_state = {"state": "confirmed"}
                    self._login_gate.set()
                    return
            self._login_state = {"state": "expired"}
        except asyncio.CancelledError:
            raise
        except IlinkSessionExpired:
            self._login_state = {"state": "error", "error": "登录会话已过期，请重试"}
        except Exception as e:
            self._login_state = {"state": "error", "error": str(e)}
            logger.warning("weixin login flow failed", extra={"binding": self.snapshot.id, "error": str(e)})

    async def _auto_allow_owner(self) -> None:
        owner_id = self._creds.get("ilink_user_id")
        if not owner_id:
            return
        async with session_scope() as db:
            existing = (
                await db.execute(
                    select(ChannelPeer).where(
                        ChannelPeer.binding_id == self.snapshot.id,
                        ChannelPeer.peer_id == owner_id,
                    ),
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    ChannelPeer(binding_id=self.snapshot.id, peer_id=owner_id, peer_name="微信本人", status="allowed"),
                )
            else:
                existing.status = "allowed"
            await db.commit()

    async def logout(self) -> None:
        login_task = self._login_task
        self._login_task = None
        if login_task is not None:
            if not login_task.done():
                login_task.cancel()
            await asyncio.gather(login_task, return_exceptions=True)
        self._creds = {}
        await self._persist_credentials()
        self._login_state = {"state": "login_required"}
        self._login_gate.clear()
        await update_binding_status(self.snapshot.id, "login_required")

    async def _expire_session(self) -> None:
        """-14 处理：凭据清空落库、转 login_required（channel.status 事件驱动 Hub 提示重新扫码）。"""
        self._creds = {}
        await self._persist_credentials()
        self._login_state = {"state": "login_required"}
        self._login_gate.clear()
        await update_binding_status(self.snapshot.id, "login_required")

    # ---- 常驻轮询 -------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            if not self.has_credentials():
                # login_pending 已由守卫循环标记；等 REST 触发的登录流置位 gate。
                await self._login_gate.wait()
                continue
            try:
                await self._poll_loop()
            except IlinkSessionExpired:
                await self._expire_session()
            except httpx.TimeoutException:
                # 长轮询客户端超时（服务端 35s hold 临界抖动）：立即用同一游标重试。
                continue

    async def aclose(self) -> None:
        await super().aclose()
        self._login_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        while self.has_credentials():
            data = await self._request(
                "POST",
                "ilink/bot/getupdates",
                payload={"get_updates_buf": self._creds.get("get_updates_buf") or "", "base_info": _base_info()},
                timeout=SETTINGS.weixin_ilink_poll_timeout_seconds,
            )
            cursor = data.get("get_updates_buf")
            if cursor:
                self._creds["get_updates_buf"] = cursor
            msgs = data.get("msgs") or []
            for msg in msgs:
                self._accept_inbound(msg)
            # 游标与新增 context_token 一批一存（~35s 一次，DB 写频率可忽略）。
            if msgs or cursor:
                await self._persist_credentials()

    def _accept_inbound(self, msg: dict) -> None:
        peer_id = msg.get("from_user_id") or ""
        text, media_descs = _split_text_and_media(msg.get("item_list"))
        if not peer_id or (not text and not media_descs):
            return
        token = msg.get("context_token")
        if token:
            self._creds.setdefault("context_tokens", {})[peer_id] = token

        async def _dispatch() -> None:
            attachments = await _materialize_inbound_attachments(self.snapshot.id, peer_id, media_descs)
            inbound = InboundMessage(
                channel=self.channel_name,
                peer_id=peer_id,
                peer_name=peer_id,
                text=text,
                context_token=token,
                attachments=attachments,
            )
            try:
                future = await self._on_inbound(inbound)
                await future
            except Exception:
                logger.exception("weixin inbound turn failed", extra={"binding": self.snapshot.id, "peer": peer_id})

        self.create_task(_dispatch(), name=f"channels.weixin.dispatch.{self.snapshot.id}")

    # ---- 出站 -----------------------------------------------------------------------

    async def send_text(self, peer_id: str, text: str, context_token: str | None = None) -> None:
        token = context_token or self._creds.get("context_tokens", {}).get(peer_id)
        if not token:
            raise ChannelError(f"no context_token for peer {peer_id!r} (iLink reply-only)", fatal=False)
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": peer_id,
                # client_id 供服务端去重（超时重试时复用同一 id 才不会被当成两条）。
                "client_id": f"spiritagent-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": token,
            },
            "base_info": _base_info(),
        }
        try:
            await self._request("POST", "ilink/bot/sendmessage", payload=payload)
        except IlinkSessionExpired:
            await self._expire_session()
            raise ChannelError("iLink session expired while sending", fatal=False) from None

    async def send_media(
        self,
        peer_id: str,
        text: str | None,
        media: list[dict],
        context_token: str | None = None,
    ) -> None:
        """出站媒体（turn 产出的图片/视频）：拉 temp-media → AES-128-ECB 加密 → CDN 上传 → sendmessage 携带
        image_item / file_item 段。文本与媒体合并为单条消息（媒体段在前）。"""
        token = context_token or self._creds.get("context_tokens", {}).get(peer_id)
        if not token:
            raise ChannelError(f"no context_token for peer {peer_id!r} (iLink reply-only)", fatal=False)
        if not media:
            if text:
                await self.send_text(peer_id, text, context_token=token)
            return

        item_list: list[dict] = []
        for m in media:
            url = m.get("url") if isinstance(m, dict) else None
            if not isinstance(url, str) or not url:
                continue
            try:
                item = await self._upload_one(peer_id, m)
            except ChannelError:
                raise
            except Exception as e:
                logger.warning(
                    "iLink upload failed, skipping media",
                    extra={"binding": self.snapshot.id, "error": str(e)},
                )
                continue
            item_list.append(item)

        if not item_list and not text:
            logger.info(
                "all media uploads failed; nothing to send",
                extra={"binding": self.snapshot.id, "peer": peer_id},
            )
            return
        if text:
            item_list.insert(0, {"type": 1, "text_item": {"text": text}})

        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": peer_id,
                "client_id": f"spiritagent-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                "message_type": 2,
                "message_state": 2,
                "item_list": item_list,
                "context_token": token,
            },
            "base_info": _base_info(),
        }
        try:
            await self._request("POST", "ilink/bot/sendmessage", payload=payload)
        except IlinkSessionExpired:
            await self._expire_session()
            raise ChannelError("iLink session expired while sending media", fatal=False) from None

    async def _upload_one(self, peer_id: str, media: dict) -> dict:
        """上传单个媒体：拉 temp-media → AES 加密 → getuploadurl 拿 upload_full_url → POST 字节 →
        返回 iLink image_item / file_item 段。"""
        url = media["url"]
        mtype = media.get("type") or "image"
        # temp-media 路径或 URL 都接受：先视作 file_id，再退化到 GET。
        plain: bytes | None = None
        content_type = "application/octet-stream"
        if url.startswith("/api/media/files/"):
            file_id = url.removeprefix("/api/media/files/")
            stored = get_file_path(file_id)
            if stored is None:
                raise ChannelError(f"temp-media not found: {file_id}", fatal=False)
            plain_path, content_type = stored
            plain = plain_path.read_bytes()
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                plain = resp.content
                content_type = resp.headers.get("content-type") or content_type

        if plain is None:
            raise ChannelError("media body empty", fatal=False)

        # iLink 媒体类型编码：1=image 2=voice 3=video 4=file；不同 kind 可能需要映射。
        ilink_kind = {"image": 1, "voice": 4, "video": 3, "file": 4}.get(mtype, 1)
        raw_key = secrets.token_bytes(16)
        ciphertext = _aes_ecb_encrypt(plain, raw_key)
        filekey = secrets.token_hex(16)
        aeskey_hex = raw_key.hex()

        upload_meta = await self._request(
            "POST",
            "ilink/bot/getuploadurl",
            payload={
                "filekey": filekey,
                "media_type": ilink_kind,
                "to_user_id": peer_id,
                "rawsize": len(plain),
                "rawfilemd5": hashlib.md5(plain).hexdigest(),
                "filesize": len(ciphertext),
                "no_need_thumb": True,
                "aeskey": aeskey_hex,
                "base_info": _base_info(),
            },
        )
        upload_url = upload_meta.get("upload_full_url") or (
            f"{CDN_BASE_URL}/upload?encrypted_query_param={upload_meta.get('upload_param', '')}&filekey={filekey}"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as c:
            resp = await c.post(
                upload_url,
                content=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
            )
            resp.raise_for_status()
        encrypt_query_param = resp.headers.get("x-encrypted-param") or upload_meta.get("upload_param")
        # omp-wechat 注释明确：base64-of-hex 是唯一可解密形态。
        aes_key_b64 = base64.b64encode(aeskey_hex.encode("utf-8")).decode("ascii")

        media_slot = {
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }

        if mtype in ("image", "video"):
            return {
                "type": _MEDIA_TYPE_IMAGE if mtype == "image" else _MEDIA_TYPE_VIDEO,
                "image_item" if mtype == "image" else "video_item": {
                    "media": media_slot,
                    "mid_size": str(len(ciphertext)),
                },
            }
        return {
            "type": _MEDIA_TYPE_FILE,
            "file_item": {
                "media": media_slot,
                "file_name": media.get("name") or url.rsplit("/", 1)[-1] or "file",
                "len": str(len(plain)),
            },
        }

    async def send_typing(self, peer_id: str, context_token: str | None = None, status: int = 1) -> None:
        """「对方正在输入…」指示：best-effort，失败只记 debug（omp-wechat 同款降级）。"""
        try:
            ticket = await self._typing_ticket()
            if not ticket:
                return
            await self._request(
                "POST",
                "ilink/bot/sendtyping",
                payload={
                    "ilink_user_id": self._creds.get("ilink_user_id") or "",
                    "typing_ticket": ticket,
                    "status": status,
                    "base_info": _base_info(),
                },
                timeout=10.0,
            )
        except IlinkSessionExpired:
            await self._expire_session()
        except Exception:
            logger.debug("typing indicator failed", extra={"binding": self.snapshot.id, "peer": peer_id})

    async def _typing_ticket(self) -> str:
        ticket = self._creds.get("typing_ticket")
        ts = self._creds.get("typing_ticket_ts") or 0
        if ticket and time.time() - ts < TYPING_TICKET_TTL_SECONDS:
            return ticket
        data = await self._request(
            "POST",
            "ilink/bot/getconfig",
            payload={"ilink_user_id": self._creds.get("ilink_user_id") or "", "base_info": _base_info()},
        )
        ticket = data.get("typing_ticket") or ""
        if ticket:
            self._creds["typing_ticket"] = ticket
            self._creds["typing_ticket_ts"] = time.time()
            await self._persist_credentials()
        return ticket

    def platform_hint(self) -> str | None:
        return "weixin"

    # ---- 凭据持久化 -----------------------------------------------------------------

    async def _persist_credentials(self) -> None:
        async with session_scope() as db:
            row = await db.get(ChannelBinding, self.snapshot.id)
            if row is None:
                return
            row.credentials = json.dumps(self._creds, ensure_ascii=False) if self._creds else ""
            await db.commit()


def _load_credentials(raw: str) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


register("weixin_ilink", WeixinIlinkAdapter)
