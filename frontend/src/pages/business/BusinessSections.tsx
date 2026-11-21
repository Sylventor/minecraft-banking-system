import { localize as ui, useI18n as useLocale } from "../../i18n";
import { Suspense, lazy, useEffect, useState } from "react";
import { businessApi } from "../../business-api";
import { Loading } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { localError } from "../../i18n";
import type { Organization } from "../../types";
const Analytics = lazy(() =>
  import("./Analytics").then((m) => ({ default: m.Analytics })),
);
const Payroll = lazy(() =>
  import("./Payroll").then((m) => ({ default: m.Payroll })),
);
const IssuerExchange = lazy(() =>
  import("../IssuerExchange").then((m) => ({ default: m.IssuerExchange })),
);
const Payments = lazy(() =>
  import("../Payments").then((m) => ({ default: m.Payments })),
);
export function BusinessSections({
  organization,
  activeTab,
}: {
  organization: Organization;
  activeTab?: string;
}) {
  useLocale();
  const [permissions, setPermissions] = useState<string[]>(),
    [tab, setTab] = useState(
      new URLSearchParams(location.hash.split("?")[1]).get("tab") || "payments",
    ),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    businessApi
      .permissions(organization.id)
      .then((v) => {
        if (active) setPermissions(v.permissions);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, [organization.id]);
  if (!permissions) return error ? <Notice error={error} /> : <Loading />;
  const canPayroll = permissions.includes("payroll.read"),
    canAnalytics = permissions.includes("analytics.read");
  const canExchange = permissions.some(
    (p) => p === "exchange.issuer.manage" || p === "exchange.dividends.manage",
  );
  const requested = activeTab || tab;
  const current =
    (requested === "payroll" && !canPayroll) ||
    (requested === "analytics" && !canAnalytics) ||
    (requested === "exchange" && !canExchange)
      ? "payments"
      : requested;
  return (
    <>
      <Notice error={error} />
      {!activeTab && (
        <div className="business-section-tabs">
          <button
            className={current === "payments" ? "" : "secondary"}
            onClick={() => setTab("payments")}
          >
            {ui("Платежи")}
          </button>
          {canAnalytics && (
            <button
              className={current === "analytics" ? "" : "secondary"}
              onClick={() => setTab("analytics")}
            >
              {ui("Аналитика")}
            </button>
          )}
          {canPayroll && (
            <button
              className={current === "payroll" ? "" : "secondary"}
              onClick={() => setTab("payroll")}
            >
              {ui("Зарплаты")}
            </button>
          )}
          {canExchange && (
            <button
              className={current === "exchange" ? "" : "secondary"}
              onClick={() => setTab("exchange")}
            >
              {ui("Акции и дивиденды")}
            </button>
          )}
        </div>
      )}
      <Suspense fallback={<Loading />}>
        {current === "exchange" && canExchange && (
          <IssuerExchange
            organization={organization}
            permissions={permissions}
          />
        )}
        {current === "payments" && (
          <Payments organizationId={organization.id} />
        )}{" "}
        {current === "analytics" && (
          <Analytics organization={organization} permissions={permissions} />
        )}{" "}
        {current === "payroll" && (
          <Payroll organization={organization} permissions={permissions} />
        )}
      </Suspense>
    </>
  );
}
