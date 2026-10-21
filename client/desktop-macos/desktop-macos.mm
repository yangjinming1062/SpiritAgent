#include <node_api.h>

#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreGraphics/CoreGraphics.h>

#include <libproc.h>
#include <sys/proc_info.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

struct Rect { double x, y, width, height; };
struct Application { pid_t pid; std::string path, name; uint64_t started; };
struct QuartzWindow { pid_t pid; uint32_t number; Rect bounds; std::string title; };

struct Window {
  AXUIElementRef element;
  Application application;
  uint32_t number;
  Rect bounds;
  std::string id;
  std::atomic<int64_t> lastActive;
  Window(AXUIElementRef value, const Application &app, uint32_t num, Rect rect, std::string key)
      : element((AXUIElementRef)CFRetain(value)), application(app), number(num), bounds(rect),
        id(std::move(key)), lastActive(0) {}
  ~Window() { CFRelease(element); }
};

struct WindowProjection {
  std::shared_ptr<Window> window;
  std::string title;
  bool minimized, onDisplay;
};
struct AppInfo { std::string path, name, bundleId; };

std::atomic<uint64_t> generation{1};
std::atomic<uint64_t> spaceRevision{1};
std::atomic<uint64_t> operationRevision{1};
std::atomic<uint64_t> nextIdentity{1};
std::mutex registryMutex;
std::mutex accessibilityMutex;
std::unordered_map<std::string, std::shared_ptr<Window>> registry;
id spaceObserver = nil;
__weak NSWindow *interactiveWindow = nil;
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

