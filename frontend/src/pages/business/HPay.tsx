import { useEffect, useState, type FormEvent } from "react";
import { request } from "../../api";
import { Notice } from "../../components/Notice";
import { localError, localize as ui, money, useI18n } from "../../i18n";
import type { Organization } from "../../types";

type ProductInput = {
  name: string;
  description: string;
  account_id: string;
  price: string;
  status: "active" | "disabled" | "archived";
};
type Product = ProductInput & {
  id: string;
  sales_count: string;
  sales_total: string;
};
type Sales = {
  count: string;
  total: string;
  items: {
    id: string;
    product_id: string;
    product_name: string;
    price: string;
    completed_at: string;
  }[];
};

export function HPay({ organization }: { organization: Organization }) {
  const { language } = useI18n();
  const accounts = organization.accounts ?? [];
  const path = "/organizations/" + organization.id + "/hpay";
  const [items, setItems] = useState<Product[]>([]);
  const [sales, setSales] = useState<Sales>();
  const [offset, setOffset] = useState(0);
  const [saleOffset, setSaleOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState<{
    id?: string;
    input: ProductInput;
  }>();
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    Promise.all([
      request<{ items: Product[] }>(
        path + "/products?offset=" + offset,
        options,
      ),
      request<Sales>(path + "/sales?offset=" + saleOffset, options),
    ])
      .then(([products, summary]) => {
        if (!controller.signal.aborted) {
          setItems(products.items);
          setSales(summary);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(localError(error));
      });
    return () => controller.abort();
  }, [path, offset, saleOffset, revision]);

  const stateNames = {
    active: ui("Активен"),
    disabled: ui("Отключён"),
    archived: ui("В архиве"),
  };

  function edit(product: Product) {
    const { name, description, account_id, price, status } = product;
    setEditing({
      id: product.id,
      input: { name, description, account_id, price, status },
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editing || busy) return;
    setBusy(true);
    setError("");
    try {
      const [whole, fraction = ""] = editing.input.price
        .trim()
        .replace(",", ".")
        .split(".");
      const price = BigInt(whole).toString() + "." + fraction.padEnd(2, "0");
      await request(path + "/products" + (editing.id ? "/" + editing.id : ""), {
        method: editing.id ? "PUT" : "POST",
        body: JSON.stringify({ ...editing.input, price }),
      });
      if (!editing.id) setOffset(0);
      setEditing(undefined);
      setRevision((value) => value + 1);
    } catch (error) {
      setError(localError(error));
    } finally {
      setBusy(false);
    }
  }

  function update(input: Partial<ProductInput>) {
    setEditing(
      (current) =>
        current && { ...current, input: { ...current.input, ...input } },
    );
  }

  async function copy(id: string) {
    try {
      await navigator.clipboard.writeText("H-" + id);
      setCopied(id);
    } catch (error) {
      setError(localError(error));
    }
  }

  const next = ui("Далее");
  const back = ui("Назад");
  return (
    <>
      <Notice error={error} />
      <section className="panel">
        <div className="section-heading">
          <h2>{ui("Товары HPay")}</h2>
          <button
            disabled={busy || !accounts.some((a) => a.status === "active")}
            onClick={() =>
              setEditing({
                input: {
                  name: "",
                  description: "",
                  price: "",
                  status: "active",
                  account_id:
                    accounts.find((a) => a.status === "active")?.id ?? "",
                },
              })
            }
          >
            {ui("Создать товар")}
          </button>
        </div>
        {editing && (
          <form className="form-stack" onSubmit={save}>
            <label>
              {ui("Название")}
              <input
                required
                maxLength={80}
                value={editing.input.name}
                onChange={(e) => update({ name: e.target.value })}
              />
            </label>
            <label>
              {ui("Описание")}
              <textarea
                maxLength={500}
                value={editing.input.description}
                onChange={(e) => update({ description: e.target.value })}
              />
            </label>
            <label>
              {ui("Цена")}
              <input
                required
                inputMode="decimal"
                pattern="[0-9]{1,16}([.,][0-9]{1,2})?"
                value={editing.input.price}
                onChange={(e) => update({ price: e.target.value })}
              />
            </label>
            <label>
              {ui("Счёт получателя")}
              <select
                required
                value={editing.input.account_id}
                onChange={(e) => update({ account_id: e.target.value })}
              >
                {accounts
                  .filter(
                    (a) =>
                      a.status === "active" ||
                      a.id === editing.input.account_id,
                  )
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · {a.public_number}
                    </option>
                  ))}
                {!accounts.some((a) => a.id === editing.input.account_id) && (
                  <option value={editing.input.account_id}>
                    {editing.input.account_id}
                  </option>
                )}
              </select>
            </label>
            <label>
              {ui("Статус")}
              <select
                value={editing.input.status}
                onChange={(e) =>
                  update({ status: e.target.value as ProductInput["status"] })
                }
              >
                <option value="active">{stateNames.active}</option>
                <option value="disabled">{stateNames.disabled}</option>
                {editing.id && (
                  <option value="archived">{stateNames.archived}</option>
                )}
              </select>
            </label>
            <div className="button-row">
              <button disabled={busy}>{ui("Сохранить")}</button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => setEditing(undefined)}
              >
                {ui("Отмена")}
              </button>
            </div>
          </form>
        )}
        {!items.length && <p>{ui("В каталоге пока нет товаров")}</p>}
        {items.map((product) => (
          <article key={product.id} className="panel">
            <h3>
              {product.name} · H-{product.id}
            </h3>
            <p>{product.description}</p>
            <p>
              {money(product.price, language)} · {stateNames[product.status]} ·{" "}
              {accounts.find((a) => a.id === product.account_id)?.name ??
                product.account_id}
            </p>
            <p>
              {ui("Продажи")} · {product.sales_count} ·{" "}
              {money(product.sales_total, language)}
            </p>
            <div className="button-row">
              <button
                className="secondary"
                onClick={() => void copy(product.id)}
              >
                {copied === product.id
                  ? ui("Скопировано")
                  : ui("Копировать ID")}
              </button>
              {product.status !== "archived" && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => edit(product)}
                >
                  {ui("Редактировать")}
                </button>
              )}
            </div>
          </article>
        ))}
        <div className="button-row">
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(offset - 100)}
          >
            {back}
          </button>
          <button
            className="secondary"
            disabled={items.length < 100}
            onClick={() => setOffset(offset + 100)}
          >
            {next}
          </button>
        </div>
      </section>
      <section className="panel">
        <h2>{ui("История и аналитика HPay")}</h2>
        {sales && (
          <>
            <p>
              {ui("Продажи")} · {sales.count} · {money(sales.total, language)}
            </p>
            <ul>
              {sales.items.map((sale) => (
                <li key={sale.id}>
                  {new Date(sale.completed_at).toLocaleString(language)} · H-
                  {sale.product_id} · {sale.product_name} ·{" "}
                  {money(sale.price, language)}
                </li>
              ))}
            </ul>
            <div className="button-row">
              <button
                className="secondary"
                disabled={!saleOffset}
                onClick={() => setSaleOffset(saleOffset - 100)}
              >
                {back}
              </button>
              <button
                className="secondary"
                disabled={saleOffset + 100 >= Number(sales.count)}
                onClick={() => setSaleOffset(saleOffset + 100)}
              >
                {next}
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
