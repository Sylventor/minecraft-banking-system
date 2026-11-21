// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../i18n";
import { Authentication } from "./Authentication";
import { AuthenticationMethods } from "./AuthenticationMethods";
import { TestBank } from "../test-support";
import { api, request } from "../api";

vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return {
    ...actual,
    request: vi.fn(),
    api: { ...actual.api, discordLogin: vi.fn() },
  };
});
beforeEach(() => {
  history.replaceState({}, "", "/");
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function mount() {
  const done = vi.fn().mockResolvedValue(undefined);
  render(
    <I18nProvider>
      <Authentication error="" onAuthenticated={done} />
    </I18nProvider>,
  );
  return done;
}

it("requires age attestation for both password and Discord registration", async () => {
  history.replaceState({}, "", "/register");
  mount();
  const signup = screen.getByRole("button", { name: "Зарегистрироваться" });
  const discord = screen.getByRole("button", {
    name: "Продолжить через Discord",
  });
  expect((signup as HTMLButtonElement).disabled).toBe(true);
  expect((discord as HTMLButtonElement).disabled).toBe(true);
  await userEvent.click(
    screen.getByLabelText("Подтверждаю, что мне исполнилось 15 лет"),
  );
  expect((discord as HTMLButtonElement).disabled).toBe(false);
  await userEvent.click(discord);
  expect(api.discordLogin).toHaveBeenCalledWith(true);
});

it("rejects overlong passwords explicitly without truncating the entered value", async () => {
  mount();
  fireEvent.change(screen.getByLabelText("Логин"), {
    target: { value: "Example" },
  });
  const input = screen.getByLabelText("Пароль") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "x".repeat(1025) } });
  expect(input.value.length).toBe(1025);
  await userEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(screen.getByText(/Он не обрезается/)).toBeTruthy();
  expect(request).not.toHaveBeenCalled();
});

it("offers ordinary sign-in first, password managers and Discord as an alternative", async () => {
  const done = mount(),
    user = userEvent.setup();
  vi.mocked(request).mockResolvedValue({ id: "test-user" });
  expect(
    screen.getByRole("heading", { name: "Войти в Banking System" }),
  ).toBeTruthy();
  const password = screen.getByLabelText("Пароль");
  expect(password.getAttribute("autocomplete")).toBe("current-password");
  await user.type(screen.getByLabelText("Логин"), "Example");
  await user.type(password, "test twelve character phrase");
  await user.click(
    screen.getByRole("button", { name: "Показать пароль: Пароль" }),
  );
  expect(password.getAttribute("type")).toBe("text");
  await user.click(screen.getByRole("button", { name: "Войти" }));
  expect(request).toHaveBeenCalledWith(
    "/auth/login",
    expect.objectContaining({ method: "POST" }),
  );
  expect(done).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("button", { name: "Продолжить через Discord" }),
  ).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Minecraft-сервер/ })).toBeNull();
  expect(screen.getByText("Minecraft-сервер")).toBeTruthy();
});

it("shows the complete virtual-bank disclosure and blocks mismatched registration", async () => {
  history.replaceState({}, "", "/register");
  mount();
  const user = userEvent.setup();
  expect(screen.getByText("Banking System — виртуальный банк")).toBeTruthy();
  expect(screen.getByText(/не является настоящим банком/)).toBeTruthy();
  await user.click(
    screen.getByLabelText("Подтверждаю, что мне исполнилось 15 лет"),
  );
  await user.type(screen.getByLabelText("Логин"), "Example");
  await user.type(
    screen.getByLabelText("Пароль"),
    "test twelve character phrase",
  );
  await user.type(
    screen.getByLabelText("Повтор пароля"),
    "test another password phrase",
  );
  await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
  expect(screen.getByText("Пароли не совпадают")).toBeTruthy();
  expect(request).not.toHaveBeenCalled();
});

it("sends the whole passphrase and prevents duplicate registration while awaiting a reply", async () => {
  history.replaceState({}, "", "/register");
  const done = mount(),
    user = userEvent.setup();
  let finish!: (value: unknown) => void;
  vi.mocked(request).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const phrase = "test long passphrase with spaces 你好";
  await user.type(screen.getByLabelText("Логин"), "Example");
  await user.type(screen.getByLabelText("Пароль"), phrase);
  await user.type(screen.getByLabelText("Повтор пароля"), phrase);
  await user.click(
    screen.getByLabelText("Подтверждаю, что мне исполнилось 15 лет"),
  );
  await user.dblClick(
    screen.getByRole("button", { name: "Зарегистрироваться" }),
  );
  expect(request).toHaveBeenCalledTimes(1);
  const body = JSON.parse(vi.mocked(request).mock.calls[0][1]!.body as string);
  expect(body.password).toBe(phrase);
  expect(body.password_confirmation).toBe(phrase);
  expect(body.age_confirmed).toBe(true);
  finish({ id: "test-user" });
  await screen.findByRole("button", { name: "Войти" });
  expect(done).toHaveBeenCalledTimes(1);
});

it("keeps Discord as a last sign-in method and offers password setup without re-registering", async () => {
  vi.mocked(request).mockResolvedValue({
    login: null,
    password_enabled: false,
    discord: { username: "Existing" },
    recent: true,
    auth_method: "discord",
  });
  render(
    <TestBank>
      <AuthenticationMethods />
    </TestBank>,
  );
  const user = userEvent.setup();
  const unlink = await screen.findByRole("button", {
    name: "Отвязать Discord",
  });
  expect((unlink as HTMLButtonElement).disabled).toBe(true);
  await user.click(
    screen.getByRole("button", { name: "Добавить вход по паролю" }),
  );
  expect(
    screen.getByLabelText("Новый пароль").getAttribute("autocomplete"),
  ).toBe("new-password");
  expect(screen.getByText(/единственный способ входа/)).toBeTruthy();
  expect(api.discordLogin).not.toHaveBeenCalled();
});
