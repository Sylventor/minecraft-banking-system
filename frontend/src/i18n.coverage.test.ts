import { describe, expect, it } from "vitest";
import ts from "typescript";
import catalog from "./locales/catalog.json";
import { ApiFailure } from "./api";
import {
  currencyLabel,
  dateTime,
  languageLocale,
  localError,
  money,
  normalizeLanguage,
  translate,
} from "./i18n";

const translations: Record<string, { en: string; uk: string }> = catalog;
const sources = import.meta.glob(
  [
    "./**/*.tsx",
    "./**/*.ts",
    "!./**/*.test.*",
    "!./test-support.tsx",
    "!./i18n.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
);

describe("three-language translation coverage", () => {
  it("contains English and Ukrainian for every source label and existing translation key", () => {
    const missing: string[] = [];
    for (const [file, raw] of Object.entries(sources)) {
      const sf = ts.createSourceFile(
        file,
        String(raw),
        ts.ScriptTarget.Latest,
        true,
        file.endsWith("tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
      );
      function check(node: ts.Node) {
        if (
          ts.isStringLiteral(node) ||
          ts.isNoSubstitutionTemplateLiteral(node)
        ) {
          const key = node.text.replace(/\s+/g, " ").trim();
          if (
            /[а-яё]/i.test(key) &&
            !["Русский", "некорректный-адрес"].includes(key)
          ) {
            if (!translations[key]?.uk || !translations[key]?.en)
              missing.push(file + ": " + key);
          }
        }
        if (
          ts.isJsxText(node) &&
          /[а-яё]/i.test(node.text) &&
          !["Русский", "Українська"].includes(node.text.trim())
        )
          missing.push(file + ": untranslated JSX text");
        ts.forEachChild(node, check);
      }
      check(sf);
    }
    expect(missing).toEqual([]);
    expect(
      Object.values(translations).filter((entry) => /[ыэъё]/i.test(entry.uk)),
    ).toEqual([]);
  });
  it("keeps existing translations and defaults while adding uk-UA", () => {
    expect(normalizeLanguage(null)).toBe("ru");
    expect(normalizeLanguage("unknown")).toBe("ru");
    expect(normalizeLanguage("uk")).toBe("uk-UA");
    expect(translate("ru", "Главная", "Home")).toBe("Главная");
    expect(translate("en", "Главная", "Home")).toBe("Home");
    expect(translate("uk-UA", "Главная", "Home")).toBe("Головна");
    expect(translate("uk-UA", "Ожидают оплаты", "Awaiting payment")).toBe(
      "Очікують на оплату",
    );
  });
  it("formats Ukrainian dates and exact amounts without floating point", () => {
    expect(languageLocale("uk-UA")).toBe("uk-UA");
    expect(currencyLabel("uk-UA")).toBe("dB");
    expect(money("9999999999999999.99", "uk-UA").replace(/\s/g, "")).toBe(
      "9999999999999999,99dB",
    );
    expect(dateTime("2030-03-12T12:00:00Z", "uk-UA", false)).toContain("бер");
  });
  it("preserves specific API errors already translated into Ukrainian", () => {
    const error = new ApiFailure({
      error: "Новину не знайдено",
      code: "http_error",
      request_id: "test",
    });
    expect(localError(error, "uk-UA")).toBe("Новину не знайдено");
  });
});
