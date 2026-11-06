#include "host.hpp"

namespace desktop_macos {

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


}  // namespace desktop_macos
