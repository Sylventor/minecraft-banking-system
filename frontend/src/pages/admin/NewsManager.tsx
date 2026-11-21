import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useBank } from "../../bank-context";
import { Empty, Loading, Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { dateTime, localError, localizeSystem } from "../../i18n";
import {
  newsApi,
  newsCategories,
  type NewsCard,
  type NewsInput,
  type NewsItem,
  type NewsPage,
  type NewsCategory,
} from "../../news-api";
import { NewsBody } from "../News";
const blank = (): NewsInput => ({
  title: "",
  summary: "",
  content: "",
  category: "general",
  cover_image: null,
  status: "draft",
  is_pinned: false,
});
export function NewsManager() {
  useLocale();
  const { data } = useBank();
  const [page, setPage] = useState<NewsPage>(),
    [category, setCategory] = useState(""),
    [status, setStatus] = useState(""),
    [offset, setOffset] = useState(0),
    [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  const [editor, setEditor] = useState(false),
    [original, setOriginal] = useState<NewsItem>(),
    [form, setForm] = useState<NewsInput>(blank),
    [preview, setPreview] = useState(false);
  const [deleting, setDeleting] = useState<NewsCard>(),
    [confirmed, setConfirmed] = useState(false);
  const pending = useRef(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    newsApi
      .adminList({ category, status, offset, limit: 20 })
      .then((value) => {
        if (active) setPage(value);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [category, status, offset, reload]);
  function newArticle() {
    setOriginal(undefined);
    setForm(blank());
    setEditor(true);
    setPreview(false);
    setError("");
    setSuccess("");
  }
  async function open(item: NewsCard) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const value = await newsApi.adminArticle(item.id);
      setOriginal(value);
      setForm({
        title: value.title,
        summary: value.summary,
        content: value.content,
        category: value.category,
        cover_image: value.cover_image,
        status: value.status,
        is_pinned: value.is_pinned,
      });
      setPreview(false);
      setEditor(true);
    } catch (e) {
      setError(localError(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const value = original
        ? await newsApi.update(original, form)
        : await newsApi.create(form);
      setOriginal(value);
      setForm({ ...form, cover_image: value.cover_image });
      setSuccess(
        value.status === "published"
          ? ui("Новость опубликована. Публичная ссылка доступна ниже.")
          : ui("Черновик сохранён. Посетители его не видят."),
      );
      setReload((x) => x + 1);
    } catch (e) {
      setError(localError(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function action(
    item: NewsCard,
    kind: "publish" | "unpublish" | "delete",
  ) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await newsApi.action(item, kind);
      setDeleting(undefined);
      setConfirmed(false);
      if (kind === "delete" && page?.items.length === 1 && offset > 0)
        setOffset(Math.max(0, offset - 20));
      setReload((x) => x + 1);
      setSuccess(
        kind === "delete"
          ? ui("Новость удалена.")
          : kind === "publish"
            ? ui("Новость опубликована.")
            : ui("Новость снята с публикации и сохранена как черновик."),
      );
    } catch (e) {
      setError(localError(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  function field<K extends keyof NewsInput>(key: K, value: NewsInput[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setSuccess("");
  }
  const previewItem: NewsItem = {
    ...form,
    id: original?.id || "preview",
    slug: original?.slug || "",
    author:
      original?.author ||
      data.profile.display_name ||
      data.profile.bank_username ||
      ui("Администрация Banking System"),
    created_at: original?.created_at || new Date().toISOString(),
    updated_at: original?.updated_at || new Date().toISOString(),
    published_at: original?.published_at || null,
  };
  return (
    <div className="news-manager">
      <Notice error={error} />
      {success && (
        <p role="status" className="success">
          {localizeSystem(success)}
        </p>
      )}
      {editor ? (
        <>
          <div className="section-row">
            <h3>
              {original ? ui("Редактирование новости") : ui("Новая новость")}
            </h3>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                setEditor(false);
                setError("");
              }}
            >
              {ui("К списку новостей")}
            </button>
          </div>
          {original && (
            <div className="news-editor-meta">
              <span>
                {ui("Создана ")}
                {dateTime(original.created_at, undefined)}
              </span>
              <span>
                {ui("Изменена ")}
                {dateTime(original.updated_at, undefined)}
              </span>
              <span>
                {ui("Автор: ")}
                {original.author}
              </span>
              {original.status === "published" && (
                <a
                  href={"/news/" + original.slug}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {ui("Открыть публикацию ↗")}
                </a>
              )}
            </div>
          )}
          <form className="panel news-editor" onSubmit={save}>
            <label>
              {ui("Заголовок")}
              <input
                value={form.title}
                required
                maxLength={180}
                onChange={(e) => field("title", e.target.value)}
              />
            </label>
            <label>
              {ui("Краткое описание")}{" "}
              <span className="muted">
                {ui("Необязательно, до 500 символов")}
              </span>
              <textarea
                rows={3}
                maxLength={500}
                value={form.summary}
                onChange={(e) => field("summary", e.target.value)}
              />
            </label>
            <div className="news-editor-grid">
              <label>
                {ui("Категория")}
                <select
                  value={form.category}
                  onChange={(e) =>
                    field("category", e.target.value as NewsCategory)
                  }
                >
                  {Object.entries(newsCategories()).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {ui("Статус публикации")}
                <select
                  value={form.status}
                  onChange={(e) =>
                    field("status", e.target.value as NewsInput["status"])
                  }
                >
                  <option value="draft">{ui("Черновик")}</option>
                  <option value="published">{ui("Опубликовано")}</option>
                </select>
              </label>
            </div>
            <label>
              {ui("Обложка")}{" "}
              <span className="muted">
                {ui(
                  "Необязательно: HTTPS-ссылка или путь /images/, /branding/, /news-covers/",
                )}
              </span>
              <input
                inputMode="url"
                maxLength={2048}
                placeholder="https://…"
                value={form.cover_image || ""}
                onChange={(e) => field("cover_image", e.target.value || null)}
              />
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={form.is_pinned}
                onChange={(e) => field("is_pinned", e.target.checked)}
              />
              {ui("Закрепить в начале списка")}
            </label>
            <div
              className="segmented"
              role="group"
              aria-label={ui("Режим редактора")}
            >
              <button
                type="button"
                aria-pressed={!preview}
                onClick={() => setPreview(false)}
              >
                {ui("Редактирование Markdown")}
              </button>
              <button
                type="button"
                aria-pressed={preview}
                onClick={() => setPreview(true)}
              >
                {ui("Предпросмотр")}
              </button>
            </div>
            {preview ? (
              <div className="news-preview">
                <NewsBody item={previewItem} preview />
              </div>
            ) : (
              <label>
                {ui("Основной текст")}
                <textarea
                  className="news-content-input"
                  value={form.content}
                  required
                  rows={14}
                  onChange={(e) => field("content", e.target.value)}
                />
                <small>
                  {ui(
                    "До 128 КиБ. Поддерживаются заголовки, списки, ссылки, таблицы и код. HTML и опасные ссылки запрещены.",
                  )}
                </small>
              </label>
            )}
            {form.status === "published" ? (
              <p className="info-box">
                {ui(
                  "После сохранения публикация будет доступна всем посетителям сайта.",
                )}
              </p>
            ) : (
              <p className="muted">
                {ui(
                  "Черновик доступен только редакторам с правом управления новостями.",
                )}
              </p>
            )}
            <div className="flow-footer">
              <button
                disabled={busy || !form.title.trim() || !form.content.trim()}
              >
                {busy
                  ? ui("Сохраняем…")
                  : form.status === "draft"
                    ? ui("Сохранить черновик")
                    : original?.status !== "published"
                      ? ui("Опубликовать новость")
                      : ui("Сохранить изменения")}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => setEditor(false)}
              >
                {ui("Отмена")}
              </button>
            </div>
          </form>
        </>
      ) : (
        <>
          <div className="section-row">
            <p className="muted">
              {ui(
                "Официальные публикации Banking System. Черновики скрыты от посетителей.",
              )}
            </p>
            <button disabled={busy} onClick={newArticle}>
              {ui("Создать новость")}
            </button>
            <button
              disabled={busy}
              onClick={() => {
                newArticle();
                const date = new Date().toISOString().slice(0, 10);
                setForm({
                  ...blank(),
                  title: ui("Обновление политики конфиденциальности"),
                  summary: ui(
                    "Изменена политика обработки персональных данных.",
                  ),
                  content:
                    date +
                    "\n\n" +
                    ui("Опишите существенные изменения перед публикацией.") +
                    "\n\n[" +
                    ui("Политика конфиденциальности") +
                    "](/privacy)",
                });
              }}
            >
              {ui("Новость об изменении политики")}
            </button>
          </div>
          <div className="news-admin-filters">
            <label>
              {ui("Категория")}
              <select
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="">{ui("Все категории")}</option>
                {Object.entries(newsCategories()).map(([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {ui("Статус")}
              <select
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="">{ui("Все статусы")}</option>
                <option value="draft">{ui("Черновики")}</option>
                <option value="published">{ui("Опубликованные")}</option>
              </select>
            </label>
            <button
              className="secondary"
              disabled={loading}
              onClick={() => setReload((x) => x + 1)}
            >
              {ui("Обновить список")}
            </button>
          </div>
          {loading ? (
            <Loading label={ui("Загружаем новости…")} />
          ) : page?.items.length ? (
            <div className="news-admin-list">
              {page.items.map((item) => (
                <article className="panel" key={item.id}>
                  <div className="news-tags">
                    <span className={"news-category category-" + item.category}>
                      {newsCategories()[item.category]}
                    </span>
                    <span className="tag">
                      {item.status === "published"
                        ? ui("Опубликована")
                        : ui("Черновик")}
                    </span>
                    {item.is_pinned && (
                      <span className="news-pinned">{ui("Закреплено")}</span>
                    )}
                  </div>
                  <h3>{item.title}</h3>
                  {item.summary && <p>{item.summary}</p>}
                  <p className="news-meta">
                    <span>
                      {ui("Автор: ")}
                      {item.author}
                    </span>
                    <span>
                      {ui("Изменена ")}
                      {dateTime(item.updated_at, undefined)}
                    </span>
                    {item.published_at && (
                      <span>
                        {ui("Опубликована ")}
                        {dateTime(item.published_at, undefined)}
                      </span>
                    )}
                  </p>
                  <div className="news-actions">
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => void open(item)}
                    >
                      {ui("Редактировать")}
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void action(
                          item,
                          item.status === "published" ? "unpublish" : "publish",
                        )
                      }
                    >
                      {item.status === "published"
                        ? ui("Снять с публикации")
                        : ui("Опубликовать")}
                    </button>
                    {item.status === "published" && (
                      <a
                        href={"/news/" + item.slug}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {ui("Посмотреть ↗")}
                      </a>
                    )}
                    <button
                      className="text-button danger"
                      disabled={busy}
                      onClick={() => {
                        setDeleting(item);
                        setConfirmed(false);
                      }}
                    >
                      {ui("Удалить")}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            !error && (
              <Empty
                icon="info"
                title={ui("Публикаций пока нет")}
                description={ui(
                  "Создайте первую новость и сохраните её как черновик или опубликуйте.",
                )}
              />
            )
          )}
          {page && (offset > 0 || page.next_offset !== null) && (
            <nav
              className="news-pagination"
              aria-label={ui("Страницы управления новостями")}
            >
              <button
                className="secondary"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                {ui("Назад")}
              </button>
              <span>
                {ui("Страница ")}
                {Math.floor(offset / 20) + 1}
              </span>
              <button
                className="secondary"
                disabled={page.next_offset === null || loading}
                onClick={() => setOffset(page.next_offset!)}
              >
                {ui("Далее")}
              </button>
            </nav>
          )}
        </>
      )}
      {deleting && (
        <Modal
          title={ui("Удалить новость?")}
          closeLabel={ui("Закрыть")}
          onClose={() => {
            if (!busy) setDeleting(undefined);
          }}
        >
          <p>
            «{deleting.title}
            {ui("» исчезнет из списка и с публичной страницы.")}
          </p>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            {ui("Подтверждаю удаление этой новости")}
          </label>
          <Notice error={error} />
          <div className="flow-footer">
            <button
              className="danger-button"
              disabled={busy || !confirmed}
              onClick={() => void action(deleting, "delete")}
            >
              {busy ? ui("Удаляем…") : ui("Удалить новость")}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setDeleting(undefined)}
            >
              {ui("Отмена")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
