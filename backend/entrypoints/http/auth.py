from typing import Annotated

from fastapi import Depends
from features.accounts.deps import (
    get_current_admin_token,
    get_current_remote_session,
    get_current_session,
    get_current_user,
    get_optional_current_session,
)
from features.accounts.snapshots import AccountIdentity, LoginIdentity, RemoteDeviceIdentity

CurrentAdmin = Annotated[str, Depends(get_current_admin_token)]
CurrentSession = Annotated[tuple[AccountIdentity, LoginIdentity], Depends(get_current_session)]
CurrentUser = Annotated[AccountIdentity, Depends(get_current_user)]
CurrentRemoteSession = Annotated[tuple[AccountIdentity, RemoteDeviceIdentity], Depends(get_current_remote_session)]
OptionalSession = Annotated[
    tuple[AccountIdentity, LoginIdentity | RemoteDeviceIdentity] | None,
    Depends(get_optional_current_session),
]
