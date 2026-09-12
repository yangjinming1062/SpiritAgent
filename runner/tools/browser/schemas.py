from typing import Any

BROWSER_NAVIGATE_SCHEMA: dict[str, Any] = {
    "name": "browser_navigate",
    "description": (
        "Navigate to a URL in the browser. Initializes the session and loads the page. "
        "Must be called before other browser tools. For simple information retrieval, prefer "
        "web_search or web_extract (faster, cheaper). For plain-text endpoints — URLs ending in "
        ".md, .txt, .json, .yaml, .yml, .csv, .xml, raw.githubusercontent.com, or any documented API "
        "endpoint — prefer curl via the terminal tool or web_extract; the browser stack is overkill and "
        "much slower for these. Use browser tools when you need to interact with a page (click, fill forms, "
        "dynamic content). Returns a compact page snapshot with interactive elements and ref IDs — no need "
        "to call browser_snapshot separately after navigating. For advanced browser capabilities (file downloads, "
        "multi-tab navigation, cookie injection, viewport / user-agent / geolocation configuration, element-level "
        "screenshots), use `search_tools` to unlock them — those tools are not in the always-visible core set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to navigate to (e.g., 'https://example.com')",
            },
        },
        "required": ["url"],
    },
}

BROWSER_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "name": "browser_snapshot",
    "description": (
        "Get a text-based snapshot of the current page's accessibility tree. Returns interactive "
        "elements with ref IDs (like @e1, @e2) for browser_click and browser_type. full=false (default): "
        "compact view with interactive elements. full=true: complete page content. Snapshots over 8000 "
        "chars are truncated or LLM-summarized. Requires browser_navigate first. Note: browser_navigate "
        "already returns a compact snapshot — use this to refresh after interactions that change the page, "
        "or with full=true for complete content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "full": {
                "type": "boolean",
                "description": (
                    "If true, returns complete page content. If false (default), returns compact view "
                    "with interactive elements only."
                ),
                "default": False,
            },
        },
        "required": [],
    },
}

BROWSER_CLICK_SCHEMA: dict[str, Any] = {
    "name": "browser_click",
    "description": (
        "Click on an element identified by its ref ID from the snapshot (e.g., '@e5'), "
        "visual Set-of-Marks badge number, or directly by physical viewport coordinates 'x,y' "
        "(e.g. '150,300'). Self-healing enabled. Requires browser_navigate and browser_snapshot "
        "to be called first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "Element reference (e.g., '@e5', '@e12'), or physical coordinates 'x,y' (e.g. '240,480')"
                ),
            },
        },
        "required": ["ref"],
    },
}


BROWSER_TYPE_SCHEMA: dict[str, Any] = {
    "name": "browser_type",
    "description": (
        "Type text into an input field identified by its ref ID. Clears the field first, then types the new text. "
        "Requires browser_navigate and browser_snapshot to be called first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "The element reference from the snapshot (e.g., '@e3')",
            },
            "text": {"type": "string", "description": "The text to type into the field"},
        },
        "required": ["ref", "text"],
    },
}

BROWSER_SCROLL_SCHEMA: dict[str, Any] = {
    "name": "browser_scroll",
    "description": (
        "Scroll the page in a direction. Use this to reveal more content that may be below or "
        "above the current viewport. Requires browser_navigate to be called first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "Direction to scroll",
            },
        },
        "required": ["direction"],
    },
}

BROWSER_BACK_SCHEMA: dict[str, Any] = {
    "name": "browser_back",
    "description": (
        "Navigate back to the previous page in browser history. Requires browser_navigate to be called first."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_PRESS_SCHEMA: dict[str, Any] = {
    "name": "browser_press",
    "description": (
        "Press a keyboard key. Useful for submitting forms (Enter), navigating (Tab), or keyboard "
        "shortcuts. Requires browser_navigate to be called first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Key to press (e.g., 'Enter', 'Tab', 'Escape', 'ArrowDown')",
            },
        },
        "required": ["key"],
    },
}

