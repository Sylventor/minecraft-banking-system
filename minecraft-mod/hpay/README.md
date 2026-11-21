# HPay Fabric client

An optional Minecraft Banking System client shortcut for **Minecraft Java 1.21.11**. It reads an `H-<id>` sign and sends a normal Minecraft private-message payment command to a configured ATM master.

The default payment key is J and can be changed in Minecraft controls. A press shows a product proposal. A second actual press confirms through the existing server-side payment flow. Holding the key does not intentionally send repeated confirmations. Ambiguous signs, unfocused windows, and invalid configuration prevent sending.

## Install and configure

Use Java 21, Fabric Loader 0.18.4 or compatible, and the Fabric API version required by the pinned build. Install the built mod jar in the client `mods` directory. Configure `config/hpay.json` with `atmNickname` matching the ATM master, `notifications`, and a bounded `minimumIntervalMs`.

The client contains no bank token, password, direct API request, or independent financial logic. It does not replace the server's identity verification and confirmed proposal/ledger checks.

## Build

Install Gradle 9.2.1 and Java 21, then run:

```bash
cd minecraft-mod/hpay
gradle --no-daemon build
```

The output is `build/libs/hpay-1.0.1.jar`. The dedicated GitHub workflow builds this module when enabled in a future owner-approved repository. No workflow result is implied by the presence of the file.
