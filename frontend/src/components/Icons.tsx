// 手写 inline SVG 图标（R8.1：不引图标库）。stroke 风格，currentColor 自适应主题。
import type { CSSProperties, ReactNode } from "react";

interface IconProps {
  size?: number;
  style?: CSSProperties;
}

const base = (size: number, style?: CSSProperties, path?: ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={style} aria-hidden="true">
    {path}
  </svg>
);

export const IconSearch = ({ size = 14, style }: IconProps) =>
  base(size, style, <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>);

export const IconSun = ({ size = 14, style }: IconProps) =>
  base(size, style, <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>);

export const IconMoon = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />);

export const IconChevron = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="m9 18 6-6-6-6" />);

export const IconAlert = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4m0 4h.01" /></>);

export const IconShield = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />);

export const IconCheck = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="M20 6 9 17l-5-5" />);

export const IconDoc = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M16 13H8m8 4H8" /></>);

export const IconGraph = ({ size = 14, style }: IconProps) =>
  base(size, style, <><circle cx="5" cy="6" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M7.3 7.2 10.5 16m6.2-8.8L13.5 16M7.5 6h9" /></>);

export const IconBox = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M21 8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7Z" /><path d="m3.3 7 8.7 5 8.7-5M12 22V12" /></>);

export const IconBook = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" /></>);

export const IconRefresh = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></>);

export const IconExternal = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><path d="M15 3h6v6M10 14 21 3" /></>);

export const IconFilter = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="M22 3H2l8 9.5V19l4 2v-8.5L22 3Z" />);

export const IconGrid = ({ size = 14, style }: IconProps) =>
  base(size, style, <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>);

export const IconEye = ({ size = 14, style }: IconProps) =>
  base(size, style, <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></>);

export const IconPlay = ({ size = 14, style }: IconProps) =>
  base(size, style, <path d="m6 4 14 8-14 8V4Z" />);
