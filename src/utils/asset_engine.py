import os
import shutil
from PIL import Image, ImageDraw, ImageFont, ImageChops
from src.processors.image_generator import generate_five_images

def process_final_assets(visual_report, project_id, location):
    """Manufactures product-ready PNGs with clean, non-pixelated text."""
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
                _remove_background_and_save(source, output_path)
                print(f" [CLEAN] Isolated: {output_path}")

            elif "MEME" in decision and os.path.exists(source):
                # ORDER MATTERS: 1. Remove BG -> 2. Apply Text
                _remove_background_and_save(source, output_path)
                _apply_meme_text(output_path, action, output_path) 
                print(f" [MEME] Clean Text Applied: {output_path}")

            elif "REGEN" in decision:
                marketable_prompt = f"{action}, isolated on a solid white background, flat vector illustration, die-cut sticker style."
                paths = generate_five_images(project_id, location, [marketable_prompt], out_dir=final_dir)
                if paths and os.path.exists(paths[0]):
                    _remove_background_and_save(paths[0], output_path)
                    os.remove(paths[0])
                    print(f" [REGEN] Created Visual Motif: {output_path}")

        except Exception as e:
            print(f" Manufacturing error: {e}")

def _remove_background_and_save(input_path, output_path):
    """
    Cleans the motif by identifying the background and purging it.
    This step MUST happen before text is added to avoid pixelation.
    """
    try:
        with Image.open(input_path).convert("RGBA") as img:
            datas = img.getdata()
            new_data = []
            for item in datas:
                # Target near-white backgrounds only
                if item[0] > 240 and item[1] > 240 and item[2] > 240:
                    new_data.append((255, 255, 255, 0))
                else:
                    new_data.append(item)
            img.putdata(new_data)
            
            # Trim excess space to make the motif clear and centered
            bbox = img.getbbox()
            if bbox: img = img.crop(bbox)
            
            img.save(output_path, "PNG")
    except Exception as e:
        print(f" BG Removal Failed: {e}")

def _apply_meme_text(image_path, text, output_path):
    """
    Draws text onto a transparent PNG. Since the BG is already gone,
    the text remains crisp and high-resolution.
    """
    try:
        with Image.open(image_path).convert("RGBA") as img:
            # Create a transparent overlay for the text
            txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(txt_layer)
            w, h = img.size
            
            try:
                # 9% of height ensures high-visibility 'Social Proof'
                font = ImageFont.truetype("impact.ttf", int(h * 0.09))
            except:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text.upper(), font=font)
            text_w = bbox[2] - bbox[0]
            
            # Draw on the text layer with a thick stroke for credibility
            draw.text(((w - text_w) // 2, int(h * 0.82)), text.upper(), 
                      font=font, fill="white", stroke_width=5, stroke_fill="black")
            
            # Composite the text layer over the subject
            out = Image.alpha_composite(img, txt_layer)
            out.save(output_path)
    except Exception as e:
        print(f" Meme text failed: {e}")