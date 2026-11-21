// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { News } from "./News";
import { Administration } from "./Administration";
import { NewsManager } from "./admin/NewsManager";
import { I18nProvider } from "../i18n";
import { newsApi, type NewsItem } from "../news-api";
import { TestBank } from "../test-support";

vi.mock("../news-api", async (original) => {
  const actual = await original<typeof import("../news-api")>();
  return {
    ...actual,
    newsApi: {
      list: vi.fn(),
      article: vi.fn(),
      adminList: vi.fn(),
      adminArticle: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      action: vi.fn(),
    },
  };
});
const item: NewsItem = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "novost-1",
  title: "Обновление банка",
  summary: "Новые возможности",
  content: "**Полный текст** новости",
  category: "updates",
  cover_image: null,
  status: "published",
  is_pinned: true,
  author: "Редактор банка",
  created_at: "2026-10-25T10:00:00Z",
  updated_at: "2026-10-25T10:00:00Z",
  published_at: "2026-10-25T10:00:00Z",
};
beforeEach(() => {
  history.replaceState({}, "", "/news");
  localStorage.clear();
  window.scrollTo = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("localizes public news controls while preserving user content with an English preference", async () => {
  localStorage.setItem("horizon.language", "en");
  vi.mocked(newsApi.list).mockResolvedValue({ items: [item], next_offset: 12 });
  render(
    <I18nProvider>
      <News />
    </I18nProvider>,
  );
  const user = userEvent.setup();
  expect(await screen.findByRole("heading", { name: item.title })).toBeTruthy();
  expect(screen.getByText("Author: Редактор банка")).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Color theme" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(newsApi.list).toHaveBeenLastCalledWith(
      { category: "", offset: 12, limit: 12 },
      expect.anything(),
    ),
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Category" }),
    "rules",
  );
  await waitFor(() =>
    expect(newsApi.list).toHaveBeenLastCalledWith(
      { category: "rules", offset: 0, limit: 12 },
      expect.anything(),
    ),
  );
});

it("renders an empty state without invented publications", async () => {
  vi.mocked(newsApi.list).mockResolvedValue({ items: [], next_offset: null });
  render(
    <I18nProvider>
      <News />
    </I18nProvider>,
  );
  expect(await screen.findByText("Новостей пока нет")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Далее" })).toBeNull();
});

it("renders complete Markdown safely and never executes HTML or dangerous links", async () => {
  history.replaceState({}, "", "/news/novost-1");
  vi.mocked(newsApi.article).mockResolvedValue({
    ...item,
    content:
      '**Полный текст**\n\n<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n[Опасная ссылка](javascript:alert(1))',
  });
  const { container } = render(
    <I18nProvider>
      <News />
    </I18nProvider>,
  );
  expect(await screen.findByText("Полный текст")).toBeTruthy();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector(".bank-markdown img")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(screen.getByRole("link", { name: "← Все новости" })).toBeTruthy();
});

it("exposes the editor only for the dedicated administrative permission", async () => {
  vi.mocked(newsApi.adminList).mockResolvedValue({
    items: [],
    next_offset: null,
  });
  const view = render(
    <TestBank>
      <Administration permissions={[]} />
    </TestBank>,
  );
  expect(screen.queryByText("Управление новостями")).toBeNull();
  expect(newsApi.adminList).not.toHaveBeenCalled();
  view.rerender(
    <TestBank>
      <Administration permissions={["admin.news.manage"]} />
    </TestBank>,
  );
  expect(
    await screen.findByRole("button", { name: "Создать новость" }),
  ).toBeTruthy();
});

it("previews Markdown before publishing and defaults new articles to a private draft", async () => {
  vi.mocked(newsApi.adminList).mockResolvedValue({
    items: [],
    next_offset: null,
  });
  vi.mocked(newsApi.create).mockResolvedValue({
    ...item,
    title: "Первая новость",
  });
  render(
    <TestBank>
      <NewsManager />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Создать новость" }),
  );
  expect(
    (
      screen.getByRole("combobox", {
        name: "Статус публикации",
      }) as HTMLSelectElement
    ).value,
  ).toBe("draft");
  await user.type(screen.getByLabelText("Заголовок"), "Первая новость");
  await user.type(
    screen.getByLabelText("Основной текст", { exact: false }),
    "**Готовый текст**",
  );
  await user.click(screen.getByRole("button", { name: "Предпросмотр" }));
  expect(screen.getByText("Готовый текст")).toBeTruthy();
  expect(newsApi.create).not.toHaveBeenCalled();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Статус публикации" }),
    "published",
  );
  await user.click(
    screen.getByRole("button", { name: "Опубликовать новость" }),
  );
  await waitFor(() =>
    expect(newsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Первая новость",
        content: "**Готовый текст**",
        status: "published",
      }),
    ),
  );
  expect(
    await screen.findByRole("link", { name: "Открыть публикацию ↗" }),
  ).toBeTruthy();
});

it("requires an explicit deletion confirmation and sends the current article version", async () => {
  vi.mocked(newsApi.adminList)
    .mockResolvedValueOnce({ items: [item], next_offset: null })
    .mockResolvedValue({ items: [], next_offset: null });
  vi.mocked(newsApi.action).mockResolvedValue(undefined);
  render(
    <TestBank>
      <NewsManager />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Удалить" }));
  const confirm = screen.getByRole("button", { name: "Удалить новость" });
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  expect(newsApi.action).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("checkbox", { name: "Подтверждаю удаление этой новости" }),
  );
  await user.click(confirm);
  await waitFor(() =>
    expect(newsApi.action).toHaveBeenCalledWith(item, "delete"),
  );
  expect(await screen.findByText("Новость удалена.")).toBeTruthy();
});
