#!/usr/bin/env python3
"""Generate a fictional, original-art Waystone demo library and README screenshots.

All artwork is drawn deterministically with Qt (gradients, polygons, rings and
fictional title lettering); no network, user library, launcher, ROM, device, or
account is read. Render the app with offscreen Qt and temporary XDG/HOME paths.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "craft-projects/playnite-desktop/app"
ART = ROOT / "assets/readme-demo"
SHOTS = ROOT / "assets/screenshots"
sys.path.insert(0, str(APP))

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, QObject, Property, Slot, QThreadPool
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest


GAMES = [
    ("aetherwake", "Aetherwake", "Steam", "Linux", "installed", "Skybound exploration"),
    ("mosslight", "Mosslight", "GOG", "Linux", "installed", "A quiet forest mystery"),
    ("copper-coast", "Copper Coast", "Epic", "Windows", "installed", "Coastal town builder"),
    ("starling-run", "Starling Run", "ROM Library", "SNES", "installed", "Pixel-art platform adventure"),
    ("glass-orbit", "Glass Orbit", "Steam", "Linux", "installed", "A small-scale space opera"),
    ("wildermere", "Wildermere", "GOG", "Linux", "installed", "Folklore and wild gardens"),
    ("tidelock", "Tidelock", "Epic", "Windows", "installed", "An underwater puzzle journey"),
    ("ember-post", "Ember Post", "ROM Library", "GameCube", "installed", "A courier's island story"),
    ("frostline", "Frostline Archive", "Steam", "Linux", "not-installed", "A cold-climate investigation"),
    ("lumen-fields", "Lumen Fields", "GOG", "Linux", "installed", "A warm, tiny farming sim"),
    ("quiet-engine", "The Quiet Engine", "Epic", "Windows", "not-installed", "A clockwork platform puzzler"),
    ("velvet-comet", "Velvet Comet", "ROM Library", "Handheld", "installed", "A pocket-sized rhythm quest"),
    ("blue-hour", "Blue Hour Atlas", "Steam", "Linux", "installed", "An illustrated route planner"),
    ("cairn-circuit", "Cairn Circuit", "GOG", "Linux", "not-installed", "A mountain racing game"),
    ("sunken-signal", "Sunken Signal", "Epic", "Windows", "installed", "A deep-sea radio drama"),
    ("paper-kingdom", "Paper Kingdoms", "ROM Library", "NES", "installed", "A folded-paper tactics game"),
]

PALETTES = [
    ("#102d3c", "#e6a85e", "#8bd0c7"), ("#19392d", "#cbdf93", "#e2ad68"),
    ("#3d2523", "#ec875f", "#e9c77c"), ("#19304a", "#73bde0", "#a8e0ce"),
    ("#182941", "#92d8e8", "#c793eb"), ("#273a2b", "#d0b66f", "#94c7a0"),
    ("#123f50", "#f2c57a", "#75d5d2"), ("#263952", "#e8a56b", "#d7d184"),
    ("#23314d", "#d6e7f2", "#87aacb"), ("#304238", "#f2d278", "#8bcab1"),
    ("#422b48", "#df9cba", "#f1d389"), ("#463324", "#e7a85b", "#b4cd91"),
    ("#203754", "#89cde4", "#ebc779"), ("#3a3924", "#d9c877", "#a6d08f"),
    ("#173b45", "#f2ad77", "#9de0df"), ("#39405f", "#e7a5b2", "#e6d58b"),
]


def qcolor(value: str, alpha: int = 255) -> QColor:
    c = QColor(value); c.setAlpha(alpha); return c


def paint_art(path: Path, title: str, index: int, kind: str) -> None:
    if kind == "cover": w, h = 480, 680
    elif kind == "hero": w, h = 1600, 900
    else: w, h = 320, 320
    image = QImage(w, h, QImage.Format_ARGB32 if kind == "icon" else QImage.Format_RGB32)
    image.fill(Qt.transparent if kind == "icon" else QColor(PALETTES[index][0]))
    painter = QPainter(image); painter.setRenderHint(QPainter.Antialiasing)
    dark, bright, accent = PALETTES[index]
    if kind == "icon":
        painter.setPen(QPen(qcolor(accent, 220), 5)); painter.setBrush(qcolor(dark, 245))
        painter.drawRoundedRect(QRectF(10, 10, w - 20, h - 20), 54, 54)
        painter.setPen(QPen(qcolor(bright, 220), 3)); painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(42, 42, w - 84, h - 84))
        painter.setPen(QColor("#fff8e8")); painter.setFont(QFont("DejaVu Sans", 126, QFont.Bold))
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, title[0])
    else:
        gradient = QLinearGradient(0, 0, w, h)
        gradient.setColorAt(0, qcolor(bright, 200)); gradient.setColorAt(.48, qcolor(dark)); gradient.setColorAt(1, qcolor(accent, 220))
        painter.fillRect(0, 0, w, h, gradient)
        painter.setPen(Qt.NoPen)
        rng = random.Random(index * 7919 + len(kind))
        # Layered abstract terrain, orbital bands and towers; each composition is deterministic.
        for layer in range(4):
            points = [QPointF(0, h * (.48 + layer * .095))]
            for x in range(0, w + 1, max(1, w // 8)):
                points.append(QPointF(x, h * (.36 + layer * .105) + rng.randint(-h // 13, h // 13)))
            points.extend([QPointF(w, h), QPointF(0, h)])
            painter.setBrush(qcolor(bright if layer % 2 == 0 else accent, 42 + layer * 15))
            painter.drawPolygon(QPolygonF(points))
        ring_color = qcolor(accent, 150)
        painter.setPen(QPen(ring_color, max(2, w // 500)))
        for n in range(3):
            d = min(w, h) * (.42 + n * .15)
            painter.drawEllipse(QRectF(w * .62 - d / 2, h * .34 - d / 2, d, d))
        painter.setPen(Qt.NoPen)
        for _ in range(24):
            x, y = rng.randrange(w), rng.randrange(int(h * .66))
            r = rng.choice((2, 3, 4, 6)) * (2 if kind == "hero" else 1)
            painter.setBrush(qcolor("#fff4d8", rng.randint(80, 215)))
            painter.drawEllipse(QPointF(x, y), r, r)
        # A dark lower/left vignette keeps title copy legible without external art.
        shade = QLinearGradient(0, h * .28, 0, h)
        shade.setColorAt(0, QColor(5, 10, 19, 0)); shade.setColorAt(.45, QColor(5, 10, 19, 90)); shade.setColorAt(1, QColor(5, 10, 19, 230))
        painter.fillRect(0, 0, w, h, shade)
    if kind == "cover":
        painter.setPen(QPen(QColor(0, 0, 0, 110), 2))
        font = QFont("DejaVu Sans", 30, QFont.Bold); font.setStyleStrategy(QFont.PreferAntialias); painter.setFont(font)
        text_rect = QRectF(w * .075, h * .60, w * .84, h * .31)
        painter.drawText(text_rect.translated(2, 3), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, title)
        painter.setPen(QColor("#fff8e8")); painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, title)
    if kind == "cover":
        painter.setFont(QFont("DejaVu Sans", 12, QFont.DemiBold)); painter.setPen(qcolor("#fff8e8", 210))
        painter.drawText(QRectF(34, 30, w - 68, 36), Qt.AlignLeft | Qt.AlignVCenter, "ORIGINAL DEMO ART  /  WAYSTONE")
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write {path}")


def claim(ref: str) -> dict:
    return {"status": "local_present", "relative_reference": ref,
            "role_source": "waystone-readme-demo-generator",
            "provenance": {"same_generation_verified": True, "fixture_only": True}}


def build_demo() -> Path:
    catalog = {"schema_version": 2, "catalog_kind": "playnite-display-selected-copy-v1",
               "provenance": {"fixture_only": True, "generator": "tools/render_readme_screenshots.py",
                              "network_used": False, "user_data_used": False}, "games": []}
    for i, (slug, title, source, platform, status, genre) in enumerate(GAMES):
        cover = f"covers/{slug}.png"; hero = f"heroes/{slug}.png"; logo = f"logos/{slug}.png"
        for name, kind in ((cover, "cover"), (hero, "hero"), (logo, "logo")):
            paint_art(ART / name, title, i, kind)
        variant = {"source": source, "source_id": f"demo-{slug}", "installation_id": f"demo-install-{slug}",
                   "platform": platform, "status": status, "recipe": [],
                   "metadata": {"genre": genre, "release_date": "2024-06-15", "developer": "Small Orbit Studio",
                                "publisher": "Waystone Demo Press", "description": f"A fictional demo title: {genre.lower()}."},
                   "source_context": {"host": "Synthetic demo"}, "observation": {"at": "2026-11-25T00:00:00Z"},
                   "evidence": {"kind": "synthetic-fixture", "detail": "No real account, installation, ROM, or game record."},
                   "artwork": {"cover": claim(cover), "background": claim(hero), "icon": claim(logo)}}
        catalog["games"].append({"id": f"demo:{slug}", "title": title, "variants": [variant]})
    ART.mkdir(parents=True, exist_ok=True)
    catalog_path = ART / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog_path


def screenshot_cli(catalog: Path, name: str, *args: str, home: Path, rom_config: Path | None = None) -> None:
    command = [sys.executable, "-B", str(APP / "app.py"), "--catalog", str(catalog),
               "--media-root", str(ART), "--screenshot", str(SHOTS / name), "--width", "1600", "--height", "900",
               *( ["--rom-config", str(rom_config)] if rom_config else [] ), *args]
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "data"),
           "XDG_CONFIG_HOME": str(home / "config"), "XDG_STATE_HOME": str(home / "state"),
           "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("data", "config", "state"):
        (home / key).mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=45)


def render_big_screen_game(catalog: Path, home: Path) -> None:
    print("Rendering Big Screen game details…", flush=True)
    from PySide6.QtCore import Qt
    sys.path.insert(0, str(APP))
    from app import create_session
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    env_names = ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
    old = {k: os.environ.get(k) for k in env_names}
    for key, sub in zip(env_names, ("data", "config", "state")):
        os.environ[key] = str(home / sub)
    app = QGuiApplication.instance() or QGuiApplication(["waystone-demo-render"])
    app.setApplicationName("Waystone")
    engine, model, root, warnings = create_session(app, catalog=catalog, media_root=ART, headless=True)
    root.resize(1600, 900); root.setVisible(True); app.processEvents(); QTest.qWait(250)
    root.setProperty("consoleMode", True); app.processEvents(); QTest.qWait(450)
    console = root.findChild(QObject, "consoleLibrary")
    if console is None:
        raise RuntimeError("Big Screen root was not found")
    # QML JavaScript functions are callable on the PySide wrapper even when
    # they are not registered as invokable C++ meta-methods.
    console.openGamePage("demo:glass-orbit")
    app.processEvents(); QTest.qWait(700)
    image = root.grabWindow()
    if image.isNull() or not image.save(str(SHOTS / "big-screen-game.png"), "PNG"):
        raise RuntimeError("Could not capture Big Screen game page")
    root.close()
    seen = set()
    for obj in vars(engine).values():
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        shutdown = getattr(obj, "shutdown", None)
        if callable(shutdown):
            shutdown()
    if getattr(engine, "live_updates", None) is not None:
        engine.live_updates.stop()
    engine.deleteLater(); app.processEvents(); QTest.qWait(50)
    QThreadPool.globalInstance().waitForDone(2000)
    lingering = [thread.name for thread in threading.enumerate() if thread is not threading.main_thread()]
    if lingering:
        raise RuntimeError("Demo screenshot left background threads running: " + ", ".join(lingering))
    for k, value in old.items():
        if value is None: os.environ.pop(k, None)
        else: os.environ[k] = value
    if warnings:
        raise RuntimeError("QML warnings: " + " | ".join(warnings))
    print("Big Screen game details captured.", flush=True)


class DemoControllerClient:
    """Controller data fixture: never opens a socket or touches input devices."""
    revision = "d" * 64
    profile = {"stick_processing": {
        "left": {"enabled": True, "inner_deadzone": .07, "outer_deadzone": 1.0, "anti_deadzone": .02, "curve": 1.15},
        "right": {"enabled": True, "inner_deadzone": .09, "outer_deadzone": .98, "anti_deadzone": 0, "curve": 1.0}},
        "routes": {}, "controller_mouse": {"enabled": False, "stick": "right", "sensitivity": 16.0, "deadzone": .15}}
    def request(self, operation, **fields):
        if operation == "status": return {"running": True, "devices": [{"name": "Demo Gamepad", "connected": True, "virtual": False}]}
        if operation == "profiles": return {"names": ["Default", "Arcade"], "active_profile": "Default", "revision": self.revision}
        if operation == "profile_get": return {"name": "Default", "profile": self.profile, "revision": self.revision}
        return {"ok": True}


class DemoLive(QObject):
    def __init__(self): super().__init__()
    @Property(bool)
    def available(self): return False
    @Property(str)
    def status(self): return "Demo only · no hardware is connected"
    @Property("QVariantMap")
    def frame(self): return {}
    @Slot(str)
    def acquire(self, _owner): pass
    @Slot(str)
    def release(self, _owner): pass


def render_controller_sticks(app: QGuiApplication) -> None:
    print("Rendering controller sticks…", flush=True)
    from controller_view import ControllerView
    service = ControllerView(client=DemoControllerClient())
    service.refresh()
    for _ in range(60):
        QTest.qWait(20)
        if not service.busy: break
    if not service.available: raise RuntimeError("Demo controller fixture did not load")
    engine = QQmlApplicationEngine(); engine.addImportPath(str(APP))
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(APP / "controller_native/UnifiedControllerStudio.qml")))
    if component.isError(): raise RuntimeError("Controller QML error: " + " | ".join(e.toString() for e in component.errors()))
    window = QQuickWindow(); window.resize(1600, 900); window.setColor(QColor("#101114"))
    studio = component.create();
    if studio is None: raise RuntimeError("Could not create native controller component")
    studio.setParentItem(window.contentItem()); studio.setProperty("service", service); studio.setProperty("live", DemoLive())
    studio.setProperty("width", 1600); studio.setProperty("height", 900); studio.setProperty("activeTab", "Sticks")
    window.show(); QTest.qWait(800)
    tab = studio.findChild(QObject, "sticksTab")
    if tab is None: raise RuntimeError("Sticks tab did not load")
    image = window.grabWindow()
    if image.isNull() or not image.save(str(SHOTS / "controllers-sticks.png"), "PNG"):
        raise RuntimeError("Could not capture controller sticks screen")
    window.close(); studio.deleteLater(); engine.deleteLater(); service.shutdown(); app.processEvents()
    print("Controller sticks captured.", flush=True)


def main() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["QT_QUICK_BACKEND"] = "software"
    app = QGuiApplication(["waystone-readme-render"]); app.setApplicationName("Waystone")
    SHOTS.mkdir(parents=True, exist_ok=True)
    catalog = build_demo()
    with tempfile.TemporaryDirectory(prefix="waystone-readme-home-") as td:
        home = Path(td)
        rom_config = home / "rom-config"
        rom_config.mkdir()
        # Non-launchable fixture records let Big Screen show its systems overview
        # without manufacturing a ROM file or touching a real ROM folder.
        rom_names = (("starling-run", "snes", "Starling Run.sfc"),
                     ("ember-post", "gamecube", "Ember Post.gcm"),
                     ("velvet-comet", "gba", "Velvet Comet.gba"),
                     ("paper-kingdom", "nes", "Paper Kingdoms.nes"))
        rom_records = [{"rom_id": f"demo-rom:{slug}", "platform": platform, "file_path": str(home / "synthetic-roms" / filename),
                       "file_name": filename, "file_size": 0, "mtime_ns": 0, "extension": Path(filename).suffix,
                       "local_presence": False, "launchable": False,
                       "unlaunchable_reason": "Synthetic README fixture; no ROM file exists."}
                      for slug, platform, filename in rom_names]
        (rom_config / "local-rom-cache.json").write_text(
            json.dumps({"kind": "local-rom-cache-v1", "records": rom_records}, indent=2) + "\n", encoding="utf-8")
        screenshot_cli(catalog, "library-desktop.png", "--show-details", home=home)
        screenshot_cli(catalog, "library-game-details.png", "--view", "details", home=home)
        screenshot_cli(catalog, "big-screen.png", "--big-screen", home=home, rom_config=rom_config)
        render_big_screen_game(catalog, home)
    render_controller_sticks(app)
    print(f"Generated {len(GAMES)} fictional game records and original code-drawn art under {ART}")
    print("Screenshots:", ", ".join(str(SHOTS / n) for n in (
        "library-desktop.png", "library-game-details.png", "big-screen.png", "big-screen-game.png", "controllers-sticks.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
