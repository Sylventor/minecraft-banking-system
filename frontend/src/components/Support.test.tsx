// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { request } from "../api";
import { SupportPanel } from "./Support";

vi.mock("../api", () => ({ request: vi.fn() }));
const original = {
  id: "ticket",
  number: 1,
  title: "Банкомат",
  category: "atm",
  status: "open",
  assigned_to: null,
  updated_at: "2030-01-01T12:00:00Z",
  unread: 1,
  messages: [
    {
      id: 1,
      author_id: "staff",
      author: "Оператор",
      sender_kind: "staff",
      body: "<img src=x onerror=alert(1)>",
      created_at: "2030-01-01T12:00:00Z",
    },
  ],
};
beforeEach(() => {
  vi.mocked(request).mockImplementation(async (path, init) => {
    if (path.includes("/read")) return {} as never;
    if (init?.method === "POST") return original as never;
    if (path.includes("/tickets?"))
      return { items: [original], next_offset: null } as never;
    return original as never;
  });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("renders support messages as text and sends Enter while preserving Shift+Enter", async () => {
  const view = render(<SupportPanel />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Банкомат/ }));
  await screen.findByText("<img src=x onerror=alert(1)>");
  expect(view.container.querySelector("img")).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "Сообщение" }),
    "Первая строка",
  );
  await user.keyboard("{Shift>}{Enter}{/Shift}Вторая строка");
  expect(
    vi
      .mocked(request)
      .mock.calls.filter(
        ([, init]) =>
          init?.method === "POST" && !String(init.body).includes("through_id"),
      ),
  ).toHaveLength(0);
  await user.keyboard("{Enter}");
  await vi.waitFor(() => {
    const send = vi
      .mocked(request)
      .mock.calls.find(([path]) => path.endsWith("/messages"));
    expect(send).toBeTruthy();
    expect(JSON.parse(String(send![1]?.body))).toMatchObject({
      body: "Первая строка\nВторая строка",
      client_id: expect.any(String),
    });
  });
});

it("creates a ticket with a topic and category without a navigation", async () => {
  render(<SupportPanel />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Новое обращение" }));
  await user.type(screen.getByLabelText("Тема"), "Не работает ATM");
  await user.selectOptions(screen.getByLabelText("Категория"), "atm");
  await user.type(
    screen.getByRole("textbox", { name: "Сообщение" }),
    "Проверьте операцию",
  );
  await user.click(screen.getByRole("button", { name: "Создать обращение" }));
  await vi.waitFor(() =>
    expect(
      vi
        .mocked(request)
        .mock.calls.some(
          ([path, init]) =>
            path === "/support/tickets" && init?.method === "POST",
        ),
    ).toBe(true),
  );
});