BROWSER_HOVER_SCHEMA: dict[str, Any] = {
    "name": "browser_hover",
    "description": (
        "Hover an element (move mouse over it) — triggers CSS :hover rules, dropdown menus, "
        "and tooltip previews without clicking. Ref IDs come from browser_snapshot output "
        "(e.g. '@e5'). Requires browser_navigate and browser_snapshot first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element reference from the snapshot (e.g. '@e5', '@e12')",
            },
        },
        "required": ["ref"],
    },
}

BROWSER_WAIT_FOR_SCHEMA: dict[str, Any] = {
    "name": "browser_wait_for",
    "description": (
        "Wait until a CSS selector or visible text substring appears in the current page "
        "(polls every 200ms via the live DOM). On a successful match the result includes a "
        "compact snapshot so you can act without a follow-up browser_snapshot. "
        "Set return_snapshot=false to disable the auto-snapshot. Requires browser_navigate first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": (
                    "CSS selector to wait for, e.g. '.checkout-button'. Mutually exclusive gating "
                    "with `text` — at least one must be provided."
                ),
            },
            "text": {
                "type": "string",
                "description": (
                    "Case-insensitive substring of visible element text to wait for, e.g. 'Order confirmed'."
                ),
            },
            "timeout_s": {
                "type": "number",
                "default": 10,
                "description": "Maximum wait in seconds (default 10).",
            },
            "return_snapshot": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), include a compact snapshot in the success result. If false, "
                    "only return the matched element description."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_FIND_SCHEMA: dict[str, Any] = {
    "name": "browser_find",
    "description": (
        "Search the live DOM for elements whose visible text matches a substring. Use this when "
        "you want a snapshot ref by text instead of grepping the previous browser_snapshot output "
        "(which may be stale after dynamic re-rendering). Returns up to 200 matches. "
        "Requires browser_navigate first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": ("Case-insensitive text substring to search for, e.g. 'Sign in' or 'Continue'."),
            },
            "ref_only": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), only return ref IDs. If false, also include tag and text for each match."
                ),
            },
        },
        "required": ["query"],
    },
}

BROWSER_DRAG_SCHEMA: dict[str, Any] = {
    "name": "browser_drag",
    "description": (
        "Drag an element from one snapshot position to another. Dispatches a CDP mouse event "
        "sequence (press → move → release) between the two refs. Works with sortable lists, "
        "sliders, and drag-and-drop UIs. Requires browser_navigate and browser_snapshot first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from_ref": {
                "type": "string",
                "description": "Source element ref from browser_snapshot (e.g. '@e3').",
            },
            "to_ref": {
                "type": "string",
                "description": "Target element ref from browser_snapshot (e.g. '@e7').",
            },
            "hold_key": {
                "type": "string",
                "enum": ["shift", "ctrl", "alt"],
                "description": "Optional modifier key held during the drag.",
            },
        },
        "required": ["from_ref", "to_ref"],
    },
}

BROWSER_SELECT_SCHEMA: dict[str, Any] = {
    "name": "browser_select",
    "description": (
        "Select an option in a <select> element or a common custom dropdown "
        "(Ant Design, Element UI, Material UI, React Select, etc.). For native <select>, sets "
        "the value directly. For custom dropdowns, clicks to open then matches by visible text. "
        "Requires browser_navigate and browser_snapshot first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref of the <select> or custom dropdown trigger (e.g. '@e4').",
            },
            "value": {"type": "string", "description": "Exact option value attribute to select."},
            "label": {
                "type": "string",
                "description": "Case-insensitive substring of the visible option text to select.",
            },
            "index": {"type": "integer", "description": "0-based option index to select."},
            "open_delay_s": {
                "type": "number",
                "default": 0.5,
                "description": (
                    "Seconds to wait after clicking a custom dropdown for its animation to finish "
                    "before searching for options (default 0.5)."
                ),
            },
        },
        "required": ["ref"],
    },
}

