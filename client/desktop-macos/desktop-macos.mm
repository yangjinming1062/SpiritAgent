#include "host.hpp"

namespace desktop_macos {

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


}  // namespace desktop_macos

static napi_value InitializeModule(napi_env env, napi_value exports) {
  return desktop_macos::Initialize(env, exports);
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, InitializeModule)
