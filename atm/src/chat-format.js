export const currencyIcon = "<sprite:blocks:block/ancient_debris_side>";
function websiteUrl() {
  const url = new URL(process.env.PUBLIC_ORIGIN || "http://localhost:8080");
  if (!/^https?:$/.test(url.protocol) || url.username || url.password)
    throw new Error("PUBLIC_ORIGIN must be an HTTP(S) origin");
  return url.origin;
}
export const bankLink = `<click:open_url:'${websiteUrl()}'>[Открыть сайт]</click>`;

export function commandText(nickname, command) {
  if (!/^[A-Za-z0-9_]{3,16}$/.test(nickname))
    throw new Error("Некорректный ник мастер-бота");
  return `[/msg ${nickname} ${command}]`;
}

export function chatCommand(nickname, command, label) {
  if (
    !/^[A-Za-z0-9_]{3,16}$/.test(nickname) ||
    !/^\$[a-z-]+(?: [A-Za-z0-9_.]+)*$/.test(command)
  )
    throw new Error("Некорректная подсказка команды");
  return `<click:suggest_command:'/msg ${nickname} ${command}'>[${chatText(label, 80)}]</click>`;
}

export function chatCard(title, lines, color = "gold") {
  return [`<${color}>${title}</${color}>`, ...lines].join("<br>");
}

export function chatText(value, limit) {
  return String(value)
    .replace(/<[^<>]*>/g, " ")
    .replace(/#[0-9a-f]{6}/gi, " ")
    .replace(/[<>\\&§\p{C}]/gu, " ")
    .slice(0, limit);
}

export function chatError(value, limit) {
  const parts = chatText(value, limit).split(
    /(?<![\p{L}\p{N}_])(?:дб|db|дебрис(?:ами|ах|ам|ов|ом|ы|а|е|у)?)(?![\p{L}\p{N}_])/giu,
  );
  let text = parts.shift();
  for (const part of parts) {
    const remaining = limit - text.length - currencyIcon.length;
    if (remaining < 0) break;
    text += currencyIcon + part.slice(0, remaining);
  }
  return text;
}

export function receiptText(text) {
  // Rendered click events are metadata, and sprites have no plain text.
  return text
    .replace(/\r\n|[\r\n]/g, "<br>")
    .replace(/<\/?(?:gold|red|green|aqua)>/gi, "")
    .replace(
      /<click:(?:suggest_command|open_url):'[^'<>\r\n]*'>|<\/click>/g,
      "",
    )
    .replaceAll(currencyIcon, "");
}
