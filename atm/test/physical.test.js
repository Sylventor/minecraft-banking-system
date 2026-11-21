import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { Vec3 } from "vec3";
import { AtmClient } from "../src/client.js";

const require = createRequire(import.meta.url);
const registry = require("prismarine-registry")("1.21.4");
const windows = require("prismarine-windows")(registry);
const Item = require("prismarine-item")(registry);
const debrisId = registry.itemsByName.ancient_debris.id;

function fixture(
  kind,
  confirmationFails = false,
  windowType = "minecraft:generic_9x3",
  stock = {},
) {
  let inventory = stock.inventory ?? (kind === "deposit" ? 10 : 32),
    barrel = stock.barrel ?? (kind === "deposit" ? 5 : 0),
    clicks = 0;
  const records = [],
    actions = [];
  const coordinator = {
    guard() {},
    journal: {
      async write(id, value) {
        records.push(value);
      },
    },
    async operation(id, action) {
      actions.push(action);
      if (action === "confirm" && confirmationFails)
        throw new Error("Ответ банка потерян");
      return {
        id,
        status:
          action === "confirm"
            ? "COMPLETED"
            : action === "fail"
              ? "REQUIRES_REVIEW"
              : "PROCESSING",
      };
    },
    async heartbeat() {},
  };
  const client = new AtmClient(
    { currency_item: "ancient_debris", player_distance: 15 },
    { id: "atm-1", container: { dimension: "overworld", x: 0, y: 64, z: 0 } },
    coordinator,
  );
  // Use the installed Window API and its actual slot boundaries. Mineflayer
  // adds only close/deposit/withdraw when opening the container.
  const container = windows.createWindow(1, windowType, "ATM");
  const fill = (start, end, count) => {
    for (let slot = start; slot < end; slot++) {
      const stack = Math.min(count, 64);
      container.updateSlot(slot, stack ? new Item(debrisId, stack) : null);
      count -= stack;
    }
    assert.equal(count, 0);
  };
  const syncSlots = () => {
    fill(0, container.inventoryStart, barrel);
    fill(container.inventoryStart, container.inventoryEnd, inventory);
  };
  syncSlots();
  container.close = () => {};
  container.deposit = async (type, metadata, quantity) => {
    assert.equal(type, debrisId);
    clicks++;
    inventory -= quantity;
    barrel += quantity;
    syncSlots();
  };
  container.withdraw = async (type, metadata, quantity) => {
    assert.equal(type, debrisId);
    clicks++;
    inventory += quantity;
    barrel -= quantity;
    syncSlots();
  };
  client.ready = true;
  client.nearby = () => true;
  client.bot = {
    game: { dimension: "overworld" },
    entity: { position: new Vec3(0, 64, 1) },
    blockAt: () => ({ name: "barrel" }),
    async openContainer() {
      return container;
    },
    registry,
  };
  return {
    client,
    container,
    coordinator,
    records,
    actions,
    counts: () => ({ inventory, barrel, clicks }),
  };
}

for (const [windowType, inventory, barrel, expected] of [
  ["minecraft:generic_9x3", 2304, 0, 1728],
  ["minecraft:generic_9x6", 2304, 0, 2304],
  ["minecraft:generic_9x3", 2304, 1723, 5],
  ["minecraft:generic_9x3", 32, 0, 32],
]) {
  test(`collection delivers the maximum that fits: ${inventory} in ATM, ${barrel} in ${windowType}`, async () => {
    const f = fixture("collection", false, windowType, { inventory, barrel });
    const quantity = await f.client.collectionQuantity("PlayerName", "uuid");
    assert.equal(quantity, expected);
    assert.deepEqual(f.counts(), { inventory, barrel, clicks: 0 });
    assert.deepEqual(f.actions, []);
    assert.deepEqual(f.records, []);
    await f.client.execute({
      id: "operation",
      kind: "collection",
      quantity,
      nickname: "PlayerName",
      player_uuid: "uuid",
    });
    assert.deepEqual(f.counts(), {
      inventory: inventory - quantity,
      barrel: barrel + quantity,
      clicks: Math.ceil(quantity / 64),
    });
    assert.deepEqual(f.records.at(-1).evidence, {
      inventory_before: inventory,
      container_before: barrel,
      inventory_after: inventory - quantity,
      container_after: barrel + quantity,
    });
  });
}

test("collection includes partial stacks and excludes slots occupied by other items", async () => {
  const f = fixture("collection", false, "minecraft:generic_9x3", {
    inventory: 2304,
  });
  for (let slot = 0; slot < f.container.inventoryStart - 1; slot++)
    f.container.updateSlot(slot, new Item(registry.itemsByName.stone.id, 64));
  f.container.updateSlot(
    f.container.inventoryStart - 1,
    new Item(debrisId, 62),
  );
  assert.equal(await f.client.collectionQuantity("PlayerName", "uuid"), 2);
  assert.equal(f.counts().clicks, 0);
});

for (const [inventory, barrel, error] of [
  [2304, 1728, /нет свободного места/],
  [0, 0, /нет дебрисов для инкассации/],
]) {
  test(`collection rejects before creating physical intent: inventory=${inventory}, barrel=${barrel}`, async () => {
    const f = fixture("collection", false, "minecraft:generic_9x3", {
      inventory,
      barrel,
    });
    await assert.rejects(
      f.client.collectionQuantity("PlayerName", "uuid"),
      error,
    );
    assert.equal(f.client.busy, false);
    assert.equal(f.counts().clicks, 0);
    assert.deepEqual(f.actions, []);
    assert.deepEqual(f.records, []);
  });
}

