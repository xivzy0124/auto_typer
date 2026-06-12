#!/usr/bin/env python3
"""Auto Typer -上课演示用自动输入工具 (macOS)"""
import json
import plistlib
import sys
import threading
import random
import time
from pathlib import Path
from pynput import keyboard
import subprocess
# CGEventTap 键盘锁定（需要 pyobjc-framework-Quartz）
try:
    import Quartz
    _QUARTZ_AVAILABLE = True
except ImportError:
    _QUARTZ_AVAILABLE = False
    print("[警告] 未找到 Quartz，键盘锁定不可用")
    print("       请运行: pip install pyobjc-framework-Quartz")

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SNIPPETS_DIR = BASE_DIR / "snippets"

kb = keyboard.Controller()
is_paused = False
abort_typing = False  # 重置标志：True 时中断当前输入
pause_lock = threading.Lock()

# ─── 配置 ───────────────────────────────────────────────────────────────────
def load_config():
    if not CONFIG_PATH.exists():
        default = {
            "mean_delay_ms": 120,
            "delay_std_ms": 40,
            "mistake_rate": 0.02,
            "extra_pause": True,
            "vscode_use_auto_close_braces": True,
        }
        CONFIG_PATH.write_text(json.dumps(default, indent=2), encoding="utf-8")
        print(f"[配置] 已创建默认配置: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# macOS 虚拟键码：数字 0-9 + 减号(-)
VK_0, VK_1, VK_2, VK_3, VK_4 = 29, 18, 19, 20, 21
VK_5, VK_6, VK_7, VK_8, VK_9 = 23, 22, 26, 28, 25
VK_MINUS = 27
SLOT_VKS = {1: VK_1, 2: VK_2, 3: VK_3, 4: VK_4, 5: VK_5,
            6: VK_6, 7: VK_7, 8: VK_8, 9: VK_9}
# 需要在锁屏期间被吞掉的热键键码（避免数字/减号落进 VS Code）
HOTKEY_DIGIT_VKS = {VK_0, VK_MINUS} | set(SLOT_VKS.values())

def load_snippets():
    SNIPPETS_DIR.mkdir(exist_ok=True)
    snippets = {}
    # 槽位 1..9，自动生成缺失的 N.txt
    for i in range(1, 10):
        path = SNIPPETS_DIR / f"{i}.txt"
        if path.exists():
            snippets[i] = path.read_text(encoding="utf-8")
        else:
            path.write_text(f"# 槽位 {i}: 在此写入代码\nprint('snippet {i}')\n", encoding="utf-8")
            snippets[i] = path.read_text(encoding="utf-8")
    return snippets

# ─── CGEventTap 键盘锁 ───────────────────────────────────────────────────────
class KeyboardLock:
    """
    用 CGEventTap 在系统层拦截键盘事件。
    锁定期间只放行由 pynput/CGEventPost 合成的事件（pid != 0），
    完全屏蔽来自物理键盘的输入（pid == 0）。
    前置条件：
      1. pip install pyobjc-framework-Quartz
      2. 系统设置 → 隐私与安全性 → 辅助功能 → 授权本终端/Python
    """
    def __init__(self):
        self._locked = False
        self._tap = None
        self._run_loop = None
        self._lock = threading.Lock()
        self._thread = None

    def _is_hotkey_digit_event(self, event_type, event):
        if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return False
        pid = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGEventSourceUnixProcessID
        )
        if pid != 0:
            return False
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode not in HOTKEY_DIGIT_VKS:
            return False
        flags = Quartz.CGEventGetFlags(event)
        return bool(
            flags & Quartz.kCGEventFlagMaskControl
            and flags & Quartz.kCGEventFlagMaskAlternate
        )

    def _callback(self, proxy, event_type, event, refcon):
        with self._lock:
            if self._is_hotkey_digit_event(event_type, event):
                return None  # 吞掉 Ctrl+Option+0-4，避免数字落进 VS Code
            if not self._locked:
                return event  # 未锁定，全部放行
            # 锁定中：检查事件来源
            # kCGEventSourceUnixProcessID == 0 → 来自硬件驱动（真实按键）→ 丢弃
            pid = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGEventSourceUnixProcessID
            )
            if pid == 0:
                return None  # 丢弃真实按键
            return event  # 放行程序合成事件（pynput 的 kb.press）

    def _run(self):
        mask = (
            1 << Quartz.kCGEventKeyDown
            | 1 << Quartz.kCGEventKeyUp
            | 1 << Quartz.kCGEventFlagsChanged
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # 可丢弃事件
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            print("[KeyboardLock] CGEventTap 创建失败")
            print("  → 请在「系统设置 → 隐私与安全性 → 辅助功能」中授权终端/Python")
            return
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()  # 阻塞，直到 stop()

    def start(self):
        if not _QUARTZ_AVAILABLE:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.15)  # 等 tap 初始化

    def stop(self):
        if self._run_loop:
            Quartz.CFRunLoopStop(self._run_loop)

    def lock(self):
        with self._lock:
            self._locked = True
        print("  [键盘已锁定] 物理按键已屏蔽")

    def unlock(self):
        with self._lock:
            self._locked = False
        print("  [键盘已解锁]")

    @property
    def available(self):
        return _QUARTZ_AVAILABLE and self._tap is not None

    @property
    def suppresses_hotkeys(self):
        return self.available

