import { localize as ui } from "./i18n";
import { createRoot } from "react-dom/client";
import { Suspense, lazy } from "react";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./theme";
import "./styles.css";
import "./design.css";
import "./auth.css";
import "./news.css";
const News = lazy(() =>
  import("./pages/News").then((m) => ({ default: m.News })),
);
const Terms = lazy(() =>
  import("./pages/Terms").then((m) => ({ default: m.Terms })),
);
const Privacy = lazy(() =>
  import("./pages/Privacy").then((m) => ({ default: m.Privacy })),
);
createRoot(document.getElementById("root")!).render(
  <ThemeProvider>
    <I18nProvider>
      <Suspense fallback={<p role="status">{ui("Загружаем страницу…")}</p>}>
        {window.location.pathname.replace(/\/$/, "") === "/privacy" ? (
          <Privacy />
        ) : window.location.pathname.replace(/\/$/, "") === "/terms" ? (
          <Terms />
        ) : window.location.pathname.replace(/\/$/, "") === "/news" ||
          window.location.pathname.startsWith("/news/") ? (
          <News />
        ) : (
          <App />
        )}
      </Suspense>
    </I18nProvider>
  </ThemeProvider>,
);
