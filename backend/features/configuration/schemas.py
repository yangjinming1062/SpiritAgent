from pydantic import BaseModel, ConfigDict, JsonValue


class DesktopConfigResponse(BaseModel):
    config: dict[str, JsonValue]


class DesktopConfigPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, JsonValue]
