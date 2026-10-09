import { AvatarController } from "@chatwaifu/avatar-sdk";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAvatarLab, type RendererKind } from "./useAvatarLab";

function Probe({ renderer }: { renderer: RendererKind }) {
  const { canvasRef, error } = useAvatarLab(renderer);
  return (
    <>
      <canvas ref={canvasRef} />
      <output data-testid="error">{error?.message ?? "none"}</output>
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Avatar Lab load lifecycle", () => {
  it("ignores a late load rejection from the renderer replaced by the user", async () => {
    let rejectOld: (error: Error) => void = () => {
      throw new Error("load was not started");
    };
    const oldLoad = new Promise<void>((_resolve, reject) => {
      rejectOld = reject;
    });
    vi.spyOn(AvatarController.prototype, "load")
      .mockReturnValueOnce(oldLoad)
      .mockResolvedValue(undefined);
    const view = render(<Probe renderer="live2d" />);
    view.rerender(<Probe renderer="fake" />);
    await act(async () => {
      rejectOld(new Error("old renderer was disposed"));
      await oldLoad.catch(() => undefined);
    });
    expect(screen.getByTestId("error").textContent).toBe("none");
  });

  it("still reports the current renderer's load failure", async () => {
    const failure = Promise.reject(new Error("current model is missing"));
    vi.spyOn(AvatarController.prototype, "load").mockReturnValue(failure);
    render(<Probe renderer="live2d" />);
    await act(async () => {
      await failure.catch(() => undefined);
    });
    expect(screen.getByTestId("error").textContent).toBe(
      "current model is missing",
    );
  });
});