BROWSER_DOWNLOAD_SCHEMA: dict[str, Any] = {
    "name": "browser_download",
    "description": (
        "Download a file by clicking a link (ref) or navigating to a URL. Blocks until the "
        "download completes and returns the local file path. Requires a CDP-capable backend. "
        "Files are saved to the browser_downloads cache (24h auto-cleanup)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref_or_url": {
                "type": "string",
                "description": "A snapshot ref (@e5) to click, or a full URL to navigate to.",
            },
            "save_as": {
                "type": "string",
                "description": ("Optional filename override. If omitted, uses the browser's suggested filename."),
            },
            "timeout_s": {
                "type": "number",
                "default": 30,
                "description": "Max seconds to wait for the download to complete (default 30).",
            },
        },
        "required": ["ref_or_url"],
    },
}

BROWSER_PDF_SCHEMA: dict[str, Any] = {
    "name": "browser_pdf",
    "description": (
        "Save the current page as a PDF file. Requires a CDP-capable backend (local Chrome or "
        "CDP override). Returns the file path, page count, and SHA-256 hash."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "save_as": {
                "type": "string",
                "description": "Optional filename (without path). Defaults to page_<id>.pdf.",
            },
            "landscape": {
                "type": "boolean",
                "default": False,
                "description": "If true, use landscape orientation.",
            },
            "print_background": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), include background graphics.",
            },
            "paper_width": {
                "type": "number",
                "default": 8.5,
                "description": "Page width in inches (default 8.5 / Letter).",
            },
            "paper_height": {
                "type": "number",
                "default": 11,
                "description": "Page height in inches (default 11 / Letter).",
            },
        },
        "required": [],
    },
}

BROWSER_SCREENSHOT_ELEMENT_SCHEMA: dict[str, Any] = {
    "name": "browser_screenshot_element",
    "description": (
        "Capture a screenshot of a single element identified by its snapshot ref. Returns the "
        "image file path. Uses getBoundingClientRect for positioning — CSS transforms "
        "(rotate/scale) are not accounted for. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref from browser_snapshot (e.g. '@e5').",
            },
            "save_as": {
                "type": "string",
                "description": "Optional filename (without path). Defaults to element_<id>.png.",
            },
        },
        "required": ["ref"],
    },
}

BROWSER_TAB_NEW_SCHEMA: dict[str, Any] = {
    "name": "browser_tab_new",
    "description": (
        "Open a new browser tab and switch to it. The new tab becomes the active target — "
        "subsequent browser_* calls operate on it. Requires a CDP-capable backend (not Camofox)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": ("Optional URL to navigate the new tab to. If omitted, opens an empty tab."),
            },
        },
        "required": [],
    },
}

BROWSER_TAB_SWITCH_SCHEMA: dict[str, Any] = {
    "name": "browser_tab_switch",
    "description": (
        "Switch the active tab to tab_id. Subsequent CDP operations route there. tab_id values "
        "come from browser_tab_list or browser_tab_new."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tab_id": {"type": "string", "description": "The target tab ID (e.g. 'ABC123...')."},
        },
        "required": ["tab_id"],
    },
}

BROWSER_TAB_CLOSE_SCHEMA: dict[str, Any] = {
    "name": "browser_tab_close",
    "description": (
        "Close a tab. Defaults to closing the currently active tab. After close, CDP routing "
        "falls back to the initial page if the closed tab was active."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "Tab ID to close. If omitted, closes the active tab.",
            },
        },
        "required": [],
    },
}

