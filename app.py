import io
import math
import re
import zipfile
from typing import List, Tuple
import os

import streamlit as st
from PIL import Image, ImageOps

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

# ------------------------- Utilities -------------------------

def natural_key(s: str):
    """Key for human/natural sort: splits digits so 2 < 10.
    Example: img2.png < img10.png
    """
    return [int(text) if text.isdigit() else text.lower() for text in re.findall(r"\d+|\D+", s)]


def bytes_to_zipfile(uploaded_bytes: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(uploaded_bytes))


def _is_valid_zip_image(name: str) -> bool:
    # Skip macOS resource forks and __MACOSX directory items
    if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
        return False
    base = os.path.basename(name)
    if base.startswith("._") or base.startswith("."):
        return False
    if name.endswith("/"):
        return False
    ext = ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""
    return ext in SUPPORTED_EXTS


def list_images_in_zip(zf: zipfile.ZipFile) -> List[str]:
    names = [n for n in zf.namelist() if _is_valid_zip_image(n)]
    return names


def load_and_prepare_image(zf: zipfile.ZipFile, name: str, *, target_width: int | None,
                           fit_mode: str, background: Tuple[int, int, int] | None,
                           to_palette: bool, dither: bool) -> Image.Image:
    """Open image from zip, convert and optionally resize.
    - If background is None and source has alpha, preserve alpha; else flatten onto background.
    - fit_mode: 'fit' (letterbox to width), 'stretch' (exact width), 'none' (original size)
    - to_palette: convert to P (GIF palette) for smaller files
    """
    with zf.open(name) as fp:
        im = Image.open(fp)
        im.load()

    # Convert
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        if background is None:
            im = im.convert("RGBA")
        else:
            # Flatten on background color
            bg = Image.new("RGBA", im.size, background + (255,))
            im = Image.alpha_composite(bg, im.convert("RGBA")).convert("RGB")
    else:
        im = im.convert("RGB")

    # Resize
    if target_width and target_width > 0:
        if fit_mode == "fit":
            im = ImageOps.contain(im, (target_width, 10**9))  # keep aspect, limit width
        elif fit_mode == "stretch":
            w, h = im.size
            if w != target_width:
                new_h = max(1, round(h * (target_width / w)))
                im = im.resize((target_width, new_h), Image.LANCZOS)
        # 'none' keeps original size

    # Convert to palette for GIF if requested (helps compression)
    if to_palette:
        dither_mode = Image.FLOYDSTEINBERG if dither else Image.NONE
        im = im.convert("P", palette=Image.ADAPTIVE, colors=256, dither=dither_mode)

    return im


def build_gif(frames: List[Image.Image], *, duration_ms: int, loop: int, disposal: int,
              optimize: bool, save_transparency: bool) -> bytes:
    if not frames:
        raise ValueError("No frames to encode.")

    # If any frame is RGBA and we want transparency preserved in GIF, we need 'P' with transparency index
    # Pillow handles this automatically if frames are 'P' with transparency in info, or if first frame is RGBA
    # but practical approach: convert to P adaptively while keeping alpha if present.
    processed = []
    transparency = None

    base = frames[0]

    # Normalize sizes to first frame
    w0, h0 = base.size
    normalized = []
    for im in frames:
        if im.size != (w0, h0):
            im = im.resize((w0, h0), Image.LANCZOS)
        normalized.append(im)

    # Convert to palette while trying to preserve transparency
    for idx, im in enumerate(normalized):
        if save_transparency and im.mode == "RGBA":
            alpha = im.split()[-1]
            # Use a matte color for transparent areas that won't appear in palette
            matte = Image.new("RGB", im.size, (255, 0, 255))
            rgb = Image.composite(im.convert("RGB"), matte, alpha)
            p = rgb.convert("P", palette=Image.ADAPTIVE, colors=255)
            # Mark index of the matte as transparent by forcing that color to the last index
            p.info["transparency"] = 255
            processed.append(p)
        else:
            processed.append(im)

    out = io.BytesIO()
    processed[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=processed[1:],
        duration=duration_ms,
        loop=loop,
        disposal=disposal,
        optimize=optimize,
    )
    return out.getvalue()


# ------------------------- UI -------------------------

st.set_page_config(page_title="Zip → GIF Maker", page_icon="🎞️", layout="centered")

