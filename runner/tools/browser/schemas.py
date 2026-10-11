from typing import Any

BROWSER_NAVIGATE_SCHEMA: dict[str, Any] = {
    "name": "browser_navigate",
    "description": (
        "Open an http/https URL in the current tab, starting the browser if needed. Prefer web_search, web_extract "
        "or terminal curl for simple retrieval when available; use the browser for interaction or dynamic pages. "
        "Returns the final URL, title and a compact snapshot with fresh refs. Navigation invalidates previous refs; "
        "refresh the snapshot before ref actions. Use search_tools when a needed browser capability is unavailable."
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
        "Read the current page accessibility tree. Use returned refs (e.g. '@e5') for actions in the active tab and "
        "root frame. full=true includes page text; the default lists interactive elements. Navigation, tab switches "
        "and root-frame changes invalidate refs, so snapshot again before acting. Results also include pending dialogs "
        "and frame information."
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
        "Click a ref from the latest snapshot ('@e5') or annotated vision ('@vN'), or viewport coordinates 'x,y'. "
        "Refresh refs after navigation, back, tab switch or root-frame change. After a failure inspect the page "
        "before retrying because the click may have partly occurred."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "Element ref ('@e5') from the latest snapshot, annotated screenshot ref ('@v3') from the "
                    "latest browser_vision result, or viewport coordinates 'x,y' (e.g. '240,480')."
                ),
            },
        },
        "required": ["ref"],
    },
}

BROWSER_TYPE_SCHEMA: dict[str, Any] = {
    "name": "browser_type",
    "description": (
        "Clear and type text into a ref from the latest snapshot/vision or 'x,y' coordinates. Refresh refs after "
        "navigation, back, tab switch or root-frame change."
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
        "Hover a ref from the latest snapshot or 'x,y' coordinates to trigger hover styles, menus or tooltips. "
        "Refresh refs after navigation or tab switch."
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
        "Wait for a CSS selector or visible text (at least one is required; if both are given either may match). "
        "Returns a compact snapshot on success unless return_snapshot=false."
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
        "anyOf": [{"required": ["selector"]}, {"required": ["text"]}],
    },
}

BROWSER_FIND_SCHEMA: dict[str, Any] = {
    "name": "browser_find",
    "description": (
        "Find visible DOM elements whose text contains a substring and return current refs (up to 200). If a ref "
        "is missing, take browser_snapshot first; refs are scoped to the active tab and root frame."
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
        "Drag between refs from the latest snapshot or 'x,y' coordinates. Refresh refs after navigation or tab "
        "switch; inspect the page before retrying a failed drag because it may have partly occurred."
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
        "Select a native or custom dropdown using a ref from the latest snapshot. Provide exactly one of value, "
        "label or index. Refresh the ref after navigation or tab switch."
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
        "oneOf": [{"required": ["value"]}, {"required": ["label"]}, {"required": ["index"]}],
    },
}

