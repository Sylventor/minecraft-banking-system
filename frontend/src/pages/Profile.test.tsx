// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, request } from "../api";
import { Profile } from "./Profile";
import { TestBank } from "../test-support";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    request: vi.fn(),
    api: {
      ...actual.api,
      unlinkMinecraft: vi.fn(),
      sessions: vi.fn(),
    },
  };
});
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.sessions).mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("Minecraft settings proof", () => {
  it("honestly disables verification when no trusted server exists", async () => {
    vi.mocked(request).mockResolvedValue({
      integration_enabled: false,
      link: null,
      pending: null,
    });
    render(
      <TestBank>
        <Profile />
      </TestBank>,
    );
    await screen.findByText(/Система банкоматов пока не настроена/);
    expect(screen.queryByRole("button", { name: "Получить код" })).toBeNull();
  });
  it("keeps a new code pending rather than claiming verified ownership", async () => {
    const state = {
      integration_enabled: true,
      link: null,
      pending: null,
    };
    vi.mocked(request)
      .mockResolvedValueOnce(state)
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({
        ...state,
        pending: {
          id: "challenge-1",
          nickname: "DemoPlayer",
          status: "delivered",
          expires_at: "2030-01-01T00:10:00Z",
          attempts: 0,
        },
      });
    render(
      <TestBank>
        <Profile />
      </TestBank>,
    );
    await userEvent.type(
      await screen.findByLabelText("Minecraft-ник"),
      "DemoPlayer",
    );
    await userEvent.click(screen.getByRole("button", { name: "Получить код" }));
    await screen.findByLabelText("Код подтверждения");
    const section = screen
      .getByRole("heading", { name: "Привязка Minecraft" })
      .closest("section") as HTMLElement;
    expect(within(section).queryByText("Подтверждён")).toBeNull();
    expect(
      screen.queryByText("Minecraft-аккаунт успешно привязан."),
    ).toBeNull();
  });
  it("requires explicit confirmation before unlinking", async () => {
    vi.mocked(request).mockResolvedValue({
      integration_enabled: true,
      pending: null,
      link: {
        nickname: "Player",
        player_uuid: "player-uuid",
        verified_at: "2030-01-01T00:00:00Z",
      },
    });
    vi.mocked(api.unlinkMinecraft).mockResolvedValue(undefined);
    render(
      <TestBank>
        <Profile />
      </TestBank>,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Отвязать Minecraft" }),
    );
    expect(api.unlinkMinecraft).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Подтвердить отвязку" }),
    );
    expect(api.unlinkMinecraft).toHaveBeenCalledOnce();
  });
});
