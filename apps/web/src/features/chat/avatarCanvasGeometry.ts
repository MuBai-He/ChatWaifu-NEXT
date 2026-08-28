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