kb_lock = KeyboardLock()

# ─── 输入法检测与切换 ────────────────────────────────────────────────────────
def check_and_switch_to_english():
    try:
        result = subprocess.run(
            ["defaults", "export", "com.apple.HIToolbox", "-"],
            capture_output=True,
        )
        if result.returncode != 0:
            print("[输入法] 无法读取配置，请手动切换到英文")
            return False
        data = plistlib.loads(result.stdout)
        source_id = data.get("AppleCurrentKeyboardLayoutInputSourceID", "").lower()
        print(f"[输入法] 当前: {source_id}")
        english = ['keylayout.abc', 'keylayout.us', 'keylayout.american',
                   'keylayout.british', 'keylayout.australian', 'keylayout.dvorak']
        if any(e in source_id for e in english):
            print("[输入法] 已是英文，无需切换")
            return False
        chinese = ['pinyin', 'chinese', 'scim', 'wubi', 'shuangpin',
                   'sogou', 'rime', 'baidu']
        if any(c in source_id for c in chinese):
            print("[输入法] 检测到中文，切换中...")
            with kb.pressed(keyboard.Key.ctrl):
                kb.press(' ')
                kb.release(' ')
            time.sleep(0.3)
            return True
        print(f"[输入法] 未知输入源 '{source_id}'，跳过")
        return False
    except Exception as e:
        print(f"[输入法] 出错: {e}")
        return False

# ─── 人类化打字引擎 ──────────────────────────────────────────────────────────
MISTAKE_MAP = {
    'a': 's', 's': 'a', 'd': 'f', 'f': 'd', 'j': 'k', 'k': 'j',
    'l': ';', ';': 'l', 'e': 'r', 'r': 'e', 't': 'y', 'y': 't',
    'i': 'o', 'o': 'i', 'n': 'm', 'm': 'n',
}

def _press(key):
    kb.press(key)
    kb.release(key)

def _clear_vscode_autoindent():
    time.sleep(0.06)
    _press(keyboard.Key.home)
    time.sleep(0.02)
    kb.press(keyboard.Key.shift)
    _press(keyboard.Key.end)
    kb.release(keyboard.Key.shift)
    time.sleep(0.02)
    # Use space+backspace instead of Delete: safe whether selection is empty or not.
    # Delete (forward-delete) with empty selection eats the next newline, merging lines.
    kb.press(keyboard.KeyCode.from_char(' '))
    kb.release(keyboard.KeyCode.from_char(' '))
    time.sleep(0.01)
    _press(keyboard.Key.backspace)
    time.sleep(0.02)

