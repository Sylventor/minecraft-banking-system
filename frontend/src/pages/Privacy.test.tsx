// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../i18n";
import { Privacy } from "./Privacy";
import { Markdown } from "../components/Markdown";

beforeEach(() => {
  localStorage.clear();
  history.replaceState({}, "", "/privacy");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Privacy must not fetch API data");
    }),
  );
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});
function page() {
  return render(
    <I18nProvider>
      <Privacy />
    </I18nProvider>,
  );
}

it("renders the public document without authentication or API requests with working section targets", () => {
  const view = page();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Политика конфиденциальности",
  );
  const links = within(
    screen.getByRole("navigation", { name: "Содержание документа" }),
  ).getAllByRole("link");
  expect(links).toHaveLength(12);
  for (const link of links) {
    const heading = document.getElementById(
      link.getAttribute("href")!.slice(1),
    );
    expect(heading?.tagName).toBe("H2");
    expect(heading?.textContent).toBe(link.textContent);
  }
  expect(view.container.querySelector("time")?.dateTime).toMatch(
    /^\d{4}-\d{2}-\d{2}$/,
  );
  expect(view.container.querySelectorAll("table")).toHaveLength(2);
  expect(fetch).not.toHaveBeenCalled();
});

it("restores Ukrainian immediately and switches all three documents and outlines in place", async () => {
  localStorage.setItem("horizon.language", "uk-UA");
  page();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Політика конфіденційності",
  );
  expect(
    screen.getByRole("heading", { name: "2. Які дані обробляються" }),
  ).toBeTruthy();
  const selector = screen.getByRole("combobox", { name: "Мова інтерфейсу" });
  const user = userEvent.setup();
  await user.selectOptions(selector, "en");
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Privacy Policy",
  );
  expect(
    screen.queryByRole("heading", { name: "2. Які дані обробляються" }),
  ).toBeNull();
  expect(screen.getByRole("link", { name: "2. Data processed" })).toBeTruthy();
  await user.selectOptions(selector, "ru");
  expect(
    screen.getByRole("heading", { name: "2. Какие данные обрабатываются" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "2. Какие данные обрабатываются" }),
  ).toBeTruthy();
  expect(localStorage.getItem("horizon.language")).toBe("ru");
  expect(location.pathname).toBe("/privacy");
  expect(fetch).not.toHaveBeenCalled();
});

it("assigns unique anchors to repeated Markdown sections and does not treat code blocks as headings", () => {
  const { container } = render(
    <Markdown
      headingIdPrefix="section"
      content={
        "## Same\n\n## Same\n\n```md\n## Not a section\n```\n\n### Subsection"
      }
    />,
  );
  const headings = Array.from(container.querySelectorAll("h2"));
  expect(headings).toHaveLength(2);
  expect(new Set(headings.map((heading) => heading.id)).size).toBe(2);
  expect(container.querySelectorAll("h3")).toHaveLength(1);
});
