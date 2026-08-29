import { afterEach, describe, expect, it, vi } from "vitest";

import { getSession } from "./sessionsClient";
import { getTtsConfigurationRegistrations } from "./ttsClient";

const validSession = {
  session_id: "00000000-0000-4000-8000-000000000101",
  character_id: "ayachi_nene",
  state: "ready",
  conversation_state: "idle",
  revision: 2,
  created_at: "2026-08-29T00:00:00Z",
  updated_at: "2026-08-29T00:00:01Z",
};

describe("Runtime HTTP contracts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("accepts a generated SessionSnapshot and preserves additive fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ ...validSession, future_server_field: "compatible" }),
        ),
    );

    const session = await getSession(validSession.session_id);

    expect(session.session_id).toBe(validSession.session_id);
    expect(session.future_server_field).toBe("compatible");
  });

  it("rejects malformed success payloads at the HTTP boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ ...validSession, revision: "not-an-integer" }),
        ),
    );

    await expect(getSession(validSession.session_id)).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
  });

  it("parses the registry-driven TTS configuration contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          schema_version: "1.0",
          count: 1,
          items: [
            {
              provider_id: "provider_from_registry",
              display_name: "Registry Voice",
              configuration_schema: {
                type: "object",
                properties: { model: { type: "string" } },
                required: ["model"],
              },
              ui_schema: {
                schema_version: "1.0",
                fields: [
                  {
                    key: "model",
                    label: "模型",
                    control: "text",
                    advanced: false,
                    options: [],
                    minimum: null,
                    maximum: null,
                    step: null,
                    placeholder: "model-id",
                    help_text: "",
                  },
                ],
              },
              configuration: {
                provider_id: "provider_from_registry",
                model: "voice-v1",
              },
            },
          ],
        }),
      ),
    );

    const registrations = await getTtsConfigurationRegistrations();

    expect(registrations[0]?.ui_schema.fields[0]).toMatchObject({
      key: "model",
      control: "text",
    });
  });
});

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
