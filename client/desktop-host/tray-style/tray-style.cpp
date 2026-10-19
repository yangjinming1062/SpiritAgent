#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <commctrl.h>
#include <ocidl.h>
#include <xamlom.h>
#include <windows.ui.xaml.hosting.desktopwindowxamlsource.h>
#undef GetCurrentTime
#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.UI.h>
#include <winrt/Windows.UI.Xaml.h>
#include <winrt/Windows.UI.Xaml.Controls.h>
#include <winrt/Windows.UI.Xaml.Hosting.h>
#include <winrt/Windows.UI.Xaml.Media.h>
#include <winrt/Windows.UI.Xaml.Shapes.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#include "shared-state.h"

namespace {
namespace wf = winrt::Windows::Foundation;
namespace xaml = winrt::Windows::UI::Xaml;
namespace media = xaml::Media;
using spirit_tray::SharedState;
using Inspectable = wf::IInspectable;

// A private TAP class, instantiated directly by XAML diagnostics without registration.
constexpr CLSID tap_class = {0x7be27828, 0x72b0, 0x4eed, {0xa5, 0xc7, 0x96, 0x31, 0x69, 0xb6, 0xcc, 0x15}};
constexpr wchar_t mapping_prefix[] = L"Local\\SpiritAgentTrayStyle-";
constexpr wchar_t attach_message[] = L"SpiritAgent.TrayStyle.Attach.v1";
HMODULE module_handle = nullptr;
std::mutex session_mutex;
std::mutex bootstrap_mutex;

struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE handle) : value(handle) {}
    Handle(Handle const&) = delete;
    Handle& operator=(Handle const&) = delete;
    ~Handle() { if (value) CloseHandle(value); }
};

HRESULT last_error() noexcept {
    auto error = GetLastError();
    return HRESULT_FROM_WIN32(error ? error : ERROR_GEN_FAILURE);
}

bool valid_mapping_name(std::wstring const& name) {
    std::wstring_view value(name);
    std::wstring_view prefix(mapping_prefix);
    if (!value.starts_with(prefix) || value.size() <= prefix.size() || value.size() > 200) return false;
    return std::all_of(value.begin() + prefix.size(), value.end(), [](wchar_t c) {
        return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z') ||
            (c >= L'0' && c <= L'9') || c == L'-' || c == L'_';
    });
}

bool is_taskbar(HWND window, DWORD pid) {
    DWORD owner = 0;
    GetWindowThreadProcessId(window, &owner);
    wchar_t name[64]{};
    return IsWindow(window) && owner == pid && GetAncestor(window, GA_ROOT) == window &&
        GetClassNameW(window, name, ARRAYSIZE(name)) &&
        (wcscmp(name, L"Shell_TrayWnd") == 0 || wcscmp(name, L"Shell_SecondaryTrayWnd") == 0);
}

std::uint64_t creation_time(HANDLE process) {
    FILETIME created{}, exited{}, kernel{}, user{};
    winrt::check_bool(GetProcessTimes(process, &created, &exited, &kernel, &user));
    return (std::uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
}

struct Session {
    Handle mapping;
    SharedState* state = nullptr;
    Handle host;
    std::wstring name;
    DWORD host_pid = 0;
    DWORD explorer_pid = 0;
    std::uint64_t host_created = 0;
    HWND taskbar = nullptr;

    explicit Session(std::wstring const& mapping_name, DWORD expected_pid) : name(mapping_name) {
        winrt::check_bool(valid_mapping_name(name));
        mapping.value = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name.c_str());
        winrt::check_bool(mapping.value != nullptr);
        state = static_cast<SharedState*>(MapViewOfFile(mapping.value, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(SharedState)));
        winrt::check_bool(state != nullptr);
        if (state->magic != spirit_tray::state_magic || state->version != spirit_tray::state_version ||
            state->explorer_pid != expected_pid || !state->host_pid || !state->host_created ||
            state->reserved != 0 || (state->dark != 0 && state->dark != 1)) {
            UnmapViewOfFile(state);
            state = nullptr;
            winrt::throw_hresult(E_INVALIDARG);
        }
        host_pid = state->host_pid;
        explorer_pid = state->explorer_pid;
        host_created = state->host_created;
        taskbar = reinterpret_cast<HWND>(static_cast<std::uintptr_t>(state->taskbar));
        try {
            winrt::check_bool(is_taskbar(taskbar, explorer_pid));
            host.value = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, host_pid);
            winrt::check_bool(host.value != nullptr);
            winrt::check_bool(creation_time(host.value) == host_created);
            DWORD explorer_session = 0, host_session = 0;
            winrt::check_bool(ProcessIdToSessionId(explorer_pid, &explorer_session));
            winrt::check_bool(ProcessIdToSessionId(host_pid, &host_session));
            winrt::check_bool(explorer_session == host_session);
            winrt::check_bool(WaitForSingleObject(host.value, 0) == WAIT_TIMEOUT);
        } catch (...) {
            UnmapViewOfFile(state);
            state = nullptr;
            throw;
        }
    }

    ~Session() { if (state) UnmapViewOfFile(state); }

    bool owns_state() const noexcept {
        return state->magic == spirit_tray::state_magic && state->version == spirit_tray::state_version &&
            state->host_pid == host_pid && state->host_created == host_created &&
            state->explorer_pid == explorer_pid && state->taskbar == reinterpret_cast<std::uintptr_t>(taskbar);
    }

    bool live() const noexcept {
        if (!owns_state() || !is_taskbar(taskbar, explorer_pid) ||
            WaitForSingleObject(host.value, 0) != WAIT_TIMEOUT) return false;
        auto beat = static_cast<ULONGLONG>(InterlockedCompareExchange64(
            reinterpret_cast<volatile LONG64*>(&state->heartbeat_tick), 0, 0));
        auto now = GetTickCount64();
        return beat <= now && now - beat <= 10'000;
    }

    bool active() const noexcept {
        return live() && InterlockedCompareExchange(&state->active, 0, 0) == 1;
    }

    void publish(LONG status, HRESULT error = S_OK, std::uint32_t count = 0) const noexcept {
        if (!owns_state()) return;
        if (status >= 0 || count) InterlockedExchange(reinterpret_cast<volatile LONG*>(&state->fill_count), static_cast<LONG>(count));
        InterlockedExchange(&state->error, error);
        InterlockedExchange(&state->status, status);
    }
};