st.title("🎞️ Zip → GIF Maker")
st.caption("Upload a .zip of images (PNG/JPG/WEBP, etc.), tweak a few options, and export a GIF.")

uploaded = st.file_uploader("Upload a .zip with your frames", type=["zip"], accept_multiple_files=False)

with st.expander("Frame Ordering & Selection", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        order = st.selectbox("Order by filename", ["Ascending (A→Z)", "Descending (Z→A)"], index=0)
    with col2:
        reverse_frames = st.checkbox("Make boomerang (play forward then backward)", value=False)

with st.expander("Size & Quality", expanded=True):
    colA, colB, colC = st.columns(3)
    with colA:
        target_width = st.number_input("Target width (px)", min_value=0, max_value=4096, value=512, step=16, help="0 keeps original sizes of the first frame; others will be resized to match.")
    with colB:
        fit_mode = st.selectbox("Resize mode", ["fit", "stretch", "none"], index=0,
                                help="'fit' keeps aspect ratio. 'stretch' scales to width. 'none' uses original.")
    with colC:
        palette = st.checkbox("Palette/Quantize (smaller GIF)", value=True)

    colD, colE, colF = st.columns(3)
    with colD:
        dither = st.checkbox("Dither", value=True)
    with colE:
        bg_transparent = st.checkbox("Preserve transparency (if present)", value=False)
    with colF:
        bg_color = st.color_picker("Background (if flattening)", value="#FFFFFF")

with st.expander("Playback", expanded=True):
    colX, colY, colZ = st.columns(3)
    with colX:
        fps = st.slider("FPS", min_value=1, max_value=30, value=10)
    with colY:
        loop = st.number_input("Loop count (0=forever)", min_value=0, max_value=1000, value=0)
    with colZ:
        disposal = st.selectbox("Disposal", options=[2, 1, 3, 0], index=0,
                                help="2=restore to background (usually best), 1=do not dispose, 3=restore to previous, 0=unspecified")

st.divider()

make_btn = st.button("💡 Build GIF", use_container_width=True, type="primary")

if make_btn:
    if not uploaded:
        st.warning("Please upload a .zip file first.")
    else:
        try:
            zf = bytes_to_zipfile(uploaded.getvalue())
            names = list_images_in_zip(zf)
            if not names:
                st.error("No supported image files found in the zip.")
            else:
                names.sort(key=natural_key, reverse=(order.startswith("Descending")))

                # Load frames
                bg_tuple = None
                if not bg_transparent:
                    # parse color like '#RRGGBB'
                    bg_tuple = tuple(int(bg_color[i:i+2], 16) for i in (1, 3, 5))

                frames: List[Image.Image] = []
                skipped: List[str] = []
                for n in names:
                    try:
                        im = load_and_prepare_image(
                            zf,
                            n,
                            target_width=target_width or None,
                            fit_mode=fit_mode,
                            background=bg_tuple,
                            to_palette=palette,
                            dither=dither,
                        )
                        frames.append(im)
                    except Exception:
                        skipped.append(n)

                if reverse_frames and len(frames) > 1:
                    frames = frames + frames[-2:0:-1]  # boomerang without duplicating endpoints

                duration_ms = max(1, round(1000 / fps))

                # Safety guard: extremely large animations can blow up memory
                total_pixels = sum(w * h for (w, h) in (f.size for f in frames))
                if total_pixels > 200_000_000:  # ~200M px across all frames
                    st.warning("This is a very large animation. Consider lowering width or FPS.")

                gif_bytes = build_gif(
                    frames,
                    duration_ms=duration_ms,
                    loop=int(loop),
                    disposal=int(disposal),
                    optimize=True,
                    save_transparency=bg_transparent,
                )

                st.success("GIF created!")
                st.image(gif_bytes, caption=f"Preview • {len(frames)} frames @ {fps} fps", use_column_width=True)
                st.download_button(
                    "⬇️ Download GIF",
                    data=gif_bytes,
                    file_name="out.gif",
                    mime="image/gif",
                    use_container_width=True,
                )
        except zipfile.BadZipFile:
            st.error("The uploaded file is not a valid ZIP archive.")
        except Exception as e:
            st.exception(e)

st.caption("Pro tip: Use 'Palette/Quantize' for much smaller files. If your sources have alpha and you want to keep it, enable 'Preserve transparency'.")
