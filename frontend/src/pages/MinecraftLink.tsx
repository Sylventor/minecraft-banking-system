import { localize as ui, useI18n as useLocale } from "../i18n";
import { useEffect, useState, type FormEvent } from "react";
import { api, request } from "../api";
import { useBank } from "../bank-context";
import { Icon, Loading, Modal } from "../components/BankUI";
import { Notice } from "../components/Notice";
import { dateTime, localError, localizeSystem } from "../i18n";
type LinkStatus = {
  integration_enabled: boolean;
  link: {
    nickname: string;
    player_uuid: string;
    verified_at: string;
    authentication: string;
  } | null;
  pending: {
    id: string;
    nickname: string;
    status: string;
    expires_at: string;
    attempts: number;
  } | null;
};
export function MinecraftLink() {
  useLocale();
  const { refresh } = useBank();
  const [data, setData] = useState<LinkStatus>();
  const [nickname, setNickname] = useState(""),
    [code, setCode] = useState("");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [unlinking, setUnlinking] = useState(false);
  async function load() {
    setData(await request<LinkStatus>("/minecraft/link/status"));
  }
  useEffect(() => {
    let active = true;
    request<LinkStatus>("/minecraft/link/status")
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, []);
  async function action(run: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await run();
    } catch (e) {
      setError(localError(e));
      // Refresh cancelled/expired/locked challenges without hiding the original
      // delivery or verification error.
      const current = await request<LinkStatus>("/minecraft/link/status").catch(
        () => null,
      );
      if (current) setData(current);
    } finally {
      setBusy(false);
    }
  }
  async function start(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      await request("/minecraft/link/start", {
        method: "POST",
        body: JSON.stringify({ nickname }),
      });
      await load();
      setCode("");
      setNotice(
        ui(
          "Код отправлен личным сообщением в Minecraft. Введите его ниже в течение 5 минут.",
        ),
      );
    });
  }
  async function verify(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      await request("/minecraft/link/verify", {
        method: "POST",
        body: JSON.stringify({ challenge_id: data?.pending?.id, code }),
      });
      setCode("");
      await load();
      await refresh();
      setNotice(ui("Minecraft-аккаунт успешно привязан."));
    });
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>{ui("Привязка Minecraft")}</h3>
        <Icon name="organization" />
      </div>
      <Notice error={error} />
      {notice && (
        <p className="success-banner" role="status">
          {localizeSystem(notice)}
        </p>
      )}
      {!data ? (
        !error && <Loading label={ui("Проверяем привязку Minecraft…")} />
      ) : (
        <>
          {data.link ? (
            <>
              <p className="linked-name">
                {data.link.nickname}{" "}
                <span className="tag positive">{ui("Подтверждён")}</span>
              </p>
              <small>
                {ui("Подтверждён ")}
                {dateTime(data.link.verified_at, undefined)}
              </small>
              <button
                className="text-button danger"
                disabled={busy}
                onClick={() => setUnlinking(true)}
              >
                {ui("Отвязать Minecraft")}
              </button>
            </>
          ) : (
            <p className="muted">{ui("Minecraft-аккаунт пока не привязан.")}</p>
          )}
          {!data.integration_enabled ? (
            <p className="info-box">
              {ui(
                "Система банкоматов пока не настроена. Обратитесь к администратору банка.",
              )}
            </p>
          ) : (
            <>
              <p className="muted">
                {ui(
                  "Войдите на Minecraft-сервер. Мастер-бот отправит код в личные сообщения. Для смены аккаунта подтвердите новый ник; текущая привязка действует до подтверждения.",
                )}
              </p>
              <form onSubmit={(event) => void start(event)}>
                <label>
                  {ui("Minecraft-ник")}
                  <input
                    required
                    pattern="[A-Za-z0-9_]{3,16}"
                    minLength={3}
                    maxLength={16}
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    autoComplete="off"
                    disabled={busy}
                  />
                </label>
                <button className="secondary" disabled={busy}>
                  {busy
                    ? ui("Подождите…")
                    : data.link
                      ? ui("Привязать другой аккаунт")
                      : ui("Получить код")}
                </button>
              </form>
              {data.pending && (
                <div className="link-challenge">
                  <p>
                    {ui("Подтверждение ника ")}
                    <b>{data.pending.nickname}</b>
                  </p>
                  <small>
                    {ui("Код действует до ")}
                    {dateTime(data.pending.expires_at, undefined)}
                    {ui(". Не более 5 попыток.")}
                  </small>
                  {data.pending.status === "delivered" ? (
                    <form onSubmit={(event) => void verify(event)}>
                      <label>
                        {ui("Код подтверждения")}
                        <input
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          pattern="[0-9]{6}"
                          minLength={6}
                          maxLength={6}
                          required
                          value={code}
                          onChange={(e) =>
                            setCode(e.target.value.replace(/\D/g, ""))
                          }
                          disabled={busy}
                        />
                      </label>
                      <button disabled={busy}>
                        {ui("Подтвердить привязку")}
                      </button>
                    </form>
                  ) : (
                    <p role="status">
                      {ui(
                        "Ожидаем подтверждение доставки от Minecraft-сервера…",
                      )}
                    </p>
                  )}
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        await request("/minecraft/link/cancel", {
                          method: "POST",
                        });
                        await load();
                        setCode("");
                      })
                    }
                  >
                    {ui("Отменить привязку")}
                  </button>
                </div>
              )}
            </>
          )}
          <button
            className="text-button"
            disabled={busy}
            onClick={() => void action(load)}
          >
            <Icon name="refresh" size={16} />
            {ui("Проверить статус")}
          </button>
        </>
      )}
      {unlinking && (
        <Modal
          title={ui("Отвязать Minecraft?")}
          onClose={() => setUnlinking(false)}
        >
          <p>
            {ui(
              "Доступ к банкоматам потребует повторной привязки. Деньги останутся на вашем банковском счёте.",
            )}
          </p>
          <button
            className="danger-button"
            disabled={busy}
            onClick={() =>
              void action(async () => {
                await api.unlinkMinecraft();
                await load();
                await refresh();
                setUnlinking(false);
              })
            }
          >
            {ui("Подтвердить отвязку")}
          </button>
        </Modal>
      )}
    </section>
  );
}
