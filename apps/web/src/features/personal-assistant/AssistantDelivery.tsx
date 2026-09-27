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
  const active = useRef<{
    connection: RuntimeConnection;
    binding: DeviceBinding;
  } | null>(null);
  const sounding = useRef(new Map<string, number>());
  const suppressed = useRef(new Set<string>());
  const acknowledge = async (item: Delivery, action: "stop" | "snooze") => {
    // Stop sound immediately even if the network is currently unavailable.
    suppressed.current.add(item.delivery_id);
    const current = active.current;
    if (!current) return;
    try {
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
      setDeliveries((old) =>
        old.filter((d) => d.delivery_id !== item.delivery_id),
      );
      setError(null);
    } catch {
      setError("声音已停止，服务器尚未确认。请恢复连接后重试关闭或贪睡。");
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
          sounding.current.clear();
          active.current = null;
          return;
        }
        if (abort.signal.aborted) return;
        if (lastServer !== connection.baseUrl) {
          setDeliveries([]);
          sounding.current.clear();
          suppressed.current.clear();
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
          return;
        }
        active.current = { connection, binding };
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
          (d) => d.expires * 1000 > Date.now(),
        );
        setDeliveries(fresh);
        sounding.current = new Map(
          [...sounding.current].filter(([id]) =>
            fresh.some((d) => d.delivery_id === id),
          ),
        );
        for (const delivery of fresh) {
          const { first } = await deviceCall<{ first: boolean }>(
            connection.baseUrl,
            "present",
            { id: delivery.delivery_id },
          );
          if (first && !abort.signal.aborted) {
            if (delivery.kind === "alarm")
              sounding.current.set(delivery.delivery_id, delivery.expires);
            void deviceCall(connection.baseUrl, "notify", {
              title: delivery.title,
            }).catch(() => {
              setError("系统通知未送达；提醒仍显示在桌宠中。");
            });
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
      } catch {
        failures += 1;
        sounding.current.clear(); // Network loss cannot keep an un-cancellable alarm ringing.
        if (!abort.signal.aborted) setError("提醒设备暂时离线，正在重新连接。");
      } finally {
        if (!abort.signal.aborted)
          timer = setTimeout(
            () => void poll(),
            Math.min(30000, 2000 * 2 ** Math.min(failures, 4)),
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
  useEffect(
    () =>
      deliveries.length ? acquireNativeInteractionGuard("dialog") : undefined,
    [deliveries.length],
  );
  if (!deliveries.length) return null;
  return (
    <aside
      className="assistant-alert"
      role="alert"
      data-native-interactive="true"
    >
      {deliveries.map((d) => (
        <div key={d.delivery_id}>
          <strong>
            {d.kind === "alarm" ? "闹钟" : "提醒"} · {d.title}
          </strong>
          <div>
            <button onClick={() => void acknowledge(d, "stop")}>关闭</button>
            <button onClick={() => void acknowledge(d, "snooze")}>
              5 分钟后提醒
            </button>
          </div>
        </div>
      ))}
      {error && <small>{error}</small>}
    </aside>
  );
}
