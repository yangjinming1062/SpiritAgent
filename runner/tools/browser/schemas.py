from typing import Any

BROWSER_NAVIGATE_SCHEMA: dict[str, Any] = {
    "name": "browser_navigate",
    "description": (
        "Navigate the current tab to a URL, starting the browser if needed. Call this before other browser "
        "tools. For simple information retrieval, prefer web_search or web_extract (faster, cheaper). For "
        "plain-text resources — URLs ending in .md, .txt, .json, .yaml, .yml, .csv, .xml, "
        "raw.githubusercontent.com, or documented API endpoints — prefer web_extract or curl in the terminal; "
        "the browser is much slower for these. Use the browser when you need to interact with a page (click, "
        "fill forms, dynamic content). Only http and https URLs are accepted. Returns the final URL, the title "
        "and a compact snapshot of interactive elements with ref IDs, so no separate browser_snapshot is needed "
        "right after navigating. If other browser tools you need (tabs, downloads, cookies, emulation, element "
        "screenshots, PDF) are not available yet, find them with search_tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The http or https URL to open (e.g. 'https://example.com').",
            },
        },
        "required": ["url"],
    },
}

BROWSER_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "name": "browser_snapshot",
    "description": (
        "Get a text snapshot of the current page's accessibility tree. Elements are tagged [ref=eN]; pass "
        "them as '@eN' to browser_click, browser_type and other element tools. full=false (default) lists "
        "interactive elements only; full=true includes the page text as well. Snapshots over 8000 characters "
        "are truncated. browser_navigate already returns a compact snapshot — use this to refresh after the "
        "page changes, or with full=true to read content. The result also includes pending_dialogs "
        "(JavaScript dialogs waiting for browser_dialog) and a frame summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "full": {
                "type": "boolean",
                "description": (
                    "If true, include all page content. If false (default), list interactive elements only."
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
        "Click an element by its ref ID from the latest snapshot (e.g. '@e5'), by an '@vN' ref from an "
        "annotated browser_vision screenshot, or at viewport coordinates 'x,y' in CSS pixels "
        "(e.g. '150,300'). Prefer refs; coordinates are best taken from annotated browser_vision output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "Element ref ('@e5'), annotated screenshot ref ('@v3'), or viewport coordinates 'x,y' "
                    "(e.g. '240,480')."
                ),
            },
        },
        "required": ["ref"],
    },
}

BROWSER_TYPE_SCHEMA: dict[str, Any] = {
    "name": "browser_type",
    "description": (
        "Type text into an input field identified by its ref ID (or 'x,y' coordinates). Clears the field "
        "first, then inserts the new text."
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
    "description": "Scroll the page up or down by about 500 pixels to reveal content outside the current viewport.",
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
    "description": "Go back to the previous page in the current tab's history.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_PRESS_SCHEMA: dict[str, Any] = {
    "name": "browser_press",
    "description": (
        "Press a single named key in the page: Enter, Tab, Escape, Backspace, Delete, Space, ArrowUp, "
        "ArrowDown, ArrowLeft, ArrowRight, PageUp, PageDown, Home, End or F1–F12. Key combinations and "
        "character keys are not supported; use browser_type to enter text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Key name, e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown'.",
            },
        },
        "required": ["key"],
    },
}

BROWSER_HOVER_SCHEMA: dict[str, Any] = {
    "name": "browser_hover",
    "description": (
        "Move the mouse over an element without clicking, triggering :hover styles, dropdown menus and "
        "tooltips. Accepts a snapshot ref (e.g. '@e5') or 'x,y' coordinates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref from the snapshot (e.g. '@e5') or 'x,y' coordinates.",
            },
        },
        "required": ["ref"],
    },
}

