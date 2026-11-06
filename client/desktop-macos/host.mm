#include "host.hpp"

namespace desktop_macos {

std::atomic<uint64_t> generation{1};
std::atomic<uint64_t> spaceRevision{1};
std::atomic<uint64_t> operationRevision{1};
std::atomic<uint64_t> nextIdentity{1};
std::mutex registryMutex;
std::mutex accessibilityMutex;
std::unordered_map<std::string, std::shared_ptr<Window>> registry;
id spaceObserver = nil;
NSWindow *__weak interactiveWindow = nil;
Rect desktopBounds{};

std::string Utf8(NSString *value) { return value ? std::string(value.UTF8String ?: "") : ""; }
NSString *String(const std::string &value) { return [NSString stringWithUTF8String:value.c_str()]; }

void Check(napi_status result) {
  if (result != napi_ok) throw std::runtime_error("macOS 桌面组件收到无效参数。");
}

napi_value Undefined(napi_env env) { napi_value value; Check(napi_get_undefined(env, &value)); return value; }
napi_value Bool(napi_env env, bool value) { napi_value output; Check(napi_get_boolean(env, value, &output)); return output; }
napi_value Number(napi_env env, double value) { napi_value output; Check(napi_create_double(env, value, &output)); return output; }
napi_value Text(napi_env env, const std::string &value) {
  napi_value output; Check(napi_create_string_utf8(env, value.c_str(), value.size(), &output)); return output;
}
napi_value Null(napi_env env) { napi_value output; Check(napi_get_null(env, &output)); return output; }
void Set(napi_env env, napi_value object, const char *key, napi_value value) { Check(napi_set_named_property(env, object, key, value)); }
napi_value Object(napi_env env) { napi_value output; Check(napi_create_object(env, &output)); return output; }

std::string ReadString(napi_env env, napi_value value) {
  size_t length; Check(napi_get_value_string_utf8(env, value, nullptr, 0, &length));
  if (length > 32768) throw std::runtime_error("macOS 桌面参数过长。");
  std::vector<char> buffer(length + 1); Check(napi_get_value_string_utf8(env, value, buffer.data(), buffer.size(), &length));
  return std::string(buffer.data(), length);
}

double ReadNumber(napi_env env, napi_value object, const char *key) {
  napi_value value; Check(napi_get_named_property(env, object, key, &value));
  double output; Check(napi_get_value_double(env, value, &output));
  if (!std::isfinite(output) || std::abs(output) > 1000000) throw std::runtime_error("macOS 屏幕坐标无效。");
  return output;
}

Rect ReadRect(napi_env env, napi_value value) {
  Rect output{ReadNumber(env, value, "x"), ReadNumber(env, value, "y"), ReadNumber(env, value, "width"), ReadNumber(env, value, "height")};
  if (output.width <= 0 || output.height <= 0 || output.width > 65536 || output.height > 65536)
    throw std::runtime_error("macOS 屏幕范围无效。");
  return output;
}

std::vector<std::string> ReadStrings(napi_env env, napi_value value, uint32_t maximum) {
  bool array; Check(napi_is_array(env, value, &array));
  uint32_t count;
  if (!array) throw std::runtime_error("macOS 桌面组件需要路径或窗口 ID 列表。");
  Check(napi_get_array_length(env, value, &count));
  if (count > maximum) throw std::runtime_error("macOS 桌面请求数量过多。");
  std::vector<std::string> output;
  for (uint32_t index = 0; index < count; ++index) {
    napi_value item; Check(napi_get_element(env, value, index, &item)); output.push_back(ReadString(env, item));
  }
  return output;
}

uint64_t ProcessStarted(pid_t pid) {
  proc_bsdinfo info{};
  if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) return 0;
  return static_cast<uint64_t>(info.pbi_start_tvsec) * 1000000 + info.pbi_start_tvusec;
}