struct PlacementTarget {
    LONG x;
    LONG y;
    LONG width;
    LONG height;
    LONG dpi;
    LONG sequence;
};

struct PlacementGuard {
    std::shared_ptr<Session> owner;
    Handle mapping;
    spirit_tray::PlacementState* state = nullptr;
    UINT_PTR timer = 0;

    explicit PlacementGuard(std::shared_ptr<Session> const& lease) : owner(lease) {
        auto name = owner->name + L"-placement";
        mapping.value = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name.c_str());
        winrt::check_bool(mapping.value != nullptr);
        state = static_cast<spirit_tray::PlacementState*>(MapViewOfFile(mapping.value,
            FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(spirit_tray::PlacementState)));
        winrt::check_bool(state != nullptr);
        if (!owns_state()) {
            UnmapViewOfFile(state);
            state = nullptr;
            winrt::throw_hresult(E_INVALIDARG);
        }
    }

    ~PlacementGuard() { if (state) UnmapViewOfFile(state); }

    bool owns_state() const noexcept {
        return owner->owns_state() && state->magic == spirit_tray::placement_magic &&
            state->version == spirit_tray::placement_version && state->host_pid == owner->host_pid &&
            state->explorer_pid == owner->explorer_pid && state->host_created == owner->host_created &&
            state->taskbar == reinterpret_cast<std::uintptr_t>(owner->taskbar);
    }

    void publish(LONG status, HRESULT error = S_OK) const noexcept {
        if (!owns_state()) return;
        InterlockedExchange(&state->error, error);
        InterlockedExchange(&state->status, status);
    }

    bool enabled() const noexcept {
        return owns_state() && owner->active() && InterlockedCompareExchange(&state->status, 0, 0) == 1 &&
            InterlockedCompareExchange(&state->enabled, 0, 0) == 1;
    }

    std::optional<PlacementTarget> target() {
        if (!enabled()) return std::nullopt;
        for (int attempt = 0; attempt < 4; ++attempt) {
            auto sequence = InterlockedCompareExchange(&state->sequence, 0, 0);
            if (sequence & 1) continue;
            auto read = [](LONG& value) { return InterlockedCompareExchange(&value, 0, 0); };
            PlacementTarget next{read(state->x), read(state->y), read(state->width), read(state->height), read(state->dpi), sequence};
            auto complete = InterlockedCompareExchange(&state->sequence, 0, 0);
            if (sequence != complete || (complete & 1)) continue;
            if (!enabled()) return std::nullopt;
            if (next.x < -1'000'000 || next.x > 1'000'000 || next.y < -1'000'000 || next.y > 1'000'000 ||
                next.width <= 0 || next.width > 65'536 || next.height <= 0 || next.height > 65'536 ||
                next.dpi < 48 || next.dpi > 960) winrt::throw_hresult(E_INVALIDARG);
            return next;
        }
        return std::nullopt;
    }
};

thread_local std::unordered_map<HWND, std::shared_ptr<PlacementGuard>> placement_guards;
LRESULT CALLBACK placement_proc(HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR) noexcept;

void remove_placement_guard(std::shared_ptr<PlacementGuard> const& guard, bool destroyed = false) noexcept {
    auto window = guard->owner->taskbar;
    auto id = reinterpret_cast<UINT_PTR>(placement_proc);
    if (!RemoveWindowSubclass(window, placement_proc, id) && !destroyed) {
        guard->publish(-1, last_error());
        return;
    }
    if (guard->timer) KillTimer(window, guard->timer);
    auto existing = placement_guards.find(window);
    if (existing != placement_guards.end() && existing->second == guard) placement_guards.erase(existing);
    if (InterlockedCompareExchange(&guard->state->status, 0, 0) >= 0) guard->publish(0);
}

void CALLBACK placement_timer(HWND window, UINT, UINT_PTR, DWORD) noexcept {
    auto existing = placement_guards.find(window);
    if (existing == placement_guards.end()) return;
    auto guard = existing->second;
    if (!guard->owns_state() || !guard->owner->active() || InterlockedCompareExchange(&guard->state->status, 0, 0) < 0) {
        remove_placement_guard(guard);
    }
}

