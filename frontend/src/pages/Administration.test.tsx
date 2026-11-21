// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { adminApi, notificationApi, termsApi } from "../admin-api";
import { Administration } from "./Administration";
import { Terms } from "./Terms";
import { NotificationSettings } from "./NotificationSettings";
import { TestBank } from "../test-support";

vi.mock("../admin-api", async (original) => {
  const real = await original<typeof import("../admin-api")>();
  return {
    ...real,
    adminApi: {
      ...real.adminApi,
      get: vi.fn(),
      users: vi.fn(),
      effective: vi.fn(),
    },
    termsApi: { current: vi.fn() },
    notificationApi: {
      preferences: vi.fn(),
      save: vi.fn(),
      deliveries: vi.fn(),
    },
  };
});
beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("shows only authorized administration sections and opens a user found by bank name", async () => {
  const user = {
    id: "registered-user",
    display_name: "Игрок",
    bank_username: "Player",
    status: "active",
    discord_id: "123456789",
  };
  vi.mocked(adminApi.get).mockResolvedValue({
    items: [user],
    next_cursor: null,
  });
  vi.mocked(adminApi.effective).mockResolvedValue({
    permissions: [],
    roles: [],
    legacy_permissions: [],
  });
  render(
    <TestBank>
      <Administration permissions={["admin.users.read"]} />
    </TestBank>,
  );
  const navigation = screen.getByRole("navigation", {
    name: "Разделы администрации",
  });
  expect(
    within(navigation).getByRole("button", { name: "Пользователи" }),
  ).toBeTruthy();
  expect(
    within(navigation).queryByRole("button", { name: "Финансы" }),
  ).toBeNull();
  expect(
    within(navigation).queryByRole("button", { name: "Роли и разрешения" }),
  ).toBeNull();
  await userEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  const card = screen.getByRole("dialog", { name: "Карточка пользователя" });
  expect(within(card).getByText("@Player")).toBeTruthy();
  expect(
    within(card).queryByRole("button", { name: "Заблокировать профиль" }),
  ).toBeNull();
  expect(within(card).queryByRole("textbox")).toBeNull();
});

it("renders the unpublished public terms page without an authentication requirement", async () => {
  vi.mocked(termsApi.current).mockResolvedValue({ current: null });
  render(<Terms />);
  expect(
    await screen.findByText(
      "Правила пользования Banking System пока не опубликованы.",
    ),
  ).toBeTruthy();
  expect(screen.getByText("Следите за обновлениями.")).toBeTruthy();
  expect(screen.queryByText(/Согласен/)).toBeNull();
});

it("persists Discord category preferences and shows a closed DM delivery status", async () => {
  const preferences = {
    discord_enabled: true,
    security_enabled: true,
    transfers_enabled: true,
    incoming_enabled: true,
    outgoing_enabled: true,
    organizations_enabled: true,
  };
  vi.mocked(notificationApi.preferences).mockResolvedValue(preferences);
  vi.mocked(notificationApi.save).mockImplementation(async (x) => x);
  vi.mocked(notificationApi.deliveries).mockResolvedValue({
    items: [
      {
        id: "delivery",
        title: "Входящий перевод",
        status: "failed",
        attempts: 1,
        last_error: "dm_closed",
        created_at: "2026-10-21T12:00:00Z",
        sent_at: null,
      },
    ],
    next_offset: null,
    worker_online: true,
  });
  render(<NotificationSettings />);
  expect(
    await screen.findByText(/Личные сообщения Discord закрыты/),
  ).toBeTruthy();
  await userEvent.click(
    await screen.findByRole("checkbox", {
      name: "Исходящие переводы и списания",
    }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Сохранить уведомления" }),
  );
  await screen.findByText("Настройки уведомлений сохранены");
  expect(
    vi.mocked(notificationApi.save).mock.calls[0][0].outgoing_enabled,
  ).toBe(false);
});
