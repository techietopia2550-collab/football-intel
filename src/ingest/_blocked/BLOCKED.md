# BLOCKED: fbref.py

**Status:** quarantined, never executed. Do not move back or run.

## Why

`fbref.py` calls `soccerdata.FBref(...)`. In soccerdata 1.9.1 that class is
built on `BaseSeleniumReader` (verified via `soccerdata.FBref.__mro__`):

```
FBref -> BaseSeleniumReader -> BaseReader -> ABC -> object
```

`BaseSeleniumReader.__init__` immediately calls `_init_webdriver()`, which
starts `seleniumbase.Driver(uc=True, ...)` — a real undetected-Chrome
process — as a side effect of constructing the reader. On a CAPTCHA the
reader calls `solve_captcha()`, which (when `headless=False`) invokes
`uc_gui_handle_captcha()` / `uc_gui_handle_cf()`, SeleniumBase's
pyautogui-based mouse/keyboard automation against the desktop.

`fbref.py` itself has no `import selenium` line, so an import grep alone
would call it clean. The Selenium dependency is inherited.

Project decision: no Selenium-based ingest.

Note on actual risk: the default is `headless=True`, in which case
`solve_captcha()` only logs a warning and sleeps, and `pyautogui` is not
installed in this venv. The GUI path would therefore not fire by default. The
file is still blocked because it launches a browser at all, which the
project rule forbids.

## Replacement options

See `BLOCKERS.md` at the repo root.
