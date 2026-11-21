import { projectName } from "../branding";
import { localize as ui, useI18n as useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { termsApi, type TermsVersion } from "../admin-api";
import { Brand, Loading } from "../components/BankUI";
import { Markdown } from "../components/Markdown";
import { Notice } from "../components/Notice";
import { dateTime, localError } from "../i18n";
import { ThemeChoice } from "../theme";
export function Terms() {
  useLocale();
  const [current, setCurrent] = useState<TermsVersion | null>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    termsApi
      .current()
      .then((data) => {
        if (active) {
          setCurrent(data.current);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, [reload]);
  return (
    <div className="terms-page">
      <header>
        <a href="/" aria-label={ui("Banking System — главная")}>
          <Brand />
        </a>
        <a href="/">{ui("Личный кабинет")}</a>
        <a href="/news">{ui("Новости")}</a>
        <ThemeChoice compact />
      </header>
      <main>
        <span className="eyebrow">{projectName}</span>
        <h1>{ui("Правила пользования")}</h1>
        <Notice error={error} />
        {error ? (
          <button onClick={() => setReload((x) => x + 1)}>
            {ui("Повторить загрузку")}
          </button>
        ) : current === undefined ? (
          <Loading />
        ) : current ? (
          <>
            <p className="muted">
              {ui("Версия ")}
              {current.version} · {dateTime(current.published_at, undefined)}
            </p>
            <Markdown content={current.markdown} />
          </>
        ) : (
          <div className="panel terms-placeholder">
            <h2>
              {ui("Правила пользования Banking System пока не опубликованы.")}
            </h2>
            <p>{ui("Следите за обновлениями.")}</p>
          </div>
        )}
      </main>
      <footer>
        <a href="/privacy">{ui("Политика конфиденциальности")}</a>
        Banking System · <a href="/">{ui("Вернуться в банк")}</a>
      </footer>
    </div>
  );
}
