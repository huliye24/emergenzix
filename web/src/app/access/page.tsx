"use client";

import { useState, type FormEvent } from "react";

export default function VisitorAccessPage() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "访问验证失败");
      const requested = new URLSearchParams(window.location.search).get("returnTo") ?? "/";
      window.location.replace(requested.startsWith("/") && !requested.startsWith("//") ? requested : "/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "访问验证失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="access-title">
        <div className="access-brand">
          <span className="brand-mark">E</span>
          <span className="brand-copy">
            <strong>Emergenzix</strong>
            <small>QUANTITATIVE WORKSPACE</small>
          </span>
        </div>
        <span className="access-kicker">PRIVATE RESEARCH ACCESS</span>
        <h1 id="access-title">输入访问者码</h1>
        <p>查看只读账户摘要、模型状态和 PAPER 研究结果。访问者不能创建订单或修改策略。</p>
        <form className="access-form" onSubmit={submit}>
          <label htmlFor="visitor-code">访问者码</label>
          <input
            id="visitor-code"
            type="password"
            autoComplete="one-time-code"
            maxLength={256}
            value={code}
            onChange={(event) => setCode(event.target.value)}
            required
            autoFocus
          />
          {error ? <div className="access-error" role="alert">{error}</div> : null}
          <button className="button button-primary button-wide" disabled={busy}>
            {busy ? "正在验证…" : "进入研究中心"}
          </button>
        </form>
        <small className="access-footnote">READ ONLY · PAPER RESEARCH · LIVE DISABLED</small>
      </section>
    </main>
  );
}
