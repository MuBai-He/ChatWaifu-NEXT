import { useEffect, useState } from "react";

import {
  ProductIcon,
  type ProductIconName,
} from "../../components/ProductIcon";
import type { DesktopSettingsSectionId } from "./desktopSettingsRegistry";

interface OnboardingStep {
  eyebrow: string;
  title: string;
  description: string;
  icon: ProductIconName;
  points: string[];
  action?: {
    label: string;
    section: DesktopSettingsSectionId;
  };
}

const steps: OnboardingStep[] = [
  {
    eyebrow: "WELCOME",
    title: "欢迎来到 ChatWaifu NEXT",
    description: "先用几分钟把聊天、角色声音和语音输入接好。",
    icon: "pet",
    points: [
      "基础安装程序包含桌面端和本地 Runtime，不包含 CUDA、PyTorch 或大型模型。",
      "本地 TTS / Whisper 通过独立 .cwpack 安装；重装应用会保留这些用户数据。",
      "你可以混用云端 API 与本地 Worker Pack，不需要一次配置所有能力。",
    ],
  },
  {
    eyebrow: "CHAT API",
    title: "先让宁宁会聊天",
    description: "配置兼容的聊天 API、模型名称和密钥，然后执行连接测试。",
    icon: "models",
    points: [
      "“聊天”是必需路由；记忆提取、总结和向量模型可以稍后独立配置。",
      "API Key 交给本机 Runtime 管理，设置接口不会把密钥内容回显到界面。",
      "本地 OpenAI 兼容服务也可以填写环回地址并按相同方式测试。",
    ],
    action: { label: "打开模型设置", section: "models" },
  },
  {
    eyebrow: "CHARACTER VOICE",
    title: "选择角色声音",
    description: "选择已经安装的本地音色，或配置一个云端 TTS Provider。",
    icon: "voice",
    points: [
      "Qwen3-TTS 只有在对应 .cwpack 已安装时才会出现，不属于基础 EXE。",
      "云端声音需要填写各 Provider 要求的凭据，并可在保存前单独测试。",
      "本地模型按需加载；首次合成通常比后续合成更慢。",
    ],
    action: { label: "打开声音设置", section: "voice" },
  },
  {
    eyebrow: "MICROPHONE & STT",
    title: "连接麦克风与语音识别",
    description: "回到桌宠点击麦克风按钮，系统询问时允许权限。",
    icon: "microphone",
    points: [
      "本地转写需要安装 faster-whisper .cwpack；未安装时仍可使用文字聊天。",
      "建议先用“按住说话”；开放麦克风模式会由 VAD 自动判断一句话结束。",
      "麦克风只由桌宠窗口持有，避免设置窗口建立第二条播放或采集链路。",
    ],
    action: { label: "查看唤醒与 VAD 设置", section: "companion" },
  },
  {
    eyebrow: "READY",
    title: "配置完成后这样检查",
    description: "分别发送一条文字、试听一次声音，再说一句话确认转写。",
    icon: "check",
    points: [
      "文字能回复：聊天模型与 API 已连通。",
      "中文 / 日文能正常播放：TTS Provider 与音色可用。",
      "松开按键或 VAD 结束后出现转写：麦克风与 STT 可用。",
      "以后可在“数据”页手动执行 Worker Pack 完整性校验。",
    ],
  },
];

export function DesktopOnboardingDialog({
  open,
  onDefer,
  onComplete,
  onNavigate,
}: {
  open: boolean;
  onDefer: () => void;
  onComplete: () => void;
  onNavigate: (section: DesktopSettingsSectionId) => void;
}) {
  const [index, setIndex] = useState(0);
  const defer = () => {
    setIndex(0);
    onDefer();
  };
  const complete = () => {
    setIndex(0);
    onComplete();
  };
  const navigate = (section: DesktopSettingsSectionId) => {
    setIndex(0);
    onNavigate(section);
  };

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIndex(0);
        onDefer();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onDefer, open]);

  if (!open) return null;
  const step = steps[index];
  if (!step) return null;
  const finalStep = index === steps.length - 1;

  return (
    <div className="desktop-onboarding-backdrop" role="presentation">
      <section
        className="desktop-onboarding-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="desktop-onboarding-title"
      >
        <header>
          <span className="desktop-onboarding-icon">
            <ProductIcon name={step.icon} />
          </span>
          <div>
            <small>{step.eyebrow}</small>
            <h2 id="desktop-onboarding-title">{step.title}</h2>
          </div>
          <button type="button" onClick={defer} aria-label="稍后继续新手引导">
            <ProductIcon name="close" />
          </button>
        </header>

        <div className="desktop-onboarding-body">
          <p>{step.description}</p>
          <ul>
            {step.points.map((point) => (
              <li key={point}>
                <ProductIcon name="check" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
          {step.action ? (
            <button
              className="desktop-onboarding-action"
              type="button"
              onClick={() => navigate(step.action!.section)}
            >
              {step.action.label}
            </button>
          ) : null}
        </div>

        <footer>
          <button type="button" className="quiet" onClick={defer}>
            以后再说
          </button>
          <ol aria-label="引导进度">
            {steps.map((item, stepIndex) => (
              <li
                key={item.eyebrow}
                className={stepIndex === index ? "active" : ""}
                aria-current={stepIndex === index ? "step" : undefined}
              />
            ))}
          </ol>
          <div>
            {index > 0 ? (
              <button type="button" onClick={() => setIndex(index - 1)}>
                上一步
              </button>
            ) : null}
            <button
              type="button"
              className="primary"
              onClick={() => (finalStep ? complete() : setIndex(index + 1))}
            >
              {finalStep ? "完成引导" : "下一步"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
