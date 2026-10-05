import type { ChannelConnectionSnapshot } from "../chat/runtimeClient";
import { parseChannelPresentationPolicy } from "@chatwaifu/protocol";

// Match the Runtime's absent-override defaults, not the DTO's single-text defaults.
export function effectiveChannelPresentation(
  connection: ChannelConnectionSnapshot,
) {
  return parseChannelPresentationPolicy(
    connection.configuration.presentation_policy ?? {
      profile: "instant_message",
      max_parts: 3,
      preferred_chars_per_part: 30,
      soft_max_chars_per_part: 60,
    },
  );
}