std::string CanonicalPath(NSURL *url) { return Utf8(url.URLByResolvingSymlinksInPath.URLByStandardizingPath.path); }

std::vector<Application> Applications() {
  std::vector<Application> output;
  for (NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications) {
    if (app.processIdentifier == getpid() || app.activationPolicy != NSApplicationActivationPolicyRegular || !app.bundleURL) continue;
    std::string path = CanonicalPath(app.bundleURL);
    if (path.empty()) continue;
    output.push_back({app.processIdentifier, path, Utf8(app.localizedName), ProcessStarted(app.processIdentifier)});
  }
  return output;
}

bool Intersects(Rect first, Rect second) {
  return std::min(first.x + first.width, second.x + second.width) > std::max(first.x, second.x) &&
         std::min(first.y + first.height, second.y + second.height) > std::max(first.y, second.y);
}

double Intersection(Rect first, Rect second) {
  return std::max(0.0, std::min(first.x + first.width, second.x + second.width) - std::max(first.x, second.x)) *
         std::max(0.0, std::min(first.y + first.height, second.y + second.height) - std::max(first.y, second.y));
}

bool OnDisplay(Rect window, Rect target) {
  const double targetArea = Intersection(window, target);
  if (targetArea <= 0) return false;
  CGDirectDisplayID ids[64]; uint32_t count = 0;
  if (CGGetActiveDisplayList(64, ids, &count) != kCGErrorSuccess) throw std::runtime_error("无法确认窗口所属显示器。");
  for (uint32_t index = 0; index < count; ++index) {
    CGRect bounds = CGDisplayBounds(ids[index]);
    if (Intersection(window, {bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height}) > targetArea) return false;
  }
  return true;
}

std::vector<QuartzWindow> QuartzWindows() {
  CFArrayRef values = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID);
  if (!values) throw std::runtime_error("无法读取当前空间的窗口列表。");
  NSArray *items = CFBridgingRelease(values);
  std::vector<QuartzWindow> output;
  for (NSDictionary *item in items) {
    if ([item[(id)kCGWindowLayer] integerValue] != 0 || [item[(id)kCGWindowAlpha] doubleValue] <= 0) continue;
    CGRect bounds;
    if (!CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)item[(id)kCGWindowBounds], &bounds)) continue;
    if (bounds.size.width <= 1 || bounds.size.height <= 1) continue;
    output.push_back({[item[(id)kCGWindowOwnerPID] intValue], [item[(id)kCGWindowNumber] unsignedIntValue],
                      {bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height}, Utf8(item[(id)kCGWindowName])});
  }
  return output;
}

void InstallSpaceObserver() {
  if (spaceObserver) return;
  spaceObserver = [NSWorkspace.sharedWorkspace.notificationCenter
      addObserverForName:NSWorkspaceActiveSpaceDidChangeNotification object:nil queue:NSOperationQueue.mainQueue
      usingBlock:^(NSNotification *) {
        spaceRevision.fetch_add(1);
        operationRevision.fetch_add(1);
        std::lock_guard<std::mutex> lock(registryMutex);
        registry.clear();
      }];
}

NSWindow *WindowForHandle(napi_env env, napi_value value) {
  bool buffer; Check(napi_is_buffer(env, value, &buffer));
  if (!buffer) throw std::runtime_error("桌面表面缺少原生窗口句柄。");
  void *data; size_t length; Check(napi_get_buffer_info(env, value, &data, &length));
  if (length != sizeof(void *)) throw std::runtime_error("桌面窗口句柄长度无效。");
  void *pointer = nullptr; memcpy(&pointer, data, sizeof(pointer));
  if (!pointer) throw std::runtime_error("桌面窗口句柄为空。");
  // 先在本进程 NSView 树中比对地址；不解引用来自 JS 的任意指针。
  for (NSWindow *window in NSApp.windows) {
    NSMutableArray<NSView *> *pending = [NSMutableArray array];
    if (window.contentView) [pending addObject:window.contentView];
    NSUInteger visited = 0;
    while (pending.count && ++visited <= 10000) {
      NSView *view = pending.lastObject; [pending removeLastObject];
      if ((__bridge void *)view == pointer) return window;
      [pending addObjectsFromArray:view.subviews];
    }
  }
  throw std::runtime_error("桌面窗口不属于当前客户端或已经关闭。");
}

