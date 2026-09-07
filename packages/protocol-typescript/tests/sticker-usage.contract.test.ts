import { describe, expect, it } from "vitest";
import {
  parseStickerUsageHistory,
  parseStickerUsageRecord,
} from "../src/index";

const record = {
  schema_version: "1.0",
  part_id: "00000000-0000-4000-8000-000000000001",
  sticker_id: "kitten_happy",
  label: "开心小猫",
  origin: "preset",
  status: "delivered",
  attempt: 1,
  created_at: "2026-09-07T01:00:00Z",
  updated_at: "2026-09-07T01:00:01Z",
  delivered_at: "2026-09-07T01:00:01Z",
};

describe("delivery-backed sticker usage contract", () => {
  it("requires actual timestamp and send attempt for a delivered image", () => {
    expect(parseStickerUsageRecord(record).status).toBe("delivered");
    for (const invalid of [
      { delivered_at: null },
      { attempt: 0 },
      { status: "failed" },
      { status: "liked" },
    ]) {
      expect(() =>
        parseStickerUsageRecord({ ...record, ...invalid }),
      ).toThrow();
    }
    expect(
      parseStickerUsageRecord({
        ...record,
        status: "failed",
        delivered_at: null,
      }).status,
    ).toBe("failed");
  });
  it("rejects oversized histories and unsupported evidence windows", () => {
    const history = {
      schema_version: "1.0",
      items: [record],
      scan_limit: 200,
      has_more: false,
    };
    expect(parseStickerUsageHistory(history).items).toHaveLength(1);
    expect(() =>
      parseStickerUsageHistory({ ...history, items: Array(51).fill(record) }),
    ).toThrow();
    expect(() =>
      parseStickerUsageHistory({ ...history, scan_limit: 1000 }),
    ).toThrow();
  });
});
