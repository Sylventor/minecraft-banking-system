import { localize as ui } from "./i18n";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Icon } from "./components/BankUI";
import { useI18n } from "./i18n";
export type Theme = "light" | "dark" | "system";
function savedTheme(): Theme {
  try {
    const value = localStorage.getItem("horizon.theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}
const context = createContext<{
  theme: Theme;
  setTheme: (value: Theme) => void;
}>({ theme: "system", setTheme: () => undefined });
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, update] = useState<Theme>(savedTheme);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolved =
        theme === "system" ? (media.matches ? "dark" : "light") : theme;
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute(
          "content",
          getComputedStyle(document.documentElement)
            .getPropertyValue("--background")
            .trim(),
        );
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  function setTheme(value: Theme) {
    try {
      localStorage.setItem("horizon.theme", value);
    } catch {
      /* The current tab can still change theme. */
    }
    update(value);
  }
  return (
    <context.Provider value={{ theme, setTheme }}>{children}</context.Provider>
  );
}
export const useTheme = () => useContext(context);
export function ThemeChoice({
  compact = false,
  russian = false,
}: {
  compact?: boolean;
  russian?: boolean;
}) {
  const { theme, setTheme } = useTheme();
  const { t } = useI18n();
  return (
    <label className={compact ? "theme-compact" : "theme-choice"}>
      <span>
        <Icon name="sun" />
        {russian ? ui("Оформление") : t("Оформление", "Appearance")}
      </span>
      <select
        aria-label={
          russian ? ui("Тема оформления") : t("Тема оформления", "Color theme")
        }
        value={theme}
        onChange={(e) => setTheme(e.target.value as Theme)}
      >
        <option value="system">
          {russian ? ui("Как на устройстве") : t("Как на устройстве", "System")}
        </option>
        <option value="light">
          {russian ? ui("Светлая") : t("Светлая", "Light")}
        </option>
        <option value="dark">
          {russian ? ui("Тёмная") : t("Тёмная", "Dark")}
        </option>
      </select>
    </label>
  );
}
