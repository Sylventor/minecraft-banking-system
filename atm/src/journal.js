import {
  mkdir,
  open,
  rename,
  readdir,
  readFile,
  unlink,
} from "node:fs/promises";
import { join } from "node:path";

export class Journal {
  constructor(path) {
    this.path = path;
    this.queue = Promise.resolve();
  }
  serial(action) {
    const work = this.queue.then(action);
    this.queue = work.catch(() => {});
    return work;
  }
  async init() {
    await mkdir(this.path, { recursive: true, mode: 0o700 });
  }
  async write(id, value) {
    return this.serial(async () => {
      if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error("Invalid journal id");
      const file = join(this.path, id + ".json"),
        temp = file + ".tmp";
      const fd = await open(temp, "w", 0o600);
      try {
        await fd.writeFile(
          JSON.stringify({
            ...value,
            id,
            written_at: new Date().toISOString(),
          }),
        );
        await fd.sync();
      } finally {
        await fd.close();
      }
      await rename(temp, file);
      // Persist directory entries as well as contents on Linux (the Docker runtime).
      if (process.platform !== "win32") {
        const dir = await open(this.path, "r");
        try {
          await dir.sync();
        } finally {
          await dir.close();
        }
      }
    });
  }
  async pending() {
    const result = [];
    for (const file of await readdir(this.path)) {
      if (!file.endsWith(".json")) continue;
      const value = JSON.parse(await readFile(join(this.path, file), "utf8"));
      if (!["COMPLETED", "FAILED"].includes(value.status)) result.push(value);
    }
    return result;
  }
  async minimize({ user_id, nicknames = [], now = Date.now() } = {}) {
    return this.serial(async () => {
      let removed = 0,
        blocked = false;
      const aliases = new Set(nicknames.map((name) => name.toLowerCase()));
      for (const file of await readdir(this.path)) {
        if (!/^[0-9a-f-]{36}\.json$/.test(file)) continue;
        const path = join(this.path, file);
        const value = JSON.parse(await readFile(path, "utf8"));
        const terminal = ["COMPLETED", "FAILED"].includes(value.status);
        const requested = value.status === "REQUESTED";
        const belongs =
          user_id &&
          (value.user_id === user_id ||
            aliases.has(String(value.nickname || "").toLowerCase()));
        const age = now - Date.parse(value.written_at);
        if (belongs && !terminal && !requested) {
          blocked = true;
          continue;
        }
        if (
          (belongs || age > (requested ? 30 : 365) * 86400000) &&
          (terminal || requested)
        ) {
          await unlink(path);
          removed++;
        }
      }
      if (removed && process.platform !== "win32") {
        const directory = await open(this.path, "r");
        try {
          await directory.sync();
        } finally {
          await directory.close();
        }
      }
      return { removed, blocked };
    });
  }
}
