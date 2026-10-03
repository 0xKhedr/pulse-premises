import os
import uuid
from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image

MAX_UPLOAD_SIZE = 5 * 1024 * 1024 # 5 MB

async def save_image_securely(file: UploadFile, directory: str, prefix: str = "") -> tuple[str, int, int]:
    """
    Securely process and save an uploaded image file.
    Validates size, type, dimensions, and re-encodes to strip malicious payloads.
    Returns (filename, width, height) of the saved image.
    """
    
    # 1. Read file and enforce maximum file size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5MB).")
        
    # 2. Allow known image types only
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid file type. Only JPEG, PNG, and WebP are allowed.")
        
    try:
        # 3. Decode with Pillow and reject corrupt images
        img = Image.open(BytesIO(contents))
        img.verify() # verify() only checks the header
        
        # We must reopen because verify() closes the file in some PIL versions
        img = Image.open(BytesIO(contents))
        
        # 4. Verify dimensions
        width, height = img.size
        if width > 8000 or height > 8000:
            raise HTTPException(status_code=400, detail="Image dimensions too large.")
            
        # 5. Generate random safe filename
        ext = "jpg" if img.format == "JPEG" else img.format.lower()
        if ext not in ["jpg", "jpeg", "png", "webp"]:
            ext = "png" # Default fallback
            
        safe_prefix = prefix + "_" if prefix else ""
        filename = f"{safe_prefix}{uuid.uuid4().hex}.{ext}"
        
        # Create directory if it doesn't exist
        os.makedirs(directory, exist_ok=True)
        
        filepath = os.path.join(directory, filename)
        
        # 6. Re-encode image (strips metadata, prevents XSS/polyglots)
        # Convert to RGB if saving as JPEG to avoid transparency errors
        if ext in ["jpg", "jpeg"] and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        img.save(filepath, format=img.format)
        
        return filename, width, height
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid image file: {e!s}")
