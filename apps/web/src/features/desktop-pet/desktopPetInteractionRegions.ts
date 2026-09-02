interface ClientPoint {
  x: number;
  y: number;
}

const NATIVE_INTERACTIVE_SELECTOR = "[data-native-interactive='true']";

/**
 * UI chrome is rectangular, while the Live2D body is checked separately via
 * renderer mesh hit testing. Keeping those paths separate prevents the canvas
 * element itself from turning transparent pixels into a native hit region.
 */
export function isPointInsideDesktopPetChrome(
  root: ParentNode,
  point: ClientPoint,
): boolean {
  return Array.from(
    root.querySelectorAll<HTMLElement>(NATIVE_INTERACTIVE_SELECTOR),
  ).some((element) =>
    isPointInsideRect(point, element.getBoundingClientRect()),
  );
}

export function isPointInsideRect(
  point: ClientPoint,
  rect: Pick<DOMRect, "left" | "top" | "right" | "bottom">,
): boolean {
  return (
    point.x >= rect.left &&
    point.y >= rect.top &&
    point.x < rect.right &&
    point.y < rect.bottom
  );
}
