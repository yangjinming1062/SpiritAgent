import asyncio
import base64
import binascii
import contextlib
import hashlib
import secrets
import struct
import time
from dataclasses import dataclass
from functools import partial

import httpx
from components import (
    REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    SETTINGS,
    download_capped,
    get_logger,
    session_scope,
)
from Crypto.Cipher import AES
from modules.channels import ChannelBinding, ChannelDeliveryMedia, ChannelLoginStateResponse, ChannelPeer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from services.infrastructure.assets import (
    build_data_uri,
    resolve_asset_reference,
    validate_image_bytes,
)

from ..base import ChannelAdapter, ChannelBindingSnapshot, ChannelError, InboundAttachment, InboundMessage
from ..bridge import handle_inbound
from ..state import update_binding_status

logger = get_logger(__name__)

# 微信 CDN 上传基址：getuploadurl 未返回 upload_full_url 时据 upload_param 拼出上传地址；入站下载直接用消息内地址。
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

_MEDIA_TYPE_IMAGE = 1
_MEDIA_TYPE_VOICE = 3
_MEDIA_TYPE_FILE = 4
_MEDIA_TYPE_VIDEO = 5

# 微信个人号 Bot API（iLink / ClawBot 协议）基址；确认登录后返回的 baseurl 可能不同，优先用返回值。
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com/"
# 通用请求头与 base_info：channel_version 需跟随官方 iLink SDK 演进（omp-wechat 同源值 2.2.0）。
CHANNEL_VERSION = "2.2.0"
BOT_AGENT = "SpiritAgent/1.0.0"
# 会话过期错误码（getupdates / sendmessage / getconfig 均可能返回）。只有轮询回路有权判定登录失效——getupdates 报 -14 才清凭据转 login_required；发送/typing 遇 -14 只代表回复上下文失效（context_token 过期），等下一条来信刷新即可。
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
    """iLink aes_key 三种编码（hex 32 / base64-of-hex / base64-of-raw）；base64-of-hex 是 omp-wechat 验证可解密的唯一稳定形态（base64-of-raw 会 CDN 403/丢文件），hex 形式尝试兜底。"""
    val = (raw or "").strip()
    if not val:
        return None
    # hex 32
    if len(val) == 32:
        with contextlib.suppress(binascii.Error, ValueError):
            return binascii.unhexlify(val)
    try:
        decoded = base64.b64decode(val, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        with contextlib.suppress(binascii.Error, ValueError):
            return binascii.unhexlify(decoded.decode())
    return None


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密 + PKCS7 拆对齐（iLink CDN 媒体传输加密方案）。"""
    padded = AES.new(key, AES.MODE_ECB).decrypt(ciphertext)
    pad = padded[-1]
    if 1 <= pad <= 16 and padded[-pad:] == bytes([pad]) * pad:
        return padded[:-pad]
    return padded


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    pad = 16 - len(plaintext) % 16
    return AES.new(key, AES.MODE_ECB).encrypt(plaintext + bytes([pad]) * pad)


def _encrypt_and_digest(plaintext: bytes, key: bytes) -> tuple[bytes, str]:
    return _aes_ecb_encrypt(plaintext, key), hashlib.md5(plaintext).hexdigest()


@dataclass(frozen=True)
class _InboundMedia:
    """入站 CDN 媒体描述，供下载解密。"""

    kind: str  # image / voice / file / video
    cdn_url: str
    aes_key: str


def _parse_inbound_item(item: dict) -> _InboundMedia | None:
    """提取可下载的媒体描述；无媒体段返回 None。image 的 type 值收发两侧不一致（出站 1、入站 2），按内层段名判别而非 type 数字。"""
    for segment, kind in (
        ("image_item", "image"),
        ("voice_item", "voice"),
        ("file_item", "file"),
        ("video_item", "video"),
    ):
        inner = item.get(segment)
        if not isinstance(inner, dict):
            continue
        cdn_url = inner.get("media")
        if not isinstance(cdn_url, str) or not cdn_url:
            return None
        aes_key = inner.get("aes_key")
        return _InboundMedia(kind=kind, cdn_url=cdn_url, aes_key=aes_key if isinstance(aes_key, str) else "")
    return None


def _split_text_and_media(item_list: list | None) -> tuple[str, list[_InboundMedia]]:
    """把入站 item_list 拆成 (text, media_descs)；媒体描述供后续下载。"""
    parts: list[str] = []
    media: list[_InboundMedia] = []
    for item in item_list or []:
        item_type = item.get("type")
        if item_type == 1 and "text_item" in item:
            text = (item.get("text_item") or {}).get("text")
            if text:
                parts.append(text)
            continue
        if item_type == 2 or "image_item" in item:
            # 入站图片的 type 值不确定（收发两侧代码不一致），按内层段名兜底判别
            parts.append("[图片]")
        elif item_type == _MEDIA_TYPE_VOICE:
            parts.append((item.get("voice_item") or {}).get("text") or "[语音]")
        elif item_type == _MEDIA_TYPE_FILE:
            file_name = (item.get("file_item") or {}).get("file_name") or ""
            parts.append(f"[文件 {file_name}]" if file_name else "[文件]")
        elif item_type == _MEDIA_TYPE_VIDEO:
            parts.append("[视频]")
        else:
            continue
        if (desc := _parse_inbound_item(item)) is not None:
            media.append(desc)
    return "\n".join(p for p in parts if p).strip(), media


async def _materialize_inbound_attachments(
    binding_id: int,
    media_descs: list[_InboundMedia],
) -> tuple[InboundAttachment, ...]:
    """仅下载、解密和校验图片，内联保存；语音转写与其他附件保留入站文字标记。"""
    out: list[InboundAttachment] = []
    for desc in media_descs:
        if desc.kind != "image":
            continue
        key = _decode_aes_key(desc.aes_key)
        if key is None:
            logger.warning("iLink media missing aes_key", extra={"binding": binding_id, "kind": desc.kind})
            continue
        try:
            ciphertext = await download_capped(desc.cdn_url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, timeout=30.0)
        except (httpx.HTTPError, ValueError, RuntimeError):
            logger.warning("iLink media download failed", extra={"binding": binding_id})
            continue
        try:
            # 整文件解密是 CPU 密集操作，移出事件循环
            plaintext = await asyncio.to_thread(_aes_ecb_decrypt, ciphertext, key)
            plaintext, content_type = await asyncio.to_thread(validate_image_bytes, plaintext)
        except Exception:
            logger.warning("iLink media decrypt or image validation failed", extra={"binding": binding_id})
            continue
        out.append(InboundAttachment(type="image", url=build_data_uri(plaintext, content_type)))
    return tuple(out)


class _WeixinCredentials(BaseModel):
    """channel_bindings.credentials 里持久化的 iLink 登录凭据，字段名即 JSON 键；读回时整体校验、未知键忽略，校验失败按无凭据处理，故服务端载荷写入前须先确认类型。"""

    model_config = ConfigDict(strict=True)

    bot_token: str = Field(min_length=1)
    baseurl: str = DEFAULT_BASE_URL
    ilink_user_id: str = ""
    ilink_bot_id: str = ""
    # peer_id → 该对端最新的 context_token。
    context_tokens: dict[str, str] = Field(default_factory=dict)
    get_updates_buf: str = ""
    typing_ticket: str = ""
    typing_ticket_ts: float = 0.0


class WeixinIlinkAdapter(ChannelAdapter):
    """微信 iLink（ClawBot 个人号 Bot API）适配器：QR 扫码登录 + getupdates 长轮询 + reply-only 回复。硬约束（协议决定）：回复必须回显入站消息的 context_token（每 peer 缓存最新值并持久化，重启免重扫）；不能主动发起会话。凭据结构见 _WeixinCredentials，无凭据（未登录或已失效）时为 None。"""

    conversation_title = "微信对话"
    supports_typing = True
    requires_login = True
    reports_connection_health = True

    def __init__(self, snapshot: ChannelBindingSnapshot) -> None:
        super().__init__(snapshot)
        self._creds: _WeixinCredentials | None = _load_credentials(snapshot.credentials, snapshot.id)
        self._login_gate = asyncio.Event()
        if self.has_credentials():
            self._login_gate.set()
        self._login_task: asyncio.Task | None = None
        self._login_state = ChannelLoginStateResponse(state="connected" if self.has_credentials() else "login_required")
        self._client: httpx.AsyncClient | None = None

    def has_credentials(self) -> bool:
        return self._creds is not None

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
        if self._creds is not None:
            headers["Authorization"] = f"Bearer {self._creds.bot_token}"
        return headers

    def _base_url(self) -> str:
        base_url = self._creds.baseurl if self._creds is not None else ""
        return base_url or DEFAULT_BASE_URL

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        payload: dict | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
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
        except httpx.ReadTimeout:
            raise
        except httpx.HTTPError as e:
            raise ChannelError("iLink transport error", fatal=False) from e
        if resp.status_code >= 500:
            raise ChannelError(f"iLink server error {resp.status_code}", fatal=False)
        if resp.status_code >= 400:
            raise ChannelError(f"iLink auth/request error {resp.status_code}", fatal=False)
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise ChannelError("iLink returned invalid JSON", fatal=False) from exc
        if not isinstance(data, dict):
            raise ChannelError("iLink returned invalid response", fatal=False)
        ret = data.get("ret")
        errcode = data.get("errcode")
        if ret == SESSION_EXPIRED or errcode == SESSION_EXPIRED:
            raise IlinkSessionExpired()
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            raise ChannelError("iLink API request failed", fatal=False)
        return data

    async def start_login(self) -> None:
        if self._login_task is not None and not self._login_task.done():
            return
        self._login_task = self.create_task(self._login_flow(), name=f"channels.weixin.login.{self.snapshot.id}")

    async def login_state(self) -> ChannelLoginStateResponse:
        return self._login_state.model_copy()

    async def _login_flow(self) -> None:
        """QR 登录状态机：取码 → 3s 轮询 wait→scaned→confirmed|expired（5 分钟总超时）。confirmed 返回 bot_token/baseurl/ilink_user_id，凭据与游标清零重建（旧 token/对端回复凭据全部失效），登录账号本人自动加入白名单（omp-wechat 同款语义）。"""
        try:
            data = await self._request("GET", "ilink/bot/get_bot_qrcode", params={"bot_type": 3})
            qrcode = data.get("qrcode")
            qr_image = data.get("qrcode_img_content")
            if not isinstance(qrcode, str) or not qrcode or (qr_image is not None and not isinstance(qr_image, str)):
                self._login_state = ChannelLoginStateResponse(state="error")
                return
            self._login_state = ChannelLoginStateResponse(state="wait", qr_image=qr_image or qrcode)
            deadline = time.monotonic() + QR_LOGIN_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(QR_POLL_INTERVAL_SECONDS)
                data = await self._request("GET", "ilink/bot/get_qrcode_status", params={"qrcode": qrcode})
                status = data.get("status")
                if status == "wait":
                    continue
                if status == "scaned":
                    self._login_state = ChannelLoginStateResponse(state="scaned")
                    continue
                if status == "expired":
                    self._login_state = ChannelLoginStateResponse(state="expired")
                    return
                if status == "confirmed":
                    bot_token = data.get("bot_token")
                    if not isinstance(bot_token, str) or not bot_token:
                        self._login_state = ChannelLoginStateResponse(state="error")
                        return
                    try:
                        creds = _WeixinCredentials.model_validate(
                            {
                                "bot_token": bot_token,
                                "baseurl": data.get("baseurl") or DEFAULT_BASE_URL,
                                "ilink_user_id": data.get("ilink_user_id") or "",
                                "ilink_bot_id": data.get("ilink_bot_id") or "",
                            },
                        )
                    except ValidationError:
                        self._login_state = ChannelLoginStateResponse(state="error")
                        logger.warning("weixin login returned invalid credentials", extra={"binding": self.snapshot.id})
                        return
                    self._creds = creds
                    await self._persist_credentials()
                    await self._auto_allow_owner(creds.ilink_user_id)
                    await update_binding_status(
                        self.snapshot.id,
                        "connected",
                        account_ref=data.get("ilink_bot_id") or "",
                        account_name="微信",
                    )
                    self._login_state = ChannelLoginStateResponse(state="confirmed")
                    self._login_gate.set()
                    return
            self._login_state = ChannelLoginStateResponse(state="expired")
        except asyncio.CancelledError:
            raise
        except IlinkSessionExpired:
            self._login_state = ChannelLoginStateResponse(state="error")
        except Exception:
            self._login_state = ChannelLoginStateResponse(state="error")
            logger.warning("weixin login flow failed", extra={"binding": self.snapshot.id}, exc_info=True)

    async def _auto_allow_owner(self, owner_id: str) -> None:
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
        await self._clear_login()

    async def _clear_login(self) -> None:
        """登出与轮询 -14 共用：凭据清空落库、转 login_required（channel.status 事件驱动 Hub 提示重新扫码）。"""
        self._creds = None
        await self._persist_credentials()
        self._login_state = ChannelLoginStateResponse(state="login_required")
        self.connection_health.reset()
        self._login_gate.clear()
        await update_binding_status(self.snapshot.id, "login_required")

    async def run(self) -> None:
        while True:
            if not self.has_credentials():
                # 等 REST 触发的登录流置位 gate。
                await self._login_gate.wait()
                continue
            try:
                await self._poll_loop()
            except IlinkSessionExpired:
                await self._clear_login()
            except httpx.ReadTimeout:
                # 长轮询客户端超时（服务端 35s hold 临界抖动）：立即用同一游标重试。
                continue
            except ChannelError as exc:
                if exc.fatal:
                    raise
                await self.connection_health.failed()
                # 轮询可恢复错误只重试同实例与游标，保留在途回合、typing 和未送达回复。
                logger.warning("iLink polling failed; retrying", extra={"binding": self.snapshot.id})
                await asyncio.sleep(SETTINGS.channels_restart_backoff_seconds)

    async def aclose(self) -> None:
        await super().aclose()
        self._login_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        while (creds := self._creds) is not None:
            data = await self._request(
                "POST",
                "ilink/bot/getupdates",
                payload={"get_updates_buf": creds.get_updates_buf, "base_info": _base_info()},
                timeout=SETTINGS.weixin_ilink_poll_timeout_seconds,
            )
            await self.connection_health.connected()
            cursor = data.get("get_updates_buf")
            msgs = data.get("msgs") or []
            for msg in msgs:
                await self._accept_inbound(msg)
            # 接收落库完成后才推进游标；回合在桥接层的独立任务中继续。
            if isinstance(cursor, str) and cursor:
                creds.get_updates_buf = cursor
            # 游标与新增 context_token 一批一存（~35s 一次，DB 写频率可忽略）。
            if msgs or cursor:
                await self._persist_credentials()

    async def _accept_inbound(self, msg: dict) -> None:
        peer_id = msg.get("from_user_id") or ""
        text, media_descs = _split_text_and_media(msg.get("item_list"))
        if not peer_id or (not text and not media_descs):
            return
        token = msg.get("context_token")
        if isinstance(token, str) and token and self._creds is not None:
            self._creds.context_tokens[peer_id] = token

        inbound = InboundMessage(
            peer_id=peer_id,
            # 入站消息不带昵称：留空让对端沿用已有备注（如登录者本人的「微信本人」），界面回退显示 peer_id。
            peer_name="",
            text=text,
            msg_id=str(msg.get("new_msg_id") or msg.get("msg_id") or ""),
            context_token=token,
            fetch_attachments=(
                partial(_materialize_inbound_attachments, self.snapshot.id, media_descs) if media_descs else None
            ),
        )
        await handle_inbound(self, inbound)

    def _reply_token(self, peer_id: str, context_token: str | None) -> str:
        cached = self._creds.context_tokens.get(peer_id) if self._creds is not None else None
        token = context_token or cached
        if not token:
            raise ChannelError(f"no context_token for peer {peer_id!r} (iLink reply-only)", fatal=False)
        return token

    async def _send_items(self, peer_id: str, token: str, item_list: list[dict]) -> None:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": peer_id,
                # client_id 每次发送新生成：失败后的补发不沿用它，若原请求其实已送达，对端会收到重复消息。
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
            # 回复上下文失效 ≠ 登录失效：不重扫码；登录态是否真失效由轮询回路的 -14 判定。
            raise ChannelError("iLink reply context expired while sending", fatal=False) from None

    async def send_text(self, peer_id: str, text: str, context_token: str | None = None) -> None:
        token = self._reply_token(peer_id, context_token)
        await self._send_items(peer_id, token, [{"type": 1, "text_item": {"text": text}}])

    async def send_media(
        self,
        peer_id: str,
        text: str | None,
        media: list[ChannelDeliveryMedia],
        context_token: str | None = None,
    ) -> None:
        """出站媒体（turn 产出的图片/视频）：读本地媒体 → AES-128-ECB 加密 → CDN 上传 → sendmessage 携带 image_item/video_item 段；文本与媒体合并为单条消息（文本段在前）。"""
        token = self._reply_token(peer_id, context_token)
        item_list: list[dict] = []
        for m in media:
            if not m.url:
                continue
            try:
                item = await self._upload_one(peer_id, m)
            except ChannelError:
                raise
            except Exception as e:
                logger.warning(
                    "iLink upload failed",
                    extra={"binding": self.snapshot.id, "error": str(e)},
                )
                raise ChannelError("iLink media upload failed", fatal=False) from e
            item_list.append(item)

        if not item_list and not text:
            logger.info(
                "media reply has no media URL or text; nothing to send",
                extra={"binding": self.snapshot.id, "peer": peer_id},
            )
            raise ChannelError("iLink media reply contains no uploadable media", fatal=False)
        if text:
            item_list.insert(0, {"type": 1, "text_item": {"text": text}})
        await self._send_items(peer_id, token, item_list)

    async def _upload_one(self, peer_id: str, media: ChannelDeliveryMedia) -> dict:
        """上传单个媒体：拉本地媒体字节 → AES 加密 → getuploadurl 拿 upload_full_url → POST 字节 → 返回 iLink image_item/video_item 段。"""
        url = media.url
        resolved = resolve_asset_reference(url)
        if resolved is not None:
            plain = await asyncio.to_thread(resolved[0].read_bytes)
        else:
            plain = await download_capped(url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, timeout=60.0)

        # iLink 上传媒体类型编码：1=image 2=voice 3=video 4=file。
        ilink_kind = 1 if media.type == "image" else 3
        raw_key = secrets.token_bytes(16)
        # 全量媒体加密与 md5 是 CPU 密集操作，移出事件循环
        ciphertext, plain_md5 = await asyncio.to_thread(_encrypt_and_digest, plain, raw_key)
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
                "rawfilemd5": plain_md5,
                "filesize": len(ciphertext),
                "no_need_thumb": True,
                "aeskey": aeskey_hex,
                "base_info": _base_info(),
            },
        )
        upload_url = upload_meta.get("upload_full_url") or (
            f"{CDN_BASE_URL}/upload?encrypted_query_param={upload_meta.get('upload_param', '')}&filekey={filekey}"
        )

        resp = await self._ensure_client().post(
            upload_url,
            content=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            timeout=httpx.Timeout(60.0, connect=10.0),
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

        if media.type == "image":
            return {"type": _MEDIA_TYPE_IMAGE, "image_item": {"media": media_slot, "mid_size": str(len(ciphertext))}}
        return {"type": _MEDIA_TYPE_VIDEO, "video_item": {"media": media_slot, "mid_size": str(len(ciphertext))}}

    async def send_typing(self, peer_id: str, context_token: str | None = None) -> None:
        """「对方正在输入…」指示：best-effort，失败只记 debug（omp-wechat 同款降级）。"""
        try:
            # 与轮询任务并发：登录失效会把 _creds 置空，一次发送内只用开头取到的这份凭据。
            creds = self._creds
            if creds is None:
                return
            ticket = await self._typing_ticket(creds)
            if not ticket:
                return
            await self._request(
                "POST",
                "ilink/bot/sendtyping",
                payload={
                    "ilink_user_id": creds.ilink_user_id,
                    "typing_ticket": ticket,
                    "status": 1,
                    "base_info": _base_info(),
                },
                timeout=10.0,
            )
        except IlinkSessionExpired:
            # typing 是 best-effort，会话是否真失效交给轮询回路判定。
            logger.debug("typing skipped: reply context expired", extra={"binding": self.snapshot.id, "peer": peer_id})
        except Exception:
            logger.debug("typing indicator failed", extra={"binding": self.snapshot.id, "peer": peer_id})

    async def _typing_ticket(self, creds: _WeixinCredentials) -> str:
        if creds.typing_ticket and time.time() - creds.typing_ticket_ts < TYPING_TICKET_TTL_SECONDS:
            return creds.typing_ticket
        data = await self._request(
            "POST",
            "ilink/bot/getconfig",
            payload={"ilink_user_id": creds.ilink_user_id, "base_info": _base_info()},
        )
        ticket = data.get("typing_ticket")
        if not isinstance(ticket, str) or not ticket:
            return ""
        creds.typing_ticket = ticket
        creds.typing_ticket_ts = time.time()
        await self._persist_credentials()
        return ticket

    def platform_hint(self) -> str | None:
        return "weixin"

    async def _persist_credentials(self) -> None:
        async with session_scope() as db:
            row = await db.get(ChannelBinding, self.snapshot.id)
            if row is None:
                return
            row.credentials = self._creds.model_dump_json() if self._creds is not None else ""
            await db.commit()


def _load_credentials(raw: str, binding_id: int) -> _WeixinCredentials | None:
    if not raw:
        return None
    try:
        return _WeixinCredentials.model_validate_json(raw)
    except ValidationError as exc:
        # 不记录 str(exc)：其中的 input_value 会带出令牌，只记出错字段位置与类型。
        fields = ", ".join(
            f"{'.'.join(map(str, error['loc'])) or '<root>'} ({error['type']})"
            for error in exc.errors(include_input=False, include_url=False)
        )
        logger.warning("iLink credentials invalid; login required", extra={"binding": binding_id, "fields": fields})
        return None
