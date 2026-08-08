#!/usr/bin/env python3
"""
ACHOULO Favicon Creator
Automatically creates favicon from logo with smart cropping
"""

from PIL import Image, ImageDraw
import os

def create_favicon(logo_path, output_path, size=192):
    """
    Create favicon from logo with smart cropping
    
    Args:
        logo_path: Path to the original logo image
        output_path: Where to save the favicon
        size: Output size in pixels (default: 192x192 for favicon)
    """
    
    try:
        # Open original logo
        img = Image.open(logo_path)
        print(f"✓ Opened logo: {logo_path}")
        print(f"  Original size: {img.size}")
        
        # Convert RGBA if needed
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Create a white background
        background = Image.new('RGBA', img.size, (255, 255, 255, 255))
        background.paste(img, (0, 0), img)
        img = background.convert('RGB')
        
        # Find the bounding box of non-white content for cropping
        img_data = img.getdata()
        non_white_pixels = []
        
        for idx, pixel in enumerate(img_data):
            # If pixel is not almost white (leave some margin)
            if not (pixel[0] > 240 and pixel[1] > 240 and pixel[2] > 240):
                x = idx % img.width
                y = idx // img.width
                non_white_pixels.append((x, y))
        
        if non_white_pixels:
            # Get bounding box
            min_x = min(p[0] for p in non_white_pixels)
            max_x = max(p[0] for p in non_white_pixels)
            min_y = min(p[1] for p in non_white_pixels)
            max_y = max(p[1] for p in non_white_pixels)
            
            # Add 10% padding
            padding = int((max_x - min_x + max_y - min_y) * 0.05)
            crop_box = (
                max(0, min_x - padding),
                max(0, min_y - padding),
                min(img.width, max_x + padding),
                min(img.height, max_y + padding)
            )
            
            img = img.crop(crop_box)
            print(f"✓ Cropped to content: {img.size}")
        
        # Resize to favicon size maintaining aspect ratio
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        print(f"✓ Resized to: {img.size}")
        
        # Create final square canvas with white background
        final_img = Image.new('RGB', (size, size), (255, 255, 255))
        
        # Paste cropped image centered
        offset = ((size - img.width) // 2, (size - img.height) // 2)
        final_img.paste(img, offset)
        
        # Save favicon
        final_img.save(output_path, 'PNG')
        print(f"✓ Favicon created: {output_path}")
        
        # Create additional favicon sizes
        sizes = [16, 32, 64, 128, 256]
        for favicon_size in sizes:
            favicon_img = Image.new('RGB', (favicon_size, favicon_size), (255, 255, 255))
            resized = img.copy()
            resized.thumbnail((favicon_size, favicon_size), Image.Resampling.LANCZOS)
            offset = ((favicon_size - resized.width) // 2, (favicon_size - resized.height) // 2)
            favicon_img.paste(resized, offset)
            
            name = f"favicon-{favicon_size}x{favicon_size}.png"
            favicon_img.save(os.path.join(os.path.dirname(output_path), name), 'PNG')
            print(f"✓ Created {name}")
        
        return True
    
    except Exception as e:
        print(f"✗ Error creating favicon: {str(e)}")
        return False

def create_web_icon(logo_path, output_dir, formats=['png', 'ico']):
    """Create multiple web icon formats"""
    try:
        img = Image.open(logo_path).convert('RGBA')
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        sizes = {
            'apple-touch-icon.png': 180,
            'favicon-32x32.png': 32,
            'favicon-16x16.png': 16,
            'android-chrome-192x192.png': 192,
            'android-chrome-512x512.png': 512,
        }
        
        for filename, size in sizes.items():
            resized = img.copy()
            resized.thumbnail((size, size), Image.Resampling.LANCZOS)
            
            # Create square canvas
            final = Image.new('RGBA', (size, size), (255, 255, 255, 0))
            offset = ((size - resized.width) // 2, (size - resized.height) // 2)
            final.paste(resized, offset, resized)
            
            output_path = os.path.join(output_dir, filename)
            final.save(output_path, 'PNG')
            print(f"✓ Created {filename}")
        
        # Create ICO file
        if 'ico' in formats:
            icon_path = os.path.join(output_dir, 'favicon.ico')
            img_rgb = img.convert('RGB')
            img_rgb.save(icon_path, 'ICO')
            print(f"✓ Created favicon.ico")
        
        return True
    
    except Exception as e:
        print(f"✗ Error creating web icons: {str(e)}")
        return False

if __name__ == '__main__':
    import sys
    
    print("\n" + "="*50)
    print("ACHOULO FAVICON CREATOR")
    print("="*50 + "\n")
    
    logo_file = '/home/claude/static/images/logo.png'
    output_dir = '/home/claude/static/images'
    
    if os.path.exists(logo_file):
        print(f"Processing logo: {logo_file}\n")
        
        # Create favicon
        favicon_path = os.path.join(output_dir, 'favicon.png')
        if create_favicon(logo_file, favicon_path, size=192):
            print("\n✓ Favicon creation completed successfully!")
        
        # Create web icons
        print("\nCreating additional web icons...")
        if create_web_icon(logo_file, output_dir):
            print("\n✓ Web icons creation completed successfully!")
        
        print("\nGenerate favicon.ico online at: https://favicon-generator.org/")
        
    else:
        print(f"✗ Logo file not found: {logo_file}")
        sys.exit(1)
    
    print("\n" + "="*50 + "\n")
