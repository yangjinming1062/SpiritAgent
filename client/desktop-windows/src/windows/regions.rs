use super::*;
use windows_sys::Win32::Graphics::Gdi::*;

const MAX_RECTS: usize = 2048;

struct Region(HRGN);

impl Region {
    fn empty() -> Result<Self> {
        let handle = unsafe { CreateRectRgn(0, 0, 0, 0) };
        if handle.is_null() {
            return Err(failure("create window region"));
        }
        Ok(Self(handle))
    }

    fn rectangle(bounds: Bounds) -> Result<Self> {
        let handle = unsafe {
            CreateRectRgn(
                bounds.x,
                bounds.y,
                bounds.x + bounds.width,
                bounds.y + bounds.height,
            )
        };
        if handle.is_null() {
            return Err(failure("create rectangular window region"));
        }
        Ok(Self(handle))
    }

    fn combine(&self, other: &Self, operation: RGN_COMBINE_MODE) -> Result<()> {
        if unsafe { CombineRgn(self.0, self.0, other.0, operation) } == 0 {
            return Err(failure("combine window regions"));
        }
        Ok(())
    }
}

impl Drop for Region {
    fn drop(&mut self) {
        unsafe { DeleteObject(self.0) };
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedRegion {
    pub identity: Identity,
    rectangles: Option<Vec<Bounds>>,
}

impl SavedRegion {
    pub(super) fn capture(identity: &Identity) -> Result<Self> {
        let region = Region::empty()?;
        let rectangles = if unsafe { GetWindowRgn(identity.window(), region.0) } == 0 {
            None
        } else {
            let size = unsafe { GetRegionData(region.0, 0, null_mut()) } as usize;
            if size < size_of::<RGNDATAHEADER>()
                || size > size_of::<RGNDATAHEADER>() + MAX_RECTS * size_of::<RECT>()
            {
                return Err("window region exceeds recovery budget".into());
            }
            let mut bytes = vec![0u8; size];
            if unsafe { GetRegionData(region.0, size as u32, bytes.as_mut_ptr().cast()) } != size as u32 {
                return Err(failure("read window region"));
            }
            let header = unsafe { std::ptr::read_unaligned(bytes.as_ptr().cast::<RGNDATAHEADER>()) };
            if header.dwSize as usize != size_of::<RGNDATAHEADER>()
                || header.nCount as usize > MAX_RECTS
                || size != size_of::<RGNDATAHEADER>() + header.nCount as usize * size_of::<RECT>()
            {
                return Err("invalid window region data".into());
            }
            let mut rectangles = Vec::new();
            for index in 0..header.nCount as usize {
                let rect = unsafe {
                    std::ptr::read_unaligned(
                        bytes
                            .as_ptr()
                            .add(size_of::<RGNDATAHEADER>() + index * size_of::<RECT>())
                            .cast::<RECT>(),
                    )
                };
                rectangles.push(Bounds {
                    x: rect.left,
                    y: rect.top,
                    width: rect.right - rect.left,
                    height: rect.bottom - rect.top,
                });
            }
            Some(rectangles)
        };
        let saved = Self {
            identity: identity.clone(),
            rectangles,
        };
        saved.validate()?;
        Ok(saved)
    }

    pub(super) fn validate(&self) -> Result<()> {
        if self.identity.handle == 0
            || self.rectangles.as_ref().is_some_and(|rectangles| {
                rectangles.len() > MAX_RECTS || rectangles.iter().any(|rect| !rect.valid())
            })
        {
            return Err("invalid recovery window region".into());
        }
        Ok(())
    }

    fn original(&self, bounds: Bounds) -> Result<Region> {
        let Some(rectangles) = &self.rectangles else {
            return Region::rectangle(Bounds {
                x: 0,
                y: 0,
                width: bounds.width,
                height: bounds.height,
            });
        };
        let region = Region::empty()?;
        for rectangle in rectangles {
            region.combine(&Region::rectangle(*rectangle)?, RGN_OR)?;
        }
        Ok(region)
    }

    pub(super) fn restore(&self) -> Result<()> {
        if !self.identity.valid() {
            return Ok(());
        }
        let region = if self.rectangles.is_some() {
            Some(self.original(rectangle(self.identity.window())?)?)
        } else {
            None
        };
        set(self.identity.window(), region)
    }

    pub(super) fn restore_resized(&self, original: Bounds) -> Result<()> {
        if !self.identity.valid() {
            return Ok(());
        }
        let actual = rectangle(self.identity.window())?;
        if actual.width == original.width && actual.height == original.height {
            return self.restore();
        }
        if !actual.valid() || !original.valid() {
            return Err("invalid system tray recovery region dimensions".into());
        }
        let Some(rectangles) = &self.rectangles else {
            return set(self.identity.window(), None);
        };
        let region = Region::empty()?;
        for source in rectangles {
            let left = source.x.max(0).min(original.width);
            let top = source.y.max(0).min(original.height);
            let right = (source.x + source.width).max(0).min(original.width);
            let bottom = (source.y + source.height).max(0).min(original.height);
            if right <= left || bottom <= top {
                continue;
            }
            let x = i64::from(left) * i64::from(actual.width) / i64::from(original.width);
            let y = i64::from(top) * i64::from(actual.height) / i64::from(original.height);
            let end_x = (i64::from(right) * i64::from(actual.width) + i64::from(original.width) - 1)
                / i64::from(original.width);
            let end_y = (i64::from(bottom) * i64::from(actual.height) + i64::from(original.height) - 1)
                / i64::from(original.height);
            region.combine(&Region::rectangle(Bounds {
                x: x as i32,
                y: y as i32,
                width: (end_x - x) as i32,
                height: (end_y - y) as i32,
            })?, RGN_OR)?;
        }
        set(self.identity.window(), Some(region))
    }

    pub(super) fn clip(&self, screen: Option<Bounds>) -> Result<()> {
        if !self.identity.valid() {
            return Err("window identity changed before clipping".into());
        }
        let bounds = rectangle(self.identity.window())?;
        let region = self.original(bounds)?;
        let mask = match screen {
            Some(screen) => Region::rectangle(Bounds {
                x: screen.x - bounds.x,
                y: screen.y - bounds.y,
                width: screen.width,
                height: screen.height,
            })?,
            None => Region::empty()?,
        };
        region.combine(&mask, RGN_AND)?;
        set(self.identity.window(), Some(region))
    }
}

fn set(window: HWND, region: Option<Region>) -> Result<()> {
    let current = Region::empty()?;
    let has_current = unsafe { GetWindowRgn(window, current.0) } != 0;
    if match &region {
        Some(region) => has_current && unsafe { EqualRgn(current.0, region.0) } != 0,
        None => !has_current,
    } {
        return Ok(());
    }
    let handle = region.as_ref().map_or(null_mut(), |region| region.0);
    if unsafe { SetWindowRgn(window, handle, 1) } == 0 {
        return Err(failure("set window region"));
    }
    if let Some(region) = region {
        // 成功后区域归 Windows 所有。
        std::mem::forget(region);
    }
    Ok(())
}
