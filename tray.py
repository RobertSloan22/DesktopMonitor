"""Optional system-tray icon so the bundled app can run windowless yet still
be controllable. Requires `pystray` + `pillow`; callers fall back to a plain
console loop if those aren't installed.
"""


def available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _make_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), "#0f1115")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 14, 54, 44], outline="#5b9dff", width=4)   # monitor
    d.rectangle([26, 46, 38, 52], fill="#5b9dff")               # stand
    d.rectangle([20, 52, 44, 56], fill="#5b9dff")               # base
    return img


def run(open_dashboard, on_quit):
    """Show the tray icon and block until the user picks Quit."""
    import pystray
    from pystray import MenuItem as Item

    def _open(icon, item):
        open_dashboard()

    def _quit(icon, item):
        try:
            on_quit()
        finally:
            icon.stop()

    icon = pystray.Icon(
        "ActivityMonitor", _make_image(), "Activity Monitor",
        menu=pystray.Menu(
            Item("Open Dashboard", _open, default=True),
            Item("Quit", _quit),
        ),
    )
    icon.run()