BROWSER_TAB_LIST_SCHEMA: dict[str, Any] = {
    "name": "browser_tab_list",
    "description": (
        "List all browser tabs currently open. Read-only — does not mutate state. Returns "
        "tab_id, url, title, and active_tab_id."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_SET_VIEWPORT_SCHEMA: dict[str, Any] = {
    "name": "browser_set_viewport",
    "description": (
        "Override the browser viewport size. Persists until next call or page reload. "
        "Use to test mobile layouts without a real device. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "width": {"type": "integer", "description": "Viewport width in CSS pixels."},
            "height": {"type": "integer", "description": "Viewport height in CSS pixels."},
            "device_scale_factor": {
                "type": "number",
                "default": 1.0,
                "description": "Device pixel ratio (default 1.0).",
            },
            "mobile": {
                "type": "boolean",
                "default": False,
                "description": "If true, the browser reports a mobile UA and viewport.",
            },
        },
        "required": ["width", "height"],
    },
}

BROWSER_SET_USER_AGENT_SCHEMA: dict[str, Any] = {
    "name": "browser_set_user_agent",
    "description": ("Override the user-agent string sent on subsequent navigations. Pass None to clear the override."),
    "parameters": {
        "type": "object",
        "properties": {
            "user_agent": {
                "type": "string",
                "description": "Full UA override (e.g. 'Mozilla/5.0 ... Mobile/15E148 Safari/604.1').",
            },
            "platform": {
                "type": "string",
                "description": "Optional navigator.platform value (e.g. 'iPhone').",
            },
            "accept_language": {
                "type": "string",
                "description": "Optional Accept-Language header (e.g. 'en-US,en;q=0.9').",
            },
        },
        "required": [],
    },
}

BROWSER_SET_EXTRA_HEADERS_SCHEMA: dict[str, Any] = {
    "name": "browser_set_extra_headers",
    "description": (
        "Replace all extra HTTP headers sent on subsequent navigations. "
        "Wholesale replacement — pass the complete desired "
        "set. Empty dict clears all overrides."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "headers": {
                "type": "object",
                "description": (
                    'Header name → value map, e.g. {"Referer": "https://example.com", "X-API-Key": "secret"}.'
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["headers"],
    },
}

BROWSER_SET_GEOLOCATION_SCHEMA: dict[str, Any] = {
    "name": "browser_set_geolocation",
    "description": (
        "Override browser-reported geolocation. "
        "Subsequent pages see injected coords via navigator.geolocation. Pass lat=NaN to clear."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lat": {
                "type": "number",
                "description": "Latitude in decimal degrees. NaN clears the override.",
            },
            "lon": {"type": "number", "description": "Longitude in decimal degrees."},
            "accuracy": {
                "type": "number",
                "default": 100,
                "description": "Accuracy in meters (default 100).",
            },
        },
        "required": ["lat", "lon"],
    },
}

BROWSER_GET_IMAGES_SCHEMA: dict[str, Any] = {
    "name": "browser_get_images",
    "description": (
        "Get a list of all images on the current page with their URLs and alt text. Useful "
        "for finding images to analyze with the vision tool. Requires browser_navigate to be "
        "called first."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_VISION_SCHEMA: dict[str, Any] = {
    "name": "browser_vision",
    "description": (
        "Take a screenshot of the current page and attach it to your context so you can "
        "inspect it visually on your next turn. Use this when you need to understand what "
        "the page looks like - especially for CAPTCHAs, visual verification challenges, "
        "Canvas/WebGL graphics, complex layouts, or cases where the text snapshot misses "
        "important visual information. Set annotate=true to inject high-contrast Set-of-Marks "
        "[N] visual badges on all interactive elements. Includes a screenshot_path that you "
        "can share with the user by including MEDIA:<screenshot_path> in your response. "
        "Requires browser_navigate to be called first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "annotate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, overlay high-contrast numbered [N] badges on all interactive "
                    "elements and return structured coordinate index table. Each [N] maps to "
                    "ref @vN for subsequent click/type commands. AXTree-based refs (@eN) "
                    "coexist separately and are produced by browser_snapshot."
                ),
            },
        },
        "required": [],
    },
}


BROWSER_CONSOLE_SCHEMA: dict[str, Any] = {
    "name": "browser_console",
    "description": (
        "Get browser console output and JavaScript errors from the current page. Returns "
        "console.log/warn/error/info messages and uncaught JS exceptions. Use this to detect "
        "silent JavaScript errors, failed API calls, and application warnings. Requires "
        "browser_navigate to be called first. When 'expression' is provided, evaluates "
        "JavaScript in the page context and returns the result — use this for DOM inspection, "
        "reading page state, or extracting data programmatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "clear": {
                "type": "boolean",
                "default": False,
                "description": "If true, clear the message buffers after reading",
            },
            "expression": {
                "type": "string",
                "description": (
                    "JavaScript expression to evaluate in the page context. Runs in the browser "
                    "like DevTools console — full access to DOM, window, document. Return values "
                    "are serialized to JSON. Example: 'document.title' or "
                    "'document.querySelectorAll(\"a\").length'"
                ),
            },
        },
        "required": [],
    },
}

