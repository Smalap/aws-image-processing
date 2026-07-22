"""
AWS Lambda — Serverless Image Processor
=======================================

Triggered by an S3 `ObjectCreated` event on the SOURCE bucket.
Resizes + compresses the uploaded image and writes the result to the
DESTINATION bucket. All activity is logged to CloudWatch Logs.

Environment variables
---------------------
DEST_BUCKET     (required) Destination bucket name.
MAX_WIDTH       (default 800)   Max output width in pixels.
MAX_HEIGHT      (default 800)   Max output height in pixels.
JPEG_QUALITY    (default 80)    JPEG/WebP quality, 1-95.
OUTPUT_FORMAT   (default keep)  keep | jpeg | webp | png
DEST_PREFIX     (default "")    Optional key prefix, e.g. "resized/".
"""

import io
import os
import json
import logging
import urllib.parse

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

DEST_BUCKET = os.environ["DEST_BUCKET"]
MAX_WIDTH = int(os.environ.get("MAX_WIDTH", "800"))
MAX_HEIGHT = int(os.environ.get("MAX_HEIGHT", "800"))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "80"))
OUTPUT_FORMAT = os.environ.get("OUTPUT_FORMAT", "keep").lower()
DEST_PREFIX = os.environ.get("DEST_PREFIX", "")

# Only these extensions are processed. Anything else is skipped, not failed.
SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# Pillow format name -> (file extension, mime type)
FORMAT_META = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}

# Defence in depth against decompression-bomb images.
Image.MAX_IMAGE_PIXELS = 100_000_000


def _target_format(pil_format: str) -> str:
    """Decide the output Pillow format name."""
    if OUTPUT_FORMAT == "keep":
        return pil_format if pil_format in FORMAT_META else "JPEG"
    return {"jpeg": "JPEG", "jpg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(
        OUTPUT_FORMAT, "JPEG"
    )


def _prepare(img: Image.Image, fmt: str) -> Image.Image:
    """Normalise colour mode so the chosen format can actually encode it."""
    if fmt == "JPEG":
        # JPEG has no alpha channel. Flatten transparency onto white.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
    elif img.mode == "P":
        return img.convert("RGBA")
    return img


def _save_bytes(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    elif fmt == "WEBP":
        img.save(buf, format="WEBP", quality=JPEG_QUALITY, method=6)
    else:  # PNG
        img.save(buf, format="PNG", optimize=True, compress_level=9)
    buf.seek(0)
    return buf.getvalue()


def process_object(src_bucket: str, key: str) -> dict:
    # --- Guard: never write back into the bucket that triggers us. ---------
    if src_bucket == DEST_BUCKET:
        raise RuntimeError(
            "Source and destination bucket are identical. This would cause an "
            "infinite Lambda invocation loop. Use two separate buckets."
        )

    _, ext = os.path.splitext(key.lower())
    if ext not in SUPPORTED_EXT:
        logger.info("Skipping unsupported file type: s3://%s/%s", src_bucket, key)
        return {"key": key, "status": "skipped", "reason": f"unsupported extension {ext}"}

    logger.info("Downloading s3://%s/%s", src_bucket, key)
    obj = s3.get_object(Bucket=src_bucket, Key=key)
    original_bytes = obj["Body"].read()
    original_size = len(original_bytes)

    with Image.open(io.BytesIO(original_bytes)) as img:
        # Honour the camera's EXIF orientation tag, then drop the EXIF block.
        img = ImageOps.exif_transpose(img)
        source_format = img.format or "JPEG"
        original_dims = img.size

        fmt = _target_format(source_format)
        img = _prepare(img, fmt)

        # thumbnail() preserves aspect ratio and never upscales.
        img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
        new_dims = img.size

        processed_bytes = _save_bytes(img, fmt)

    out_ext, content_type = FORMAT_META[fmt]
    base, _ = os.path.splitext(key)
    dest_key = f"{DEST_PREFIX}{base}{out_ext}"

    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=dest_key,
        Body=processed_bytes,
        ContentType=content_type,
        Metadata={
            "source-bucket": src_bucket,
            "source-key": key,
            "original-width": str(original_dims[0]),
            "original-height": str(original_dims[1]),
        },
    )

    new_size = len(processed_bytes)
    saved_pct = round((1 - new_size / original_size) * 100, 1) if original_size else 0.0

    logger.info(
        "Processed %s -> s3://%s/%s | %sx%s -> %sx%s | %s B -> %s B (%s%% smaller)",
        key, DEST_BUCKET, dest_key,
        original_dims[0], original_dims[1], new_dims[0], new_dims[1],
        original_size, new_size, saved_pct,
    )

    return {
        "key": key,
        "status": "processed",
        "destination": f"s3://{DEST_BUCKET}/{dest_key}",
        "original_dimensions": list(original_dims),
        "new_dimensions": list(new_dims),
        "original_bytes": original_size,
        "new_bytes": new_size,
        "size_reduction_pct": saved_pct,
    }


def lambda_handler(event, context):
    results = []

    for record in event.get("Records", []):
        src_bucket = record["s3"]["bucket"]["name"]
        # S3 URL-encodes keys in event notifications: spaces arrive as "+".
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"], encoding="utf-8")

        try:
            results.append(process_object(src_bucket, key))

        except s3.exceptions.NoSuchKey:
            # Object was deleted between the event firing and our GET.
            logger.warning("Object no longer exists: s3://%s/%s", src_bucket, key)
            results.append({"key": key, "status": "failed", "reason": "object not found"})

        except UnidentifiedImageError:
            logger.warning("Not a decodable image, skipping: %s", key)
            results.append({"key": key, "status": "failed", "reason": "corrupt or not an image"})

        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            logger.error("S3 error (%s) on %s", code, key, exc_info=True)
            raise  # Re-raise: let Lambda retry, then fall through to the DLQ.

        except Exception:
            logger.error("Unexpected failure on %s", key, exc_info=True)
            raise

    summary = {"processed": len(results), "results": results}
    logger.info("Batch summary: %s", json.dumps(summary))
    return {"statusCode": 200, "body": json.dumps(summary)}
