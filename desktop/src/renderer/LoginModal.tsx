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
import { Icon } from "./icons";

const MODE_LABELS: Record<AuthMode, string> = {
  oauth_pkce: "OAuth 浏览器授权",
  oauth_device: "设备码授权",
  proxy: "代理地址 + 密钥",
  api_key: "API Key / 凭据",
};

const MODE_SHORT: Record<AuthMode, string> = {
  oauth_pkce: "OAuth",
  oauth_device: "Device",
  proxy: "Proxy",
  api_key: "Key",
};

const CATEGORY_ORDER: readonly { key: ProviderCategory; label: string }[] = [
  { key: "oauth", label: "OAuth 订阅" },
  { key: "flagship", label: "常用大模型" },
  { key: "cloud", label: "云平台与路由" },
  { key: "local", label: "本地 / 代理" },
];

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function initExtraValues(row: AuthProviderRow): Record<string, string> {
  const out: Record<string, string> = {};
  for (const ef of row.extra_fields ?? []) {
    const existing = !ef.secret ? row.extra_config?.[ef.key] : "";
    out[ef.key] = (existing || ef.default || "").trim();
  }
  return out;
}

function hasCloudAltCredentials(row: AuthProviderRow, extraValues: Record<string, string>): boolean {
  if (row.id === "amazon-bedrock") {
    const iam = Boolean(extraValues.aws_access_key_id?.trim() && extraValues.aws_secret_access_key?.trim());
    const prof = Boolean(extraValues.aws_profile?.trim());
    return iam || prof || Boolean(row.env_configured);
  }
  if (row.id === "google-vertex") {
    return Boolean(extraValues.project_id?.trim() && (extraValues.location?.trim() || "us-central1"));
  }
  return false;
}