def _type_literal(char):
    kb.press(keyboard.KeyCode.from_char(char))
    kb.release(keyboard.KeyCode.from_char(char))

def type_newline():
    _press(keyboard.Key.esc)
    time.sleep(0.03)
    _press(keyboard.Key.enter)
    _clear_vscode_autoindent()

def move_to_vscode_auto_closed_brace():
    _press(keyboard.Key.esc)
    time.sleep(0.02)
    _press(keyboard.Key.down)
    time.sleep(0.02)
    _press(keyboard.Key.end)
    time.sleep(0.02)

def type_char(char, config=None):
    if char == "\n":
        type_newline()
    elif char == "\t":
        _press(keyboard.Key.tab)
    else:
        _type_literal(char)

def _next_line_auto_close_brace_end(text, newline_index):
    index = newline_index + 1
    while index < len(text) and text[index] in (' ', '\t'):
        index += 1
    if index < len(text) and text[index] == '}':
        return index + 1
    return None

def _pause_gate():
    """阻塞直到恢复输入；返回 False 表示被重置，应中断。"""
    while True:
        with pause_lock:
            if abort_typing:
                return False
            if not is_paused:
                return True
        time.sleep(0.02)

def _sleep_interruptible(seconds):
    """分片睡眠：一旦进入暂停/重置立刻返回，使暂停近乎即时生效。"""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        with pause_lock:
            if is_paused or abort_typing:
                return
        time.sleep(min(0.02, remaining))

def type_text_human(text, config):
    global is_paused, abort_typing
    mean = config.get("mean_delay_ms", 120)
    std = config.get("delay_std_ms", 40)
    mistake_rate = config.get("mistake_rate", 0.02)
    extra_pause = config.get("extra_pause", True)
    print(f"  开始输入 ({len(text)} 字符)...")
    i = 0
    while i < len(text):
        ch = text[i]
        delay = max(25, random.gauss(mean, std)) / 1000.0
        _sleep_interruptible(delay)
        if extra_pause and ch in '.,;:\n' and random.random() < 0.6:
            _sleep_interruptible(random.uniform(0.2, 0.6))
        # 真正落键前确认暂停状态：暂停时阻塞于此，不会再吐出字符
        if not _pause_gate():
            break
        if random.random() < mistake_rate and ch in MISTAKE_MAP:
            wrong = MISTAKE_MAP[ch]
            type_char(wrong, config)
            time.sleep(random.uniform(0.08, 0.2))
            _press(keyboard.Key.backspace)
            time.sleep(random.uniform(0.05, 0.12))
        if (
            ch == "\n"
            and config.get("vscode_use_auto_close_braces", True)
            and (next_index := _next_line_auto_close_brace_end(text, i)) is not None
        ):
            move_to_vscode_auto_closed_brace()
            i = next_index
            continue
        type_char(ch, config)
        i += 1
    if abort_typing:
        print("  [重置] 输入已中断")
    else:
        print("  输入完成!")

