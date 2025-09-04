import time
import random
import sys
from pathlib import Path
from pynput.keyboard import Controller  # kept as in your original
import pydirectinput as directin

import pyautogui
import keyboard
from PIL import Image  # <-- NEW: preload templates via PIL

# Try to detect OpenCV (needed for confidence-based matching)
try:
    import cv2  # noqa: F401
    _HAS_CV2 = True
except Exception:
    _HAS_CV2 = False

# =============== C O N F I G ===============
IMG1 = Path("Mega_Arcana_Pack.png")    # First image to search
IMG2 = Path("The_Soul.png")            # Second image to search
SKIP_BUTTON = Path("skip_blind.png")   # Button to click after IMG1

CONFIDENCE       = 0.70                # Similarity threshold in [0.0–1.0]
SCAN_INTERVAL    = 0.30                # Screen scan interval (seconds)
PRESS_R_EVERY    = 7.0                 # Press 'R' every 7s if IMG1 is not found
SEARCH_B_TIMEOUT = 8.0                 # Max time to search IMG2 (seconds)
SKIP_TIMEOUT     = 5.0                 # Max time to search skip button (seconds)
ALERT_INTERVAL   = 5.5                 # Beep interval while IMG2 stays on screen
HUMAN_MOVE       = (0.08, 0.18)        # Slight mouse move duration before click
CLICK_JITTER     = 2                   # +/- pixel jitter around click point
HOTKEY_TOGGLE    = "F8"                # Start/Stop hotkey
REGION_MARGIN    = 12                  # Pixels to expand around IMG2 box for stability
# ==========================================

pyautogui.FAILSAFE = True  # Move mouse to a screen corner to trigger fail-safe


# ==== DO NOT CHANGE (kept exactly as you provided) ====
def reset_game():

    directin.keyDown('r')
    time.sleep(2)
    directin.keyUp('r')
# ======================================================


def beep_loop():
    try:
        if sys.platform.startswith("win"):
            while True:
                try:
                    import winsound
                    winsound.Beep(880, 180); winsound.Beep(660, 120)
                except Exception:
                    import winsound
                    winsound.MessageBeep(-1)
        else:
            print("\a", end="", flush=True)
    except Exception:
        print("\a", end="", flush=True)



def beep(count=1, interval=0.2, pattern=((880, 180), (660, 120)), max_beeps=50):
    """
    Play a beep sound multiple times.
    - count: how many times to beep (rounded down to int)
    - interval: pause between beeps (seconds)
    - pattern: for Windows, a sequence of (frequency Hz, duration ms) prrairs
               non-Windows will use the terminal bell once per beep
    - max_beeps: safety cap to avoid accidental spam
    """
    try:
        count = int(count)
    except Exception:
        count = 1
    if count <= 0:
        return
    count = min(count, max_beeps)

    for _ in range(count):
        try:
            if sys.platform.startswith("win"):
                import winsound
                for freq, dur in pattern:
                    # Clamp to winsound limits
                    f = int(max(37, min(32767, freq)))
                    d = int(max(1, dur))
                    winsound.Beep(f, d)
            else:
                # Simple terminal bell (works in many terminals)
                print("\a", end="", flush=True)
        except Exception:
            # Fallback bell
            print("\a", end="", flush=True)
        time.sleep(float(interval))


def hms() -> str:
    return time.strftime("%H:%M:%S")


def _clamp_region(left, top, width, height):
    sw, sh = pyautogui.size()
    left = max(0, left)
    top = max(0, top)
    width = max(1, min(width, sw - left))
    height = max(1, min(height, sh - top))
    return (left, top, width, height)


def _expand_box(box, margin=REGION_MARGIN):
    return _clamp_region(box.left - margin, box.top - margin,
                         box.width + 2*margin, box.height + 2*margin)


# ---------- preload templates with PIL (avoids OpenCV path decoding issues) ----------
TEMPLATES = {}  # name -> PIL.Image

def _load_template(path: Path, name: str):
    try:
        img = Image.open(str(path))
        # Normalize mode; RGB or L works well with matchTemplate
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        TEMPLATES[name] = img
        print(f"[INFO] Loaded template '{name}': {path} size={img.size} mode={img.mode}")
    except Exception as e:
        raise RuntimeError(f"Failed to open template {name} at {path}: {e!r}")

def ensure_templates():
    for pth, name in [(IMG1, "IMG1"), (IMG2, "IMG2"), (SKIP_BUTTON, "SKIP")]:
        if not pth.exists():
            raise FileNotFoundError(f"Template not found: {pth}")
        _load_template(pth, name)


def locate(img_or_name, region=None):
    """
    Locate using a preloaded PIL.Image (preferred) or a Path/name.
    Passing an Image object avoids cv2.imread() on paths (fixes ImageNotFoundException).
    """
    try:
        # Resolve to an Image
        if isinstance(img_or_name, Image.Image):
            needle = img_or_name
        elif isinstance(img_or_name, str):
            needle = TEMPLATES.get(img_or_name, None)
            if needle is None:
                # allow direct path-like string as last resort
                needle = Image.open(img_or_name).convert("RGB")
        elif isinstance(img_or_name, Path):
            needle = TEMPLATES.get(img_or_name.name, None)
            if needle is None:
                needle = Image.open(str(img_or_name)).convert("RGB")
        else:
            needle = img_or_name  # hope it's acceptable

        if _HAS_CV2:
            return pyautogui.locateOnScreen(needle, confidence=CONFIDENCE, grayscale=True, region=region)
        else:
            return pyautogui.locateOnScreen(needle, region=region)
    except Exception as e:
        print(f"[WARN] locateOnScreen error: {img_or_name} -> {type(e).__name__}: {e!r}")
        return None


