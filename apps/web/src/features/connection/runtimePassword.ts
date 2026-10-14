import {
  assertRuntimeRequestContext,
  isDesktopHost,
  isRemoteRuntime,
  runtimeFetchWithConnection,
  type RuntimeRequestContext,
} from "../chat/runtimeEndpoint";

export interface PasswordStatus {
  schema_version: 1;
  configured: boolean;
  revision: number;
}

async function passwordResponse(response: Response): Promise<unknown> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      response.status === 404
        ? "当前服务器尚未支持登录密码，请更新 Runtime。"
        : typeof body?.detail === "string"
          ? body.detail
          : `密码操作失败（${response.status}）。`,
    );
  }
  return response.json();
}

function sessionToken(value: unknown): string {
  const data = value as { schema_version?: unknown; token?: unknown } | null;
  if (
    data?.schema_version !== 1 ||
    typeof data.token !== "string" ||
    data.token.length < 32 ||
    !/^[A-Za-z0-9_-]+$/.test(data.token)
  )
    throw new Error("服务器返回了无效的登录凭据。");
  return data.token;
}

export async function loginWithPassword(
  baseUrl: string,
  password: string,
  signal: AbortSignal,
): Promise<string> {
  const response = await runtimeFetchWithConnection(
    { baseUrl },
    "/v1/runtime/password/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
      signal,
      cache: "no-store",
      redirect: "error",
      credentials: "omit",
    },
  );
  const data = await passwordResponse(response);
  signal.throwIfAborted();
  return sessionToken(data);
}

export async function getPasswordStatus(
  context: RuntimeRequestContext,
  signal: AbortSignal,
): Promise<PasswordStatus> {
  assertRuntimeRequestContext(context);
  const data = await passwordResponse(
    await runtimeFetchWithConnection(
      context.connection,
      "/v1/runtime/password",
      { signal, cache: "no-store", redirect: "error" },
    ),
  );
  signal.throwIfAborted();
  assertRuntimeRequestContext(context);
  const status = data as PasswordStatus | null;
  if (
    status?.schema_version !== 1 ||
    typeof status.configured !== "boolean" ||
    !Number.isSafeInteger(status.revision) ||
    status.revision < 0
  )
    throw new Error("服务器返回了无效的密码设置。");
  return status;
}

export async function updatePassword(
  context: RuntimeRequestContext,
  update: {
    new_password: string;
    current_password?: string;
    expected_revision: number;
  },
  signal: AbortSignal,
): Promise<string> {
  assertRuntimeRequestContext(context);
  const data = await passwordResponse(
    await runtimeFetchWithConnection(
      context.connection,
      "/v1/runtime/password",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
        signal,
        cache: "no-store",
        redirect: "error",
      },
    ),
  );
  signal.throwIfAborted();
  assertRuntimeRequestContext(context);
  return sessionToken(data);
}

/** Persist only the exchanged credential, using the same native/browser boundary as the gate. */
export async function replacePasswordSession(
  context: RuntimeRequestContext,
  token: string,
): Promise<void> {
  assertRuntimeRequestContext(context);
  if (!isRemoteRuntime()) return;
  const connection = {
    mode: "remote",
    base_url: context.connection.baseUrl,
    token,
  };
  if (isDesktopHost()) {
    const { invoke } = await import("@tauri-apps/api/core");
    assertRuntimeRequestContext(context);
    await invoke("set_client_connection", { connection });
  } else {
    sessionStorage.setItem(
      "chatwaifu.client.connection",
      JSON.stringify(connection),
    );
    window.location.reload();
  }
}