LRESULT CALLBACK placement_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR) noexcept {
    auto existing = placement_guards.find(window);
    if (existing == placement_guards.end()) return DefSubclassProc(window, message, wparam, lparam);
    auto guard = existing->second;
    if (message == WM_NCDESTROY) {
        remove_placement_guard(guard, true);
        return DefSubclassProc(window, message, wparam, lparam);
    }
    auto position = message == WM_WINDOWPOSCHANGING && lparam ? reinterpret_cast<WINDOWPOS*>(lparam) : nullptr;
    // Explorer may replace WINDOWPOS while processing the message; constrain its final result.
    auto result = DefSubclassProc(window, message, wparam, lparam);
    if (!position || position->hwnd != window || (position->flags & SWP_NOMOVE)) return result;
    try {
        auto target = guard->target();
        if (!target) return result;
        struct DpiContext {
            DPI_AWARENESS_CONTEXT previous = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            ~DpiContext() { if (previous) SetThreadDpiAwarenessContext(previous); }
        } context;
        winrt::check_bool(context.previous != nullptr);
        RECT current{};
        winrt::check_bool(GetWindowRect(window, &current));
        if (current.right - current.left != target->width || current.bottom - current.top != target->height ||
            GetDpiForWindow(window) != static_cast<UINT>(target->dpi)) {
            return result;
        }
        if (!guard->enabled() || InterlockedCompareExchange(&guard->state->sequence, 0, 0) != target->sequence) return result;
        if (position->x != target->x || position->y != target->y) {
            position->x = target->x;
            position->y = target->y;
            InterlockedIncrement(reinterpret_cast<volatile LONG*>(&guard->state->corrections));
        }
    } catch (...) {
        guard->publish(-1, winrt::to_hresult());
    }
    return result;
}

void install_placement_guard(std::shared_ptr<Session> const& owner) {
    winrt::check_bool(GetWindowThreadProcessId(owner->taskbar, nullptr) == GetCurrentThreadId());
    winrt::check_bool(owner->active());
    auto existing = placement_guards.find(owner->taskbar);
    if (existing != placement_guards.end()) {
        if (existing->second->owner->name == owner->name) return;
        auto previous = existing->second;
        if (previous->owner->active()) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_BUSY));
        remove_placement_guard(previous);
        if (placement_guards.contains(owner->taskbar)) winrt::throw_hresult(last_error());
    }
    auto guard = std::make_shared<PlacementGuard>(owner);
    if (GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(owner->taskbar)) != DPI_AWARENESS_PER_MONITOR_AWARE) {
        guard->publish(-1, E_NOTIMPL);
        winrt::throw_hresult(E_NOTIMPL);
    }
    // The callback outlives the injection hook, as does the XAML TAP in this module.
    static std::once_flag retain_module;
    try {
        std::call_once(retain_module, [] {
            HMODULE retained = nullptr;
            winrt::check_bool(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                reinterpret_cast<LPCWSTR>(placement_proc), &retained));
        });
    } catch (...) {
        auto error = winrt::to_hresult();
        guard->publish(-1, error);
        winrt::throw_hresult(error);
    }
    auto id = reinterpret_cast<UINT_PTR>(placement_proc);
    placement_guards.emplace(owner->taskbar, guard);
    if (!SetWindowSubclass(owner->taskbar, placement_proc, id, 0)) {
        auto error = last_error();
        placement_guards.erase(owner->taskbar);
        guard->publish(-1, error);
        winrt::throw_hresult(error);
    }
    guard->timer = SetTimer(owner->taskbar, id, 250, placement_timer);
    if (!guard->timer) {
        auto error = last_error();
        guard->publish(-1, error);
        remove_placement_guard(guard);
        winrt::throw_hresult(error);
    }
    guard->publish(1);
}

bool placement_ready(std::shared_ptr<Session> const& owner) {
    PlacementGuard control(owner);
    auto status = InterlockedCompareExchange(&control.state->status, 0, 0);
    if (status < 0) winrt::throw_hresult(InterlockedCompareExchange(&control.state->error, 0, 0));
    return status == 1;
}

std::shared_ptr<Session> current_session;

std::shared_ptr<Session> session() {
    std::lock_guard lock(session_mutex);
    return current_session;
}

void set_session(std::shared_ptr<Session> const& next) {
    std::lock_guard lock(session_mutex);
    if (!next->active()) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_CANCELLED));
    if (!current_session || current_session->name != next->name) current_session = next;
}

Inspectable saved_value(xaml::DependencyObject const& element, xaml::DependencyProperty const& property) {
    auto value = element.ReadLocalValue(property);
    if (value) {
        auto type = winrt::get_class_name(value);
        // Binding expressions are not assignable property values.
        if (type == L"Windows.UI.Xaml.Data.BindingExpression" || type == L"Windows.UI.Xaml.Data.BindingExpressionBase") {
            return element.GetValue(property);
        }
    }
    return value;
}

