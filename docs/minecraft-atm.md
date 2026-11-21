# Minecraft ATM integration

The optional `atm` service is a Node.js/Mineflayer coordinator with independently connected Minecraft clients. One configured master receives private commands. Each device has its own inventory, bound container, connection, and physical-operation state.

## Private configuration

Copy `atm/config.example.json` to an ignored `atm/config.json`. Configure the test Minecraft host/port/version, bot identities, authentication mode, exact container coordinates/dimension, and server-specific private-message patterns. Create an ignored `atm/bot-secrets.env` only if game login passwords are needed.

Generate a distinct `ATM_SERVICE_TOKEN` in the private environment. Use `online-mode` with Microsoft authentication, or explicitly configure trusted `server-auth` only when the server securely authenticates player identities before commands and inventory access. A nickname alone is not proof of identity.

```bash
docker compose --profile atm up -d --build atm
docker compose logs --tail=100 atm
```

The private configuration is read at startup. Restart `atm` after changing it. Changing a Compose environment/mount setting requires recreating the container. Keep configuration files readable by the container's unprivileged user without making passwords world-readable.

## Linking and commands

Start account linking in the dashboard with the player's nickname. The master delivers a short-lived code in Minecraft; the user confirms that code on the website. Nickname and UUID must both match a verified active profile.

Examples below assume the master is named `BankingATM`; the real help output uses its configured nickname.

```text
/msg BankingATM $help
/msg BankingATM $bal
/msg BankingATM $with 16
/msg BankingATM $dep 32
/msg BankingATM $transaction DemoPlayer 12.50
```

The help contains full `/msg` commands in square brackets and only the website is clickable. Collection appears only with the effective `admin.atm.collect` permission:

```text
/msg BankingATM $clear-atm
```

Collection delivers the maximum amount fitting in the bound container, up to the device's inventory, using bounded batches. Renamed ancient debris is not accepted as currency. Ordinary transfers retain decimal precision; physical deposits and withdrawals use whole items.

## Formatting and delivery

Outgoing messages start with `<br>`. Currency can use `<sprite:blocks:block/ancient_debris_side>`. FlectonePulse must permit the selected formatting tags. Command suggestions use `suggest_command`, leaving final Enter confirmation to the user. A long prompt falls back to a full command in brackets.

The incoming and delivery patterns must match the test server's actual private-message format. Delivery matching binds the full visible text and recipient and rejects ordinary public-chat impersonation. Do not treat an unverified echo as proof that a player received a linking/payment prompt.

## Administrative preview

```text
/msg BankingATM $test
/msg BankingATM $test stop
```

Preview access requires a verified profile and one of `admin.atm.read`, `admin.atm.manage`, or `admin.atm.collect`. It shows the shared message templates with fictional values, at least 1.5 seconds apart in addition to the normal send queue. Only one preview runs at a time, followed by a 30-second cooldown. Its command buttons open help; it never creates payments or moves items.

Unknown physical outcomes require review. Never manually clear the coordinator state to force another delivery. See [Operations](operations.md) and [HPay](hpay.md).