BROWSER_DOWNLOAD_SCHEMA: dict[str, Any] = {
    "name": "browser_download",
    "description": (
        "Download by clicking a link ref or opening a URL; returns the local path after completion. A timeout or "
        "error may still have triggered the download, so inspect page state or downloads before retrying."
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
        "Capture and attach an element screenshot using a ref from the latest snapshot; the PNG path is also "
        "returned. Refresh the ref after navigation, tab switch or root-frame change."
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
    "description": "Open and activate a new tab. Refs from the previous tab are invalid; take a fresh snapshot or vision before acting.",
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
        "Switch to a tab_id from browser_tab_list or browser_tab_new. Switching tabs invalidates refs; take a fresh "
        "snapshot or vision before acting."
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
        "Override the current tab's user agent, platform or Accept-Language; provide at least one field. An empty "
        "user_agent clears that override. Refresh the page and snapshot before using old refs."
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
        "anyOf": [{"required": ["user_agent"]}, {"required": ["platform"]}, {"required": ["accept_language"]}],
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
        "navigator.geolocation. Provide lat and lon together, or omit both to remove the override."
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
        "allOf": [
            {"if": {"required": ["lat"]}, "then": {"required": ["lon"]}},
            {"if": {"required": ["lon"]}, "then": {"required": ["lat"]}},
        ],
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
        "Run page actions in order: click {ref}, type {ref,text}, press {key}, hover {ref}, scroll {direction,pixels}, "
        "wait {seconds}, or select {ref plus exactly one of value/label/index}. Refs come from the latest snapshot "
        "or annotated vision. Stops at the first failure and reports the step; success returns details and a compact "
        "snapshot by default."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Action objects to execute in order, e.g. [{'action': 'click', 'ref': '@e2'}, "
                    "{'action': 'type', 'ref': '@e3', 'text': 'search'}, {'action': 'press', 'key': 'Enter'}]."
                ),
                "items": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "type", "press", "hover", "scroll", "wait", "select"],
                        },
                        "ref": {"type": "string"},
                        "text": {"type": "string"},
                        "key": {"type": "string"},
                        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                        "pixels": {"type": ["integer", "number", "string"]},
                        "seconds": {"type": ["integer", "number", "string"]},
                        "value": {"type": "string"},
                        "label": {"type": "string"},
                        "index": {"type": "integer"},
                    },
                    "additionalProperties": False,
                    "allOf": [
                        {
                            "if": {"properties": {"action": {"enum": ["click", "hover"]}}},
                            "then": {"required": ["ref"]},
                        },
                        {
                            "if": {"properties": {"action": {"const": "type"}}},
                            "then": {"required": ["ref", "text"]},
                        },
                        {
                            "if": {"properties": {"action": {"const": "press"}}},
                            "then": {"required": ["key"]},
                        },
                        {
                            "if": {"properties": {"action": {"const": "scroll"}}},
                            "then": {"required": ["direction"]},
                        },
                        {
                            "if": {"properties": {"action": {"const": "select"}}},
                            "then": {
                                "allOf": [
                                    {"required": ["ref"]},
                                    {
                                        "oneOf": [
                                            {"required": ["value"]},
                                            {"required": ["label"]},
                                            {"required": ["index"]},
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                },
            },
            "return_snapshot": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), include a compact snapshot after all actions succeed.",
            },
            "wait_between_ms": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5000,
                "default": 100,
                "description": "Milliseconds to pause between actions (default 100, max 5000).",
            },
        },
        "required": ["actions"],
    },
}


# All browser argument objects are strict at the Backend boundary. Dynamic maps remain
# explicitly open on their own property (extra headers and raw CDP params).
for _browser_schema in (
    BROWSER_NAVIGATE_SCHEMA,
    BROWSER_SNAPSHOT_SCHEMA,
    BROWSER_CLICK_SCHEMA,
    BROWSER_TYPE_SCHEMA,
    BROWSER_SCROLL_SCHEMA,
    BROWSER_BACK_SCHEMA,
    BROWSER_PRESS_SCHEMA,
    BROWSER_HOVER_SCHEMA,
    BROWSER_WAIT_FOR_SCHEMA,
    BROWSER_FIND_SCHEMA,
    BROWSER_DRAG_SCHEMA,
    BROWSER_SELECT_SCHEMA,
    BROWSER_DOWNLOAD_SCHEMA,
    BROWSER_PDF_SCHEMA,
    BROWSER_SCREENSHOT_ELEMENT_SCHEMA,
    BROWSER_TAB_NEW_SCHEMA,
    BROWSER_TAB_SWITCH_SCHEMA,
    BROWSER_TAB_CLOSE_SCHEMA,
    BROWSER_TAB_LIST_SCHEMA,
    BROWSER_SET_VIEWPORT_SCHEMA,
    BROWSER_SET_USER_AGENT_SCHEMA,
    BROWSER_SET_EXTRA_HEADERS_SCHEMA,
    BROWSER_SET_GEOLOCATION_SCHEMA,
    BROWSER_GET_IMAGES_SCHEMA,
    BROWSER_VISION_SCHEMA,
    BROWSER_CONSOLE_SCHEMA,
    BROWSER_COOKIES_GET_SCHEMA,
    BROWSER_COOKIES_SET_SCHEMA,
    BROWSER_COOKIES_CLEAR_SCHEMA,
    BROWSER_STORAGE_GET_SCHEMA,
    BROWSER_STORAGE_SET_SCHEMA,
    BROWSER_DIALOG_SCHEMA,
    BROWSER_CDP_SCHEMA,
    BROWSER_BATCH_SCHEMA,
):
    _browser_schema["parameters"].setdefault("additionalProperties", False)