void Job::Current() const {
  if (epoch != generation.load() || (spaceBound && space != spaceRevision.load())) throw std::runtime_error("桌面或空间已改变，请重新操作。");
  if (mutating && operation != operationRevision.load()) throw std::runtime_error("用户已继续操作，已取消后续窗口调整。");
  // NSRunningApplication 属性由系统按主 run loop 更新，SDK 明确允许后台线程读取。
  if (mutating) {
    bool allowed = caller.isActive || targetApplication.isActive;
    for (NSRunningApplication *application in allowedApplications) allowed = allowed || application.isActive;
    if (!allowed) throw std::runtime_error("用户已切换到其他应用，已取消后续窗口调整。");
  }
  if (std::chrono::steady_clock::now() >= deadline) throw std::runtime_error("应用响应超时；已提交的系统操作可能仍会完成，请检查窗口状态。");
}

void Trusted() {
  if (!AXIsProcessTrusted()) throw std::runtime_error("需要在系统设置的隐私与安全性中授予辅助功能权限。");
}

std::string AXFailure(AXError error, const char *action) {
  if (error == kAXErrorCannotComplete) return std::string(action) + "：应用未及时响应，操作结果未知，请检查窗口状态。";
  return std::string(action) + "失败（辅助功能错误 " + std::to_string(error) + "）。";
}

CFTypeRef CopyAttribute(Job &job, AXUIElementRef element, CFStringRef key, CFValue &output, bool optional = false) {
  job.Current();
  AXError error = AXUIElementCopyAttributeValue(element, key, &output.value);
  if (error == kAXErrorSuccess) return output.value;
  if (optional && (error == kAXErrorAttributeUnsupported || error == kAXErrorNoValue || error == kAXErrorInvalidUIElement)) return nullptr;
  throw std::runtime_error(AXFailure(error, "读取窗口"));
}

std::string AXText(Job &job, AXUIElementRef element, CFStringRef key, bool optional = true) {
  CFValue value; CopyAttribute(job, element, key, value, optional);
  return value.value && CFGetTypeID(value.value) == CFStringGetTypeID() ? Utf8((__bridge NSString *)value.value) : "";
}

bool AXBool(Job &job, AXUIElementRef element, CFStringRef key, bool fallback = false) {
  CFValue value; CopyAttribute(job, element, key, value, true);
  return value.value && CFGetTypeID(value.value) == CFBooleanGetTypeID() ? CFBooleanGetValue((CFBooleanRef)value.value) : fallback;
}

void SetAXTimeout(AXUIElementRef element) {
  const AXError error = AXUIElementSetMessagingTimeout(element, 0.25);
  if (error != kAXErrorSuccess) throw std::runtime_error(AXFailure(error, "设置窗口响应预算"));
}

Rect AXBounds(Job &job, AXUIElementRef element) {
  CFValue position, size;
  CopyAttribute(job, element, kAXPositionAttribute, position);
  CopyAttribute(job, element, kAXSizeAttribute, size);
  CGPoint point; CGSize dimensions;
  if (CFGetTypeID(position.value) != AXValueGetTypeID() || CFGetTypeID(size.value) != AXValueGetTypeID() ||
      !AXValueGetValue((AXValueRef)position.value, kAXValueTypeCGPoint, &point) ||
      !AXValueGetValue((AXValueRef)size.value, kAXValueTypeCGSize, &dimensions))
    throw std::runtime_error("应用返回了无效的窗口坐标。");
  if (!std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(dimensions.width) || !std::isfinite(dimensions.height) || dimensions.width <= 0 || dimensions.height <= 0)
    throw std::runtime_error("应用返回了无效的窗口大小。");
  return {point.x, point.y, dimensions.width, dimensions.height};
}