bool same_value(xaml::DependencyProperty const& property, Inspectable const& first, Inspectable const& second) {
    if (first == second) return true;
    if (!first || !second) return false;
    if (first == xaml::DependencyProperty::UnsetValue() || second == xaml::DependencyProperty::UnsetValue()) return false;
    if (property == xaml::FrameworkElement::WidthProperty() || property == xaml::FrameworkElement::HeightProperty()) {
        return winrt::unbox_value<double>(first) == winrt::unbox_value<double>(second);
    }
    if (property == xaml::Controls::Grid::CornerRadiusProperty()) {
        return winrt::unbox_value<xaml::CornerRadius>(first) == winrt::unbox_value<xaml::CornerRadius>(second);
    }
    if (property == xaml::FrameworkElement::HorizontalAlignmentProperty()) {
        return winrt::unbox_value<xaml::HorizontalAlignment>(first) == winrt::unbox_value<xaml::HorizontalAlignment>(second);
    }
    if (property == xaml::FrameworkElement::VerticalAlignmentProperty()) {
        return winrt::unbox_value<xaml::VerticalAlignment>(first) == winrt::unbox_value<xaml::VerticalAlignment>(second);
    }
    if (property == xaml::FrameworkElement::RequestedThemeProperty()) {
        return winrt::unbox_value<xaml::ElementTheme>(first) == winrt::unbox_value<xaml::ElementTheme>(second);
    }
    return false;
}

struct Property : std::enable_shared_from_this<Property> {
    xaml::DependencyObject element{nullptr};
    xaml::DependencyProperty property{nullptr};
    Inspectable original{nullptr};
    Inspectable applied{nullptr};
    std::int64_t token = 0;
    bool writing = false;
    bool enabled = true;
    bool fill = false;
    std::uint64_t seen = 0;
    std::weak_ptr<Session> owner;

    Property(xaml::DependencyObject const& target, xaml::DependencyProperty const& dp, Inspectable const& value,
        bool is_fill, std::shared_ptr<Session> const& lease)
        : element(target), property(dp), original(saved_value(target, dp)), applied(value), fill(is_fill), owner(lease) {}

    void write() {
        auto lease = owner.lock();
        if (!lease || !lease->active()) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_CANCELLED));
        // Publish ownership before checking active again, so restoration cannot race a first write.
        InterlockedCompareExchange(reinterpret_cast<volatile LONG*>(&lease->state->fill_count), 1, 0);
        if (!lease->active()) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_CANCELLED));
        if (same_value(property, element.GetValue(property), applied)) return;
        writing = true;
        try {
            element.SetValue(property, applied);
            writing = false;
        } catch (...) {
            writing = false;
            throw;
        }
    }

    void observe() {
        std::weak_ptr<Property> weak = shared_from_this();
        token = element.RegisterPropertyChangedCallback(property, [weak](auto const&, auto const&) {
            auto self = weak.lock();
            if (!self || self->writing || !self->enabled) return;
            try {
                auto value = saved_value(self->element, self->property);
                // Some theme notifications keep the local override in place.
                if (same_value(self->property, value, self->applied)) return;
                self->original = value;
                if (auto lease = self->owner.lock(); lease && lease->active()) self->write();
            } catch (...) {
                if (auto owner = self->owner.lock()) owner->publish(-1, winrt::to_hresult());
            }
        });
    }

    void restore() {
        enabled = false;
        if (token) {
            element.UnregisterPropertyChangedCallback(property, token);
            token = 0;
        }
        if (original == xaml::DependencyProperty::UnsetValue()) element.ClearValue(property);
        else element.SetValue(property, original);
    }
};

struct View : std::enable_shared_from_this<View> {
    std::unordered_map<InstanceHandle, xaml::Hosting::DesktopWindowXamlSource> sources;
    std::vector<std::shared_ptr<Property>> properties;
    std::shared_ptr<Session> owner;
    xaml::DispatcherTimer timer{nullptr};
    media::SolidColorBrush transparent{nullptr};
    media::SolidColorBrush bitmap_background{nullptr};
    winrt::event_token tick_token{};
    bool dirty = true;
    bool stopped = false;
    bool claimed = false;
    bool target_seen = false;
    LONG dark = -1;
    std::uint64_t generation = 0;
    ULONGLONG missing_since = 0;

    void start() {
        timer = xaml::DispatcherTimer();
        timer.Interval(std::chrono::milliseconds(250));
        std::weak_ptr<View> weak = shared_from_this();
        tick_token = timer.Tick([weak](auto const&, auto const&) {
            if (auto self = weak.lock()) self->tick();
        });
        timer.Start();
    }

    void add(xaml::DependencyObject const& target, xaml::DependencyProperty const& dp, Inspectable const& value, bool fill) {
        for (auto const& property : properties) {
            if (property->element == target && property->property == dp) {
                property->seen = generation;
                if (!same_value(dp, property->applied, value)) {
                    property->applied = value;
                    property->write();
                }
                return;
            }
        }
        auto property = std::make_shared<Property>(target, dp, value, fill, owner);
        property->seen = generation;
        property->observe();
        properties.push_back(property);
        claimed = true;
        property->write();
    }

    void restore(bool publish) {
        bool had_properties = claimed || !properties.empty();
        HRESULT error = S_OK;
        std::vector<std::shared_ptr<Property>> remaining;
        for (auto it = properties.rbegin(); it != properties.rend(); ++it) {
            try { (*it)->restore(); }
            catch (...) {
                HRESULT failure = winrt::to_hresult();
                if (failure == RO_E_CLOSED) continue;
                remaining.push_back(*it);
                if (SUCCEEDED(error)) error = failure;
            }
        }
        std::reverse(remaining.begin(), remaining.end());
        properties = std::move(remaining);
        if (FAILED(error)) {
            if (owner) owner->publish(-1, error, 1);
            winrt::throw_hresult(error);
        }
        dark = -1;
        claimed = false;
        target_seen = false;
        missing_since = 0;
        if (owner && had_properties) {
            if (publish) owner->publish(2);
            else if (owner->owns_state()) InterlockedExchange(reinterpret_cast<volatile LONG*>(&owner->state->fill_count), 0);
        }
    }

