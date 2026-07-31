# Create GitHub Social Preview for FoxQuiz
import os

from PIL import Image, ImageDraw, ImageFont


def main():
    base_dir = (
        "/home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz/assets"
    )
    input_path = os.path.join(base_dir, "foxquiz_mascots_performing_quiz.png")

    if not os.path.exists(input_path):
        print(f"Error: Input image not found at {input_path}")
        return

    # 1. Open original image (1920x1080)
    print(f"Opening original image from {input_path}...")
    img = Image.open(input_path).convert("RGBA")
    orig_w, orig_h = img.size
    print(f"Original size: {orig_w}x{orig_h}")

    # 2. Crop to 2:1 aspect ratio (1920x960) center-crop
    target_ratio = 2.0  # 1280 / 640
    crop_w = orig_w
    crop_h = int(orig_w / target_ratio)  # 1920 / 2 = 960

    top = (orig_h - crop_h) // 2  # (1080 - 960) // 2 = 60
    bottom = top + crop_h
    left = 0
    right = orig_w

    print(
        f"Center cropping to {crop_w}x{crop_h} (box: {left}, {top}, {right}, {bottom})..."
    )
    cropped_img = img.crop((left, top, right, bottom))

    # 3. Resize to exactly 1280x640px
    target_w, target_h = 1280, 640
    print(f"Resizing to {target_w}x{target_h}...")
    try:
        # For modern Pillow
        resampling = Image.Resampling.LANCZOS
    except AttributeError:
        # Fallback for older Pillow
        resampling = Image.ANTIALIAS

    resized_img = cropped_img.resize((target_w, target_h), resampling)

    # 4. Create transparent overlay for Glassmorphism Card
    overlay = Image.new("RGBA", resized_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Card dimensions & position
    card_x0, card_y0 = 50, 50
    card_x1, card_y1 = 360, 160
    card_r = 16

    # Draw card background (semi-transparent dark with a hint of navy/orange)
    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=card_r,
        fill=(14, 16, 22, 190),  # Dark slate blue-gray with 75% opacity
        outline=(255, 255, 255, 35),  # Elegant subtle white border with 14% opacity
        width=1,
    )

    # Draw a thin premium orange accent bar at the top of the card
    # (Just below the rounded top-corners, a sleek divider-style glow)
    draw.rounded_rectangle(
        [card_x0 + 20, card_y0 + 12, card_x0 + 70, card_y0 + 16],
        radius=2,
        fill=(255, 107, 0, 230),  # High-vibrancy FoxQuiz Orange
    )

    # 5. Load Ubuntu Fonts
    font_bold_path = "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"

    # Fallback to default if ubuntu font files are not found
    try:
        font_title = ImageFont.truetype(font_bold_path, size=54)
        print("Loaded Ubuntu fonts successfully.")
    except Exception as e:
        print(f"Warning loading Ubuntu fonts ({e}). Falling back to default font.")
        font_title = ImageFont.load_default()

    # 6. Draw Text elements inside the card
    # Brand Title: FoxQuiz (vibrant orange)
    draw.text(
        (card_x0 + 20, card_y0 + 32),
        "FoxQuiz",
        font=font_title,
        fill=(255, 120, 0, 255),  # Vibrant orange
    )

    # 7. Composite overlay on top of resized image
    final_img = Image.alpha_composite(resized_img, overlay).convert("RGB")

    # 8. Save final image as social preview (both optimized PNG and JPEG to ensure <1MB)
    output_path_png = os.path.join(base_dir, "foxquiz_github_social_preview.png")
    output_path_jpg = os.path.join(base_dir, "foxquiz_github_social_preview.jpg")

    print(f"Saving final social preview as optimized PNG to {output_path_png}...")
    final_img.save(output_path_png, "PNG", optimize=True)

    print(f"Saving final social preview as JPEG (quality=90) to {output_path_jpg}...")
    final_img.save(output_path_jpg, "JPEG", quality=90)
    print("Success! Social preview images created successfully.")


if __name__ == "__main__":
    main()