void SetAttribute(Job &job, AXUIElementRef element, CFStringRef attribute, CFTypeRef value, const char *action) {
  job.Current();
  const AXError error = AXUIElementSetAttributeValue(element, attribute, value);
  if (error != kAXErrorSuccess) throw std::runtime_error(AXFailure(error, action));
}

void Validate(Job &job, const std::shared_ptr<Window> &window) {
  job.Current();
  pid_t pid = 0;
  if (AXUIElementGetPid(window->element, &pid) != kAXErrorSuccess || pid != window->application.pid ||
      ProcessStarted(pid) != window->application.started || AXText(job, window->element, kAXRoleAttribute, false) != "AXWindow")
    throw std::runtime_error("窗口已关闭或所属应用已经改变。");
}

void ValidateOnDisplay(Job &job, const std::shared_ptr<Window> &window) {
  Validate(job, window);
  Rect target;
  { std::lock_guard<std::mutex> lock(registryMutex); target = desktopBounds; }
  if (target.width <= 0 || !OnDisplay(AXBounds(job, window->element), target))
    throw std::runtime_error("窗口已离开交互屏，请重新选择。");
}

void NoModal(Job &job, const std::shared_ptr<Window> &window) {
  CFValue children; CopyAttribute(job, window->element, kAXChildrenAttribute, children, true);
  if (children.value && CFGetTypeID(children.value) == CFArrayGetTypeID()) {
    for (CFIndex index = 0; index < CFArrayGetCount((CFArrayRef)children.value); ++index) {
      CFTypeRef child = CFArrayGetValueAtIndex((CFArrayRef)children.value, index);
      if (CFGetTypeID(child) == AXUIElementGetTypeID()) {
        SetAXTimeout((AXUIElementRef)child);
        if (AXText(job, (AXUIElementRef)child, kAXRoleAttribute) == "AXSheet")
          throw std::runtime_error("应用存在未决对话框，请先处理后再操作窗口。");
      }
    }
  }
  AXUIElementRef app = AXUIElementCreateApplication(window->application.pid);
  try { SetAXTimeout(app); } catch (...) { CFRelease(app); throw; }
  CFValue focused;
  try {
    CopyAttribute(job, app, kAXFocusedWindowAttribute, focused, true);
    if (focused.value && CFGetTypeID(focused.value) == AXUIElementGetTypeID()) {
      SetAXTimeout((AXUIElementRef)focused.value);
      if (AXBool(job, (AXUIElementRef)focused.value, kAXModalAttribute))
        throw std::runtime_error("应用存在未决模态对话框，请先处理后再操作窗口。");
    }
  } catch (...) { CFRelease(app); throw; }
  CFRelease(app);
}

std::shared_ptr<Window> ResolveWindow(const std::string &id) {
  std::lock_guard<std::mutex> lock(registryMutex);
  auto item = registry.find(id);
  if (item == registry.end()) throw std::runtime_error("窗口已关闭或列表已更新，请重新选择。");
  return item->second;
}

void ResetState() {
  generation.fetch_add(1);
  { std::lock_guard<std::mutex> lock(registryMutex); registry.clear(); desktopBounds = {}; }
  interactiveWindow = nil;
  if (spaceObserver) { [NSWorkspace.sharedWorkspace.notificationCenter removeObserver:spaceObserver]; spaceObserver = nil; }
}

napi_value Reset(napi_env env, napi_callback_info) { return Export(env, [&] { ResetState(); return Undefined(env); }); }
napi_value CancelOperations(napi_env env, napi_callback_info) {
  return Export(env, [&] { operationRevision.fetch_add(1); return Undefined(env); });
}


}  // namespace desktop_macos