    bool source_matches(xaml::Hosting::DesktopWindowXamlSource const& source) {
        HWND window = nullptr;
        auto native = source.as<IDesktopWindowXamlSourceNative>();
        winrt::check_hresult(native->get_WindowHandle(&window));
        return window && GetAncestor(window, GA_ROOT) == owner->taskbar;
    }

    void wait_for_target() {
        dirty = true;
        auto now = GetTickCount64();
        if (!missing_since) missing_since = now;
        if (now - missing_since >= 2000) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_NOT_FOUND));
    }

    void contrast_plate(xaml::DependencyObject const& element, media::Brush const& background) {
        auto image = element.try_as<xaml::Controls::Image>();
        if (!image) return;
        auto grid = media::VisualTreeHelper::GetParent(image).try_as<xaml::Controls::Grid>();
        if (!grid || grid.Name() != L"ContainerGrid") return;
        auto content = media::VisualTreeHelper::GetParent(grid);
        if (!content || winrt::get_class_name(content) != L"SystemTray.ImageIconContent") return;
        bool notification = false;
        bool tray = false;
        auto ancestor = media::VisualTreeHelper::GetParent(content);
        for (int depth = 0; ancestor && depth < 64; ++depth) {
            auto type = winrt::get_class_name(ancestor);
            if (type == L"SystemTray.NotifyIconView") notification = true;
            if (type == L"SystemTray.SystemTrayFrame") { tray = true; break; }
            ancestor = media::VisualTreeHelper::GetParent(ancestor);
        }
        if (!notification || !tray) return;
        // App bitmaps do not follow RequestedTheme; retain their pixels and give them a bounded neutral surface.
        add(grid, xaml::Controls::Panel::BackgroundProperty(), background, false);
        add(grid, xaml::Controls::Grid::CornerRadiusProperty(), winrt::box_value(xaml::CornerRadius{6, 6, 6, 6}), false);
        add(grid, xaml::FrameworkElement::WidthProperty(), winrt::box_value(24.0), false);
        add(grid, xaml::FrameworkElement::HeightProperty(), winrt::box_value(24.0), false);
        add(grid, xaml::FrameworkElement::HorizontalAlignmentProperty(), winrt::box_value(xaml::HorizontalAlignment::Center), false);
        add(grid, xaml::FrameworkElement::VerticalAlignmentProperty(), winrt::box_value(xaml::VerticalAlignment::Center), false);
    }

    void apply() {
        ++generation;
        std::vector<xaml::DependencyObject> pending;
        bool found_frame = false;
        xaml::FrameworkElement tray{nullptr};
        xaml::FrameworkElement frame{nullptr};
        for (auto const& [handle, source] : sources) {
            try {
                if (source_matches(source)) {
                    auto content = source.Content();
                    target_seen = true;
                    if (content) pending.push_back(content);
                }
            } catch (winrt::hresult_error const& error) {
                if (error.code() != RPC_E_WRONG_THREAD && error.code() != RO_E_CLOSED) throw;
            }
        }
        std::size_t visited = 0;
        if (!transparent) transparent = media::SolidColorBrush(winrt::Windows::UI::Color{0, 0, 0, 0});
        if (!bitmap_background) bitmap_background = media::SolidColorBrush(winrt::Windows::UI::Color{224, 242, 242, 242});
        while (!pending.empty()) {
            if (++visited > 8192) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_BUFFER_OVERFLOW));
            auto element = pending.back();
            pending.pop_back();
            auto control = element.try_as<xaml::FrameworkElement>();
            auto type = winrt::get_class_name(element);
            contrast_plate(element, bitmap_background);
            if (type == L"Taskbar.TaskbarFrame") {
                frame = control;
                found_frame = true;
            }
            if (control && control.Name() == L"SystemTrayFrameGrid") tray = control;
            if (found_frame && control && type == L"Windows.UI.Xaml.Shapes.Rectangle" &&
                (control.Name() == L"BackgroundFill" || control.Name() == L"BackgroundStroke")) {
                // Require a TaskbarFrame ancestor, excluding thumbnails and popups.
                auto ancestor = media::VisualTreeHelper::GetParent(element);
                bool belongs = false;
                for (int depth = 0; ancestor && depth < 64; ++depth) {
                    if (ancestor == frame) { belongs = true; break; }
                    ancestor = media::VisualTreeHelper::GetParent(ancestor);
                }
                if (belongs) add(element, xaml::Shapes::Shape::FillProperty(), transparent, control.Name() == L"BackgroundFill");
            }
            auto count = media::VisualTreeHelper::GetChildrenCount(element);
            for (int index = 0; index < count; ++index) pending.push_back(media::VisualTreeHelper::GetChild(element, index));
        }
        if (!found_frame) {
            // Other XAML UI threads may never contain the selected taskbar.
            if (target_seen || claimed || !properties.empty()) wait_for_target();
            return;
        }
        auto theme_target = tray ? tray : frame;
        auto wanted = InterlockedCompareExchange(&owner->state->dark, 0, 0);
        if (wanted != 0 && wanted != 1) winrt::throw_hresult(E_INVALIDARG);
        auto theme = wanted ? xaml::ElementTheme::Dark : xaml::ElementTheme::Light;
        add(theme_target, xaml::FrameworkElement::RequestedThemeProperty(), winrt::box_value(theme), false);
        dark = wanted;
        for (auto it = properties.begin(); it != properties.end();) {
            if ((*it)->seen == generation) { ++it; continue; }
            try { (*it)->restore(); }
            catch (winrt::hresult_error const& error) { if (error.code() != RO_E_CLOSED) throw; }
            it = properties.erase(it);
        }
        auto fills = std::count_if(properties.begin(), properties.end(), [](auto const& property) { return property->fill; });
        if (!fills) { wait_for_target(); return; }
        missing_since = 0;
        if (!owner->active()) return;
        owner->publish(1, S_OK, static_cast<std::uint32_t>(fills));
        dirty = false;
    }

    void tick() noexcept {
        try {
            auto next = session();
            if (next != owner) {
                restore(true);
                owner = next;
                dirty = true;
                stopped = false;
            }
            if (!owner) return;
            if (!owner->active()) {
                if (!stopped || !properties.empty() || claimed) {
                    restore(true);
                    stopped = true;
                }
                return;
            }
            if (InterlockedCompareExchange(&owner->state->status, 0, 0) < 0) {
                if (!stopped || !properties.empty() || claimed) restore(false);
                stopped = true;
                return;
            }
            if (stopped) return;
            if (dark != InterlockedCompareExchange(&owner->state->dark, 0, 0)) dirty = true;
            if (dirty) apply();
        } catch (...) {
            HRESULT error = winrt::to_hresult();
            bool restored = false;
            try { restore(false); restored = true; }
            catch (...) { error = winrt::to_hresult(); }
            if (owner) {
                if (restored && !owner->active()) owner->publish(2);
                else owner->publish(-1, error, restored ? 0 : 1);
            }
            stopped = true;
        }
    }
};

