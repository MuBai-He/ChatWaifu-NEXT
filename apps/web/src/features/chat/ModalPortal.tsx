import type { HTMLAttributes, ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalPortalProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

export function ModalPortal({ children, ...props }: ModalPortalProps) {
  return createPortal(<div {...props}>{children}</div>, document.body);
}
