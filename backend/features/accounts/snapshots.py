"""鉴权返回的只读身份；账户和设备记录只能经所属用例修改。"""

from dataclasses import dataclass
from datetime import datetime

from features.accounts.models import LoginRecord, User
from features.accounts.remote.models import RemoteSession


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    id: int
    username: str


@dataclass(frozen=True, slots=True)
class LoginIdentity:
    id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class RemoteDeviceIdentity:
    id: int
    user_id: int
    name: str
    expires_at: datetime
    last_seen_at: datetime
    created_at: datetime


def account_identity(user: User) -> AccountIdentity:
    return AccountIdentity(user.id, user.username)


def login_identity(login: LoginRecord) -> LoginIdentity:
    return LoginIdentity(login.id, login.user_id)


def remote_identity(device: RemoteSession) -> RemoteDeviceIdentity:
    return RemoteDeviceIdentity(
        device.id,
        device.user_id,
        device.name,
        device.expires_at,
        device.last_seen_at,
        device.created_at,
    )
