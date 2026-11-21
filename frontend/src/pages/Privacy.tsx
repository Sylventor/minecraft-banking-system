import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Brand } from "../components/BankUI";
import { LanguageChoice } from "../components/LanguageChoice";
import { Markdown } from "../components/Markdown";
import {
  languageLocale,
  localize as ui,
  normalizeLanguage,
  useI18n,
} from "../i18n";
import { ThemeChoice } from "../theme";
import ru from "../content/legal/privacy.ru.md?raw";
import en from "../content/legal/privacy.en.md?raw";
import uk from "../content/legal/privacy.uk.md?raw";
import "../privacy.css";

const documents = { ru, en, "uk-UA": uk };

export function Privacy() {
  const { language } = useI18n();
  const content = documents[normalizeLanguage(language)];
  const documentRef = useRef<HTMLDivElement>(null);
  const [outline, setOutline] = useState<{ id: string; title: string }[]>([]);
  // Headings come from the existing Markdown renderer, not a second parser.
  // Layout timing updates the outline before the newly selected language paints.
  useLayoutEffect(() => {
    setOutline(
      Array.from(
        documentRef.current?.querySelectorAll("h2") ?? [],
        (heading) => ({ id: heading.id, title: heading.textContent ?? "" }),
      ),
    );
  }, [content]);
  useEffect(() => {
    const previous = document.title;
    document.title = ui("Политика конфиденциальности") + " · Banking System";
    return () => {
      document.title = previous;
    };
  }, [language]);
  const updated = /^<!-- updated: (\d{4}-\d{2}-\d{2}) -->$/m.exec(content)?.[1];
  const date = updated ? new Date(updated + "T00:00:00Z") : undefined;
  const validDate =
    date &&
    !Number.isNaN(date.getTime()) &&
    date.toISOString().slice(0, 10) === updated;
  return (
    <div className="terms-page privacy-page">
      <header>
        <a href="/" aria-label={ui("Banking System — главная")}>
          <Brand />
        </a>
        <a href="/terms">{ui("Правила пользования")}</a>
        <LanguageChoice />
        <ThemeChoice compact />
      </header>
      <a className="privacy-skip" href="#privacy-document">
        {ui("Перейти к документу")}
      </a>
      <main>
        <h1>{ui("Политика конфиденциальности")}</h1>
        {validDate && (
          <p className="muted">
            {ui("Последнее обновление")}:{" "}
            <time dateTime={updated}>
              {new Intl.DateTimeFormat(languageLocale(language), {
                dateStyle: "long",
                timeZone: "UTC",
              }).format(date)}
            </time>
          </p>
        )}
        <div className="privacy-layout">
          <nav
            className="panel privacy-toc"
            aria-label={ui("Содержание документа")}
          >
            <h2>{ui("Содержание документа")}</h2>
            <ol>
              {outline.map((section) => (
                <li key={section.id}>
                  <a href={`#${section.id}`}>{section.title}</a>
                </li>
              ))}
            </ol>
          </nav>
          <div id="privacy-document" ref={documentRef} tabIndex={-1}>
            <Markdown content={content} headingIdPrefix="privacy-section" />
          </div>
        </div>
      </main>
      <footer>
        <button
          className="secondary"
          onClick={() => {
            if (history.length > 1) history.back();
            else location.assign("/");
          }}
        >
          {ui("Назад")}
        </button>
        <a href="/">{ui("Главная")}</a>
        <a href="/terms">{ui("Правила пользования")}</a>
        <span>Banking System</span>
      </footer>
    </div>
  );
}