template <typename F> napi_value Export(napi_env env, F action) {
  @autoreleasepool {
    try { return action(); }
    catch (const std::exception &error) { napi_throw_error(env, nullptr, error.what()); return nullptr; }
  }
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

struct Job {
  napi_async_work work = nullptr;
  napi_deferred deferred;
  uint64_t epoch = generation.load(), space = spaceRevision.load();
  uint64_t operation = operationRevision.load();
  std::chrono::steady_clock::time_point deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
  std::string error;
  std::function<void(Job &)> execute;
  std::function<napi_value(napi_env, Job &)> complete;
  std::vector<WindowProjection> windows;
  std::vector<std::shared_ptr<Window>> targets;
  std::vector<Application> applications;
  std::vector<std::string> paths;
  std::vector<AppInfo> information;
  __strong NSRunningApplication *caller = nil;
  __strong NSRunningApplication *targetApplication = nil;
  __strong NSArray<NSRunningApplication *> *allowedApplications = nil;
  pid_t frontPid = 0;
  Rect bounds{};
  bool move = false;
  bool mutating = false;
  bool serialAX = true;
  bool includeIcon = false;
  bool spaceBound = true;

  void Current() const {
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
};

napi_value Queue(napi_env env, std::unique_ptr<Job> job, const char *name) {
  napi_value promise; Check(napi_create_promise(env, &job->deferred, &promise));
  napi_value resource = Text(env, name);
  Check(napi_create_async_work(env, nullptr, resource, [](napi_env, void *data) {
    Job &job = *static_cast<Job *>(data);
    @autoreleasepool {
      try {
        std::unique_lock<std::mutex> lock(accessibilityMutex, std::defer_lock);
        if (job.serialAX) lock.lock();
        job.Current(); job.execute(job); job.Current();
      }
      catch (const std::exception &error) { job.error = error.what(); }
    }
  }, [](napi_env env, napi_status status, void *data) {
    std::unique_ptr<Job> job(static_cast<Job *>(data));
    @autoreleasepool {
      try {
        if (status != napi_ok) throw std::runtime_error("macOS 窗口请求已取消。");
        if (!job->error.empty()) throw std::runtime_error(job->error);
        job->Current();
        napi_value result = job->complete ? job->complete(env, *job) : Undefined(env);
        Check(napi_resolve_deferred(env, job->deferred, result));
      } catch (const std::exception &error) {
        napi_value reason; napi_create_error(env, nullptr, Text(env, error.what()), &reason);
        napi_reject_deferred(env, job->deferred, reason);
      }
      napi_delete_async_work(env, job->work);
    }
  }, job.get(), &job->work));
  const napi_status queued = napi_queue_async_work(env, job->work);
  if (queued != napi_ok) { napi_delete_async_work(env, job->work); Check(queued); }
  job.release();
  return promise;
}

void Trusted() {
  if (!AXIsProcessTrusted()) throw std::runtime_error("需要在系统设置的隐私与安全性中授予辅助功能权限。");
}

std::string AXFailure(AXError error, const char *action) {
  if (error == kAXErrorCannotComplete) return std::string(action) + "：应用未及时响应，操作结果未知，请检查窗口状态。";
  return std::string(action) + "失败（辅助功能错误 " + std::to_string(error) + "）。";
}

class CFValue {
 public:
  CFTypeRef value = nullptr;
  ~CFValue() { if (value) CFRelease(value); }
};

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

napi_value ListWindows(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    Trusted();
    size_t count = 1; napi_value value; Check(napi_get_cb_info(env, info, &count, &value, nullptr, nullptr));
    if (count != 1) throw std::runtime_error("缺少交互屏范围。");
    auto job = std::make_unique<Job>(); job->bounds = ReadRect(env, value); job->applications = Applications();
    job->frontPid = NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier;
    { std::lock_guard<std::mutex> lock(registryMutex); desktopBounds = job->bounds; }
    job->execute = [](Job &job) {
      const auto quartz = QuartzWindows();
      std::vector<std::shared_ptr<Window>> previous;
      { std::lock_guard<std::mutex> lock(registryMutex); for (const auto &item : registry) previous.push_back(item.second); }
      std::unordered_map<std::string, std::shared_ptr<Window>> next;
      const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
      for (const Application &app : job.applications) {
        job.Current();
        AXUIElementRef application = AXUIElementCreateApplication(app.pid);
        CFValue windows, focused;
        try {
          SetAXTimeout(application);
          CopyAttribute(job, application, kAXWindowsAttribute, windows, true);
          if (app.pid == job.frontPid) CopyAttribute(job, application, kAXFocusedWindowAttribute, focused, true);
        }
        catch (...) { CFRelease(application); throw; }
        CFRelease(application);
        if (!windows.value || CFGetTypeID(windows.value) != CFArrayGetTypeID()) continue;
        const CFIndex count = CFArrayGetCount((CFArrayRef)windows.value);
        if (count > 512) throw std::runtime_error("应用返回的窗口数量过多。");
        for (CFIndex index = 0; index < count; ++index) {
          job.Current();
          AXUIElementRef element = (AXUIElementRef)CFArrayGetValueAtIndex((CFArrayRef)windows.value, index);
          if (CFGetTypeID(element) != AXUIElementGetTypeID()) continue;
          SetAXTimeout(element);
          if (AXText(job, element, kAXRoleAttribute) != "AXWindow" || AXText(job, element, kAXSubroleAttribute) != "AXStandardWindow") continue;
          const bool minimized = AXBool(job, element, kAXMinimizedAttribute);
          const Rect bounds = AXBounds(job, element);
          const std::string title = AXText(job, element, kAXTitleAttribute);
          const QuartzWindow *match = nullptr;
          bool ambiguous = false;
          for (const auto &candidate : quartz) {
            if (candidate.pid != app.pid || std::abs(candidate.bounds.x - bounds.x) > 3 || std::abs(candidate.bounds.y - bounds.y) > 3 ||
                std::abs(candidate.bounds.width - bounds.width) > 3 || std::abs(candidate.bounds.height - bounds.height) > 3 ||
                (!candidate.title.empty() && !title.empty() && candidate.title != title)) continue;
            if (match) { ambiguous = true; break; }
            match = &candidate;
          }
          if (ambiguous) continue;
          std::shared_ptr<Window> known;
          for (const auto &prior : previous) {
            if (prior->application.pid == app.pid && prior->application.started == app.started &&
                ((match && prior->number == match->number) || CFEqual(prior->element, element))) { known = prior; break; }
          }
          // 首次发现的最小化窗口没有公开 API 可证明 Space 归属，不能猜测并迁移。
          if (!match && !(minimized && known)) continue;
          if (!known) {
            const std::string identity = "mac:" + std::to_string(job.epoch) + ":" + std::to_string(job.space) + ":" + std::to_string(nextIdentity.fetch_add(1));
            known = std::make_shared<Window>(element, app, match->number, bounds, identity);
          }
          if (focused.value && CFEqual(element, focused.value)) known->lastActive = now;
          const bool onDisplay = OnDisplay(bounds, job.bounds);
          job.windows.push_back({known, title, minimized, onDisplay});
          next.emplace(known->id, known);
        }
      }
      job.Current();
      { std::lock_guard<std::mutex> lock(registryMutex); job.Current(); registry = std::move(next); }
    };
    job->complete = [](napi_env env, Job &job) {
      napi_value result; Check(napi_create_array_with_length(env, job.windows.size(), &result));
      for (size_t index = 0; index < job.windows.size(); ++index) {
        const auto &item = job.windows[index]; const auto &window = item.window;
        napi_value value = Object(env);
        Set(env, value, "id", Text(env, window->id)); Set(env, value, "appId", Text(env, window->application.path));
        Set(env, value, "target", Text(env, window->application.path)); Set(env, value, "name", Text(env, window->application.name));
        Set(env, value, "title", Text(env, item.title)); Set(env, value, "minimized", Bool(env, item.minimized));
        Set(env, value, "onDesktopDisplay", Bool(env, item.onDisplay)); Set(env, value, "lastActive", Number(env, window->lastActive.load()));
        Check(napi_set_element(env, result, index, value));
      }
      return result;
    };
    return Queue(env, std::move(job), "macOS desktop window list");
  });
}

