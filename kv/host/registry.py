"""Native messaging 宿主注册：浏览器注册表键（HKCU）。

Chrome 与 Edge 各查一个键的**默认值**（= manifest 绝对路径）：
  HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<name>
  HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts\\<name>
只用 HKCU——不提权、只对当前用户生效，与 vault 的 Windows 账户绑定同一
信任边界。winreg 的调用全部收在本模块，测试可整体替换。
"""

from __future__ import annotations

import winreg
from pathlib import Path

HOST_NAME = "com.keyvault.host"
BROWSERS: dict[str, str] = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}


def key_path(browser: str) -> str:
    """单个浏览器宿主注册键的完整路径。"""
    try:
        root = BROWSERS[browser]
    except KeyError:
        raise ValueError(f"不支持的浏览器：{browser}") from None
    return f"{root}\\{HOST_NAME}"


def register(browser: str, manifest: Path) -> None:
    """写 HKCU 注册键（默认值 = manifest 路径）。键不存在则创建。"""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path(browser)) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest))


def unregister(browser: str) -> bool:
    """删注册键。不存在返回 False（幂等卸载）。"""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path(browser))
        return True
    except FileNotFoundError:
        return False


def lookup(browser: str) -> str | None:
    """读当前注册的 manifest 路径；未注册返回 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path(browser)) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return str(value) if value else None
    except (FileNotFoundError, OSError):
        return None