struct Watcher : winrt::implements<Watcher, IVisualTreeServiceCallback2, winrt::non_agile> {
    enum class Subscription { idle, pending, ready };
    winrt::com_ptr<IXamlDiagnostics> diagnostics;
    std::atomic<Subscription> subscription{Subscription::idle};
    std::mutex views_mutex;
    std::unordered_map<DWORD, std::shared_ptr<View>> views;

    explicit Watcher(winrt::com_ptr<IXamlDiagnostics> const& site) : diagnostics(site) {}

    bool ready() const noexcept { return subscription.load() == Subscription::ready; }

    void begin(std::shared_ptr<Session> const& owner) {
        auto expected = Subscription::idle;
        if (!subscription.compare_exchange_strong(expected, Subscription::pending)) return;
        try {
            auto strong = get_strong();
            std::thread([strong, owner] {
                try {
                    // Diagnostics marshals callbacks onto each XAML UI thread.
                    winrt::check_hresult(strong->diagnostics.as<IVisualTreeService3>()->AdviseVisualTreeChange(strong.get()));
                    strong->subscription.store(Subscription::ready);
                } catch (...) {
                    auto error = winrt::to_hresult();
                    strong->subscription.store(Subscription::idle);
                    if (owner->active()) owner->publish(-1, error);
                }
            }).detach();
        } catch (...) {
            subscription.store(Subscription::idle);
            throw;
        }
    }

    HRESULT STDMETHODCALLTYPE OnVisualTreeChange(ParentChildRelation, VisualElement element, VisualMutationType mutation) noexcept override {
        // XAML diagnostics transfers these BSTR allocations to the callback.
        auto free_strings = [&] {
            if (mutation != Add) return;
            SysFreeString(element.SrcInfo.FileName);
            SysFreeString(element.SrcInfo.Hash);
            SysFreeString(element.Name);
            SysFreeString(element.Type);
        };
        std::shared_ptr<View> view;
        std::shared_ptr<Session> callback_owner;
        try {
            {
                std::lock_guard lock(views_mutex);
                auto& existing = views[GetCurrentThreadId()];
                if (!existing) { existing = std::make_shared<View>(); existing->start(); }
                view = existing;
            }
            callback_owner = view->owner;
            view->dirty = true;
            if (mutation == Add && element.Type &&
                std::wstring_view(element.Type, SysStringLen(element.Type)) == L"Windows.UI.Xaml.Hosting.DesktopWindowXamlSource") {
                Inspectable object{nullptr};
                winrt::check_hresult(diagnostics->GetIInspectableFromHandle(element.Handle,
                    reinterpret_cast<::IInspectable**>(winrt::put_abi(object))));
                auto source = object.try_as<xaml::Hosting::DesktopWindowXamlSource>();
                if (source) view->sources.insert_or_assign(element.Handle, source);
            } else if (mutation == Remove) {
                view->sources.erase(element.Handle);
            }
            free_strings();
            return S_OK;
        } catch (...) {
            auto error = winrt::to_hresult();
            free_strings();
            if (callback_owner && callback_owner->active()) callback_owner->publish(-1, error);
            return S_OK;
        }
    }

    HRESULT STDMETHODCALLTYPE OnElementStateChanged(InstanceHandle, VisualElementState, LPCWSTR) noexcept override { return S_OK; }
};

