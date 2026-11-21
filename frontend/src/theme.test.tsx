// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeChoice, ThemeProvider } from "./theme";

let dark = false;
let changed: (() => void) | undefined;
beforeEach(() => {
  localStorage.clear();
  dark = false;
  changed = undefined;
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return dark;
    },
    addEventListener: (_name: string, listener: () => void) => {
      changed = listener;
    },
    removeEventListener: () => {
      changed = undefined;
    },
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
function mount() {
  return render(
    <ThemeProvider>
      <ThemeChoice />
    </ThemeProvider>,
  );
}
it("keeps an explicit theme across remounts and follows system changes only in automatic mode", async () => {
  const view = mount();
  const user = userEvent.setup();
  expect(document.documentElement.dataset.theme).toBe("light");
  await user.selectOptions(screen.getByLabelText("Тема оформления"), "dark");
  expect(localStorage.getItem("horizon.theme")).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  view.unmount();
  mount();
  expect(
    (screen.getByLabelText("Тема оформления") as HTMLSelectElement).value,
  ).toBe("dark");
  dark = false;
  changed?.();
  expect(document.documentElement.dataset.theme).toBe("dark");
  await user.selectOptions(screen.getByLabelText("Тема оформления"), "system");
  expect(document.documentElement.dataset.theme).toBe("light");
  dark = true;
  changed?.();
  expect(document.documentElement.dataset.theme).toBe("dark");
});
it("still changes the current appearance when browser storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  mount();
  await userEvent.selectOptions(
    screen.getByLabelText("Тема оформления"),
    "dark",
  );
  expect(document.documentElement.dataset.theme).toBe("dark");
});
