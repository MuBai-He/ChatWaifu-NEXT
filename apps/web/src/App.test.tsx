import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("Phase 0 status page", () => {
  it("states the implemented and deferred boundaries", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "ChatWaifu NEXT" }),
    ).toBeTruthy();
    expect(screen.getByText("Versioned domain protocol")).toBeTruthy();
    expect(screen.getByText("Runtime / Pipecat / WebRTC")).toBeTruthy();
  });
});
