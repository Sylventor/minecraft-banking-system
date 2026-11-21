// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { businessApi, type PayrollBatch } from "../../business-api";
import { TestBank, testAccount, testProfile } from "../../test-support";
import { Analytics, cents, plotRatio } from "./Analytics";
import { Payroll } from "./Payroll";
import type { Organization } from "../../types";

vi.mock("../../business-api", () => ({
  businessApi: {
    get: vi.fn(),
    write: vi.fn(),
    analytics: vi.fn(),
    export: vi.fn(),
  },
}));
const org: Organization = {
  id: "org",
  name: "Test employer",
  accounts: [
    { ...testAccount, account_type: "organization", organization_id: "org" },
  ],
};
const item: PayrollBatch = {
  id: "batch",
  source_account_id: testAccount.id,
  description: "October payroll",
  total: "100.00",
  fee: "0.00",
  status: "pending_approval",
  created_by: "another-user",
  approved_by: testProfile.id,
  executor_id: null,
  created_at: "2026-10-22T10:00:00Z",
  completed_at: null,
  items: [
    {
      id: "item",
      employee_id: "employee",
      user_id: "employee-user",
      user_name: "Employee",
      destination_account_id: "destination",
      amount: "100.00",
      status: "pending",
      operation_id: null,
      error_code: null,
      paid_at: null,
    },
  ],
};
beforeEach(() => {
  localStorage.clear();
  vi.mocked(businessApi.get).mockImplementation(
    async <T,>(_org: string, path: string) =>
      (path === "overview"
        ? {
            employees: 1,
            paid_last30: "0.00",
            next_schedule: null,
            last_batch: null,
          }
        : {
            items: path.startsWith("batches") ? [item] : [],
            next_cursor: null,
          }) as T,
  );
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("keeps monetary calculations exact above Number.MAX_SAFE_INTEGER", () => {
  expect(cents("9999999999999999.99") + cents("0.01")).toBe(
    1000000000000000000n,
  );
  expect(cents("-0.01")).toBe(-1n);
  expect(plotRatio("9999999999999999.99", 999999999999999999n)).toBe(100);
});

it("shows only read controls to a payroll reader", async () => {
  render(
    <TestBank>
      <Payroll organization={org} permissions={["payroll.read"]} />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Ведомости" }));
  await user.click(
    await screen.findByRole("button", { name: /October payroll/ }),
  );
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).queryByRole("button", { name: "Выплатить" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Создать ведомость" }),
  ).toBeNull();
  expect(businessApi.write).not.toHaveBeenCalled();
});

it("requires explicit confirmation and does not claim success on a lost execution response", async () => {
  vi.mocked(businessApi.write).mockRejectedValue(
    new TypeError("connection lost"),
  );
  render(
    <TestBank>
      <Payroll
        organization={org}
        permissions={["payroll.read", "payroll.execute"]}
      />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Ведомости" }));
  await user.click(
    await screen.findByRole("button", { name: /October payroll/ }),
  );
  await user.click(screen.getByRole("button", { name: "Выплатить" }));
  expect(businessApi.write).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText(/destination/)).toBeTruthy();
  await user.click(within(dialog).getByRole("button", { name: "Подтвердить" }));
  await screen.findAllByRole("alert");
  expect(businessApi.write).toHaveBeenCalledWith(
    "org",
    "batches/batch/execute",
    { confirmed: true },
  );
  expect(screen.queryByText("Выполнено")).toBeNull();
});

it("renders a real zero period and keeps export hidden without permission", async () => {
  const current = {
    incoming: "0.00",
    outgoing: "0.00",
    net_cashflow: "0.00",
    incoming_count: 0,
    outgoing_count: 0,
    average_incoming: "0.00",
    average_outgoing: "0.00",
    largest_incoming: "0.00",
    largest_outgoing: "0.00",
  };
  vi.mocked(businessApi.analytics).mockImplementation(
    async <T,>(_org: string, kind: string) =>
      (kind === "summary"
        ? {
            current,
            previous: current,
            changes: {},
            balance: "0.00",
            held: "0.00",
            account_count: 1,
          }
        : { items: [], next_cursor: null }) as T,
  );
  render(
    <TestBank>
      <Analytics organization={org} permissions={["analytics.read"]} />
    </TestBank>,
  );
  await screen.findByText("Текущий баланс");
  expect(screen.getByText("За период нет операций.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Экспорт CSV" })).toBeNull();
});

it("walks through payroll recipients, amounts, source and review before creating a draft", async () => {
  vi.mocked(businessApi.get).mockImplementation(
    async <T,>(_org: string, path: string) =>
      (path === "overview"
        ? {
            employees: 1,
            paid_last30: "0.00",
            next_schedule: null,
            last_batch: null,
          }
        : {
            items: path.startsWith("employees")
              ? [
                  {
                    id: "employee",
                    user_id: "someone-else",
                    user_name: "Employee",
                    account_id: "destination",
                    salary: "100.00",
                    frequency: "monthly",
                    position: "Builder",
                    active: true,
                    revision: 1,
                    note: "",
                  },
                ]
              : [],
            next_cursor: null,
          }) as T,
  );
  vi.mocked(businessApi.write).mockResolvedValue({ ...item, status: "draft" });
  render(
    <TestBank>
      <Payroll
        organization={org}
        permissions={[
          "payroll.read",
          "payroll.batches.create",
          "payroll.salaries.manage",
        ]}
      />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Ведомости" }));
  await user.click(
    await screen.findByRole("button", { name: "Создать ведомость" }),
  );
  let dialog = within(await screen.findByRole("dialog"));
  await user.click(dialog.getByRole("checkbox", { name: /Employee/ }));
  await user.click(dialog.getByRole("button", { name: "Продолжить" }));
  await user.clear(dialog.getByLabelText("Выплата Employee"));
  await user.type(dialog.getByLabelText("Выплата Employee"), "125,50");
  await user.click(dialog.getByRole("button", { name: "Продолжить" }));
  expect(dialog.getByLabelText("Счёт организации")).toBeTruthy();
  await user.click(dialog.getByRole("button", { name: "Продолжить" }));
  expect(dialog.getAllByText(/125,50/).length).toBeGreaterThan(0);
  await user.click(dialog.getByRole("button", { name: "Продолжить" }));
  expect(businessApi.write).not.toHaveBeenCalled();
  expect(
    (
      dialog.getByRole("button", {
        name: "Создать черновик",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(dialog.getByRole("checkbox", { name: /Я проверил/ }));
  await user.click(dialog.getByRole("button", { name: "Создать черновик" }));
  await screen.findByRole("dialog", { name: "Зарплатная ведомость" });
  dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText(/Черновик/)).toBeTruthy();
  expect(vi.mocked(businessApi.write).mock.calls[0].slice(0, 4)).toEqual([
    "org",
    "batches",
    {
      source_account_id: testAccount.id,
      description: "Зарплата",
      items: [{ employee_id: "employee", amount: "125.50" }],
    },
    "POST",
  ]);
});