export function LoginModal({
  open,
  rpc,
  initialStatus,
  onClose,
  onAuthChanged,
}: {
  open: boolean;
  rpc: RpcClient;
  initialStatus: AuthStatusResult | null;
  onClose: () => void;
  onAuthChanged: (status: AuthStatusResult) => void;
}) {
  const [status, setStatus] = useState<AuthStatusResult | null>(initialStatus);
  const [selectedId, setSelectedId] = useState<string>("cliproxyapi");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<AuthMode>("proxy");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [authCode, setAuthCode] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [started, setStarted] = useState<AuthStartResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const syncProviderDefaults = (row: AuthProviderRow) => {
    setSelectedId(row.id);
    setMode(row.auth_modes[0] ?? "api_key");
    setBaseUrl(row.base_url || row.default_base_url);
    setApiKey("");
    setAuthCode("");
    setExtraValues(initExtraValues(row));
    setStarted(null);
  };

  const loadStatus = (preferredId?: string) => {
    void rpc.call<AuthStatusResult>("auth.status", {}).then(
      (res) => {
        setStatus(res);
        onAuthChanged(res);
        const list = res.providers ?? [];
        const pick =
          (preferredId && list.find((p) => p.id === preferredId)) ||
          list.find((p) => p.id === selectedId) ||
          list.find((p) => p.connected) ||
          list[0];
        if (pick) syncProviderDefaults(pick);
      },
      (e) => setMsg({ kind: "err", text: errMsg(e) }),
    );
  };

  useEffect(() => {
    if (!open) return;
    setMsg(null);
    loadStatus(selectedId);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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

  if (!open) return null;

  const current = providers.find((p) => p.id === selectedId) ?? flatVisible[0] ?? providers[0] ?? null;

  const handleStartOAuth = () => {
    if (!current) return;
    setBusy(true);
    setMsg(null);
    void rpc
      .call<AuthStartResult>("auth.start", { provider: current.id, mode })
      .then(
        (res) => {
          setStarted(res);
          if (res.mode === "oauth_pkce" && res.authorize_url) {
            setMsg({ kind: "ok", text: "已生成授权链接：在浏览器完成登录后，复制回调链接或授权码粘贴到下方" });
          } else if (res.mode === "oauth_device" && res.user_code) {
            setMsg({ kind: "ok", text: `设备码 ${res.user_code} 已生成，请在浏览器授权后点击“检查并完成授权”` });
          }
        },
        (e) => setMsg({ kind: "err", text: errMsg(e) }),
      )
      .finally(() => setBusy(false));
  };

  const handleSubmit = () => {
    if (!current) return;
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
    void rpc
      .call<AuthCompleteResult>("auth.complete", {
        provider: current.id,
        payload: JSON.stringify(payloadObj),
      })
      .then(
        (res) => {
          if (res.pending) {
            setMsg({ kind: "warn", text: res.text || "等待用户在浏览器完成设备码授权…" });
            return;
          }
          setApiKey("");
          setAuthCode("");
          setStarted(null);
          setMsg({ kind: "ok", text: res.text || `已登录 ${current.name}` });
          loadStatus(current.id);
        },
        (e) => setMsg({ kind: "err", text: errMsg(e) }),
      )
      .finally(() => setBusy(false));
  };

  const handleLogout = () => {
    if (!current) return;
    setBusy(true);
    setMsg(null);
    void rpc
      .call<{ ok: boolean; text: string }>("auth.logout", { provider: current.id })
      .then(
        (res) => {
          setMsg({ kind: "ok", text: res.text });
          loadStatus(current.id);
        },
        (e) => setMsg({ kind: "err", text: errMsg(e) }),
      )
      .finally(() => setBusy(false));
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
    <div className="login-backdrop" data-testid="login-backdrop">
      <button
        type="button"
        tabIndex={-1}
        className="login-backdrop-btn"
        aria-label="关闭登录窗口"
        onClick={onClose}
      />
      <div
        className="login-modal"
        data-testid="login-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-modal-title"
      >
        <div className="login-modal-head">
          <div className="login-modal-title-wrap">
            <h2 id="login-modal-title" className="login-modal-title">
              模型服务商登录（/login）
            </h2>
            <span className="muted login-modal-sub">
              {status ? `已连接 ${status.connected_count} / ${providers.length} 家 · 独立存储于 config/auth.local.json (0600)` : "正在读取认证状态…"}
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
                placeholder={`筛选服务商（${providers.length || 44} 家，名/协议/Env）…`}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    stepSelection(1);
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    stepSelection(-1);
                  }
                }}
              />
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
                        ? "Env"
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

          <div className="login-detail-pane">
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
                      <button
                        type="button"
                        className="ui-btn ui-btn--ghost ui-btn--sm login-logout-btn"
                        data-testid="login-logout-btn"
                        disabled={busy}
                        onClick={handleLogout}
                      >
                        退出登录
                      </button>
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
                  <div className="ui-seg login-mode-tabs" role="tablist" aria-label="认证方式">
                    {current.auth_modes.map((m) => (
                      <button
                        key={m}
                        type="button"
                        role="tab"
                        aria-pressed={mode === m}
                        data-testid="login-mode-tab"
                        data-mode={m}
                        onClick={() => {
                          setMode(m);
                          setStarted(null);
                          setMsg(null);
                        }}
                      >
                        {m === "oauth_pkce" && current.oauth_label ? current.oauth_label : MODE_LABELS[m]}
                      </button>
                    ))}
                  </div>
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
                        onChange={(e) => setBaseUrl(e.target.value)}
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
                        onChange={(e) => setApiKey(e.target.value)}
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
                        onChange={(e) => setApiKey(e.target.value)}
                      />
                    </label>

                    {extraFields.length > 0 && (
                      <div className="login-extra-grid" data-testid="login-extra-fields">
                        {extraFields.map((ef) => (
                          <label key={ef.key} className="ui-field">
                            <span className="login-field-head">
                              <span>{ef.label}{ef.required ? " *" : ""}</span>
                              {ef.env_var && <span className="ui-badge">{ef.env_var}</span>}
                            </span>
                            <input
                              type={ef.secret ? "password" : "text"}
                              className="ui-input login-mono-input"
                              data-testid={`login-extra-${ef.key}`}
                              value={extraValues[ef.key] ?? ""}
                              placeholder={ef.placeholder || ef.default}
                              onChange={(e) =>
                                setExtraValues((prev) => ({ ...prev, [ef.key]: e.target.value }))
                              }
                            />
                            {ef.help && <span className="login-field-help">{ef.help}</span>}
                          </label>
                        ))}
                      </div>
                    )}

                    {current.supports_custom_base_url && (
                      <label className="ui-field">
                        <span>接口地址（可留默认，或填自建兼容网关）</span>
                        <input
                          className="ui-input login-mono-input"
                          data-testid="login-baseurl-input"
                          value={baseUrl}
                          placeholder={current.default_base_url}
                          onChange={(e) => setBaseUrl(e.target.value)}
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
                      <span className="muted">第 1 步：生成 PKCE S256 授权链接，复制并在系统浏览器中打开</span>
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
                        <div className="login-url-box">
                          <code className="login-url-text" data-testid="login-authorize-url">{started.authorize_url}</code>
                          <button
                            type="button"
                            className="ui-btn ui-btn--sm"
                            data-testid="login-copy-url-btn"
                            onClick={() => copyText("url", started.authorize_url ?? "")}
                          >
                            <Icon name="copy" size="sm" />
                            {copiedKey === "url" ? "已复制链接" : "复制链接后到浏览器打开"}
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
                        onChange={(e) => setAuthCode(e.target.value)}
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
                    {extraFields.length > 0 && (
                      <div className="login-extra-grid" data-testid="login-extra-fields">
                        {extraFields.map((ef) => (
                          <label key={ef.key} className="ui-field">
                            <span className="login-field-head">
                              <span>{ef.label}</span>
                            </span>
                            <input
                              type={ef.secret ? "password" : "text"}
                              className="ui-input login-mono-input"
                              data-testid={`login-extra-${ef.key}`}
                              value={extraValues[ef.key] ?? ""}
                              placeholder={ef.placeholder || ef.default}
                              onChange={(e) =>
                                setExtraValues((prev) => ({ ...prev, [ef.key]: e.target.value }))
                              }
                            />
                            {ef.help && <span className="login-field-help">{ef.help}</span>}
                          </label>
                        ))}
                      </div>
                    )}
                    <div className="login-oauth-step">
                      <span className="muted">第 1 步：获取 {current.name} 设备码，复制链接到系统浏览器输入设备码完成授权</span>
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
                        <div className="login-url-box">
                          <code className="login-url-text" data-testid="login-device-url">{started.verification_uri}</code>
                          <button
                            type="button"
                            className="ui-btn ui-btn--sm"
                            data-testid="login-copy-url-btn"
                            onClick={() => copyText("url", started.verification_uri ?? "")}
                          >
                            <Icon name="copy" size="sm" />
                            {copiedKey === "url" ? "已复制链接" : "复制链接后到浏览器打开"}
                          </button>
                        </div>
                      )}
                      {started?.user_code && (
                        <div className="login-device-card">
                          <span className="muted">一次性设备码：</span>
                          <strong className="login-device-code" data-testid="login-device-code">{started.user_code}</strong>
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--sm"
                            onClick={() => copyText("code", started.user_code ?? "")}
                          >
                            <Icon name="copy" size="sm" />
                            {copiedKey === "code" ? "已复制" : "复制"}
                          </button>
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
                        {busy ? "正在检查…" : "检查并完成授权"}
                      </button>
                    </div>
                  </div>
                )}

                {msg && (
                  <div
                    className={msg.kind === "err" ? "login-feedback login-feedback--err" : msg.kind === "warn" ? "login-feedback login-feedback--warn" : "login-feedback login-feedback--ok"}
                    data-testid={msg.kind === "err" ? "login-error-msg" : "login-status-msg"}
                  >
                    {msg.text}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
