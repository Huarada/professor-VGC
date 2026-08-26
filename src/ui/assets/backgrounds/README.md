# Background images

Drop your own wallpaper image(s) here and `src/ui/app.py` picks them up
automatically on the next page load — no code edit needed.

| File (any one extension) | Where it's used |
|---|---|
| `page.jpg` / `page.jpeg` / `page.png` / `page.webp` | The whole app's backdrop (behind the Q&A, the technical expanders, the sidebar) — currently a built-in "Pokemon research lab" CSS gradient. Rendered with `background-attachment: fixed`, so it stays anchored to the viewport while the page scrolls over it (the parallax effect). |
| `battle-stage.jpg` / `.jpeg` / `.png` / `.webp` | The battle-replay panel's own backdrop, behind the sprites and HP bars — currently a built-in "genetics lab" CSS gradient. |

Both are independent — you can supply one, both, or neither.

## Nothing here yet? That's fine.

This is the default, working state. Both backgrounds fall back to a
self-contained CSS gradient (`_LAB_BACKGROUND_CSS_DEFAULT` /
`_PAGE_BACKGROUND_CSS_DEFAULT` in `src/ui/app.py`) when no file is found —
a gradient can never fail to load, unlike a fetched image URL. This repo
never ships or references a real third-party background image itself
(nothing lab-themed exists in Showdown's own asset CDN, and this project
avoids depending on third-party image URLs for exactly the reliability
reasons documented throughout `src/ui/app.py`), so this folder starts
empty by design.

## How it's wired up

`src/ui/app.py`'s `_find_background_image()` looks for `page.<ext>` /
`battle-stage.<ext>` here, and if found, `_image_data_uri()` reads +
base64-encodes it into a `data:` URI, layered under a dark scrim for text
legibility (`linear-gradient(rgba(7,14,12,.72), rgba(7,14,12,.88)),
url(...)`). The encode is cached by file path + modification time
(`st.cache_data`) so replacing the file is picked up on the next load
without restarting the app, and — more importantly — so a multi-MB image
only gets read and encoded once, not on every single rerun (every stepper
click in the battle panel is a full Streamlit rerun).

## Picking an image

- Any resolution works — `background-size: cover` handles scaling, but
  something roughly landscape and at least ~1600px wide will look
  sharpest on a large monitor.
- Keep the file size reasonable (a few hundred KB, not tens of MB): it's
  base64-encoded directly into the page's HTML on first load.
- A busy image can fight with the text sitting on top of it. The scrim
  layer already darkens it somewhat; a naturally darker or lower-contrast
  image will read better than a bright, high-contrast one.
