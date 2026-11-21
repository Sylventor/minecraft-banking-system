// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { businessApi } from "../business-api";
import { exchangeApi } from "../exchange-api";
import { TestBank, testAccount } from "../test-support";
import { IssuerWizard } from "./IssuerWizard";
const org = {
  id: "org",
  name: "Horizon Construction",
  accounts: [
    { ...testAccount, organization_id: "org", account_type: "organization" },
  ],
};
vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, organizations: vi.fn(), organization: vi.fn() },
  };
});
vi.mock("../business-api", () => ({ businessApi: { permissions: vi.fn() } }));
vi.mock("../exchange-api", async (original) => {
  const actual = await original<typeof import("../exchange-api")>();
  return {
    ...actual,
    exchangeApi: { ...actual.exchangeApi, get: vi.fn(), write: vi.fn() },
  };
});
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.organizations).mockResolvedValue([org]);
  vi.mocked(api.organization).mockResolvedValue(org);
  vi.mocked(businessApi.permissions).mockResolvedValue({
    permissions: ["exchange.issuer.manage"],
  });
  vi.mocked(exchangeApi.get).mockResolvedValue({ available: true });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const submitted = vi.fn(async () => undefined);
function mount() {
  return render(
    <TestBank>
      <IssuerWizard
        organization={org}
        permissions={["exchange.issuer.manage"]}
        onSubmitted={submitted}
      />
    </TestBank>,
  );
}
async function fill() {
  const user = userEvent.setup();
  await screen.findByLabelText("Банковский счёт компании");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  await user.type(screen.getByLabelText("Тикер"), "HCON");
  await user.type(screen.getByLabelText("Описание компании"), "Строим города");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  await user.clear(await screen.findByLabelText("Количество акций для IPO"));
  await user.type(screen.getByLabelText("Количество акций для IPO"), "250");
  expect(screen.getByRole("img").getAttribute("aria-label")).toContain("25%");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  await user.type(screen.getByLabelText("Цена одной акции, ДБ"), "10.00");
  expect(screen.getAllByText(/2\s500,00 ДБ/).length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: "Далее" }));
  fireEvent.change(screen.getByLabelText("Дата начала IPO"), {
    target: { value: "2030-01-01T12:00" },
  });
  fireEvent.change(screen.getByLabelText("Дата окончания IPO"), {
    target: { value: "2030-01-10T12:00" },
  });
  await user.type(
    screen.getByLabelText("Условия размещения"),
    "Развитие компании, без гарантии доходности",
  );
  await user.type(
    screen.getByLabelText("Права акционеров"),
    "Право на объявленные дивиденды",
  );
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(exchangeApi.write).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Далее" }));
  await user.click(screen.getByRole("checkbox"));
  return user;
}
it("calculates an IPO plan and only creates/submits the issuer after explicit review, without opening sales", async () => {
  vi.mocked(exchangeApi.write)
    .mockResolvedValueOnce({ id: "stock", status: "draft" })
    .mockResolvedValueOnce({
      id: "stock",
      ticker: "HCON",
      name: org.name,
      status: "pending_review",
      created_at: "2026-10-24T12:00:00Z",
    });
  mount();
  const user = await fill();
  await user.click(
    screen.getByRole("button", { name: "Отправить на рассмотрение" }),
  );
  await screen.findByRole("heading", {
    name: "Заявка отправлена на рассмотрение",
  });
  expect(vi.mocked(exchangeApi.write).mock.calls[0][1]).toMatchObject({
    ticker: "HCON",
    quantity: 1000,
    organization_id: org.id,
    confirmed: true,
  });
  expect(
    String(
      (vi.mocked(exchangeApi.write).mock.calls[0][1] as { terms: string })
        .terms,
    ),
  ).toContain("250 акций по 10.00");
  expect(vi.mocked(exchangeApi.write).mock.calls.map((c) => c[0])).toEqual([
    "issuers",
    "issuers/stock/submit",
  ]);
});
it("restores a pending submission with the same key after remounting", async () => {
  vi.mocked(exchangeApi.write).mockRejectedValueOnce(
    new TypeError("lost response"),
  );
  const view = mount();
  const user = await fill();
  await user.click(
    screen.getByRole("button", { name: "Отправить на рассмотрение" }),
  );
  await screen.findByRole("alert");
  const first = vi.mocked(exchangeApi.write).mock.calls[0];
  view.unmount();
  vi.mocked(exchangeApi.write)
    .mockResolvedValueOnce({ id: "stock", status: "draft" })
    .mockResolvedValueOnce({
      id: "stock",
      ticker: "HCON",
      name: org.name,
      status: "pending_review",
      created_at: "2026-10-24T12:00:00Z",
    });
  mount();
  expect(exchangeApi.write).toHaveBeenCalledTimes(1);
  await user.click(
    await screen.findByRole("button", {
      name: "Проверить / повторить отправку",
    }),
  );
  await screen.findByRole("heading", {
    name: "Заявка отправлена на рассмотрение",
  });
  expect(vi.mocked(exchangeApi.write).mock.calls[1]).toEqual(first);
});
it("keeps a taken ticker on the company step and preserves the draft", async () => {
  vi.mocked(exchangeApi.get).mockResolvedValue({ available: false });
  const view = mount();
  const user = userEvent.setup();
  await screen.findByLabelText("Банковский счёт компании");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  await user.type(screen.getByLabelText("Тикер"), "TAKEN");
  await user.type(screen.getByLabelText("Описание компании"), "A company");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect((await screen.findByRole("alert")).textContent).toContain("занят");
  expect(exchangeApi.write).not.toHaveBeenCalled();
  view.unmount();
  mount();
  expect((screen.getByLabelText("Тикер") as HTMLInputElement).value).toBe(
    "TAKEN",
  );
});

it("replaces the previous company name when switching the issuing organization", async () => {
  const second = {
    id: "bar",
    name: "Бар Пчелка",
    accounts: [
      { ...testAccount, organization_id: "bar", account_type: "organization" },
    ],
  };
  vi.mocked(api.organizations).mockResolvedValue([org, second]);
  vi.mocked(api.organization).mockImplementation(async (id) =>
    id === second.id ? second : org,
  );
  mount();
  const user = userEvent.setup();
  await screen.findByRole("option", { name: second.name });
  await user.selectOptions(screen.getByLabelText("Организация"), second.id);
  await screen.findByText(second.name, { selector: "b" });
  await user.selectOptions(
    screen.getByLabelText("Банковский счёт компании"),
    testAccount.id,
  );
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(
    (screen.getByLabelText("Название компании") as HTMLInputElement).value,
  ).toBe(second.name);
  expect(screen.getByText(/Организация-владелец:/).textContent).toContain(
    second.name,
  );
  expect(exchangeApi.write).not.toHaveBeenCalled();
});