napi_value ActivateWindow(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    Trusted();
    size_t count = 2; napi_value args[2]; Check(napi_get_cb_info(env, info, &count, args, nullptr, nullptr));
    if (count != 2) throw std::runtime_error("缺少目标窗口或屏幕。");
    auto job = std::make_unique<Job>(); job->mutating = true; job->targets.push_back(ResolveWindow(ReadString(env, args[0])));
    job->caller = NSRunningApplication.currentApplication;
    job->targetApplication = [NSRunningApplication runningApplicationWithProcessIdentifier:job->targets[0]->application.pid];
    const pid_t frontPid = NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier;
    if (frontPid != getpid() && frontPid != job->targets[0]->application.pid)
      throw std::runtime_error("用户已切换到其他应用，已取消激活窗口。");
    napi_valuetype kind; Check(napi_typeof(env, args[1], &kind));
    job->move = kind != napi_null;
    if (job->move) job->bounds = ReadRect(env, args[1]);
    job->execute = [](Job &job) {
      const auto &window = job.targets[0];
      if (job.move) { Validate(job, window); NoModal(job, window); }
      else ValidateOnDisplay(job, window);
      if (AXBool(job, window->element, kAXMinimizedAttribute)) SetAttribute(job, window->element, kAXMinimizedAttribute, kCFBooleanFalse, "还原窗口");
      if (job.move) {
        if (AXBool(job, window->element, CFSTR("AXFullScreen"))) throw std::runtime_error("请先退出应用全屏，再迁移窗口。");
        Rect current = AXBounds(job, window->element);
        CGSize size{std::min(current.width, job.bounds.width), std::min(current.height, job.bounds.height)};
        CGPoint position{std::clamp(current.x, job.bounds.x, job.bounds.x + job.bounds.width - size.width),
                         std::clamp(current.y, job.bounds.y, job.bounds.y + job.bounds.height - size.height)};
        if (size.width != current.width || size.height != current.height) {
          AXValueRef value = AXValueCreate(kAXValueTypeCGSize, &size);
          try { SetAttribute(job, window->element, kAXSizeAttribute, value, "调整窗口大小"); }
          catch (...) { CFRelease(value); throw; } CFRelease(value);
        }
        AXValueRef point = AXValueCreate(kAXValueTypeCGPoint, &position);
        try { SetAttribute(job, window->element, kAXPositionAttribute, point, "迁移窗口"); }
        catch (...) { CFRelease(point); throw; } CFRelease(point);
        const auto observationDeadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(600);
        while (true) {
          const Rect actual = AXBounds(job, window->element);
          if (actual.x >= job.bounds.x - 3 && actual.y >= job.bounds.y - 3 &&
              actual.x + actual.width <= job.bounds.x + job.bounds.width + 3 && actual.y + actual.height <= job.bounds.y + job.bounds.height + 3) break;
          if (std::chrono::steady_clock::now() >= observationDeadline)
            throw std::runtime_error("应用尚未确认窗口已完整移入目标屏幕；操作可能仍会完成，请检查窗口位置。");
          std::this_thread::sleep_for(std::chrono::milliseconds(30)); job.Current();
        }
      }
      ValidateOnDisplay(job, window);
      if (job.move) NoModal(job, window);
      job.Current();
      AXError error = AXUIElementPerformAction(window->element, kAXRaiseAction);
      if (error != kAXErrorSuccess) throw std::runtime_error(AXFailure(error, "置前窗口"));
    };
    job->complete = [](napi_env env, Job &job) {
      job.Current();
      const auto &window = job.targets[0];
      const pid_t frontPid = NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier;
      if (frontPid != getpid() && frontPid != window->application.pid)
        throw std::runtime_error("用户已切换到其他应用，已取消激活窗口。");
      NSRunningApplication *application = [NSRunningApplication runningApplicationWithProcessIdentifier:window->application.pid];
      if (!application || ProcessStarted(application.processIdentifier) != window->application.started || CanonicalPath(application.bundleURL) != window->application.path)
        throw std::runtime_error("目标应用已退出或已经改变。");
      job.Current();
      if (![application activateWithOptions:0]) throw std::runtime_error("macOS 拒绝激活目标应用。");
      return Undefined(env);
    };
    return Queue(env, std::move(job), "macOS desktop activate window");
  });
}

