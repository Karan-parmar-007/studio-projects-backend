# Platforms a binary can target. "linux" and "macos" and "windows" are the desktop
# app; "android" is the keyboard APK.
PLATFORMS = ("windows", "macos", "linux", "android")

# Products that can have downloads (must be a slug in the project catalog).
PRODUCTS = ("whisperflow",)


def object_key(product: str, platform: str, filename: str) -> str:
    safe = filename.strip().replace("/", "_").replace("\\", "_") or f"{platform}.bin"
    return f"studio/downloads/{product}/{platform}/{safe}"
