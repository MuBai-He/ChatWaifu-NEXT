export interface DrawableMeshSource {
  getDrawableCount(): number;
  getDrawableDynamicFlagIsVisible(drawableIndex: number): boolean;
  getDrawableOpacity(drawableIndex: number): number;
  getDrawableVertexCount(drawableIndex: number): number;
  getDrawableVertices(drawableIndex: number): Float32Array;
  getDrawableVertexIndexCount(drawableIndex: number): number;
  getDrawableVertexIndices(drawableIndex: number): Uint16Array;
}

const minimumDrawableOpacity = 0.02;
const triangleEpsilon = 1e-7;

export function isPointInVisibleDrawableMeshes(
  model: DrawableMeshSource,
  x: number,
  y: number,
): boolean {
  for (
    let drawableIndex = 0;
    drawableIndex < model.getDrawableCount();
    drawableIndex += 1
  ) {
    if (
      !model.getDrawableDynamicFlagIsVisible(drawableIndex) ||
      model.getDrawableOpacity(drawableIndex) <= minimumDrawableOpacity
    ) {
      continue;
    }
    const vertexCount = model.getDrawableVertexCount(drawableIndex);
    const vertices = model.getDrawableVertices(drawableIndex);
    const indexCount = model.getDrawableVertexIndexCount(drawableIndex);
    const indices = model.getDrawableVertexIndices(drawableIndex);
    for (let offset = 0; offset + 2 < indexCount; offset += 3) {
      const first = indices[offset];
      const second = indices[offset + 1];
      const third = indices[offset + 2];
      if (
        first === undefined ||
        second === undefined ||
        third === undefined ||
        first >= vertexCount ||
        second >= vertexCount ||
        third >= vertexCount
      ) {
        continue;
      }
      if (
        pointInTriangle(
          x,
          y,
          vertices[first * 2] ?? 0,
          vertices[first * 2 + 1] ?? 0,
          vertices[second * 2] ?? 0,
          vertices[second * 2 + 1] ?? 0,
          vertices[third * 2] ?? 0,
          vertices[third * 2 + 1] ?? 0,
        )
      ) {
        return true;
      }
    }
  }
  return false;
}

function pointInTriangle(
  pointX: number,
  pointY: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
  cx: number,
  cy: number,
): boolean {
  if (Math.abs(signedArea(cx, cy, ax, ay, bx, by)) <= triangleEpsilon) {
    return false;
  }
  const first = signedArea(pointX, pointY, ax, ay, bx, by);
  const second = signedArea(pointX, pointY, bx, by, cx, cy);
  const third = signedArea(pointX, pointY, cx, cy, ax, ay);
  const hasNegative =
    first < -triangleEpsilon ||
    second < -triangleEpsilon ||
    third < -triangleEpsilon;
  const hasPositive =
    first > triangleEpsilon ||
    second > triangleEpsilon ||
    third > triangleEpsilon;
  return !(hasNegative && hasPositive);
}

function signedArea(
  pointX: number,
  pointY: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  return (pointX - bx) * (ay - by) - (ax - bx) * (pointY - by);
}
