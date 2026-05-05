import os
import shutil
from PIL import Image, ImageDraw, ImageFont
from src.processors.image_generator import generate_images

def process_final_assets(visual_report, project_id, location):
    """Manufactures product-ready assets without attempting background removal."""
    final_dir = "output/final_assets"
    os.makedirs(final_dir, exist_ok=True)

    lines = visual_report.strip().split('\n')
    for line in lines:
        if "TREND:" not in line or "|" not in line: continue
        
        try:
            parts = {p.split(':', 1)[0].strip().upper(): p.split(':', 1)[1].strip() for p in line.split('|')}
            term, decision, action, source = parts.get('TREND'), parts.get('DECISION', '').upper(), parts.get('ACTION'), parts.get('SOURCE')

            if not term: continue
            term_slug = term.lower().replace(" ", "_")
            output_path = os.path.join(final_dir, f"{term_slug}_final.png")

            if "CLEAN" in decision and os.path.exists(source):
                # Simply copy the source to the final destination
                shutil.copy(source, output_path)
                print(f" [CLEAN] Asset saved: {output_path}")

            elif "MEME" in decision and os.path.exists(source):
                # Apply text directly to the source image
                _apply_meme_text(source, action, output_path) 
                print(f" [MEME] Meme text applied: {output_path}")

            elif "REGEN" in decision:
                marketable_prompt = f"{action}, flat vector illustration, die-cut sticker style."
                paths = generate_images(project_id, location, [marketable_prompt], out_dir=final_dir)
                if paths and os.path.exists(paths[0]):
                    # Move the generated image to the final path with the correct slug
                    shutil.move(paths[0], output_path)
                    print(f" [REGEN] Created Visual Motif: {output_path}")

        except Exception as e:
            print(f" Manufacturing error: {e}")

def _apply_meme_text(image_path, text, output_path):
    """
    Draws text onto the provided image.
    """
    try:
        with Image.open(image_path).convert("RGB") as img:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            
            try:
                # 9% of height for high-visibility text
                font = ImageFont.truetype("impact.ttf", int(h * 0.09))
            except:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text.upper(), font=font)
            text_w = bbox[2] - bbox[0]
            
            # Draw directly on the image with a thick stroke for readability
            draw.text(((w - text_w) // 2, int(h * 0.82)), text.upper(), 
                      font=font, fill="white", stroke_width=5, stroke_fill="black")
            
            img.save(output_path)
    except Exception as e:
        print(f" Meme text failed: {e}")