BROWSER_WAIT_FOR_SCHEMA: dict[str, Any] = {
    "name": "browser_wait_for",
    "description": (
        "Wait until a CSS selector matches or a text substring appears in the page's visible text "
        "(checked every 200 ms). Provide selector, text, or both; either match succeeds. On success the "
        "result includes a compact snapshot unless return_snapshot=false."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector to wait for, e.g. '.checkout-button'.",
            },
            "text": {
                "type": "string",
                "description": "Case-insensitive substring of the page's visible text, e.g. 'Order confirmed'.",
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
                    "only report which condition matched."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_FIND_SCHEMA: dict[str, Any] = {
    "name": "browser_find",
    "description": (
        "Search the live DOM for visible elements whose text contains a substring and return their refs "
        "(up to 200 matches). Refs are the ones assigned by the most recent snapshot; elements rendered "
        "after it have none, so take a new browser_snapshot if nothing matches."
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
                    "If true (default), return only elements that have refs, as ref IDs. If false, return "
                    "every match with its ref (possibly empty), tag and text."
                ),
            },
        },
        "required": ["query"],
    },
}

BROWSER_DRAG_SCHEMA: dict[str, Any] = {
    "name": "browser_drag",
    "description": (
        "Drag from one element to another with a mouse press, move and release. Works with sortable "
        "lists, sliders and drag-and-drop UIs. Accepts snapshot refs or 'x,y' coordinates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from_ref": {
                "type": "string",
                "description": "Source element ref (e.g. '@e3') or 'x,y' coordinates.",
            },
            "to_ref": {
                "type": "string",
                "description": "Target element ref (e.g. '@e7') or 'x,y' coordinates.",
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
        "Select an option in a native <select> or a custom dropdown. A native <select> is set directly; "
        "a custom dropdown is clicked open and the option is matched by its visible text. Give one of "
        "value, label or index (value takes precedence, then label)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref of the <select> or custom dropdown trigger (e.g. '@e4').",
            },
            "value": {"type": "string", "description": "Exact option value, or exact option text."},
            "label": {
                "type": "string",
                "description": "Case-insensitive substring of the visible option text to select.",
            },
            "index": {"type": "integer", "description": "0-based option index."},
        },
        "required": ["ref"],
    },
}

BROWSER_DOWNLOAD_SCHEMA: dict[str, Any] = {
    "name": "browser_download",
    "description": (
        "Download a file by clicking a link (ref) or opening a URL in the current tab. Waits until the "
        "download completes and returns the local file path. Files are saved in the local downloads "
        "folder; an existing file with the same name is replaced."
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
                "description": "Optional file name (directory parts are ignored). Defaults to the browser's suggested name.",
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
    "description": "Save the current page as a PDF file. Returns the local file path and its SHA-256 hash.",
    "parameters": {
        "type": "object",
        "properties": {
            "save_as": {
                "type": "string",
                "description": "Optional file name (directory parts are ignored). Defaults to page_<id>.pdf.",
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
        "Capture a screenshot of a single element identified by its snapshot ref and attach it for visual "
        "inspection. The PNG is also saved locally and its path is included; attaching it does not deliver "
        "the image to the user."
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
                "description": "Optional file name (directory parts are ignored). Defaults to element_<id>.png.",
            },
        },
        "required": ["ref"],
    },
}

BROWSER_TAB_NEW_SCHEMA: dict[str, Any] = {
    "name": "browser_tab_new",
    "description": "Open a new browser tab and switch to it; subsequent browser tools operate on the new tab.",
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
        "Switch the active tab; subsequent browser tools operate on it. tab_id values come from "
        "browser_tab_list or browser_tab_new."
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
        "Close a tab, by default the active one. If the active tab is closed, another open tab becomes active."
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
    "description": "List the open browser tabs with each tab's tab_id, url, title and is_active.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_SET_VIEWPORT_SCHEMA: dict[str, Any] = {
    "name": "browser_set_viewport",
    "description": (
        "Override the viewport size of the current tab until it is changed again. Use it to test "
        "responsive or mobile layouts."
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
                "description": (
                    "If true, emulate a mobile device (viewport meta tag, overlay scrollbars). "
                    "The user agent is not changed; use browser_set_user_agent for that."
                ),
            },
        },
        "required": ["width", "height"],
    },
}

