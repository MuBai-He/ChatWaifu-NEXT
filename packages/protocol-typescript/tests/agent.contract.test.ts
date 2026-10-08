import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  parseAgentTask,
  parseArtifactRef,
  parseCapabilityPage,
  parseDecisionRecord,
} from "../src/index";

const fixture = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL(
        "../../../tests/fixtures/protocol/v1/agent-contracts.json",
        import.meta.url,
      ),
    ),
    "utf8",
  ),
);
describe("Python-owned Agent contracts", () => {
  it("preserves task scope, grants, checkpoints and artifact integrity", () => {
    expect(parseAgentTask(fixture.task)).toEqual(fixture.task);
    expect(parseArtifactRef(fixture.artifact)).toEqual(fixture.artifact);
    expect(parseCapabilityPage(fixture.capabilities)).toEqual(
      fixture.capabilities,
    );
    expect(parseDecisionRecord(fixture.decision)).toEqual(fixture.decision);
  });
  it("requires original evidence and never accepts unknown authority fields", () => {
    expect(() =>
      parseDecisionRecord({ action: "respond", reason: "missing source" }),
    ).toThrow();
    expect(() => parseAgentTask({ ...fixture.task, owner: true })).toThrow();
    expect(() =>
      parseAgentTask({
        ...fixture.task,
        authorization: { ...fixture.task.authorization, allow_writes: "true" },
      }),
    ).toThrow();
  });
});
