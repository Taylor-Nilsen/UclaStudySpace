"""
Download classroom images from UCLA DTS and store them locally.
Updates classrooms.json to use local image URLs instead of external URLs.
"""

import json
import os
import requests
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import re
from urllib.parse import unquote

def download_image(url, local_dir='images'):
    """Download an image and return the local path, or None if it fails."""
    if not url:
        return None
    
    # Create images directory if it doesn't exist
    Path(local_dir).mkdir(exist_ok=True)
    
    try:
        # Generate filename from URL hash to handle duplicates
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        parsed_url = urlparse(url)
        original_filename = parsed_url.path.split('/')[-1].split('?')[0]
        # Decode %20 etc. and keep filenames URL-safe; a literal "%20" in a
        # filename 404s on GitHub Pages because the browser decodes it first.
        original_filename = re.sub(r'[^A-Za-z0-9._-]+', '-', unquote(original_filename))
        
        # Clean up filename
        if not original_filename or original_filename.startswith('styles'):
            original_filename = f"classroom_{url_hash}"
        
        local_path = os.path.join(local_dir, original_filename)
        
        # Skip if already downloaded
        if os.path.exists(local_path):
            return f"{local_dir}/{original_filename}"
        
        # Download with timeout
        response = requests.get(url, timeout=10, allow_redirects=True)
        response.raise_for_status()
        
        # Save file
        with open(local_path, 'wb') as f:
            f.write(response.content)
        
        return f"{local_dir}/{original_filename}"
    
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return None

def main():
    """Download all images and update classrooms.json."""
    
    # Load classrooms
    with open('classrooms.json', 'r') as f:
        classrooms = json.load(f)
    
    downloaded = 0
    failed = 0
    
    print("Downloading classroom images...")
    
    for i, room in enumerate(classrooms):
        if not room.get('image_url'):
            continue
        
        print(f"[{i+1}/{len(classrooms)}] Downloading {room.get('text', 'Unknown')}...", end=' ')
        
        local_url = download_image(room['image_url'])
        
        if local_url:
            room['image_url'] = local_url
            downloaded += 1
            print("✓")
        else:
            failed += 1
            print("✗")
    
    # Save updated classrooms.json
    with open('classrooms.json', 'w') as f:
        json.dump(classrooms, f, indent=1)
    
    print(f"\nDownload complete!")
    print(f"Downloaded: {downloaded}")
    print(f"Failed: {failed}")
    print(f"Updated classrooms.json with local image URLs")

if __name__ == '__main__':
    main()
