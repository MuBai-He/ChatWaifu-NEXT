import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsGroup, SettingsToggle } from "./SettingsPrimitives";

export function AppearanceSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const { appearance, canvasRef, desktop } = context;
  const disabled = desktop.loading || desktop.saving;
  const attribution = appearance.avatarManifest.attribution;
  return (
    <>
      <section className="desktop-settings-preview-card">
        <div className="desktop-settings-avatar-preview">
          <canvas key={appearance.rendererKind} ref={canvasRef} />
        </div>
        <div>
          <small>CURRENT CHARACTER</small>
          <h2>{appearance.character?.display_name ?? "绫地宁宁"}</h2>
          <p>
            {appearance.rendererKind === "live2d" ? "Live2D" : "安全回退"} ·{" "}
            {appearance.snapshot?.status ?? "loading"}
          </p>
          <span>拖动宁宁角色本体可移动桌宠，拖动窗口边缘可调整大小。</span>
          {attribution ? (
            <dl
              className="desktop-settings-avatar-attribution"
              aria-label="Live2D 模型与署名"
              role="group"
            >
              <div>
                <dt>模型作者</dt>
                <dd>{attribution.modelAuthor ?? "未随本地资产提供"}</dd>
              </div>
              <div>
                <dt>模型来源</dt>
                <dd>
                  {attribution.sourceUrl ? (
                    <a
                      href={attribution.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {attribution.sourceLabel}
                    </a>
                  ) : (
                    attribution.sourceLabel
                  )}
                </dd>
              </div>
            </dl>
          ) : null}
        </div>
      </section>

      {!desktop.desktopHost ? (
        <p className="desktop-settings-preview-note">
          当前是浏览器预览；窗口置顶、显示和透明区域穿透会在桌面版中生效。
        </p>
      ) : null}

      <SettingsGroup title="窗口" description="控制桌宠在桌面上的行为">
        <SettingsToggle
          label="显示桌宠"
          description="隐藏后仍可从托盘或这里重新显示"
          checked={desktop.preferences.overlayVisible}
          disabled={disabled}
          onChange={desktop.setOverlayVisible}
        />
        <SettingsToggle
          label="始终置顶"
          description="让宁宁保持在其他窗口上方"
          checked={desktop.preferences.alwaysOnTop}
          disabled={disabled}
          onChange={desktop.setAlwaysOnTop}
        />
        <SettingsToggle
          label="透明区域穿透"
          description="开启后角色、字幕和控件仍可点击，空白区域会点击到下方窗口"
          checked={desktop.preferences.clickThrough}
          disabled={disabled}
          onChange={desktop.setClickThrough}
        />
      </SettingsGroup>

      <SettingsGroup title="画面" description="只改变显示，不影响语音和动作">
        <SettingsToggle
          label="显示字幕"
          description="显示宁宁当前正在说的话"
          checked={desktop.preferences.showSubtitles}
          disabled={disabled}
          onChange={(enabled) => desktop.setDisplay({ showSubtitles: enabled })}
        />
      </SettingsGroup>
    </>
  );
}
