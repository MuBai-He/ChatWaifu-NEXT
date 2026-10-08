import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  parseChannelRuntimePolicy,
  parseChannelRuntimeSettingsResponse,
  parseChannelRuntimeSettingsUpdate,
  parseGroupDiscussionPolicy,
} from "../src/index";

describe("channel settings cross-language contract", () => {
  it("parses the complete Python-owned fixture without losing permissions or budgets", () => {
    const root = path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../..",
    );
    const input = JSON.parse(
      readFileSync(
        path.join(
          root,
          "tests/fixtures/protocol/v1/channel-runtime-settings.json",
        ),
        "utf8",
      ),
    );
    const response = parseChannelRuntimeSettingsResponse(input);
    expect(response).toEqual(input);
    expect(response.policy.group_discussion?.input_tokens).toBe(4096);
    expect(response.policy.qq_owner_voice_reply_enabled).toBe(false);
    expect(response.search_provider).toBe("searxng");
    expect(response.reader_provider).toBe("crawl4ai");
  });
  it.each(["false", "true", 0, 1, null])(
    "never coerces permission %j",
    (value) => {
      for (const field of [
        "qq_account_enabled",
        "qq_owner_public_web_enabled",
        "qq_owner_voice_reply_enabled",
        "qq_owner_voice_input_enabled",
        "qq_native_favorites_enabled",
      ])
        expect(() => parseChannelRuntimePolicy({ [field]: value })).toThrow();
      expect(() => parseGroupDiscussionPolicy({ enabled: value })).toThrow();
    },
  );
  it.each([
    { input_tokens: "4096" },
    { input_tokens: 8193 },
    { input_tokens: 127 },
    { member_messages: 33, cache_messages: 32 },
    { member_characters: 6000, cache_characters: 3200 },
    { summary_timeout_seconds: 0 },
    { unknown: 1 },
  ])("rejects out-of-budget or inconsistent capacity %j", (patch) => {
    expect(() => parseGroupDiscussionPolicy(patch)).toThrow();
  });
  it("requires CAS, ignores no unknown identity edits, and retains legacy defaults", () => {
    for (const expected_revision of [-1, "1", true, null])
      expect(() =>
        parseChannelRuntimeSettingsUpdate({ expected_revision, policy: {} }),
      ).toThrow();
    expect(() =>
      parseChannelRuntimeSettingsUpdate({
        expected_revision: 0,
        policy: {},
        account_key: "999",
      }),
    ).toThrow();
    const defaults = parseChannelRuntimePolicy({});
    expect(defaults.qq_owner_public_web_enabled).toBe(false);
    expect(defaults.qq_owner_voice_reply_enabled).toBe(true);
    expect(defaults.group_discussion).toEqual(parseGroupDiscussionPolicy({}));
  });
});