BROWSER_COOKIES_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_get",
    "description": (
        "Read all cookies visible to the current page, optionally filtered by URL. "
        "Read-only — does not mutate state. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "Optional URL whose cookies to retrieve (e.g. 'https://example.com'). If "
                    "omitted, returns all cookies for the current browser context."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_COOKIES_SET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_set",
    "description": (
        "Set a cookie. Useful for re-establishing session state after restart, or injecting "
        "auth tokens for testing. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Cookie name."},
            "value": {"type": "string", "description": "Cookie value."},
            "domain": {"type": "string", "description": "Cookie domain (e.g. 'example.com')."},
            "path": {"type": "string", "default": "/", "description": "Cookie path (default '/')."},
            "expires": {
                "type": "number",
                "description": "Expiration as UNIX timestamp. Omit for a session cookie.",
            },
            "httpOnly": {
                "type": "boolean",
                "default": False,
                "description": "If true, the cookie is not accessible via JavaScript.",
            },
            "secure": {
                "type": "boolean",
                "default": False,
                "description": "If true, the cookie is only sent over HTTPS.",
            },
            "sameSite": {
                "type": "string",
                "enum": ["Strict", "Lax", "None"],
                "description": "SameSite policy. Defaults to None (browser default).",
            },
        },
        "required": ["name", "value", "domain"],
    },
}

BROWSER_COOKIES_CLEAR_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_clear",
    "description": (
        "Clear browser cookies and/or storage. WARNING: scope is GLOBAL, not the current "
        "origin — this affects every site the browser has visited. "
        "By default clears both session cookies and all storage data. Pass session=False "
        "and storage=False to no-op (useful for explicit intent signalling)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), clear all session cookies across all origins.",
            },
            "storage": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), clear localStorage / sessionStorage / indexedDB across all origins."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_STORAGE_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_storage_get",
    "description": (
        "Read the value of a localStorage or sessionStorage entry from a specific origin. "
        "Read-only. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "origin": {
                "type": "string",
                "description": "Origin whose storage to read (e.g. 'https://example.com').",
            },
            "kind": {
                "type": "string",
                "enum": ["localStorage", "sessionStorage"],
                "default": "localStorage",
                "description": "Which storage tier to read (default localStorage).",
            },
        },
        "required": ["key", "origin"],
    },
}

BROWSER_STORAGE_SET_SCHEMA: dict[str, Any] = {
    "name": "browser_storage_set",
    "description": (
        "Set the value of a localStorage / sessionStorage entry for a specific origin. "
        "Mutates page state. Requires a CDP-capable backend."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "value": {"type": "string", "description": "Value to store."},
            "origin": {
                "type": "string",
                "description": "Target origin (e.g. 'https://example.com').",
            },
            "kind": {
                "type": "string",
                "enum": ["localStorage", "sessionStorage"],
                "default": "localStorage",
                "description": "Which storage tier to write (default localStorage).",
            },
        },
        "required": ["key", "value", "origin"],
    },
}

