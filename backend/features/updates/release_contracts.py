from pydantic import BaseModel, Field


class ReleaseManifestFileItem(BaseModel):
    url: str
    sha512: str
    size: int


class ReleaseManifestResponse(BaseModel):
    version: str
    releaseDate: str
    releaseNotes: str = ""
    path: str
    sha512: str
    files: list[ReleaseManifestFileItem] = Field(default_factory=list)
