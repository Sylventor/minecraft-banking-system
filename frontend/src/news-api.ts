import { localize as ui } from "./i18n";
import { request } from "./api";
export const newsCategories = () =>
  ({
    general: ui("Общие новости"),
    updates: ui("Обновления"),
    maintenance: ui("Технические работы"),
    important: ui("Важные объявления"),
    rules: ui("Изменения правил"),
    exchange: ui("Фондовая биржа"),
  }) as const;
export type NewsCategory = keyof ReturnType<typeof newsCategories>;
export type NewsInput = {
  title: string;
  summary: string;
  content: string;
  category: NewsCategory;
  cover_image: string | null;
  status: "draft" | "published";
  is_pinned: boolean;
};
export type NewsItem = NewsInput & {
  id: string;
  slug: string;
  author: string;
  created_at: string;
  updated_at: string;
  published_at: string | null;
};
export type NewsCard = Omit<NewsItem, "content">;
export type NewsPage = {
  items: NewsCard[];
  next_offset: number | null;
};
function query(params: Record<string, string | number>) {
  const search = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== "")
      .map(([k, v]) => [k, String(v)]),
  );
  return search.size ? "?" + search : "";
}
export const newsApi = {
  list: (params: Record<string, string | number>, signal?: AbortSignal) =>
    request<NewsPage>("/news" + query(params), { signal, cache: "no-store" }),
  article: (slug: string, signal?: AbortSignal) =>
    request<NewsItem>("/news/" + encodeURIComponent(slug), {
      signal,
      cache: "no-store",
    }),
  adminList: (params: Record<string, string | number>) =>
    request<NewsPage>("/admin/news" + query(params), { cache: "no-store" }),
  adminArticle: (id: string) =>
    request<NewsItem>("/admin/news/" + encodeURIComponent(id), {
      cache: "no-store",
    }),
  create: (body: NewsInput) =>
    request<NewsItem>("/admin/news", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  update: (item: NewsItem, body: NewsInput) =>
    request<NewsItem>("/admin/news/" + item.id, {
      method: "PATCH",
      body: JSON.stringify({ ...body, expected_updated_at: item.updated_at }),
    }),
  action: (item: NewsCard, action: "publish" | "unpublish" | "delete") =>
    request<NewsItem | undefined>(
      "/admin/news/" + item.id + (action === "delete" ? "" : "/" + action),
      {
        method: action === "delete" ? "DELETE" : "POST",
        body: JSON.stringify({
          confirmed: true,
          expected_updated_at: item.updated_at,
        }),
      },
    ),
};
