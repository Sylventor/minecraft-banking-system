// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

afterEach(cleanup);
it("renders tables, headings and safe links without executing HTML or dangerous URLs", () => {
  const { container } = render(
    <Markdown
      content={
        "# Правила\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n[банк](https://example.com)\n\n[опасно](javascript:alert(1))\n\n<script>alert(1)</script>\n\n![tracking](https://example.com/pixel.png)"
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "Правила" })).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
  expect(screen.getByRole("link", { name: "банк" }).getAttribute("href")).toBe(
    "https://example.com",
  );
  expect(container.querySelector("script, iframe, img")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
});
