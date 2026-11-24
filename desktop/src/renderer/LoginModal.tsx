// D170：AVA 独立 /login 多服务商认证弹窗（对齐 Pi /login 生态覆盖与 ZCode ui-* 规范）。
// 只调 auth.status / auth.start / auth.complete / auth.logout RPC（密钥永不进 spawn argv，通过 core stdin 写 config/auth.local.json）。
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AuthCompleteResult,
  AuthExtraFieldSpec,
  AuthMode,
  AuthProviderRow,
  AuthStartResult,
  AuthStatusResult,
  ProviderCategory,
} from "../shared/protocol";
import type { RpcClient } from "./rpc";
import { FloatingModal } from "./floating";
import { Icon } from "./icons";
import { SegmentedBar } from "./motion";

const MODE_LABELS: Record<AuthMode, string> = {
  oauth_pkce: "OAuth 浏览器授权",
  oauth_device: "设备码授权",
  proxy: "代理地址 + 密钥",
  api_key: "API Key / 凭据",
};

const MODE_SHORT: Record<AuthMode, string> = {
  oauth_pkce: "OAuth",
  oauth_device: "设备码",
  proxy: "代理",
  api_key: "密钥",
};

const CATEGORY_ORDER: readonly { key: ProviderCategory; label: string }[] = [
  { key: "oauth", label: "OAuth 订阅" },
  { key: "flagship", label: "常用大模型" },
  { key: "cloud", label: "云平台与路由" },
  { key: "local", label: "本地 / 代理" },
];

const MASKED_EXTRA_KEYS = new Set([
  "aws_access_key_id",
  "credentials_path",
  "enterprise_domain",
  "project_id",
]);

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function initExtraValues(row: AuthProviderRow): Record<string, string> {
  const out: Record<string, string> = {};
  for (const ef of row.extra_fields ?? []) {
    const masked = Boolean(ef.secret) || MASKED_EXTRA_KEYS.has(ef.key);
    const existing = !masked ? row.extra_config?.[ef.key] : "";
    out[ef.key] = (existing || ef.default || "").trim();
  }
  return out;
}

function clearSecretExtraValues(
  row: AuthProviderRow,
  prev: Record<string, string>,
): Record<string, string> {
  const secretKeys = new Set((row.extra_fields ?? []).filter((ef) => ef.secret).map((ef) => ef.key));
  if (secretKeys.size === 0) return prev;
  const next = { ...prev };
  for (const key of secretKeys) next[key] = "";
  return next;
}

function hasCloudAltCredentials(row: AuthProviderRow, extraValues: Record<string, string>): boolean {
  if (row.id === "amazon-bedrock") {
    const iam = Boolean(extraValues.aws_access_key_id?.trim() && extraValues.aws_secret_access_key?.trim());
    const prof = Boolean(extraValues.aws_profile?.trim());
    return iam || prof || Boolean(row.env_configured);
  }
  if (row.id === "google-vertex") {
    return Boolean(extraValues.project_id?.trim() || extraValues.credentials_path?.trim());
  }
  return false;
}

function isImeEnter(e: { key: string; keyCode: number; nativeEvent: { isComposing: boolean } }): boolean {
  return e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229;
}

function ExtraFieldsGrid({
  fields,
  values,
  showRequiredMark,
  onChange,
  onEnter,
}: {
  fields: AuthExtraFieldSpec[];
  values: Record<string, string>;
  showRequiredMark: boolean;
  onChange: (key: string, value: string) => void;
  onEnter?: () => void;
}) {
  if (fields.length === 0) return null;
  return (
    <div className="login-extra-grid" data-testid="login-extra-fields">
      {fields.map((ef) => (
        <label key={ef.key} className="ui-field">
          <span className="login-field-head">
            <span>{ef.label}{showRequiredMark && ef.required ? " *" : ""}</span>
            {ef.env_var && <span className="ui-badge">{ef.env_var}</span>}
          </span>
          <input
            type={ef.secret ? "password" : "text"}
            className="ui-input login-mono-input"
            data-testid={`login-extra-${ef.key}`}
            value={values[ef.key] ?? ""}
            placeholder={ef.placeholder || ef.default}
            onChange={(e) => onChange(ef.key, e.target.value)}
            onKeyDown={(e) => {
              if (isImeEnter(e) && onEnter) {
                e.preventDefault();
                onEnter();
              }
            }}
          />
          {ef.help && <span className="login-field-help">{ef.help}</span>}
        </label>
      ))}
    </div>
  );
}

