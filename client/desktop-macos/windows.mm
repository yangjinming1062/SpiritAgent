#include "host.hpp"

namespace desktop_macos {

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


}  // namespace desktop_macos
