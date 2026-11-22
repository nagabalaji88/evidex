// Icons copied from design/reference-ui.html.
import type { ReactNode } from "react";

function Svg({ size = 20, children }: { size?: number; children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const IC = {
  menu: (
    <Svg><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></Svg>
  ),
  dashboard: <Svg><path d="M4 20V10M10 20V4M16 20v-7M21 20H3" /></Svg>,
  upload: (
    <Svg><path d="M12 15V4M7.5 8.5L12 4l4.5 4.5" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></Svg>
  ),
  uploadSmall: (
    <Svg size={16}><path d="M12 15V4M7.5 8.5L12 4l4.5 4.5" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></Svg>
  ),
  chat: <Svg><path d="M20 12a7.5 7.5 0 0 1-11 6.6L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12z" /></Svg>,
  kb: (
    <Svg><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5z" />
      <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z" /></Svg>
  ),
  activity: <Svg><path d="M3 12h4l3-7 4 14 3-7h4" /></Svg>,
  evaluation: <Svg><path d="M9 12l2 2 4-4" /><circle cx="12" cy="12" r="8.5" /></Svg>,
  settings: (
    <Svg><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></Svg>
  ),
  sun: (
    <Svg size={16}><circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></Svg>
  ),
  moon: <Svg size={16}><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></Svg>,
};

function Sm({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export const I = {
  ok: <Sm d="M3.5 8.5l3 3 6-7" />,
  x: <Sm d="M4.5 4.5l7 7M11.5 4.5l-7 7" />,
  bang: <Sm d="M8 3.5v5.5M8 12.2v.3" />,
  chev: (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M6 3.5l4.5 4.5L6 12.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  ),
};