BROWSER_SET_USER_AGENT_SCHEMA: dict[str, Any] = {
    "name": "browser_set_user_agent",
    "description": (
        "Override the user agent (and optionally navigator.platform and Accept-Language) for the current "
        "tab. Provide at least one field; an empty user_agent removes the user-agent override."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_agent": {
                "type": "string",
                "description": (
                    "Full user-agent string (e.g. 'Mozilla/5.0 ... Mobile/15E148 Safari/604.1'); an empty "
                    "string or omission keeps the browser's default."
                ),
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
        "Set extra HTTP headers sent with every request from the current tab, replacing any set before. "
        "Pass the complete set each time; {} clears them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "headers": {
                "type": "object",
                "description": (
                    'Header name → value map, e.g. {"Referer": "https://example.com", "Accept-Language": "fr-FR"}.'
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
        "Override the geolocation reported to pages in the current tab and allow sites to read it via "
        "navigator.geolocation. Omit lat and lon to remove the override."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lat": {
                "type": "number",
                "description": "Latitude in decimal degrees.",
            },
            "lon": {"type": "number", "description": "Longitude in decimal degrees."},
            "accuracy": {
                "type": "number",
                "default": 100,
                "description": "Accuracy in meters (default 100).",
            },
        },
        "required": [],
    },
}

BROWSER_GET_IMAGES_SCHEMA: dict[str, Any] = {
    "name": "browser_get_images",
    "description": "List the images on the current page with their URL, alt text, natural size and ref.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BROWSER_VISION_SCHEMA: dict[str, Any] = {
    "name": "browser_vision",
    "description": (
        "Take a screenshot of the current viewport and attach it for visual inspection. Use this when you "
        "need to see the page — CAPTCHAs, visual challenges, Canvas/WebGL graphics, complex layouts, or "
        "anything the text snapshot misses. Set annotate=true to overlay numbered badges on interactive "
        "elements. The PNG is also saved locally and its path is included; attaching it does not deliver "
        "the image to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "annotate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, overlay numbered [N] badges on interactive elements and list each badge "
                    "with its ref '@vN' and viewport coordinates, followed by a compact snapshot. Use "
                    "'@vN' refs with click, type and other element tools; '@eN' refs from browser_snapshot "
                    "stay valid alongside them."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_CONSOLE_SCHEMA: dict[str, Any] = {
    "name": "browser_console",
    "description": (
        "Read recent console messages (console.log/warn/error/info, up to the last 50) and uncaught "
        "JavaScript exceptions from the browser session. When 'expression' is provided, evaluate "
        "JavaScript in the current page instead and return its result — use this for DOM inspection, "
        "reading page state, or extracting data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "clear": {
                "type": "boolean",
                "default": False,
                "description": "If true, clear the message buffer after reading. Ignored when expression is set.",
            },
            "expression": {
                "type": "string",
                "description": (
                    "JavaScript expression to evaluate in the page, like the DevTools console. Promises "
                    "are awaited and the value is returned as JSON. Example: 'document.title' or "
                    "'document.querySelectorAll(\"a\").length'."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_COOKIES_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_get",
    "description": "Read the cookies that apply to the current page and its frames, or to a given URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "Optional URL whose cookies to read (e.g. 'https://example.com'). If omitted, uses the "
                    "current page and its frames."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_COOKIES_SET_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_set",
    "description": (
        "Set a cookie in the browser, e.g. to restore a login session or inject a test token. The result "
        "warns when the domain does not match the current page."
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
                "description": "Expiration as a UNIX timestamp in seconds. Omit for a session cookie.",
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
                "description": "SameSite policy; omit to use the browser default. 'None' requires secure=true.",
            },
        },
        "required": ["name", "value", "domain"],
    },
}

BROWSER_COOKIES_CLEAR_SCHEMA: dict[str, Any] = {
    "name": "browser_cookies_clear",
    "description": (
        "Clear browser cookies and/or site storage. WARNING: this is global, not limited to the current "
        "site — it affects every site the browser has visited and logs the browser out everywhere. By "
        "default clears both."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cookies": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), delete all cookies for all sites.",
            },
            "storage": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), clear localStorage, IndexedDB, cache storage and other site data for all sites."
                ),
            },
        },
        "required": [],
    },
}

