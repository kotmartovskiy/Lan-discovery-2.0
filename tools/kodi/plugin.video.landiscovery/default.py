# -*- coding: utf-8 -*-
"""LAN Discovery для Kodi: открывает веб-панель в системном браузере.

Android (CoreELEC/Android TV): StartAndroidActivity с VIEW-интентом.
Linux (LibreELEC/PC): System.Exec с chromium/google-chrome.
"""
import sys

import xbmc
import xbmcaddon
import xbmcgui


def panel_url():
    addon = xbmcaddon.Addon()
    url = (addon.getSetting("panel_url") or "").strip().rstrip("/")
    if not url:
        url = "http://192.168.3.243:8080"
    return url


def open_browser(url):
    platform = xbmc.getCondVisibility("System.Platform.Android") and "android" \
        or xbmc.getCondVisibility("System.Platform.Linux") and "linux" \
        or "other"
    if platform == "android":
        xbmc.executebuiltin(
            'StartAndroidActivity(com.android.browser,android.intent.action.VIEW,,"' + url + '/tv")'
        )
        return True
    if platform == "linux":
        for browser in ("chromium", "chromium-browser", "google-chrome",
                        "google-chrome-stable", "firefox"):
            xbmc.executebuiltin('System.Exec("which ' + browser +
                                ' >/dev/null 2>&1 && ' + browser +
                                ' --kiosk ' + url + '/tv || true")')
            return True
    xbmcgui.Dialog().ok(
        "LAN Discovery",
        "Не найден браузер. Откройте %s/tv вручную." % url,
    )
    return False


def main():
    addon = xbmcaddon.Addon()
    if len(sys.argv) > 1 and sys.argv[1] == "settings":
        addon.openSettings()
        return
    url = panel_url()
    dlg = xbmcgui.Dialog()
    idx = dlg.select("LAN Discovery — " + url, [
        "Открыть TV-режим (/tv)",
        "Открыть обычную панель (/)",
        "Настройки (URL панели)",
        "Отмена",
    ])
    if idx == 0:
        open_browser(url)
    elif idx == 1:
        open_browser(url)  # браузер откроет /tv; обычный вид — после выхода из fullscreen
        xbmc.sleep(500)
        xbmc.executebuiltin('System.Exec("xdg-open ' + url + '/ || true")')
    elif idx == 2:
        addon.openSettings()


if __name__ == "__main__":
    main()