# ─── 热键监听 ────────────────────────────────────────────────────────────────
def start_listener(config, snippets):
    global is_paused
    is_typing = False
    pressed_mods = set()
    pressed_vks = set()

    def _norm(key):
        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            return 'ctrl'
        if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
            return 'alt'
        if hasattr(key, 'vk') and key.vk is not None:
            return ('vk', key.vk)
        return None

    def on_press(key):
        global is_paused, abort_typing
        nonlocal is_typing
        n = _norm(key)
        if n in ('ctrl', 'alt'):
            pressed_mods.add(n)
            return
        if not isinstance(n, tuple):
            return

        vk = n[1]
        # 长按时系统会重复发送 keydown（无中间 keyup）。若该键已在按下集合中，
        # 说明是自动重复，忽略它——否则一次长按会把暂停反复切换，表现为
        # “恢复后只打一个字母又暂停”。
        is_repeat = vk in pressed_vks
        pressed_vks.add(vk)
        if is_repeat:
            return

        if 'ctrl' not in pressed_mods or 'alt' not in pressed_mods:
            return

        if vk == VK_MINUS:
            # 重置：中断当前输入并解除暂停，便于按错后重新选择
            with pause_lock:
                abort_typing = True
                is_paused = False
            kb_lock.unlock()
            print(f"\n  [重置] Ctrl+Option+- — 已中断输入，可重新选择槽位")
            return

        if vk == VK_0:
            with pause_lock:
                is_paused = not is_paused
                paused_now = is_paused
            if paused_now:
                kb_lock.unlock()   # 暂停 → 解锁键盘，允许手动输入
                print(f"\n  [暂停] Ctrl+Option+0 — 键盘已解锁，可手动操作")
            else:
                kb_lock.lock()     # 恢复 → 重新锁定
                print(f"\n  [恢复] Ctrl+Option+0 — 键盘已重新锁定")
            return

        for slot, slot_vk in SLOT_VKS.items():
            if vk == slot_vk:
                if is_typing:
                    print(f"  [跳过] 槽位{slot} - 正在输入中")
                    return
                if slot not in snippets or not snippets[slot].strip():
                    print(f"  [空] 槽位{slot} 无内容，请编辑 snippets/{slot}.txt")
                    return
                is_typing = True
                switched = check_and_switch_to_english()
                threading.Thread(
                    target=_do_type,
                    args=(snippets[slot], switched, slot),
                    daemon=True,
                ).start()
                return

    def on_release(key):
        n = _norm(key)
        if n in ('ctrl', 'alt'):
            pressed_mods.discard(n)
        elif isinstance(n, tuple):
            pressed_vks.discard(n[1])

    def _do_type(text, switched, slot):
        nonlocal is_typing
        global abort_typing, is_paused
        with pause_lock:
            abort_typing = False
        try:
            time.sleep(0.05)
            if not kb_lock.suppresses_hotkeys:
                _press(keyboard.Key.backspace)
            if switched:
                time.sleep(0.5)
            print(f"  [触发] 槽位{slot}")
            kb_lock.lock()
            type_text_human(text, config)
        finally:
            with pause_lock:
                aborted = abort_typing
            # 正常打完后继续锁定 3 秒：手指可能还压在热键上、或系统键盘缓冲
            # 尚未排空，立即解锁会让这些物理按键漏进编辑器多敲字母。
            # 重置路径已在 on_press 里立即解锁，故此处仅在未被重置时延迟。
            if not aborted:
                _sleep_interruptible(3.0)
            kb_lock.unlock()
            with pause_lock:
                abort_typing = False
                is_paused = False
            is_typing = False

    print("[Auto Typer] 已启动")
    print(f"  延迟: {config.get('mean_delay_ms', 120)}±{config.get('delay_std_ms', 40)}ms")
    print(f"  手误率: {config.get('mistake_rate', 0.02)*100:.1f}%")
    print(f"  槽位: {', '.join(f'{k}({len(v)}字符)' for k, v in snippets.items())}")
    print(f"  快捷键: Ctrl+Option+1~9 输入 | Ctrl+Option+0 暂停/恢复 | Ctrl+Option+- 重置")
    if kb_lock.available:
        print(f"  键盘锁定: CGEventTap ✓ 系统级拦截就绪")
    else:
        print(f"  键盘锁定: ✗ 不可用（pip install pyobjc-framework-Quartz 后重启）")
    print(f"  按 Ctrl+C 退出\n")
    check_and_switch_to_english()
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()

# ─── 入口 ────────────────────────────────────────────────────────────────────
def main():
    if sys.platform != "darwin":
        sys.exit(1)
    kb_lock.start()
    config = load_config()
    snippets = load_snippets()
    try:
        start_listener(config, snippets)
    finally:
        kb_lock.stop()

if __name__ == "__main__":
    main()
