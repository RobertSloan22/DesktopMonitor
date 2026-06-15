"""Shared browser detection + title cleanup, used by both OS collectors.

Process/app names differ per platform (chrome.exe vs "Google Chrome"), so each
collector picks the right set; the title cleanup is common.
"""

# Windows process names
WINDOWS_BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "iexplore.exe", "arc.exe", "chromium.exe",
}

# macOS application (localized) names
MAC_BROWSERS = {
    "Google Chrome", "Safari", "Microsoft Edge", "Brave Browser",
    "Firefox", "Arc", "Opera", "Vivaldi", "Chromium", "Safari Technology Preview",
}

# Suffixes appended to browser window titles, stripped to leave the page title.
_SUFFIXES = [
    " - Google Chrome", " - Microsoft​ Edge", " - Microsoft Edge",
    " — Mozilla Firefox", " - Mozilla Firefox", " - Brave",
    " - Opera", " - Vivaldi", " - Internet Explorer", " - Chromium",
]


def clean_title(title: str) -> str:
    """Reduce a browser window title to just the page title."""
    if not title:
        return "(new tab / blank)"
    t = title
    for suffix in _SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    # Drop a leading unread-count badge like "(3) "
    if t.startswith("(") and ")" in t[:6]:
        t = t[t.index(")") + 1:].lstrip()
    return t.strip() or "(new tab / blank)"
