// Public build-time display settings. Never place secrets in VITE_* values.
export const projectName =
  import.meta.env.VITE_PROJECT_NAME?.trim() || "Banking System";
export const bankLogoUrl = import.meta.env.VITE_BANK_LOGO_URL?.trim() || "";
export const serverUrl = import.meta.env.VITE_SERVER_URL?.trim() || "";
