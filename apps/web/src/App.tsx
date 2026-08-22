import "./App.css";
import { AvatarLabPage } from "./features/avatar-lab/AvatarLabPage";

const delivered = [
  "Monorepo 与三语言质量门",
  "Versioned domain protocol",
  "Deterministic JSON Schema",
  "TypeScript 类型与 Zod 校验",
  "Cross-language contract tests",
];

const deferred = [
  "Runtime / Pipecat / WebRTC",
  "Tauri sidecar",
  "Live2D Core 与 Avatar Lab",
  "模型 SDK 与模型权重",
  "SQLite 业务表与 Event Store",
];

function StatusList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: string;
}) {
  return (
    <section className={`status-card ${tone}`}>
      <h2>{title}</h2>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

export default function App() {
  if (window.location.pathname === "/avatar-lab") {
    return <AvatarLabPage />;
  }

  return (
    <main>
      <header className="hero">
        <p className="eyebrow">ChatWaifuV2 · repository foundation</p>
        <h1>ChatWaifu NEXT</h1>
        <p className="summary">
          当前是可验证的 Phase 0 + Phase 1
          工程底座，不是已经连接模型的产品演示。
        </p>
        <div className="phase-row" aria-label="Implementation progress">
          <span>Phase 0 · Foundation</span>
          <span>Phase 1 · Protocol</span>
        </div>
      </header>

      <div className="status-grid">
        <StatusList title="本轮交付" items={delivered} tone="ready" />
        <StatusList title="明确延后" items={deferred} tone="deferred" />
      </div>

      <section className="contract-note">
        <p>下一道阶段门</p>
        <strong>make generate-protocol &amp;&amp; make test-contract</strong>
        <a className="lab-link" href="/avatar-lab">
          Open Phase 2 Avatar Lab →
        </a>
      </section>
    </main>
  );
}
