// D170：AVA 独立 /login 多服务商认证弹窗（对齐 Pi /login 体验，零外部 CLI 前提）。
// 只调 auth.status / auth.start / auth.complete / auth.logout RPC（密钥永不进 spawn argv，通过 core stdin 写 config/auth.local.json）。
import { useEffect, useState } from "react";
import type {
  AuthCompleteResult,
  AuthMode,
  AuthProviderRow,
  AuthStartResult,
  AuthStatusResult,
} from "../shared/protocol";
import type { RpcClient } from "./rpc";
import { Icon } from "./icons";

const MODE_LABELS: Record<AuthMode, string> = {
  oauth_pkce: "OAuth 浏览器授权",
  oauth_device: "GitHub 设备码",
  proxy: "代理地址 + 密钥",
  api_key: "API Key",
};

const MODE_SHORT: Record<AuthMode, string> = {
  oauth_pkce: "OAuth",
  oauth_device: "Device",
  proxy: "Proxy",
  api_key: "Key",
};

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
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
  const [started, setStarted] = useState<AuthStartResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

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

  const syncProviderDefaults = (row: AuthProviderRow) => {
    setSelectedId(row.id);
    setMode(row.auth_modes[0] ?? "api_key");
    setBaseUrl(row.base_url || row.default_base_url);
    setApiKey("");
    setAuthCode("");
    setStarted(null);
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

  if (!open) return null;

  const providers = status?.providers ?? [];
  const q = query.trim().toLowerCase();
  const filtered = q
    ? providers.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q) ||
          p.desc.toLowerCase().includes(q),
      )
    : providers;
  const current = providers.find((p) => p.id === selectedId) ?? providers[0] ?? null;

  const selectProvider = (row: AuthProviderRow) => {
    setMsg(null);
    syncProviderDefaults(row);
  };

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
    const payloadObj: Record<string, string> = { mode };
    if (mode === "proxy") {
      payloadObj.base_url = baseUrl.trim() || current.default_base_url;
      if (apiKey.trim()) payloadObj.api_key = apiKey.trim();
    } else if (mode === "api_key") {
      payloadObj.api_key = apiKey.trim();
      if (baseUrl.trim()) payloadObj.base_url = baseUrl.trim();
    } else if (mode === "oauth_pkce") {
      payloadObj.code = authCode.trim();
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

  const submitDisabled =
    busy ||
    !current ||
    (mode === "api_key" && apiKey.trim() === "") ||
    (mode === "oauth_pkce" && authCode.trim() === "");

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
                placeholder="筛选服务商（10 家）…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="login-provider-list">
              {filtered.map((p) => {
                const isSelected = current?.id === p.id;
                const primaryMode = p.auth_modes[0] ?? "api_key";
                return (
                  <button
                    key={p.id}
                    type="button"
                    aria-current={isSelected ? "true" : undefined}
                    className={`ui-row login-provider-row${isSelected ? " is-selected" : ""}`}
                    data-testid="login-provider-row"
                    data-provider={p.id}
                    data-connected={p.connected ? "1" : "0"}
                    onClick={() => selectProvider(p)}
                  >
                    <span className={`ui-dot${p.connected ? " login-dot--ok" : ""}`} aria-hidden="true" />
                    <span className="login-provider-name">{p.name}</span>
                    <span className="ui-badge">{p.connected ? "已连接" : MODE_SHORT[primaryMode]}</span>
                  </button>
                );
              })}
              {filtered.length === 0 && <div className="muted login-empty">无匹配服务商</div>}
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
                  <span className="ui-badge" data-testid="login-detail-protocol">{current.api_protocol}</span>
                </div>

                {current.connected && (
                  <div className="login-connected-card" data-testid="login-connected-card">
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
                        {MODE_LABELS[m]}
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
                      <span>API Key</span>
                      <input
                        type="password"
                        className="ui-input login-mono-input"
                        data-testid="login-key-input"
                        value={apiKey}
                        placeholder="粘贴服务商 API Key…"
                        onChange={(e) => setApiKey(e.target.value)}
                      />
                    </label>
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
                            className="ui-btn ui-btn--primary ui-btn--sm"
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
                        placeholder="http://localhost:1455/auth/callback?code=… 或 code#state"
                        onChange={(e) => setAuthCode(e.target.value)}
                      />
                    </label>
                    {started?.redirect_uri?.includes("localhost:") && (
                      <div className="login-hint-note" data-testid="login-localhost-callback-note">
                        提示：授权后浏览器跳转至 <code>{started.redirect_uri}</code> 显示「无法访问此网站 / 连接被拒绝」属正常现象（无需本地回调服务），请直接复制浏览器地址栏中的完整回调链接粘贴到上方输入框。
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
                    <div className="login-oauth-step">
                      <span className="muted">第 1 步：获取 GitHub 设备码，复制链接到系统浏览器输入设备码完成授权</span>
                      <div className="login-actions">
                        <button
                          type="button"
                          className="ui-btn"
                          data-testid="login-device-start-btn"
                          disabled={busy}
                          onClick={handleStartOAuth}
                        >
                          {started?.user_code ? "重新获取设备码" : "获取 GitHub 设备码"}
                        </button>
                      </div>
                      {started?.verification_uri && (
                        <div className="login-url-box">
                          <code className="login-url-text" data-testid="login-device-url">{started.verification_uri}</code>
                          <button
                            type="button"
                            className="ui-btn ui-btn--primary ui-btn--sm"
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
