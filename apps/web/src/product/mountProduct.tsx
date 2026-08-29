import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

export type ProductKind = "web" | "desktop";
export type ProductSurface =
  "application" | "avatar-lab" | "desktop-pet" | "desktop-settings";

interface MountProductOptions {
  product: ProductKind;
  surface: ProductSurface;
  children: ReactNode;
}

export function mountProduct({
  product,
  surface,
  children,
}: MountProductOptions): void {
  document.documentElement.dataset.product = product;
  document.body.dataset.product = product;
  document.documentElement.dataset.surface = surface;
  document.body.dataset.surface = surface;

  const root = document.getElementById("root");
  if (!root) throw new Error("ChatWaifu product root is missing");
  createRoot(root).render(<StrictMode>{children}</StrictMode>);
}
