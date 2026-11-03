"use client";

import { useState, type FormEvent } from "react";

export default function OperatorAccessPage() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/operator", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const payload = await response.json().catch(() => ({})) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "操作员验证失败");
      window.location.replace("/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作员验证失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="operator-title">
        <div className="access-brand"><span className="brand-mark">E</span><span className="brand-copy"><strong>Emergenzix</strong><small>RESEARCH OPERATOR</small></span></div>
        <span className="access-kicker">BOUNDED RESEARCH ACCESS</span>
        <h1 id="operator-title">输入操作员码</h1>
        <p>仅授权历史回测写入。不能创建 PAPER 订单、修改策略或进行 LIVE 交易。</p>
        <form className="access-form" onSubmit={submit}>
          <label htmlFor="operator-code">操作员码</label>
          <input id="operator-code" type="password" autoComplete="current-password" maxLength={256} value={code} onChange={(event) => setCode(event.target.value)} required autoFocus />
          {error ? <div className="access-error" role="alert">{error}</div> : null}
          <button className="button button-primary button-wide" disabled={busy}>{busy ? "正在验证…" : "进入回测控制台"}</button>
        </form>
        <small className="access-footnote">BACKTEST ONLY · PAPER ORDERS DENIED · LIVE DISABLED</small>
      </section>
    </main>
  );
}
