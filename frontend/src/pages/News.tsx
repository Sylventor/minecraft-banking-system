import { ServerLink } from "./Authentication";
import { localize as ui, useI18n as useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { ApiFailure } from "../api";
import {
  Brand,
  Empty,
  Icon,
  Loading,
  NewsShortcut,
} from "../components/BankUI";
import { Markdown } from "../components/Markdown";
import { Notice } from "../components/Notice";
import { dateTime, localError } from "../i18n";
import {
  newsApi,
  newsCategories,
  type NewsCategory,
  type NewsItem,
  type NewsPage,
} from "../news-api";
import { ThemeChoice } from "../theme";
export function NewsCover({
  url,
  title,
}: {
  url: string | null;
  title: string;
}) {
  useLocale();
  const [failed, setFailed] = useState(false);
  return url && !failed ? (
    <img
      className="news-cover"
      src={url}
      alt={ui("Обложка новости «") + title + "»"}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  ) : null;
}
export function NewsBody({
  item,
  preview = false,
}: {
  item: NewsItem;
  preview?: boolean;
}) {
  useLocale();
  return (
    <article className="news-article panel">
      <div className="news-tags">
        <span className={"news-category category-" + item.category}>
          {newsCategories()[item.category]}
        </span>
        {item.is_pinned && (
          <span className="news-pinned">
            <Icon name="info" size={15} />
            {ui("Закреплено")}
          </span>
        )}
        {preview && <span className="tag">{ui("Предпросмотр")}</span>}
      </div>
      {preview ? (
        <h3 className="news-preview-title">
          {item.title || ui("Заголовок новости")}
        </h3>
      ) : (
        <h1>{item.title}</h1>
      )}
      <p className="news-meta">
        <time dateTime={item.published_at || item.created_at}>
          {preview && !item.published_at
            ? ui("Ещё не опубликовано")
            : dateTime(item.published_at || item.created_at, undefined)}
        </time>
        <span>
          {ui("Автор: ")}
          {item.author}
        </span>
      </p>
      {item.summary && <p className="news-lead">{item.summary}</p>}
      <NewsCover
        key={item.cover_image}
        url={item.cover_image}
        title={item.title}
      />
      <Markdown content={item.content} />
      {item.published_at && item.updated_at > item.published_at && (
        <small className="muted">
          {ui("Обновлено ")}
          {dateTime(item.updated_at, undefined)}
        </small>
      )}
    </article>
  );
}
const PAGE_SIZE = 12;
function filters() {
  const params = new URLSearchParams(location.search),
    value = params.get("category") || "",
    page = params.get("page") || "1";
  return {
    category: Object.hasOwn(newsCategories(), value)
      ? (value as NewsCategory)
      : "",
    page: /^[1-9]\d{0,4}$/.test(page) ? Math.min(Number(page), 83333) : 1,
  };
}
export function News() {
  const { language } = useLocale();
  const [filter, setFilter] = useState(filters),
    [data, setData] = useState<NewsPage>(),
    [article, setArticle] = useState<NewsItem>();
  const [error, setError] = useState(""),
    [missing, setMissing] = useState(false),
    [reload, setReload] = useState(0);
  const raw = location.pathname
    .replace(/\/$/, "")
    .slice("/news".length)
    .replace(/^\//, "");
  let slug = "";
  try {
    slug = decodeURIComponent(raw);
  } catch {
    slug = "некорректный-адрес";
  }
  useEffect(() => {
    const pop = () => setFilter(filters());
    addEventListener("popstate", pop);
    return () => removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setData(undefined);
    setArticle(undefined);
    setError("");
    setMissing(false);
    const load = slug
      ? newsApi.article(slug, controller.signal)
      : newsApi.list(
          {
            category: filter.category,
            offset: (filter.page - 1) * PAGE_SIZE,
            limit: PAGE_SIZE,
          },
          controller.signal,
        );
    load
      .then((value) => {
        if (active) {
          if (slug) setArticle(value as NewsItem);
          else setData(value as NewsPage);
        }
      })
      .catch((e) => {
        if (active) {
          setMissing(e instanceof ApiFailure && e.status === 404);
          setError(
            e instanceof ApiFailure && e.status === 404
              ? ui("Эта новость не найдена или снята с публикации.")
              : localError(e),
          );
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [slug, filter.category, filter.page, reload]);
  useEffect(() => {
    document.title = article
      ? article.title + ui(" — Новости Banking System")
      : ui("Новости Banking System");
    return () => {
      document.title = ui("Banking System — Ваш мир. Ваш банк.");
    };
  }, [article, language]);
  function change(category: string, page: number) {
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (page > 1) params.set("page", String(page));
    history.pushState({}, "", "/news" + (params.size ? "?" + params : ""));
    setFilter({ category: category as NewsCategory | "", page });
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  return (
    <div className="news-page">
      <header className="news-header">
        <a href="/" aria-label={ui("Banking System — главная")}>
          <Brand />
        </a>
        <nav aria-label={ui("Основное меню")}>
          <NewsShortcut russian />
          <a href="/">{ui("Личный кабинет")}</a>
        </nav>
        <ThemeChoice compact russian />
      </header>
      <main className="news-main" id="news-content">
        {slug ? (
          <a className="news-back" href="/news">
            {ui("← Все новости")}
          </a>
        ) : (
          <>
            <div className="news-heading">
              <span className="eyebrow">{ui("ОФИЦИАЛЬНЫЕ НОВОСТИ")}</span>
              <h1>{ui("Новости")}</h1>
              <p>
                {ui(
                  "Обновления сервиса, важные объявления и события игровой экономики",
                )}
              </p>
            </div>
            <label className="news-filter">
              {ui("Категория")}
              <select
                value={filter.category}
                onChange={(e) => change(e.target.value, 1)}
              >
                <option value="">{ui("Все категории")}</option>
                {Object.entries(newsCategories()).map(([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <Notice error={error} />
        {error && !missing && (
          <button className="secondary" onClick={() => setReload((x) => x + 1)}>
            {ui("Повторить загрузку")}
          </button>
        )}
        {!error && !data && !article && (
          <Loading label={ui("Загружаем новости…")} />
        )}
        {article && <NewsBody item={article} />}
        {data &&
          (data.items.length ? (
            <div className="news-grid">
              {data.items.map((item) => (
                <article
                  className={
                    "news-card panel" +
                    (item.is_pinned ? " news-card-pinned" : "")
                  }
                  key={item.id}
                >
                  <NewsCover
                    key={item.cover_image}
                    url={item.cover_image}
                    title={item.title}
                  />
                  <div className="news-card-content">
                    <div className="news-tags">
                      <span
                        className={"news-category category-" + item.category}
                      >
                        {newsCategories()[item.category]}
                      </span>
                      {item.is_pinned && (
                        <span className="news-pinned">{ui("Закреплено")}</span>
                      )}
                    </div>
                    <h2>
                      <a href={"/news/" + item.slug}>{item.title}</a>
                    </h2>
                    {item.summary && <p>{item.summary}</p>}
                    <div className="news-meta">
                      <time dateTime={item.published_at || ""}>
                        {dateTime(item.published_at || "", undefined)}
                      </time>
                      <span>
                        {ui("Автор: ")}
                        {item.author}
                      </span>
                    </div>
                    <a
                      className="news-read"
                      href={"/news/" + item.slug}
                      aria-label={ui("Читать новость «") + item.title + "»"}
                    >
                      {ui("Читать полностью ")}
                      <span aria-hidden="true">→</span>
                    </a>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <Empty
              icon="info"
              title={
                filter.category
                  ? ui("В этой категории пока нет новостей")
                  : ui("Новостей пока нет")
              }
              description={ui(
                "Официальные объявления Banking System появятся здесь.",
              )}
            />
          ))}
        {data && (filter.page > 1 || data.next_offset !== null) && (
          <nav className="news-pagination" aria-label={ui("Страницы новостей")}>
            <button
              className="secondary"
              disabled={filter.page === 1}
              onClick={() => change(filter.category, filter.page - 1)}
            >
              {ui("Назад")}
            </button>
            <span>
              {ui("Страница ")}
              {filter.page}
            </span>
            <button
              className="secondary"
              disabled={data.next_offset === null}
              onClick={() => change(filter.category, filter.page + 1)}
            >
              {ui("Далее")}
            </button>
          </nav>
        )}
      </main>
      <footer className="news-footer">
        <span>{ui("Banking System · Игровой банковский сервис")}</span>
        <a href="/terms">{ui("Правила пользования")}</a>
        <a href="/privacy">{ui("Политика конфиденциальности")}</a>
        <ServerLink />
      </footer>
    </div>
  );
}
