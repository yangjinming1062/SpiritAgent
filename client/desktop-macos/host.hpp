#pragma once

#include <node_api.h>

#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreGraphics/CoreGraphics.h>

#include <libproc.h>
#include <sys/proc_info.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cstring>
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

namespace desktop_macos {

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

extern std::atomic<uint64_t> generation;
extern std::atomic<uint64_t> spaceRevision;
extern std::atomic<uint64_t> operationRevision;
extern std::atomic<uint64_t> nextIdentity;
extern std::mutex registryMutex;
extern std::mutex accessibilityMutex;
extern std::unordered_map<std::string, std::shared_ptr<Window>> registry;
extern id spaceObserver;
extern NSWindow *__weak interactiveWindow;
extern Rect desktopBounds;

std::string Utf8(NSString *value);
NSString *String(const std::string &value);
void Check(napi_status result);
napi_value Undefined(napi_env env);
napi_value Bool(napi_env env, bool value);
napi_value Number(napi_env env, double value);
napi_value Text(napi_env env, const std::string &value);
napi_value Null(napi_env env);
void Set(napi_env env, napi_value object, const char *key, napi_value value);
napi_value Object(napi_env env);
std::string ReadString(napi_env env, napi_value value);
double ReadNumber(napi_env env, napi_value object, const char *key);
Rect ReadRect(napi_env env, napi_value value);
std::vector<std::string> ReadStrings(napi_env env, napi_value value, uint32_t maximum);

template <typename F> napi_value Export(napi_env env, F action) {
  @autoreleasepool {
    try { return action(); }
    catch (const std::exception &error) { napi_throw_error(env, nullptr, error.what()); return nullptr; }
  }
}

uint64_t ProcessStarted(pid_t pid);
std::string CanonicalPath(NSURL *url);
std::vector<Application> Applications();
bool Intersects(Rect first, Rect second);
double Intersection(Rect first, Rect second);
bool OnDisplay(Rect window, Rect target);
std::vector<QuartzWindow> QuartzWindows();
void InstallSpaceObserver();
NSWindow *WindowForHandle(napi_env env, napi_value value);

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

  void Current() const;
};

napi_value Queue(napi_env env, std::unique_ptr<Job> job, const char *name);
void Trusted();
std::string AXFailure(AXError error, const char *action);

class CFValue {
 public:
  CFTypeRef value = nullptr;
  ~CFValue() { if (value) CFRelease(value); }
};

CFTypeRef CopyAttribute(Job &job, AXUIElementRef element, CFStringRef key, CFValue &output, bool optional = false);
std::string AXText(Job &job, AXUIElementRef element, CFStringRef key, bool optional = true);
bool AXBool(Job &job, AXUIElementRef element, CFStringRef key, bool fallback = false);
void SetAXTimeout(AXUIElementRef element);
Rect AXBounds(Job &job, AXUIElementRef element);
void SetAttribute(Job &job, AXUIElementRef element, CFStringRef attribute, CFTypeRef value, const char *action);
void Validate(Job &job, const std::shared_ptr<Window> &window);
void ValidateOnDisplay(Job &job, const std::shared_ptr<Window> &window);
void NoModal(Job &job, const std::shared_ptr<Window> &window);
std::shared_ptr<Window> ResolveWindow(const std::string &id);
void ResetState();

napi_value ConfigureSurfaces(napi_env env, napi_callback_info info);
napi_value SurfaceState(napi_env env, napi_callback_info info);
napi_value ListWindows(napi_env env, napi_callback_info info);
napi_value ActivateWindow(napi_env env, napi_callback_info info);
napi_value CloseWindows(napi_env env, napi_callback_info info);
napi_value ApplicationInfo(napi_env env, napi_callback_info info);
napi_value AccessibilityStatus(napi_env env, napi_callback_info info);
napi_value RequestAccessibility(napi_env env, napi_callback_info info);
napi_value Reset(napi_env env, napi_callback_info info);
napi_value CancelOperations(napi_env env, napi_callback_info info);
napi_value Initialize(napi_env env, napi_value exports);

}  // namespace desktop_macos