def click_center(box):
    cx, cy = pyautogui.center(box)
    cx += random.randint(-CLICK_JITTER, CLICK_JITTER)
    cy += random.randint(-CLICK_JITTER, CLICK_JITTER)
    pyautogui.moveTo(cx, cy, duration=random.uniform(*HUMAN_MOVE))
    pyautogui.click()
    return cx, cy


# ---------- reusable image search ----------
def search_image(
    img_key_or_img,
    *,
    state=None,
    timeout=None,
    interval=SCAN_INTERVAL,
    press_r_every=None,
    region=None,
):
    """
    Repeatedly scan the screen for the given image (key in TEMPLATES or PIL.Image)
    until found or timeout. Optional:
      - press_r_every: press 'R' every N seconds while waiting
      - region: restrict search region (L,T,W,H)
    Returns: pyautogui.Box or None (if not found / paused / timed out).
    """
    start = time.time()
    last_r = 0.0

    while True:
        if keyboard.is_pressed("esc"):
            print("[INFO] Exiting...")
            raise KeyboardInterrupt

        if state is not None and not state.get("running", False):
            return None

        box = locate(img_key_or_img, region=region)
        if box:
            return box

        now = time.time()

        if press_r_every is not None and (now - last_r) >= press_r_every:
            reset_game()
            last_r = now
            # try to print a nice name
            name = img_key_or_img if isinstance(img_key_or_img, str) else getattr(img_key_or_img, "filename", "image")
            print(f"[{hms()}] {name} not found -> pressed 'R'")

        if timeout is not None and (now - start) >= timeout:
            return None

        time.sleep(interval)


def toggle(state):
    state["running"] = not state.get("running", False)
    print(f"[INFO] Running: {'ON' if state['running'] else 'OFF'}")


def main():
    ensure_templates()
    print("== Automation started ==")
    print(f"- IMG1: {IMG1.resolve()}")
    print(f"- SKIP BUTTON: {SKIP_BUTTON.resolve()}")
    print(f"- IMG2: {IMG2.resolve()}")
    print(f"- Toggle with {HOTKEY_TOGGLE}, exit with ESC.")
    print("- Fail-safe is ON: move the cursor to a screen corner to stop.")

    state = {"running": False}
    keyboard.add_hotkey(HOTKEY_TOGGLE, lambda: toggle(state))

    print("[INFO] Initial reset in 5s...")
    time.sleep(5)
    reset_game()
    time.sleep(3)
    beep() # dont delete this line 

    try:
        while True:
            if keyboard.is_pressed("esc"):
                print("[INFO] Exiting...")
                break

            if not state["running"]:
                time.sleep(0.1)
                continue

            # ====== STATE A: SEARCH IMG1 (periodic 'R') ======
            box1 = search_image(
                "IMG1",
                state=state,
                timeout=None,
                interval=SCAN_INTERVAL,
                press_r_every=PRESS_R_EVERY
            )
            if not state["running"] or box1 is None:
                continue

            cx, cy = click_center(box1)
            print(f"[{hms()}] IMG1 clicked @ ({cx},{cy}) {box1}")

            # ====== STATE A2: FIND & CLICK SKIP BUTTON ======
            btn_box = search_image(
                "SKIP",
                state=state,
                timeout=SKIP_TIMEOUT,
                interval=SCAN_INTERVAL,
                press_r_every=None
            )
            if not state["running"]:
                continue

            if btn_box:
                bx, by = click_center(btn_box)
                print(f"[{hms()}] SKIP button clicked @ ({bx},{by}) {btn_box}")
                time.sleep(0.25)
            else:
                print(f"[{hms()}] SKIP button not found within {SKIP_TIMEOUT}s, continuing.")

            # ====== STATE B: SEARCH IMG2 (time-limited, no auto-R) ======
            box2 = search_image(
                "IMG2",
                state=state,
                timeout=SEARCH_B_TIMEOUT,
                interval=SCAN_INTERVAL,
                press_r_every=None
            )

            if not state["running"]:
                continue

            if box2 is None:
                reset_game()
                print(f"[{hms()}] IMG2 not found -> pressed 'R', returning to start.")
                continue

            print(f"[{hms()}] IMG2 FOUND -> ALERT MODE")

            beep_loop()  # immediate feedback
            beep()
            beep()

            # Track only around the found box for stability
            region = _expand_box(box2, REGION_MARGIN)

            # ====== STATE C: ALERT while IMG2 remains on screen ======
            next_beep = time.time() + ALERT_INTERVAL
            while state["running"]:
                if keyboard.is_pressed("esc"):
                    print("[INFO] Exiting...")
                    raise KeyboardInterrupt

                still_box = locate("IMG2", region=region)
                if still_box:
                    now = time.time()
                    if now >= next_beep:
                        beep()
                        print(f"[{hms()}] BEEP (IMG2 still on screen)")
                        next_beep = now + ALERT_INTERVAL
                    time.sleep(0.2)
                else:
                    print(f"[{hms()}] IMG2 disappeared -> returning to start.")
                    break

    except pyautogui.FailSafeException:
        print("[INFO] Fail-safe triggered. Stopped.")
    except KeyboardInterrupt:
        print("[INFO] Stopped via ESC.")
    finally:
        try:
            keyboard.remove_hotkey(HOTKEY_TOGGLE)
        except Exception:
            pass


if __name__ == "__main__":
    main()