napi_value CloseWindows(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    Trusted(); size_t count = 1; napi_value arg; Check(napi_get_cb_info(env, info, &count, &arg, nullptr, nullptr));
    if (count != 1) throw std::runtime_error("缺少需要关闭的窗口。");
    const auto ids = ReadStrings(env, arg, 64);
    if (ids.empty()) throw std::runtime_error("需要选择至少一个窗口。");
    auto job = std::make_unique<Job>(); job->mutating = true; job->caller = NSRunningApplication.currentApplication;
    for (const auto &id : ids) {
      auto target = ResolveWindow(id);
      if (std::find(job->targets.begin(), job->targets.end(), target) != job->targets.end()) throw std::runtime_error("窗口列表包含重复项目。");
      job->targets.push_back(target);
    }
    NSMutableArray<NSRunningApplication *> *allowed = [NSMutableArray array];
    for (const auto &window : job->targets) {
      if (window->application.path != job->targets[0]->application.path)
        throw std::runtime_error("一次只能关闭同一应用的所选窗口。");
      NSRunningApplication *application = [NSRunningApplication runningApplicationWithProcessIdentifier:window->application.pid];
      if (application) [allowed addObject:application];
    }
    job->allowedApplications = [allowed copy];
    job->execute = [](Job &job) {
      // 整批先验，提交期间每个窗口再验；AXPress 仅提交普通关闭，保存提示仍由应用处理。
      for (const auto &window : job.targets) { ValidateOnDisplay(job, window); NoModal(job, window); }
      size_t submitted = 0;
      try {
        for (const auto &window : job.targets) {
          ValidateOnDisplay(job, window); NoModal(job, window);
          CFValue button; CopyAttribute(job, window->element, kAXCloseButtonAttribute, button);
          if (!button.value || CFGetTypeID(button.value) != AXUIElementGetTypeID()) throw std::runtime_error("窗口不提供普通关闭按钮。");
          SetAXTimeout((AXUIElementRef)button.value);
          job.Current();
          AXError error = AXUIElementPerformAction((AXUIElementRef)button.value, kAXPressAction);
          if (error != kAXErrorSuccess) throw std::runtime_error(AXFailure(error, "提交关闭窗口"));
          submitted++;
        }
      } catch (const std::exception &error) {
        if (submitted) throw std::runtime_error("部分窗口已提交关闭（" + std::to_string(submitted) + "）；" + error.what());
        throw;
      }
    };
    return Queue(env, std::move(job), "macOS desktop close windows");
  });
}

