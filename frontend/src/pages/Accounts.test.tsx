// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { TestBank, testAccount, testDashboard } from "../test-support";
import { Accounts } from "./Accounts";
vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, balance: vi.fn(), activity: vi.fn() },
  };
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("does not request history or expose its action without history.read", async () => {
  const account = {
    ...testAccount,
    permissions: ["accounts.read", "balance.read"],
  };
  vi.mocked(api.balance).mockResolvedValue(account);
  render(
    <TestBank overrides={{ data: { ...testDashboard, accounts: [account] } }}>
      <Accounts />
    </TestBank>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: /Основной счёт.*100,00/ }),
  );
  expect(
    await screen.findByText("Просмотр истории не разрешён владельцем счёта"),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Вся история" })).toBeNull();
  expect(api.activity).not.toHaveBeenCalled();
});
