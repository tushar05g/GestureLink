"""
shortcuts.py — persistent gesture shortcut mappings and action launcher.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ShortcutBinding:
    enabled: bool = False
    target: str = ""


@dataclass(frozen=True)
class DiscoveredApp:
    name: str
    target: str


class ShortcutManager:
    """Stores user mappings for one/two/three-finger shortcuts and launches them."""

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = Path(config_path or Path.home() / ".gesturelink_shortcuts.json")
        self._bindings: dict[str, ShortcutBinding] = {
            "one_finger": ShortcutBinding(),
            "two_fingers": ShortcutBinding(),
            "three_fingers": ShortcutBinding(),
        }
        self._app_cache: list[DiscoveredApp] | None = None
        self.load()

    def load(self) -> None:
        if not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            for key in self._bindings:
                item = data.get(key, {})
                self._bindings[key] = ShortcutBinding(
                    enabled=bool(item.get("enabled", False)),
                    target=str(item.get("target", "")).strip(),
                )
        except Exception as exc:
            logger.warning("Failed to load shortcut config (%s): %s", self._config_path, exc)

    def save(self) -> None:
        payload = {
            key: {"enabled": bind.enabled, "target": bind.target}
            for key, bind in self._bindings.items()
        }
        self._config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_target(self, slot: str) -> str:
        bind = self._bindings.get(slot)
        return bind.target if bind else ""

    def configure(self, slot: str, target: str, enabled: bool = True) -> None:
        if slot not in self._bindings:
            raise ValueError(f"Unknown shortcut slot: {slot}")
        self._bindings[slot] = ShortcutBinding(enabled=enabled and bool(target.strip()), target=target.strip())
        self.save()

    def get_bindings(self) -> dict[str, dict[str, object]]:
        return {
            slot: {
                "enabled": binding.enabled,
                "target": binding.target,
            }
            for slot, binding in self._bindings.items()
        }

    def set_bindings(self, payload: dict[str, dict[str, object]]) -> None:
        for slot, binding in payload.items():
            if slot not in self._bindings:
                continue
            enabled = bool(binding.get("enabled", False))
            target = str(binding.get("target", "")).strip()
            self._bindings[slot] = ShortcutBinding(
                enabled=enabled and bool(target),
                target=target,
            )
        self.save()

    def list_discovered_apps(self, limit: int = 200) -> list[dict[str, str]]:
        platform_name = sys_platform()
        candidates: list[DiscoveredApp] = []

        if platform_name == "linux":
            candidates = self._discover_linux_desktop_apps()
        elif platform_name == "darwin":
            candidates = self._discover_macos_apps()
        elif platform_name == "windows":
            candidates = self._discover_windows_apps()

        # Fallback: if desktop app discovery is empty, use generic command discovery.
        if not candidates:
            candidates = self._discover_apps()

        apps = self._dedupe_and_sort(candidates)[: max(1, limit)]
        return [{"name": app.name, "target": app.target} for app in apps]

    def disable(self, slot: str) -> None:
        if slot not in self._bindings:
            raise ValueError(f"Unknown shortcut slot: {slot}")
        self._bindings[slot] = ShortcutBinding(enabled=False, target="")
        self.save()

    def trigger(self, slot: str) -> str:
        bind = self._bindings.get(slot)
        if not bind or not bind.enabled or not bind.target:
            return "not configured"
        try:
            self._launch(bind.target)
            return f"launched {bind.target}"
        except Exception as exc:
            logger.warning("Failed to launch target '%s': %s", bind.target, exc)
            return f"launch failed: {exc}"

    def launch_target(self, target: str) -> str:
        target = str(target).strip()
        if not target:
            return "not configured"
        try:
            self._launch(target)
            return f"launched {target}"
        except Exception as exc:
            logger.warning("Failed to launch explicit target '%s': %s", target, exc)
            return f"launch failed: {exc}"

    def open_settings_wizard(self) -> str:
        """Open GUI settings when available; otherwise fall back to CLI wizard."""
        gui_result = self.open_settings_gui()
        if gui_result != "GUI unavailable":
            return gui_result
        return self.open_settings_cli()

    def open_settings_gui(self) -> str:
        """Gamified GUI settings with dropdown app picker + manual target entry."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            from tkinter import ttk
        except Exception:
            return "GUI unavailable"

        # No display available (common on headless Linux sessions).
        if sys_platform() == "linux" and not os.environ.get("DISPLAY"):
            return "GUI unavailable"

        labels = [
            ("one_finger", "One Finger Quest"),
            ("two_fingers", "Two Finger Quest"),
            ("three_fingers", "Three Finger Quest"),
        ]
        discovered = self._discover_apps()
        discovered_display = [f"{a.name} | {a.target}" for a in discovered]
        display_to_target = {f"{a.name} | {a.target}": a.target for a in discovered}

        root = tk.Tk()
        root.title("GestureLink Shortcut Arena")
        root.geometry("900x650")
        root.configure(bg="#101820")

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Title.TLabel", background="#101820", foreground="#7DF9FF", font=("Helvetica", 17, "bold"))
        style.configure("Sub.TLabel", background="#101820", foreground="#D2E7F0", font=("Helvetica", 10))
        style.configure("Card.TLabelframe", background="#182838", foreground="#7DF9FF")
        style.configure("Card.TLabelframe.Label", background="#182838", foreground="#7DF9FF", font=("Helvetica", 11, "bold"))
        style.configure("Action.TButton", font=("Helvetica", 10, "bold"))

        title = ttk.Label(root, text="GestureLink Shortcut Arena", style="Title.TLabel")
        title.pack(pady=(14, 4))

        subtitle = ttk.Label(
            root,
            text=(
                "Assign launch actions to one, two, and three-finger gestures. "
                "Choose from detected apps or provide a custom path/command/URL."
            ),
            style="Sub.TLabel",
            wraplength=820,
            justify="center",
        )
        subtitle.pack(pady=(0, 10))

        score_var = tk.StringVar(value="Level: Rookie (0/3 configured)")
        score_label = ttk.Label(root, textvariable=score_var, style="Sub.TLabel")
        score_label.pack(pady=(0, 6))

        progress = ttk.Progressbar(root, maximum=3, length=500)
        progress.pack(pady=(0, 10))

        card_container = tk.Frame(root, bg="#101820")
        card_container.pack(fill="both", expand=True, padx=16, pady=8)

        slot_state: dict[str, dict[str, object]] = {}

        def _compute_level(configured: int) -> str:
            if configured <= 0:
                return "Rookie"
            if configured == 1:
                return "Explorer"
            if configured == 2:
                return "Pro"
            return "Gesture Master"

        def _resolved_target(slot: str) -> str:
            row = slot_state[slot]
            manual = str(row["manual_var"].get()).strip()  # type: ignore[index]
            if manual:
                return manual
            picked = str(row["combo_var"].get()).strip()  # type: ignore[index]
            return display_to_target.get(picked, "")

        def _refresh_score() -> None:
            configured = 0
            for slot, _ in labels:
                row = slot_state[slot]
                enabled = bool(row["enabled_var"].get())  # type: ignore[index]
                if enabled and _resolved_target(slot):
                    configured += 1
            progress["value"] = configured
            score_var.set(f"Level: {_compute_level(configured)} ({configured}/3 configured)")

        def _browse_for_target(slot: str) -> None:
            row = slot_state[slot]
            selected = filedialog.askopenfilename(title="Choose app or file")
            if not selected:
                selected = filedialog.askdirectory(title="Choose folder")
            if selected:
                row["manual_var"].set(selected)  # type: ignore[index]
                _refresh_score()

        def _test_slot(slot: str) -> None:
            row = slot_state[slot]
            target = _resolved_target(slot)
            enabled = bool(row["enabled_var"].get())  # type: ignore[index]
            status_widget = row["status_var"]  # type: ignore[index]
            if not enabled or not target:
                status_widget.set("Status: configure target first")
                return
            try:
                self._launch(target)
                status_widget.set(f"Status: launched {target}")
            except Exception as exc:
                status_widget.set(f"Status: launch failed ({exc})")

        for slot, heading in labels:
            current = self._bindings[slot]

            frame = ttk.LabelFrame(card_container, text=heading, style="Card.TLabelframe")
            frame.pack(fill="x", padx=4, pady=8, ipady=6)

            enabled_var = tk.BooleanVar(value=current.enabled)
            combo_var = tk.StringVar(value="")
            manual_var = tk.StringVar(value=current.target if current.enabled else "")
            status_var = tk.StringVar(value="Status: ready")

            current_match = next((d for d in discovered_display if display_to_target[d] == current.target), "")
            if current_match:
                combo_var.set(current_match)

            slot_state[slot] = {
                "enabled_var": enabled_var,
                "combo_var": combo_var,
                "manual_var": manual_var,
                "status_var": status_var,
            }

            enabled_cb = tk.Checkbutton(
                frame,
                text="Enable shortcut",
                variable=enabled_var,
                bg="#182838",
                fg="#EAF9FF",
                selectcolor="#182838",
                activebackground="#182838",
                activeforeground="#EAF9FF",
                command=_refresh_score,
            )
            enabled_cb.grid(row=0, column=0, sticky="w", padx=8, pady=4)

            ttk.Label(frame, text="Pick discovered app:", style="Sub.TLabel").grid(
                row=1, column=0, sticky="w", padx=8
            )
            combo = ttk.Combobox(frame, textvariable=combo_var, values=discovered_display, width=85)
            combo.grid(row=2, column=0, columnspan=3, sticky="we", padx=8, pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _e: _refresh_score())

            ttk.Label(frame, text="Or enter manual path/command/URL:", style="Sub.TLabel").grid(
                row=3, column=0, sticky="w", padx=8
            )
            manual_entry = ttk.Entry(frame, textvariable=manual_var, width=88)
            manual_entry.grid(row=4, column=0, columnspan=2, sticky="we", padx=8, pady=4)
            manual_entry.bind("<KeyRelease>", lambda _e: _refresh_score())

            browse_btn = ttk.Button(frame, text="Browse", style="Action.TButton", command=lambda s=slot: _browse_for_target(s))
            browse_btn.grid(row=4, column=2, padx=8, sticky="e")

            test_btn = ttk.Button(frame, text="Test", style="Action.TButton", command=lambda s=slot: _test_slot(s))
            test_btn.grid(row=5, column=2, padx=8, pady=(2, 4), sticky="e")

            status_lbl = ttk.Label(frame, textvariable=status_var, style="Sub.TLabel")
            status_lbl.grid(row=5, column=0, columnspan=2, padx=8, pady=(2, 4), sticky="w")

            frame.grid_columnconfigure(0, weight=1)
            frame.grid_columnconfigure(1, weight=1)

        result_message = {"text": "No shortcut changes"}

        def _save_and_close() -> None:
            changed = False
            for slot, _ in labels:
                row = slot_state[slot]
                enabled = bool(row["enabled_var"].get())  # type: ignore[index]
                target = _resolved_target(slot)
                new_binding = ShortcutBinding(enabled=enabled and bool(target), target=target.strip())
                old_binding = self._bindings[slot]
                if new_binding != old_binding:
                    self._bindings[slot] = new_binding
                    changed = True
            if changed:
                self.save()
                result_message["text"] = f"Saved shortcut mappings to {self._config_path}"
            root.destroy()

        def _cancel() -> None:
            root.destroy()

        btn_row = tk.Frame(root, bg="#101820")
        btn_row.pack(fill="x", pady=(6, 12), padx=16)

        save_btn = ttk.Button(btn_row, text="Save Quests", style="Action.TButton", command=_save_and_close)
        save_btn.pack(side="left", padx=6)

        cancel_btn = ttk.Button(btn_row, text="Cancel", style="Action.TButton", command=_cancel)
        cancel_btn.pack(side="left", padx=6)

        hint_lbl = ttk.Label(
            btn_row,
            text="Tip: if app is not listed, use manual command/path (example: firefox, /usr/bin/code, https://example.com)",
            style="Sub.TLabel",
        )
        hint_lbl.pack(side="right", padx=6)

        _refresh_score()
        root.mainloop()
        return result_message["text"]

    def open_settings_cli(self) -> str:
        """Interactive terminal-based settings workflow."""
        print("\nGestureLink Shortcut Settings")
        print("Choose discovered app, or enter an absolute path, command, or URL.\n")

        labels = [
            ("one_finger", "One finger up"),
            ("two_fingers", "Two fingers up"),
            ("three_fingers", "Three fingers up"),
        ]

        changed = False
        for slot, label in labels:
            changed = self._configure_slot_interactive(slot, label) or changed

        if changed:
            self.save()
            return f"Saved shortcut mappings to {self._config_path}"
        return "No shortcut changes"

    def _configure_slot_interactive(self, slot: str, label: str) -> bool:
        current = self._bindings[slot]
        cur_txt = current.target if current.enabled and current.target else "<disabled>"

        print(f"\n{label}")
        print(f"Current: {cur_txt}")
        print("1) Keep current")
        print("2) Disable")
        print("3) Choose from discovered apps")
        print("4) Enter path/command/URL manually")
        print("5) Test current")

        choice = input("Select option [1-5, default 1]: ").strip() or "1"
        if choice == "1":
            return False
        if choice == "2":
            self._bindings[slot] = ShortcutBinding(enabled=False, target="")
            return True
        if choice == "5":
            result = self.trigger(slot)
            print(f"Test result: {result}")
            return False
        if choice == "3":
            selected = self._pick_discovered_app()
            if not selected:
                print("No selection made.")
                return False
            self._bindings[slot] = ShortcutBinding(enabled=True, target=selected)
            return True
        if choice == "4":
            manual = input("Enter path, command, or URL ('-' to disable): ").strip()
            if manual == "":
                return False
            if manual == "-":
                self._bindings[slot] = ShortcutBinding(enabled=False, target="")
                return True
            self._bindings[slot] = ShortcutBinding(enabled=True, target=manual)
            return True

        print("Invalid option, keeping current.")
        return False

    def _pick_discovered_app(self) -> Optional[str]:
        apps = self._discover_apps()
        if not apps:
            print("No apps discovered. Use manual entry instead.")
            return None

        query = input("Search app name (blank for top list): ").strip().lower()
        filtered = [a for a in apps if query in a.name.lower()] if query else apps
        if not filtered:
            print("No matching apps found.")
            return None

        max_items = min(25, len(filtered))
        print(f"Showing {max_items} of {len(filtered)} matches:")
        for i in range(max_items):
            app = filtered[i]
            print(f"{i + 1}) {app.name} -> {app.target}")

        raw = input("Pick number (or Enter to cancel): ").strip()
        if not raw:
            return None
        if not raw.isdigit():
            print("Invalid selection.")
            return None
        idx = int(raw) - 1
        if idx < 0 or idx >= max_items:
            print("Selection out of range.")
            return None
        return filtered[idx].target

    def _discover_apps(self) -> list[DiscoveredApp]:
        if self._app_cache is not None:
            return self._app_cache

        candidates: list[DiscoveredApp] = []
        platform_name = sys_platform()

        if platform_name == "linux":
            candidates.extend(self._discover_linux_desktop_apps())
        elif platform_name == "darwin":
            candidates.extend(self._discover_macos_apps())
        elif platform_name == "windows":
            candidates.extend(self._discover_windows_apps())

        candidates.extend(self._discover_path_commands())
        self._app_cache = self._dedupe_and_sort(candidates)
        return self._app_cache

    def _discover_linux_desktop_apps(self) -> list[DiscoveredApp]:
        roots = [
            Path.home() / ".local/share/applications",
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path("/snap/current/share/applications"),
        ]
        out: list[DiscoveredApp] = []
        for root in roots:
            if not root.exists():
                continue
            try:
                for desktop_file in root.rglob("*.desktop"):
                    app = self._parse_desktop_file(desktop_file)
                    if app:
                        out.append(app)
            except Exception as e:
                logger.debug("Error searching %s: %s", root, e)
        return out

    def _discover_windows_apps(self) -> list[DiscoveredApp]:
        import os
        roots = [
            os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "Microsoft\\Windows\\Start Menu\\Programs"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs"),
            os.path.join(os.path.expanduser("~"), "Desktop"),
        ]
        out: list[DiscoveredApp] = []
        for root_path in roots:
            root = Path(root_path)
            if not root.exists():
                continue
            try:
                for lnk_file in root.rglob("*.lnk"):
                    # For Windows, the name is the filename without extension
                    # The 'target' is the path to the .lnk itself (Windows handles launching it)
                    name = lnk_file.stem
                    out.append(DiscoveredApp(name=name, target=str(lnk_file)))
            except Exception as e:
                logger.debug("Error searching Windows apps in %s: %s", root, e)
        return out

    def _parse_desktop_file(self, desktop_file: Path) -> Optional[DiscoveredApp]:
        name = ""
        exec_line = ""
        no_display = False
        hidden = False
        terminal = False
        app_type = ""
        try:
            text = desktop_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("Name=") and not name:
                name = line.split("=", 1)[1].strip()
            elif line.startswith("Exec=") and not exec_line:
                exec_line = line.split("=", 1)[1].strip()
            elif line.startswith("NoDisplay="):
                no_display = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("Hidden="):
                hidden = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("Terminal="):
                terminal = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("Type="):
                app_type = line.split("=", 1)[1].strip()

        if no_display or hidden or terminal or not exec_line:
            return None
        if app_type and app_type.lower() != "application":
            return None

        cleaned = re.sub(r"%[fFuUdDnNickvm]", "", exec_line).strip()
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            return None

        return DiscoveredApp(name=name or desktop_file.stem, target=cleaned)

    def _discover_macos_apps(self) -> list[DiscoveredApp]:
        roots = [Path("/Applications"), Path.home() / "Applications"]
        out: list[DiscoveredApp] = []
        for root in roots:
            if not root.exists():
                continue
            for app_path in root.glob("*.app"):
                name = app_path.stem
                out.append(DiscoveredApp(name=name, target=str(app_path)))
        return out

    def _discover_path_commands(self, limit: int = 200) -> list[DiscoveredApp]:
        out: list[DiscoveredApp] = []
        seen: set[str] = set()
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            path_dir = Path(entry)
            if not path_dir.is_dir():
                continue
            try:
                for candidate in path_dir.iterdir():
                    if len(out) >= limit:
                        return out
                    if not candidate.is_file():
                        continue
                    if not os.access(candidate, os.X_OK):
                        continue
                    name = candidate.name
                    if name in seen:
                        continue
                    seen.add(name)
                    out.append(DiscoveredApp(name=name, target=name))
            except OSError:
                continue
        return out

    @staticmethod
    def _dedupe_and_sort(items: list[DiscoveredApp]) -> list[DiscoveredApp]:
        seen: set[tuple[str, str]] = set()
        unique: list[DiscoveredApp] = []
        for item in items:
            key = (item.name.strip().lower(), item.target.strip())
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        unique.sort(key=lambda x: x.name.lower())
        return unique

    def _launch(self, target: str) -> None:
        # URLs
        if target.startswith("http://") or target.startswith("https://"):
            webbrowser.open(target, new=2)
            return

        # Parse once so we can correctly distinguish executable paths from files.
        argv = shlex.split(os.path.expanduser(target))
        if not argv:
            raise ValueError("empty command")

        first = argv[0]
        if os.path.exists(first):
            if os.path.isdir(first):
                self._open_path(first)
                return
            if os.path.isfile(first) and os.access(first, os.X_OK):
                subprocess.Popen(argv, start_new_session=True)
                return
            if len(argv) == 1:
                self._open_path(first)
                return
            raise ValueError(f"target is not executable: {first}")

        # Command available in PATH.
        subprocess.Popen(argv, start_new_session=True)

    @staticmethod
    def _open_path(path: str) -> None:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return
        if sys_platform() == "darwin":
            subprocess.Popen(["open", path], start_new_session=True)
            return
        subprocess.Popen(["xdg-open", path], start_new_session=True)


def sys_platform() -> str:
    import sys
    if sys.platform == "win32":
        return "windows"
    import platform
    return platform.system().lower()
