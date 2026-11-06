#include "host.hpp"

namespace desktop_macos {

napi_value ConfigureSurfaces(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    if (!NSThread.isMainThread) throw std::runtime_error("桌面窗口只能在主线程配置。");
    size_t count = 2; napi_value args[2]; Check(napi_get_cb_info(env, info, &count, args, nullptr, nullptr));
    if (count != 2) throw std::runtime_error("桌面模式需要背景与交互窗口。");
    NSWindow *background = WindowForHandle(env, args[0]);
    NSWindow *overlay = WindowForHandle(env, args[1]);
    if (background == overlay) throw std::runtime_error("桌面背景和交互层不能使用同一窗口。");
    const NSWindowLevel level = CGWindowLevelForKey(kCGDesktopIconWindowLevelKey) - 1;
    background.level = level;
    background.ignoresMouseEvents = YES;
    background.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorIgnoresCycle;
    overlay.level = NSNormalWindowLevel;
    // 点击穿透到 Finder 时系统可能执行“显示桌面”，交互层应与背景一样留在桌面。
    overlay.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorIgnoresCycle;
    if (background.level != level || overlay.level != NSNormalWindowLevel)
      throw std::runtime_error("无法确认桌面窗口层级。");
    interactiveWindow = overlay;
    InstallSpaceObserver();
    return Undefined(env);
  });
}

napi_value SurfaceState(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    size_t count = 1; napi_value value; Check(napi_get_cb_info(env, info, &count, &value, nullptr, nullptr));
    if (count != 1) throw std::runtime_error("缺少交互屏范围。");
    Rect bounds = ReadRect(env, value);
    { std::lock_guard<std::mutex> lock(registryMutex); desktopBounds = bounds; }
    InstallSpaceObserver();
    NSWindow *overlay = interactiveWindow;
    NSRunningApplication *front = NSWorkspace.sharedWorkspace.frontmostApplication;
    const bool active = overlay && overlay.isOnActiveSpace && overlay.isKeyWindow && front.processIdentifier == getpid();
    bool fullscreen = false, hasFinderWindow = false;
    for (const QuartzWindow &window : QuartzWindows()) {
      if (window.pid == getpid() || !Intersects(window.bounds, bounds)) continue;
      if (window.pid == front.processIdentifier) {
        hasFinderWindow = true;
        fullscreen = window.bounds.x <= bounds.x + 2 && window.bounds.y <= bounds.y + 2 &&
                     window.bounds.x + window.bounds.width >= bounds.x + bounds.width - 2 &&
                     window.bounds.y + window.bounds.height >= bounds.y + bounds.height - 2;
        // Quartz 顺序由前至后，后面的背景窗口不能覆盖前台窗口判定。
        break;
      }
    }
    const bool finderDesktop = [front.bundleIdentifier isEqualToString:@"com.apple.finder"] && !hasFinderWindow;
    napi_value output = Object(env);
    Set(env, output, "active", Bool(env, active && !fullscreen));
    Set(env, output, "stageAvailable", Bool(env, (active || finderDesktop) && !fullscreen));
    Set(env, output, "fullscreen", Bool(env, fullscreen));
    Set(env, output, "spaceRevision", Number(env, spaceRevision.load()));
    return output;
  });
}

napi_value AccessibilityStatus(napi_env env, napi_callback_info) { return Export(env, [&] { return Bool(env, AXIsProcessTrusted()); }); }
napi_value RequestAccessibility(napi_env env, napi_callback_info) {
  return Export(env, [&] {
    NSDictionary *options = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
    AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options); return Undefined(env);
  });
}


}  // namespace desktop_macos
