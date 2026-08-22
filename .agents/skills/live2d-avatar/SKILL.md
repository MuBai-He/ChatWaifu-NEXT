---
name: live2d-avatar
description: Implement or debug ChatWaifu avatar presentation, semantic AvatarCue contracts, Live2D Cubism rendering, state reduction, emotion/action mapping, motion priority, lipsync, gaze, idle behavior, interpolation, or frontend avatar integration.
---

# Live2d Avatar

Live2D is a replaceable presentation adapter. Preserve this path:

Agent -> semantic AvatarCue -> protocol -> state reducer -> resolver -> model mapping
-> Cubism runtime.

Never emit Live2D parameter or asset identifiers from a model. Compose avatar output
from explicit layers and priorities so idle, emotion, gesture, speaking, lipsync,
blink, gaze, and temporary actions can blend without overwriting one another.

Drive lipsync from viseme/phoneme timing when available, otherwise audio amplitude,
with a bounded synthetic fallback. Do not use LLM token timing. React owns UI and
semantic target state; a dedicated requestAnimationFrame controller owns 60 FPS
parameters and interpolation.

Unknown cues fall back to neutral, missing resources produce structured warnings,
and pre-ready queues remain bounded. Test validation, reduction, priority, stale
cues, speaking lifecycle, missing mappings, reset, and visual behavior.
