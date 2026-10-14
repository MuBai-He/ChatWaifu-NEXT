import type { ImgHTMLAttributes } from "react";

/** AI-drawn ChatWaifu web mark. Functional icons come from Lucide. */
export function BrandMark(props: ImgHTMLAttributes<HTMLImageElement>) {
  return (
    <img
      alt=""
      aria-hidden="true"
      src={`${import.meta.env.BASE_URL}brand/chatwaifu-mark-small.png`}
      {...props}
    />
  );
}
