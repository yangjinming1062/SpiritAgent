use super::*;
use std::cell::RefCell;
use std::sync::OnceLock;
use windows_sys::Win32::Graphics::Dwm::{
    DWM_THUMBNAIL_PROPERTIES, DWM_TNP_RECTDESTINATION, DWM_TNP_VISIBLE,
    DwmQueryThumbnailSourceSize, DwmRegisterThumbnail, DwmUnregisterThumbnail,
    DwmUpdateThumbnailProperties,
};
use windows_sys::Win32::Graphics::Gdi::{
    BeginPaint, CreateFontW, DC_BRUSH, DEFAULT_CHARSET, DEFAULT_PITCH, DEFAULT_QUALITY, DT_CENTER,
    DT_END_ELLIPSIS, DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER, DeleteObject, DrawTextW, EndPaint,
    FF_DONTCARE, FillRect, FrameRect, GetStockObject, HDC, HFONT, InvalidateRect, PAINTSTRUCT,
    SelectObject, SetBkMode, SetDCBrushColor, SetTextColor, TRANSPARENT,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::SystemServices::{
    MK_CONTROL, MK_MBUTTON, MK_RBUTTON, MK_SHIFT, MK_XBUTTON1, MK_XBUTTON2,
};

const CLASS: &str = "SpiritAgentDockPreview";
const LEAVE_DELAY: Duration = Duration::from_millis(200);

pub(super) struct PreviewSource {
    pub(super) id: String,
    pub(super) title: String,
    pub(super) identity: Identity,
}

fn pixels(dip: i32, dpi: u32) -> i32 {
    (dip * dpi as i32 + 48) / 96
}

fn rect(left: i32, top: i32, right: i32, bottom: i32) -> RECT {
    RECT {
        left,
        top,
        right,
        bottom,
    }
}

fn contains(rect: RECT, point: POINT) -> bool {
    point.x >= rect.left && point.x < rect.right && point.y >= rect.top && point.y < rect.bottom
}

fn physical(area: Bounds) -> RECT {
    rect(area.x, area.y, area.x + area.width, area.y + area.height)
}

pub(super) fn cursor() -> Option<POINT> {
    let mut point = POINT::default();
    (unsafe { GetCursorPos(&mut point) } != 0).then_some(point)
}

struct Layout {
    bounds: Bounds,
    slots: usize,
    card_width: i32,
    card_height: i32,
    title_height: i32,
    picture_inset: i32,
    margin: i32,
    footer: i32,
}

impl Layout {
    fn new(anchor: Bounds, work: Bounds, dpi: u32, count: usize) -> Option<Self> {
        let margin = pixels(8, dpi);
        let available_width = work.width - 4 * margin;
        let card_width = pixels(192, dpi).min(available_width);
        if count == 0 || card_width < pixels(96, dpi) {
            return None;
        }
        let slots =
            count.min(((available_width + margin) / (card_width + margin)).clamp(1, 6) as usize);
        let footer = if count > slots { pixels(28, dpi) } else { 0 };
        let bottom = (work.y + work.height - margin).min(anchor.y - margin);
        let title_height = pixels(28, dpi);
        let card_height =
            (title_height + pixels(116, dpi)).min(bottom - work.y - 3 * margin - footer);
        if card_height < title_height + pixels(32, dpi) {
            return None;
        }
        let width = (slots as i32) * (card_width + margin) + margin;
        let height = card_height + 2 * margin + footer;
        let left = (anchor.x + anchor.width / 2 - width / 2)
            .clamp(work.x + margin, work.x + work.width - width - margin);
        Some(Self {
            bounds: Bounds {
                x: left,
                y: bottom - height,
                width,
                height,
            },
            slots,
            card_width,
            card_height,
            title_height,
            picture_inset: pixels(5, dpi),
            margin,
            footer,
        })
    }

    fn card(&self, slot: usize) -> RECT {
        let left = self.margin + slot as i32 * (self.card_width + self.margin);
        rect(
            left,
            self.margin,
            left + self.card_width,
            self.margin + self.card_height,
        )
    }

    fn picture(&self, slot: usize) -> RECT {
        let card = self.card(slot);
        rect(
            card.left + self.picture_inset,
            card.top + self.title_height,
            card.right - self.picture_inset,
            card.bottom - self.picture_inset,
        )
    }

    fn page_button(&self, next: bool) -> RECT {
        let middle = self.bounds.width / 2;
        let top = self.margin + self.card_height;
        let bottom = top + self.footer;
        if next {
            rect(middle, top, self.bounds.width - self.margin, bottom)
        } else {
            rect(self.margin, top, middle, bottom)
        }
    }
}

struct Thumbnail {
    handle: isize,
    destination: Option<Bounds>,
}

impl Thumbnail {
    fn register(destination: HWND, source: HWND) -> Option<Self> {
        let mut handle = 0;
        let result = unsafe { DwmRegisterThumbnail(destination, source, &mut handle) };
        (result >= 0 && handle != 0).then_some(Self {
            handle,
            destination: None,
        })
    }

    fn update(&mut self, frame: RECT) -> bool {
        let mut size = SIZE::default();
        if unsafe { DwmQueryThumbnailSourceSize(self.handle, &mut size) } < 0
            || size.cx <= 0
            || size.cy <= 0
        {
            return false;
        }
        let width = frame.right - frame.left;
        let height = frame.bottom - frame.top;
        let scale =
            (f64::from(width) / f64::from(size.cx)).min(f64::from(height) / f64::from(size.cy));
        let w = (f64::from(size.cx) * scale).round() as i32;
        let h = (f64::from(size.cy) * scale).round() as i32;
        let destination = Bounds {
            x: frame.left + (width - w) / 2,
            y: frame.top + (height - h) / 2,
            width: w.max(1),
            height: h.max(1),
        };
        if self.destination == Some(destination) {
            return true;
        }
        let properties = DWM_THUMBNAIL_PROPERTIES {
            dwFlags: DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE,
            rcDestination: physical(destination),
            fVisible: 1,
            ..Default::default()
        };
        if unsafe { DwmUpdateThumbnailProperties(self.handle, &properties) } < 0 {
            return false;
        }
        self.destination = Some(destination);
        true
    }
}

impl Drop for Thumbnail {
    fn drop(&mut self) {
        if self.handle != 0 {
            unsafe { DwmUnregisterThumbnail(self.handle) };
        }
    }
}

fn display_allowed(window: HWND) -> bool {
    let mut affinity = WDA_NONE;
    unsafe { GetWindowDisplayAffinity(window, &mut affinity) == 0 || affinity == WDA_NONE }
}

fn unmodified_click(wparam: WPARAM) -> bool {
    let flags = MK_CONTROL | MK_SHIFT | MK_RBUTTON | MK_MBUTTON | MK_XBUTTON1 | MK_XBUTTON2;
    wparam & flags as WPARAM == 0
        && [VK_MENU, VK_LWIN, VK_RWIN]
            .into_iter()
            .all(|key| unsafe { GetKeyState(i32::from(key)) >= 0 })
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Hit {
    Window(usize),
    Page(bool),
}

struct View {
    layout: Layout,
    palette: Palette,
    font: HFONT,
    placeholder: HICON,
    sources: Vec<PreviewSource>,
    page: usize,
    thumbnails: Vec<Option<Thumbnail>>,
    hovered: Option<Hit>,
    pressed: Option<Hit>,
    action: Option<Hit>,
    invalidated: bool,
}

impl View {
    fn page_start(&self) -> usize {
        self.page * self.layout.slots
    }

    fn count(&self) -> usize {
        self.layout
            .slots
            .min(self.sources.len() - self.page_start())
    }

    fn can_page(&self, next: bool) -> bool {
        if next {
            self.page_start() + self.layout.slots < self.sources.len()
        } else {
            self.page > 0
        }
    }

    fn hit(&self, point: POINT) -> Option<Hit> {
        for slot in 0..self.count() {
            if contains(self.layout.card(slot), point) {
                return Some(Hit::Window(slot));
            }
        }
        [false, true]
            .into_iter()
            .find(|&next| self.can_page(next) && contains(self.layout.page_button(next), point))
            .map(Hit::Page)
    }

    fn sync_thumbnails(&mut self, window: HWND) -> bool {
        let mut repaint = false;
        let count = self.count();
        let start = self.page_start();
        self.thumbnails.resize_with(count, || None);
        for slot in 0..count {
            let source = &self.sources[start + slot].identity;
            let thumbnail = &mut self.thumbnails[slot];
            let was_available = thumbnail.is_some();
            if !source.valid() || !display_allowed(source.window()) {
                *thumbnail = None;
                repaint |= was_available;
                continue;
            }
            let picture = self.layout.picture(slot);
            if thumbnail.is_none() {
                *thumbnail = Thumbnail::register(window, source.window());
            }
            if thumbnail
                .as_mut()
                .is_some_and(|thumbnail| !thumbnail.update(picture))
            {
                *thumbnail = None;
            }
            repaint |= was_available != thumbnail.is_some();
        }
        repaint
    }

    fn paint(&self, window: HWND) {
        let (surface, line, tile, hover, picture, text, muted) = match self.palette {
            Palette::Day => (
                0xF5F6FA, 0xCACED9, 0xFFFFFF, 0xE4EAF5, 0xE9EDF4, 0x202637, 0x586579,
            ),
            Palette::Night => (
                0x22252D, 0x525967, 0x2D323E, 0x394558, 0x21252E, 0xF2F4F9, 0xAAB5C8,
            ),
        };
        let mut paint = PAINTSTRUCT::default();
        let dc = unsafe { BeginPaint(window, &mut paint) };
        if dc.is_null() {
            return;
        }
        let old_font = unsafe { SelectObject(dc, self.font) };
        unsafe { SetBkMode(dc, TRANSPARENT as i32) };
        let bounds = rect(0, 0, self.layout.bounds.width, self.layout.bounds.height);
        fill(dc, bounds, surface);
        outline(dc, bounds, line);
        for slot in 0..self.count() {
            let card = self.layout.card(slot);
            let is_hovered = self.hovered == Some(Hit::Window(slot));
            fill(dc, card, if is_hovered { hover } else { tile });
            outline(dc, card, if is_hovered { muted } else { line });
            let image = self.layout.picture(slot);
            fill(dc, image, picture);
            if self.thumbnails.get(slot).is_none_or(Option::is_none) {
                let icon_size = self.layout.title_height;
                unsafe {
                    DrawIconEx(
                        dc,
                        (image.left + image.right - icon_size) / 2,
                        (image.top + image.bottom - icon_size) / 2,
                        self.placeholder,
                        icon_size,
                        icon_size,
                        0,
                        null_mut(),
                        DI_NORMAL,
                    )
                };
            }
            let inset = self.layout.margin;
            draw_text(
                dc,
                &self.sources[self.page_start() + slot].title,
                rect(card.left + inset, card.top, card.right - inset, image.top),
                text,
                DT_END_ELLIPSIS,
            );
        }
        if self.layout.footer > 0 {
            for (next, label) in [(false, "‹"), (true, "›")] {
                let button = self.layout.page_button(next);
                let enabled = self.can_page(next);
                if self.hovered == Some(Hit::Page(next)) && enabled {
                    fill(dc, button, hover);
                }
                draw_text(
                    dc,
                    label,
                    button,
                    if enabled { text } else { muted },
                    DT_CENTER,
                );
            }
        }
        unsafe {
            SelectObject(dc, old_font);
            EndPaint(window, &paint);
        }
    }
}

impl Drop for View {
    fn drop(&mut self) {
        unsafe { DeleteObject(self.font) };
    }
}

fn color(rgb: u32) -> COLORREF {
    ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | (rgb >> 16)
}

fn fill(dc: HDC, region: RECT, rgb: u32) {
    unsafe {
        SetDCBrushColor(dc, color(rgb));
        FillRect(dc, &region, GetStockObject(DC_BRUSH));
    }
}

fn outline(dc: HDC, region: RECT, rgb: u32) {
    unsafe {
        SetDCBrushColor(dc, color(rgb));
        FrameRect(dc, &region, GetStockObject(DC_BRUSH));
    }
}

fn draw_text(dc: HDC, text: &str, mut area: RECT, rgb: u32, flags: u32) {
    let title: Vec<u16> = text.encode_utf16().take(512).collect();
    unsafe {
        SetTextColor(dc, color(rgb));
        DrawTextW(
            dc,
            title.as_ptr(),
            title.len() as i32,
            &mut area,
            DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | flags,
        );
    }
}

pub(super) struct DockPreview {
    pub(super) request_id: u64,
    pub(super) window_ids: Vec<String>,
    pub(super) work: Bounds,
    pub(super) foreground: HWND,
    anchor: Bounds,
    window: HWND,
    view: Box<RefCell<View>>,
    left_at: Option<Instant>,
}

impl DockPreview {
    pub(super) fn show(
        request: DockPreviewLayout,
        work: Bounds,
        dpi: u32,
        palette: Palette,
        sources: Vec<PreviewSource>,
    ) -> Result<Option<Self>> {
        if !cursor().is_some_and(|point| contains(physical(request.anchor), point)) {
            return Ok(None);
        }
        let Some(layout) = Layout::new(request.anchor, work, dpi, sources.len()) else {
            return Ok(None);
        };
        let bounds = layout.bounds;
        let instance = unsafe { GetModuleHandleW(null()) };
        let class = wide(CLASS);
        static REGISTERED: OnceLock<Result<()>> = OnceLock::new();
        REGISTERED
            .get_or_init(|| {
                let registered = WNDCLASSW {
                    lpfnWndProc: Some(window_proc),
                    hInstance: instance,
                    hCursor: unsafe { LoadCursorW(null_mut(), IDC_ARROW) },
                    lpszClassName: class.as_ptr(),
                    ..Default::default()
                };
                if unsafe { RegisterClassW(&registered) } == 0 {
                    return Err(failure("register Dock preview window"));
                }
                Ok(())
            })
            .clone()?;
        let font = unsafe {
            CreateFontW(
                -pixels(13, dpi),
                0,
                0,
                0,
                400,
                0,
                0,
                0,
                DEFAULT_CHARSET as u32,
                0,
                0,
                DEFAULT_QUALITY as u32,
                (DEFAULT_PITCH | FF_DONTCARE) as u32,
                wide("Segoe UI").as_ptr(),
            )
        };
        let view = Box::new(RefCell::new(View {
            layout,
            palette,
            font,
            placeholder: unsafe { LoadIconW(null_mut(), IDI_APPLICATION) },
            sources,
            page: 0,
            thumbnails: Vec::new(),
            hovered: None,
            pressed: None,
            action: None,
            invalidated: false,
        }));
        let foreground = foreground_window();
        let window = unsafe {
            CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                class.as_ptr(),
                wide("Dock").as_ptr(),
                WS_POPUP,
                bounds.x,
                bounds.y,
                bounds.width,
                bounds.height,
                null_mut(),
                null_mut(),
                instance,
                (&*view as *const RefCell<View>).cast(),
            )
        };
        if window.is_null() {
            return Err(failure("create Dock preview window"));
        }
        let preview = Self {
            request_id: request.request_id,
            window_ids: request.window_ids,
            work,
            foreground,
            anchor: request.anchor,
            window,
            view,
            left_at: None,
        };
        preview.view.borrow_mut().sync_thumbnails(window);
        if unsafe {
            SetWindowPos(
                window,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        } == 0
        {
            return Err(failure("show Dock preview window"));
        }
        Ok(Some(preview))
    }

    pub(super) fn update(&self, sources: Vec<PreviewSource>, palette: Palette) -> bool {
        let mut view = self.view.borrow_mut();
        if sources.len() != view.sources.len()
            || sources.iter().zip(&view.sources).any(|(next, last)| {
                next.id != last.id || !next.identity.same_window(&last.identity)
            })
        {
            return false;
        }
        let repaint = view.palette != palette
            || sources
                .iter()
                .zip(&view.sources)
                .any(|(next, last)| next.title != last.title);
        view.palette = palette;
        view.sources = sources;
        let thumbnails_changed = view.sync_thumbnails(self.window);
        if repaint || thumbnails_changed {
            unsafe { InvalidateRect(self.window, null(), 0) };
        }
        true
    }

    pub(super) fn renew(&mut self, request: &DockPreviewLayout, work: Bounds) -> bool {
        if self.work != work
            || self.anchor != request.anchor
            || self.window_ids != request.window_ids
            || self.should_close()
        {
            return false;
        }
        self.request_id = request.request_id;
        true
    }

    pub(super) fn should_close(&mut self) -> bool {
        let mut view = self.view.borrow_mut();
        if view.invalidated || self.foreground != foreground_window() {
            return true;
        }
        let Some(point) = cursor() else {
            return true;
        };
        let in_panel = contains(physical(view.layout.bounds), point);
        if !in_panel {
            view.pressed = None;
            if view.hovered.take().is_some() {
                unsafe { InvalidateRect(self.window, null(), 0) };
            }
        }
        if in_panel || contains(physical(self.anchor), point) {
            self.left_at = None;
            return false;
        }
        self.left_at.get_or_insert_with(Instant::now).elapsed() >= LEAVE_DELAY
            || unsafe {
                GetAsyncKeyState(VK_LBUTTON as i32) < 0 || GetAsyncKeyState(VK_RBUTTON as i32) < 0
            }
    }

    pub(super) fn selection(&self) -> Option<String> {
        let mut view = self.view.borrow_mut();
        match view.action.take()? {
            Hit::Window(slot) => view
                .sources
                .get(view.page_start() + slot)
                .map(|source| source.id.clone()),
            Hit::Page(next) => {
                if view.can_page(next) {
                    view.page = if next { view.page + 1 } else { view.page - 1 };
                    view.thumbnails.clear();
                    view.hovered = None;
                    view.sync_thumbnails(self.window);
                    unsafe { InvalidateRect(self.window, null(), 0) };
                }
                None
            }
        }
    }
}

impl Drop for DockPreview {
    fn drop(&mut self) {
        self.view.borrow_mut().thumbnails.clear();
        unsafe { DestroyWindow(self.window) };
    }
}

unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_NCCREATE => unsafe {
            let creation = &*(lparam as *const CREATESTRUCTW);
            SetWindowLongPtrW(window, GWLP_USERDATA, creation.lpCreateParams as isize);
            return DefWindowProcW(window, message, wparam, lparam);
        },
        WM_MOUSEACTIVATE => return MA_NOACTIVATE as LRESULT,
        WM_ERASEBKGND => return 1,
        _ => {}
    }
    let view =
        unsafe { (GetWindowLongPtrW(window, GWLP_USERDATA) as *const RefCell<View>).as_ref() };
    let Some(view) = view else {
        return unsafe { DefWindowProcW(window, message, wparam, lparam) };
    };
    let point = POINT {
        x: (lparam as i16) as i32,
        y: ((lparam >> 16) as i16) as i32,
    };
    match message {
        WM_PAINT => {
            if let Ok(view) = view.try_borrow() {
                view.paint(window);
            } else {
                return unsafe { DefWindowProcW(window, message, wparam, lparam) };
            }
        }
        WM_MOUSEMOVE => {
            let mut state = view.borrow_mut();
            let hit = state.hit(point);
            if state.hovered != hit {
                state.hovered = hit;
                unsafe { InvalidateRect(window, null(), 0) };
            }
        }
        WM_LBUTTONDOWN => {
            let mut state = view.borrow_mut();
            state.action = None;
            state.pressed = if unmodified_click(wparam) {
                state.hit(point)
            } else {
                None
            };
        }
        WM_LBUTTONUP => {
            let mut state = view.borrow_mut();
            let pressed = state.pressed.take();
            if pressed.is_some() && pressed == state.hit(point) && unmodified_click(wparam) {
                state.action = pressed;
            }
        }
        WM_CANCELMODE | WM_CAPTURECHANGED => view.borrow_mut().pressed = None,
        WM_MOUSEWHEEL => {
            let next = ((wparam >> 16) as i16) < 0;
            view.borrow_mut().action = Some(Hit::Page(next));
        }
        WM_CLOSE | WM_DPICHANGED | WM_DISPLAYCHANGE => view.borrow_mut().invalidated = true,
        _ => return unsafe { DefWindowProcW(window, message, wparam, lparam) },
    }
    0
}
