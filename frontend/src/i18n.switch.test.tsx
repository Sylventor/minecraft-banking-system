// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  I18nProvider,
  localize,
  localizeSystem,
  localizeTemplate,
  notificationBody,
  useI18n,
} from "./i18n";
import { supportCategories } from "./support-api";

afterEach(() => {
  cleanup();
  localStorage.clear();
});
function Example() {
  const { language, setLanguage, t } = useI18n();
  return (
    <>
      <select
        aria-label="Language"
        value={language}
        onChange={(e) => setLanguage(e.target.value as typeof language)}
      >
        <option value="ru">Русский</option>
        <option value="en">English</option>
        <option value="uk-UA">Українська</option>
      </select>
      <h1>{t("Главная", "Home")}</h1>
      <p>{localize("Поддержка")}</p>
      <p>{supportCategories().atm}</p>
      <p>{localizeTemplate("Рады видеть вас, {0}", "Бар Пчелка")}</p>
    </>
  );
}
it("switches all three languages live and restores the existing preference", async () => {
  localStorage.setItem("horizon.language", "uk-UA");
  const view = render(
    <I18nProvider>
      <Example />
    </I18nProvider>,
  );
  expect(screen.getByRole("heading").textContent).toBe("Головна");
  expect(screen.getByText("Банкомати Minecraft")).toBeTruthy();
  expect(screen.getByText("Раді бачити вас, Бар Пчелка")).toBeTruthy();
  expect(document.documentElement.lang).toBe("uk-UA");
  const user = userEvent.setup();
  await user.selectOptions(screen.getByRole("combobox"), "en");
  expect(screen.getByRole("heading").textContent).toBe("Home");
  expect(screen.getByText("Support")).toBeTruthy();
  await user.selectOptions(screen.getByRole("combobox"), "ru");
  expect(screen.getByRole("heading").textContent).toBe("Главная");
  expect(localStorage.getItem("horizon.language")).toBe("ru");
  view.unmount();
});
it("translates system events while preserving names and unknown user content", () => {
  localStorage.setItem("horizon.language", "uk-UA");
  render(
    <I18nProvider>
      <Example />
    </I18nProvider>,
  );
  expect(localizeSystem("Статус: Открыто → В работе")).toBe(
    "Статус: Відкрито → У роботі",
  );
  expect(localizeSystem("Поддержка · обращение №42")).toBe(
    "Підтримка · звернення №42",
  );
  expect(localize("Бар Пчелка")).toBe("Бар Пчелка");
  expect(
    notificationBody(
      "1250.50 ДБ · Бар Пчелка",
      "transfer",
      "Перевод выполнен",
    ).replace(/\s/g, ""),
  ).toBe("1250,50dB·БарПчелка");
  expect(
    notificationBody("Главная", "support", "Вам назначено обращение №42"),
  ).toBe("Главная");
});
