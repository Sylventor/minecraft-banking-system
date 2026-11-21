# HPay game payments

HPay connects an organization-managed product catalog to Minecraft private-message payments. It uses the existing financial ledger; no client stores an independent balance.

Organization owners/administrators manage products under `/api/v1/organizations/{organization_id}/hpay/products` and review sales under `/hpay/sales`. Products have immutable numeric IDs, a display name, a receiving organization account, an exact price, and active/disabled/archived state.

## Purchase flow

```text
/msg BankingATM $pay 1234
/msg BankingATM $pay 1234
/msg BankingATM $cancel
/msg BankingATM $payhelp
```

The first request displays a short-lived snapshot of product, merchant, amount, and receiving account. Repeating the command after delivery confirms that specific proposal. A sign marked `H-1234` uses the numeric ID `1234`.

The core serializes pending purchases per buyer and uses a stable purchase-specific idempotency key. A successful retry returns the recorded result. Disabling/reactivating a product invalidates an earlier activation revision. A price/account/name change does not silently alter a proposal already shown to the user. Insufficient funds or an unavailable receiving account cannot produce a completed purchase.

The coordinator requires confirmed prompt delivery before saving the proposal for confirmation. It filters duplicate native chat packets and applies short protection intervals. After a restart it displays a proposal again rather than guessing that an earlier confirmation was intended.

## Optional Fabric client

`minecraft-mod/hpay` targets Minecraft Java **1.21.11**, Java 21, Fabric Loader 0.18.4, and the pinned Fabric API in its build file. Press the configurable payment key while looking at an unambiguous `H-<id>` sign to send a normal private-message command. A separate actual press is needed for the second command.

The client configuration specifies the master nickname, notifications, and minimum input interval. It has no API token, banking password, telemetry endpoint, or direct financial API call. See the module README for build/install instructions and limits.