BROWSER_DIALOG_SCHEMA: dict[str, Any] = {
    "name": "browser_dialog",
    "description": (
        "Respond to a native JavaScript dialog (alert / confirm / prompt / beforeunload) that "
        "is currently blocking the page.\n\n"
        "**Workflow:** call ``browser_snapshot`` first — if a dialog is open, it appears in "
        "the ``pending_dialogs`` field with ``id``, ``type``, and ``message``. Then call this "
        "tool with ``action='accept'`` or ``action='dismiss'``.\n\n"
        "**Prompt dialogs:** pass ``prompt_text`` to supply the response string. Ignored for "
        "alert/confirm/beforeunload.\n\n"
        "**Multiple dialogs:** if more than one dialog is queued (rare — happens when a second "
        "dialog fires while the first is still open), pass ``dialog_id`` from the snapshot "
        "to disambiguate.\n\n"
        "**Availability:** only present when a CDP-capable backend is attached — local "
        "Chromium-family browser via ``/browser connect``, or ``browser.cdp_url`` in Desktop "
        "settings. Not available on Camofox (REST-only) or the default Playwright local browser "
        "(CDP port is hidden)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": (
                    "'accept' clicks OK / returns the prompt text. 'dismiss' clicks Cancel / "
                    "returns null from prompt(). For ``beforeunload`` dialogs: 'accept' allows "
                    "the navigation, 'dismiss' keeps the page."
                ),
            },
            "prompt_text": {
                "type": "string",
                "description": (
                    "Response string for a ``prompt()`` dialog. Ignored for other dialog types. "
                    "Defaults to empty string."
                ),
            },
            "dialog_id": {
                "type": "string",
                "description": (
                    "Specific dialog to respond to, from ``browser_snapshot.pending_dialogs[].id``. "
                    "Required only when multiple dialogs are queued."
                ),
            },
        },
        "required": ["action"],
    },
}

BROWSER_CDP_SCHEMA: dict[str, Any] = {
    "name": "browser_cdp",
    "description": (
        "Send a raw Chrome DevTools Protocol (CDP) command. Escape hatch for browser "
        "operations not covered by browser_navigate, browser_click, browser_console, etc.\n\n"
        "**Requires a reachable CDP endpoint.** Available when the user has run "
        "'/browser connect' to attach to a running Chrome, Brave, Chromium, or Edge "
        "browser, or when 'browser.cdp_url' is set in Desktop settings. If the tool is "
        "in your toolset at all, a CDP endpoint is already reachable.\n\n"
        "**CDP method reference:** https://chromedevtools.github.io/devtools-protocol/"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": (
                    "CDP method name, e.g. 'Target.getTargets', 'Runtime.evaluate', 'Page.handleJavaScriptDialog'."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Method-specific parameters as a JSON object. Omit or pass {} for methods that take no parameters."
                ),
                "properties": {},
                "additionalProperties": True,
            },
            "target_id": {
                "type": "string",
                "description": (
                    "Optional. Target/tab ID from Target.getTargets result "
                    "(each entry's 'targetId'). Use for page-level methods at the top-level tab "
                    "scope. Mutually exclusive with frame_id."
                ),
            },
            "frame_id": {
                "type": "string",
                "description": (
                    "Optional. Out-of-process iframe (OOPIF) frame_id from "
                    "browser_snapshot.frame_tree.children[] where is_oopif=true. When set, "
                    "routes the call through the CDP supervisor's live session for that iframe."
                ),
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default 30, max 300).",
                "default": 30,
            },
        },
        "required": ["method"],
    },
}

BROWSER_BATCH_SCHEMA: dict[str, Any] = {
    "name": "browser_batch",
    "description": (
        "Execute a sequence of browser actions in a single round trip to minimize latency. "
        "Supported actions: 'click' (by ref, badge [N], or 'x,y'), 'type' (text into input), "
        "'press' (key like Enter/Tab), 'hover', 'scroll' (direction/pixels), 'wait' (seconds), "
        "'select' (dropdown). Executes actions sequentially with configurable inter-action "
        "pauses (wait_between_ms) and a final post-batch page settlement check. Returns "
        "execution details and optional post-batch snapshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": (
                    "List of action objects to execute in order. "
                    "Examples: [{'action': 'click', 'ref': '@e2'}, "
                    "{'action': 'type', 'ref': '@e3', 'text': 'search'}, "
                    "{'action': 'press', 'key': 'Enter'}]."
                ),
                "items": {"type": "object"},
            },
            "return_snapshot": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), returns a fresh compact page snapshot after all actions complete.",
            },
            "wait_between_ms": {
                "type": "integer",
                "default": 100,
                "description": "Milliseconds to pause between consecutive actions (default 100).",
            },
        },
        "required": ["actions"],
    },
}