napi_value ApplicationInfo(napi_env env, napi_callback_info info) {
  return Export(env, [&] {
    if (!NSThread.isMainThread) throw std::runtime_error("应用信息只能在主线程读取。");
    size_t count = 2; napi_value args[2]; Check(napi_get_cb_info(env, info, &count, args, nullptr, nullptr));
    if (count < 1) throw std::runtime_error("缺少应用路径。");
    auto job = std::make_unique<Job>(); job->serialAX = false; job->spaceBound = false; job->paths = ReadStrings(env, args[0], 64);
    if (count > 1) Check(napi_get_value_bool(env, args[1], &job->includeIcon));
    if (job->includeIcon && job->paths.size() > 8) throw std::runtime_error("每批最多读取 8 个应用图标。");
    job->execute = [](Job &job) {
      for (const std::string &path : job.paths) {
        job.Current();
        NSURL *url = [NSURL fileURLWithPath:String(path) isDirectory:YES];
        NSBundle *bundle = [NSBundle bundleWithURL:url];
        NSURL *executable = bundle.executableURL;
        NSNumber *regular = nil; NSError *failure = nil;
        if (![url.path.pathExtension.lowercaseString isEqualToString:@"app"] || !bundle || !bundle.infoDictionary || !executable ||
            ![executable getResourceValue:&regular forKey:NSURLIsRegularFileKey error:&failure] || !regular.boolValue ||
            ![NSFileManager.defaultManager isExecutableFileAtPath:executable.path])
          throw std::runtime_error("应用缺少有效的程序文件：" + path);
        NSString *displayName = [bundle objectForInfoDictionaryKey:@"CFBundleDisplayName"] ?: [bundle objectForInfoDictionaryKey:@"CFBundleName"];
        if (![displayName isKindOfClass:NSString.class] || !displayName.length) displayName = url.path.lastPathComponent.stringByDeletingPathExtension;
        job.information.push_back({CanonicalPath(url), Utf8(displayName), Utf8(bundle.bundleIdentifier)});
      }
    };
    job->complete = [](napi_env env, Job &job) {
      napi_value result; Check(napi_create_array_with_length(env, job.information.size(), &result));
      for (size_t index = 0; index < job.information.size(); ++index) {
        job.Current();
        const auto &application = job.information[index];
        napi_value value = Object(env);
        Set(env, value, "path", Text(env, application.path)); Set(env, value, "name", Text(env, application.name));
        Set(env, value, "bundleId", application.bundleId.empty() ? Null(env) : Text(env, application.bundleId));
        std::string icon;
        if (job.includeIcon) {
          NSImage *source = [NSWorkspace.sharedWorkspace iconForFile:String(application.path)];
          NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:nullptr pixelsWide:64 pixelsHigh:64 bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSCalibratedRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
          NSGraphicsContext *context = [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
          if (source && context) {
            [NSGraphicsContext saveGraphicsState]; NSGraphicsContext.currentContext = context;
            [source drawInRect:NSMakeRect(0, 0, 64, 64) fromRect:NSZeroRect operation:NSCompositingOperationCopy fraction:1 respectFlipped:YES hints:nil];
            [NSGraphicsContext restoreGraphicsState];
            NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            if (png) icon = "data:image/png;base64," + Utf8([png base64EncodedStringWithOptions:0]);
          }
        }
        Set(env, value, "icon", icon.empty() ? Null(env) : Text(env, icon)); Check(napi_set_element(env, result, index, value));
      }
      return result;
    };
    return Queue(env, std::move(job), "macOS desktop application metadata");
  });
}

napi_value AccessibilityStatus(napi_env env, napi_callback_info) { return Export(env, [&] { return Bool(env, AXIsProcessTrusted()); }); }
napi_value RequestAccessibility(napi_env env, napi_callback_info) {
  return Export(env, [&] {
    NSDictionary *options = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
    AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options); return Undefined(env);
  });
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

napi_value Initialize(napi_env env, napi_value exports) {
  const napi_property_descriptor functions[] = {
      {"configureSurfaces", nullptr, ConfigureSurfaces, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"surfaceState", nullptr, SurfaceState, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"listWindows", nullptr, ListWindows, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"activateWindow", nullptr, ActivateWindow, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"closeWindows", nullptr, CloseWindows, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"applicationInfo", nullptr, ApplicationInfo, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"accessibilityStatus", nullptr, AccessibilityStatus, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"requestAccessibility", nullptr, RequestAccessibility, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"reset", nullptr, Reset, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"cancelOperations", nullptr, CancelOperations, nullptr, nullptr, nullptr, napi_default, nullptr},
  };
  napi_define_properties(env, exports, sizeof(functions) / sizeof(functions[0]), functions);
  napi_add_env_cleanup_hook(env, [](void *) { @autoreleasepool { ResetState(); } }, nullptr);
  return exports;
}

}  // namespace

NAPI_MODULE(NODE_GYP_MODULE_NAME, Initialize)
