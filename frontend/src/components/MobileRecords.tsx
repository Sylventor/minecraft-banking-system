import type { ReactNode } from "react";

export type MobileRecord = {
  id: string;
  title: ReactNode;
  amount?: ReactNode;
  subtitle?: ReactNode;
  fields: [string, ReactNode][];
  action?: ReactNode;
};
export function MobileRecords({ items }: { items: MobileRecord[] }) {
  return (
    <div className="mobile-records">
      {items.map((item) => (
        <article className="record-card" key={item.id}>
          <header>
            <b>{item.title}</b>
            {item.amount && <strong>{item.amount}</strong>}
          </header>
          {item.subtitle && <small>{item.subtitle}</small>}
          <dl>
            {item.fields.map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          {item.action}
        </article>
      ))}
    </div>
  );
}
