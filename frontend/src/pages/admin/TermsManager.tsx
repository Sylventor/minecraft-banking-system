import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../../i18n";
import { useEffect, useState } from "react";
import { adminApi, termsApi, type TermsVersion } from "../../admin-api";
import { Markdown } from "../../components/Markdown";
import { Notice } from "../../components/Notice";
import { dateTime, localError } from "../../i18n";
import { ActionDialog, NextPage, PageState, useAdminPage } from "./Shared";
export function TermsManager() {
  useLocale();
  const [offset, setOffset] = useState(0),
    [content, setContent] = useState(""),
    [filename, setFilename] = useState("rules.md"),
    [error, setError] = useState(""),
    [current, setCurrent] = useState<TermsVersion | null>();
  const [action, setAction] = useState<{
    type: "publish" | "restore" | "unpublish";
    version?: TermsVersion;
  }>();
  const history = useAdminPage<TermsVersion>("terms?offset=" + offset);
  useEffect(() => {
    termsApi
      .current()
      .then((x) => setCurrent(x.current))
      .catch((e) => setError(localError(e)));
  }, []);
  async function loadFile(file: File) {
    setError("");
    try {
      if (!/\.md$/i.test(file.name) || file.size > 131072)
        throw new Error(
          ui("Файл должен иметь расширение .md и размер до 128 КиБ"),
        );
      const text = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      setFilename(file.name);
      setContent(text);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : ui("Не удалось прочитать Markdown"),
      );
    }
  }
  async function perform() {
    if (!action) return;
    if (action.type === "publish")
      await adminApi.write("terms", {
        filename,
        markdown: content,
        confirmed: true,
      });
    if (action.type === "restore")
      await adminApi.write(`terms/${action.version!.id}/restore`, {
        confirmed: true,
      });
    if (action.type === "unpublish")
      await adminApi.write("terms/unpublish", { confirmed: true });
    setCurrent((await termsApi.current()).current);
    history.reload();
  }
  async function view(id: string) {
    try {
      const value = await adminApi.get<TermsVersion>("terms/" + id);
      setContent(value.markdown);
      setFilename("rules.md");
    } catch (e) {
      setError(localError(e));
    }
  }
  return (
    <>
      <div className="panel">
        <div className="section-heading">
          <h3>{ui("Текущая публикация")}</h3>
          <a href="/terms" target="_blank" rel="noopener noreferrer">
            {ui("Открыть публичную страницу")}
          </a>
        </div>
        <p>
          {current
            ? uit(
                "Опубликована версия {0} · {1}",
                current.version,
                dateTime(current.published_at, undefined),
              )
            : ui("Правила пока не опубликованы")}
        </p>
        {current && (
          <div className="admin-toolbar">
            <button
              className="secondary"
              onClick={() => {
                setContent(current.markdown);
                setFilename("rules.md");
              }}
            >
              {ui("Просмотреть текущую")}
            </button>
            <button
              className="danger-button"
              onClick={() => setAction({ type: "unpublish" })}
            >
              {ui("Снять с публикации")}
            </button>
          </div>
        )}
      </div>
      <Notice error={error} />
      <label className="file-upload">
        {ui("Загрузить Markdown (.md, до 128 КиБ)")}
        <input
          type="file"
          accept=".md,text/markdown"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void loadFile(file);
          }}
        />
      </label>
      <div className="terms-editor">
        <label>
          {ui("Содержимое Markdown")}
          <textarea
            aria-label={ui("Содержимое Markdown")}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            maxLength={131072}
          />
        </label>
        <section className="panel">
          <h3>{ui("Предпросмотр")}</h3>
          <Markdown content={content} />
        </section>
      </div>
      <button
        disabled={
          !content.trim() || new TextEncoder().encode(content).length > 131072
        }
        onClick={() => setAction({ type: "publish" })}
      >
        {ui("Опубликовать новую версию")}
      </button>
      <h3>{ui("История публикаций")}</h3>
      <PageState
        loading={!history.data}
        error={history.error}
        empty={!!history.data && !history.data.items.length}
      />
      {history.data?.items.map((v) => (
        <div className="delivery-row" key={v.id}>
          <span>
            <b>
              {ui("Версия ")}
              {v.version} ·{" "}
              {v.status === "published"
                ? ui("Опубликована")
                : v.status === "unpublished"
                  ? ui("Снята с публикации")
                  : ui("Архив")}
            </b>
            <small>
              {dateTime(v.published_at, undefined)} · @{v.author}
            </small>
          </span>
          <div className="admin-toolbar">
            <button className="secondary" onClick={() => void view(v.id)}>
              {ui("Просмотреть")}
            </button>
            <button
              className="secondary"
              onClick={() => setAction({ type: "restore", version: v })}
            >
              {ui("Восстановить")}
            </button>
          </div>
        </div>
      ))}
      <NextPage
        next={history.data?.next_offset}
        onNext={() => setOffset(history.data!.next_offset!)}
        onFirst={() => setOffset(0)}
      />
      {action && (
        <ActionDialog
          title={
            action.type === "publish"
              ? ui("Опубликовать правила?")
              : action.type === "restore"
                ? ui("Восстановить версию?")
                : ui("Снять правила с публикации?")
          }
          reasonRequired={false}
          onClose={() => setAction(undefined)}
          onConfirm={perform}
        >
          <p>
            {action.type === "restore"
              ? uit(
                  "Будет опубликована новая версия с содержимым версии {0}.",
                  action.version!.version,
                )
              : action.type === "unpublish"
                ? ui(
                    "Публичная страница покажет сообщение об отсутствии правил.",
                  )
                : ui("Этот текст станет доступен всем посетителям банка.")}
          </p>
        </ActionDialog>
      )}
    </>
  );
}
