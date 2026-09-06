export function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatFullDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function formatCaptureDate(
  capturedAt?: string | null,
  offset?: string | null,
): string {
  if (!capturedAt) return "未知";
  const hasTz =
    Boolean(offset) ||
    capturedAt.endsWith("Z") ||
    /[+-]\d{2}(:\d{2})?$/.test(capturedAt);

  if (!hasTz) {
    // Naive ISO string from EXIF with unknown timezone.
    // Explicitly avoid new Date() which assumes browser local timezone!
    const clean = capturedAt.replace("T", " ").replace(/\.\d+$/, "");
    return `${clean} (时区未知)`;
  }

  if (offset) {
    const clean = capturedAt
      .replace("T", " ")
      .replace(/[+-]\d{2}(:\d{2})?$/, "")
      .replace("Z", "")
      .replace(/\.\d+$/, "");
    return `${clean} (UTC${offset})`;
  }

  const date = new Date(capturedAt);
  if (Number.isNaN(date.getTime())) return capturedAt;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
