// Inline line icons (bundled, no icon font or CDN). 24x24, stroke = currentColor.
const PATHS = {
  home: "M3 11l9-7 9 7M5 10v10h14V10M10 20v-6h4v6",
  building: "M4 3h16v18H4zM8 7h2M14 7h2M8 11h2M14 11h2M8 15h2M14 15h2M11 21v-3h2v3",
  bill: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3",
  drop: "M12 3s6 6.5 6 11a6 6 0 01-12 0c0-4.5 6-11 6-11z",
  swap: "M7 7h13l-3-3M17 17H4l3 3",
  pass: "M3 6h18v12H3zM13 10h5M13 14h3M8 10a2 2 0 110 4 2 2 0 010-4",
  receipt: "M4 4h16v16H4zM8 9h8M8 13h8M8 17h4",
  users: "M9 4.5a3.5 3.5 0 110 7 3.5 3.5 0 010-7M2.5 20c.7-3.5 3.4-5.5 6.5-5.5s5.8 2 6.5 5.5M16 4.5a3.5 3.5 0 010 7M18 14.8c2 .7 3.2 2.5 3.5 5.2",
  gear: "M12 9a3 3 0 110 6 3 3 0 010-6M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  door: "M4 21h16M6 21V4h9v17M15 4l3 1v16",
  megaphone: "M3 11v2a1 1 0 001 1h2l5 4V6L6 10H4a1 1 0 00-1 1zM15 8a5 5 0 010 8M18 5a9 9 0 010 14",
  wrench: "M14.7 6.3a4 4 0 00-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 005.4-5.4l-2.6 2.6-2.8-.6-.6-2.8z",
  truck: "M1 6h13v10H1zM14 10h4l3 3v3h-7M5.5 16.2a1.8 1.8 0 110 3.6 1.8 1.8 0 010-3.6M17.5 16.2a1.8 1.8 0 110 3.6 1.8 1.8 0 010-3.6",
  file: "M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h4",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z",
  list: "M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01",
  menu: "M3 6h18M3 12h18M3 18h18",
  panel: "M3 4h18v16H3zM9 4v16",
  chev: "M6 9l6 6 6-6",
  sun: "M12 8a4 4 0 110 8 4 4 0 010-8M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  key: "M15 7a3 3 0 110 6 3 3 0 010-6M12.5 11.5L4 20M7 17l2 2M9 15l2 2",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  alert: "M12 3l10 18H2zM12 10v4M12 17h.01",
  info: "M12 3a9 9 0 110 18 9 9 0 010-18M12 11v5M12 8h.01",
  search: "M11 4a7 7 0 110 14 7 7 0 010-14M20 20l-3.5-3.5",
  x: "M6 6l12 12M18 6L6 18",
};

export default function Icon({ name, size = 18, className = "", label }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={PATHS[name] || PATHS.info} />
    </svg>
  );
}