export function LoginModal({
  open,
  rpc,
  initialStatus,
  preferredProvider,
  onClose,
  onAuthChanged,
}: {
  open: boolean;
  rpc: RpcClient;
  initialStatus: AuthStatusResult | null;
  preferredProvider?: string;
  onClose: () => void;
  onAuthChanged: (status: AuthStatusResult) => void;
}) {
  const [status, setStatus] = useState<AuthStatusResult | null>(initialStatus);
  const [selectedId, setSelectedId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<AuthMode>("api_key");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [authCode, setAuthCode] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [started, setStarted] = useState<AuthStartResult | null>(null);
  const [pollActive, setPollActive] = useState(false);
  const [logoutArmed, setLogoutArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  // flowGenRef 在切服务商、切认证方式、重新发起或关闭弹窗时递增，防止迟到异步回包串态或幽灵轮询
  const flowGenRef = useRef(0);
  const statusGenRef = useRef(0);
  // inFlightGenRef 在同一 flowGen 内串行化设备码自动轮询与手动「立即检查」，避免竞态覆写成功提示
  const inFlightGenRef = useRef<number | null>(null);

  const syncProviderDefaults = (row: AuthProviderRow) => {
    setSelectedId(row.id);
    setMode(row.auth_modes[0] ?? "api_key");
    setBaseUrl(row.base_url || row.default_base_url);
    setApiKey("");
    setAuthCode("");
    setExtraValues(initExtraValues(row));
    setStarted(null);
    setPollActive(false);
    setLogoutArmed(false);
    setBusy(false);
  };

  const loadStatus = (preferredId?: string) => {
    const sGen = (statusGenRef.current += 1);
    void rpc.call<AuthStatusResult>("auth.status", {}).then(
      (res) => {
        setStatus(res);
        onAuthChanged(res);
        if (!openRef.current || sGen !== statusGenRef.current) return;
        const list = res.providers ?? [];
        const pick =
          (preferredId && list.find((p) => p.id === preferredId)) ||
          (selectedId && list.find((p) => p.id === selectedId)) ||
          list.find((p) => p.connected) ||
          list[0];
        if (pick) syncProviderDefaults(pick);
      },
      (e) => {
        if (!openRef.current || sGen !== statusGenRef.current) return;
        setMsg({ kind: "err", text: errMsg(e) });
      },
    );
  };

  useEffect(() => {
    flowGenRef.current += 1;
    statusGenRef.current += 1;
    if (!open) {
      setApiKey("");
      setAuthCode("");
      setExtraValues({});
      setStarted(null);
      setPollActive(false);
      setLogoutArmed(false);
      setBusy(false);
      setMsg(null);
      return;
    }
    setMsg(null);
    setLogoutArmed(false);
    setBusy(false);
    loadStatus(preferredProvider || selectedId);
  }, [open, preferredProvider]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!logoutArmed) return;
    const t = window.setTimeout(() => setLogoutArmed(false), 5000);
    return () => window.clearTimeout(t);
  }, [logoutArmed]);

  const providers = status?.providers ?? [];
  const q = query.trim().toLowerCase();

  const { groups, flatVisible } = useMemo(() => {
    const filtered = q
      ? providers.filter((p) => {
          const hay = [
            p.name,
            p.id,
            p.desc,
            p.api_protocol,
            ...(p.aliases ?? []),
            ...(p.env_vars ?? []),
          ]
            .join(" ")
            .toLowerCase();
          return hay.includes(q);
        })
      : providers;

    const out: { key: string; label: string; items: AuthProviderRow[] }[] = [];
    const connectedItems = filtered.filter((p) => p.connected);
    if (connectedItems.length > 0) {
      out.push({ key: "connected", label: "已连接", items: connectedItems });
    }
    const remaining = filtered.filter((p) => !p.connected);
    for (const cat of CATEGORY_ORDER) {
      const items = remaining.filter((p) => (p.category ?? "flagship") === cat.key);
      if (items.length > 0) out.push({ key: cat.key, label: cat.label, items });
    }
    const categorizedIds = new Set(out.flatMap((g) => g.items.map((p) => p.id)));
    const other = remaining.filter((p) => !categorizedIds.has(p.id));
    if (other.length > 0) out.push({ key: "other", label: "其他", items: other });

    return { groups: out, flatVisible: out.flatMap((g) => g.items) };
  }, [providers, q]);

  const selectProvider = (row: AuthProviderRow) => {
    flowGenRef.current += 1;
    statusGenRef.current += 1;
    setMsg(null);
    syncProviderDefaults(row);
  };

  const stepSelection = (delta: number) => {
    if (flatVisible.length === 0) return;
    const idx = flatVisible.findIndex((p) => p.id === selectedId);
    const nextIdx = idx < 0 ? 0 : Math.max(0, Math.min(flatVisible.length - 1, idx + delta));
    const next = flatVisible[nextIdx];
    if (next) {
      selectProvider(next);
      const btn = listRef.current?.querySelector(`[data-provider="${next.id}"]`);
      if (btn instanceof HTMLElement) btn.scrollIntoView({ block: "nearest" });
    }
  };

  const current = providers.find((p) => p.id === selectedId) ?? flatVisible[0] ?? providers[0] ?? null;

  // A4：设备码授权自动轮询，完成即自动落盘刷新；切服务商/关弹窗时立刻停表，与手动检查共用 flowGen 互斥锁，瞬时错误有限次退避
  useEffect(() => {
    if (!open || !pollActive || mode !== "oauth_device" || !started?.user_code || !current) return;
    let alive = true;
    const gen = flowGenRef.current;
    const intervalMs = Math.max(5, started.interval_s ?? 5) * 1000;
    const providerId = current.id;
    const providerName = current.name;
    let errCount = 0;
    let nextAllowedAt = 0;
    const timer = window.setInterval(() => {
      if (!alive || gen !== flowGenRef.current || inFlightGenRef.current === gen) return;
      if (Date.now() < nextAllowedAt) return;
      inFlightGenRef.current = gen;
      void rpc
        .call<AuthCompleteResult>("auth.complete", {
          provider: providerId,
          payload: JSON.stringify({ mode: "oauth_device" }),
        })
        .then(
          (res) => {
            if (!alive || gen !== flowGenRef.current) return;
            errCount = 0;
            if (res.pending) return;
            setPollActive(false);
            setStarted(null);
            setMsg({ kind: "ok", text: res.text || `已登录 ${providerName}` });
            loadStatus(providerId);
          },
          (e) => {
            if (!alive || gen !== flowGenRef.current) return;
            const text = errMsg(e);
            const fatal = /过期|expired|尚未发起|拒绝|access_denied|取消/i.test(text);
            errCount += 1;
            if (fatal || errCount >= 3) {
              setPollActive(false);
              setMsg({ kind: "err", text });
            } else {
              nextAllowedAt = Date.now() + intervalMs * Math.min(4, errCount + 1);
              setMsg({ kind: "warn", text: `检查授权状态暂时失败（第 ${errCount} 次），稍后自动重试：${text}` });
            }
          },
        )
        .finally(() => {
          if (inFlightGenRef.current === gen) inFlightGenRef.current = null;
        });
    }, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [open, pollActive, mode, started?.user_code, started?.interval_s, current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStartOAuth = () => {
    if (!current) return;
    const gen = (flowGenRef.current += 1);
    statusGenRef.current += 1;
    setBusy(true);
    setMsg(null);
    setPollActive(false);
    const startExtra: Record<string, string> = {};
    if (mode === "oauth_device") {
      for (const ef of current.extra_fields ?? []) {
        if (ef.secret) continue;
        const val = (extraValues[ef.key] ?? "").trim();
        if (val) startExtra[ef.key] = val;
      }
    }
    const rpcArgs: Record<string, string> = { provider: current.id, mode };
    if (Object.keys(startExtra).length > 0) {
      rpcArgs.payload = JSON.stringify({ extra_config: startExtra });
    }
    void rpc
      .call<AuthStartResult>("auth.start", rpcArgs)
      .then(
        (res) => {
          if (gen !== flowGenRef.current) return;
          setStarted(res);
          if (res.mode === "oauth_pkce" && res.authorize_url) {
            setMsg({ kind: "ok", text: "已生成授权链接：在浏览器完成登录后，复制回调链接或授权码粘贴到下方" });
          } else if (res.mode === "oauth_device" && res.user_code) {
            setPollActive(true);
            setMsg({ kind: "ok", text: `设备码 ${res.user_code} 已生成，在浏览器完成授权后将自动生效` });
          }
        },
        (e) => {
          if (gen !== flowGenRef.current) return;
          setMsg({ kind: "err", text: errMsg(e) });
        },
      )
      .finally(() => {
        if (gen === flowGenRef.current) setBusy(false);
      });
  };

  const openInBrowser = (url: string) => {
    const gen = flowGenRef.current;
    void rpc.call<{ ok: boolean }>("auth.openUrl", { url }).catch((e) => {
      if (gen !== flowGenRef.current) return;
      setMsg({ kind: "err", text: `未能自动打开浏览器（可点右侧复制链接手动打开）：${errMsg(e)}` });
    });
  };

  const handleSubmit = () => {
    if (!current) return;
    const gen = flowGenRef.current;
    statusGenRef.current += 1;
    if (mode === "oauth_device" && inFlightGenRef.current === gen) {
      setMsg({ kind: "warn", text: "正在自动检查授权状态，请稍候…" });
      return;
    }
    inFlightGenRef.current = gen;
    setBusy(true);
    setMsg(null);
    const cleanExtra: Record<string, string> = {};
    for (const [k, v] of Object.entries(extraValues)) {
      if (v.trim()) cleanExtra[k] = v.trim();
    }
    const payloadObj: Record<string, unknown> = { mode };
    if (mode === "proxy") {
      payloadObj.base_url = baseUrl.trim() || current.default_base_url;
      if (apiKey.trim()) payloadObj.api_key = apiKey.trim();
    } else if (mode === "api_key") {
      if (apiKey.trim()) payloadObj.api_key = apiKey.trim();
      if (baseUrl.trim()) payloadObj.base_url = baseUrl.trim();
    } else if (mode === "oauth_pkce") {
      payloadObj.code = authCode.trim();
    }
    if (Object.keys(cleanExtra).length > 0) {
      payloadObj.extra_config = cleanExtra;
    }
    const targetProvider = current;
    void rpc
      .call<AuthCompleteResult>("auth.complete", {
        provider: targetProvider.id,
        payload: JSON.stringify(payloadObj),
      })
      .then(
        (res) => {
          if (gen !== flowGenRef.current) return;
          if (res.pending) {
            setMsg({ kind: "warn", text: res.text || "等待用户在浏览器完成设备码授权…" });
            return;
          }
          setApiKey("");
          setAuthCode("");
          setStarted(null);
          setPollActive(false);
          setMsg({ kind: "ok", text: res.text || `已登录 ${targetProvider.name}` });
          loadStatus(targetProvider.id);
        },
        (e) => {
          if (gen !== flowGenRef.current) return;
          setExtraValues((prev) => clearSecretExtraValues(targetProvider, prev));
          setMsg({ kind: "err", text: errMsg(e) });
        },
      )
      .finally(() => {
        if (inFlightGenRef.current === gen) inFlightGenRef.current = null;
        if (gen === flowGenRef.current) setBusy(false);
      });
  };

  const handleLogout = () => {
    if (!current) return;
    const gen = (flowGenRef.current += 1);
    statusGenRef.current += 1;
    const targetProvider = current;
    setBusy(true);
    setMsg(null);
    setLogoutArmed(false);
    setPollActive(false);
    void rpc
      .call<{ ok: boolean; text: string }>("auth.logout", { provider: targetProvider.id })
      .then(
        (res) => {
          if (gen !== flowGenRef.current) return;
          setApiKey("");
          setAuthCode("");
          setStarted(null);
          setExtraValues(initExtraValues(targetProvider));
          setMsg({ kind: "ok", text: res.text });
          loadStatus(targetProvider.id);
        },
        (e) => {
          if (gen !== flowGenRef.current) return;
          setMsg({ kind: "err", text: errMsg(e) });
        },
      )
      .finally(() => {
        if (gen === flowGenRef.current) setBusy(false);
      });
  };

  const copyText = (key: string, text: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((prev) => (prev === key ? null : prev)), 1500);
    });
  };

  const missingRequiredExtra = (current?.extra_fields ?? []).some(
    (ef) => ef.required && !(extraValues[ef.key] ?? "").trim(),
  );
  const altCloudOk = current ? hasCloudAltCredentials(current, extraValues) : false;
  const submitDisabled =
    busy ||
    !current ||
    missingRequiredExtra ||
    (mode === "api_key" && apiKey.trim() === "" && !altCloudOk) ||
    (mode === "oauth_pkce" && authCode.trim() === "");

  const extraFields: AuthExtraFieldSpec[] = current?.extra_fields ?? [];
  const needsLoopbackNote =
    Boolean(started?.redirect_uri?.includes("localhost:")) ||
    Boolean(started?.redirect_uri?.includes("127.0.0.1:"));

  return (
    <FloatingModal
      open={open}
      onClose={onClose}
      backdropTestId="login-backdrop"
      testId="login-modal"
      className="login-modal"
      ariaLabel="模型服务商登录（/login）"
    >
      <div className="login-modal-head">
          <div className="login-modal-title-wrap">
            <h2 id="login-modal-title" className="login-modal-title">
              模型服务商登录（/login）
            </h2>
            <span className="muted login-modal-sub">
              {status
                ? `已连接 ${status.connected_count} / ${providers.length} 家 · ${
                    status.storage_encrypted === true
                      ? "凭据经系统钥匙串加密保存"
                      : status.storage_encrypted === false
                        ? "凭据仅保存在本机（未启用钥匙串加密）"
                        : "凭据仅保存在本机"
                  }`
                : "正在读取认证状态…"}
            </span>
          </div>
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm"
            data-testid="login-close-btn"
            aria-label="关闭登录窗口"
            onClick={onClose}
          >
            <Icon name="close" size="sm" />
            关闭
          </button>
        </div>

        <div className="login-modal-body">
          <div className="login-provider-pane">
            <div className="ui-search login-provider-search">
              <Icon name="search" size="sm" />
              <input
                className="ui-input"
                data-testid="login-provider-search"
                placeholder={`筛选服务商（${providers.length || 44} 家，名称/协议/环境变量）…`}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    stepSelection(1);
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    stepSelection(-1);
                  } else if (e.key === "Escape" && query !== "") {
                    e.preventDefault();
                    e.stopPropagation();
                    setQuery("");
                  }
                }}
              />
              {query !== "" && (
                <button
                  type="button"
                  className="ui-search-clear"
                  aria-label="清空筛选"
                  onClick={() => setQuery("")}
                >
                  <Icon name="close" size="sm" />
                </button>
              )}
            </div>
            <div className="login-provider-list" ref={listRef}>
              {groups.map((grp) => (
                <div key={grp.key} className="login-group" data-testid={`login-group-${grp.key}`}>
                  <div className="ui-section login-group-head">
                    <span>{grp.label}</span>
                    <span className="ui-count">{grp.items.length}</span>
                  </div>
                  {grp.items.map((p) => {
                    const isSelected = current?.id === p.id;
                    const primaryMode = p.auth_modes[0] ?? "api_key";
                    const badgeLabel = p.connected
                      ? "已连接"
                      : p.env_configured
                        ? "环境变量"
                        : MODE_SHORT[primaryMode];
                    const badgeCls = p.connected ? "ui-badge ui-badge--ok" : "ui-badge";
                    const dotCls = p.connected ? "ui-dot ui-dot--ok" : "ui-dot";
                    return (
                      <button
                        key={p.id}
                        type="button"
                        aria-current={isSelected ? "true" : undefined}
                        className="ui-row login-provider-row"
                        data-testid="login-provider-row"
                        data-provider={p.id}
                        data-connected={p.connected ? "1" : "0"}
                        onClick={() => selectProvider(p)}
                        onKeyDown={(e) => {
                          if (e.key === "ArrowDown") {
                            e.preventDefault();
                            stepSelection(1);
                          } else if (e.key === "ArrowUp") {
                            e.preventDefault();
                            stepSelection(-1);
                          }
                        }}
                      >
                        <span className={dotCls} aria-hidden="true" />
                        <span className="login-provider-name">{p.name}</span>
                        <span className={badgeCls}>{badgeLabel}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
              {flatVisible.length === 0 && <div className="muted login-empty">无匹配服务商</div>}
            </div>
          </div>

          <div key={current?.id ?? "empty"} className="login-detail-pane ui-fade-swap">
            {current && (
              <>
                <div className="login-detail-head">
                  <div>
                    <div className="login-detail-name">{current.name}</div>
                    <div className="muted login-detail-desc">{current.desc}</div>
                  </div>
                  <div className="login-detail-badges">
                    <span className="ui-badge" data-testid="login-detail-protocol">{current.api_protocol}</span>
                  </div>
                </div>

                {current.connected && (
                  <div className="ui-card login-connected-card" data-testid="login-connected-card">
                    <div className="login-connected-row">
                      <span className="ui-badge ui-badge--ok">当前已连接</span>
                      {current.masked_secret && <span className="login-secret-mask">{current.masked_secret}</span>}
                      {logoutArmed ? (
                        <>
                          <button
                            type="button"
                            className="ui-btn ui-btn--danger ui-btn--sm login-logout-btn"
                            data-testid="login-logout-confirm"
                            disabled={busy}
                            onClick={handleLogout}
                          >
                            确认退出
                          </button>
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--sm"
                            data-testid="login-logout-cancel"
                            disabled={busy}
                            onClick={() => setLogoutArmed(false)}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="ui-btn ui-btn--ghost ui-btn--danger-hover ui-btn--sm login-logout-btn"
                          data-testid="login-logout-btn"
                          disabled={busy}
                          onClick={() => setLogoutArmed(true)}
                        >
                          退出登录
                        </button>
                      )}
                    </div>
                    <div className="muted login-connected-meta">地址：{current.base_url}</div>
                    {current.models.length > 0 && (
                      <div className="muted login-connected-meta" data-testid="login-connected-models">
                        可用模型（前缀 {current.id}/）：{current.models.slice(0, 6).join("、")}
                        {current.models.length > 6 ? ` 等 ${current.models.length} 个` : ""}
                      </div>
                    )}
                  </div>
                )}

                {!current.connected && (current.env_configured || (current.env_vars?.length ?? 0) > 0) && (
                  <div className="ui-card login-env-card" data-testid="login-env-card">
                    {current.env_configured ? (
                      <>
                        <span className="ui-badge ui-badge--ok">环境变量已就绪</span>
                        <span>检测到 <code>{current.env_source}</code></span>
                        {current.masked_env_secret && <span className="login-secret-mask">{current.masked_env_secret}</span>}
                      </>
                    ) : (
                      <>
                        <span className="muted">支持环境变量：</span>
                        <div className="login-env-badges">
                          {(current.env_vars ?? []).map((ev) => (
                            <span key={ev} className="ui-badge">{ev}</span>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {current.auth_modes.length > 1 && (
                  <SegmentedBar activeKey={mode} className="login-mode-tabs" role="group" label="认证方式">
                    {current.auth_modes.map((m) => (
                      <button
                        key={m}
                        type="button"
                        aria-pressed={mode === m}
                        data-testid="login-mode-tab"
                        data-mode={m}
                        onClick={() => {
                          flowGenRef.current += 1;
                          statusGenRef.current += 1;
                          setBusy(false);
                          setMode(m);
                          setStarted(null);
                          setPollActive(false);
                          setMsg(null);
                        }}
                      >
                        {m === "oauth_pkce" && current.oauth_label ? current.oauth_label : MODE_LABELS[m]}
                      </button>
                    ))}
                  </SegmentedBar>
                )}

                {mode === "proxy" && (
                  <div className="login-form">
                    <label className="ui-field">
                      <span>代理地址（自动探针 GET /models 并同步能力表）</span>
                      <input
                        className="ui-input login-mono-input"
                        data-testid="login-baseurl-input"
                        value={baseUrl}
                        placeholder={current.default_base_url}
                        onChange={(e) => { statusGenRef.current += 1; setBaseUrl(e.target.value); }}
                        onKeyDown={(e) => {
                          if (isImeEnter(e) && !submitDisabled) {
                            e.preventDefault();
                            handleSubmit();
                          }
                        }}
                      />
                    </label>
                    <label className="ui-field">
                      <span>代理密钥（可选，本机无鉴权可留空）</span>
                      <input
                        type="password"
                        className="ui-input login-mono-input"
                        data-testid="login-key-input"
                        value={apiKey}
                        placeholder="sk-…（留空则无密钥直连）"
                        onChange={(e) => { statusGenRef.current += 1; setApiKey(e.target.value); }}
                        onKeyDown={(e) => {
                          if (isImeEnter(e) && !submitDisabled) {
                            e.preventDefault();
                            handleSubmit();
                          }
                        }}
                      />
                    </label>
                    <div className="login-actions">
                      <button
                        type="button"
                        className="ui-btn ui-btn--primary"
                        data-testid="login-submit-btn"
                        disabled={submitDisabled}
                        onClick={handleSubmit}
                      >
                        {busy ? "正在同步…" : "连接并同步模型"}
                      </button>
                    </div>
                  </div>
                )}

                {mode === "api_key" && (
                  <div className="login-form">
                    <label className="ui-field">
                      <span>{current.id === "amazon-bedrock" || current.id === "google-vertex" ? "API Key / Bearer Token（使用下方 IAM/ADC 时可留空）" : "API Key"}</span>
                      <input
                        type="password"
                        className="ui-input login-mono-input"
                        data-testid="login-key-input"
                        value={apiKey}
                        placeholder="粘贴服务商 API Key 或令牌…"
                        onChange={(e) => { statusGenRef.current += 1; setApiKey(e.target.value); }}
                        onKeyDown={(e) => {
                          if (isImeEnter(e) && !submitDisabled) {
                            e.preventDefault();
                            handleSubmit();
                          }
                        }}
                      />
                    </label>

                    <ExtraFieldsGrid
                      fields={extraFields}
                      values={extraValues}
                      showRequiredMark={true}
                      onChange={(key, value) => { statusGenRef.current += 1; setExtraValues((prev) => ({ ...prev, [key]: value })); }}
                      onEnter={() => {
                        if (!submitDisabled) handleSubmit();
                      }}
                    />

                    {current.supports_custom_base_url && (
                      <label className="ui-field">
                        <span>接口地址（可留默认，或填自建兼容网关）</span>
                        <input
                          className="ui-input login-mono-input"
                          data-testid="login-baseurl-input"
                          value={baseUrl}
                          placeholder={current.default_base_url}
                          onChange={(e) => { statusGenRef.current += 1; setBaseUrl(e.target.value); }}
                          onKeyDown={(e) => {
                            if (isImeEnter(e) && !submitDisabled) {
                              e.preventDefault();
                              handleSubmit();
                            }
                          }}
                        />
                      </label>
                    )}

                    <div className="login-actions">
                      <button
                        type="button"
                        className="ui-btn ui-btn--primary"
                        data-testid="login-submit-btn"
                        disabled={submitDisabled}
                        onClick={handleSubmit}
                      >
                        {busy ? "正在保存…" : "保存凭据并启用"}
                      </button>
                    </div>
                  </div>
                )}

                {mode === "oauth_pkce" && (
                  <div className="login-form">
                    <div className="login-oauth-step">
                      <span className="muted">第 1 步：生成浏览器授权链接，点击打开（或复制后手动打开）</span>
                      <div className="login-actions">
                        <button
                          type="button"
                          className="ui-btn"
                          data-testid="login-oauth-start-btn"
                          disabled={busy}
                          onClick={handleStartOAuth}
                        >
                          {started?.authorize_url ? "重新生成授权链接" : "生成授权链接"}
                        </button>
                      </div>
                      {started?.authorize_url && (
                        <div className="login-url-box ui-card-enter">
                          <code className="login-url-text" data-testid="login-authorize-url">{started.authorize_url}</code>
                          <button
                            type="button"
                            className="ui-btn ui-btn--sm"
                            data-testid="login-open-browser-btn"
                            onClick={() => openInBrowser(started.authorize_url ?? "")}
                          >
                            <Icon name="external-link" size="sm" />
                            在浏览器中打开
                          </button>
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--sm"
                            data-testid="login-copy-url-btn"
                            onClick={() => copyText("url", started.authorize_url ?? "")}
                          >
                            <Icon name={copiedKey === "url" ? "check" : "copy"} size="sm" />
                            {copiedKey === "url" ? "已复制" : "复制链接"}
                          </button>
                        </div>
                      )}
                    </div>
                    <label className="ui-field">
                      <span>第 2 步：粘贴浏览器回调链接（或 code#state）</span>
                      <input
                        className="ui-input login-mono-input"
                        data-testid="login-code-input"
                        value={authCode}
                        placeholder="http://127.0.0.1:1455/auth/callback?code=… 或 code#state"
                        onChange={(e) => { statusGenRef.current += 1; setAuthCode(e.target.value); }}
                        onKeyDown={(e) => {
                          if (isImeEnter(e) && !submitDisabled) {
                            e.preventDefault();
                            handleSubmit();
                          }
                        }}
                      />
                    </label>
                    {needsLoopbackNote && (
                      <div className="login-hint-note" data-testid="login-localhost-callback-note">
                        提示：授权后浏览器跳转至 <code>{started?.redirect_uri}</code> 显示「无法访问此网站 / 连接被拒绝」属正常现象（无需本地回调服务），请直接复制浏览器地址栏中的完整回调链接粘贴到上方输入框。
                      </div>
                    )}
                    <div className="login-actions">
                      <button
                        type="button"
                        className="ui-btn ui-btn--primary"
                        data-testid="login-submit-btn"
                        disabled={submitDisabled}
                        onClick={handleSubmit}
                      >
                        {busy ? "正在换取令牌…" : "完成 OAuth 登录"}
                      </button>
                    </div>
                  </div>
                )}

                {mode === "oauth_device" && (
                  <div className="login-form">
                    <ExtraFieldsGrid
                      fields={extraFields}
                      values={extraValues}
                      showRequiredMark={false}
                      onChange={(key, value) => { statusGenRef.current += 1; setExtraValues((prev) => ({ ...prev, [key]: value })); }}
                    />
                    <div className="login-oauth-step">
                      <span className="muted">第 1 步：获取 {current.name} 设备码，在浏览器输入设备码完成授权</span>
                      <div className="login-actions">
                        <button
                          type="button"
                          className="ui-btn"
                          data-testid="login-device-start-btn"
                          disabled={busy}
                          onClick={handleStartOAuth}
                        >
                          {started?.user_code ? "重新获取设备码" : `获取 ${current.name} 设备码`}
                        </button>
                      </div>
                      {started?.verification_uri && (
                        <div className="login-url-box ui-card-enter">
                          <code className="login-url-text" data-testid="login-device-url">{started.verification_uri}</code>
                          <button
                            type="button"
                            className="ui-btn ui-btn--sm"
                            data-testid="login-open-browser-btn"
                            onClick={() => openInBrowser(started.verification_uri ?? "")}
                          >
                            <Icon name="external-link" size="sm" />
                            在浏览器中打开
                          </button>
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--sm"
                            data-testid="login-copy-url-btn"
                            onClick={() => copyText("url", started.verification_uri ?? "")}
                          >
                            <Icon name={copiedKey === "url" ? "check" : "copy"} size="sm" />
                            {copiedKey === "url" ? "已复制" : "复制链接"}
                          </button>
                        </div>
                      )}
                      {started?.user_code && (
                        <div className="login-device-card ui-card-enter">
                          <span className="muted">一次性设备码：</span>
                          <strong className="login-device-code" data-testid="login-device-code">{started.user_code}</strong>
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--sm"
                            onClick={() => copyText("code", started.user_code ?? "")}
                          >
                            <Icon name={copiedKey === "code" ? "check" : "copy"} size="sm" />
                            {copiedKey === "code" ? "已复制" : "复制"}
                          </button>
                        </div>
                      )}
                      {started?.user_code && pollActive && (
                        <div className="login-poll-row" data-testid="login-device-polling">
                          <span className="ui-spinner" />
                          <span>正在自动检测浏览器授权状态，完成后将自动生效…</span>
                        </div>
                      )}
                    </div>
                    <div className="login-actions">
                      <button
                        type="button"
                        className="ui-btn ui-btn--primary"
                        data-testid="login-submit-btn"
                        disabled={busy || !started?.user_code}
                        onClick={handleSubmit}
                      >
                        {busy ? "正在检查…" : pollActive ? "立即检查" : "检查并完成授权"}
                      </button>
                    </div>
                  </div>
                )}

                {msg && (
                  <div
                    className={
                      msg.kind === "err"
                        ? "login-feedback login-feedback--err ui-error-shake"
                        : msg.kind === "warn"
                          ? "login-feedback login-feedback--warn ui-card-enter"
                          : "login-feedback login-feedback--ok ui-confirm-enter"
                    }
                    data-testid={msg.kind === "err" ? "login-error-msg" : "login-status-msg"}
                  >
                    {msg.text}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
    </FloatingModal>
  );
}
