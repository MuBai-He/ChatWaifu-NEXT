---
name: vertical-slice
description: Implement a ChatWaifu user-visible or cross-domain feature as a complete vertical slice spanning its contracts, behavior, integration, tests, observability, and documentation. Do not use for a strictly local refactor with no behavior change.
---

# Vertical Slice

Identify the intended user-visible behavior and every affected layer before editing.
A complete slice normally covers domain contracts, events or schemas, service
behavior, persistence when needed, integration, frontend behavior, observability,
tests, and protocol documentation.

Do not stop at a store, service, endpoint, UI mock, or prompt when the behavior
requires the rest of the path. Do not invent a parallel contract for an existing
concept.

When changing a public protocol, identify all producers and consumers, preserve
compatibility when practical, version breaking semantics explicitly, and test both
success and failure paths. Deliver the smallest complete behavior that can travel
through the actual application path.
