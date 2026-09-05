"""Provider-neutral ingress and delivery coordination for external channels."""

from chatwaifu_runtime.external_channels.presentation import (
    BubbleSplitResult,
    BubbleSplitter,
    CadenceCalculator,
    DeliveryPlanFactory,
    InstantMessageDeliveryPlanFactory,
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.external_channels.service import ExternalChannelService

__all__ = [
    "BubbleSplitResult",
    "BubbleSplitter",
    "CadenceCalculator",
    "DeliveryPlanFactory",
    "ExternalChannelService",
    "InstantMessageDeliveryPlanFactory",
    "SingleTextDeliveryPlanFactory",
]