BROWSER_STORAGE_GET_SCHEMA: dict[str, Any] = {
    "name": "browser_storage_get",
    "description": (
        "Read a localStorage or sessionStorage entry of a site origin. sessionStorage is read from the current tab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "origin": {
                "type": "string",
                "description": "Origin whose storage to read, e.g. 'https://example.com' (any path is ignored).",
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
        "Set a localStorage or sessionStorage entry for a site origin. sessionStorage is written in the current tab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Storage entry name."},
            "value": {"type": "string", "description": "Value to store."},
            "origin": {
                "type": "string",
                "description": "Target origin, e.g. 'https://example.com' (any path is ignored).",
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
        "Respond to a JavaScript dialog (alert / confirm / prompt / beforeunload) that is blocking the "
        "page. Open dialogs are reported with id, type and message by the click that opened them, in "
        "browser_snapshot's pending_dialogs, and in results of page actions they blocked; answer with "
        "action='accept' or 'dismiss'. If several are queued, pass dialog_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": (
                    "'accept' clicks OK (prompt() returns prompt_text); 'dismiss' clicks Cancel (prompt() "
                    "returns null). For beforeunload, 'accept' leaves the page and 'dismiss' stays."
                ),
            },
            "prompt_text": {
                "type": "string",
                "description": (
                    "Text returned by a prompt() dialog when accepted. Ignored for other dialog types. "
                    "Defaults to an empty string."
                ),
            },
            "dialog_id": {
                "type": "string",
                "description": (
                    "id from browser_snapshot pending_dialogs. Needed only when several dialogs are queued."
                ),
            },
        },
        "required": ["action"],
    },
}

BROWSER_CDP_SCHEMA: dict[str, Any] = {
    "name": "browser_cdp",
    "description": (
        "Send a raw Chrome DevTools Protocol (CDP) command to the browser session and return its response. "
        "Use it only for operations the other browser tools do not cover. Events are not returned, and "
        "methods that would close the browser, wipe all site data, change the download directory, "
        "intercept requests (Fetch) or grant permissions are refused.\n\n"
        "CDP method reference: https://chromedevtools.github.io/devtools-protocol/"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": "CDP method name, e.g. 'Target.getTargets', 'Runtime.evaluate', 'DOM.getDocument'.",
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
                    "Optional target to send the command to, from Target.getTargets (a tab, or an "
                    "out-of-process iframe with type 'iframe'). Defaults to the active tab."
                ),
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default 30, clamped to 1–300).",
                "default": 30,
            },
        },
        "required": ["method"],
    },
}

BROWSER_BATCH_SCHEMA: dict[str, Any] = {
    "name": "browser_batch",
    "description": (
        "Run a sequence of page actions in one call, in order. Each action object has an 'action' field "
        "and its own fields: click {ref}, type {ref, text}, press {key}, hover {ref}, scroll {direction: "
        "up|down|left|right, pixels (default 500)}, wait {seconds (max 10)}, select {ref, and one of value, "
        "label or index}. A ref may be '@eN', '@vN' or 'x,y' (select needs an element ref). Execution stops "
        "at the first failing action; the result then gives the error, the failing step index and the "
        "results of the steps attempted. On success it returns per-step details and, by default, a compact "
        "snapshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": (
                    "Action objects to execute in order, e.g. [{'action': 'click', 'ref': '@e2'}, "
                    "{'action': 'type', 'ref': '@e3', 'text': 'search'}, {'action': 'press', 'key': 'Enter'}]."
                ),
                "items": {"type": "object"},
            },
            "return_snapshot": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), include a compact snapshot after all actions succeed.",
            },
            "wait_between_ms": {
                "type": "integer",
                "default": 100,
                "description": "Milliseconds to pause between actions (default 100, max 5000).",
            },
        },
        "required": ["actions"],
    },
}
