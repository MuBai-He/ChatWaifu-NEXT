"""Model worker capability and routing contracts."""

from pydantic import Field

from chatwaifu_protocol.base import ProtocolModel


class ModelResourceProfile(ProtocolModel):
    devices: list[str] = Field(default_factory=list)
    estimated_vram_mb: int | None = Field(default=None, ge=0)
    estimated_ram_mb: int | None = Field(default=None, ge=0)
    exclusive_gpu: bool = False


class ModelLicense(ProtocolModel):
    id: str
    review_required: bool = True


class ModelManifest(ProtocolModel):
    model_id: str
    kind: str
    adapter_version: str
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    resource: ModelResourceProfile = Field(default_factory=ModelResourceProfile)
    local: bool
    stores_input: bool = False
    license: ModelLicense


class RouteDecision(ProtocolModel):
    provider_id: str
    model_id: str
    backend_kind: str
    reason_codes: list[str] = Field(default_factory=list)
    fallback_chain: list[str] = Field(default_factory=list)
    cloud_context_policy: str
    estimated_cost: float | None = Field(default=None, ge=0)
