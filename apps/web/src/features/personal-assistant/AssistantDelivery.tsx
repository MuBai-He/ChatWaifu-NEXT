import { AlarmClock, Bell, Clock3 } from "lucide-react";
import { readConversationScope } from "../chat/conversationScope";
import { useEffect, useRef, useState } from "react";
import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";
import {
  isDesktopHost,
  resolveRuntimeConnection,
  type RuntimeConnection,
} from "../chat/runtimeEndpoint";
import {
  deviceCall,
  organizerRequest,
  type Delivery,
  type DeviceBinding,
} from "./organizer";
import "./organizer.css";

/** Mounted only by avatar-overlay; never by the control center or web client. */
export function AssistantDelivery() {
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actions, setActions] = useState<NonNullable<DeviceBinding["actions"]>>(
    {},
  );
  const [busy, setBusy] = useState<Set<string>>(() => new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  const [revoked, setRevoked] = useState(false);
  const active = useRef<{
    connection: RuntimeConnection;
    binding: DeviceBinding;
  } | null>(null);
  const sounding = useRef(new Map<string, number>());
  const suppressed = useRef(new Set<string>());
  const clicking = useRef(new Set<string>());
  const revokedDevice = useRef<string | null>(null);
  const acknowledge = async (item: Delivery, action: "stop" | "snooze") => {
    if (clicking.current.has(item.delivery_id)) return;
    clicking.current.add(item.delivery_id);
    setBusy((old) => new Set(old).add(item.delivery_id));
    // Stop sound immediately even if the network is currently unavailable.
    suppressed.current.add(item.delivery_id);
    sounding.current.delete(item.delivery_id);
    const current = active.current;
    if (!current) {
      clicking.current.delete(item.delivery_id);
      setBusy((old) => {
        const updated = new Set(old);
        updated.delete(item.delivery_id);
        return updated;
      });
      return;
    }
    let queued = false;
    try {
      await deviceCall(current.connection.baseUrl, "queue_action", {
        id: item.delivery_id,
        action,
      });
      queued = true;
      setActionError(null);
      setActions((old) => ({
        ...old,
        [item.delivery_id]: { action, rejected: false },
      }));
      setDeliveries((old) =>
        old.filter((d) => d.delivery_id !== item.delivery_id),
      );
      await organizerRequest(
        "/devices/ack",
        {
          device_id: current.binding.device_id,
          secret: current.binding.secret,
          item_id: item.delivery_id,
          action,
        },
        current.connection,
      );
      await deviceCall(
        current.connection.baseUrl,
        "forget_action",
        item.delivery_id,
      );
      setActions((old) => {
        const updated = { ...old };
        delete updated[item.delivery_id];
        return updated;
      });
      setError(null);
    } catch (cause) {
      if (!queued) {
        suppressed.current.delete(item.delivery_id);
        setActionError("本机未能保存这次操作，请重新点击关闭或贪睡。");
      } else if (
        cause instanceof Error &&
        cause.message === "delivery_not_active"
      ) {
        await deviceCall(
          current.connection.baseUrl,
          "reject_action",
          item.delivery_id,
        ).catch(() => undefined);
        setActions((old) => ({
          ...old,
          [item.delivery_id]: { action, rejected: true },
        }));
      } else {
        setError("声音已停止；操作已保存到本机，会在恢复连接后自动确认。");
      }
    } finally {
      clicking.current.delete(item.delivery_id);
      setBusy((old) => {
        const updated = new Set(old);
        updated.delete(item.delivery_id);
        return updated;
      });
    }
  };
  useEffect(() => {
    if (!isDesktopHost()) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    let lastServer = "";
    const poll = async () => {
      try {
        const connection = await resolveRuntimeConnection();
        const scope = await readConversationScope();
        if (scope.participant_id !== "local" || scope.scene_id) {
          setDeliveries([]);
          setActions({});
          setActionError(null);
          sounding.current.clear();
          active.current = null;
          return;
        }
        if (abort.signal.aborted) return;
        if (lastServer !== connection.baseUrl) {
          setDeliveries([]);
          sounding.current.clear();
          suppressed.current.clear();
          setActions({});
          setActionError(null);
          setRevoked(false);
          revokedDevice.current = null;
          active.current = null;
          lastServer = connection.baseUrl;
        }
        const binding = await deviceCall<DeviceBinding | null>(
          connection.baseUrl,
          "load",
        );
        if (abort.signal.aborted) return;
        if (!binding) {
          active.current = null;
          setDeliveries([]);
          setActions({});
          setActionError(null);
          setRevoked(false);
          return;
        }
        if (revokedDevice.current === binding.device_id) {
          setRevoked(true);
          setDeliveries([]);
          sounding.current.clear();
          return;
        }
        const deviceChanged =
          active.current !== null &&
          active.current.binding.device_id !== binding.device_id;
        if (deviceChanged) {
          suppressed.current.clear();
          sounding.current.clear();
          setActions({});
          setActionError(null);
        }
        revokedDevice.current = null;
        setRevoked(false);
        active.current = { connection, binding };
        for (const id of binding.presentation_receipts ?? []) {
          try {
            await organizerRequest(
              "/devices/ack",
              {
                device_id: binding.device_id,
                secret: binding.secret,
                item_id: id,
                action: "presented",
              },
              connection,
              abort.signal,
            );
          } catch (e) {
            if (!(e instanceof Error && e.message === "delivery_not_active"))
              throw e;
          }
          await deviceCall(connection.baseUrl, "forget_presentation", id);
        }
        const retainedActions = binding.actions ?? {};
        setActions((old) =>
          deviceChanged ? retainedActions : { ...old, ...retainedActions },
        );
        for (const [id, receipt] of Object.entries(retainedActions)) {
          suppressed.current.add(id);
          if (receipt.rejected) continue;
          try {
            await organizerRequest(
              "/devices/ack",
              {
                device_id: binding.device_id,
                secret: binding.secret,
                item_id: id,
                action: receipt.action,
              },
              connection,
              abort.signal,
            );
          } catch (cause) {
            if (
              !(cause instanceof Error) ||
              cause.message !== "delivery_not_active"
            )
              throw cause;
            await deviceCall(connection.baseUrl, "reject_action", id);
            setActions((old) => ({
              ...old,
              [id]: { ...receipt, rejected: true },
            }));
            continue;
          }
          await deviceCall(connection.baseUrl, "forget_action", id);
          setActions((old) => {
            const updated = { ...old };
            delete updated[id];
            return updated;
          });
        }
        // Retained results are retried, never the EventKit write itself.
        for (const [id, result] of Object.entries(binding.results)) {
          try {
            await organizerRequest(
              "/devices/ack",
              {
                device_id: binding.device_id,
                secret: binding.secret,
                item_id: id,
                action: "result",
                result,
              },
              connection,
              abort.signal,
            );
            await deviceCall(connection.baseUrl, "forget_result", id);
          } catch (e) {
            if (
              e instanceof Error &&
              ["operation_not_active", "source_no_longer_selected"].includes(
                e.message,
              )
            )
              await deviceCall(connection.baseUrl, "forget_result", id);
            else throw e;
          }
        }
        const result = await organizerRequest<{
          deliveries: Delivery[];
          operations: Record<string, unknown>[];
        }>(
          "/devices/poll",
          {
            device_id: binding.device_id,
            secret: binding.secret,
            sources: binding.sources,
            source_revision: binding.source_revision,
          },
          connection,
          abort.signal,
        );
        if (abort.signal.aborted) return;
        const fresh = result.deliveries.filter(
          (d) =>
            d.expires * 1000 > Date.now() &&
            !suppressed.current.has(d.delivery_id),
        );
        // All cards must have a durable native receipt before any can be
        // clicked. A slow server ACK for one card must not expose another
        // card whose local stop/snooze cannot yet be journaled.
        const presented: { delivery: Delivery; first: boolean }[] = [];
        for (const delivery of fresh) {
          const { first } = await deviceCall<{ first: boolean }>(
            connection.baseUrl,
            "present",
            { id: delivery.delivery_id },
          );
          presented.push({ delivery, first });
        }
        if (abort.signal.aborted) return;
        setDeliveries(fresh);
        sounding.current = new Map(
          [...sounding.current].filter(([id]) =>
            fresh.some((d) => d.delivery_id === id),
          ),
        );
        for (const { delivery, first } of presented) {
          if (first && !abort.signal.aborted) {
            if (delivery.kind === "alarm")
              sounding.current.set(delivery.delivery_id, delivery.expires);
            void deviceCall(connection.baseUrl, "notify", {
              title: delivery.title,
            }).catch(() => {
              setError("系统通知未送达；提醒仍显示在桌宠中。");
            });
          }
        }
        for (const { delivery } of presented) {
          // Native presentation is durable. Retry this idempotent receipt even
          // when the previous server acknowledgement was lost after a remount.
          if (
            !abort.signal.aborted &&
            !suppressed.current.has(delivery.delivery_id)
          ) {
            try {
              await organizerRequest(
                "/devices/ack",
                {
                  device_id: binding.device_id,
                  secret: binding.secret,
                  item_id: delivery.delivery_id,
                  action: "presented",
                },
                connection,
                abort.signal,
              );
            } catch (error) {
              if (
                !suppressed.current.has(delivery.delivery_id) ||
                !(error instanceof Error) ||
                error.message !== "delivery_not_active"
              )
                throw error;
            }
            await deviceCall(
              connection.baseUrl,
              "forget_presentation",
              delivery.delivery_id,
            );
          }
        }
        for (const operation of result.operations) {
          if (abort.signal.aborted) break;
          // This call durably journals uncertain writes before entering EventKit.
          const output = await deviceCall<Record<string, unknown>>(
            connection.baseUrl,
            "execute",
            operation,
          );
          await organizerRequest(
            "/devices/ack",
            {
              device_id: binding.device_id,
              secret: binding.secret,
              item_id: operation.request_id,
              action: "result",
              result: output,
            },
            connection,
            abort.signal,
          );
          await deviceCall(
            connection.baseUrl,
            "forget_result",
            operation.request_id,
          );
        }
        failures = 0;
        setError(null);
      } catch (cause) {
        failures += 1;
        sounding.current.clear(); // Network loss cannot keep an un-cancellable alarm ringing.
        if (
          cause instanceof Error &&
          cause.message === "device_not_authorized"
        ) {
          revokedDevice.current = active.current?.binding.device_id ?? null;
          setRevoked(true);
          setDeliveries([]);
          setError(null);
        } else if (!abort.signal.aborted) {
          setError("提醒设备暂时离线，正在重新连接。");
        }
      } finally {
        if (!abort.signal.aborted)
          timer = setTimeout(
            () => void poll(),
            revokedDevice.current
              ? 30000
              : Math.min(30000, 2000 * 2 ** Math.min(failures, 4)),
          );
      }
    };
    void poll();
    const ring = setInterval(() => {
      setDeliveries((old) => old.filter((d) => d.expires * 1000 > Date.now()));
      const current = active.current;
      if (
        current &&
        [...sounding.current].some(
          ([id, expires]) =>
            expires * 1000 > Date.now() && !suppressed.current.has(id),
        )
      ) {
        void deviceCall(current.connection.baseUrl, "sound").catch(
          () => undefined,
        );
      }
    }, 3000);
    return () => {
      abort.abort();
      clearTimeout(timer);
      clearInterval(ring);
      sounding.current.clear();
      active.current = null;
    };
  }, []);
  const actionCount = Object.keys(actions).length;
  useEffect(
    () =>
      deliveries.length || actionCount || revoked || actionError
        ? acquireNativeInteractionGuard("dialog")
        : undefined,
    [actionCount, actionError, deliveries.length, revoked],
  );
  const pendingCount = revoked
    ? 0
    : Object.values(actions).filter((record) => !record.rejected).length;
  const rejectedActions = revoked
    ? []
    : Object.entries(actions).filter(([, record]) => record.rejected);
  if (
    !deliveries.length &&
    !pendingCount &&
    !rejectedActions.length &&
    !revoked &&
    !actionError
  )
    return null;
  const dismissRejected = async (id: string) => {
    const current = active.current;
    if (!current) return;
    try {
      await deviceCall(current.connection.baseUrl, "forget_action", id);
      setActions((old) => {
        const updated = { ...old };
        delete updated[id];
        return updated;
      });
    } catch {
      setError("无法清除本机记录，请稍后重试。");
    }
  };
  return (
    <aside
      className="assistant-alert"
      role="alert"
      data-native-interactive="true"
    >
      {deliveries.map((d) => (
        <section className="assistant-alert-card" key={d.delivery_id}>
          <header className="assistant-alert-heading">
            <span className="assistant-alert-icon" aria-hidden="true">
              {d.kind === "alarm" ? (
                <AlarmClock size={19} />
              ) : (
                <Bell size={19} />
              )}
            </span>
            <div>
              <span className="assistant-alert-kind">
                {d.kind === "alarm" ? "闹钟" : "提醒"}
              </span>
              <strong>{d.title}</strong>
              {Number.isFinite(d.due) && (
                <small className="assistant-alert-time">
                  原定 {new Date(d.due * 1000).toLocaleString()}
                </small>
              )}
            </div>
          </header>
          <div className="assistant-alert-actions">
            <button
              className="assistant-alert-stop"
              disabled={busy.has(d.delivery_id)}
              onClick={() => void acknowledge(d, "stop")}
            >
              关闭
            </button>
            <button
              disabled={busy.has(d.delivery_id)}
              onClick={() => void acknowledge(d, "snooze")}
            >
              <Clock3 size={14} aria-hidden="true" />5 分钟后提醒
            </button>
          </div>
        </section>
      ))}
      {pendingCount > 0 && (
        <small className="assistant-alert-error">
          {pendingCount} 项操作已保存在本机，等待服务器确认。
        </small>
      )}
      {rejectedActions.slice(0, 3).map(([id, record]) => (
        <section className="assistant-alert-card" key={id}>
          <strong>
            {record.action === "snooze"
              ? "贪睡未生效：这次提醒已过期或取消，请重新设置。"
              : "关闭未获服务器确认：这次提醒已过期或取消。"}
          </strong>
          <button onClick={() => void dismissRejected(id)}>知道了</button>
        </section>
      ))}
      {rejectedActions.length > 3 && (
        <small className="assistant-alert-error">
          另有 {rejectedActions.length - 3} 项操作未生效。
        </small>
      )}
      {revoked && (
        <section className="assistant-alert-card">
          <strong>此设备配对已撤销，请在桌宠设置中重新配对。</strong>
        </section>
      )}
      {actionError && (
        <section className="assistant-alert-card">
          <strong>{actionError}</strong>
          <button onClick={() => setActionError(null)}>知道了</button>
        </section>
      )}
      {error && <small className="assistant-alert-error">{error}</small>}
    </aside>
  );
}
