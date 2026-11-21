// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { exchangeApi } from "./exchange-api";
afterEach(() => vi.unstubAllGlobals());
it("omits optional blank UUID filters instead of sending a value rejected by FastAPI", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    });
  vi.stubGlobal("fetch", fetcher);
  await exchangeApi.get("stocks", { q: "", cursor: "", limit: "25" });
  await exchangeApi.admin("issuers", { cursor: "" });
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/exchange/stocks?limit=25");
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/admin/exchange/issuers");
});
it("preserves nonempty cursors and correctly encodes company searches", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetcher);
  const cursor = "00000000-0000-0000-0000-000000000026";
  await exchangeApi.get("stocks", { q: "Mine & Build", cursor });
  const url = new URL(fetcher.mock.calls[0][0], "http://localhost");
  expect(url.searchParams.get("q")).toBe("Mine & Build");
  expect(url.searchParams.get("cursor")).toBe(cursor);
});
