// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import {
  TestBank,
  testAccount,
  testDashboard,
  testQuote,
  testRecipient,
} from "../test-support";
import { Transfer } from "./Transfer";

vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      organizations: vi.fn(),
      savedRecipients: vi.fn(),
      recentRecipients: vi.fn(),
      prepareTransfer: vi.fn(),
      confirmTransfer: vi.fn(),
      quote: vi.fn(),
    },
  };
});
const reserve = {
  ...testAccount,
  id: "reserve",
  public_number: "HB-RESERVE",
  name: "Личный резерв",
  is_primary: false,
};
const corporate = {
  ...testAccount,
  id: "corporate",
  account_type: "organization",
  organization_id: "org",
  owner_id: "",
  name: "Магазин",
  public_number: "HB-SHOP",
  permissions: ["accounts.read", "balance.read"],
};
const foreign = {
  ...testAccount,
  id: "foreign",
  owner_id: "another-user",
  name: "Чужой личный",
};
const dashboard = {
  ...testDashboard,
  accounts: [testAccount, reserve, corporate, foreign],
};
const quote = {
  ...testQuote,
  internal: true,
  recipient: {
    ...testRecipient,
    account_id: reserve.id,
    public_number: reserve.public_number,
    display_name: "Личный резерв",
    owner_id: testAccount.owner_id,
  },
};
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.mocked(api.organizations).mockResolvedValue([{ id: "org", name: "Shop" }]);
  vi.mocked(api.savedRecipients).mockResolvedValue([]);
  vi.mocked(api.recentRecipients).mockResolvedValue([]);
  vi.mocked(api.prepareTransfer).mockResolvedValue(quote);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function mount() {
  return render(
    <TestBank overrides={{ data: dashboard }}>
      <Transfer />
    </TestBank>,
  );
}

it("quotes the exact destination account, excludes foreign personal accounts and never debits during preview", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Между счетами" }));
  const target = screen.getByLabelText("Куда");
  expect(within(target).queryByRole("option", { name: /Чужой/ })).toBeNull();
  expect(
    within(target).queryByRole("option", { name: /Основной счёт/ }),
  ).toBeNull();
  await user.selectOptions(target, reserve.id);
  await user.type(screen.getByLabelText("Сумма перевода"), "25,50");
  await user.click(screen.getByRole("button", { name: "Продолжить" }));
  await screen.findByRole("button", { name: "Подтвердить перевод" });
  expect(api.prepareTransfer).toHaveBeenCalledWith({
    internal: true,
    source_account_id: testAccount.id,
    recipient: { kind: "account", value: reserve.public_number },
    amount: "25.50",
    reason: "",
  });
  expect(api.confirmTransfer).not.toHaveBeenCalled();
});

it("swaps accounts and lets a view-only organizational account receive without becoming a debit source", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Между счетами" }));
  await user.selectOptions(screen.getByLabelText("Куда"), reserve.id);
  await user.click(
    screen.getByRole("button", { name: "Поменять счета местами" }),
  );
  expect((screen.getByLabelText("Откуда") as HTMLSelectElement).value).toBe(
    reserve.id,
  );
  expect((screen.getByLabelText("Куда") as HTMLSelectElement).value).toBe(
    testAccount.id,
  );
  await waitFor(() =>
    expect(
      within(screen.getByLabelText("Куда")).getByRole("option", {
        name: /Магазин/,
      }),
    ).toBeTruthy(),
  );
  await user.selectOptions(screen.getByLabelText("Куда"), corporate.id);
  expect(
    (
      screen.getByRole("button", {
        name: "Поменять счета местами",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    within(screen.getByLabelText("Откуда")).queryByRole("option", {
      name: /Магазин/,
    }),
  ).toBeNull();
});

it("restores the internal pending confirmation and retries the same quote after a lost response", async () => {
  sessionStorage.setItem(
    "horizon.pending." + testAccount.owner_id,
    JSON.stringify({ id: quote.id, submitted: true }),
  );
  vi.mocked(api.quote).mockResolvedValue(quote);
  vi.mocked(api.confirmTransfer).mockRejectedValue(new TypeError("lost"));
  mount();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", {
      name: "Проверить результат / повторить",
    }),
  );
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Отмена" })).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Проверить результат / повторить" }),
  );
  expect(vi.mocked(api.confirmTransfer).mock.calls[0][0]).toEqual(
    vi.mocked(api.confirmTransfer).mock.calls[1][0],
  );
  expect(api.prepareTransfer).not.toHaveBeenCalled();
});