winrt::com_ptr<Watcher> watcher;

struct Site : winrt::implements<Site, IObjectWithSite, winrt::non_agile> {
    winrt::com_ptr<IUnknown> site;

    HRESULT STDMETHODCALLTYPE SetSite(IUnknown* value) noexcept override {
        try {
            site.copy_from(value);
            if (!value) return S_OK;
            auto diagnostics = site.as<IXamlDiagnostics>();
            BSTR data = nullptr;
            winrt::check_hresult(diagnostics->GetInitializationData(&data));
            std::wstring mapping_name(data ? data : L"", data ? SysStringLen(data) : 0);
            SysFreeString(data);
            auto next = std::make_shared<Session>(mapping_name, GetCurrentProcessId());
            set_session(next);
            winrt::com_ptr<Watcher> active_watcher;
            {
                std::lock_guard lock(session_mutex);
                if (!watcher) watcher = winrt::make_self<Watcher>(diagnostics);
                active_watcher = watcher;
            }
            active_watcher->begin(next);
            return S_OK;
        } catch (...) { return winrt::to_hresult(); }
    }

    HRESULT STDMETHODCALLTYPE GetSite(REFIID iid, void** result) noexcept override { return site.as(iid, result); }
};

struct Factory : winrt::implements<Factory, IClassFactory> {
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** result) noexcept override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        try { return winrt::make<Site>().as(iid, result); }
        catch (...) { return winrt::to_hresult(); }
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL) noexcept override { return S_OK; }
};

std::wstring module_path() {
    std::wstring path(32768, L'\0');
    auto length = GetModuleFileNameW(module_handle, path.data(), static_cast<DWORD>(path.size()));
    winrt::check_bool(length && length < path.size());
    path.resize(length);
    return path;
}

using Initialize = decltype(&InitializeXamlDiagnosticsEx);

struct AttemptResult {
    Handle completed{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    HRESULT result = E_PENDING;
};

struct Attempt {
    std::shared_ptr<AttemptResult> response;
    HMODULE self = nullptr;
    HMODULE xaml_library = nullptr;
    Initialize function = nullptr;
    DWORD pid = 0;
    std::wstring endpoint;
    std::wstring path;
    std::wstring mapping;
    ~Attempt() { if (xaml_library) FreeLibrary(xaml_library); }
};

DWORD WINAPI initialize_attempt(void* parameter) noexcept {
    std::unique_ptr<Attempt> attempt(static_cast<Attempt*>(parameter));
    auto self = attempt->self;
    auto apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    attempt->response->result = attempt->function(attempt->endpoint.c_str(), attempt->pid,
        nullptr, attempt->path.c_str(), tap_class, attempt->mapping.c_str());
    if (SUCCEEDED(apartment)) CoUninitialize();
    SetEvent(attempt->response->completed.value);
    attempt.reset();
    // A timed-out attach may have removed the hook while this API call was still running.
    FreeLibraryAndExitThread(self, 0);
}

HRESULT initialize(DWORD pid, std::wstring const& mapping_name, DWORD budget) noexcept {
    try {
        auto path = module_path();
        auto started = GetTickCount64();
        HRESULT result = HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        for (int index = 1; index <= 64 && GetTickCount64() - started < budget; ++index) {
            // Initialization is cached per calling thread, so each endpoint needs a fresh one.
            auto attempt = std::make_unique<Attempt>();
            attempt->response = std::make_shared<AttemptResult>();
            winrt::check_bool(attempt->response->completed.value != nullptr);
            attempt->xaml_library = LoadLibraryExW(L"Windows.UI.Xaml.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
            winrt::check_bool(attempt->xaml_library != nullptr);
            attempt->function = reinterpret_cast<Initialize>(GetProcAddress(attempt->xaml_library, "InitializeXamlDiagnosticsEx"));
            winrt::check_bool(attempt->function != nullptr);
            attempt->pid = pid;
            attempt->endpoint = L"VisualDiagConnection" + std::to_wstring(index);
            attempt->path = path;
            attempt->mapping = mapping_name;
            winrt::check_bool(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                reinterpret_cast<LPCWSTR>(initialize_attempt), &attempt->self));
            auto response = attempt->response;
            Handle worker(CreateThread(nullptr, 0, initialize_attempt, attempt.get(), 0, nullptr));
            if (!worker.value) { auto error = last_error(); FreeLibrary(attempt->self); return error; }
            attempt.release();
            auto elapsed = GetTickCount64() - started;
            auto remaining = elapsed < budget ? budget - static_cast<DWORD>(elapsed) : 0;
            auto wait = WaitForSingleObject(response->completed.value, remaining);
            if (wait == WAIT_TIMEOUT) return HRESULT_FROM_WIN32(WAIT_TIMEOUT);
            if (wait != WAIT_OBJECT_0) return last_error();
            result = response->result;
            if (SUCCEEDED(result) || result != HRESULT_FROM_WIN32(ERROR_NOT_FOUND)) break;
        }
        return result;
    } catch (...) { return winrt::to_hresult(); }
}

struct BootstrapRequest {
    std::wstring mapping;
    HMODULE self = nullptr;
};

DWORD WINAPI bootstrap(void* value) noexcept {
    HMODULE self = nullptr;
    {
        std::unique_ptr<BootstrapRequest> request(static_cast<BootstrapRequest*>(value));
        self = request->self;
        std::shared_ptr<Session> next;
        try {
            std::lock_guard lock(bootstrap_mutex);
            next = std::make_shared<Session>(request->mapping, GetCurrentProcessId());
            if (!next->active()) winrt::throw_hresult(HRESULT_FROM_WIN32(ERROR_CANCELLED));
            bool ready;
            { std::lock_guard session_lock(session_mutex); ready = watcher && watcher->ready(); }
            if (ready) set_session(next);
            else {
                auto error = initialize(GetCurrentProcessId(), request->mapping, 1400);
                if (FAILED(error) && next->active()) next->publish(-1, error);
            }
        } catch (...) {
            if (next && next->active()) next->publish(-1, winrt::to_hresult());
        }
    }
    FreeLibraryAndExitThread(self, 0);
}
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** result) {
    if (!result) return E_POINTER;
    *result = nullptr;
    if (clsid != tap_class) return CLASS_E_CLASSNOTAVAILABLE;
    try { return winrt::make<Factory>().as(iid, result); }
    catch (...) { return winrt::to_hresult(); }
}