test("collection rechecks space before physical intent if the container changes after planning", async () => {
  const f = fixture("collection", false, "minecraft:generic_9x3", {
    inventory: 2304,
  });
  const quantity = await f.client.collectionQuantity("PlayerName", "uuid");
  for (let slot = 0; slot < f.container.inventoryStart; slot++)
    f.container.updateSlot(slot, new Item(debrisId, 64));
  await assert.rejects(
    f.client.execute({
      id: "operation",
      kind: "collection",
      quantity,
      nickname: "PlayerName",
      player_uuid: "uuid",
    }),
    /Недостаточно свободного места/,
  );
  assert.equal(f.counts().clicks, 0);
  assert.deepEqual(f.actions, ["fail"]);
});

function renamedDebris(format) {
  if (format === "components")
    return Item.fromNotch({
      itemId: debrisId,
      itemCount: 5,
      components: [
        {
          type: "custom_name",
          data: { type: "string", value: "Ancient Debris" },
        },
      ],
      removeComponents: [],
    });
  const item = new Item(debrisId, 5);
  item.customName = '{"text":"Ancient Debris"}';
  return item;
}

for (const format of ["nbt", "components"]) {
  test(`renamed debris (${format}) is excluded from currency and free stack space`, () => {
    const f = fixture("deposit"),
      renamed = renamedDebris(format);
    assert.equal(f.client.count([renamed, new Item(debrisId, 3)]), 3);
    assert.equal(f.client.capacity([renamed]), 0);
  });
  for (const kind of ["deposit", "withdraw", "collection"]) {
    test(`${kind} refuses mixed regular and renamed debris (${format}) before physical intent`, async () => {
      const f = fixture(kind);
      f.container.updateSlot(
        kind === "deposit" ? 1 : f.container.inventoryStart,
        renamedDebris(format),
      );
      await assert.rejects(
        f.client.execute({
          id: "operation",
          kind,
          quantity: 5,
          nickname: "PlayerName",
          player_uuid: "uuid",
        }),
        /Переименованные древние обломки не принимаются/,
      );
      assert.equal(f.counts().clicks, 0);
      assert.deepEqual(f.actions, ["fail"]);
      assert.ok(
        !f.records.some(
          (r) => r.status === "ARMED" || r.status === "PHYSICAL_CONFIRMED",
        ),
      );
    });
  }
}

test("a renamed stack added after preflight is rejected before inventory clicks", async () => {
  const f = fixture("deposit");
  const operation = f.coordinator.operation.bind(f.coordinator);
  f.coordinator.operation = async (id, action) => {
    if (action === "start")
      f.container.updateSlot(1, renamedDebris("components"));
    return operation(id, action);
  };
  await assert.rejects(
    f.client.execute({
      id: "operation",
      kind: "deposit",
      quantity: 5,
      nickname: "PlayerName",
      player_uuid: "uuid",
    }),
    /Переименованные древние обломки не принимаются/,
  );
  assert.equal(f.counts().clicks, 0);
  assert.deepEqual(f.actions, ["start", "fail"]);
});
test("a Prismarine container credits a deposit only after durable physical evidence", async () => {
  const f = fixture("deposit");
  await f.client.execute({
    id: "operation",
    kind: "deposit",
    quantity: 5,
    nickname: "PlayerName",
    player_uuid: "uuid",
  });
  assert.deepEqual(f.counts(), { inventory: 15, barrel: 0, clicks: 1 });
  assert.deepEqual(
    f.records.map((r) => r.status),
    ["ARMED", "PROCESSING", "PHYSICAL_CONFIRMED", "COMPLETED"],
  );
});
for (const kind of ["withdraw", "collection"]) {
  for (const windowType of ["minecraft:generic_9x3", "minecraft:generic_9x6"]) {
    test(`${kind} confirms exact inventory movement using ${windowType}`, async () => {
      const f = fixture(kind, false, windowType);
      const result = await f.client.execute({
        id: "operation",
        kind,
        quantity: 5,
        nickname: "PlayerName",
        player_uuid: "uuid",
      });
      assert.equal(result.status, "COMPLETED");
      assert.deepEqual(f.counts(), { inventory: 27, barrel: 5, clicks: 1 });
      assert.deepEqual(f.records.at(-1).evidence, {
        inventory_before: 32,
        container_before: 0,
        inventory_after: 27,
        container_after: 5,
      });
    });
  }
}
test("a lost financial confirmation never automatically repeats or reverses the physical delivery", async () => {
  const f = fixture("withdraw", true);
  await assert.rejects(
    f.client.execute({
      id: "operation",
      kind: "withdraw",
      quantity: 5,
      nickname: "PlayerName",
      player_uuid: "uuid",
    }),
    /проверка администратора/,
  );
  assert.deepEqual(f.counts(), { inventory: 27, barrel: 5, clicks: 1 });
  assert.equal(f.records.at(-1).status, "REQUIRES_REVIEW");
  assert.equal(f.records.at(-1).evidence.inventory_after, 27);
});
