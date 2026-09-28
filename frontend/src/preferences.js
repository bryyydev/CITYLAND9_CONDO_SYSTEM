import { useEffect, useState } from "react";

// Per-browser UI preferences (theme, collapsed sidebar). Never business data.
// localStorage can be unavailable (private mode, blocked storage), so every access is guarded.
export function usePreference(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* preference simply isn't remembered */
    }
  }, [key, value]);
  return [value, setValue];
}
