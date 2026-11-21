// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, ApiFailure } from "./api";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { testDashboard } from "./test-support";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: { ...actual.api, dashboard: vi.fn() } };
});
beforeEach(() => {
  localStorage.clear();
  location.hash = "";
  window.scrollTo = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function mount() {
  render(
    <I18nProvider>
      <App />
    </I18nProvider>,
  );
}

it("shows real returned balance, Russian navigation and no administrative entry without grants", async () => {
  vi.mocked(api.dashboard).mockResolvedValue(testDashboard);
  mount();
  await screen.findByRole("heading", { name: /Рады видеть вас/ });
  const nav = screen.getByRole("navigation", { name: "Основная навигация" });
  for (const name of [
    "Главная",
    "Переводы",
    "Счета",
    "Биржа",
    "Организации",
    "Ещё",
    "Настройки",
  ])
    expect(within(nav).getByRole("button", { name })).toBeTruthy();
  expect(within(nav).queryByRole("button", { name: "История" })).toBeNull();
  const mobile = screen.getByRole("navigation", {
    name: "Мобильная навигация",
  });
  expect(
    within(mobile)
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["Главная", "Счета", "Переводы", "Биржа", "Ещё"]);
  expect(
    screen.queryByRole("button", { name: "Администрирование" }),
  ).toBeNull();
  expect(screen.getAllByText(/100,00/).length).toBeGreaterThan(0);
});

it("does not convert a backend outage into a fake balance or a login screen", async () => {
  vi.mocked(api.dashboard).mockRejectedValue(
    new ApiFailure(
      {
        error: "unavailable",
        code: "core_unavailable",
        request_id: "test-request",
      },
      503,
    ),
  );
  mount();
  expect(
    await screen.findByRole("heading", { name: "Не удалось загрузить банк" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "Добро пожаловать" }),
  ).toBeNull();
  expect(screen.queryByText("0,00 ДБ")).toBeNull();
});

it("shows Discord sign-in on expired session", async () => {
  vi.mocked(api.dashboard).mockRejectedValue(
    new ApiFailure(
      { error: "login", code: "unauthorized", request_id: "test-request" },
      401,
    ),
  );
  mount();
  expect(
    await screen.findByRole("button", { name: "Продолжить через Discord" }),
  ).toBeTruthy();
});

it("separates developer tools from the primary navigation", async () => {
  vi.mocked(api.dashboard).mockResolvedValue(testDashboard);
  mount();
  const open = await screen.findByRole("button", { name: "Для разработчиков" });
  expect(
    within(
      screen.getByRole("navigation", { name: "Основная навигация" }),
    ).queryByRole("button", { name: "Для разработчиков" }),
  ).toBeNull();
  await userEvent.click(open);
  expect(
    await screen.findByRole("heading", { name: "Для разработчиков" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /документац/i }).getAttribute("href"),
  ).toBe("/api/docs");
  await userEvent.click(screen.getByRole("link", { name: "К содержимому" }));
  expect(document.activeElement?.id).toBe("main-content");
  expect(
    screen.getByRole("heading", { name: "Для разработчиков" }),
  ).toBeTruthy();
});
