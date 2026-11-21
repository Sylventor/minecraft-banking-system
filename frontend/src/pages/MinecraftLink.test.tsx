// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MinecraftLink } from "./MinecraftLink";
import { ApiFailure, request } from "../api";
import { TestBank } from "../test-support";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  request: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("requests a code for the nickname and confirms the game-delivered code on the website", async () => {
  const state = { integration_enabled: true, link: null, pending: null };
  const pending = {
    id: "challenge",
    nickname: "PlayerName",
    status: "delivered",
    expires_at: "2026-10-25T10:05:00Z",
    attempts: 0,
  };
  vi.mocked(request)
    .mockResolvedValueOnce(state)
    .mockResolvedValueOnce({})
    .mockResolvedValueOnce({ ...state, pending });
  render(
    <TestBank>
      <MinecraftLink />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Minecraft-ник"), "PlayerName");
  await user.click(screen.getByRole("button", { name: "Получить код" }));
  await user.type(await screen.findByLabelText("Код подтверждения"), "123456");
  vi.mocked(request)
    .mockResolvedValueOnce({})
    .mockResolvedValueOnce({
      ...state,
      link: { nickname: "PlayerName", verified_at: "2026-10-25T10:00:00Z" },
    });
  await user.click(
    screen.getByRole("button", { name: "Подтвердить привязку" }),
  );
  expect(
    await screen.findByText("Minecraft-аккаунт успешно привязан."),
  ).toBeTruthy();
  expect(request).toHaveBeenCalledWith(
    "/minecraft/link/verify",
    expect.objectContaining({
      body: JSON.stringify({ challenge_id: "challenge", code: "123456" }),
    }),
  );
});
it("never shows a sent-code confirmation after a delivery failure", async () => {
  vi.mocked(request)
    .mockResolvedValueOnce({
      integration_enabled: true,
      link: null,
      pending: null,
    })
    .mockRejectedValueOnce(
      new ApiFailure(
        {
          error: "Не удалось отправить код подтверждения",
          code: "request_rejected",
          request_id: "test",
        },
        503,
      ),
    )
    .mockResolvedValueOnce({
      integration_enabled: true,
      link: null,
      pending: null,
    });
  render(
    <TestBank>
      <MinecraftLink />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Minecraft-ник"), "PlayerName");
  await user.click(screen.getByRole("button", { name: "Получить код" }));
  expect(await screen.findByText(/Не удалось отправить код/)).toBeTruthy();
  expect(screen.queryByLabelText("Код подтверждения")).toBeNull();
});