STDAPI DllCanUnloadNow() { return S_FALSE; }

#pragma comment(linker, "/export:DllGetClassObject,PRIVATE")
#pragma comment(linker, "/export:DllCanUnloadNow,PRIVATE")

extern "C" __declspec(dllexport) LRESULT CALLBACK SpiritAgentHook(int code, WPARAM wparam, LPARAM lparam) noexcept {
    if (code >= 0 && lparam) {
        auto message = reinterpret_cast<CWPSTRUCT const*>(lparam);
        if (message->message == RegisterWindowMessageW(attach_message) && message->wParam <= 0xffff) {
            wchar_t name[256]{};
            if (GlobalGetAtomNameW(static_cast<ATOM>(message->wParam), name, ARRAYSIZE(name))) {
                std::shared_ptr<Session> owner;
                try {
                    owner = std::make_shared<Session>(name, GetCurrentProcessId());
                    if (message->hwnd != owner->taskbar) winrt::throw_hresult(E_ACCESSDENIED);
                    install_placement_guard(owner);
                    auto request = std::make_unique<BootstrapRequest>();
                    request->mapping = name;
                    if (valid_mapping_name(request->mapping) &&
                        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                            reinterpret_cast<LPCWSTR>(bootstrap), &request->self)) {
                        auto worker = CreateThread(nullptr, 0, bootstrap, request.get(), 0, nullptr);
                        if (worker) { request.release(); CloseHandle(worker); }
                        else FreeLibrary(request->self);
                    }
                } catch (...) {
                    if (owner) owner->publish(-1, winrt::to_hresult());
                }
            }
        }
    }
    return CallNextHookEx(nullptr, code, wparam, lparam);
}

extern "C" __declspec(dllexport) HRESULT WINAPI SpiritAgentAttach(DWORD explorer_pid, LPCWSTR mapping_name) noexcept {
    try {
        if (!mapping_name) return E_POINTER;
        auto target = std::make_shared<Session>(mapping_name, explorer_pid);
        auto deadline = GetTickCount64() + 3500;
        auto result = initialize(explorer_pid, mapping_name, 1000);
        auto wait_started = GetTickCount64();
        while (SUCCEEDED(result) && GetTickCount64() - wait_started < 250) {
            auto status = InterlockedCompareExchange(&target->state->status, 0, 0);
            if (status == 1) break;
            if (status == 2 && !target->active()) return S_OK;
            if (status < 0) return target->state->error;
            Sleep(25);
        }
        // Always install the UI-thread geometry guard, including an already-ready XAML TAP.
        DWORD actual_pid = 0;
        auto thread = GetWindowThreadProcessId(target->taskbar, &actual_pid);
        if (!thread || actual_pid != explorer_pid) return E_ACCESSDENIED;
        auto hook = SetWindowsHookExW(WH_CALLWNDPROC, SpiritAgentHook, module_handle, thread);
        if (!hook) return last_error();
        auto atom = GlobalAddAtomW(mapping_name);
        if (!atom) { auto error = last_error(); UnhookWindowsHookEx(hook); return error; }
        DWORD_PTR ignored = 0;
        auto delivered = SendMessageTimeoutW(target->taskbar, RegisterWindowMessageW(attach_message), atom, 0,
            SMTO_ABORTIFHUNG | SMTO_BLOCK, 250, &ignored);
        auto delivery_error = delivered ? S_OK : last_error();
        GlobalDeleteAtom(atom);
        result = delivery_error;
        bool ready = false;
        try {
            while (SUCCEEDED(result) && GetTickCount64() < deadline) {
                auto status = InterlockedCompareExchange(&target->state->status, 0, 0);
                if (status == 2 && !target->active()) { ready = true; break; }
                if (status < 0) { result = target->state->error; break; }
                if (status == 1 && placement_ready(target)) { ready = true; break; }
                Sleep(25);
            }
        } catch (...) { result = winrt::to_hresult(); }
        UnhookWindowsHookEx(hook);
        if (FAILED(result)) return result;
        return ready ? S_OK : HRESULT_FROM_WIN32(WAIT_TIMEOUT);
    } catch (...) { return winrt::to_hresult(); }
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) noexcept {
    if (reason == DLL_PROCESS_ATTACH) module_handle = instance;
    return TRUE;
}
