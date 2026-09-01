export type AvatarCanvasSize = {
  width: number;
  height: number;
};

export type AvatarCanvasPoint = {
  x: number;
  y: number;
};

export function getAvatarCanvasLayoutSize(
  canvas: HTMLCanvasElement,
): AvatarCanvasSize {
  const bounds = canvas.getBoundingClientRect();
  return {
    width: canvas.clientWidth || bounds.width,
    height: canvas.clientHeight || bounds.height,
  };
}

export function getAvatarCanvasRenderPixelRatio(
  canvas: HTMLCanvasElement,
  devicePixelRatio: number,
): number {
  // Desktop framing magnifies the canvas with a CSS transform after layout.
  // Cover that visual scale in the WebGL backing buffer instead of asking the
  // compositor to interpolate a layout-sized avatar bitmap.
  const layout = getAvatarCanvasLayoutSize(canvas);
  const bounds = canvas.getBoundingClientRect();
  const baseRatio =
    Number.isFinite(devicePixelRatio) && devicePixelRatio > 0
      ? devicePixelRatio
      : 1;
  const visualScaleX =
    layout.width > 0 && bounds.width > 0 ? bounds.width / layout.width : 1;
  const visualScaleY =
    layout.height > 0 && bounds.height > 0 ? bounds.height / layout.height : 1;
  const visualScale = Math.max(1, visualScaleX, visualScaleY);

  return Math.min(4, baseRatio * visualScale);
}

export function mapClientPointToAvatarCanvas(
  canvas: HTMLCanvasElement,
  clientX: number,
  clientY: number,
): AvatarCanvasPoint | null {
  const bounds = canvas.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return null;
  const layout = getAvatarCanvasLayoutSize(canvas);
  if (layout.width <= 0 || layout.height <= 0) return null;
  return {
    x: (clientX - bounds.left) * (layout.width / bounds.width),
    y: (clientY - bounds.top) * (layout.height / bounds.height),
  };
}
